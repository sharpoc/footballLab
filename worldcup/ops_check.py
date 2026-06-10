"""Read-only operational check for the World Cup analysis site."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from worldcup.refresh_audit import DEFAULT_LAUNCH_AGENT, inspect_launch_agent, summarize_history

DEFAULT_PUBLIC_BASE_URL = "https://football.celab.xin"
DEFAULT_REMOTE_HOST = "strategy-lab-ecs"
DEFAULT_LOCAL_LOGS = (
    Path.home() / "Library" / "Logs" / "worldcup" / "scheduled-publish.out.log",
    Path.home() / "Library" / "Logs" / "worldcup" / "scheduled-publish.err.log",
)
DISCLAIMER = "仅用于研究分析，不构成投注建议"
FORBIDDEN_PUBLIC_TERMS = [
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
]
SENSITIVE_RE = re.compile(
    r"api[_-]?key|the_odds_api_key|ingest_hmac_secret|hmac secret|database_url|"
    r"x-worldcup-signature|authorization|cookie|token|password|private[-_ ]?key|"
    r"request body|body\":|signature",
    re.I,
)
ERROR_RE = re.compile(r"traceback|\berror\b|exception|failed|panic|critical", re.I)
REMOTE_DROP_KEYS = {
    "payload",
    "payload_json",
    "snapshot",
    "snapshot_json",
    "body",
    "body_json",
    "secret",
    "signature",
}

FetchResult = dict[str, Any]
Fetcher = Callable[[str, int], FetchResult]
RemoteRunner = Callable[[str, int], dict[str, Any]]


def scan_text(text: str) -> dict[str, int]:
    return {
        "bytes_checked": len(text.encode("utf-8", errors="replace")),
        "sensitive_hits": len(SENSITIVE_RE.findall(text)),
        "error_hits": len(ERROR_RE.findall(text)),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _snapshot_summary(path: Path) -> dict[str, Any]:
    snapshot = _read_json(path)
    if snapshot is None:
        return {"status": "error", "path": str(path), "message": "missing_or_unreadable"}

    data_quality = snapshot.get("data_quality") or {}
    matches = snapshot.get("matches")
    counts = snapshot.get("counts") or {}
    match_count = counts.get("matches")
    if match_count is None and isinstance(matches, list):
        match_count = len(matches)

    return {
        "status": "ok",
        "path": str(path),
        "run_id": (snapshot.get("run") or {}).get("run_id"),
        "snapshot_at": snapshot.get("snapshot_at"),
        "matches": match_count,
        "source_errors_count": len(data_quality.get("source_errors") or []),
        "stale_sources": list(data_quality.get("stale_sources") or []),
    }


def _quota_summary(path: Path) -> dict[str, Any]:
    quota = _read_json(path)
    if quota is None:
        return {"status": "warn", "path": str(path), "message": "missing_or_unreadable"}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    return {"status": "ok", "path": str(path), "providers": providers}


def _scan_log_file(path: Path, max_bytes: int = 200_000) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        raw = path.read_bytes()
    except OSError:
        return {"status": "unreadable", "path": str(path)}
    text = raw[-max_bytes:].decode("utf-8", errors="replace")
    return {"status": "ok", "path": str(path), **scan_text(text)}


def _local_checks(
    root: Path,
    launch_agent_path: str | Path,
    local_log_paths: list[str | Path],
) -> dict[str, Any]:
    return {
        "snapshot": _snapshot_summary(root / "data/cache/analysis_snapshot.json"),
        "quota": _quota_summary(root / "data/cache/quota.json"),
        "history": summarize_history(root / "data/local/history", limit=3),
        "launch_agent": inspect_launch_agent(launch_agent_path),
        "logs": [_scan_log_file(Path(path).expanduser()) for path in local_log_paths],
    }


def _default_fetcher(url: str, timeout: int) -> FetchResult:
    request = Request(url, headers={"User-Agent": "worldcup-ops-check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {
                "status": response.status,
                "body": body,
                "headers": dict(response.headers.items()),
            }
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": exc.code,
            "body": body,
            "headers": dict(exc.headers.items()),
        }
    except URLError as exc:
        return {"status": None, "body": "", "headers": {}, "error": exc.reason}


def _header(headers: dict[str, Any], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def _forbidden_hits(text: str) -> list[str]:
    lower = text.lower()
    return [term for term in FORBIDDEN_PUBLIC_TERMS if term.lower() in lower]


def _last_update(text: str) -> str | None:
    match = re.search(r"最后更新\s*(?:<br\s*/?>)?\s*([^<]+)", text, flags=re.I)
    return match.group(1).strip() if match else None


def _public_json_check(base_url: str, path: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    response = fetcher(base_url.rstrip("/") + path, timeout)
    body = str(response.get("body") or "")
    result: dict[str, Any] = {
        "http_status": response.get("status"),
        "bytes": len(body.encode("utf-8", errors="replace")),
        "content_type": _header(response.get("headers") or {}, "content-type"),
    }
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    if path == "/api/matches" and isinstance(parsed, dict):
        matches = parsed.get("matches")
        result["count"] = len(matches) if isinstance(matches, list) else None
        result["forbidden_hits"] = _forbidden_hits(body)
    return result


def _public_html_check(base_url: str, path: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    response = fetcher(base_url.rstrip("/") + path, timeout)
    body = str(response.get("body") or "")
    return {
        "http_status": response.get("status"),
        "bytes": len(body.encode("utf-8", errors="replace")),
        "content_type": _header(response.get("headers") or {}, "content-type"),
        "has_disclaimer": DISCLAIMER in body,
        "forbidden_hits": _forbidden_hits(body),
        "last_update": _last_update(body),
    }


def _public_checks(base_url: str, fetcher: Fetcher, timeout: int) -> dict[str, Any]:
    return {
        "healthz": _public_json_check(base_url, "/healthz", fetcher, timeout),
        "matches": _public_json_check(base_url, "/api/matches", fetcher, timeout),
        "snapshot_latest": _public_json_check(base_url, "/api/snapshot/latest", fetcher, timeout),
        "home": _public_html_check(base_url, "/", fetcher, timeout),
        "preview": _public_html_check(base_url, "/preview", fetcher, timeout),
    }


REMOTE_SCRIPT = r"""
from __future__ import annotations
import json, re, sqlite3, subprocess, urllib.error, urllib.request
from pathlib import Path

