from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.query import (
    load_latest_snapshot_view,
    load_latest_snapshot,
    load_recent_snapshots,
    load_recent_snapshot_views,
    project_finished_rows,
    project_match_rows,
    project_single_match_competitions,
    project_league_statistics,
)
from worldcup.store import SQLiteSnapshotStore


FORMAL_LEAGUE_IDS = {
    "serie_a_2026_27",
    "serie_a_brazil_2026",
    "laliga_2026_27",
    "epl_2026_27",
    "bundesliga_2026_27",
    "ligue_1_2026_27",
}


def test_single_match_competitions_include_fixed_six_without_fake_rows():
    snapshot = {"matches": [], "finished": {"matches": []}}
    projection = project_single_match_competitions(snapshot)
    by_id = {row["competition_id"]: row for row in projection}

    assert FORMAL_LEAGUE_IDS <= set(by_id)
    assert all(
        by_id[competition_id]["status"] == "disabled_until_live_acceptance"
        for competition_id in FORMAL_LEAGUE_IDS
    )
    assert project_match_rows(snapshot) == []


def test_single_match_competitions_project_evidence_bound_runtime_status_without_fake_rows():
    projection = project_single_match_competitions({
        "matches": [],
        "league_acceptance": {
            "schema_version": 1,
            "competitions": {
                "epl_2026_27": {"competition_id": "epl_2026_27", "state": "probing"},
                "laliga_2026_27": {"competition_id": "laliga_2026_27", "state": "active"},
            },
        },
    })
    by_id = {row["competition_id"]: row for row in projection}

    assert by_id["epl_2026_27"]["status"] == "probing"
    assert by_id["laliga_2026_27"]["status"] == "active"
    assert by_id["serie_a_2026_27"]["status"] == "disabled_until_live_acceptance"


def test_league_statistics_projection_exposes_only_safe_scope():
    snapshot = {"league_statistics": {
        "statistics_scope": "observed_schema_v2_match_pick_only",
        "competitions": {"epl_2026_27": {
            "decision_tally": {"hit": 2, "miss": 1, "push": 0, "no_pick": 0},
            "decision_sample": {"decided": 3, "hit_rate": 2 / 3, "sample_too_small": True},
            "decision_coverage": {"missing_closing_count": 1},
            "raw_provider": "must-not-leak",
        }},
        "aggregate": {
            "decision_tally": {"hit": 2, "miss": 1, "push": 0, "no_pick": 0},
            "decision_sample": {"decided": 3, "hit_rate": 2 / 3, "sample_too_small": True},
            "decision_coverage": {"missing_closing_count": 1},
        },
    }}
    projected = project_league_statistics(snapshot)
    assert projected["competitions"]["epl_2026_27"]["decision_tally"]["hit"] == 2
    assert "raw_provider" not in str(projected)


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

    def list_recent_snapshots(self, limit=2):
        return [self.latest] if self.latest else []


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "20260608T000000Z-live"},
        "counts": {"matches": 2},
        "data_quality": {
            "stale_sources": ["theoddsapi"],
            "source_errors": [{"source": "theoddsapi", "error": "TimeoutError"}],
            "missing_odds": [],
            "missing_elo": [],
            "time_mismatches": [],
        },
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "refresh_plan": {
                    "next_update_at": "2026-06-11T17:30:00+00:00",
                    "label": "T-1小时30分",
                    "description": "阵容/伤停预热",
                },
                "signals": [
                    {"market_type": "1X2_90min", "selection": "Mexico", "grade": "A", "ev": 0.06},
                    {"market_type": "OverUnder_90min", "selection": "Over", "grade": "B", "ev": 0.03},
                ],
            },
            {
                "kickoff_at_utc": "2026-06-12T01:00:00+00:00",
                "stage": "Matchday 1",
                "home_team": "Canada",
                "away_team": "Qatar",
                "signals": [],
            },
        ],
    }


