# Finished Review API Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development while implementing. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, public复盘投影和 API，让已完赛 closing 复盘可被前端/静态站点消费，同时把复盘富化异常和小样本/coverage 风险显式暴露。

**Architecture:** Reuse the existing snapshot `finished` block as source of truth. Add a query-layer projection that strips internal run/quota/source details, wire it through the standard-library HTTP adapter, FastAPI wrapper, and static exporter, then surface lightweight coverage/sample notes in the ledger view. Keep server-side ingest, model logic, collectors, and refresh cadence unchanged.

**Tech Stack:** Python standard library, existing FastAPI wrapper, existing HTML string renderer, current `tests/run_tests.py` harness.

---

## File Structure

- Modify `worldcup/query.py`: add `project_finished_rows(snapshot)` and helper metadata functions.
- Modify `worldcup/http_app.py`: add `GET /api/finished`.
- Modify `worldcup/fastapi_app.py`: expose the same route through FastAPI.
- Modify `worldcup/export.py`: export `api/finished.json` and include finished projection in public snapshot.
- Modify `worldcup/ledger.py`: pass finished metadata from `build_finished_view`.
- Modify `worldcup/ledger_html.py`: render `skipped_no_closing`, coverage, and sample-size note in the history view.
- Modify `worldcup/refresh_runner.py`: write enrichment failures into `snapshot.data_quality.enrichment_errors`.
- Modify tests: add contract tests for query/http/FastAPI/export/preview/refresh behavior.
- Modify `docs/superpowers/data-contract.md`: document `GET /api/finished` and static export file.
- Modify `RECENT_WORK.md`: record local code/test changes after verification.

## Tasks

### Task 1: Safe Finished Projection

**Files:**
- Modify: `tests/test_query.py`
- Modify: `worldcup/query.py`

- [ ] **Step 1: Write failing tests**
  - Add a fixture snapshot with `finished.matches`, `tally`, `skipped_no_closing`, and internal fields that must not leak.
  - Assert `project_finished_rows()` returns `schema_version`, `summary`, and safe `matches`.
  - Assert no `run_id`, quota, raw provider name, or source error text leaks.

- [ ] **Step 2: Run query tests and confirm RED**
  - Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_query.py -q` if pytest is available, otherwise run the project harness after adding tests.
  - Expected: import failure for `project_finished_rows`.

- [ ] **Step 3: Implement minimal projection**
  - Add `project_finished_rows(snapshot)`.
  - Include only safe fields: team labels, kickoff, score, stage/group, closing snapshot time, signal count, S/A tally, coverage counts, and per-signal closing outcome/odds/trend.

- [ ] **Step 4: Run query tests and confirm GREEN**

### Task 2: API And Static Export Wiring

**Files:**
- Modify: `tests/test_http_app.py`
- Modify: `tests/test_fastapi_app.py`
- Modify: `tests/test_export.py`
- Modify: `worldcup/http_app.py`
- Modify: `worldcup/fastapi_app.py`
- Modify: `worldcup/export.py`

- [ ] **Step 1: Write failing tests**
  - `GET /api/finished` returns safe projection.
  - FastAPI wrapper exposes the same route.
  - Static export writes `api/finished.json`, references it in manifest, and includes safe finished summary in `api/snapshot/latest.json`.

- [ ] **Step 2: Run targeted tests and confirm RED**

- [ ] **Step 3: Wire route/export**
  - Delegate all projection to `worldcup.query.project_finished_rows`.
  - Do not expose full internal snapshot through new API.

- [ ] **Step 4: Run targeted tests and confirm GREEN**

### Task 3: Visible Review Quality Metadata

**Files:**
- Modify: `tests/test_ledger.py`
- Modify: `tests/test_preview.py`
- Modify: `tests/test_refresh_runner.py`
- Modify: `worldcup/ledger.py`
- Modify: `worldcup/ledger_html.py`
- Modify: `worldcup/refresh_runner.py`

- [ ] **Step 1: Write failing tests**
  - `build_finished_view()` carries `summary.skipped_no_closing`, `match_count`, `signal_count`, and `sample_too_small`.
  - Preview renders a research note when sample is small and displays skipped closing count when non-zero.
  - Refresh enrichment failure records `data_quality.enrichment_errors`.

- [ ] **Step 2: Run targeted tests and confirm RED**

- [ ] **Step 3: Implement metadata**
  - Compute metadata from `finished.matches`, `finished.tally`, and `skipped_no_closing`.
  - Use a small default threshold of `min_sample=20` for display-only caution; no model tuning or decision output.
  - Keep warning text research-only and avoid betting/execution language.

- [ ] **Step 4: Run targeted tests and confirm GREEN**

### Task 4: Documentation And Verification

**Files:**
- Modify: `docs/superpowers/data-contract.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update data contract**
  - Add `GET /api/finished`.
  - Add `api/finished.json` to static export.
  - Note the projection is public-safe and research-only.

- [ ] **Step 2: Update recent work**
  - Add a top entry summarizing local-only changes and verification.

- [ ] **Step 3: Run full verification**
  - Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
  - Expected: all tests pass.

## 对抗性自审

- This does not change model parameters, signal grading, settlement口径, data collection, quota usage, ingest HMAC, SQLite/PostgreSQL storage semantics, ECS deployment, or notifications.
- The API projection must not leak `run_id`, quota, raw provider names, source error bodies, secrets, or money/stake fields.
- `sample_too_small` is a display caution only; it must not become tuning advice or betting guidance.
- `skipped_no_closing` must be visible so missing closing data is not silently treated as complete review coverage.
- All work stays local. No live refresh, no The Odds API call, no service restart, no push, no deployment.
