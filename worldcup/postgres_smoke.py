from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.ingest import build_ingest_request
from worldcup.refresh_runner import _load_env
from worldcup.store_factory import normalize_store_kind

DEFAULT_ENDPOINT = "https://example.invalid/api/ingest/snapshot"
DEFAULT_SECRET_ENV = "INGEST_HMAC_SECRET"
DEFAULT_DATABASE_URL_ENV = "DATABASE_URL"


def _ok(**kwargs) -> dict[str, Any]:
    return {"status": "ok", **kwargs}


def _error(message: str, **kwargs) -> dict[str, Any]:
    return {"status": "error", "message": message, **kwargs}


def _check_store(env: dict[str, str]) -> dict[str, Any]:
    store = normalize_store_kind(env.get("WORLDCUP_STORE"))
    if store != "postgres":
        return _error("expected_postgres", store=store)
    return _ok(store=store)


def _check_present(env: dict[str, str], name: str) -> dict[str, Any]:
    if env.get(name):
        return _ok(name=name, present=True)
    return _error(f"missing_{name}", name=name, present=False)


def _load_snapshot(path: str | Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return None, _error("missing", path=str(path))
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, _error("invalid_json", path=str(path))
    except OSError:
        return None, _error("unreadable", path=str(path))
    if not isinstance(data, dict):
        return None, _error("not_object", path=str(path))
    matches = data.get("matches")
    return data, _ok(path=str(path), matches=len(matches) if isinstance(matches, list) else None)


def _redacted_request_summary(request: dict[str, Any]) -> dict[str, Any]:
    headers = request["headers"]
    safe_header_names = sorted(name for name in headers if name != "X-Worldcup-Signature")
    return {
        "method": request["method"],
        "url": request["url"],
        "path": request["path"],
        "header_names": safe_header_names,
        "run_id": headers["X-Worldcup-Run-Id"],
        "snapshot_id": headers["X-Worldcup-Snapshot-Id"],
        "body_sha256": headers["X-Worldcup-Body-SHA256"],
        "idempotency_key": headers["X-Worldcup-Idempotency-Key"],
        "body_bytes": len(request["body"].encode("utf-8")),
    }


def build_postgres_smoke_dry_run(
    snapshot_path: str | Path,
    endpoint: str,
    env: dict[str, str],
    timestamp: str | None = None,
    secret_env: str = DEFAULT_SECRET_ENV,
    database_url_env: str = DEFAULT_DATABASE_URL_ENV,
) -> dict[str, Any]:
    snapshot, snapshot_check = _load_snapshot(snapshot_path)
    checks = {
        "store": _check_store(env),
        "database_url": _check_present(env, database_url_env),
        "secret": _check_present(env, secret_env),
        "snapshot": snapshot_check,
    }
    if any(check["status"] == "error" for check in checks.values()):
        return {
            "status": "blocked",
            "checks": checks,
            "expected_sequence": ["stored", "duplicate"],
        }

    assert snapshot is not None
    try:
        request = build_ingest_request(
            snapshot=snapshot,
            endpoint=endpoint,
            secret=env[secret_env],
            timestamp=timestamp,
        )
    except (KeyError, TypeError, ValueError) as exc:
        checks["request"] = _error(str(exc))
        return {
            "status": "blocked",
            "checks": checks,
            "expected_sequence": ["stored", "duplicate"],
        }

    checks["request"] = _ok()
    return {
        "status": "dry_run_ready",
        "checks": checks,
        "request": _redacted_request_summary(request),
        "expected_sequence": ["stored", "duplicate"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a redacted PostgreSQL smoke dry-run summary.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default=DEFAULT_SECRET_ENV)
    parser.add_argument("--database-url-env", default=DEFAULT_DATABASE_URL_ENV)
    parser.add_argument("--timestamp", default=None)
    args = parser.parse_args(argv)

    result = build_postgres_smoke_dry_run(
        snapshot_path=args.snapshot,
        endpoint=args.endpoint,
        env=_load_env(args.env),
        timestamp=args.timestamp,
        secret_env=args.secret_env,
        database_url_env=args.database_url_env,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "dry_run_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