def _snapshot_with_finished():
    snapshot = _snapshot()
    snapshot["run"] = {
        "run_id": "private-run-id",
        "quota": {"private-provider": {"remaining": 777}},
    }
    snapshot["data_quality"]["source_errors"] = [
        {"source": "private-provider", "error": "TimeoutError: raw upstream detail"}
    ]
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
                        "prediction": {
                            "status": "hit",
                            "label": "命中",
                            "detail": "赛果：墨西哥 2-0 南非；方向：主胜",
                        },
                    },
                    {
                        "market_type": "AsianHandicap_90min",
                        "selection": "home_-1",
                        "line": -1.0,
                        "grade": "A",
                        "odds": 1.74,
                        "prediction": {
                            "status": "push",
                            "label": "走水",
                            "detail": "赛果：墨西哥 2-0 南非；方向：主队 -1",
                        },
                    },
                ],
                "odds_trend": {"1x2": {"home": [["2026-06-11T18:45:00+00:00", 1.78]]}},
            }
        ],
        "tally": {
            "S": {"hit": 1, "miss": 0, "push": 0},
            "A": {"hit": 0, "miss": 0, "push": 1},
        },
        "skipped_no_closing": 1,
    }
    return snapshot


def _competition_snapshot(
    competition_id: str,
    competition_label: str,
    home_team: str,
    away_team: str,
    run_id: str,
) -> dict:
    snapshot = _snapshot()
    snapshot["snapshot_at"] = f"2026-06-08T0{len(run_id)}:00:00+00:00"
    snapshot["run"] = {"run_id": run_id}
    snapshot["counts"] = {"matches": 1}
    snapshot["competition"] = {"id": competition_id, "name": competition_label}
    snapshot["matches"] = [
        {
            "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
            "stage": "Matchday 1",
            "group": "",
            "home_team": home_team,
            "away_team": away_team,
            "competition": {"id": competition_id, "name": competition_label},
            "signals": [],
        }
    ]
    return snapshot


def test_load_latest_snapshot_reads_latest_from_sqlite_store():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload={
                "run_id": "run-1",
                "snapshot_id": "snapshot-1",
                "snapshot_at": "2026-06-08T00:00:00+00:00",
                "snapshot": {"counts": {"matches": 1}},
            },
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-2:snapshot-2",
            payload={
                "run_id": "run-2",
                "snapshot_id": "snapshot-2",
                "snapshot_at": "2026-06-08T01:00:00+00:00",
                "snapshot": _snapshot(),
            },
            stored_at="2026-06-08T01:02:00+00:00",
        )

        snapshot = load_latest_snapshot(db_path)

        assert snapshot["counts"]["matches"] == 2
        assert snapshot["run"]["run_id"] == "20260608T000000Z-live"


def test_load_latest_snapshot_view_merges_latest_snapshot_per_competition():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="wc-old:wc-old-snapshot",
            payload={
                "run_id": "wc-old",
                "snapshot_id": "wc-old-snapshot",
                "snapshot_at": "2026-06-08T00:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "fifa_world_cup_2026",
                    "2026 世界杯",
                    "Mexico",
                    "South Africa",
                    "wc-old",
                ),
            },
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="csl-new:csl-new-snapshot",
            payload={
                "run_id": "csl-new",
                "snapshot_id": "csl-new-snapshot",
                "snapshot_at": "2026-06-08T01:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "csl_2026",
                    "中超 2026",
                    "Shanghai Port",
                    "Beijing Guoan",
                    "csl-new",
                ),
            },
            stored_at="2026-06-08T01:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="wc-new:wc-new-snapshot",
            payload={
                "run_id": "wc-new",
                "snapshot_id": "wc-new-snapshot",
                "snapshot_at": "2026-06-08T02:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "fifa_world_cup_2026",
                    "2026 世界杯",
                    "Canada",
                    "Qatar",
                    "wc-new",
                ),
            },
            stored_at="2026-06-08T02:02:00+00:00",
        )

        snapshot = load_latest_snapshot_view(db_path)

        assert snapshot["counts"]["matches"] == 2
        assert snapshot["competition"]["id"] == "multi_competition"
        assert [match["home_team"] for match in snapshot["matches"]] == ["Canada", "Shanghai Port"]
        assert [match["competition"]["id"] for match in snapshot["matches"]] == [
            "fifa_world_cup_2026",
            "csl_2026",
        ]
        assert project_match_rows(snapshot)[1]["competition_id"] == "csl_2026"


