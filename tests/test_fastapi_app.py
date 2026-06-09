import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from worldcup.fastapi_app import build_store_from_env, create_fastapi_app
from worldcup.ingest import build_ingest_request
from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore


class MemorySnapshotStore:
    def __init__(self, latest=None):
        self.latest = latest

    def initialize(self):
        pass

    def put_snapshot(self, idempotency_key, payload, stored_at=None):
        self.latest = {
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
            "snapshot_at": payload.get("snapshot_at"),
            "stored_at": stored_at,
            "payload": payload,
            "snapshot": payload["snapshot"],
        }
        return {
            "status": "stored",
            "idempotency_key": idempotency_key,
            "run_id": payload["run_id"],
            "snapshot_id": payload["snapshot_id"],
        }

    def count_snapshots(self):
        return 1 if self.latest else 0

    def latest_snapshot(self):
        return self.latest


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "run-1"},
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A"}],
            }
        ],
    }


def _store_snapshot(db_path: Path):
    SQLiteSnapshotStore(db_path).put_snapshot(
        idempotency_key="run-1:snapshot-1",
        payload={
            "run_id": "run-1",
            "snapshot_id": "snapshot-1",
            "snapshot_at": "2026-06-08T00:00:00+00:00",
            "snapshot": _snapshot(),
        },
        stored_at="2026-06-08T00:02:00+00:00",
    )


def test_fastapi_build_store_from_env_defaults_to_sqlite():
    store = build_store_from_env(
        env={},
        db_path="data/local/worldcup.db",
        store_arg=None,
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, SQLiteSnapshotStore)


def test_fastapi_build_store_from_env_supports_postgres_without_connecting():
    store = build_store_from_env(
        env={"WORLDCUP_STORE": "postgres", "DATABASE_URL": "postgresql://example.invalid/worldcup"},
        db_path="unused.db",
        store_arg=None,
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, PostgresSnapshotStore)
    assert store.dsn == "postgresql://example.invalid/worldcup"


def test_fastapi_build_store_from_env_cli_arg_overrides_env_store_kind():
    store = build_store_from_env(
        env={"WORLDCUP_STORE": "postgres"},
        db_path="data/local/worldcup.db",
        store_arg="sqlite",
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, SQLiteSnapshotStore)


def test_fastapi_healthz_does_not_require_db_or_secret():
    with TemporaryDirectory() as tmp:
        app = create_fastapi_app(db_path=Path(tmp) / "missing.db", secret="")
        client = TestClient(app)

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {
            "schema_version": 1,
            "service": "worldcup-analysis",
            "status": "ok",
        }


def test_fastapi_get_matches_returns_safe_projection():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/api/matches")

        assert response.status_code == 200
        row = response.json()["matches"][0]
        assert row["match_label"] == "Mexico vs South Africa"
        assert "stake" not in row
        assert "bet_amount" not in row


def test_fastapi_get_matches_uses_injected_store():
    store = MemorySnapshotStore(latest={"snapshot": _snapshot()})
    app = create_fastapi_app(db_path="unused.db", secret="test-hmac-secret", store=store)
    client = TestClient(app)

    response = client.get("/api/matches")

    assert response.status_code == 200
    row = response.json()["matches"][0]
    assert row["match_label"] == "Mexico vs South Africa"


def test_fastapi_get_preview_returns_disclaimer_html():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/preview")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "研究分析工具，不构成投注建议" in response.text
        assert "Mexico vs South Africa" in response.text


def test_fastapi_latest_snapshot_returns_404_when_missing():
    with TemporaryDirectory() as tmp:
        app = create_fastapi_app(db_path=Path(tmp) / "empty.db", secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/api/snapshot/latest")

        assert response.status_code == 404
        assert response.json()["error"] == "snapshot_not_found"


def test_fastapi_post_ingest_snapshot_stores_signed_request():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        timestamp = datetime.now(timezone.utc).isoformat()
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp=timestamp,
        )
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "stored"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1


def test_fastapi_post_ingest_snapshot_rejects_bad_signature_without_writing():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        timestamp = datetime.now(timezone.utc).isoformat()
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp=timestamp,
        )
        headers = dict(request["headers"])
        headers["X-Worldcup-Signature"] = "sha256=bad"
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.post(
            "/api/ingest/snapshot",
            headers=headers,
            content=request["body"],
        )

        body = response.json()
        assert response.status_code == 400
        assert body["status"] == "rejected"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 0


def test_fastapi_post_ingest_snapshot_duplicate_is_idempotent():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        timestamp = datetime.now(timezone.utc).isoformat()
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp=timestamp,
        )
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        first = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )
        second = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )

        assert first.json()["status"] == "stored"
        assert second.json()["status"] == "duplicate"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1
