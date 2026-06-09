# Plan 3B PostgreSQL Store Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostgreSQL snapshot store adapter behind the existing `SnapshotStore` boundary, while keeping current SQLite local behavior and FastAPI route contracts intact.

**Architecture:** First make `query`, `ingest_app`, `http_app`, and `fastapi_app` accept an injected `SnapshotStore` so the API layer no longer directly depends on SQLite. Then add `PostgresSnapshotStore` with the same idempotent write and latest-read semantics as `SQLiteSnapshotStore`. Tests use fake PostgreSQL connections, so this phase does not require a real RDS, local PostgreSQL server, live cloud credentials, or dependency installation.

**Tech Stack:** Python 3.11+, existing `SnapshotStore` protocol, SQLite for local default, optional `psycopg` for future PostgreSQL deployment, existing `tests/run_tests.py`.

---

## Boundaries

- Do not connect to a real PostgreSQL/RDS instance.
- Do not deploy, push, or write online state.
- Do not print or commit `.env`, database URLs, passwords, or secrets.
- Keep SQLite as the default for local CLI and FastAPI startup.
- Declare `psycopg` as an optional dependency only; do not install it in this phase.

## File Structure

- Modify `worldcup/query.py`: allow loading the latest snapshot from an injected `SnapshotStore`.
- Modify `worldcup/ingest_app.py`: allow signed ingest processing to write through an injected `SnapshotStore`.
- Modify `worldcup/http_app.py`: pass the injected store through GET and POST route handling.
- Modify `worldcup/fastapi_app.py`: accept an optional store in `create_fastapi_app`.
- Create `worldcup/postgres_store.py`: PostgreSQL implementation of `SnapshotStore`.
- Modify `worldcup/store_contract.py`: add `initialize()` to the protocol because both stores need schema setup.
- Modify `pyproject.toml`: add optional `postgres` dependency for future deployment installs.
- Add/modify tests in `tests/test_query.py`, `tests/test_ingest_app.py`, `tests/test_http_app.py`, `tests/test_fastapi_app.py`, `tests/test_store_contract.py`, and `tests/test_postgres_store.py`.
- Update `README.md`, `docs/superpowers/data-contract.md`, `docs/ops/local-to-cloud-checklist.md`, and `RECENT_WORK.md`.

## Task 1: Store Injection Boundary

**Files:**
- Modify: `tests/test_query.py`
- Modify: `tests/test_ingest_app.py`
- Modify: `tests/test_http_app.py`
- Modify: `tests/test_fastapi_app.py`
- Modify: `worldcup/query.py`
- Modify: `worldcup/ingest_app.py`
- Modify: `worldcup/http_app.py`
- Modify: `worldcup/fastapi_app.py`

- [ ] **Step 1: Write failing injection tests**

Add tests that prove route/query/ingest code can use a non-SQLite store:

```python
class MemorySnapshotStore:
    def __init__(self, latest=None):
        self.latest = latest
        self.puts = []
        self.initialized = False

    def initialize(self):
        self.initialized = True

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
```

Required assertions:

```python
snapshot = load_latest_snapshot(store=store)
assert snapshot["counts"]["matches"] == 1
```

```python
result = process_local_ingest(
    db_path="unused.db",
    method=request["method"],
    path=request["path"],
    headers=request["headers"],
    body=request["body"],
    secret="test-hmac-secret",
    now="2026-06-08T00:03:00+00:00",
    store=store,
)
assert result["status"] == "stored"
assert store.count_snapshots() == 1
```

```python
response = handle_request(
    method="GET",
    path="/api/matches",
    headers={},
    body="",
    db_path="unused.db",
    secret="test-hmac-secret",
    store=store,
)
assert response["status"] == 200
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: new tests fail with unexpected keyword argument `store`.

- [ ] **Step 3: Implement minimal injection support**

Change signatures to accept `store: SnapshotStore | None = None`. Use `store or SQLiteSnapshotStore(db_path)`.

The core shape should be:

```python
def load_latest_snapshot(db_path: str | Path = "data/local/worldcup.db", store: SnapshotStore | None = None) -> dict[str, Any] | None:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    latest = snapshot_store.latest_snapshot()
    if latest is None:
        return None
    return latest["snapshot"]
```

```python
def process_local_ingest(..., store: SnapshotStore | None = None) -> dict[str, Any]:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    if not verification.ok:
        snapshot_store.initialize()
        return {"status": "rejected", "reason": verification.reason}
    return snapshot_store.put_snapshot(...)