def test_load_latest_snapshot_view_keeps_competition_after_many_newer_snapshots():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="csl-live:csl-live-snapshot",
            payload={
                "run_id": "csl-live",
                "snapshot_id": "csl-live-snapshot",
                "snapshot_at": "2026-06-08T00:00:00+00:00",
                "snapshot": _competition_snapshot(
                    "csl_2026",
                    "中超 2026",
                    "Shanghai Port",
                    "Beijing Guoan",
                    "csl-live",
                ),
            },
            stored_at="2026-06-08T00:01:00+00:00",
        )
        for index in range(75):
            run_id = f"wc-live-{index}"
            store.put_snapshot(
                idempotency_key=f"{run_id}:{run_id}-snapshot",
                payload={
                    "run_id": run_id,
                    "snapshot_id": f"{run_id}-snapshot",
                    "snapshot_at": f"2026-06-08T01:{index:02d}:00+00:00",
                    "snapshot": _competition_snapshot(
                        "fifa_world_cup_2026",
                        "2026 世界杯",
                        f"World Cup Home {index}",
                        f"World Cup Away {index}",
                        run_id,
                    ),
                },
                stored_at=f"2026-06-08T01:{index:02d}:00+00:00",
            )

        snapshot = load_latest_snapshot_view(db_path)

        assert snapshot["competition"]["id"] == "multi_competition"
        assert [match["competition"]["id"] for match in snapshot["matches"]] == [
            "fifa_world_cup_2026",
            "csl_2026",
        ]
        assert [match["home_team"] for match in snapshot["matches"]] == [
            "World Cup Home 74",
            "Shanghai Port",
        ]


def test_load_recent_snapshot_views_compares_each_competition_with_own_previous_snapshot():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        for run_id, competition_id, label, home, away, stored_at in [
            ("wc-old", "fifa_world_cup_2026", "2026 世界杯", "Mexico", "South Africa", "2026-06-08T00:02:00+00:00"),
            ("csl-old", "csl_2026", "中超 2026", "Shanghai Shenhua", "Shandong Taishan", "2026-06-08T00:03:00+00:00"),
            ("csl-new", "csl_2026", "中超 2026", "Shanghai Port", "Beijing Guoan", "2026-06-08T01:02:00+00:00"),
            ("wc-new", "fifa_world_cup_2026", "2026 世界杯", "Canada", "Qatar", "2026-06-08T02:02:00+00:00"),
        ]:
            store.put_snapshot(
                idempotency_key=f"{run_id}:{run_id}-snapshot",
                payload={
                    "run_id": run_id,
                    "snapshot_id": f"{run_id}-snapshot",
                    "snapshot_at": stored_at.replace("02:00", "00:00"),
                    "snapshot": _competition_snapshot(competition_id, label, home, away, run_id),
                },
                stored_at=stored_at,
            )

        current, previous = load_recent_snapshot_views(db_path, limit=2)

        assert [match["home_team"] for match in current["matches"]] == ["Canada", "Shanghai Port"]
        assert [match["home_team"] for match in previous["matches"]] == ["Mexico", "Shanghai Shenhua"]


def test_load_latest_snapshot_reads_from_injected_store():
    store = MemorySnapshotStore(latest={"snapshot": _snapshot()})

    snapshot = load_latest_snapshot(store=store)

    assert snapshot["counts"]["matches"] == 2
    assert snapshot["run"]["run_id"] == "20260608T000000Z-live"


def test_load_recent_snapshots_reads_recent_from_sqlite_store():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        store = SQLiteSnapshotStore(db_path)
        store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload={
                "run_id": "run-1",
                "snapshot_id": "snapshot-1",
                "snapshot_at": "2026-06-08T00:00:00+00:00",
                "snapshot": {"run": {"run_id": "run-1"}, "counts": {"matches": 1}},
            },
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-2:snapshot-2",
            payload={
                "run_id": "run-2",
                "snapshot_id": "snapshot-2",
                "snapshot_at": "2026-06-08T01:00:00+00:00",
                "snapshot": {"run": {"run_id": "run-2"}, "counts": {"matches": 2}},
            },
            stored_at="2026-06-08T01:02:00+00:00",
        )

        snapshots = load_recent_snapshots(db_path, limit=2)

        assert [snapshot["run"]["run_id"] for snapshot in snapshots] == ["run-2", "run-1"]


def test_load_recent_snapshots_falls_back_to_latest_for_minimal_store():
    class LatestOnlyStore:
        def latest_snapshot(self):
            return {"snapshot": _snapshot()}

    snapshots = load_recent_snapshots(store=LatestOnlyStore())

    assert len(snapshots) == 1
    assert snapshots[0]["counts"]["matches"] == 2


