# Probability Families Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `model_raw`, `model_market_total`, and `market_only` probability families as shadow outputs with provenance metadata, without changing model parameters or production signal behavior in the first implementation pass.

**Architecture:** Keep the current snapshot/API contract backward compatible by preserving existing `model.combined_1x2`, `model.ou_2_5`, `market.*`, and `signals`. Add a new `model.probability_families` block that separates model probabilities by provenance and declares which families are allowed for value signals. The first implementation pass is shadow-only: it serializes and tests the three families, while final `signals` continue to use the existing fail-safe-protected path until a later, separately approved activation step.

**Tech Stack:** Python 3.12 standard library, dataclasses, existing `worldcup.pipeline`, `worldcup.local_runner`, `worldcup.query`, `worldcup.ledger`, and `tests/run_tests.py`.

---

## Current Baseline

P0 and P0.5 are already stable:

- `signal-failsafe-v1` baseline is recorded.
- Current code commit for fail-safe: `71c4d68`.
- Current dirty odds quarantine commit: `77540e4`.
- Latest natural scheduled publish observed: `20260616T071316Z-live`.
- Current fail-safe invariants:
  - `same_market_total_anchor=true` OU S/A = `0`
  - `ah_market_validated=false` AH S/A = `0`
- Full local test gate after P0.5: `406/406 tests passed`.

This plan must not change:

- `poisson.mu_total`
- `poisson.mu_market_weight`
- `poisson.mu_dr_slope`
- `poisson.dc_rho`
- Elo K
- host advantage
- ensemble weights
- S/A thresholds
- odds source configuration
- refresh, publish, quota, deployment, or LaunchAgent behavior

Research boundary remains unchanged: this project is for research analysis only and does not constitute betting advice.

## Probability Family Definitions

### `model_raw`

Purpose: model probability family that does not use same-match market probabilities to shape model probabilities.

Rules:

- Uses Elo, Poisson, and current ensemble weights.
- Uses `poisson.prior_mu(dr, cfg["poisson"])` as total-goal prior.
- Does not use OU market probability to infer `mu_total`.
- May use offered market line as the contract being evaluated, for example OU line or AH line, but not the offered market probability as a model input.
- May later be used as the default value-signal probability family, but this plan's first implementation pass only shadows it.

Provenance:

```json
{
  "family": "model_raw",
  "role": "value_candidate",
  "uses_same_match_market_probability": false,
  "uses_market_total_anchor": false,
  "uses_market_line": true,
  "allowed_for_value_signal": true,
  "activation": "shadow_only"
}
```

### `model_market_total`

Purpose: model probability family equivalent to the current production total-goal behavior, where an eligible OU market can inform total goals.

Rules:

- Uses current `mu_market_weight` logic.
- Uses current `min_books` check for OU market total.
- Can be used for explanation, diagnostics, and cross-market calibration.
- Must not generate S/A for same-market OU value when `same_market_total_anchor=true`.
- First pass keeps current final signals unchanged and explicitly labels this family as the legacy active family.

Provenance:

```json
{
  "family": "model_market_total",
  "role": "legacy_active_model",
  "uses_same_match_market_probability": true,
  "uses_market_total_anchor": true,
  "uses_market_line": true,
  "allowed_for_value_signal": true,
  "same_market_value_restrictions": ["no_strong_ou_when_market_total_anchor"]
}
```

### `market_only`

Purpose: devigged market consensus baseline.

Rules:

- Uses only aggregated/de-vigged market probabilities from `market.1x2.market_probs` and `market.ou_2_5.market_probs`.
- AH remains diagnostic until a market-edge/fair-line loop is explicitly designed.
- Does not generate model value signals.
- Used for baseline, disagreement diagnostics, and future backtest comparisons.

Provenance:

```json
{
  "family": "market_only",
  "role": "baseline_diagnostic",
  "uses_same_match_market_probability": true,
  "uses_market_total_anchor": false,
  "uses_market_line": true,
  "allowed_for_value_signal": false
}
```

