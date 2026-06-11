# Match-Specific Refresh Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent `next_update_at` plans for every match and show them in the research ledger.

**Architecture:** Keep the existing global scheduler, but add per-match refresh plans and derive the global `next_due_at` from the earliest match plan. Store plans in snapshot run metadata and project them into ledger/API-safe rows.

**Tech Stack:** Python standard library, existing `tests/run_tests.py`, no network calls, no external services.

---

### Task 1: Scheduler Match Plans

**Files:**
- Modify: `worldcup/scheduler.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing tests**

Add tests for a normal critical anchor, low-quota preserved anchors, quota exhausted, and global report using earliest match plan.

- [ ] **Step 2: Run scheduler tests to verify failure**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: fails because `build_match_refresh_plan` / `match_plans` do not exist yet.

- [ ] **Step 3: Implement minimal scheduler support**

Add focused helpers in `worldcup/scheduler.py`:

- `build_match_refresh_plan(now, last_refresh_at, match, quota_remaining)`
- `build_match_refresh_plans(now, last_refresh_at, matches, quota_remaining)`
- `build_match_refresh_decision(now, last_refresh_at, matches, quota_remaining)`

Keep `build_refresh_decision` backward compatible.

- [ ] **Step 4: Run scheduler tests**

Expected: scheduler tests pass.

### Task 2: Snapshot Metadata

**Files:**
- Modify: `worldcup/local_runner.py`
- Modify: `worldcup/refresh_runner.py`
- Test: `tests/test_local_runner.py`
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: Write failing tests**

Assert generated local/live snapshot run policy contains `match_plans`, and each match with `kickoff_at_utc` has a matching `refresh_plan`.

- [ ] **Step 2: Run tests to verify failure**

Expected: tests fail because snapshots do not yet attach per-match refresh plans.

- [ ] **Step 3: Attach plans**

Use scheduler helpers after matches are built. Copy the matching plan into each match as `refresh_plan`, and pass the match-aware decision to `build_run_metadata`.

- [ ] **Step 4: Run tests**

Expected: local/refresh runner tests pass.

### Task 3: Ledger And API Projection

**Files:**
- Modify: `worldcup/ledger.py`
- Modify: `worldcup/query.py`
- Test: `tests/test_ledger.py`
- Test: `tests/test_query.py`

- [ ] **Step 1: Write failing tests**

Assert signal rows expose `next_update_time`, `next_update_label`, `next_update_full`, and detail item `下次更新`. Assert `/api/matches` projection includes safe `next_update_at` and `next_update_label`.

- [ ] **Step 2: Run tests to verify failure**

Expected: tests fail because projection fields are missing.

- [ ] **Step 3: Implement projection**

Format the existing `match.refresh_plan` with Beijing time. Do not remove existing `updated_*` fields.

- [ ] **Step 4: Run tests**

Expected: ledger/query tests pass.

### Task 4: HTML Display

**Files:**
- Modify: `worldcup/ledger_html.py`
- Test: `tests/test_preview.py`

- [ ] **Step 1: Write failing tests**

Assert the table contains `<th scope="col">下次更新</th>`, row content contains a formatted next-update time and label, and the update policy card says `按每场比赛独立调度`.

- [ ] **Step 2: Run preview test to verify failure**

Expected: preview test fails because the column/card text is missing.

- [ ] **Step 3: Implement HTML changes**

Add the new column after existing “更新”. Update right-rail policy copy and nearest planned match summary.

- [ ] **Step 4: Run all tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

### Task 5: Docs

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update docs**

Document match-specific scheduling, low-quota behavior, and no live refresh/deploy performed.

- [ ] **Step 2: Run final verification**

Run all tests and `git status --short`. Do not push or deploy.
