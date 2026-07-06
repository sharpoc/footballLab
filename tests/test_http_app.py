import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.http_app import SnapshotViewCache, handle_request
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


class CountingRecentSnapshotStore(MemorySnapshotStore):
    def __init__(self, records=None):
        super().__init__(latest=records[0] if records else None)
        self.records = list(records or [])
        self.list_recent_calls = 0

    def put_snapshot(self, idempotency_key, payload, stored_at=None):
        result = super().put_snapshot(idempotency_key, payload, stored_at)
        self.records.insert(0, self.latest)
        return result

    def count_snapshots(self):
        return len(self.records)

    def latest_snapshot(self):
        return self.records[0] if self.records else None

    def list_recent_snapshots(self, limit=2):
        self.list_recent_calls += 1
        return self.records[:limit]


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


def _competition_snapshot(competition_id, competition_label, home_team, away_team, run_id):
    snapshot = _snapshot(run_id)
    snapshot["competition"] = {"id": competition_id, "name": competition_label}
    snapshot["counts"] = {"matches": 1}
    snapshot["matches"][0]["home_team"] = home_team
    snapshot["matches"][0]["away_team"] = away_team
    snapshot["matches"][0]["competition"] = {"id": competition_id, "name": competition_label}
    return snapshot


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


def test_http_get_matches_returns_latest_rows_for_all_competitions():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="csl-live:csl-live-snapshot",
            payload={
                "run_id": "csl-live",
                "snapshot_id": "csl-live-snapshot",
                "snapshot_at": "2026-06-08T01:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "csl_2026",
                    "中超 2026",
                    "Shanghai Port",
                    "Beijing Guoan",
                    "csl-live",
                ),
            },
            stored_at="2026-06-08T01:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="wc-live:wc-live-snapshot",
            payload={
                "run_id": "wc-live",
                "snapshot_id": "wc-live-snapshot",
                "snapshot_at": "2026-06-08T02:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "fifa_world_cup_2026",
                    "2026 世界杯",
                    "Canada",
                    "Qatar",
                    "wc-live",
                ),
            },
            stored_at="2026-06-08T02:02:00+00:00",
        )

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
        assert [match["competition_id"] for match in body["matches"]] == [
            "fifa_world_cup_2026",
            "csl_2026",
        ]
        assert body["matches"][1]["match_label"] == "Shanghai Port vs Beijing Guoan"


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


def test_http_get_matches_reuses_cached_latest_view():
    store = CountingRecentSnapshotStore(records=[{"snapshot": _snapshot("run-cache")}])
    cache = SnapshotViewCache()

    first = handle_request(
        method="GET",
        path="/api/matches",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
        view_cache=cache,
    )
    second = handle_request(
        method="GET",
        path="/api/matches",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
        view_cache=cache,
    )

    assert first["status"] == 200
    assert second["status"] == 200
    assert store.list_recent_calls == 1


def test_http_post_ingest_snapshot_clears_latest_view_cache():
    old_snapshot = _snapshot("run-old")
    new_snapshot = _snapshot("run-new")
    new_snapshot["matches"][0]["home_team"] = "Canada"
    new_snapshot["matches"][0]["away_team"] = "Qatar"
    store = CountingRecentSnapshotStore(records=[{"snapshot": old_snapshot}])
    cache = SnapshotViewCache()

    cached = handle_request(
        method="GET",
        path="/api/matches",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
        view_cache=cache,
    )
    request = build_ingest_request(
        snapshot=new_snapshot,
        endpoint="https://example.com/api/ingest/snapshot",
        secret="test-hmac-secret",
        timestamp="2026-06-08T00:02:00+00:00",
    )
    ingest = handle_request(
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
        view_cache=cache,
    )
    refreshed = handle_request(
        method="GET",
        path="/api/matches",
        headers={},
        body="",
        db_path="unused.db",
        secret="test-hmac-secret",
        store=store,
        view_cache=cache,
    )

    assert json.loads(cached["body"])["matches"][0]["match_label"] == "Mexico vs South Africa"
    assert ingest["status"] == 200
    assert json.loads(refreshed["body"])["matches"][0]["match_label"] == "Canada vs Qatar"
    assert store.list_recent_calls == 2


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


