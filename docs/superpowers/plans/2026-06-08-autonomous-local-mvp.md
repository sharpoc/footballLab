# Autonomous Local MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete low-risk local preview path from signed ingest request to persisted snapshot, query/export data, and a static read-only preview page without deploying, pushing, committing, consuming live API quota, or writing online state.

**Architecture:** Keep the current standard-library-first architecture. Add a SQLite persistence layer for ingest idempotency and snapshot storage, an application service that wraps existing HMAC verification, a query/export layer for the latest snapshot, and a static HTML preview generator. All behavior is test-first and runs locally.

**Tech Stack:** Python 3.11+, standard library only (`sqlite3`, `json`, `html`, `pathlib`, `argparse`), existing `tests/run_tests.py`.

---

## Execution Boundaries

Allowed without further confirmation:
- Modify local project files related to storage, query, preview, tests, docs.
- Run local tests and local dry-run CLI commands.
- Read ignored local cache files when needed.

Not allowed while the user is asleep:
- `git commit`, `git push`, branch publication, PR creation.
- Deployment, DNS/SSL changes, cloud resource changes.
- `refresh_runner --live`, `scheduled_refresh --live`, or any command that consumes API quota.
- Online writes to ECS/RDS/OSS or any external service.
- Deleting project data, destructive git commands, migrations against real databases.

---

## File Structure

- Create `worldcup/store.py`: SQLite schema, idempotent snapshot persistence, latest snapshot lookup.
- Create `tests/test_store.py`: persistence and idempotency tests.
- Create `worldcup/ingest_app.py`: application service combining `ingest_server` verification with SQLite storage.
- Create `tests/test_ingest_app.py`: accepted, duplicate, and rejected ingest request tests.
- Create `worldcup/query.py`: read latest snapshot and flatten match rows for preview/API use.
- Create `tests/test_query.py`: latest snapshot and match row projection tests.
- Create `worldcup/preview.py`: generate a static HTML preview from persisted/latest snapshot.
- Create `tests/test_preview.py`: generated HTML contains disclaimer, data quality, match rows, and no betting amounts.
- Modify `README.md`, `AGENTS.md`, `CLAUDE.md`, `RECENT_WORK.md`, `docs/superpowers/data-contract.md`: document local storage, query, and preview behavior.

---

### Task 1: SQLite Snapshot Store

