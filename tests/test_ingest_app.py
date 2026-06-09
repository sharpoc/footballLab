from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ingest import build_ingest_request
from worldcup.ingest_app import build_store_from_env, process_local_ingest
from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore


class MemorySnapshotStore:
    def __init__(self):
        self.puts = []
        self.initialized = False

    def initialize(self):
        self.initialized = True

    def put_snapshot(self, idempotency_key, payload, stored_at=None):
        self.puts.append((idempotency_key, payload, stored_at))
        return {
            "status": "stored",
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
        }

    def count_snapshots(self):
        return len(self.puts)

    def latest_snapshot(self):
        if not self.puts:
            return None
        idempotency_key, payload, stored_at = self.puts[-1]
        return {
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
            "snapshot_at": payload.get("snapshot_at"),
            "stored_at": stored_at,
            "payload": payload,
            "snapshot": payload["snapshot"],
        }


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "20260608T000000Z-live"},
        "counts": {"matches": 1},
        "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
    }


def _request(secret="test-hmac-secret"):
    return build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret=secret,
        timestamp="2026-06-08T00:02:00+00:00",
    )


def test_ingest_build_store_from_env_defaults_to_sqlite():
    store = build_store_from_env(
        env={},
        db_path="data/local/worldcup.db",
        store_arg=None,
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, SQLiteSnapshotStore)


def test_ingest_build_store_from_env_supports_postgres_without_connecting():
    store = build_store_from_env(
        env={"WORLDCUP_STORE": "postgres", "DATABASE_URL": "postgresql://example.invalid/worldcup"},
        db_path="unused.db",
        store_arg=None,
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, PostgresSnapshotStore)
    assert store.dsn == "postgresql://example.invalid/worldcup"


def test_ingest_build_store_from_env_cli_arg_overrides_env_store_kind():
    store = build_store_from_env(
        env={"WORLDCUP_STORE": "postgres"},
        db_path="data/local/worldcup.db",
        store_arg="sqlite",
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, SQLiteSnapshotStore)


def test_process_local_ingest_stores_signed_request_in_sqlite():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = _request()

        result = process_local_ingest(
            db_path=db_path,
            method=request["method"],
            path=request["path"],
            headers=request["headers"],
            body=request["body"],
            secret="test-hmac-secret",
            now="2026-06-08T00:03:00+00:00",
        )

        latest = SQLiteSnapshotStore(db_path).latest_snapshot()
        assert result["status"] == "stored"
        assert result["run_id"] == "20260608T000000Z-live"
        assert latest["snapshot"]["counts"]["matches"] == 1


def test_process_local_ingest_writes_to_injected_store():
    store = MemorySnapshotStore()
    request = _request()

    result = process_local_ingest(
        db_path="unused.db",
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
    )

    latest = store.latest_snapshot()
    assert result["status"] == "stored"
    assert store.count_snapshots() == 1
    assert latest["snapshot"]["counts"]["matches"] == 1


def test_process_local_ingest_returns_duplicate_for_same_idempotency_key():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = _request()
        kwargs = {
            "db_path": db_path,
            "method": request["method"],
            "path": request["path"],
            "headers": request["headers"],
            "body": request["body"],
            "secret": "test-hmac-secret",
        }

        first = process_local_ingest(**kwargs, now="2026-06-08T00:03:00+00:00")
        second = process_local_ingest(**kwargs, now="2026-06-08T00:04:00+00:00")

        assert first["status"] == "stored"
        assert second["status"] == "duplicate"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1


def test_process_local_ingest_rejects_tampered_body_without_writing():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = _request()

        result = process_local_ingest(
            db_path=db_path,
            method=request["method"],
            path=request["path"],
            headers=request["headers"],
            body=request["body"].replace("Mexico", "Canada"),
            secret="test-hmac-secret",
            now="2026-06-08T00:03:00+00:00",
        )

        assert result["status"] == "rejected"
        assert result["reason"] == "body_hash_mismatch"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 0
