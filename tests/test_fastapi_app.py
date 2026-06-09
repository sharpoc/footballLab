import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from worldcup.fastapi_app import create_fastapi_app
from worldcup.store import SQLiteSnapshotStore


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
