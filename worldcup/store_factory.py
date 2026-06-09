from __future__ import annotations

from pathlib import Path

from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore


def normalize_store_kind(value: str | None) -> str:
    normalized = (value or "sqlite").strip().lower()
    if normalized in {"", "sqlite"}:
        return "sqlite"
    if normalized in {"postgres", "postgresql"}:
        return "postgres"
    return normalized


def create_snapshot_store(
    store_kind: str | None,
    db_path: str | Path,
    database_url: str | None = None,
) -> SnapshotStore:
    kind = normalize_store_kind(store_kind)
    if kind == "sqlite":
        return SQLiteSnapshotStore(db_path)
    if kind == "postgres":
        dsn = (database_url or "").strip()
        if not dsn:
            raise ValueError("DATABASE_URL is required when WORLDCUP_STORE=postgres")
        return PostgresSnapshotStore(dsn)
    raise ValueError(f"Unsupported WORLDCUP_STORE: {kind}")
