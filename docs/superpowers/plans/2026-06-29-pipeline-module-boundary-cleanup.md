# Pipeline Module Boundary Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Split `worldcup/pipeline.py` into smaller module boundaries while preserving all public imports, snapshot schema, model probabilities, signal grading, and test behavior.

**Architecture:** Keep `worldcup.pipeline` as the compatibility facade exporting `MatchAnalysisInput`, `BuildMatchInputsResult`, `MatchAnalysis`, `build_match_inputs`, `analyze_match_input`, and `generate_value_signals`. Move pure analysis/shadow helpers into `worldcup.pipeline_analysis` and signal/guard helpers into `worldcup.pipeline_signals`, then import them back through `pipeline.py`.

**Tech Stack:** Python standard library, dataclasses, existing `worldcup.engine` modules, existing local test runner.

---


## Execution Result

- Implemented with `worldcup.pipeline` retaining the public dataclasses and input builder, while `pipeline_analysis.py` and `pipeline_signals.py` hold the moved analysis/signal logic.
- Used type-only annotations and a local `MatchAnalysis` import inside `analyze_match_input` to avoid runtime circular imports while preserving existing `worldcup.pipeline` import paths.
- Added both facade import compatibility and split-module export tests; final verification returned `628/628 tests passed`.

## Scope

- Do not change model math, thresholds, probabilities, signal grades, candidate grade policy, AH validation policy, lineup shadow semantics, OU total shadow semantics, or snapshot schema.
- Do not change callers in `worldcup.local_runner` / `worldcup.league_runner` unless a compatibility import requires it.
- Preserve the private `_ah_validation_shadow` import used by `worldcup.shadow_backfill_diagnostics`.
- No live refresh, no `.env`, no The Odds API, no quota, no publish, no deploy, no LaunchAgent.

## Files

- Create: `worldcup/pipeline_analysis.py`
- Create: `worldcup/pipeline_signals.py`
- Modify: `worldcup/pipeline.py`
- Modify: `worldcup/shadow_backfill_diagnostics.py` only if the compatibility facade becomes too awkward; preferred path is no caller change.
- Modify: `tests/test_pipeline.py`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

## Task 1: Add Import-Compatibility Regression Tests

- [x] Add `test_pipeline_public_facade_keeps_existing_import_contract` to `tests/test_pipeline.py`.

```python
def test_pipeline_public_facade_keeps_existing_import_contract():
    import worldcup.pipeline as pipeline

    assert pipeline.MatchAnalysisInput is MatchAnalysisInput
    assert callable(pipeline.build_match_inputs)
    assert callable(pipeline.analyze_match_input)
    assert callable(pipeline.generate_value_signals)
    assert callable(pipeline._ah_validation_shadow)
```

- [x] Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- [x] Expected before moving code: passes. This is a guard test, not a red test, because the refactor must preserve the current contract.

## Task 2: Extract Analysis And Shadow Helpers

- [x] Create `worldcup/pipeline_analysis.py`.
- [x] Move these definitions from `worldcup/pipeline.py` into `worldcup/pipeline_analysis.py`:
  - `_adjusted_dr`
  - `_elo_home_advantage`
  - `_model_probability_block`
  - `_market_only_probability_block`
  - `_probability_families`
  - `_parse_lineup_datetime`
  - `_latest_quote_observed_at`
  - `_lineup_float`
  - `_clamp`
  - `_rounded_probs`
  - `_edge_shadow`
  - `_edge_delta`
  - `_ou_total_shadow_block`
  - `_lineup_shadow_block`
  - `analyze_match_input`
- [x] Import required dependencies directly in `pipeline_analysis.py`: `math`, `datetime`, `timezone`, `Any`, `ensemble`, `elo`, `handicap`, `odds`, `poisson`, `MarketType`, `OddsQuote`, `MatchAnalysis`, `MatchAnalysisInput`.
- [x] Keep `MatchAnalysis` and `MatchAnalysisInput` dataclasses in `pipeline.py` first to avoid circular churn; import them into `pipeline_analysis.py`.
- [x] In `pipeline.py`, import `analyze_match_input` from `worldcup.pipeline_analysis`.
- [x] Run full tests. Expected: `626/626 tests passed` before adding later refactor tests.

