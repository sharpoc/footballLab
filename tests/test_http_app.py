import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.http_app import handle_request
from worldcup.ingest import build_ingest_request
from worldcup.store import SQLiteSnapshotStore


class MemorySnapshotStore:
    def __init__(self, latest=None):
        self.latest = latest
        self.puts = []

    def initialize(self):
        pass

    def put_snapshot(self, idempotency_key, payload, stored_at=None):
        self.puts.append((idempotency_key, payload, stored_at))
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
        return len(self.puts)

    def latest_snapshot(self):
        return self.latest


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
                "market": {"1x2": {"odds": {"home": 2.0}, "market_probs": {"home": 0.57}}},
                "model": {"combined_1x2": {"home": 0.61}},
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "A",
                        "ev": 0.052,
                        "edge": 0.041,
                    }
                ],
            }
        ],
    }


def _snapshot_with_finished(run_id="20260608T000000Z-live"):
    snapshot = _snapshot(run_id)
    snapshot["run"] = {
        "run_id": run_id,
        "quota": {"private-provider": {"remaining": 777}},
    }
    snapshot["finished"] = {
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "stage": "Matchday 1",
                "group": "Group A",
                "result": {"home_score": 2, "away_score": 0},
                "closing_snapshot_at": "2026-06-11T18:45:00+00:00",
                "closing_signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "line": None,
                        "grade": "S",
                        "odds": 1.78,
                        "prediction": {"status": "hit", "label": "命中", "detail": "全场 2-0"},
                    }
                ],
            }
        ],
        "tally": {"S": {"hit": 1, "miss": 0, "push": 0}},
        "skipped_no_closing": 0,
    }
    return snapshot


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


def test_http_get_matches_uses_injected_store():
    store = MemorySnapshotStore(
        latest={
            "snapshot": _snapshot("run-memory"),
        }
    )

    response = handle_request(
        method="GET",
        path="/api/matches",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 200
    assert body["matches"][0]["match_label"] == "Mexico vs South Africa"


def test_http_get_finished_returns_safe_projection():
    store = MemorySnapshotStore(latest={"snapshot": _snapshot_with_finished("run-memory")})

    response = handle_request(
        method="GET",
        path="/api/finished",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 200
    assert body["finished"]["summary"]["match_count"] == 1
    assert body["finished"]["matches"][0]["score_label"] == "2 - 0"
    serialized = response["body"]
    assert "run-memory" not in serialized
    assert "quota" not in serialized
    assert "private-provider" not in serialized
    assert "stake" not in serialized.lower()


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
        assert "仅用于研究分析，不构成投注建议" in response["body"]
        assert "墨西哥 对 南非" in response["body"]


def test_http_get_preview_compares_latest_two_snapshots():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        previous = _snapshot("run-1")
        current = deepcopy(previous)
        current["run"]["run_id"] = "run-2"
        current["matches"][0]["market"]["1x2"]["odds"]["home"] = 1.85
        current["matches"][0]["signals"][0]["grade"] = "S"
        current["matches"][0]["signals"][0]["ev"] = 0.092
        store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload={
                "run_id": "run-1",
                "snapshot_id": "snapshot-1",
                "snapshot_at": "2026-06-08T00:00:00+00:00",
                "snapshot": previous,
            },
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-2:snapshot-2",
            payload={
                "run_id": "run-2",
                "snapshot_id": "snapshot-2",
                "snapshot_at": "2026-06-08T12:00:00+00:00",
                "snapshot": current,
            },
            stored_at="2026-06-08T12:02:00+00:00",
        )

        response = handle_request(
            method="GET",
            path="/preview",
            headers={},
            body="",
            db_path=db_path,
            secret="test-hmac-secret",
        )

        assert response["status"] == 200
        assert 'class="change-summary"' not in response["body"]
        assert "本轮变化" in response["body"]
        assert "等级 A → S" in response["body"]
        assert "赔率 2.00 → 1.85" in response["body"]


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


def test_http_post_ingest_snapshot_uses_injected_store():
    store = MemorySnapshotStore()
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
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 200
    assert body["status"] == "stored"
    assert store.count_snapshots() == 1


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
