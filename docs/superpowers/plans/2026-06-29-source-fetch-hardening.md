# Source Fetch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden The Odds API source fetch behavior so live refresh failures are classified, bounded, and safe to expose in local diagnostics.

**Architecture:** Keep the existing callable transport injection and cache/quota contracts. Add a small `SourceFetchError` and shared JSON fetch helper in `worldcup.sources.theoddsapi`, then reuse it from odds and scores fetchers while letting callers decide whether to fall back to cache or return a safe blocked/error status.

**Tech Stack:** Python standard library, existing local test runner, no network in tests.

---

### Task 1: Source Error Contract

**Files:**
- Modify: `worldcup/sources/theoddsapi.py`
- Test: `tests/sources/test_theoddsapi_source.py`

- [ ] Add red tests for transient retry, credential non-retry, invalid JSON, and safe redacted error strings.
- [ ] Implement `SourceFetchError` with `reason`, `status`, `retryable`, `attempts`, and `sanitized_url`.
- [ ] Add `sanitize_url_for_log()` that redacts `apiKey` query values.

### Task 2: Shared Fetch Helper

**Files:**
- Modify: `worldcup/sources/theoddsapi.py`
- Modify: `worldcup/sources/theoddsapi_scores.py`
- Test: `tests/sources/test_theoddsapi_scores_source.py`

- [ ] Move odds fetch body/status/json handling into a shared helper.
- [ ] Retry only retryable transport failures and 5xx HTTP responses up to a bounded `max_attempts`.
- [ ] Do not retry credential errors, quota/rate-limit errors, 4xx non-auth errors, or JSON parse errors.
- [ ] Only write cache and quota after a valid JSON response.

### Task 3: Caller-Safe Diagnostics

**Files:**
- Modify: `worldcup/refresh_runner.py`
- Modify: `worldcup/league_odds_refresh.py`
- Modify: `worldcup/scores_capture.py`
- Test: `tests/test_refresh_runner.py`
- Test: `tests/test_league_odds_refresh.py`
- Test: `tests/test_scores_capture.py`

- [ ] Add structured `reason`, `retryable`, `attempts`, and `status` to The Odds API `source_errors` when fallback cache is used.
- [ ] Return safe `status=error` diagnostics from league odds refresh instead of raising source fetch exceptions.
- [ ] Return safe `status=error` diagnostics from scores capture instead of writing partial results.

### Task 4: Documentation And Verification

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] Document the source fetch hardening behavior at a high level.
- [ ] Run red tests before implementation and record failure count.
- [ ] Run `git diff --check`.
- [ ] Run `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`.

## Adversarial Review

- Retrying must be bounded and must not apply to credential or quota failures, otherwise free quota can be wasted.
- Error strings must not include `apiKey`, request URLs with secrets, raw response bodies, `.env` values, or bookmaker/odds payloads.
- Cache fallback remains a refresh-runner responsibility; source functions should raise classified errors and avoid silent stale-cache behavior.
- This phase must not change model probabilities, signal grading, CSL `club_rating_pending`, scheduler cadence, live launch agents, deployment state, or public research boundaries.
