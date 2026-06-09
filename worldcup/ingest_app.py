from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from worldcup.ingest import build_ingest_request
from worldcup.ingest_server import DEFAULT_REPLAY_WINDOW_SECONDS, verify_ingest_request
from worldcup.refresh_runner import _load_env
from worldcup.store import SQLiteSnapshotStore


def process_local_ingest(
    db_path: str | Path,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    secret: str,
    now: str | None = None,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> dict[str, Any]:
    verification = verify_ingest_request(
        method=method,
        path=path,
        headers=headers,
        body=body,
        secret=secret,
        now=now,
        replay_window_seconds=replay_window_seconds,
    )
    store = SQLiteSnapshotStore(db_path)
    if not verification.ok:
        store.initialize()
        return {
            "status": "rejected",
            "reason": verification.reason,
        }

    assert verification.idempotency_key is not None
    assert verification.payload is not None
    result = store.put_snapshot(
        idempotency_key=verification.idempotency_key,
        payload=verification.payload,
        stored_at=now,
    )
    return result


def build_local_ingest_request_from_snapshot(
    snapshot_path: str | Path,
    endpoint: str,
    secret: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    return build_ingest_request(
        snapshot=snapshot,
        endpoint=endpoint,
        secret=secret,
        timestamp=timestamp,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and store a local ingest request into SQLite.")
    parser.add_argument("--db", default="data/local/worldcup.db")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--endpoint", default="https://example.invalid/api/ingest/snapshot")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default="INGEST_HMAC_SECRET")
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)

    secret = _load_env(args.env).get(args.secret_env)
    if not secret:
        raise SystemExit(f"{args.secret_env} is missing in {args.env}")

    request = build_local_ingest_request_from_snapshot(
        snapshot_path=args.snapshot,
        endpoint=args.endpoint,
        secret=secret,
        timestamp=args.timestamp,
    )
    result = process_local_ingest(
        db_path=args.db,
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret=secret,
        now=args.now or args.timestamp,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
