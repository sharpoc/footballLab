"""SSH archive deployment helper; defaults to dry-run."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_HOST = "strategy-lab-ecs"
DEFAULT_PUBLIC_BASE_URL = "https://football.celab.xin"
DEFAULT_RELEASES_DIR = "/opt/worldcup/releases"
DEFAULT_CURRENT_SYMLINK = "/opt/worldcup/current"
DEFAULT_SERVICE = "worldcup.service"
DEFAULT_NGINX_SERVICE = "nginx"
DEFAULT_REF = "HEAD"
DEFAULT_SSH_TIMEOUT = 15
DEFAULT_HTTP_TIMEOUT = 15
DEFAULT_REMOTE_READYZ_URL = "http://127.0.0.1:8788/readyz"
DEFAULT_REMOTE_PY_COMPILE = ("worldcup/query.py", "worldcup/http_app.py", "worldcup/nginx_routes.py")
DEFAULT_NGINX_SITE_CONFIG = "/etc/nginx/sites-available/football.celab.xin.conf"
DEFAULT_NGINX_SNIPPET_PATH = "/etc/nginx/snippets/worldcup-daily-picks.conf"
DEFAULT_NGINX_BACKUP_DIR = "/root/nginx-backups"
DEFAULT_NGINX_TEMPLATE_PATH = "deploy/nginx/worldcup-daily-picks.conf"
DISCLAIMER = "仅用于研究分析，不构成投注建议"
FORBIDDEN_PUBLIC_TERMS = (
    "stake",
    "bet amount",
    "bankroll",
    "payout",
    "wager",
    "unit",
    "下注金额",
    "投注金额",
    "本金",
    "重注",
    "追损",
    "串关",
    "喊单",
)
SENSITIVE_RE = re.compile(
    r"api[_-]?key|ingest_hmac_secret|database_url|authorization|cookie|token|"
    r"password|private[-_ ]?key|signature",
    re.I,
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: bytes = b""


@dataclass(frozen=True)
class FetchResult:
    ok: bool
    status_code: int | None
    body: str
    error: str | None


CommandRunner = Callable[..., CommandResult]
Fetcher = Callable[[str, int], FetchResult]


def _base_safety(deployed: bool = False) -> dict[str, bool]:
    return {
        "read_env": False,
        "called_theoddsapi": False,
        "published": False,
        "deployed": deployed,
        "changed_launch_agent": False,
    }


def _default_command_runner(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int = 30,
) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    return CommandResult(completed.returncode, stdout, stderr, completed.stdout)


def _default_fetcher(url: str, timeout: int) -> FetchResult:
    request = Request(url, headers={"User-Agent": "worldcup-ssh-deploy/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(500_000).decode("utf-8", errors="replace")
            return FetchResult(True, int(response.status), body, None)
    except HTTPError as exc:
        body = exc.read(20_000).decode("utf-8", errors="replace")
        return FetchResult(False, int(exc.code), body, "http_error")
    except (OSError, TimeoutError, URLError) as exc:
        return FetchResult(False, None, "", type(exc).__name__)


def _redact(text: str, limit: int = 800) -> str:
    redacted = SENSITIVE_RE.sub("<redacted>", text)
    return redacted[-limit:]


def _release_path(releases_dir: str, commit: str) -> str:
    return f"{releases_dir.rstrip('/')}/{commit}"


def _parse_key_values(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            parsed[key] = value.strip()
    return parsed


def _command_failure(reason: str, result: CommandResult) -> dict[str, object]:
    return {
        "status": "blocked",
        "reason": reason,
        "returncode": result.returncode,
        "stdout_tail": _redact(result.stdout),
        "stderr_tail": _redact(result.stderr),
        "safety": _base_safety(False),
    }


def _git_commit(
    runner: CommandRunner,
    root: Path,
    ref: str,
    command_timeout: int,
) -> tuple[str | None, dict[str, object] | None]:
    result = runner(
        ["git", "rev-parse", "--verify", ref],
        cwd=root,
        timeout=command_timeout,
    )
    if result.returncode != 0:
        return None, _command_failure("git_ref_unresolved", result)
    commit = result.stdout.strip()
    if not commit:
        return None, {
            "status": "blocked",
            "reason": "git_ref_empty",
            "safety": _base_safety(False),
        }
    return commit, None


def _worktree_state(
    runner: CommandRunner,
    root: Path,
    command_timeout: int,
) -> tuple[int | None, dict[str, object] | None]:
    result = runner(
        ["git", "status", "--porcelain"],
        cwd=root,
        timeout=command_timeout,
    )
    if result.returncode != 0:
        return None, _command_failure("git_status_failed", result)
    dirty_lines = [line for line in result.stdout.splitlines() if line.strip()]
    return len(dirty_lines), None


def _deploy_script(
    *,
    release: str,
    current_symlink: str,
    service: str,
    nginx_service: str,
    py_compile_files: tuple[str, ...],
    readyz_url: str,
    rollback_on_fail: bool,
    nginx_site_config: str = DEFAULT_NGINX_SITE_CONFIG,
    nginx_snippet_path: str = DEFAULT_NGINX_SNIPPET_PATH,
    nginx_backup_dir: str = DEFAULT_NGINX_BACKUP_DIR,
    nginx_template_path: str = DEFAULT_NGINX_TEMPLATE_PATH,
) -> str:
    tmp = f"{release}.tmp.deploy"
    nginx_template = f"{release}/{nginx_template_path}"
    compile_paths = " ".join(f'"$tmp/{path}"' for path in py_compile_files)
    rollback_trap = ""
    if rollback_on_fail:
        rollback_trap = (
            "trap 'code=$?; "
            'if [ "$code" -ne 0 ] && [ -n "$previous" ] && [ -d "$previous" ]; then '
            'ln -sfn "$previous" "$current"; systemctl restart "$service" || true; '
            "printf \"rollback_on_remote_failure=attempted\\n\"; fi; exit \"$code\"' EXIT\n"
        )
    return "\n".join(
        [
            "set -euo pipefail",
            f"release={shlex.quote(release)}",
            f"tmp={shlex.quote(tmp)}",
            f"current={shlex.quote(current_symlink)}",
            f"service={shlex.quote(service)}",
            f"nginx_service={shlex.quote(nginx_service)}",
            'previous=$(readlink -f "$current" 2>/dev/null || true)',
            rollback_trap.rstrip(),
            'rm -rf "$tmp"',
            'mkdir -p "$tmp"',
            'tar -C "$tmp" -xf -',
            f"python3 -m py_compile {compile_paths}",
            'if [ ! -d "$release" ]; then mv "$tmp" "$release"; else rm -rf "$tmp"; fi',
            'ln -sfn "$release" "$current"',
            'systemctl restart "$service"',
            'service_status=$(systemctl is-active "$service")',
            (
                "python3 - "
                f"{shlex.quote(readyz_url)} <<'PY'\n"
                "import json\n"
                "import sys\n"
                "import time\n"
                "from urllib.request import urlopen\n"
                "url = sys.argv[1]\n"
                "deadline = time.monotonic() + 30\n"
                "last_error = 'not_attempted'\n"
                "while time.monotonic() < deadline:\n"
                "    try:\n"
                "        with urlopen(url, timeout=3) as response:\n"
                "            payload = json.loads(response.read(20000).decode('utf-8'))\n"
                "        if response.status == 200 and payload.get('status') == 'ready':\n"
                "            break\n"
                "        last_error = f'status={response.status}; payload_status={payload.get(\"status\")}'\n"
                "    except Exception as exc:\n"
                "        last_error = type(exc).__name__\n"
                "    time.sleep(1)\n"
                "else:\n"
                "    raise SystemExit(f'readyz_warmup_failed: {last_error}')\n"
                "PY"
            ),
            'readyz_warmup=ok',
            'nginx_status=$(systemctl is-active "$nginx_service")',
            (
                "PYTHONPATH=\"$release\" python3 -m worldcup.nginx_routes --install "
                f"--site-config {shlex.quote(nginx_site_config)} "
                f"--snippet-path {shlex.quote(nginx_snippet_path)} "
                f"--snippet-source {shlex.quote(nginx_template)} "
                f"--backup-dir {shlex.quote(nginx_backup_dir)} "
                f"--nginx-service \"$nginx_service\""
            ),
            'current_target=$(readlink -f "$current" 2>/dev/null || true)',
            'printf "previous_release=%s\\n" "$previous"',
            'printf "release=%s\\n" "$release"',
            'printf "service_status=%s\\n" "$service_status"',
            'printf "readyz_warmup=%s\\n" "$readyz_warmup"',
            'printf "nginx_status=%s\\n" "$nginx_status"',
            'printf "current_target=%s\\n" "$current_target"',
        ]
    )


def _rollback_script(
    *,
    previous_release: str,
    current_symlink: str,
    service: str,
) -> str:
    return "\n".join(
        [
            "set -euo pipefail",
            f"previous={shlex.quote(previous_release)}",
            f"current={shlex.quote(current_symlink)}",
            f"service={shlex.quote(service)}",
            'ln -sfn "$previous" "$current"',
            'systemctl restart "$service"',
            'service_status=$(systemctl is-active "$service")',
            'current_target=$(readlink -f "$current" 2>/dev/null || true)',
            'printf "rollback=ok\\n"',
            'printf "service_status=%s\\n" "$service_status"',
            'printf "current_target=%s\\n" "$current_target"',
        ]
    )


def _scan_forbidden(text: str) -> list[str]:
    lowered = text.lower()
    return [term for term in FORBIDDEN_PUBLIC_TERMS if term.lower() in lowered]


def _smoke_public(base_url: str, fetcher: Fetcher, timeout: int) -> dict[str, object]:
    base = base_url.rstrip("/")
    checks: list[dict[str, object]] = []
    failed = False
    for path in ("/healthz", "/api/matches", "/preview"):
        fetched = fetcher(f"{base}{path}", timeout)
        check: dict[str, object] = {
            "path": path,
            "ok": fetched.ok,
            "status_code": fetched.status_code,
        }
        if fetched.error is not None:
            check["error"] = fetched.error
        if not fetched.ok or fetched.status_code != 200:
            failed = True
        if path in {"/api/matches", "/preview"}:
            forbidden = _scan_forbidden(fetched.body)
            check["forbidden_hits"] = len(forbidden)
            if forbidden:
                check["forbidden_terms"] = forbidden
                failed = True
        if path == "/preview":
            disclaimer_present = DISCLAIMER in fetched.body
            check["disclaimer_present"] = disclaimer_present
            if not disclaimer_present:
                failed = True
        checks.append(check)
    return {"status": "error" if failed else "ok", "checks": checks}


def _run_rollback(
    *,
    host: str,
    bind_address: str | None,
    previous_release: str,
    current_symlink: str,
    service: str,
    ssh_timeout: int,
    command_runner: CommandRunner,
    command_timeout: int,
) -> dict[str, object]:
    ssh_args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={ssh_timeout}",
    ]
    if bind_address:
        ssh_args.extend(["-b", bind_address])
    ssh_args.extend(
        [
            host,
            _rollback_script(
                previous_release=previous_release,
                current_symlink=current_symlink,
                service=service,
            ),
        ]
    )
    result = command_runner(
        ssh_args,
        input_bytes=None,
        timeout=command_timeout,
    )
    if result.returncode != 0:
        return {
            "status": "error",
            "returncode": result.returncode,
            "stdout_tail": _redact(result.stdout),
            "stderr_tail": _redact(result.stderr),
        }
    return {"status": "ok", **_parse_key_values(result.stdout)}


def run_ssh_deploy(
    *,
    root: str | Path = ".",
    live: bool = False,
    ref: str = DEFAULT_REF,
    host: str = DEFAULT_HOST,
    bind_address: str | None = None,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    releases_dir: str = DEFAULT_RELEASES_DIR,
    current_symlink: str = DEFAULT_CURRENT_SYMLINK,
    service: str = DEFAULT_SERVICE,
    nginx_service: str = DEFAULT_NGINX_SERVICE,
    rollback_on_fail: bool = False,
    ssh_timeout: int = DEFAULT_SSH_TIMEOUT,
    http_timeout: int = DEFAULT_HTTP_TIMEOUT,
    command_timeout: int = 60,
    command_runner: CommandRunner = _default_command_runner,
    fetcher: Fetcher = _default_fetcher,
) -> dict[str, object]:
    root_path = Path(root)
    mode = "live" if live else "dry_run"
    commit, failure = _git_commit(command_runner, root_path, ref, command_timeout)
    if failure is not None:
        return {"schema_version": 1, "mode": mode, **failure}
    assert commit is not None

    dirty_count, failure = _worktree_state(command_runner, root_path, command_timeout)
    if failure is not None:
        return {"schema_version": 1, "mode": mode, "commit": commit, **failure}
    assert dirty_count is not None

    release = _release_path(releases_dir, commit)
    base = {
        "schema_version": 1,
        "mode": mode,
        "commit": commit,
        "host": host,
        "paths": {
            "release": release,
            "current": current_symlink,
            "releases_dir": releases_dir,
        },
        "safety": _base_safety(False),
    }
    if dirty_count:
        return {
            **base,
            "status": "blocked",
            "reason": "dirty_worktree",
            "dirty_files": dirty_count,
        }
    if not live:
        return {**base, "status": "dry_run_ready"}

    archive = command_runner(
        ["git", "archive", "--format=tar", commit],
        cwd=root_path,
        timeout=command_timeout,
    )
    if archive.returncode != 0:
        return {**base, **_command_failure("git_archive_failed", archive)}

    remote_script = _deploy_script(
        release=release,
        current_symlink=current_symlink,
        service=service,
        nginx_service=nginx_service,
        py_compile_files=DEFAULT_REMOTE_PY_COMPILE,
        readyz_url=DEFAULT_REMOTE_READYZ_URL,
        rollback_on_fail=rollback_on_fail,
    )
    ssh_args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={ssh_timeout}",
    ]
    if bind_address:
        ssh_args.extend(["-b", bind_address])
    ssh_args.extend([host, remote_script])
    remote = command_runner(
        ssh_args,
        input_bytes=archive.stdout_bytes,
        timeout=command_timeout,
    )
    if remote.returncode != 0:
        return {**base, **_command_failure("ssh_deploy_failed", remote)}

    remote_summary = _parse_key_values(remote.stdout)
    smoke = _smoke_public(public_base_url, fetcher, http_timeout)
    deployed_base = {**base, "remote": remote_summary, "smoke": smoke, "safety": _base_safety(True)}
    if smoke["status"] == "ok":
        return {**deployed_base, "status": "deployed"}

    previous = remote_summary.get("previous_release")
    if rollback_on_fail and previous:
        rollback = _run_rollback(
            host=host,
            bind_address=bind_address,
            previous_release=previous,
            current_symlink=current_symlink,
            service=service,
            ssh_timeout=ssh_timeout,
            command_runner=command_runner,
            command_timeout=command_timeout,
        )
        return {
            **deployed_base,
            "status": "rolled_back" if rollback.get("status") == "ok" else "failed",
            "reason": "smoke_failed",
            "rollback": rollback,
        }
    return {**deployed_base, "status": "failed", "reason": "smoke_failed", "rollback": None}


def main(
    argv: list[str] | None = None,
    *,
    command_runner: CommandRunner = _default_command_runner,
    fetcher: Fetcher = _default_fetcher,
) -> int:
    parser = argparse.ArgumentParser(
        description="Deploy the current git commit to the ECS host over SSH. Defaults to dry-run."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--bind-address",
        default=None,
        help="Bind SSH to a local source address, useful when a TUN/proxy route hijacks the ECS IP.",
    )
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--releases-dir", default=DEFAULT_RELEASES_DIR)
    parser.add_argument("--current", default=DEFAULT_CURRENT_SYMLINK)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--nginx-service", default=DEFAULT_NGINX_SERVICE)
    parser.add_argument("--ssh-timeout", type=int, default=DEFAULT_SSH_TIMEOUT)
    parser.add_argument("--http-timeout", type=int, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--command-timeout", type=int, default=60)
    parser.add_argument("--live", action="store_true", help="Perform the SSH deployment.")
    parser.add_argument(
        "--rollback-on-fail",
        action="store_true",
        help="Relink the previous release if remote deployment or public smoke fails.",
    )
    args = parser.parse_args(argv)

    result = run_ssh_deploy(
        root=args.root,
        live=args.live,
        ref=args.ref,
        host=args.host,
        bind_address=args.bind_address,
        public_base_url=args.public_base_url,
        releases_dir=args.releases_dir,
        current_symlink=args.current,
        service=args.service,
        nginx_service=args.nginx_service,
        rollback_on_fail=args.rollback_on_fail,
        ssh_timeout=args.ssh_timeout,
        http_timeout=args.http_timeout,
        command_timeout=args.command_timeout,
        command_runner=command_runner,
        fetcher=fetcher,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"dry_run_ready", "deployed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