def test_project_match_rows_returns_preview_safe_rows():
    snapshot = _snapshot()
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "policy_version": "match_pick_v2",
        "label": "MATCH_PICK",
        "market": "DNB",
        "selection": "home",
        "line": 0.0,
        "p_hit_safe": 0.59,
        "p_no_loss_safe": 0.73,
        "computed_at": "2026-06-08T00:10:00+00:00",
        "odds_latest_at": "2026-06-08T00:09:00+00:00",
        "valid_until": "2099-06-11T19:00:00+00:00",
    }

    rows = project_match_rows(snapshot)

    assert len(rows) == 2
    assert rows[0]["match_label"] == "Mexico vs South Africa"
    assert rows[0]["match_decision"]["label"] == "MATCH_PICK"
    assert "top_grade" not in rows[0]
    assert "signal_count" not in rows[0]
    assert rows[0]["next_update_at"] == "2026-06-11T17:30:00+00:00"
    assert rows[0]["next_update_label"] == "T-1小时30分"
    assert rows[0]["next_update_description"] == "阵容/伤停预热"
    assert rows[0]["last_update_at"] == "2026-06-08T00:09:00+00:00"
    assert rows[0]["last_update_label"] == "赔率更新"
    assert rows[1]["last_update_at"] == "2026-06-08T00:00:00+00:00"
    assert rows[1]["last_update_label"] == "分析更新"
    assert rows[0]["stale"] is True
    assert rows[0]["competition_id"] == "fifa_world_cup_2026"
    assert rows[0]["competition_label"] == "2026 世界杯"
    assert rows[1]["match_decision"] == {
        "schema_version": 2,
        "policy_version": "match_pick_v2",
        "label": "NO_CLEAN_MARKET",
    }
    assert "stake" not in rows[0]
    assert "bet_amount" not in rows[0]


def test_project_match_rows_hides_postponed_match_without_mutating_snapshot():
    snapshot = _snapshot()
    snapshot["matches"][0]["fixture_status"] = "POSTPONED"
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "valid_until": "2099-06-11T19:00:00+00:00",
    }

    rows = project_match_rows(snapshot)

    assert [row["match_label"] for row in rows] == ["Canada vs Qatar"]
    assert len(snapshot["matches"]) == 2
    assert snapshot["matches"][0]["fixture_status"] == "POSTPONED"
    assert snapshot["matches"][0]["match_decision"]["label"] == "MATCH_PICK"


def test_project_match_rows_hides_confirmed_finished_match_but_not_started_unfinished_match():
    finished_snapshot = _snapshot_with_finished()

    finished_rows = project_match_rows(finished_snapshot)
    unfinished_rows = project_match_rows(_snapshot())

    assert [row["match_label"] for row in finished_rows] == ["Canada vs Qatar"]
    assert [row["match_label"] for row in unfinished_rows] == [
        "Mexico vs South Africa",
        "Canada vs Qatar",
    ]


def test_public_projection_uses_top_level_competition_when_match_blocks_are_absent():
    snapshot = _competition_snapshot(
        "csl_2026",
        "中超 2026",
        "Shanghai Port",
        "Beijing Guoan",
        "csl-top-level-only",
    )
    match = snapshot["matches"][0]
    match.pop("competition")
    snapshot["finished"] = {
        "schema_version": 2,
        "matches": [
            {
                "kickoff_at_utc": match["kickoff_at_utc"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "result": {"home_score": 1, "away_score": 0},
                "closing_match_decision": {
                    "schema_version": 2,
                    "label": "MATCH_PICK",
                    "market": "1X2",
                    "selection": "home",
                },
            }
        ],
        "skipped_no_closing": 0,
    }

    assert project_match_rows(snapshot) == []
    finished = project_finished_rows(snapshot)
    assert finished["matches"][0]["competition_id"] == "csl_2026"
    assert finished["matches"][0]["competition_label"] == "中超 2026"


def test_live_projection_never_repackages_legacy_or_expired_pick_as_current_pick():
    snapshot = _snapshot()
    snapshot["data_quality"]["stale_sources"] = []
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 1,
        "label": "LOW_CONFIDENCE_LEAN",
        "market": "1X2",
        "selection": "home",
    }
    snapshot["matches"][1]["match_decision"] = {
        "schema_version": 2,
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "valid_until": "2000-01-01T00:00:00+00:00",
    }

    rows = project_match_rows(snapshot)

    assert {row["match_decision"]["label"] for row in rows} == {"NO_CLEAN_MARKET"}


def test_expired_v3_pick_keeps_v3_policy_version_when_projected_as_no_pick():
    snapshot = _snapshot()
    snapshot["data_quality"]["stale_sources"] = []
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "valid_until": "2000-01-01T00:00:00+00:00",
    }

    row = project_match_rows(snapshot)[0]

    assert row["match_decision"] == {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "NO_CLEAN_MARKET",
    }


