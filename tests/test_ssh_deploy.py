from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from worldcup.ssh_deploy import CommandResult, FetchResult, main, run_ssh_deploy


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        input_bytes: bytes | None = None,
        timeout: int = 30,
    ) -> CommandResult:
        self.calls.append(
            {"args": args, "cwd": cwd, "input_bytes": input_bytes, "timeout": timeout}
        )
        if args[:3] == ["git", "rev-parse", "--verify"]:
            return CommandResult(0, "00158faef75b\n", "", b"00158faef75b\n")
        if args[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(0, "", "", b"")
        if args[:3] == ["git", "archive", "--format=tar"]:
            return CommandResult(0, "", "", b"tar-bytes")
        if args[:1] == ["ssh"]:
            if input_bytes == b"tar-bytes":
                return CommandResult(
                    0,
                    "\n".join(
                        [
                            "previous_release=/opt/worldcup/releases/old",
                            "release=/opt/worldcup/releases/00158faef75b",
                            "service_status=active",
                            "readyz_warmup=ok",
                            "nginx_status=active",
                            "current_target=/opt/worldcup/releases/00158faef75b",
                        ]
                    )
                    + "\n",
                    "",
                )
            return CommandResult(
                0,
                "rollback=ok\nservice_status=active\ncurrent_target=/opt/worldcup/releases/old\n",
                "",
            )
        raise AssertionError(f"unexpected command: {args}")


def ok_fetcher(url: str, timeout: int) -> FetchResult:
    if url.endswith("/healthz"):
        return FetchResult(ok=True, status_code=200, body='{"status":"ok"}', error=None)
    if url.endswith("/readyz"):
        return FetchResult(ok=True, status_code=200, body='{"status":"ready"}', error=None)
    if url.endswith("/api/matches"):
        return FetchResult(ok=True, status_code=200, body='{"matches":[]}', error=None)
    if url.endswith("/preview"):
        return FetchResult(ok=True, status_code=200, body="仅用于研究分析，不构成投注建议", error=None)
    raise AssertionError(f"unexpected url: {url}")


def test_dry_run_reports_plan_without_ssh_or_archive() -> None:
    runner = FakeRunner()

    result = run_ssh_deploy(
        root=".",
        live=False,
        command_runner=runner,
        fetcher=ok_fetcher,
    )

    assert result["status"] == "dry_run_ready"
    assert result["mode"] == "dry_run"
    assert result["commit"] == "00158faef75b"
    assert result["paths"]["release"] == "/opt/worldcup/releases/00158faef75b"
    assert result["safety"] == {
        "read_env": False,
        "called_theoddsapi": False,
        "published": False,
        "deployed": False,
        "changed_launch_agent": False,
    }
    assert not any(call["args"][0] == "ssh" for call in runner.calls)
    assert not any(call["args"][:3] == ["git", "archive", "--format=tar"] for call in runner.calls)


def test_dry_run_blocks_dirty_worktree_before_ssh() -> None:
    class DirtyRunner(FakeRunner):
        def __call__(self, args, *, cwd=None, input_bytes=None, timeout=30):
            if args[:3] == ["git", "status", "--porcelain"]:
                self.calls.append(
                    {"args": args, "cwd": cwd, "input_bytes": input_bytes, "timeout": timeout}
                )
                return CommandResult(0, " M worldcup/query.py\n?? scratch.txt\n", "")
            return super().__call__(args, cwd=cwd, input_bytes=input_bytes, timeout=timeout)

    runner = DirtyRunner()

    result = run_ssh_deploy(
        root=".",
        live=False,
        command_runner=runner,
        fetcher=ok_fetcher,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "dirty_worktree"
    assert result["dirty_files"] == 2
    assert not any(call["args"][0] == "ssh" for call in runner.calls)


def test_live_deploy_uploads_archive_restarts_and_smokes_public_routes() -> None:
    runner = FakeRunner()

    result = run_ssh_deploy(
        root=".",
        live=True,
        command_runner=runner,
        fetcher=ok_fetcher,
    )

    assert result["status"] == "deployed"
    assert result["mode"] == "live"
    assert result["remote"]["service_status"] == "active"
    assert result["remote"]["nginx_status"] == "active"
    assert result["smoke"]["status"] == "ok"
    assert result["safety"]["deployed"] is True
    assert [check["path"] for check in result["smoke"]["checks"]] == [
        "/healthz",
        "/api/matches",
        "/preview",
    ]
    ssh_calls = [call for call in runner.calls if call["args"][0] == "ssh"]
    assert len(ssh_calls) == 1
    deploy_call = ssh_calls[0]
    assert deploy_call["input_bytes"] == b"tar-bytes"
    remote_script = deploy_call["args"][-1]
    assert "/opt/worldcup/releases/00158faef75b" in remote_script
    assert '"$tmp/worldcup/query.py"' in remote_script
    assert "http://127.0.0.1:8788/readyz" in remote_script
    assert "time.monotonic() + 30" in remote_script
    assert "time.sleep(1)" in remote_script
    assert "systemctl restart" in remote_script
    assert "worldcup-daily-sidecar.service" in remote_script
    assert "worldcup-daily-sidecar.timer" in remote_script
    assert "/var/lib/worldcup/daily_odds" in remote_script
    assert "systemctl daemon-reload" in remote_script
    assert 'systemctl disable worldcup-daily-sidecar.timer' in remote_script
    assert 'systemctl enable "$sidecar_timer"' not in remote_script


def test_live_deploy_does_not_require_public_readyz_route() -> None:
    def no_public_readyz_fetcher(url: str, timeout: int) -> FetchResult:
        if url.endswith("/readyz"):
            raise AssertionError("readyz warmup must run over SSH, not public Nginx")
        return ok_fetcher(url, timeout)

    runner = FakeRunner()

    result = run_ssh_deploy(
        root=".",
        live=True,
        command_runner=runner,
        fetcher=no_public_readyz_fetcher,
    )

    assert result["status"] == "deployed"
    assert result["remote"]["readyz_warmup"] == "ok"


def test_live_deploy_can_bind_ssh_source_address() -> None:
    runner = FakeRunner()

    result = run_ssh_deploy(
        root=".",
        live=True,
        bind_address="192.168.31.152",
        command_runner=runner,
        fetcher=ok_fetcher,
    )

    assert result["status"] == "deployed"
    ssh_calls = [call for call in runner.calls if call["args"][0] == "ssh"]
    deploy_args = ssh_calls[0]["args"]
    assert "-b" in deploy_args
    assert "192.168.31.152" in deploy_args


def test_live_deploy_rolls_back_when_smoke_fails() -> None:
    def failing_fetcher(url: str, timeout: int) -> FetchResult:
        if url.endswith("/api/matches"):
            return FetchResult(ok=False, status_code=None, body="", error="timeout")
        return ok_fetcher(url, timeout)

    runner = FakeRunner()

    result = run_ssh_deploy(
        root=".",
        live=True,
        rollback_on_fail=True,
        command_runner=runner,
        fetcher=failing_fetcher,
    )

    assert result["status"] == "rolled_back"
    assert result["reason"] == "smoke_failed"
    assert result["rollback"]["status"] == "ok"
    ssh_calls = [call for call in runner.calls if call["args"][0] == "ssh"]
    assert len(ssh_calls) == 2
    rollback_script = ssh_calls[1]["args"][-1]
    assert "previous=/opt/worldcup/releases/old" in rollback_script
    assert 'ln -sfn "$previous" "$current"' in rollback_script


def test_main_prints_json_dry_run() -> None:
    runner = FakeRunner()
    stdout = StringIO()

    with redirect_stdout(stdout):
        exit_code = main(["--root", "."], command_runner=runner, fetcher=ok_fetcher)

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["status"] == "dry_run_ready"
    assert payload["mode"] == "dry_run"
