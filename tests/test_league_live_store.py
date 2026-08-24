import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_live_store import LeagueLiveStore


def test_live_store_writes_partitioned_snapshot_and_history_idempotently():
    with TemporaryDirectory() as tmp:
        store = LeagueLiveStore(tmp)
        snapshot = {
            "run_id": "run-1",
            "snapshot_id": "snapshot-1",
            "generated_at": "2026-08-24T12:00:00Z",
            "competition": {"id": "epl_2026_27"},
            "matches": [],
        }
        assert store.commit_snapshot("epl_2026_27", snapshot) == "stored"
        assert store.commit_snapshot("epl_2026_27", snapshot) == "unchanged"

        current = Path(tmp) / "data/cache/leagues/epl_2026_27/snapshot.json"
        history = Path(tmp) / "data/local/leagues/epl_2026_27/history/snapshot-1.json"
        assert json.loads(current.read_text())["snapshot_id"] == "snapshot-1"
        assert json.loads(history.read_text())["snapshot_id"] == "snapshot-1"


def test_live_store_rejects_cross_partition_snapshot():
    with TemporaryDirectory() as tmp:
        store = LeagueLiveStore(tmp)
        try:
            store.commit_snapshot("epl_2026_27", {
                "snapshot_id": "snapshot-1",
                "competition": {"id": "laliga_2026_27"},
                "matches": [],
            })
        except ValueError as exc:
            assert str(exc) == "league_live_snapshot_competition_mismatch"
        else:
            raise AssertionError("cross partition snapshot must fail")
