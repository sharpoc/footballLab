from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from worldcup.ssh_deploy import (
    CommandResult,
    FetchResult,
    _deploy_script,
    _rollback_script,
    main,
    run_ssh_deploy,
)


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
    assert "releases_dir=/opt/worldcup/releases" in remote_script
    assert "release_name=00158faef75b" in remote_script
    assert '"$tmp/worldcup/query.py"' in remote_script
    assert "http://127.0.0.1:8788/readyz" in remote_script
    assert "time.monotonic() + 30" in remote_script
    assert "time.sleep(1)" in remote_script
    assert "systemctl restart" in remote_script


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


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fake_remote_bin(root: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "flock",
        """#!/bin/bash
if [ "$1" != "-n" ]; then exit 2; fi
exec /usr/bin/python3 -c 'import fcntl, sys
try:
    fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(1)' "$2"
""",
    )
    _write_executable(
        fake_bin / "readlink",
        """#!/bin/bash
exec /usr/bin/python3 -c 'import os, sys
path = os.path.realpath(sys.argv[-1])
if not os.path.exists(path):
    raise SystemExit(1)
print(path)' "$@"
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/bash
if [ "$1" = "restart" ] && [ -n "${TEST_RESTART_MARKER:-}" ]; then
  touch "$TEST_RESTART_MARKER"
  while [ ! -e "$TEST_RESTART_GATE" ]; do sleep 0.02; done
fi
if [ "$1" = "is-active" ]; then printf 'active\\n'; fi
exit 0
""",
    )
    _write_executable(fake_bin / "python3", "#!/bin/bash\ncat >/dev/null || true\nexit 0\n")
    _write_executable(fake_bin / "tar", "#!/bin/bash\ncat >/dev/null || true\nexit 0\n")
    return fake_bin


