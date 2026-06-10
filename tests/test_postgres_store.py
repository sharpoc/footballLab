import json

from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store_contract import SnapshotStore


def _payload(run_id="run-1", snapshot_id="snapshot-1"):
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


class FakePostgresConnection:
    def __init__(self, rows, statements):
        self.rows = rows
        self.statements = statements
        self._last_row = None
        self._last_rows = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        upper = normalized.upper()
        self._last_rows = []
        if upper.startswith("CREATE TABLE") or upper.startswith("CREATE INDEX"):
            self._last_row = None
        elif upper.startswith("INSERT INTO SNAPSHOTS"):
            key = params[0]
            if key in self.rows:
                self._last_row = None
            else:
                self.rows[key] = {
                    "idempotency_key": params[0],
                    "run_id": params[1],
                    "snapshot_id": params[2],
                    "snapshot_at": params[3],
                    "stored_at": params[4],
                    "payload_json": params[5],
                    "snapshot_json": params[6],
                }
                self._last_row = (key,)
        elif upper.startswith("SELECT COUNT(*)"):
            self._last_row = (len(self.rows),)
        elif upper.startswith("SELECT"):
            if not self.rows:
                self._last_row = None
            else:
                sorted_rows = sorted(
                    self.rows.values(),
                    key=lambda row: (row["stored_at"], row["idempotency_key"]),
                    reverse=True,
                )
                if params:
                    sorted_rows = sorted_rows[: params[0]]
                self._last_rows = [
                    (
                        row["idempotency_key"],
                        row["run_id"],
                        row["snapshot_id"],
                        row["snapshot_at"],
                        row["stored_at"],
                        row["payload_json"],
                        row["snapshot_json"],
                    )
                    for row in sorted_rows
                ]
                self._last_row = self._last_rows[0]
        else:
            raise AssertionError(f"Unexpected SQL: {normalized}")
        return self

    def fetchone(self):
        return self._last_row

    def fetchall(self):
        return self._last_rows

    def commit(self):
        self.commits += 1


class FakePostgresFactory:
    def __init__(self):
        self.rows = {}
        self.statements = []
        self.connections = []

    def __call__(self, dsn):
        assert dsn == "postgresql://example.invalid/worldcup"
        connection = FakePostgresConnection(self.rows, self.statements)
        self.connections.append(connection)
        return connection


def test_postgres_snapshot_store_satisfies_protocol_and_initializes_schema():
    factory = FakePostgresFactory()
    store = PostgresSnapshotStore(
        dsn="postgresql://example.invalid/worldcup",
        connection_factory=factory,
    )

    store.initialize()

    assert isinstance(store, SnapshotStore)
    assert any("CREATE TABLE IF NOT EXISTS snapshots" in sql for sql, _ in factory.statements)
    assert any("payload_json JSONB NOT NULL" in sql for sql, _ in factory.statements)


def test_postgres_snapshot_store_put_is_idempotent():
    factory = FakePostgresFactory()
    store = PostgresSnapshotStore(
        dsn="postgresql://example.invalid/worldcup",
        connection_factory=factory,
    )

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
    assert any("ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key" in sql for sql, _ in factory.statements)


def test_postgres_snapshot_store_latest_snapshot_returns_latest_by_stored_at():
    factory = FakePostgresFactory()
    store = PostgresSnapshotStore(
        dsn="postgresql://example.invalid/worldcup",
        connection_factory=factory,
    )
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


def test_postgres_snapshot_store_list_recent_snapshots_returns_newest_first():
    factory = FakePostgresFactory()
    store = PostgresSnapshotStore(
        dsn="postgresql://example.invalid/worldcup",
        connection_factory=factory,
    )
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
    assert any("LIMIT %s" in sql for sql, _ in factory.statements)