def test_http_get_preview_renders_latest_rows_for_all_competitions():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="csl-live:csl-live-snapshot",
            payload={
                "run_id": "csl-live",
                "snapshot_id": "csl-live-snapshot",
                "snapshot_at": "2026-06-08T01:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "csl_2026",
                    "中超 2026",
                    "Shanghai Port",
                    "Beijing Guoan",
                    "csl-live",
                ),
            },
            stored_at="2026-06-08T01:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="wc-live:wc-live-snapshot",
            payload={
                "run_id": "wc-live",
                "snapshot_id": "wc-live-snapshot",
                "snapshot_at": "2026-06-08T02:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "fifa_world_cup_2026",
                    "2026 世界杯",
                    "Canada",
                    "Qatar",
                    "wc-live",
                ),
            },
            stored_at="2026-06-08T02:02:00+00:00",
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
        assert '<option value="csl_2026">中超 2026</option>' in response["body"]
        assert "Shanghai Port 对 Beijing Guoan" in response["body"]


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


def test_http_post_ingest_snapshot_echoes_request_id_on_success():
    store = MemorySnapshotStore()
    request = build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret="test-hmac-secret",
        timestamp="2026-06-08T00:02:00+00:00",
    )
    headers = dict(request["headers"])
    headers["X-Request-Id"] = "req-csl-001"

    response = handle_request(
        method=request["method"],
        path=request["path"],
        headers=headers,
        body=request["body"],
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 200
    assert response["headers"]["X-Request-Id"] == "req-csl-001"
    assert body["request_id"] == "req-csl-001"
    assert body["status"] == "stored"
    assert store.count_snapshots() == 1


def test_http_post_ingest_snapshot_requires_json_content_type_without_writing():
    store = MemorySnapshotStore()
    request = build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret="test-hmac-secret",
        timestamp="2026-06-08T00:02:00+00:00",
    )
    headers = dict(request["headers"])
    headers["Content-Type"] = "text/plain"
    headers["X-Request-Id"] = "req-bad-type"

    response = handle_request(
        method=request["method"],
        path=request["path"],
        headers=headers,
        body=request["body"],
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 415
    assert response["headers"]["X-Request-Id"] == "req-bad-type"
    assert body == {
        "error": {
            "code": "unsupported_media_type",
            "request_id": "req-bad-type",
        }
    }
    assert store.count_snapshots() == 0


def test_http_post_ingest_snapshot_rejects_large_body_without_writing():
    store = MemorySnapshotStore()
    request = build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret="test-hmac-secret",
        timestamp="2026-06-08T00:02:00+00:00",
    )
    headers = dict(request["headers"])
    headers["X-Request-Id"] = "req-too-large"

    response = handle_request(
        method=request["method"],
        path=request["path"],
        headers=headers,
        body=request["body"],
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
        max_ingest_body_bytes=10,
    )

    body = json.loads(response["body"])
    assert response["status"] == 413
    assert response["headers"]["X-Request-Id"] == "req-too-large"
    assert body["error"]["code"] == "body_too_large"
    assert body["error"]["request_id"] == "req-too-large"
    assert store.count_snapshots() == 0


def test_http_post_ingest_snapshot_default_limit_allows_signed_multi_mb_snapshot():
    store = MemorySnapshotStore()
    snapshot = _snapshot()
    snapshot["matches"][0]["large_finished_payload"] = "x" * 1_200_000
    request = build_ingest_request(
        snapshot=snapshot,
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

    assert response["status"] == 200
    assert store.count_snapshots() == 1


def test_http_post_ingest_snapshot_returns_structured_error_for_bad_signature():
    store = MemorySnapshotStore()
    request = build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret="test-hmac-secret",
        timestamp="2026-06-08T00:02:00+00:00",
    )
    headers = dict(request["headers"])
    headers["X-Worldcup-Signature"] = "sha256=bad"
    headers["X-Request-Id"] = "req-bad-signature"

    response = handle_request(
        method=request["method"],
        path=request["path"],
        headers=headers,
        body=request["body"],
        db_path="unused.db",
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
        store=store,
    )

    body = json.loads(response["body"])
    assert response["status"] == 401
    assert response["headers"]["X-Request-Id"] == "req-bad-signature"
    assert body == {
        "error": {
            "code": "signature_mismatch",
            "request_id": "req-bad-signature",
        }
    }
    assert store.count_snapshots() == 0


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
