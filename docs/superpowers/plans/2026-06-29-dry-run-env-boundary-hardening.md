# Dry-Run Env Boundary Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure default dry-run CLI paths do not read `.env`, secrets, or API keys before a live action is explicitly requested.

**Architecture:** Keep this as a small boundary hardening change, not a new profile system. Move env loading behind existing `live` / `live-scores` gates and add focused tests that monkeypatch `_load_env` to fail when dry-run paths touch it.

**Tech Stack:** Python standard library, existing local test runner, existing CLI function patterns.

---

## Scope

- Harden only dry-run env boundaries for scheduled refresh, scheduled publish, scores capture, and daily eval.
- Preserve all current live behavior, key rotation, quota ledger reads, publish behavior, notification behavior, and knockout score guard.
- Do not change model probabilities, signal grading, CSL rating policy, snapshot schemas, deployment, LaunchAgent, or online resources.
- Do not read real `.env`, call The Odds API, publish, notify, or deploy while implementing this plan.

## Files

- Modify: `worldcup/scheduled_refresh.py`
- Modify: `worldcup/scheduled_publish.py`
- Modify: `worldcup/scores_capture.py`
- Modify: `worldcup/daily_eval.py`
- Modify: `tests/test_scheduled_refresh.py`
- Modify: `tests/test_scheduled_publish.py`
- Modify: `tests/test_scores_capture.py`
- Modify: `tests/test_daily_eval.py`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

## Tasks

### Task 1: Scheduled Refresh Dry-Run Env Boundary

- [ ] Add a failing test in `tests/test_scheduled_refresh.py` named `test_scheduled_refresh_dry_run_does_not_load_env`.
- [ ] Monkeypatch `worldcup.scheduled_refresh._load_env` to raise `AssertionError("dry-run must not load env")`.
- [ ] Call `run_scheduled_refresh(live=False, env_path=root / ".env", ...)`.
- [ ] Expected red failure: current code calls `_load_env` before checking `live`.
- [ ] Implement minimal fix: in `run_scheduled_refresh`, load env only when `live=True` and no explicit `api_key` is passed.
- [ ] Re-run the focused test and keep existing scheduled refresh tests passing.

### Task 2: Scheduled Publish Dry-Run Env Boundary

- [ ] Add a failing test in `tests/test_scheduled_publish.py` named `test_scheduled_publish_dry_run_does_not_load_env_or_publish`.
- [ ] Monkeypatch `worldcup.scheduled_publish._load_env` to raise `AssertionError("dry-run must not load env")`.
- [ ] Use fake `refresh_fn` and `publish_fn` that raise if called.
- [ ] Call `run_scheduled_publish(live=False, notify=False, env_path=root / ".env", ...)`.
- [ ] Expected red failure: current code loads env before calling scheduled refresh.
- [ ] Implement minimal fix: in `run_scheduled_publish`, only load env and quota notification state when `live=True` and those values are needed.
- [ ] Ensure dry-run returns `status="dry_run"`, `publish=None`, `notification=None`, and does not call refresh/publish.

### Task 3: Scores Capture Dry-Run Env Boundary

- [ ] Add a failing CLI test in `tests/test_scores_capture.py` named `test_scores_capture_main_dry_run_does_not_load_env`.
- [ ] Monkeypatch `worldcup.scores_capture._load_env` to raise `AssertionError("dry-run must not load env")`.
- [ ] Call `worldcup.scores_capture.main([...])` without `--live`.
- [ ] Expected red failure: current CLI passes `_load_env(args.env)` even for dry-run.
- [ ] Implement minimal fix: in `scores_capture.main`, pass `{}` unless `args.live` is true.
- [ ] Keep existing `run_scores_capture(live=False, ...)` test passing.

### Task 4: Daily Eval Live-Scores Env Boundary

- [ ] Add a test in `tests/test_daily_eval.py` named `test_daily_eval_without_live_scores_does_not_load_env`.
- [ ] Monkeypatch the relevant loader so any `.env` read fails when `--live-scores` is absent.
- [ ] Call `worldcup.daily_eval.main([...])` without `--live-scores`.
- [ ] Expected behavior: no env read and no scores capture call.
- [ ] If the test is already green, keep it as regression coverage and do not change implementation unnecessarily.
- [ ] Verify `--live-scores` path still injects env only for live scores capture.

### Task 5: Docs and Recent Work

- [ ] Update `README.md` current status from `622/622` to the new full test count.
- [ ] Add a short line clarifying that scheduled refresh/publish/scores dry-run paths do not read `.env`; only live variants load env.
- [ ] Add a top entry to `RECENT_WORK.md` for P9.21, including scope, non-actions, and verification.

### Task 6: Verification

- [ ] Run `git diff --check`.
- [ ] Run `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`.
- [ ] Confirm no network, no real `.env` read, no The Odds API call, no publish, no deploy.

## Adversarial Review

- Root cause: dry-run commands should be safe to execute in automation and review contexts without touching secret files.
- Scope control: do not introduce a large profile/config abstraction until this smaller boundary is verified.
- Secret risk: tests must monkeypatch env loading rather than creating or reading a real `.env`.
- Live behavior risk: live paths must continue to choose key slots, read quota ledger, and publish when explicitly requested.
- Verification risk: passing focused tests is insufficient; run full project tests because scheduled publish and daily eval are shared operational paths.
