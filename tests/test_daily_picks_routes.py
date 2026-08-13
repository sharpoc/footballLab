from __future__ import annotations

import json
from tempfile import TemporaryDirectory
from pathlib import Path

from worldcup.http_app import handle_request


class MemoryStore:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def initialize(self):
        return None

    def latest_snapshot(self):
        return {"snapshot": self.snapshot}

    def list_recent_snapshots(self, limit=2):
        return [{"snapshot": self.snapshot}]

    def list_latest_snapshots_by_competition(self, competition_ids, per_competition_limit=1):
        return [{"snapshot": self.snapshot}]


def _snapshot():
    return {
        "snapshot_at": "2026-07-31T12:00:00+00:00",
        "competition": {"id": "csl_2026", "name": "中超"},
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-08-01T09:00:00+00:00",
                "competition": {"id": "csl_2026", "name": "中超"},
                "home_team": "上海海港",
                "away_team": "北京国安",
                "match_decision": {
                    "schema_version": 2,
                    "policy_version": "match_pick_v3",
                    "label": "MATCH_PICK",
                    "market": "1X2",
                    "selection": "home",
                    "odds": 1.80,
                    "p_hit_safe": 0.68,
                    "p_no_loss_safe": 0.68,
                    "valid_until": "2026-08-01T10:00:00+00:00",
                },
            }
        ],
        "finished": {"matches": []},
    }


def test_existing_single_match_routes_keep_their_previous_projection_contract():
    store = MemoryStore(_snapshot())
    with TemporaryDirectory() as tmp:
        matches = handle_request("GET", "/api/matches", {}, "", Path(tmp) / "db", "test-secret", store=store)
        finished = handle_request("GET", "/api/finished", {}, "", Path(tmp) / "db", "test-secret", store=store)
        preview = handle_request("GET", "/preview", {}, "", Path(tmp) / "db", "test-secret", store=store)
    assert matches["status"] == 200
    assert json.loads(matches["body"])["matches"][0]["match_label"] == "上海海港 vs 北京国安"
    assert finished["status"] == 200
    assert json.loads(finished["body"])["finished"]["summary"]["match_count"] == 0
    assert preview["status"] == 200
    assert "仅用于研究分析，不构成投注建议" in preview["body"]
    assert "上海海港 对 北京国安" in preview["body"]


def test_daily_picks_api_is_an_additive_route_with_safe_projection():
    store = MemoryStore(_snapshot())
    with TemporaryDirectory() as tmp:
        response = handle_request(
            "GET",
            "/api/daily-picks?date=2026-07-31",
            {},
            "",
            Path(tmp) / "db",
            "test-secret",
            store=store,
            now="2026-07-31T12:00:00+00:00",
        )
    assert response["status"] == 200
    body = json.loads(response["body"])
    assert "singles" in body
    assert "parlay_2" in body
    assert body["selected_count"] == 1
    assert body["singles"][0]["prediction_probability"] == 0.68
    assert body["singles"][0]["market_implied_probability"] is None
    serialized = response["body"]
    assert "test-secret" not in serialized
    assert "quota" not in serialized
    assert "signals" not in serialized
    assert "grade" not in serialized.lower()


def test_daily_picks_html_is_an_additive_route():
    store = MemoryStore(_snapshot())
    with TemporaryDirectory() as tmp:
        response = handle_request(
            "GET",
            "/daily-picks",
            {},
            "",
            Path(tmp) / "db",
            "test-secret",
            store=store,
            now="2026-07-31T12:00:00+00:00",
        )
    assert response["status"] == 200
    assert response["headers"]["Content-Type"] == "text/html; charset=utf-8"
    assert "每日精选" in response["body"]
    assert "组合概率研究" in response["body"]
    assert 'href="/daily-picks"' in response["body"]
    assert 'class="primary-nav-item active" aria-current="page" href="/daily-picks">每日精选</a>' in response["body"]
