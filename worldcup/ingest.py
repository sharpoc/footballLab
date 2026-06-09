from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from worldcup.refresh_runner import _load_env

DEFAULT_ENDPOINT = "https://example.invalid/api/ingest/snapshot"
DEFAULT_SECRET_ENV = "INGEST_HMAC_SECRET"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _endpoint_path(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def build_ingest_payload(snapshot: dict[str, Any], generated_at: str | None = None) -> dict[str, Any]:
    run = snapshot.get("run") or {}
    run_id = run.get("run_id")
    if not run_id:
        raise ValueError("snapshot.run.run_id is required for ingest")

    snapshot_body = canonical_json(snapshot)
    snapshot_id = _sha256_text(snapshot_body)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "snapshot_at": snapshot.get("snapshot_at"),
        "generated_at": generated_at or _now_utc_iso(),
        "snapshot": snapshot,
    }


def _signature_message(
    timestamp: str,
    method: str,
    path: str,
    run_id: str,
    snapshot_id: str,
    body_sha256: str,
) -> str:
    return "\n".join([timestamp, method.upper(), path, run_id, snapshot_id, body_sha256])


def build_ingest_request(
    snapshot: dict[str, Any],
    endpoint: str,
    secret: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    if not secret:
        raise ValueError("HMAC secret is required")

    observed = timestamp or _now_utc_iso()
    method = "POST"
    path = _endpoint_path(endpoint)
    payload = build_ingest_payload(snapshot, generated_at=observed)
    body = canonical_json(payload)
    body_sha256 = _sha256_text(body)
    message = _signature_message(
        timestamp=observed,
        method=method,
        path=path,
        run_id=payload["run_id"],
        snapshot_id=payload["snapshot_id"],
        body_sha256=body_sha256,
    )
    signature = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "method": method,
        "url": endpoint,
        "path": path,
        "headers": {
            "Content-Type": "application/json",
            "X-Worldcup-Timestamp": observed,
            "X-Worldcup-Run-Id": payload["run_id"],
            "X-Worldcup-Snapshot-Id": payload["snapshot_id"],
            "X-Worldcup-Body-SHA256": body_sha256,
            "X-Worldcup-Signature": f"sha256={signature}",
            "X-Worldcup-Idempotency-Key": f"{payload['run_id']}:{payload['snapshot_id']}",
        },
        "body": body,
    }


def build_ingest_dry_run(
    snapshot_path: str | Path,
    endpoint: str,
    secret: str,
    timestamp: str | None = None,
    include_body: bool = False,
) -> dict[str, Any]:
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    request = build_ingest_request(
        snapshot=snapshot,
        endpoint=endpoint,
        secret=secret,
        timestamp=timestamp,
    )
    body = request["body"]
    return {
        "status": "dry_run",
        "request": {
            "method": request["method"],
            "url": request["url"],
            "path": request["path"],
            "headers": request["headers"],
            "body_sha256": request["headers"]["X-Worldcup-Body-SHA256"],
            "body_bytes": len(body.encode("utf-8")),
            "body": body if include_body else None,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a signed ingest request preview without sending it.")
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default=DEFAULT_SECRET_ENV)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--include-body", action="store_true")
    args = parser.parse_args(argv)

    secret = _load_env(args.env).get(args.secret_env)
    if not secret:
        raise SystemExit(f"{args.secret_env} is missing in {args.env}")

    result = build_ingest_dry_run(
        snapshot_path=args.snapshot_path,
        endpoint=args.endpoint,
        secret=secret,
        timestamp=args.timestamp,
        include_body=args.include_body,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
