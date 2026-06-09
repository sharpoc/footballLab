from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from worldcup.store_contract import SnapshotStore

ConnectionFactory = Callable[[str], Any]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresSnapshotStore(SnapshotStore):
    def __init__(self, dsn: str, connection_factory: ConnectionFactory | None = None) -> None:
        self.dsn = dsn
        self.connection_factory = connection_factory

    def _connect(self):
        if self.connection_factory is not None:
            return self.connection_factory(self.dsn)
        try:
            import psycopg
        except ModuleNotFoundError as exc:
            raise RuntimeError("psycopg is required for PostgresSnapshotStore; install worldcup[postgres]") from exc
        return psycopg.connect(self.dsn)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                  idempotency_key TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  snapshot_at TIMESTAMPTZ,
                  stored_at TIMESTAMPTZ NOT NULL,
                  payload_json JSONB NOT NULL,
                  snapshot_json JSONB NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_stored_at
                ON snapshots(stored_at)
                """
            )
            conn.commit()

    def put_snapshot(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
        stored_at: str | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        stored = stored_at or _now_utc_iso()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        snapshot = payload.get("snapshot") or {}
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO snapshots (
                  idempotency_key,
                  run_id,
                  snapshot_id,
                  snapshot_at,
                  stored_at,
                  payload_json,
                  snapshot_json
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """,
                (
                    idempotency_key,
                    payload["run_id"],
                    payload["snapshot_id"],
                    payload.get("snapshot_at"),
                    stored,
                    payload_json,
                    snapshot_json,
                ),
            )
            row = cursor.fetchone()
            conn.commit()
        return {
            "status": "stored" if row is not None else "duplicate",
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
        }

    def count_snapshots(self) -> int:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        return int(row[0])

    def latest_snapshot(self) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                  idempotency_key,
                  run_id,
                  snapshot_id,
                  snapshot_at,
                  stored_at,
                  payload_json,
                  snapshot_json
                FROM snapshots
                ORDER BY stored_at DESC, idempotency_key DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        idempotency_key, run_id, snapshot_id, snapshot_at, stored_at, payload_json, snapshot_json = row
        payload = _json_loads(payload_json)
        snapshot = _json_loads(snapshot_json)
        return {
            "idempotency_key": idempotency_key,
            "run_id": run_id,
            "snapshot_id": snapshot_id,
            "snapshot_at": snapshot_at,
            "stored_at": stored_at,
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "snapshot_json": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            "payload": payload,
            "snapshot": snapshot,
        }
