from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.store_contract import SnapshotStore


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteSnapshotStore(SnapshotStore):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                  idempotency_key TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  snapshot_at TEXT,
                  stored_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  snapshot_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_stored_at
                ON snapshots(stored_at)
                """
            )

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
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO snapshots (
                  idempotency_key,
                  run_id,
                  snapshot_id,
                  snapshot_at,
                  stored_at,
                  payload_json,
                  snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
        return {
            "status": "stored" if cursor.rowcount == 1 else "duplicate",
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
        }

    def count_snapshots(self) -> int:
        self.initialize()
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        return int(row[0])

    def latest_snapshot(self) -> dict[str, Any] | None:
        self.initialize()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
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
                ORDER BY stored_at DESC, rowid DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        snapshot = json.loads(row["snapshot_json"])
        return {
            "idempotency_key": row["idempotency_key"],
            "run_id": row["run_id"],
            "snapshot_id": row["snapshot_id"],
            "snapshot_at": row["snapshot_at"],
            "stored_at": row["stored_at"],
            "payload_json": row["payload_json"],
            "snapshot_json": row["snapshot_json"],
            "payload": payload,
            "snapshot": snapshot,
        }