def test_pending_club_rating_forces_public_no_pick_even_for_malformed_legacy_snapshot():
    snapshot = _snapshot()
    snapshot["data_quality"] = {"warnings": ["club_rating_pending"]}
    snapshot["matches"][0]["competition"] = {
        "id": "csl_2026",
        "name": "中超 2026",
        "rating_policy": "club_rating_pending",
    }
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "valid_until": "2099-01-01T00:00:00+00:00",
    }

    row = project_match_rows(snapshot)[0]

    assert row["match_decision"]["label"] == "NO_CLEAN_MARKET"


def test_pending_club_rating_allows_explicit_v3_market_fallback_pick():
    snapshot = _snapshot()
    snapshot["data_quality"] = {"warnings": ["club_rating_pending"]}
    snapshot["matches"][0]["competition"] = {
        "id": "csl_2026",
        "name": "中超 2026",
        "rating_policy": "club_rating_pending",
    }
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "p_hit_safe": 0.48,
        "p_no_loss_safe": 0.48,
        "valid_until": "2099-01-01T00:00:00+00:00",
    }

    row = project_match_rows(snapshot)[0]

    assert row["match_decision"]["label"] == "MATCH_PICK"
    assert row["match_decision"]["policy_version"] == "match_pick_v3"


def test_project_match_rows_ignores_probability_families_for_public_summary():
    snapshot = {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "refresh_plan": {"next_update_at": "2026-06-09T00:00:00+00:00", "label": "常规"},
                "model": {
                    "probability_families": {
                        "schema_version": 1,
                        "families": {
                            "model_raw": {"combined_1x2": {"home": 0.5}},
                            "model_market_total": {"combined_1x2": {"home": 0.51}},
                            "market_only": {"1x2": {"home": 0.49}},
                        },
                    }
                },
                "signals": [{"grade": "A"}],
            }
        ],
    }

    rows = project_match_rows(snapshot)

    assert rows == [
        {
            "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
            "stage": "Matchday 1",
            "group": "Group A",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "match_label": "Mexico vs South Africa",
            "competition_id": "fifa_world_cup_2026",
            "competition_label": "2026 世界杯",
            "fixture_status": "SCHEDULED",
            "last_update_at": "2026-06-08T00:00:00+00:00",
            "last_update_label": "分析更新",
            "next_update_at": "2026-06-09T00:00:00+00:00",
            "next_update_label": "常规",
            "next_update_description": None,
            "stale": False,
            "match_decision": {
                "schema_version": 2,
                "policy_version": "match_pick_v2",
                "label": "NO_CLEAN_MARKET",
            },
        }
    ]


def test_project_finished_rows_returns_public_safe_review_projection():
    snapshot = _snapshot_with_finished()
    snapshot["finished"]["matches"][0]["closing_match_decision"] = {
        "schema_version": 1,
        "label": "HIGH_CONFIDENCE_LEAN",
        "market": "DNB",
        "selection": "home",
        "line": 0.0,
        "p_hit_safe": 0.59,
        "p_no_loss_safe": 0.73,
    }

    finished = project_finished_rows(snapshot)

    assert finished["schema_version"] == 2
    assert finished["summary"]["match_count"] == 1
    assert finished["summary"]["skipped_no_closing"] == 1
    assert finished["summary"]["coverage"]["missing_closing_count"] == 1
    assert finished["summary"]["sample"]["sample_too_small"] is True
    assert finished["summary"]["decision_tally"] == {
        "hit": 0,
        "miss": 0,
        "push": 0,
        "no_pick": 0,
    }
    assert finished["summary"]["coverage"]["legacy_decision_count"] == 1
    assert finished["matches"][0]["match_label"] == "Mexico vs South Africa"
    assert finished["matches"][0]["score_label"] == "2 - 0"
    assert finished["matches"][0]["closing_match_decision"]["label"] == "MATCH_PICK"
    assert (
        finished["matches"][0]["closing_match_decision"]["policy_version"]
        == "legacy_match_decision_v1"
    )
    assert finished["matches"][0]["decision_outcome"]["status"] == "hit"
    assert "signals" not in finished["matches"][0]
    assert "grade" not in str(finished).lower()

    serialized = str(finished)
    assert "run_id" not in serialized
    assert "private-run-id" not in serialized
    assert "quota" not in serialized
    assert "private-provider" not in serialized
    assert "raw upstream detail" not in serialized
    assert "stake" not in serialized.lower()
    assert "bet_amount" not in serialized.lower()