## Snapshot Schema

Add this block under each match's existing `model` object:

```json
{
  "model": {
    "probability_families": {
      "schema_version": 1,
      "active_signal_family": "model_market_total",
      "recommended_future_signal_family": "model_raw",
      "families": {
        "model_raw": {
          "provenance": {
            "role": "value_candidate",
            "uses_same_match_market_probability": false,
            "uses_market_total_anchor": false,
            "uses_market_line": true,
            "allowed_for_value_signal": true,
            "activation": "shadow_only"
          },
          "mu_total": 2.6,
          "mu_source": "prior",
          "ou_line": 2.5,
          "lambdas": {"home": 1.2, "away": 1.1},
          "poisson_tail": 0.0001,
          "elo_1x2": {"home": 0.4, "draw": 0.28, "away": 0.32},
          "poisson_1x2": {"home": 0.39, "draw": 0.29, "away": 0.32},
          "combined_1x2": {"home": 0.395, "draw": 0.285, "away": 0.32},
          "ou": {"line": 2.5, "probs": {"over": 0.48, "under": 0.52}}
        },
        "model_market_total": {
          "provenance": {
            "role": "legacy_active_model",
            "uses_same_match_market_probability": true,
            "uses_market_total_anchor": true,
            "uses_market_line": true,
            "allowed_for_value_signal": true,
            "same_market_value_restrictions": ["no_strong_ou_when_market_total_anchor"]
          },
          "mu_total": 2.7,
          "mu_prior": 2.6,
          "mu_market": 2.9,
          "mu_market_weight": 0.5,
          "mu_source": "market_informed",
          "same_market_total_anchor": true,
          "ou_line": 2.5,
          "lambdas": {"home": 1.25, "away": 1.15},
          "poisson_tail": 0.0001,
          "elo_1x2": {"home": 0.4, "draw": 0.28, "away": 0.32},
          "poisson_1x2": {"home": 0.41, "draw": 0.27, "away": 0.32},
          "combined_1x2": {"home": 0.405, "draw": 0.275, "away": 0.32},
          "ou": {"line": 2.5, "probs": {"over": 0.52, "under": 0.48}}
        },
        "market_only": {
          "provenance": {
            "role": "baseline_diagnostic",
            "uses_same_match_market_probability": true,
            "uses_market_total_anchor": false,
            "uses_market_line": true,
            "allowed_for_value_signal": false
          },
          "1x2": {"home": 0.42, "draw": 0.27, "away": 0.31},
          "ou": {"line": 2.5, "probs": {"over": 0.51, "under": 0.49}},
          "ah": {
            "line_home": -0.5,
            "status": "diagnostic_only",
            "probs": {}
          }
        }
      }
    }
  }
}
```

Backward compatibility:

- Keep existing `model.mu_total`, `model.mu_prior`, `model.mu_market`, `model.mu_market_weight`, `model.total_mu_source`, `model.same_market_total_anchor`, `model.ou_line`, `model.elo_1x2`, `model.poisson_1x2`, `model.combined_1x2`, and `model.ou_2_5`.
- In Phase 2A, set existing legacy model fields from `model_market_total`, preserving current page/API behavior.
- Consumers that do not know `probability_families` must continue to work unchanged.

## File Structure

Modify only these files in the first implementation pass:

- `worldcup/pipeline.py`
  - Add internal probability-family helpers.
  - Extend `MatchAnalysis` with `probability_families`.
  - Keep legacy analysis fields populated from current behavior.
- `worldcup/local_runner.py`
  - Serialize `model.probability_families`.
  - Keep existing `model.*` legacy fields.
- `worldcup/query.py`
  - Keep public `/api/matches` stable.
  - Add no new public fields unless tests show the current projection strips required diagnostic metadata.
- `worldcup/ledger.py`
  - Keep probability rendering backward compatible.
  - Prefer legacy fields for visible rows in Phase 2A.
