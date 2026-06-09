from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from worldcup.ingest import build_ingest_request
from worldcup.ingest_server import DEFAULT_REPLAY_WINDOW_SECONDS, verify_ingest_request
from worldcup.refresh_runner import _load_env
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_factory import create_snapshot_store
from worldcup.store_contract import SnapshotStore


def process_local_ingest(
    db_path: str | Path,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    secret: str,
    now: str | None = None,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
    store: SnapshotStore | None = None,
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
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    if not verification.ok:
        snapshot_store.initialize()
        return {
            "status": "rejected",
            "reason": verification.reason,
        }

    assert verification.idempotency_key is not None
    assert verification.payload is not None
    result = snapshot_store.put_snapshot(
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


def build_store_from_env(
    env: dict[str, str],
    db_path: str | Path,
    store_arg: str | None,
    database_url_env: str,
) -> SnapshotStore:
    store_kind = store_arg or env.get("WORLDCUP_STORE")
    return create_snapshot_store(
        store_kind=store_kind,
        db_path=db_path,
        database_url=env.get(database_url_env),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and store a local ingest request.")
    parser.add_argument("--db", default="data/local/worldcup.db")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--endpoint", default="https://example.invalid/api/ingest/snapshot")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default="INGEST_HMAC_SECRET")
    parser.add_argument("--store", default=None, choices=["sqlite", "postgres"])
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args(argv)

    env = _load_env(args.env)
    secret = env.get(args.secret_env)
    if not secret:
        raise SystemExit(f"{args.secret_env} is missing in {args.env}")
    store = build_store_from_env(
        env=env,
        db_path=args.db,
        store_arg=args.store,
        database_url_env=args.database_url_env,
    )

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
        store=store,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
