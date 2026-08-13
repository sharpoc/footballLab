"""Versioned, narrowly scoped Nginx management for the daily-picks routes."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_NGINX_SITE_CONFIG = "/etc/nginx/sites-available/football.celab.xin.conf"
DEFAULT_NGINX_SNIPPET_PATH = "/etc/nginx/snippets/worldcup-daily-picks.conf"
DEFAULT_NGINX_BACKUP_DIR = "/root/nginx-backups"
DEFAULT_NGINX_SERVICE = "nginx"
MANAGED_INCLUDE_MARKER = "worldcup-daily-picks.conf"
DAILY_PICK_ROUTES = (
    "/api/daily-picks",
    "/daily-picks",
    "/api/daily-picks-sidecar",
    "/daily-picks-sidecar",
)

CommandRunner = Callable[..., object]
_LOCATION_RE = re.compile(
    r"^(?P<indent>\s*)location\s*=\s*(?P<route>\S+)\s*\{\s*(?:#.*)?(?:\r?\n)?$"
)
_SERVER_OPEN_RE = re.compile(r"^\s*server\s*\{\s*(?:#.*)?(?:\r?\n)?$")
_SERVER_NAME_RE = re.compile(r"^\s*server_name\s+([^;]+);\s*(?:#.*)?$")


def render_daily_picks_snippet() -> str:
    """Render only the four exact locations managed by this project."""
    blocks = [
        "# Managed by worldcup.nginx_routes; do not edit.\n",
    ]
    for index, route in enumerate(DAILY_PICK_ROUTES):
        blocks.extend(
            [
                f"location = {route} {{\n",
                "    proxy_pass http://127.0.0.1:8788;\n",
                "}\n",
            ]
        )
        if index < len(DAILY_PICK_ROUTES) - 1:
            blocks.append("\n")
    return "".join(blocks)


def _brace_delta(line: str) -> int:
    code = line.split("#", 1)[0]
    return code.count("{") - code.count("}")


def _remove_managed_locations(lines: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(lines):
        match = _LOCATION_RE.match(lines[index])
        if not match or match.group("route") not in DAILY_PICK_ROUTES:
            result.append(lines[index])
            index += 1
            continue
        depth = _brace_delta(lines[index])
        index += 1
        while depth > 0 and index < len(lines):
            depth += _brace_delta(lines[index])
            index += 1
        if depth != 0:
            raise ValueError("unbalanced_daily_picks_location")
    return result


def _remove_managed_includes(lines: list[str], include_path: str) -> list[str]:
    escaped = re.escape(include_path)
    include_re = re.compile(rf"^\s*include\s+{escaped}\s*;\s*(?:#.*)?(?:\r?\n)?$")
    return [line for line in lines if not include_re.match(line)]


def _target_server_open_index(lines: list[str]) -> int:
    index = 0
    while index < len(lines):
        if not _SERVER_OPEN_RE.match(lines[index]):
            index += 1
            continue
        start = index
        depth = _brace_delta(lines[index])
        index += 1
        has_target_name = False
        while depth > 0 and index < len(lines):
            if _SERVER_NAME_RE.match(lines[index]):
                names = _SERVER_NAME_RE.match(lines[index]).group(1).split()
                has_target_name = "football.celab.xin" in names
            depth += _brace_delta(lines[index])
            index += 1
        if depth != 0:
            raise ValueError("unbalanced_server_block")
        if has_target_name:
            return start
    raise ValueError("target_server_not_found")


def normalize_site_config(
    site_config: str,
    *,
    include_path: str = DEFAULT_NGINX_SNIPPET_PATH,
) -> str:
    """Replace only legacy exact daily-picks locations in the target server."""
    lines = site_config.splitlines(keepends=True)
    newline = "\n"
    if lines and lines[0].endswith("\r\n"):
        newline = "\r\n"
    server_index = _target_server_open_index(lines)
    depth = _brace_delta(lines[server_index])
    server_end = server_index + 1
    while depth > 0 and server_end < len(lines):
        depth += _brace_delta(lines[server_end])
        server_end += 1
    if depth != 0:
        raise ValueError("unbalanced_server_block")
    inner = _remove_managed_locations(lines[server_index + 1 : server_end - 1])
    inner = _remove_managed_includes(inner, include_path)
    opening = lines[server_index]
    indent = opening[: len(opening) - len(opening.lstrip())]
    include_line = f"{indent}    include {include_path};{newline}"
    lines[server_index + 1 : server_end - 1] = [include_line, *inner]
    return "".join(lines)


def _default_command_runner(args: Sequence[str], *, timeout: int = 30):
    completed = subprocess.run(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return completed


def _returncode(result: object) -> int:
    return int(getattr(result, "returncode", 1))


def _stderr(result: object) -> str:
    value = getattr(result, "stderr", "")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _restore_file(path: Path, backup: Path | None, existed: bool) -> None:
    if existed:
        assert backup is not None
        _atomic_write(path, backup.read_text(encoding="utf-8"))
    else:
        path.unlink(missing_ok=True)


def _backup_file(source: Path, backup: Path) -> None:
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup)


def install_daily_picks_routes(
    *,
    site_config: str | Path = DEFAULT_NGINX_SITE_CONFIG,
    snippet_path: str | Path = DEFAULT_NGINX_SNIPPET_PATH,
    snippet_source: str | Path,
    backup_dir: str | Path = DEFAULT_NGINX_BACKUP_DIR,
    nginx_service: str = DEFAULT_NGINX_SERVICE,
    command_runner: CommandRunner = _default_command_runner,
    timestamp: str | None = None,
) -> dict[str, object]:
    """Install the managed snippet with atomic writes and guarded Nginx reload."""
    site_path = Path(site_config)
    managed_path = Path(snippet_path)
    source_path = Path(snippet_source)
    backup_root = Path(backup_dir)
    stamp = timestamp or _timestamp()

    if site_path.is_symlink() or managed_path.is_symlink():
        return {"status": "blocked", "reason": "nginx_config_is_symlink", "restored": False}
    if not site_path.is_file() or not source_path.is_file():
        return {"status": "blocked", "reason": "nginx_config_missing", "restored": False}
    source_text = source_path.read_text(encoding="utf-8")
    expected_snippet = render_daily_picks_snippet()
    if source_text != expected_snippet:
        return {"status": "blocked", "reason": "managed_snippet_mismatch", "restored": False}

    old_site = site_path.read_text(encoding="utf-8")
    old_snippet_exists = managed_path.exists()
    old_snippet = managed_path.read_text(encoding="utf-8") if old_snippet_exists else ""
    try:
        desired_site = normalize_site_config(old_site, include_path=str(managed_path))
    except ValueError as exc:
        return {"status": "blocked", "reason": str(exc), "restored": False}
    changed = desired_site != old_site or old_snippet != expected_snippet or not old_snippet_exists
    if not changed:
        return {
            "status": "unchanged",
            "changed": False,
            "routes": list(DAILY_PICK_ROUTES),
            "restored": False,
        }

    site_backup = backup_root / f"{stamp}-{site_path.name}"
    snippet_backup = backup_root / f"{stamp}-{managed_path.name}"
    _backup_file(site_path, site_backup)
    if old_snippet_exists:
        _backup_file(managed_path, snippet_backup)

    try:
        _atomic_write(site_path, desired_site)
        _atomic_write(managed_path, expected_snippet)
    except Exception:
        _restore_file(site_path, site_backup, True)
        _restore_file(managed_path, snippet_backup if old_snippet_exists else None, old_snippet_exists)
        raise

    nginx_test = command_runner(["nginx", "-t"])
    if _returncode(nginx_test) != 0:
        _restore_file(site_path, site_backup, True)
        _restore_file(managed_path, snippet_backup if old_snippet_exists else None, old_snippet_exists)
        return {
            "status": "nginx_test_failed",
            "changed": True,
            "restored": True,
            "stderr": _stderr(nginx_test),
        }

    reload_result = command_runner(["systemctl", "reload", nginx_service])
    if _returncode(reload_result) == 0:
        return {
            "status": "installed",
            "changed": True,
            "routes": list(DAILY_PICK_ROUTES),
            "backup": [str(site_backup)] + ([str(snippet_backup)] if old_snippet_exists else []),
            "restored": False,
        }

    _restore_file(site_path, site_backup, True)
    _restore_file(managed_path, snippet_backup if old_snippet_exists else None, old_snippet_exists)
    recovery_test = command_runner(["nginx", "-t"])
    recovery_reload = None
    if _returncode(recovery_test) == 0:
        recovery_reload = command_runner(["systemctl", "reload", nginx_service])
    return {
        "status": "reload_failed",
        "changed": True,
        "restored": True,
        "stderr": _stderr(reload_result),
        "recovery_test": _returncode(recovery_test) == 0,
        "recovery_reload": _returncode(recovery_reload) == 0 if recovery_reload is not None else False,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install managed daily-picks Nginx routes.")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--site-config", default=DEFAULT_NGINX_SITE_CONFIG)
    parser.add_argument("--snippet-path", default=DEFAULT_NGINX_SNIPPET_PATH)
    parser.add_argument("--snippet-source", required=True)
    parser.add_argument("--backup-dir", default=DEFAULT_NGINX_BACKUP_DIR)
    parser.add_argument("--nginx-service", default=DEFAULT_NGINX_SERVICE)
    args = parser.parse_args(argv)
    if not args.install:
        parser.error("--install is required")
    result = install_daily_picks_routes(
        site_config=args.site_config,
        snippet_path=args.snippet_path,
        snippet_source=args.snippet_source,
        backup_dir=args.backup_dir,
        nginx_service=args.nginx_service,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"installed", "unchanged"} else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
