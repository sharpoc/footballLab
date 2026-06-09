import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.asgi_app import create_asgi_app
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


def _call_asgi(app, method: str, path: str, body: bytes = b""):
    events = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        events.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    asyncio.run(app(scope, receive, send))
    status = next(event["status"] for event in events if event["type"] == "http.response.start")
    response_body = b"".join(
        event.get("body", b"") for event in events if event["type"] == "http.response.body"
    )
    return status, response_body.decode("utf-8")


def test_asgi_app_get_matches_returns_json():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_asgi_app(db_path=db_path, secret="test-secret")

        status, body = _call_asgi(app, "GET", "/api/matches")

        assert status == 200
        assert json.loads(body)["matches"][0]["match_label"] == "Mexico vs South Africa"


def test_asgi_app_healthz_returns_ok_without_snapshot():
    with TemporaryDirectory() as tmp:
        app = create_asgi_app(db_path=Path(tmp) / "worldcup.db", secret="test-secret")

        status, body = _call_asgi(app, "GET", "/healthz")

        assert status == 200
        assert json.loads(body)["status"] == "ok"


def test_asgi_app_get_preview_returns_html():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_asgi_app(db_path=db_path, secret="test-secret")

        status, body = _call_asgi(app, "GET", "/preview")

        assert status == 200
        assert "研究分析工具，不构成投注建议" in body
        assert "Mexico vs South Africa" in body