def _remote_env(fake_bin: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env.update(extra)
    return env


def _deploy_for_paths(release: Path, current: Path, *, rollback: bool = False) -> str:
    return _deploy_script(
        release=str(release),
        current_symlink=str(current),
        service="worldcup.service",
        nginx_service="nginx",
        py_compile_files=("worldcup/http_app.py",),
        readyz_url="http://127.0.0.1:8788/readyz",
        rollback_on_fail=rollback,
    )


def _run_remote_script(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        input="",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=5,
        check=False,
    )


def test_deploy_script_aborts_if_release_is_symlink() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        shared = root / "shared"
        releases.mkdir()
        shared.mkdir()
        release = releases / "abc123"
        release.symlink_to(shared, target_is_directory=True)
        result = _run_remote_script(
            _deploy_for_paths(release, root / "current"),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode != 0
        assert "deploy_blocked=release_is_symlink" in result.stdout
        assert list(shared.iterdir()) == []


def test_deploy_script_validates_previous_is_physical_dir_under_releases() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        outside = root / "outside"
        releases.mkdir()
        outside.mkdir()
        current = root / "current"
        current.symlink_to(outside, target_is_directory=True)
        release = releases / "abc123"
        result = _run_remote_script(
            _deploy_for_paths(release, current, rollback=True),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode != 0
        assert "deploy_blocked=previous_outside_releases" in result.stdout
        assert not release.exists()
        assert current.resolve() == outside.resolve()


def test_deploy_script_acquires_flock() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        releases.mkdir()
        current = root / "current"
        fake_bin = _fake_remote_bin(root)
        marker = root / "restart.started"
        gate = root / "restart.continue"
        first = subprocess.Popen(
            ["bash", "-c", _deploy_for_paths(releases / "first", current)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_remote_env(
                fake_bin,
                TEST_RESTART_MARKER=str(marker),
                TEST_RESTART_GATE=str(gate),
            ),
        )
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "first deployment did not reach the lock-holding restart"

            second = _run_remote_script(
                _deploy_for_paths(releases / "second", current),
                _remote_env(fake_bin),
            )
            assert second.returncode != 0
            assert "deploy_blocked=concurrent_deploy" in second.stdout
            assert not (releases / "second").exists()
        finally:
            gate.touch()
            first_stdout, first_stderr = first.communicate(timeout=5)
        assert first.returncode == 0, (first_stdout, first_stderr)


def test_deploy_script_aborts_when_current_exists_but_resolves_empty() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        releases.mkdir()
        current = root / "current"
        current.symlink_to(root / "missing")
        release = releases / "abc123"
        result = _run_remote_script(
            _deploy_for_paths(release, current),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode != 0
        assert "deploy_blocked=current_unresolvable" in result.stdout
        assert not release.exists()


def test_deploy_script_creates_missing_releases_directory_on_first_deploy() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        current = root / "current"
        release = releases / "first"
        result = _run_remote_script(
            _deploy_for_paths(release, current),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert release.is_dir()
        assert current.resolve() == release.resolve()


def test_deploy_script_rejects_lock_symlink_without_touching_target() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        releases.mkdir()
        victim = root / "victim"
        victim.write_text("keep-me", encoding="utf-8")
        (releases / ".deploy.lock").symlink_to(victim)
        result = _run_remote_script(
            _deploy_for_paths(releases / "first", root / "current"),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode != 0
        assert "deploy_blocked=lock_is_symlink" in result.stdout
        assert victim.read_text(encoding="utf-8") == "keep-me"


def test_deploy_script_compares_previous_against_physical_releases_dir() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        real_releases = root / "real-releases"
        real_releases.mkdir()
        alias = root / "releases"
        alias.symlink_to(real_releases, target_is_directory=True)
        previous = real_releases / "previous"
        previous.mkdir()
        current = root / "current"
        current.symlink_to(previous, target_is_directory=True)
        release = alias / "next"
        result = _run_remote_script(
            _deploy_for_paths(release, current),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert current.resolve() == (real_releases / "next").resolve()


def test_rollback_script_skips_when_current_points_to_newer_release() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        previous = releases / "previous"
        failed = releases / "failed"
        newer = releases / "newer"
        for path in (previous, failed, newer):
            path.mkdir(parents=True, exist_ok=True)
        current = root / "current"
        current.symlink_to(newer, target_is_directory=True)
        result = _run_remote_script(
            _rollback_script(
                previous_release=str(previous),
                failed_release=str(failed),
                current_symlink=str(current),
                service="worldcup.service",
            ),
            _remote_env(_fake_remote_bin(root)),
        )

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "rollback=skipped_current_changed" in result.stdout
        assert current.resolve() == newer.resolve()


def test_rollback_script_does_not_switch_current_during_lock_contention() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        releases = root / "releases"
        previous = releases / "previous"
        failed = releases / "failed"
        previous.mkdir(parents=True)
        failed.mkdir()
        current = root / "current"
        current.symlink_to(failed, target_is_directory=True)
        lock_file = releases / ".deploy.lock"
        lock_file.touch()
        fake_bin = _fake_remote_bin(root)
        marker = root / "lock.held"
        gate = root / "lock.release"
        holder = subprocess.Popen(
            [
                "bash",
                "-c",
                f'exec 9<"{lock_file}"; flock -n 9; touch "{marker}"; '
                f'while [ ! -e "{gate}" ]; do sleep 0.02; done',
            ],
            env=_remote_env(fake_bin),
        )
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "lock holder did not acquire the deployment lock"
            result = _run_remote_script(
                _rollback_script(
                    previous_release=str(previous),
                    failed_release=str(failed),
                    current_symlink=str(current),
                    service="worldcup.service",
                ),
                _remote_env(fake_bin),
            )
            assert result.returncode != 0
            assert "rollback_blocked=concurrent_deploy" in result.stdout
            assert current.resolve() == failed.resolve()
        finally:
            gate.touch()
            holder.wait(timeout=5)


def test_live_deploy_reports_failed_if_rollback_skips_changed_current() -> None:
    class ChangedCurrentRunner(FakeRunner):
        def __call__(self, args, *, cwd=None, input_bytes=None, timeout=30):
            if args[:1] == ["ssh"] and input_bytes is None:
                self.calls.append(
                    {"args": args, "cwd": cwd, "input_bytes": input_bytes, "timeout": timeout}
                )
                return CommandResult(
                    0,
                    "rollback=skipped_current_changed\n"
                    "current_target=/opt/worldcup/releases/newer\n",
                    "",
                )
            return super().__call__(args, cwd=cwd, input_bytes=input_bytes, timeout=timeout)

    def failing_fetcher(url: str, timeout: int) -> FetchResult:
        if url.endswith("/api/matches"):
            return FetchResult(ok=False, status_code=None, body="", error="timeout")
        return ok_fetcher(url, timeout)

    result = run_ssh_deploy(
        root=".",
        live=True,
        rollback_on_fail=True,
        command_runner=ChangedCurrentRunner(),
        fetcher=failing_fetcher,
    )

    assert result["status"] == "failed"
    assert result["rollback"]["status"] == "skipped"
    assert result["rollback"]["rollback"] == "skipped_current_changed"
