import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.store import SQLiteSnapshotStore


def _payload(run_id="20260608T000000Z-live", snapshot_id="snapshot-1"):
    return {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "generated_at": "2026-06-08T00:01:00+00:00",
        "snapshot": {
            "snapshot_at": "2026-06-08T00:00:00+00:00",
            "run": {"run_id": run_id},
            "counts": {"matches": 1},
            "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
        },
    }


def test_sqlite_snapshot_store_put_is_idempotent():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")
        store.initialize()

        first = store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload=_payload(),
            stored_at="2026-06-08T00:02:00+00:00",
        )
        second = store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload=_payload(),
            stored_at="2026-06-08T00:03:00+00:00",
        )

        assert first["status"] == "stored"
        assert second["status"] == "duplicate"
        assert store.count_snapshots() == 1


def test_sqlite_snapshot_store_latest_snapshot_returns_latest_by_stored_at():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")
        store.initialize()
        store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload=_payload(run_id="run-1", snapshot_id="snapshot-1"),
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-2:snapshot-2",
            payload=_payload(run_id="run-2", snapshot_id="snapshot-2"),
            stored_at="2026-06-08T00:03:00+00:00",
        )

        latest = store.latest_snapshot()

        assert latest["run_id"] == "run-2"
        assert latest["snapshot_id"] == "snapshot-2"
        assert latest["idempotency_key"] == "run-2:snapshot-2"
        assert latest["snapshot"]["counts"]["matches"] == 1
        assert json.loads(latest["payload_json"])["run_id"] == "run-2"


def test_sqlite_snapshot_store_list_recent_snapshots_returns_newest_first():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")
        store.initialize()
        store.put_snapshot(
            idempotency_key="run-1:snapshot-1",
            payload=_payload(run_id="run-1", snapshot_id="snapshot-1"),
            stored_at="2026-06-08T00:02:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-2:snapshot-2",
            payload=_payload(run_id="run-2", snapshot_id="snapshot-2"),
            stored_at="2026-06-08T00:03:00+00:00",
        )
        store.put_snapshot(
            idempotency_key="run-3:snapshot-3",
            payload=_payload(run_id="run-3", snapshot_id="snapshot-3"),
            stored_at="2026-06-08T00:04:00+00:00",
        )

        recent = store.list_recent_snapshots(limit=2)

        assert [item["run_id"] for item in recent] == ["run-3", "run-2"]
        assert [item["snapshot_id"] for item in recent] == ["snapshot-3", "snapshot-2"]
        assert recent[0]["snapshot"]["run"]["run_id"] == "run-3"