- `tests/test_pipeline.py`
  - Add pipeline-level family/provenance tests.
- `tests/test_local_runner.py`
  - Add snapshot serialization/compatibility tests.
- `tests/test_query.py`
  - Add API projection compatibility tests if `probability_families` must remain hidden from `/api/matches`.
- `tests/test_ledger.py`
  - Add frontend-safe fallback tests if ledger helpers are changed.
- `README.md`
  - Add one concise note after implementation.
- `RECENT_WORK.md`
  - Add a recent work entry after implementation.

Do not modify in Phase 2A:

- `config/settings.yaml`
- `worldcup/engine/value.py`
- `worldcup/engine/poisson.py` except if a tiny pure helper is strictly required and covered by tests
- `worldcup/scheduled_publish.py`
- `worldcup/refresh_runner.py`
- deployment scripts or ECS state

## Task 1: Add Probability Family Helpers

**Files:**
- Modify: `worldcup/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing test for model_raw vs model_market_total**

Append this test near existing OU market total tests in `tests/test_pipeline.py`:

```python
def test_analyze_match_input_outputs_raw_and_market_total_probability_families():
    match_input = _priced_match_input(
        h2h_odds={"home": 2.0, "draw": 3.5, "away": 3.8},
        ah_home_line=-0.5,
        ah_odds={"home": 1.9, "away": 1.9},
        ou_odds={"over": 1.55, "under": 2.6},
    )
    base_cfg = load_config()
    cfg = {
        **base_cfg,
        "odds": {**base_cfg["odds"], "min_books": 1},
        "poisson": {**base_cfg["poisson"], "mu_market_weight": 1.0},
    }

    analysis = analyze_match_input(match_input, cfg)
    families = analysis.probability_families["families"]

    assert set(families) == {"model_raw", "model_market_total", "market_only"}
    assert analysis.probability_families["schema_version"] == 1
    assert analysis.probability_families["active_signal_family"] == "model_market_total"
    assert analysis.probability_families["recommended_future_signal_family"] == "model_raw"

    raw = families["model_raw"]
    market_total = families["model_market_total"]
    assert raw["provenance"]["uses_same_match_market_probability"] is False
    assert raw["provenance"]["uses_market_total_anchor"] is False
    assert raw["mu_source"] == "prior"
    assert market_total["provenance"]["uses_same_match_market_probability"] is True
    assert market_total["provenance"]["uses_market_total_anchor"] is True
    assert market_total["same_market_total_anchor"] is True
    assert raw["mu_total"] == analysis.mu_prior_used
    assert market_total["mu_total"] == analysis.mu_total_used
    assert raw["ou"]["line"] == analysis.ou_line
    assert market_total["ou"]["line"] == analysis.ou_line
    assert raw["combined_1x2"] != market_total["combined_1x2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py | rg "test_analyze_match_input_outputs_raw_and_market_total_probability_families|FAIL|tests passed"
```

Expected:

```text
FAIL tests/test_pipeline.py::test_analyze_match_input_outputs_raw_and_market_total_probability_families
```

The failure must be because `MatchAnalysis` has no `probability_families` field or the field lacks the expected family keys.

- [ ] **Step 3: Implement minimal family builder in `worldcup/pipeline.py`**

Add helper functions near `analyze_match_input`:

```python
def _model_probability_block(
    *,
    dr: float,
    cfg: dict,
    mu_total: float,
    ou_line: float,
    elo_1x2: dict[str, float],
    role: str,
    provenance: dict,
    mu_source: str,
    mu_prior: float | None = None,
    mu_market: float | None = None,
    mu_market_weight: float | None = None,
    same_market_total_anchor: bool | None = None,
) -> dict:
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_total)
    matrix, tail = poisson.score_matrix(lh, la, cfg["poisson"])
    poisson_1x2 = poisson.probs_1x2(matrix)
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2,
        poisson_1x2,
        cfg["ensemble"]["w_elo"],
        cfg["ensemble"]["w_poisson"],
    )
    p_over = poisson.prob_over(matrix, ou_line)
    block = {
        "provenance": {"role": role, **provenance},
        "mu_total": mu_total,
        "mu_source": mu_source,
        "ou_line": ou_line,
        "lambdas": {"home": lh, "away": la},
        "poisson_tail": tail,
        "elo_1x2": elo_1x2,
        "poisson_1x2": poisson_1x2,
        "combined_1x2": combined_1x2,
        "ou": {"line": ou_line, "probs": {"over": p_over, "under": 1.0 - p_over}},
    }
    if mu_prior is not None:
        block["mu_prior"] = mu_prior
    if mu_market is not None:
        block["mu_market"] = mu_market
    if mu_market_weight is not None:
        block["mu_market_weight"] = mu_market_weight
    if same_market_total_anchor is not None:
        block["same_market_total_anchor"] = same_market_total_anchor
    return block