def run(cmd):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"active": p.returncode == 0 and p.stdout.strip() == "active"}

def fetch(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8788" + path, timeout=10) as resp:
            body = resp.read()
            return {"http_status": resp.status, "bytes": len(body), "content_type": resp.headers.get("content-type")}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {"http_status": exc.code, "bytes": len(body), "content_type": exc.headers.get("content-type")}
    except Exception:
        return {"http_status": None, "bytes": 0}

def journal_counts():
    text = subprocess.run(
        ["journalctl", "-u", "worldcup.service", "--since", "24 hours ago", "--no-pager"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return {
        "line_count": len(text.splitlines()),
        "sensitive_hits": len(re.findall(r"api[_-]?key|the_odds_api_key|ingest_hmac_secret|hmac secret|database_url|x-worldcup-signature|authorization|cookie|token|password|private[-_ ]?key|request body|body\":|signature", text, flags=re.I)),
        "error_hits": len(re.findall(r"traceback|\berror\b|exception|failed|panic|critical", text, flags=re.I)),
        "ingest_hits": len(re.findall(r"/api/ingest/snapshot|stored|duplicate|conflict", text, flags=re.I)),
    }

def nginx_counts():
    result = {}
    for path in [Path("/var/log/nginx/error.log"), Path("/var/log/nginx/access.log")]:
        if not path.exists():
            result[str(path)] = {"exists": False}
            continue
        text = path.read_text(errors="replace")[-200000:]
        result[str(path)] = {
            "exists": True,
            "sensitive_project_hits": len(re.findall(r"THE_ODDS_API_KEY|INGEST_HMAC_SECRET|DATABASE_URL|X-Worldcup-Signature|X-Worldcup-Body-SHA256", text, flags=re.I)),
            "errors_5xx_or_upstream": len(re.findall(r"\b(500|502|503|504)\b|upstream.*error|connect\(\) failed|crit|alert|emerg", text, flags=re.I)),
        }
    return result

summary = {
    "services": {
        "worldcup": run(["systemctl", "is-active", "worldcup.service"]),
        "nginx": run(["systemctl", "is-active", "nginx"]),
    },
    "release": {
        "current": str(Path("/opt/worldcup/current").resolve()) if Path("/opt/worldcup/current").exists() else None,
    },
    "local_http": {
        "/healthz": fetch("/healthz"),
        "/api/matches": fetch("/api/matches"),
        "/api/snapshot/latest": fetch("/api/snapshot/latest"),
    },
    "logs": {
        "journal": journal_counts(),
        "nginx": nginx_counts(),
    },
}
try:
    con = sqlite3.connect("/var/lib/worldcup/worldcup.db")
    con.row_factory = sqlite3.Row
    latest = con.execute("select idempotency_key, run_id, snapshot_id, snapshot_at, stored_at from snapshots order by stored_at desc limit 1").fetchone()
    summary["sqlite"] = {
        "snapshot_count": con.execute("select count(*) as c from snapshots").fetchone()["c"],
        "latest_meta": dict(latest) if latest is not None else None,
    }
    con.close()
except Exception as exc:
    summary["sqlite"] = {"error": type(exc).__name__}
print(json.dumps(summary, ensure_ascii=False))
"""


def _default_remote_runner(host: str, timeout: int) -> dict[str, Any]:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        host,
        "python3 -c " + shlex.quote(REMOTE_SCRIPT),
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _sanitize_remote(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_remote(item)
            for key, item in value.items()
            if key.lower() not in REMOTE_DROP_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_remote(item) for item in value]
    return value


def _remote_checks(host: str | None, runner: RemoteRunner, timeout: int) -> dict[str, Any]:
    if not host:
        return {"status": "skipped"}
    result = runner(host, timeout)
    if result.get("returncode") != 0:
        return {
            "status": "error",
            "host": host,
            "message": "remote_command_failed",
            "returncode": result.get("returncode"),
        }
    try:
        parsed = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        return {"status": "error", "host": host, "message": "invalid_remote_json"}
    sanitized = _sanitize_remote(parsed)
    if isinstance(sanitized, dict):
        sanitized["status"] = "ok"
        sanitized["host"] = host
        return sanitized
    return {"status": "error", "host": host, "message": "invalid_remote_shape"}


def _count_issues(result: dict[str, Any]) -> dict[str, int]:
    errors = 0
    warnings = 0

    local = result.get("local") or {}
    if (local.get("snapshot") or {}).get("status") != "ok":
        errors += 1
    for log in local.get("logs") or []:
        errors += int(log.get("sensitive_hits", 0) > 0)
        warnings += int(log.get("error_hits", 0) > 0)

    public = result.get("public") or {}
    if public.get("status") != "skipped":
        errors += int((public.get("healthz") or {}).get("http_status") != 200)
        errors += int((public.get("matches") or {}).get("http_status") != 200)
        errors += int(bool((public.get("matches") or {}).get("forbidden_hits")))
        for key in ("home", "preview"):
            item = public.get(key) or {}
            errors += int(item.get("http_status") != 200)
            errors += int(item.get("has_disclaimer") is not True)
            errors += int(bool(item.get("forbidden_hits")))

    remote = result.get("remote") or {}
    errors += int(remote.get("status") == "error")
    logs = remote.get("logs") or {}
    journal = logs.get("journal") or {}
    errors += int(journal.get("sensitive_hits", 0) > 0)
    warnings += int(journal.get("error_hits", 0) > 0)
    nginx = logs.get("nginx") or {}
    nginx_items = (
        list(nginx.values())
        if any(isinstance(item, dict) for item in nginx.values())
        else [nginx]
    )
    for item in nginx_items:
        if not isinstance(item, dict):
            continue
        errors += int(item.get("sensitive_project_hits", 0) > 0)
        warnings += int(item.get("errors_5xx_or_upstream", 0) > 0)

    return {"errors": errors, "warnings": warnings}


def run_ops_check(
    root: str | Path = ".",
    public_base_url: str | None = DEFAULT_PUBLIC_BASE_URL,
    remote_host: str | None = DEFAULT_REMOTE_HOST,
    fetcher: Fetcher = _default_fetcher,
    remote_runner: RemoteRunner = _default_remote_runner,
    launch_agent_path: str | Path = DEFAULT_LAUNCH_AGENT,
    local_log_paths: list[str | Path] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    project_root = Path(root)
    result: dict[str, Any] = {
        "schema_version": 1,
        "local": _local_checks(
            project_root,
            launch_agent_path=launch_agent_path,
            local_log_paths=list(local_log_paths or DEFAULT_LOCAL_LOGS),
        ),
        "public": {"status": "skipped"}
        if public_base_url is None
        else _public_checks(public_base_url, fetcher=fetcher, timeout=timeout),
        "remote": _remote_checks(remote_host, runner=remote_runner, timeout=timeout),
    }
    summary = _count_issues(result)
    result["summary"] = summary
    result["ok"] = summary["errors"] == 0
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only operational checks for local scheduler, public site, and ECS."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--public-base-url", default=DEFAULT_PUBLIC_BASE_URL)
    parser.add_argument("--no-public", action="store_true")
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--no-remote", action="store_true")
    parser.add_argument("--launch-agent", default=str(DEFAULT_LAUNCH_AGENT))
    parser.add_argument("--local-log", action="append", dest="local_logs")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)

    result = run_ops_check(
        root=args.root,
        public_base_url=None if args.no_public else args.public_base_url,
        remote_host=None if args.no_remote else args.remote_host,
        launch_agent_path=args.launch_agent,
        local_log_paths=args.local_logs,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
