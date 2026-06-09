from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from worldcup.ingest import build_ingest_request
from worldcup.refresh_runner import _load_env

DEFAULT_ENDPOINT = "https://example.invalid/api/ingest/snapshot"
DEFAULT_SECRET_ENV = "INGEST_HMAC_SECRET"


Sender = Callable[[dict[str, Any]], dict[str, Any]]


def _default_sender(request: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        request["url"],
        data=request["body"].encode("utf-8"),
        headers=request["headers"],
        method=request["method"],
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return {
                "http_status": response.status,
                "body": response.read().decode("utf-8"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "http_status": exc.code,
            "body": exc.read().decode("utf-8", errors="replace"),
        }


def _redacted_request_summary(request: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(request["body"])
    header_names = sorted(
        name for name in request["headers"] if name.lower() != "x-worldcup-signature"
    )
    return {
        "method": request["method"],
        "url": request["url"],
        "path": request["path"],
        "header_names": header_names,
        "run_id": payload["run_id"],
        "snapshot_id": payload["snapshot_id"],
        "idempotency_key": request["headers"]["X-Worldcup-Idempotency-Key"],
        "body_sha256": request["headers"]["X-Worldcup-Body-SHA256"],
        "body_bytes": len(request["body"].encode("utf-8")),
    }


def _parse_response_body(body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def publish_snapshot(
    snapshot_path: str | Path,
    endpoint: str,
    secret: str,
    timestamp: str | None = None,
    live: bool = False,
    sender: Sender | None = None,
) -> dict[str, Any]:
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    request = build_ingest_request(
        snapshot=snapshot,
        endpoint=endpoint,
        secret=secret,
        timestamp=timestamp,
    )
    summary = _redacted_request_summary(request)

    if not live:
        return {
            "status": "dry_run",
            "request": summary,
        }

    send = sender or _default_sender
    response = send(request)
    parsed_body = _parse_response_body(str(response.get("body", "")))
    return {
        "status": "sent",
        "http_status": response.get("http_status"),
        "ingest_status": parsed_body.get("status"),
        "request": summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a signed snapshot to the ingest endpoint. Defaults to dry-run."
    )
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default=DEFAULT_SECRET_ENV)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--live", action="store_true", help="Actually send the signed request.")
    args = parser.parse_args(argv)

    secret = _load_env(args.env).get(args.secret_env)
    if not secret:
        raise SystemExit(f"{args.secret_env} is missing in {args.env}")

    result = publish_snapshot(
        snapshot_path=args.snapshot_path,
        endpoint=args.endpoint,
        secret=secret,
        timestamp=args.timestamp,
        live=args.live,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