def _market_only_probability_block(market_1x2: dict, market_ou: dict, market_ah: dict | None) -> dict:
    return {
        "provenance": {
            "role": "baseline_diagnostic",
            "uses_same_match_market_probability": True,
            "uses_market_total_anchor": False,
            "uses_market_line": True,
            "allowed_for_value_signal": False,
        },
        "1x2": dict(market_1x2.get("market_probs") or {}),
        "ou": {
            "line": market_ou.get("line"),
            "probs": dict(market_ou.get("market_probs") or {}),
        },
        "ah": {
            "line_home": (market_ah or {}).get("line_home"),
            "status": "diagnostic_only",
            "probs": {},
        },
    }
```

Extend `MatchAnalysis`:

```python
    probability_families: dict
```

Inside `analyze_match_input`, compute `market_1x2` and `market_ah_main` once before the return, then construct:

```python
    market_1x2 = odds.aggregate_market(...)
    market_ah_main = _aggregate_ah_main(match_input.quotes, ratio)
    raw_family = _model_probability_block(...)
    market_total_family = _model_probability_block(...)
    probability_families = {
        "schema_version": 1,
        "active_signal_family": "model_market_total",
        "recommended_future_signal_family": "model_raw",
        "families": {
            "model_raw": raw_family,
            "model_market_total": market_total_family,
            "market_only": _market_only_probability_block(market_1x2, market_ou_2_5, market_ah_main),
        },
    }
```

For `model_raw`, pass:

```python
mu_total=mu_prior_used
role="value_candidate"
provenance={
    "uses_same_match_market_probability": False,
    "uses_market_total_anchor": False,
    "uses_market_line": True,
    "allowed_for_value_signal": True,
    "activation": "shadow_only",
}
mu_source="prior"
```

For `model_market_total`, pass:

```python
mu_total=mu_total_used
role="legacy_active_model"
provenance={
    "uses_same_match_market_probability": same_market_total_anchor,
    "uses_market_total_anchor": same_market_total_anchor,
    "uses_market_line": True,
    "allowed_for_value_signal": True,
    "same_market_value_restrictions": ["no_strong_ou_when_market_total_anchor"],
}
mu_source=total_mu_source
mu_prior=mu_prior_used
mu_market=mu_market_used
mu_market_weight=mu_market_weight
same_market_total_anchor=same_market_total_anchor
```

Keep all existing legacy fields unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py | rg "test_analyze_match_input_outputs_raw_and_market_total_probability_families|FAIL|tests passed"
```

Expected:

```text
PASS tests/test_pipeline.py::test_analyze_match_input_outputs_raw_and_market_total_probability_families
```

## Task 2: Serialize Families While Preserving Legacy Snapshot Fields

**Files:**
- Modify: `worldcup/local_runner.py`
- Test: `tests/test_local_runner.py`

- [ ] **Step 1: Write failing snapshot serialization test**

Append this test to `tests/test_local_runner.py`:

