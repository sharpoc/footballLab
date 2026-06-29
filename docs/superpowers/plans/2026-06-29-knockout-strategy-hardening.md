# Knockout Strategy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Tighten official knockout-stage research signals by downgrading fragile 1X2 draw/long-odds signals, preserving lineup-aware AH promotion gates, and preventing ambiguous knockout scores from automatic results ingestion.

**Architecture:** Keep the engine and collectors local-first and deterministic. Add confidence guard reasons in `worldcup.pipeline` using existing `Signal` grade caps, and add a small scores-capture guard that blocks The Odds API score writes when knockout events can contain extra-time or penalty-settled results without a 90-minute score contract. Preserve `lineup_shadow` as diagnostics/gating only, not an active probability input.

**Tech Stack:** Python standard library, existing dataclasses, existing `tests/run_tests.py` runner.

---

## File Structure

- Modify `config/settings.yaml`: add conservative 1X2 official-signal thresholds.
- Modify `worldcup/pipeline.py`: cap draw and long-odds 1X2 S/A signals to B with explicit reasons.
- Modify `tests/test_pipeline.py`: add TDD coverage for draw and long-odds 1X2 caps, plus a keep-path for short-priced confirmed signals.
- Modify `worldcup/scores_capture.py`: add an explicit `allow_knockout_scores` opt-in so automatic live score capture blocks after the knockout boundary unless manually confirmed.
- Modify `tests/test_scores_capture.py`: add TDD coverage that live scores block by default for knockout timestamps and still allow an explicit manual opt-in.
- Modify `worldcup/ledger.py`: translate new 1X2 candidate-only reasons in preview risk notes.
- Modify `tests/test_ledger.py`: cover the preview-facing risk note labels for the new reasons.
- Modify `README.md`: document the new local safety behavior.
- Modify `RECENT_WORK.md`: record the local-only implementation and verification results.

## Task 1: 1X2 Official Signal Caps

**Files:**
- Modify: `config/settings.yaml`
- Modify: `worldcup/pipeline.py`
- Test: `tests/test_pipeline.py`

- [x] **Step 1: Write failing tests**

Add tests that:
- A strong `1X2_90min draw` signal is capped to B and includes `x12_draw_candidate_only`.
- A strong `1X2_90min` signal with odds above the configured cap is capped to B and includes `x12_long_odds_candidate_only`.
- A short-priced confirmed home signal remains official S/A.

- [x] **Step 2: Verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_pipeline.py -q
```

Expected: the new tests fail because the new guard reasons do not exist yet.

- [x] **Step 3: Implement minimal guard**

Add `quality.x12_official_odds_max: 2.2` and `quality.x12_draw_official: false`. In `worldcup.pipeline`, add `_x12_candidate_only_reasons()` and include it in `_apply_confidence_guards()`.

- [x] **Step 4: Verify green**

Run the same focused pipeline tests. Expected: all pipeline tests pass.

## Task 2: Knockout Score Capture Guard

**Files:**
- Modify: `worldcup/scores_capture.py`
- Test: `tests/test_scores_capture.py`

- [x] **Step 1: Write failing tests**

Add tests that:
- A live score capture with completed matches on or after `2026-06-28T00:00:00Z` returns `blocked / knockout_score_manual_review_required` by default, does not write `results.csv`, and does not upsert ambiguous scores.
- Passing `allow_knockout_scores=True` permits the existing live upsert path for explicitly reviewed data.

- [x] **Step 2: Verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_scores_capture.py -q
```

Expected: the new tests fail because `allow_knockout_scores` is not implemented.

- [x] **Step 3: Implement minimal guard**

Add a `KNOCKOUT_SCORE_REVIEW_START` constant and `allow_knockout_scores` parameter to `run_scores_capture()`. After parsing fetched scores but before writing rows, block if any result kickoff is at or after the knockout boundary and the opt-in is false.

- [x] **Step 4: Verify green**

Run the same focused scores tests. Expected: all scores-capture tests pass.

## Task 3: Docs And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [x] **Step 1: Update docs**

Document that 1X2 draw and long-odds official S/A are candidate-only by default, and knockout score capture requires manual 90-minute review opt-in.

- [x] **Step 1b: Update preview-facing risk labels**

Add ledger risk-note labels for `x12_draw_candidate_only` and `x12_long_odds_candidate_only` so the static preview explains why those raw S/A signals are candidate-only.

- [x] **Step 2: Full verification**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: the full suite passes and whitespace check is clean.

## Scope Exclusions

- No live refresh.
- No The Odds API calls.
- No deployment.
- No LaunchAgent changes.
- No commit, push, or cloud writes unless separately confirmed.

## Self-Review

- Spec coverage: the plan covers signal filtering, lineup gate preservation, and knockout score safety.
- Placeholder scan: no placeholder implementation remains.
- Type consistency: the plan uses existing `Signal`, `Grade`, and `run_scores_capture()` boundaries.