```

`handle_request` and FastAPI `_dispatch` should pass the same optional store through to `load_latest_snapshot` and `process_local_ingest`.

- [ ] **Step 4: Verify injection tests and full suite pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_query.py tests/test_ingest_app.py tests/test_http_app.py tests/test_fastapi_app.py worldcup/query.py worldcup/ingest_app.py worldcup/http_app.py worldcup/fastapi_app.py
git commit -m "refactor: inject snapshot store into api routes"
```

## Task 2: PostgreSQL Store Adapter

**Files:**
- Create: `worldcup/postgres_store.py`
- Create: `tests/test_postgres_store.py`
- Modify: `tests/test_store_contract.py`
- Modify: `worldcup/store_contract.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing PostgreSQL adapter tests**

Add fake connection tests covering:

```python
store = PostgresSnapshotStore(dsn="postgresql://example.invalid/db", connection_factory=factory)
assert isinstance(store, SnapshotStore)
```

```python
first = store.put_snapshot("run-1:snapshot-1", payload, stored_at="2026-06-08T00:02:00+00:00")
second = store.put_snapshot("run-1:snapshot-1", payload, stored_at="2026-06-08T00:03:00+00:00")
assert first["status"] == "stored"
assert second["status"] == "duplicate"
```

```python
latest = store.latest_snapshot()
assert latest["run_id"] == "run-2"
assert latest["snapshot"]["counts"]["matches"] == 1
```

The fake connection should implement context manager methods, `execute(sql, params=None)`, `fetchone()`, and `commit()` so tests can inspect SQL without a real database.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: import failure for `worldcup.postgres_store`.

- [ ] **Step 3: Implement `PostgresSnapshotStore`**

Create a store with these behaviors:

```python
class PostgresSnapshotStore(SnapshotStore):
    def __init__(self, dsn: str, connection_factory=None) -> None:
        self.dsn = dsn
        self.connection_factory = connection_factory
```

Required SQL semantics:

```sql
CREATE TABLE IF NOT EXISTS snapshots (
  idempotency_key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  snapshot_at TIMESTAMPTZ,
  stored_at TIMESTAMPTZ NOT NULL,
  payload_json JSONB NOT NULL,
  snapshot_json JSONB NOT NULL
)
```

```sql
INSERT INTO snapshots (...) VALUES (...)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key
```

```sql
SELECT ... FROM snapshots ORDER BY stored_at DESC, idempotency_key DESC LIMIT 1
```

If `connection_factory` is absent, import `psycopg` lazily inside `_connect()`. If import fails, raise `RuntimeError("psycopg is required for PostgresSnapshotStore; install worldcup[postgres]")`.

- [ ] **Step 4: Add optional PostgreSQL dependency**

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
postgres = [
  "psycopg[binary]>=3.2,<4",
]
```

- [ ] **Step 5: Verify full suite passes**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass without local `psycopg` installed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml worldcup/store_contract.py worldcup/postgres_store.py tests/test_postgres_store.py tests/test_store_contract.py
git commit -m "feat: add postgres snapshot store adapter"
```

## Task 3: Documentation And Local Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `docs/ops/local-to-cloud-checklist.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update docs**

Document:

- SQLite remains the default local store.
- PostgreSQL adapter is available through `PostgresSnapshotStore`.
- Real deployment should install `worldcup[postgres]` or equivalent `psycopg[binary]`.
- RDS connection strings and passwords must only live in `.env` or cloud secret manager.
- No real PostgreSQL connection was made in this phase.

- [ ] **Step 2: Regenerate local cache smoke artifacts only if needed**

If local smoke uses current `data/cache/analysis_snapshot.json`, keep it ignored and do not commit it.

- [ ] **Step 3: Run final verification**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
git diff --check
git status --short --branch
```

Run a tracked-file sensitive value scan using `.env` values for variables containing `KEY`, `SECRET`, `TOKEN`, or `PASSWORD`. Expected: `tracked_sensitive_value_leaks= 0`.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/data-contract.md docs/ops/local-to-cloud-checklist.md RECENT_WORK.md
git commit -m "docs: record postgres store adapter workflow"
```

## Completion Criteria

- Existing SQLite CLI/FastAPI behavior remains unchanged.
- `PostgresSnapshotStore` satisfies `SnapshotStore`.
- Idempotent write returns `stored` then `duplicate`.
- Latest snapshot query returns parsed `payload` and `snapshot`.
- Tests pass without a real PostgreSQL server or `psycopg` installed.
- Readiness remains green for the current local SQLite/cache setup.
- No secrets, database URLs, `.env`, `data/cache/`, `data/local/`, or `data/probe/` are tracked.