```python
def test_build_snapshot_serializes_probability_families_without_removing_legacy_model_fields():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        model = snapshot["matches"][0]["model"]
        assert "combined_1x2" in model
        assert "ou_2_5" in model
        assert "mu_total" in model
        families = model["probability_families"]
        assert families["schema_version"] == 1
        assert families["active_signal_family"] == "model_market_total"
        assert families["recommended_future_signal_family"] == "model_raw"
        assert set(families["families"]) == {"model_raw", "model_market_total", "market_only"}
        assert families["families"]["model_raw"]["provenance"]["activation"] == "shadow_only"
        assert families["families"]["market_only"]["provenance"]["allowed_for_value_signal"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py | rg "test_build_snapshot_serializes_probability_families_without_removing_legacy_model_fields|FAIL|tests passed"
```

Expected: failure because `model["probability_families"]` is missing.

- [ ] **Step 3: Serialize `analysis.probability_families` in `worldcup/local_runner.py`**

In `_analysis_to_dict`, inside the `model` object, add:

```python
"probability_families": analysis.probability_families,
```

Do not remove any existing `model` keys.

- [ ] **Step 4: Run test to verify it passes**

Run the same command from Step 2.

Expected: targeted test passes.

## Task 3: Confirm Signal Generation Remains Behavior-Compatible

**Files:**
- Modify: `tests/test_pipeline.py`
- Production code: no change expected in this task

- [ ] **Step 1: Add no-behavior-change test for final signal grades**

Append this test to `tests/test_pipeline.py`:

```python
def test_probability_families_do_not_change_current_signal_grades_in_shadow_mode():
    match_input = _priced_match_input(
        h2h_odds={"home": 2.0, "draw": 3.5, "away": 3.8},
        ah_home_line=-0.5,
        ah_odds={"home": 1.9, "away": 1.9},
        ou_odds={"over": 1.55, "under": 2.6},
    )
    base_cfg = load_config()
    cfg = {
        **base_cfg,
        "odds": {**base_cfg["odds"], "min_books": 1},
        "poisson": {**base_cfg["poisson"], "mu_market_weight": 1.0},
    }

    analysis = analyze_match_input(match_input, cfg)
    signals = generate_value_signals(analysis, cfg, observed_at="2026-06-08T00:00:00+00:00")
    ou_signals = [signal for signal in signals if signal.market_type == MarketType.OU]

    assert analysis.probability_families["active_signal_family"] == "model_market_total"
    assert ou_signals
    assert all(signal.same_market_total_anchor is True for signal in ou_signals)
    assert all(signal.grade not in (Grade.S, Grade.A) for signal in ou_signals)
    assert all("market_informed_total" in signal.reasons for signal in ou_signals)
```

- [ ] **Step 2: Run test**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py | rg "test_probability_families_do_not_change_current_signal_grades_in_shadow_mode|FAIL|tests passed"
```

Expected: pass after Task 1, because Phase 2A does not switch the active value family.

## Task 4: API and Frontend Compatibility Check

**Files:**
- Test: `tests/test_query.py`
- Test: `tests/test_ledger.py`
- Production code: change only if tests reveal a break

- [ ] **Step 1: Add API projection compatibility test**

Append to `tests/test_query.py`:

```python
def test_project_match_rows_ignores_probability_families_for_public_summary():
    snapshot = {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "refresh_plan": {"next_update_at": "2026-06-09T00:00:00+00:00", "label": "常规"},
                "model": {
                    "probability_families": {
                        "schema_version": 1,
                        "families": {
                            "model_raw": {"combined_1x2": {"home": 0.5}},
                            "model_market_total": {"combined_1x2": {"home": 0.51}},
                            "market_only": {"1x2": {"home": 0.49}},
                        },
                    }
                },
                "signals": [{"grade": "A"}],
            }
        ],
    }

    rows = project_match_rows(snapshot)

    assert rows == [
        {
            "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
            "stage": "Matchday 1",
            "group": "Group A",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "match_label": "Mexico vs South Africa",
            "signal_count": 1,
            "top_grade": "A",
            "next_update_at": "2026-06-09T00:00:00+00:00",
            "next_update_label": "常规",
            "next_update_description": None,
            "stale": False,
        }
    ]