## Task 3: Extract Signal And Guard Helpers

- [x] Create `worldcup/pipeline_signals.py`.
- [x] Move these definitions from `worldcup/pipeline.py` into `worldcup/pipeline_signals.py`:
  - `_value_cfg`
  - `_top_probability_key`
  - `_model_disagreement`
  - `_host_side`
  - `_quality_threshold`
  - `_ah_main_has_min_books`
  - `_ah_main_line_home`
  - `_round_metric`
  - `_quarter_lines`
  - `_model_fair_ah_line`
  - `_ah_validation_shadow`
  - `_ah_main_supports_x12`
  - `_x12_confidence_guard_reasons`
  - `_x12_candidate_only_reasons`
  - `_big_handicap`
  - `_extreme_favorite_side`
  - `_ou_confidence_guard_reasons`
  - `_ah_signal_side`
  - `_ah_confidence_guard_reasons`
  - `_cap_signal_at_b`
  - `_candidate_metadata`
  - `_apply_candidate_grades`
  - `_apply_confidence_guards`
  - `_normalize_observed_at`
  - `_line_matches`
  - `_odds_age_seconds`
  - `_signal_ctx`
  - `_line_label`
  - `_half_goal_line`
  - `_main_ou_line`
  - `_main_home_ah_line`
  - `_aggregate_ah_main`
  - `_invert_dist`
  - `_integer_market_signals`
  - `_ah_signals`
  - `generate_value_signals`
- [x] Import required dependencies directly in `pipeline_signals.py`: `replace`, `datetime`, `timezone`, `handicap`, `odds`, `value`, `Grade`, `MarketType`, `OddsQuote`, `Signal`, `MatchAnalysis`, `MatchAnalysisInput`.
- [x] In `pipeline.py`, import `generate_value_signals`, `_ah_validation_shadow`, `_main_ou_line`, and `_aggregate_ah_main` from `worldcup.pipeline_signals`.
- [x] In `pipeline_analysis.py`, import `_main_ou_line` and `_aggregate_ah_main` from `worldcup.pipeline_signals`.
- [x] Run full tests. Expected: no behavior change.

## Task 4: Keep Facade Small And Review Imports

- [x] Remove unused imports from `worldcup/pipeline.py` after extraction.
- [x] Keep `worldcup.pipeline` focused on dataclasses, fixture/odds/Elo/lineup input matching, and re-exported public functions.
- [x] Run:

```bash
python3 - <<'PY'
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input, generate_value_signals, _ah_validation_shadow
print(MatchAnalysisInput.__name__, callable(analyze_match_input), callable(generate_value_signals), callable(_ah_validation_shadow))
PY
```

- [x] Expected output includes `MatchAnalysisInput True True True`.

## Task 5: Docs And Recent Work

- [x] Update `README.md` directory structure to mention `pipeline_analysis.py` and `pipeline_signals.py`.
- [x] Update `RECENT_WORK.md` with P9.22 implementation, explicitly saying this was a behavior-preserving split.
- [x] Do not claim any model improvement or accuracy change.

## Task 6: Verification

- [x] Run:

```bash
git diff --check
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- [x] Expected final status after implementation: all tests pass, test count increased by 2 from the facade and split-module import regression tests.

## Adversarial Review

- Circular import risk: keep dataclasses in `pipeline.py` and move implementation helpers around them carefully.
- Behavior drift risk: do not rename JSON keys, reason strings, candidate reasons, or shadow schema fields.
- Public/private boundary risk: `_ah_validation_shadow` is private but currently used by diagnostics; keep the facade import until that caller is intentionally migrated.
- Scope creep risk: do not use this split to tune model parameters, change `club_rating_pending`, or alter signal eligibility.
- Verification risk: `tests/test_pipeline.py` is broad but not enough by itself; run the full suite because local runner, league runner, odds trend, diagnostics, and scheduler read pipeline outputs indirectly.
