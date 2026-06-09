from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore


def test_sqlite_store_satisfies_snapshot_store_protocol():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")

        assert isinstance(store, SnapshotStore)
        store.initialize()
        assert store.count_snapshots() == 0
