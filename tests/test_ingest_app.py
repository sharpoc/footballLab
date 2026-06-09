from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ingest import build_ingest_request
from worldcup.ingest_app import process_local_ingest
from worldcup.store import SQLiteSnapshotStore


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
