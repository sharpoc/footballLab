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


def test_sqlite_snapshot_store_list_recent_snapshots_allows_snapshot_view_scan_limit():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")
        store.initialize()
        for index in range(25):
            store.put_snapshot(
                idempotency_key=f"run-{index}:snapshot-{index}",
                payload=_payload(run_id=f"run-{index}", snapshot_id=f"snapshot-{index}"),
                stored_at=f"2026-06-08T00:{index:02d}:00+00:00",
            )

        recent = store.list_recent_snapshots(limit=25)

        assert len(recent) == 25
        assert recent[0]["run_id"] == "run-24"
        assert recent[-1]["run_id"] == "run-0"


def test_sqlite_snapshot_store_lists_latest_snapshots_by_competition_without_recent_window():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")
        store.initialize()
        csl_payload = _payload(run_id="csl-live", snapshot_id="csl-live-snapshot")
        csl_payload["snapshot"]["competition"] = {"id": "csl_2026", "name": "中超 2026"}
        csl_payload["snapshot"]["matches"][0]["competition"] = {
            "id": "csl_2026",
            "name": "中超 2026",
        }
        store.put_snapshot(
            idempotency_key="csl-live:csl-live-snapshot",
            payload=csl_payload,
            stored_at="2026-06-08T00:01:00+00:00",
        )
        for index in range(75):
            run_id = f"wc-live-{index}"
            payload = _payload(run_id=run_id, snapshot_id=f"{run_id}-snapshot")
            payload["snapshot"]["competition"] = {
                "id": "fifa_world_cup_2026",
                "name": "2026 世界杯",
            }
            payload["snapshot"]["matches"][0]["competition"] = {
                "id": "fifa_world_cup_2026",
                "name": "2026 世界杯",
            }
            store.put_snapshot(
                idempotency_key=f"{run_id}:{run_id}-snapshot",
                payload=payload,
                stored_at=f"2026-06-08T01:{index:02d}:00+00:00",
            )

        records = store.list_latest_snapshots_by_competition(
            ["fifa_world_cup_2026", "csl_2026"],
            per_competition_limit=1,
        )

        assert [record["run_id"] for record in records] == ["wc-live-74", "csl-live"]
        assert [record["snapshot"]["competition"]["id"] for record in records] == [
            "fifa_world_cup_2026",
            "csl_2026",
        ]
def test_league_publication_uses_one_top_level_candidate_query():
    import runpy
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from worldcup.ingest import build_ingest_payload
    from worldcup.league_publication import build_publication_vector
    from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
    fixture = runpy.run_path(str(Path(__file__).with_name("test_league_publication.py")))
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp)/"store.db")
        store.initialize(); statements = []; original = store._connect
        def connect():
            conn = original(); conn.set_trace_callback(statements.append); return conn
        store._connect = connect
        snapshot = fixture["_aggregate"]()
        parts = []
        for cid in FORMAL_SINGLE_MATCH_IDS:
            part = fixture["_component"](); part["competition"]["id"] = cid; part["matches"] = []
            parts.append(part)
        snapshot["league_publication"]["components"] = build_publication_vector(parts)
        snapshot["matches"] = []
        store.put_snapshot("six", build_ingest_payload(snapshot))
        assert len([sql for sql in statements if "json_extract(snapshot_json" in sql]) == 1
        assert any("BEGIN IMMEDIATE" in sql for sql in statements)
