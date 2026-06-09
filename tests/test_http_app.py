import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.http_app import handle_request
from worldcup.ingest import build_ingest_request
from worldcup.store import SQLiteSnapshotStore


def _snapshot(run_id="20260608T000000Z-live"):
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": run_id},
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A"}],
            }
        ],
    }


def _store_snapshot(db_path: Path):
    store = SQLiteSnapshotStore(db_path)
    store.put_snapshot(
        idempotency_key="run-1:snapshot-1",
        payload={
            "run_id": "run-1",
            "snapshot_id": "snapshot-1",
            "snapshot_at": "2026-06-08T00:00:00+00:00",
            "snapshot": _snapshot("run-1"),
        },
        stored_at="2026-06-08T00:02:00+00:00",
    )


def test_http_get_matches_returns_projected_rows():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)

        response = handle_request(
            method="GET",
            path="/api/matches",
            headers={},
            body="",
            db_path=db_path,
            secret="test-hmac-secret",
        )

        body = json.loads(response["body"])
        assert response["status"] == 200
        assert response["headers"]["Content-Type"] == "application/json"
        assert body["matches"][0]["match_label"] == "Mexico vs South Africa"
        assert "stake" not in body["matches"][0]


def test_http_healthz_returns_ok_without_snapshot():
    with TemporaryDirectory() as tmp:
        response = handle_request(
            method="GET",
            path="/healthz",
            headers={},
            body="",
            db_path=Path(tmp) / "worldcup.db",
            secret="test-hmac-secret",
        )

        assert response["status"] == 200
        assert json.loads(response["body"]) == {
            "schema_version": 1,
            "service": "worldcup-analysis",
            "status": "ok",
        }


def test_http_get_preview_returns_html():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)

        response = handle_request(
            method="GET",
            path="/preview",
            headers={},
            body="",
            db_path=db_path,
            secret="test-hmac-secret",
        )

        assert response["status"] == 200
        assert response["headers"]["Content-Type"] == "text/html; charset=utf-8"
        assert "研究分析工具，不构成投注建议" in response["body"]
        assert "Mexico vs South Africa" in response["body"]


def test_http_post_ingest_snapshot_stores_signed_request():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp="2026-06-08T00:02:00+00:00",
        )

        response = handle_request(
            method=request["method"],
            path=request["path"],
            headers=request["headers"],
            body=request["body"],
            db_path=db_path,
            secret="test-hmac-secret",
            now="2026-06-08T00:03:00+00:00",
        )

        body = json.loads(response["body"])
        assert response["status"] == 200
        assert body["status"] == "stored"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1


def test_http_unknown_route_returns_404():
    with TemporaryDirectory() as tmp:
        response = handle_request(
            method="GET",
            path="/missing",
            headers={},
            body="",
            db_path=Path(tmp) / "worldcup.db",
            secret="test-hmac-secret",
        )

        assert response["status"] == 404
        assert json.loads(response["body"])["error"] == "not_found"