```

- [ ] **Step 2: Add ledger probability fallback test only if needed**

If Task 1 or Task 2 changes `_signal_model_prob`, add this test to `tests/test_ledger.py`:

```python
def test_signal_model_prob_prefers_legacy_fields_when_probability_families_exist():
    match = {
        "model": {
            "combined_1x2": {"home": 0.44},
            "probability_families": {
                "families": {
                    "model_raw": {"combined_1x2": {"home": 0.50}},
                    "model_market_total": {"combined_1x2": {"home": 0.44}},
                }
            },
        }
    }
    signal = {"market_type": "1X2_90min", "selection": "home"}

    assert _signal_model_prob(match, signal) == 0.44
```

If `_signal_model_prob` remains untouched, skip this test and note that no ledger change was required.

- [ ] **Step 3: Run relevant tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py | rg "test_project_match_rows_ignores_probability_families_for_public_summary|test_signal_model_prob_prefers_legacy_fields_when_probability_families_exist|FAIL|tests passed"
```

Expected: API projection test passes. Ledger test is only present if ledger code changed.

## Task 5: Backtest and Old Snapshot Policy

**Files:**
- Create: no new production file
- Modify: `README.md`
- Test: no code test required if no backtest code changes

- [ ] **Step 1: Document snapshot compatibility policy in README**

Add a short paragraph near the existing model/snapshot section:

```markdown
Phase 2 probability-family snapshots may include `model.probability_families` with `model_raw`, `model_market_total`, and `market_only`. Older snapshots without this block remain valid; backtests and API projections must treat missing `probability_families` as `schema_version=0` and continue reading legacy `model.combined_1x2`, `model.ou_2_5`, and `market.*` fields.
```

- [ ] **Step 2: Do not change backtest scoring in Phase 2A**

Backtest behavior remains unchanged in Phase 2A. The implementation report must explicitly say: historical backtests still use legacy fields until a separate backtest-family selection plan is approved.

## Task 6: Full Verification

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run full test gate**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected:

```text
410/410 tests passed
```

The exact count may differ if the implementer adds the optional ledger test. Report the exact count from the command output.

- [ ] **Step 2: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 3: Run current-cache sanity check**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from collections import Counter
from worldcup.local_runner import build_snapshot_from_cache

