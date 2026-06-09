# Plan 3D PostgreSQL Smoke Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dry-run PostgreSQL smoke guard that verifies local smoke prerequisites and produces a redacted signed-ingest summary before any real test-environment database smoke is attempted.

**Architecture:** Keep real PostgreSQL/RDS connections out of this phase. The new module reads a snapshot and an env dictionary, validates that PostgreSQL mode has the required variable names, builds a signed ingest request only when the HMAC secret is available, and returns redacted request metadata with no body, signature, secret, or database URL value. A CLI prints the summary and exits non-zero when required smoke prerequisites are missing.

**Tech Stack:** Python 3.11+, existing `worldcup.ingest` HMAC builder, existing `.env` parser from `refresh_runner`, existing `tests/run_tests.py`.

---

## Boundaries

- Do not connect to PostgreSQL/RDS.
- Do not send HTTP requests.
- Do not install dependencies.
- Do not print `INGEST_HMAC_SECRET`, `DATABASE_URL`, `X-Worldcup-Signature`, or request body.
- Keep this as a staging-preflight guard, not a deployment script.

## File Structure

- Create `worldcup/postgres_smoke.py`: redacted dry-run builder and CLI.
- Add `tests/test_postgres_smoke.py`: redaction, blocked-state, and ready-state tests.
- Update `README.md`, `docs/superpowers/data-contract.md`, `docs/ops/local-to-cloud-checklist.md`, and `RECENT_WORK.md`.

## Task 1: PostgreSQL Smoke Dry-Run Guard

**Files:**
- Create: `worldcup/postgres_smoke.py`
- Create: `tests/test_postgres_smoke.py`

- [ ] **Step 1: Write failing smoke guard tests**

Add tests:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.postgres_smoke import build_postgres_smoke_dry_run


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "20260608T000000Z-local"},
        "counts": {"matches": 1},
        "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
    }


def test_postgres_smoke_blocks_when_database_url_missing_without_printing_secret():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "postgres",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        serialized = json.dumps(result, ensure_ascii=False)
        assert result["status"] == "blocked"
        assert result["checks"]["database_url"]["status"] == "error"
        assert result["checks"]["database_url"]["message"] == "missing_DATABASE_URL"
        assert "very-secret-value" not in serialized
```

```python
def test_postgres_smoke_ready_summary_is_redacted():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "postgres",
                "DATABASE_URL": "postgresql://user:pass@example.invalid/db",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        serialized = json.dumps(result, ensure_ascii=False)
        assert result["status"] == "dry_run_ready"
        assert result["checks"]["store"]["store"] == "postgres"
        assert result["checks"]["database_url"]["status"] == "ok"
        assert result["request"]["method"] == "POST"
        assert result["request"]["path"] == "/api/ingest/snapshot"
        assert result["request"]["run_id"] == "20260608T000000Z-local"
        assert result["request"]["body_sha256"]
        assert result["expected_sequence"] == ["stored", "duplicate"]
        assert "very-secret-value" not in serialized
        assert "postgresql://user:pass@example.invalid/db" not in serialized
        assert "X-Worldcup-Signature" not in serialized
        assert '"body"' not in serialized
```

```python
def test_postgres_smoke_blocks_when_store_is_not_postgres():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_postgres_smoke_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            env={
                "WORLDCUP_STORE": "sqlite",
                "INGEST_HMAC_SECRET": "very-secret-value",
            },
            timestamp="2026-06-08T00:02:00+00:00",
        )

        assert result["status"] == "blocked"
        assert result["checks"]["store"]["message"] == "expected_postgres"
```

- [ ] **Step 2: Verify tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: import failure for `worldcup.postgres_smoke`.

- [ ] **Step 3: Implement dry-run guard**

Create `worldcup/postgres_smoke.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup.ingest import build_ingest_request
from worldcup.refresh_runner import _load_env
from worldcup.store_factory import normalize_store_kind


def _check_store(env: dict[str, str]) -> dict[str, Any]:
    store = normalize_store_kind(env.get("WORLDCUP_STORE"))
    if store != "postgres":
        return {"status": "error", "store": store, "message": "expected_postgres"}
    return {"status": "ok", "store": store}


def build_postgres_smoke_dry_run(...):
    ...
```

Implementation requirements:

- Return `status = blocked` if store is not postgres, `DATABASE_URL` is missing, secret is missing, snapshot is unreadable, or request construction fails.
- Return `status = dry_run_ready` only when all checks pass.
- Never include `DATABASE_URL` value, secret value, request body, or `X-Worldcup-Signature` in output.
- Include only safe request metadata: method, URL, path, header names, run_id, snapshot_id, body_sha256, idempotency_key, body_bytes.
- Include `expected_sequence = ["stored", "duplicate"]`.
- CLI defaults:

```bash
python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

The CLI prints JSON and exits `0` only when `status == dry_run_ready`.

- [ ] **Step 4: Verify full suite passes**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/postgres_smoke.py tests/test_postgres_smoke.py
git commit -m "feat: add postgres smoke dry-run guard"
```

## Task 2: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `docs/ops/local-to-cloud-checklist.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update docs**

Document:

- `worldcup.postgres_smoke` is a dry-run guard only.
- It requires `WORLDCUP_STORE=postgres`, `DATABASE_URL`, and `INGEST_HMAC_SECRET`.
- It prints redacted request metadata only.
- A real PostgreSQL smoke still requires separate confirmation and a test database.

- [ ] **Step 2: Run final verification**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
git diff --check
```

Run a tracked-file sensitive value scan using `.env` values for variables containing `KEY`, `SECRET`, `TOKEN`, `PASSWORD`, or `DATABASE_URL`. Expected: `tracked_sensitive_value_leaks= 0`.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/data-contract.md docs/ops/local-to-cloud-checklist.md RECENT_WORK.md
git commit -m "docs: record postgres smoke guard workflow"
```

## Completion Criteria

- Dry-run guard can report blocked state without leaking secrets.
- Ready summary omits body, HMAC signature, secret, and database URL value.
- Full tests and readiness pass.
- No real PostgreSQL/RDS connection is attempted.
- No deployment or push is performed.