**Files:**
- Create: `worldcup/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write failing tests for schema initialization and idempotent insert**

Test requirements:
- `SQLiteSnapshotStore.initialize()` creates a usable local database.
- `put_snapshot()` stores the first payload and returns `stored`.
- Repeating the same idempotency key returns `duplicate`.
- `latest_snapshot()` returns the latest stored snapshot payload.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: import failure for `worldcup.store` or missing symbols.

- [ ] **Step 3: Implement `SQLiteSnapshotStore`**

Implementation notes:
- Use `sqlite3`.
- Table `snapshots`:
  - `idempotency_key TEXT PRIMARY KEY`
  - `run_id TEXT NOT NULL`
  - `snapshot_id TEXT NOT NULL`
  - `snapshot_at TEXT`
  - `stored_at TEXT NOT NULL`
  - `payload_json TEXT NOT NULL`
  - `snapshot_json TEXT NOT NULL`
- Use `INSERT OR IGNORE` for idempotency.
- Return plain dicts, not ORM objects.

- [ ] **Step 4: Run tests to verify GREEN**

Expected: store tests pass and full suite remains green.

---

### Task 2: Ingest Application Service

**Files:**
- Create: `worldcup/ingest_app.py`
- Create: `tests/test_ingest_app.py`

- [ ] **Step 1: Write failing tests for local ingest application**

Test requirements:
- A signed request built by `worldcup.ingest.build_ingest_request()` is accepted and stored in SQLite.
- Sending the same request twice returns `duplicate`.
- Tampered body returns `rejected` and does not write.

- [ ] **Step 2: Run tests to verify RED**

Run the full local test runner. Expected: missing `worldcup.ingest_app`.

- [ ] **Step 3: Implement `process_local_ingest()`**

Implementation notes:
- Accept `db_path`, `method`, `path`, `headers`, `body`, `secret`, `now`.
- Call `verify_ingest_request()`.
- On reject, return `{"status": "rejected", "reason": ...}`.
- On accept, initialize `SQLiteSnapshotStore`, persist by idempotency key, return `stored` or `duplicate`.

- [ ] **Step 4: Run tests to verify GREEN**

Expected: all tests pass.

---

### Task 3: Query and Projection Layer

**Files:**
- Create: `worldcup/query.py`
- Create: `tests/test_query.py`

- [ ] **Step 1: Write failing tests for latest snapshot query and row projection**

Test requirements:
- `load_latest_snapshot(db_path)` returns latest persisted snapshot.
- `project_match_rows(snapshot)` returns compact rows with kickoff, teams, stage, market signal count, top grade, stale flag.
- Missing optional fields produce stable empty/default values.

- [ ] **Step 2: Run tests to verify RED**

Expected: missing `worldcup.query`.

- [ ] **Step 3: Implement query functions**

Implementation notes:
- Keep functions pure after loading data.
- Do not add betting amount fields.
- Return JSON-serializable dict/list data only.

- [ ] **Step 4: Run tests to verify GREEN**

Expected: all tests pass.

---

### Task 4: Static Preview Generator

**Files:**
- Create: `worldcup/preview.py`
- Create: `tests/test_preview.py`

- [ ] **Step 1: Write failing tests for static preview generation**

Test requirements:
- `build_preview_html(snapshot)` includes title, disclaimer, counts, data quality, and match rows.
- Output never contains betting amount labels such as `stake`, `bet amount`, `下注金额`.
- `write_preview(snapshot, output_path)` writes parent directories and HTML file.

- [ ] **Step 2: Run tests to verify RED**

Expected: missing `worldcup.preview`.

- [ ] **Step 3: Implement HTML generation**

Implementation notes:
- Use standard library `html.escape`.
- Keep visual design simple and readable.
- Include data quality section for `stale_sources`, `source_errors`, `missing_odds`, `missing_elo`, `time_mismatches`.
- Include disclaimer: `研究分析工具，不构成投注建议。`

- [ ] **Step 4: Run tests to verify GREEN**

Expected: all tests pass.

---

### Task 5: Local CLI Wiring

**Files:**
- Modify: `worldcup/ingest_app.py`
- Modify: `worldcup/preview.py`
- Add tests if CLI behavior needs direct coverage.

- [ ] **Step 1: Add dry-run-safe CLIs**

Commands:

```bash
python3 -m worldcup.ingest_app --db data/local/worldcup.db --snapshot data/cache/analysis_snapshot.json --env .env
python3 -m worldcup.preview --snapshot data/cache/analysis_snapshot.json --out data/cache/preview.html
```

Constraints:
- Do not send network requests.
- Do not require real cloud endpoint.
- Do not print secrets.
- `data/local/` must remain ignored before writing there; if not ignored, add it to `.gitignore`.

- [ ] **Step 2: Verify CLIs locally with temporary files**

Use temporary directories or ignored `data/cache/` outputs only.

- [ ] **Step 3: Run full tests**

Expected: all tests pass.

---

### Task 6: Documentation and Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`
- Modify: `docs/superpowers/data-contract.md`

- [ ] **Step 1: Document new local MVP path**

Document:
- SQLite store purpose and no-cloud boundary.
- Ingest application service behavior.
- Query/preview commands.
- Safety rule: no betting advice, no stake amounts, no online writes by default.

- [ ] **Step 2: Run final verification**

Commands:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.refresh_runner
git diff --check
git check-ignore -v .env data/probe/openfootball_2026.json data/cache/analysis_snapshot.json data/cache/theoddsapi_wc_odds.json data/cache/quota.json
```

- [ ] **Step 3: Security scan**

Scan for the known The Odds API key outside ignored files:

```bash
key="$(awk -F= '/^THE_ODDS_API_KEY=/{print $2; exit}' .env | tr -d "\"'")"
if [ -n "$key" ]; then
  rg -l --fixed-strings "$key" --glob '!.env' --glob '!data/probe/**' --glob '!data/cache/**' .
fi
```

Expected: no output.

---

## Self-Review

- Spec coverage: The plan covers the next local MVP path without cloud writes: persistent store, ingest app, query, preview, docs, verification.
- Placeholder scan: No task uses `TBD`, `TODO`, or unspecified implementation.
- Type consistency: Store returns plain dicts; ingest app consumes `verify_ingest_request`; preview consumes snapshot/query projection.
- Risk boundary: Deployment, push, commit, live refresh, and online writes are explicitly excluded.
