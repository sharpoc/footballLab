from pathlib import Path

from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_factory import create_snapshot_store, normalize_store_kind


def test_normalize_store_kind_defaults_to_sqlite():
    assert normalize_store_kind(None) == "sqlite"
    assert normalize_store_kind("") == "sqlite"
    assert normalize_store_kind(" PostgreSQL ") == "postgres"


def test_create_snapshot_store_returns_sqlite_by_default():
    store = create_snapshot_store(store_kind=None, db_path=Path("data/local/worldcup.db"))

    assert isinstance(store, SQLiteSnapshotStore)
    assert store.path == Path("data/local/worldcup.db")


def test_create_snapshot_store_returns_postgres_without_connecting():
    store = create_snapshot_store(
        store_kind="postgres",
        db_path=Path("unused.db"),
        database_url="postgresql://example.invalid/worldcup",
    )

    assert isinstance(store, PostgresSnapshotStore)
    assert store.dsn == "postgresql://example.invalid/worldcup"


def test_create_snapshot_store_requires_database_url_for_postgres():
    try:
        create_snapshot_store(store_kind="postgres", db_path=Path("unused.db"), database_url="")
    except ValueError as exc:
        assert str(exc) == "DATABASE_URL is required when WORLDCUP_STORE=postgres"
    else:
        raise AssertionError("expected ValueError")


def test_create_snapshot_store_rejects_unknown_store_kind():
    try:
        create_snapshot_store(store_kind="mysql", db_path=Path("unused.db"))
    except ValueError as exc:
        assert str(exc) == "Unsupported WORLDCUP_STORE: mysql"
    else:
        raise AssertionError("expected ValueError")
