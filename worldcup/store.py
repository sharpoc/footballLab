from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.store_contract import SnapshotStore


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_snapshot_record(row: sqlite3.Row) -> dict[str, Any]:
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


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _competition_id_like_patterns(competition_id: str) -> tuple[str, str]:
    encoded_id = _escape_like(json.dumps(competition_id, ensure_ascii=False))
    return (f'%"id": {encoded_id}%', f'%"id":{encoded_id}%')


_BUSY_TIMEOUT_MS = 5000


class SQLiteSnapshotStore(SnapshotStore):
    supports_atomic_league_publication = True
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000)
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return conn

    def initialize(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
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
        self._initialized = True

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
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT idempotency_key FROM snapshots WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
            if existing is not None:
                return {"status": "duplicate", "idempotency_key": idempotency_key,
                        "run_id": payload["run_id"], "snapshot_id": payload["snapshot_id"]}
            if (snapshot.get("competition") or {}).get("id") == "multi_league" or "league_publication" in snapshot:
                from worldcup.league_publication import validate_publication_transition
                previous = conn.execute(
                    "SELECT snapshot_json, MAX(stored_at) OVER () FROM snapshots WHERE json_extract(snapshot_json, '$.competition.id') = ? ORDER BY rowid DESC LIMIT 1",
                    ("multi_league",),
                ).fetchone()
                validate_publication_transition(json.loads(previous[0]) if previous else None, snapshot)
                if previous is not None and "league_publication" in snapshot:
                    # Public readers order by stored_at then rowid. A request may
                    # capture its clock before waiting for this transaction lock;
                    # never let that earlier clock hide a later accepted version.
                    stored = max(stored, previous[1])
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
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        return int(row[0])

    def latest_snapshot(self) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as conn:
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
        return _row_to_snapshot_record(row)

    def list_recent_snapshots(self, limit: int = 2) -> list[dict[str, Any]]:
        self.initialize()
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
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
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [_row_to_snapshot_record(row) for row in rows]

    def list_latest_snapshots_by_competition(
        self,
        competition_ids: list[str],
        per_competition_limit: int = 1,
    ) -> list[dict[str, Any]]:
        self.initialize()
        bounded_limit = max(1, min(int(per_competition_limit), 500))
        requested_ids: list[str] = []
        for competition_id in competition_ids:
            normalized = str(competition_id or "").strip()
            if normalized and normalized not in requested_ids:
                requested_ids.append(normalized)

        records: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            for competition_id in requested_ids:
                spaced_pattern, compact_pattern = _competition_id_like_patterns(competition_id)
                rows = conn.execute(
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
                    WHERE snapshot_json LIKE ? ESCAPE '\\'
                       OR snapshot_json LIKE ? ESCAPE '\\'
                    ORDER BY stored_at DESC, rowid DESC
                    LIMIT ?
                    """,
                    (spaced_pattern, compact_pattern, bounded_limit),
                ).fetchall()
                for row in rows:
                    idempotency_key = str(row["idempotency_key"])
                    if idempotency_key in seen_keys:
                        continue
                    seen_keys.add(idempotency_key)
                    records.append(_row_to_snapshot_record(row))
        return records
