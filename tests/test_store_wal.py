"""Tests for SQLite WAL mode and HTTP 503 on storage errors."""

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.store import SQLiteSnapshotStore
from worldcup.http_app import handle_request


def test_sqlite_store_enables_wal_after_initialize():
    """After initialize(), journal_mode is wal."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteSnapshotStore(db_path)
        store.initialize()
        with sqlite3.connect(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_sqlite_store_wal_persists_across_connections():
    """WAL mode persists for new connections to the same DB."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteSnapshotStore(db_path)
        store.initialize()
        # New raw connection also sees WAL
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


def test_sqlite_store_busy_timeout_is_set():
    """Connections created by the store have busy_timeout = 5000."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteSnapshotStore(db_path)
        store.initialize()
        conn = store._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert timeout == 5000


def test_sqlite_store_put_and_read_with_wal():
    """Basic put/read still works with WAL enabled."""
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteSnapshotStore(db_path)
        payload = {
            "run_id": "run-1",
            "snapshot_id": "snap-1",
            "snapshot_at": "2026-07-20T00:00:00Z",
            "snapshot": {"competition": {"id": "test"}},
        }
        result = store.put_snapshot("key-1", payload)
        assert result["status"] == "stored"
        latest = store.latest_snapshot()
        assert latest["idempotency_key"] == "key-1"


def _make_store_with_data(tmp_dir):
    """Helper: create a store with one snapshot."""
    db_path = Path(tmp_dir) / "test.db"
    store = SQLiteSnapshotStore(db_path)
    payload = {
        "run_id": "run-1",
        "snapshot_id": "snap-1",
        "snapshot_at": "2026-07-20T00:00:00Z",
        "snapshot": {
            "competition": {"id": "fifa_world_cup_2026"},
            "matches": [],
        },
    }
    store.put_snapshot("key-1", payload)
    return db_path, store


def test_http_returns_503_on_database_locked():
    """When store raises OperationalError, HTTP returns 503 JSON."""
    with TemporaryDirectory() as tmp:
        db_path, store = _make_store_with_data(tmp)

        with patch(
            "worldcup.http_app.load_latest_snapshot_view",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            response = handle_request(
                method="GET",
                path="/api/matches",
                headers={},
                body="",
                db_path=str(db_path),
                secret="test-secret",
                store=store,
            )

        assert response["status"] == 503
        assert response["headers"]["Content-Type"] == "application/json"
        body = json.loads(response["body"])
        assert body["error"]["code"] == "service_unavailable"
        # Must not leak internal details
        assert "locked" not in response["body"]
        assert "sqlite" not in response["body"].lower()


def test_http_returns_503_on_ingest_database_error():
    """POST /api/ingest/snapshot returns 503 when store has DB error."""
    with TemporaryDirectory() as tmp:
        db_path, store = _make_store_with_data(tmp)

        with patch(
            "worldcup.http_app.process_local_ingest",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            response = handle_request(
                method="POST",
                path="/api/ingest/snapshot",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "2",
                },
                body="{}",
                db_path=str(db_path),
                secret="test-secret",
                store=store,
            )

        assert response["status"] == 503
        body = json.loads(response["body"])
        assert body["error"]["code"] == "service_unavailable"


def test_http_normal_get_still_works():
    """Normal GET /api/matches still returns 200 with data."""
    with TemporaryDirectory() as tmp:
        db_path, store = _make_store_with_data(tmp)

        response = handle_request(
            method="GET",
            path="/api/matches",
            headers={},
            body="",
            db_path=str(db_path),
            secret="test-secret",
            store=store,
        )

        assert response["status"] == 200


def test_http_healthz_unaffected_by_store_errors():
    """/healthz does not touch the store, so DB errors don't affect it."""
    response = handle_request(
        method="GET",
        path="/healthz",
        headers={},
        body="",
        db_path="/nonexistent/path.db",
        secret="test-secret",
    )
    assert response["status"] == 200


def test_http_503_does_not_leak_path_or_traceback():
    """503 response body must not contain file paths or stack traces."""
    with TemporaryDirectory() as tmp:
        db_path, store = _make_store_with_data(tmp)

        with patch(
            "worldcup.http_app.load_latest_snapshot_view",
            side_effect=sqlite3.OperationalError(f"unable to open database file: {db_path}"),
        ):
            response = handle_request(
                method="GET",
                path="/api/matches",
                headers={},
                body="",
                db_path=str(db_path),
                secret="test-secret",
                store=store,
            )

        assert response["status"] == 503
        assert str(db_path) not in response["body"]
        assert "Traceback" not in response["body"]
        assert "File" not in response["body"]
