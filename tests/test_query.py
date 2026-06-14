from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.query import (
    load_latest_snapshot,
    load_recent_snapshots,
    project_finished_rows,
    project_match_rows,
)
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
    rows = project_match_rows(_snapshot())

    assert len(rows) == 2
    assert rows[0]["match_label"] == "Mexico vs South Africa"
    assert rows[0]["top_grade"] == "A"
    assert rows[0]["signal_count"] == 2
    assert rows[0]["next_update_at"] == "2026-06-11T17:30:00+00:00"
    assert rows[0]["next_update_label"] == "T-1小时30分"
    assert rows[0]["next_update_description"] == "阵容/伤停预热"
    assert rows[0]["stale"] is True
    assert rows[1]["top_grade"] == ""
    assert rows[1]["signal_count"] == 0
    assert "stake" not in rows[0]
    assert "bet_amount" not in rows[0]


def test_project_finished_rows_returns_public_safe_review_projection():
    finished = project_finished_rows(_snapshot_with_finished())

    assert finished["schema_version"] == 1
    assert finished["summary"]["match_count"] == 1
    assert finished["summary"]["signal_count"] == 2
    assert finished["summary"]["skipped_no_closing"] == 1
    assert finished["summary"]["coverage"]["missing_closing_count"] == 1
    assert finished["summary"]["sample"]["sample_too_small"] is True
    assert finished["summary"]["tally"]["S"] == {"hit": 1, "miss": 0, "push": 0}
    assert finished["matches"][0]["match_label"] == "Mexico vs South Africa"
    assert finished["matches"][0]["score_label"] == "2 - 0"
    assert finished["matches"][0]["signals"][0]["outcome"] == "命中"
    assert finished["matches"][0]["signals"][0]["prediction_status"] == "hit"

    serialized = str(finished)
    assert "run_id" not in serialized
    assert "private-run-id" not in serialized
    assert "quota" not in serialized
    assert "private-provider" not in serialized
    assert "raw upstream detail" not in serialized
    assert "stake" not in serialized.lower()
    assert "bet_amount" not in serialized.lower()
