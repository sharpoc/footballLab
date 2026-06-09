# Plan 3C Store Selection Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire local CLI and FastAPI startup to choose SQLite or PostgreSQL storage through explicit configuration, without connecting to a real PostgreSQL/RDS instance.

**Architecture:** Keep SQLite as the default local store. Add a small `worldcup.store_factory` module that maps `sqlite` to `SQLiteSnapshotStore` and `postgres` to `PostgresSnapshotStore`, requiring a `DATABASE_URL` only when PostgreSQL is selected. FastAPI and ingest CLIs use the factory at startup, while route logic remains store-injected and unchanged.

**Tech Stack:** Python 3.11+, existing `SnapshotStore` protocol, SQLite default, optional PostgreSQL adapter from Plan 3B, existing `tests/run_tests.py`.

---

## Boundaries

- Do not connect to a real PostgreSQL/RDS instance.
- Do not deploy, push, or write online state.
- Do not install `psycopg`.
- Do not print or commit `.env`, database URLs, passwords, API keys, or HMAC secrets.
- Keep current local commands working with no new required local secret.

## File Structure

- Create `worldcup/store_factory.py`: store selection and validation.
- Add `tests/test_store_factory.py`: factory behavior with no database connections.
- Modify `worldcup/fastapi_app.py`: CLI reads `WORLDCUP_STORE` and `DATABASE_URL` from `.env` and passes an injected store.
- Modify `worldcup/ingest_app.py`: CLI reads the same store settings before processing local ingest.
- Modify `.env.example`: add empty `WORLDCUP_STORE=` and `DATABASE_URL=`.
- Modify `worldcup/readiness.py`: ensure `.env.example` includes store variables and check `DATABASE_URL` is present only when `.env` selects PostgreSQL.
- Modify `tests/test_readiness.py`: cover SQLite default and PostgreSQL selected cases.
- Update `README.md`, `docs/superpowers/data-contract.md`, `docs/ops/local-to-cloud-checklist.md`, and `RECENT_WORK.md`.

## Task 1: Add Store Factory

**Files:**
- Create: `worldcup/store_factory.py`
- Create: `tests/test_store_factory.py`

- [ ] **Step 1: Write failing factory tests**

Add tests:

```python
from pathlib import Path

from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_factory import create_snapshot_store, normalize_store_kind


def test_normalize_store_kind_defaults_to_sqlite():
    assert normalize_store_kind(None) == "sqlite"
    assert normalize_store_kind("") == "sqlite"
    assert normalize_store_kind(" PostgreSQL ") == "postgres"


def test_create_snapshot_store_returns_sqlite_by_default():
    store = create_snapshot_store(store_kind=None, db_path=Path("data/local/worldcup.db"))

    assert isinstance(store, SQLiteSnapshotStore)
    assert store.path == Path("data/local/worldcup.db")


def test_create_snapshot_store_returns_postgres_without_connecting():
    store = create_snapshot_store(
        store_kind="postgres",
        db_path=Path("unused.db"),
        database_url="postgresql://example.invalid/worldcup",
    )

    assert isinstance(store, PostgresSnapshotStore)
    assert store.dsn == "postgresql://example.invalid/worldcup"


def test_create_snapshot_store_requires_database_url_for_postgres():
    try:
        create_snapshot_store(store_kind="postgres", db_path=Path("unused.db"), database_url="")
    except ValueError as exc:
        assert str(exc) == "DATABASE_URL is required when WORLDCUP_STORE=postgres"
    else:
        raise AssertionError("expected ValueError")


def test_create_snapshot_store_rejects_unknown_store_kind():
    try:
        create_snapshot_store(store_kind="mysql", db_path=Path("unused.db"))
    except ValueError as exc:
        assert str(exc) == "Unsupported WORLDCUP_STORE: mysql"
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: import failure for `worldcup.store_factory`.

- [ ] **Step 3: Implement store factory**

Create:

```python
from __future__ import annotations

from pathlib import Path

from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore


def normalize_store_kind(value: str | None) -> str:
    normalized = (value or "sqlite").strip().lower()
    if normalized in {"", "sqlite"}:
        return "sqlite"
    if normalized in {"postgres", "postgresql"}:
        return "postgres"
    return normalized


def create_snapshot_store(
    store_kind: str | None,
    db_path: str | Path,
    database_url: str | None = None,
) -> SnapshotStore:
    kind = normalize_store_kind(store_kind)
    if kind == "sqlite":
        return SQLiteSnapshotStore(db_path)
    if kind == "postgres":
        dsn = (database_url or "").strip()
        if not dsn:
            raise ValueError("DATABASE_URL is required when WORLDCUP_STORE=postgres")
        return PostgresSnapshotStore(dsn)
    raise ValueError(f"Unsupported WORLDCUP_STORE: {kind}")
```

- [ ] **Step 4: Verify factory tests and full suite pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/store_factory.py tests/test_store_factory.py
git commit -m "feat: add snapshot store factory"
```

## Task 2: Wire CLI Entrypoints

**Files:**
- Modify: `worldcup/fastapi_app.py`
- Modify: `worldcup/ingest_app.py`
- Modify: `tests/test_fastapi_app.py`
- Modify: `tests/test_ingest_app.py`

- [ ] **Step 1: Write failing CLI helper tests**

Add tests for helper functions:

```python
from worldcup.fastapi_app import build_store_from_env as build_fastapi_store
from worldcup.ingest_app import build_store_from_env as build_ingest_store
from worldcup.postgres_store import PostgresSnapshotStore
from worldcup.store import SQLiteSnapshotStore


def test_fastapi_build_store_from_env_defaults_to_sqlite():
    store = build_fastapi_store(env={}, db_path="data/local/worldcup.db", store_arg=None, database_url_env="DATABASE_URL")

    assert isinstance(store, SQLiteSnapshotStore)


def test_fastapi_build_store_from_env_supports_postgres():
    store = build_fastapi_store(
        env={"WORLDCUP_STORE": "postgres", "DATABASE_URL": "postgresql://example.invalid/worldcup"},
        db_path="unused.db",
        store_arg=None,
        database_url_env="DATABASE_URL",
    )

    assert isinstance(store, PostgresSnapshotStore)
    assert store.dsn == "postgresql://example.invalid/worldcup"
```

Repeat the same shape for `worldcup.ingest_app.build_store_from_env`.

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: import failure or missing `build_store_from_env`.

- [ ] **Step 3: Implement CLI helpers and args**

In both modules, add:

```python
from worldcup.store_factory import create_snapshot_store


def build_store_from_env(
    env: dict[str, str],
    db_path: str | Path,
    store_arg: str | None,
    database_url_env: str,
) -> SnapshotStore:
    store_kind = store_arg or env.get("WORLDCUP_STORE")
    return create_snapshot_store(
        store_kind=store_kind,
        db_path=db_path,
        database_url=env.get(database_url_env),
    )
```

Add CLI args:

```python
parser.add_argument("--store", default=None, choices=["sqlite", "postgres"])
parser.add_argument("--database-url-env", default="DATABASE_URL")
```

`main()` should load `.env` once, load secret from that dict, build the store, then pass `store=...` into `create_fastapi_app()` or `process_local_ingest()`.

- [ ] **Step 4: Verify full suite passes**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/fastapi_app.py worldcup/ingest_app.py tests/test_fastapi_app.py tests/test_ingest_app.py
git commit -m "feat: wire snapshot store selection into cli"
```

## Task 3: Readiness And Env Template

**Files:**
- Modify: `.env.example`
- Modify: `worldcup/readiness.py`
- Modify: `tests/test_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

Add:

```python
def test_readiness_accepts_sqlite_store_without_database_url():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\nWORLDCUP_STORE=sqlite\n")
        _write(root / ".env.example", "API_FOOTBALL_KEY=\nTHE_ODDS_API_KEY=\nODDS_API_IO_KEY=\nODDSPAPI_KEY=\nINGEST_HMAC_SECRET=\nWORLDCUP_STORE=\nDATABASE_URL=\n")
        ...
        result = run_readiness_checks(root)
        assert result["checks"]["env_store"]["status"] == "ok"
        assert result["checks"]["env_store"]["store"] == "sqlite"
```

Add:

```python
def test_readiness_requires_database_url_name_when_postgres_selected():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / ".env", "THE_ODDS_API_KEY=x\nINGEST_HMAC_SECRET=y\nWORLDCUP_STORE=postgres\n")
        ...
        result = run_readiness_checks(root)
        assert result["checks"]["env_store"]["status"] == "error"
        assert result["checks"]["env_store"]["message"] == "missing_DATABASE_URL"
        assert "postgresql://" not in str(result)
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: readiness tests fail because `env_store` does not exist and `.env.example` lacks names.

- [ ] **Step 3: Implement readiness support**

Add `WORLDCUP_STORE` and `DATABASE_URL` to `REQUIRED_ENV_EXAMPLE_NAMES`.

Add `_check_store_env(root)`:

```python
entries = _read_env_entries(root / ".env")
store = normalize_store_kind(entries.get("WORLDCUP_STORE"))
if store not in {"sqlite", "postgres"}:
    return "env_store", {"status": "error", "name": "WORLDCUP_STORE", "message": "unsupported_store", "store": store}
if store == "postgres" and "DATABASE_URL" not in entries:
    return "env_store", {"status": "error", "name": "DATABASE_URL", "message": "missing_DATABASE_URL", "store": store}
return "env_store", {"status": "ok", "name": "WORLDCUP_STORE", "store": store}
```

Do not include `DATABASE_URL` value in readiness output.

- [ ] **Step 4: Update `.env.example`**

Add:

```text
WORLDCUP_STORE=
DATABASE_URL=
```

- [ ] **Step 5: Verify full suite and readiness pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
```

Expected: all tests pass; local readiness remains green because local `.env` defaults to SQLite when `WORLDCUP_STORE` is absent.

- [ ] **Step 6: Commit**

```bash
git add .env.example worldcup/readiness.py tests/test_readiness.py
git commit -m "feat: add store selection readiness checks"
```

## Task 4: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `docs/ops/local-to-cloud-checklist.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update docs**

Document:

- `WORLDCUP_STORE` defaults to SQLite.
- `WORLDCUP_STORE=postgres` requires `DATABASE_URL`.
- `DATABASE_URL` must remain in `.env` or cloud secret manager only.
- Plan 3C still does not connect to a real database or deploy.

- [ ] **Step 2: Run final verification**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
git diff --check
git status --short --branch
```

Run tracked-file sensitive value scan using `.env` values for variables containing `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, or `DATABASE_URL`. Expected: `tracked_sensitive_value_leaks= 0`.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/data-contract.md docs/ops/local-to-cloud-checklist.md RECENT_WORK.md
git commit -m "docs: record store selection workflow"
```

## Completion Criteria

- Existing SQLite defaults continue to work.
- CLI callers can explicitly select `--store sqlite` or `--store postgres`.
- `.env` can select `WORLDCUP_STORE=postgres` with `DATABASE_URL` without changing route code.
- Readiness does not require `DATABASE_URL` unless PostgreSQL is selected.
- No real PostgreSQL/RDS connection is attempted in tests.
- Full test suite and readiness pass.
- No secrets, `.env`, `data/cache/`, `data/local/`, or `data/probe/` are tracked.
