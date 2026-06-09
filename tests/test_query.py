from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.query import load_latest_snapshot, project_match_rows
from worldcup.store import SQLiteSnapshotStore


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


def test_project_match_rows_returns_preview_safe_rows():
    rows = project_match_rows(_snapshot())

    assert len(rows) == 2
    assert rows[0]["match_label"] == "Mexico vs South Africa"
    assert rows[0]["top_grade"] == "A"
    assert rows[0]["signal_count"] == 2
    assert rows[0]["stale"] is True
    assert rows[1]["top_grade"] == ""
    assert rows[1]["signal_count"] == 0
    assert "stake" not in rows[0]
    assert "bet_amount" not in rows[0]