snapshot = build_snapshot_from_cache("data/cache", snapshot_at="2026-06-16T08:00:00+00:00")
signals = [signal for match in snapshot.get("matches", []) for signal in match.get("signals", [])]
grades = Counter(signal.get("grade") for signal in signals)
missing_families = [
    match.get("source_event_id")
    for match in snapshot.get("matches", [])
    if "probability_families" not in (match.get("model") or {})
]
ou_anchor_strong = sum(
    1
    for signal in signals
    if signal.get("market_type") == "OverUnder_90min"
    and signal.get("same_market_total_anchor") is True
    and signal.get("grade") in {"S", "A"}
)
ah_unvalidated_strong = sum(
    1
    for signal in signals
    if signal.get("market_type") == "AsianHandicap_90min"
    and signal.get("ah_market_validated") is False
    and signal.get("grade") in {"S", "A"}
)
print({
    "matches": snapshot["counts"]["matches"],
    "missing_probability_families": len(missing_families),
    "grades": dict(sorted(grades.items())),
    "ou_anchor_strong": ou_anchor_strong,
    "ah_unvalidated_strong": ah_unvalidated_strong,
})
PY
```

Expected:

```text
missing_probability_families: 0
ou_anchor_strong: 0
ah_unvalidated_strong: 0
```

- [ ] **Step 4: Update `RECENT_WORK.md`**

Add a short entry with:

- `model.probability_families` schema added.
- Phase 2A is shadow-only.
- Existing signal behavior remains unchanged.
- Full test count.
- Current-cache sanity result.
- No model parameter, refresh, publish, quota, ECS, or live data changes.

- [ ] **Step 5: Commit only after tests pass**

Run:

```bash
git add worldcup/pipeline.py worldcup/local_runner.py tests/test_pipeline.py tests/test_local_runner.py tests/test_query.py README.md RECENT_WORK.md
git commit -m "feat: add probability family shadow schema"
```

Do not deploy and do not trigger refresh in this task.

## API / Frontend Display Strategy

Phase 2A:

- No visible page change required.
- `/api/matches` remains compact and safe.
- Full snapshot latest on ECS may contain `probability_families` internally, but public `/api/snapshot/latest` remains intentionally unavailable.
- Ledger details continue to show the same model and market probabilities as before.

Phase 2B, separate confirmation:

- Add a diagnostics-only expanded row showing `model_raw`, `model_market_total`, and `market_only` side-by-side for 1X2 and OU.
- Label these as probability provenance, not as betting guidance.
- Keep `market_only` out of value-signal grading.

## Backtest Strategy

Phase 2A:

- Old snapshots without `probability_families` are treated as legacy schema.
- New snapshots with `probability_families` still score the existing legacy model fields by default.
- No backtest metric headline should mix old and new probability families without explicit label.

Phase 2B, separate confirmation:

- Add backtest family selector:
  - `--prob-family legacy`
  - `--prob-family model_raw`
  - `--prob-family model_market_total`
  - `--prob-family market_only`
- Reports must label `pre-failsafe` / `post-failsafe` and probability family.

## Acceptance Criteria

Phase 2A is complete only if all are true:

- Every match in newly built snapshots has `model.probability_families.schema_version == 1`.
- Families are exactly `model_raw`, `model_market_total`, and `market_only`.
- `model_raw.provenance.uses_same_match_market_probability == false`.
- `model_market_total.provenance.same_market_value_restrictions` includes `no_strong_ou_when_market_total_anchor`.
- `market_only.provenance.allowed_for_value_signal == false`.
- Existing legacy `model.*`, `market.*`, and `signals` fields still exist.
- Current fail-safe invariants remain:
  - same-market OU S/A = `0`
  - unvalidated AH S/A = `0`
- Full test gate passes.
- No refresh, no publish, no quota use, no ECS deployment.

## Out Of Scope

- Enabling `mu_total=2.2`.
- Enabling `mu_dr_slope=0.0015`.
- Enabling `dc_rho=-0.15`.
- Changing S/A thresholds.
- Changing odds age / min books / dispersion thresholds.
- Changing The Odds API source configuration.
- Changing LaunchAgent schedule.
- Switching final signal generation from `model_market_total` to `model_raw`.
- Adding AH market-edge validation.
- Adding public UI comparisons beyond existing legacy probability display.

## Adversarial Self-Review

Data leakage risk:

- The plan separates probability provenance but does not switch final signal behavior in Phase 2A. This avoids silently changing signal distribution while adding observability.
- `model_raw` may still use market line as the evaluated contract. That must be documented as `uses_market_line=true`, distinct from using market probability.

Schema risk:

- Adding a nested `probability_families` block can enlarge snapshot size. The block is compact and only per-match; no raw bookmaker quotes are added.
- Existing consumers must read legacy fields unchanged.

Backtest comparability risk:

- Old snapshots and new snapshots are not automatically comparable by probability family. Reports must label schema/family explicitly before any future family-level backtest.

Operational risk:

- Phase 2A does not call external sources, consume quota, write live snapshots, deploy, or migrate storage.
- A later deployment is required before automatic live snapshots include the new schema; that deployment must be separately confirmed.

Over-conclusion risk:

- This plan does not prove `model_raw` is better. It only creates clean shadow output so future backtests and reviews can compare probability families without circular market leakage.

## Implementation Handoff

Plan complete when this document is saved. Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Do not execute either option until the user explicitly confirms implementation.
