# Signal Quality Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative signal-quality guards for Elo/Poisson disagreement and bookmaker odds dispersion without changing the public API, storage, ingest, or live refresh flow.

**Architecture:** Keep the existing engine split: `odds.py` computes market aggregate metadata, `pipeline.py` converts analysis outputs into signal context, `value.py` applies grade caps, and `ledger.py` renders human-readable risk notes. The change only adds downgrade reasons and metadata; raw model probabilities, market probabilities, EV, Edge, and snapshot route contracts remain intact.

**Tech Stack:** Python standard library, existing dataclasses and test runner, project config in `config/settings.yaml`, no new runtime dependency.

---

## Scope Check

This plan implements only the first phase from `docs/superpowers/specs/2026-06-10-signal-quality-backtest-design.md`:

- `model_disagreement`
- `market_dispersion`
- Chinese page reason text
- local verification

The minimal backtest framework is a separate subsystem and should get its own implementation plan after the historical data source is confirmed.

## File Structure

- Modify `config/settings.yaml`: add a `quality` config block for thresholds.
- Modify `worldcup/engine/odds.py`: keep existing aggregate outputs and add dispersion metadata.
- Modify `worldcup/engine/value.py`: apply downgrade caps when quality context is triggered.
- Modify `worldcup/pipeline.py`: compute quality context from Elo/Poisson probabilities and odds aggregate metadata.
- Modify `worldcup/ledger.py`: map new signal reasons to Chinese risk notes.
- Modify `tests/engine/test_odds.py`: cover dispersion metadata after outlier filtering.
- Modify `tests/engine/test_value.py`: cover grade caps for new quality reasons.
- Modify `tests/test_pipeline.py`: cover end-to-end signal context generation.
- Modify `tests/test_ledger.py`: cover public-facing reason text.
- Modify `RECENT_WORK.md`: record implementation outcome after code and tests are done.

Use this runner unless `pytest` is intentionally installed:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

---

### Task 1: Add Odds Dispersion Metadata

**Files:**
- Modify: `tests/engine/test_odds.py`
- Modify: `worldcup/engine/odds.py`

- [ ] **Step 1: Write failing odds aggregation tests**

Add these tests to `tests/engine/test_odds.py` after `test_aggregate_same_line_average`:

```python
def test_aggregate_reports_dispersion_after_outlier_filtering():
    quotes = [
        OddsQuote("bk1", MarketType.X12, "home", 1.8),
        OddsQuote("bk2", MarketType.X12, "home", 2.0),
        OddsQuote("bk3", MarketType.X12, "home", 2.2),
        OddsQuote("bk4", MarketType.X12, "home", 10.0),
    ]

    out = aggregate(quotes, MarketType.X12, "home", ratio=2.0)

    assert out["n_books"] == 3
    assert math.isclose(out["odds"], 2.0)
    assert out["min_odds"] == 1.8
    assert out["max_odds"] == 2.2
    assert math.isclose(out["dispersion_ratio"], 2.2 / 1.8)


def test_aggregate_without_quotes_reports_empty_dispersion():
    out = aggregate([], MarketType.X12, "home")

    assert out == {
        "odds": None,
        "n_books": 0,
        "min_odds": None,
        "max_odds": None,
        "dispersion_ratio": None,
    }
```

Extend `test_aggregate_market_devigs_all_selections_same_line` with these assertions:

```python
    assert set(out["dispersion_by_selection"]) == {"over", "under"}
    assert math.isclose(out["dispersion_by_selection"]["over"], 2.1 / 1.9)
    assert math.isclose(out["dispersion_by_selection"]["under"], 2.0 / 1.8)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL entries from `tests/engine/test_odds.py` showing missing keys such as `min_odds`, `max_odds`, `dispersion_ratio`, or `dispersion_by_selection`.

- [ ] **Step 3: Implement minimal dispersion metadata**

Replace `aggregate` and the loop inside `aggregate_market` in `worldcup/engine/odds.py` with this version:

```python
def aggregate(
    quotes: list[OddsQuote],
    market_type: MarketType,
    selection: str,
    line: float | None = None,
    ratio: float = 2.0,
) -> dict:
    matched = [
        q.odds
        for q in quotes
        if q.market_type == market_type and q.selection == selection and q.line == line
    ]
    cleaned = filter_outliers(matched, ratio)
    n = len(cleaned)
    if not n:
        return {
            "odds": None,
            "n_books": 0,
            "min_odds": None,
            "max_odds": None,
            "dispersion_ratio": None,
        }
    min_odds = min(cleaned)
    max_odds = max(cleaned)
    return {
        "odds": sum(cleaned) / n,
        "n_books": n,
        "min_odds": min_odds,
        "max_odds": max_odds,
        "dispersion_ratio": max_odds / min_odds,
    }
```

Then update `aggregate_market` so it preserves the new metadata:

```python
    odds: dict[str, float] = {}
    n_books: dict[str, int] = {}
    dispersion_by_selection: dict[str, float] = {}
```

Inside the `for selection in selections:` loop, use:

```python
        agg = aggregate(quotes, market_type, selection, line=line, ratio=ratio)
        n_books[selection] = agg["n_books"]
        if agg["dispersion_ratio"] is not None:
            dispersion_by_selection[selection] = agg["dispersion_ratio"]
        if agg["odds"] is not None:
            odds[selection] = agg["odds"]
```

Include the new map in `base`:

```python
    base = {
        "odds": odds,
        "market_probs": {},
        "n_books_by_selection": n_books,
        "dispersion_by_selection": dispersion_by_selection,
        "last_update_at": last_update_at,
    }
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all existing tests plus the new odds tests pass.

- [ ] **Step 5: Check diff**

Run:

```bash
git diff -- tests/engine/test_odds.py worldcup/engine/odds.py
```

Expected: only odds aggregation metadata and tests changed; no formatter churn in unrelated files.

---

### Task 2: Apply Quality Caps In Value Grading

**Files:**
- Modify: `tests/engine/test_value.py`
- Modify: `worldcup/engine/value.py`

- [ ] **Step 1: Write failing grade cap tests**

Extend `CFG` in `tests/engine/test_value.py`:

```python
    "odds_dispersion_ratio_max": 1.18,
```

Add these tests after `test_few_books_caps_at_b`:

```python
def test_model_disagreement_caps_1x2_at_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(model_disagreement=True),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "model_disagreement" in signal.reasons


def test_model_disagreement_does_not_apply_to_ah():
    signal = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.0,
        None,
        1.85,
        ok_ctx(model_disagreement=True),
        CFG,
        ah_ev=0.09,
    )

    assert signal.grade == Grade.S
    assert "model_disagreement" not in signal.reasons


def test_market_dispersion_caps_at_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_market_dispersion_caps_ah_at_b():
    signal = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.0,
        None,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
        ah_ev=0.09,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_market_dispersion_threshold_is_not_triggered_at_limit():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.18),
        CFG,
    )

    assert signal.grade == Grade.S
    assert "market_dispersion" not in signal.reasons
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL entries from `tests/engine/test_value.py` because the new reasons are not applied yet.

- [ ] **Step 3: Implement grade caps**

In `worldcup/engine/value.py`, add this helper below `_should_cap_longshot`:

```python
def _should_cap_dispersion(ctx: dict, cfg: dict) -> bool:
    threshold = cfg.get("odds_dispersion_ratio_max")
    ratio = ctx.get("odds_dispersion_ratio")
    return threshold is not None and ratio is not None and ratio > threshold
```

Then add these blocks in `grade_signal` after the existing `few_books` block and before `longshot_uncertainty`:

```python
    if market_type == MarketType.X12 and ctx.get("model_disagreement"):
        base = _cap_at_b(base)
        reasons.append("model_disagreement")
    if _should_cap_dispersion(ctx, cfg):
        base = _cap_at_b(base)
        reasons.append("market_dispersion")
```

Keep `model_disagreement` limited to `MarketType.X12`. Allow `market_dispersion` for all market types, including AH.

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all value tests pass.

- [ ] **Step 5: Check diff**

Run:

```bash
git diff -- tests/engine/test_value.py worldcup/engine/value.py
```

Expected: only new context-driven caps and tests changed.

---

### Task 3: Wire Quality Context Through Pipeline

**Files:**
- Modify: `config/settings.yaml`
- Modify: `tests/test_pipeline.py`
- Modify: `worldcup/pipeline.py`

- [ ] **Step 1: Write failing pipeline tests**

Update the imports at the top of `tests/test_pipeline.py`:

```python
from worldcup.models import Grade, MarketType, OddsQuote
```

Add this test after `test_generate_value_signals_outputs_1x2_ou_and_ah`:

```python
def test_generate_value_signals_caps_1x2_when_models_disagree():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis.elo_1x2 = {"home": 0.70, "draw": 0.18, "away": 0.12}
    analysis.poisson_1x2 = {"home": 0.44, "draw": 0.30, "away": 0.26}
    analysis.combined_1x2 = {"home": 0.62, "draw": 0.22, "away": 0.16}
    analysis.market_1x2["n_books_by_selection"]["home"] = 3

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "model_disagreement" in home_1x2.reasons
```

Add this test after it:

```python
def test_generate_value_signals_caps_when_market_dispersion_is_high():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis.combined_1x2 = {"home": 0.62, "draw": 0.22, "away": 0.16}
    analysis.elo_1x2 = {"home": 0.62, "draw": 0.22, "away": 0.16}
    analysis.poisson_1x2 = {"home": 0.61, "draw": 0.23, "away": 0.16}
    analysis.market_1x2["n_books_by_selection"]["home"] = 3
    analysis.market_1x2["dispersion_by_selection"] = {"home": 1.25}

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "market_dispersion" in home_1x2.reasons
    assert "model_disagreement" not in home_1x2.reasons
```

Add this AH-specific test after it:

```python
def test_generate_value_signals_passes_market_dispersion_to_ah():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis.match_input.quotes.extend(
        [
            OddsQuote("bk2", MarketType.AH, "home", 2.2, line=-0.5),
            OddsQuote("bk3", MarketType.AH, "home", 2.4, line=-0.5),
        ]
    )

    signals = generate_value_signals(analysis, cfg)
    ah_home = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "home_-0.5"
    )

    assert ah_home.grade == Grade.B
    assert "market_dispersion" in ah_home.reasons
    assert "model_disagreement" not in ah_home.reasons
```

- [ ] **Step 2: Add config and run tests to verify failure**

Add this block to `config/settings.yaml` after the `value` block:

```yaml
quality:
  disagreement_prob_delta: 0.12
  odds_dispersion_ratio_max: 1.18
```

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: the new pipeline tests fail because `_signal_ctx` does not yet include `model_disagreement` or `odds_dispersion_ratio`, and `_value_cfg` does not pass quality thresholds.

- [ ] **Step 3: Pass quality thresholds to value grading**

Replace `_value_cfg` in `worldcup/pipeline.py` with:

```python
def _value_cfg(cfg: dict) -> dict:
    value_cfg = dict(cfg["value"])
    value_cfg["min_books"] = cfg["odds"]["min_books"]
    quality_cfg = cfg.get("quality", {})
    if "odds_dispersion_ratio_max" in quality_cfg:
        value_cfg["odds_dispersion_ratio_max"] = quality_cfg["odds_dispersion_ratio_max"]
    return value_cfg
```

Do not pass `disagreement_prob_delta` into `value.py`; that threshold is used in `pipeline.py` while comparing Elo and Poisson probabilities.

- [ ] **Step 4: Add model disagreement helper**

Add these helpers above `_signal_ctx` in `worldcup/pipeline.py`:

```python
def _top_probability_key(probs: dict[str, float]) -> str | None:
    if not probs:
        return None
    return max(probs, key=probs.get)


def _model_disagreement(
    analysis: MatchAnalysis,
    selection: str,
    cfg: dict,
) -> bool:
    threshold = (cfg.get("quality") or {}).get("disagreement_prob_delta")
    if threshold is None:
        return False
    elo_probs = analysis.elo_1x2
    poisson_probs = analysis.poisson_1x2
    if _top_probability_key(elo_probs) != _top_probability_key(poisson_probs):
        return True
    if selection not in elo_probs or selection not in poisson_probs:
        return False
    return abs(elo_probs[selection] - poisson_probs[selection]) >= threshold
```

- [ ] **Step 5: Extend signal context**

Change `_signal_ctx` signature in `worldcup/pipeline.py` to:

```python
def _signal_ctx(
    match_input: MatchAnalysisInput,
    market_type: MarketType,
    selection: str,
    line: float | None,
    n_books: int,
    observed_at: datetime | None,
    depends_on_backup: bool,
    model_disagreement: bool = False,
    odds_dispersion_ratio: float | None = None,
) -> dict:
```

Inside `_signal_ctx`, after the existing `ctx` literal and before `return ctx`, add:

```python
    if model_disagreement:
        ctx["model_disagreement"] = True
    if odds_dispersion_ratio is not None:
        ctx["odds_dispersion_ratio"] = odds_dispersion_ratio
```

Keep the existing `odds_age_seconds` logic unchanged.

- [ ] **Step 6: Pass quality context for 1X2 and O/U signals**

In `_integer_market_signals`, define the dispersion map after `n_books`:

```python
    dispersion_by_selection = market.get("dispersion_by_selection", {})
```

In the `_signal_ctx(...)` call, add:

```python
                    model_disagreement=(
                        market_type == MarketType.X12
                        and _model_disagreement(analysis, selection, cfg)
                    ),
                    odds_dispersion_ratio=dispersion_by_selection.get(selection),
```

The full `_signal_ctx` call should still pass the existing `analysis.match_input`, market identifiers, `n_books`, `observed_at`, and `depends_on_backup`.

- [ ] **Step 7: Pass dispersion context for AH signals**

In `_ah_signals`, add `odds_dispersion_ratio=home_agg["dispersion_ratio"]` to the home `_signal_ctx(...)` call:

```python
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "home",
                    home_line,
                    home_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                    odds_dispersion_ratio=home_agg["dispersion_ratio"],
                ),
```

Add the same for away:

```python
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "away",
                    away_line,
                    away_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                    odds_dispersion_ratio=away_agg["dispersion_ratio"],
                ),
```

- [ ] **Step 8: Run tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all pipeline tests pass, including new context-driven downgrade tests.

- [ ] **Step 9: Check diff**

Run:

```bash
git diff -- config/settings.yaml tests/test_pipeline.py worldcup/pipeline.py
```

Expected: config adds one `quality` block; pipeline adds focused helper/context wiring; tests cover `model_disagreement` and `market_dispersion`.

---

### Task 4: Add Public Risk Note Text

**Files:**
- Modify: `tests/test_ledger.py`
- Modify: `worldcup/ledger.py`

- [ ] **Step 1: Write failing ledger test**

Add this test after `test_project_signal_rows_includes_detail_items` if that test exists; otherwise add it after the first `project_signal_rows` test:

```python
def test_project_signal_rows_explains_quality_guard_reasons():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["data_quality"] = {
        "missing_odds": [],
        "missing_elo": [],
        "time_mismatches": [],
        "stale_sources": [],
        "source_errors": [],
    }
    snapshot["matches"][0]["signals"][0]["reasons"] = [
        "model_disagreement",
        "market_dispersion",
    ]

    rows = project_signal_rows(snapshot)
    risk_item = next(
        item for item in rows[0]["detail_items"] if item["label"] == "风险提示"
    )

    assert "Elo 与 Poisson 模型分歧" in risk_item["value"]
    assert "多家赔率报价分歧较大" in risk_item["value"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: the new ledger test fails because `_signal_risk_note` does not yet map the two new reasons.

- [ ] **Step 3: Add reason labels**

In `worldcup/ledger.py`, extend `reason_labels` inside `_signal_risk_note`:

```python
        "model_disagreement": "Elo 与 Poisson 模型分歧，等级已压低",
        "market_dispersion": "多家赔率报价分歧较大，等级已压低",
```

Keep existing labels unchanged.

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all ledger tests pass.

- [ ] **Step 5: Check diff**

Run:

```bash
git diff -- tests/test_ledger.py worldcup/ledger.py
```

Expected: only new reason text coverage and mapping changed.

---

### Task 5: Final Verification And Recent Work

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run full local test suite**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. The exact count may increase from the current `177/177 tests passed` because this plan adds tests.

- [ ] **Step 2: Run whitespace diff check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git status --short
```

Expected changed paths:

```text
 M RECENT_WORK.md
 M config/settings.yaml
 M tests/engine/test_odds.py
 M tests/engine/test_value.py
 M tests/test_ledger.py
 M tests/test_pipeline.py
 M worldcup/engine/odds.py
 M worldcup/engine/value.py
 M worldcup/ledger.py
 M worldcup/pipeline.py
```

If this implementation plan file is still uncommitted in the same worktree, it will also appear under `docs/superpowers/plans/`.

- [ ] **Step 4: Update recent work**

Add a new entry near the top of `RECENT_WORK.md`:

```markdown
## 2026-06-10 信号质量防抖第一阶段实现

- 新增 `model_disagreement` 降级：Elo 与 Poisson 在 1X2 上明显分歧时，S/A 信号最高压到 B，并记录原因。
- 新增 `market_dispersion` 降级：同一盘口下多家 bookmaker 报价离散过大时，S/A 信号最高压到 B，并记录原因。
- 赔率聚合保留原有均值、去水市场概率和报价数量，同时补充离散度摘要；现有 API/ingest/store 契约不变。
- 页面详情风险提示新增模型分歧和市场报价分歧中文解释。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过，`git diff --check` 通过。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未部署、未 push。
```

Replace the test count sentence only after the command has actually been run.

- [ ] **Step 5: Review public-safety boundaries**

Run:

```bash
rg -n "下注金额|凯利|仓位|重注|追损|喊单" worldcup tests config RECENT_WORK.md
```

Expected: no new user-facing recommendation or funding language. Existing disclaimer-related text is acceptable when it says not to provide such advice.

- [ ] **Step 6: Final status summary**

Prepare a concise summary with:

- implemented quality guards
- tests run and result
- confirmation that no live refresh, API call, deploy, push, or online write happened
- remaining next step: separate backtest framework plan after historical data source is confirmed

Do not claim deployment or live data refresh unless those actions were explicitly approved and completed.

---

## Commit Guidance

If the user has explicitly approved local commits for the implementation run, use one focused commit after Task 5:

```bash
git add config/settings.yaml \
  worldcup/engine/odds.py \
  worldcup/engine/value.py \
  worldcup/pipeline.py \
  worldcup/ledger.py \
  tests/engine/test_odds.py \
  tests/engine/test_value.py \
  tests/test_pipeline.py \
  tests/test_ledger.py \
  RECENT_WORK.md
git commit -m "feat: add signal quality guards"
```

Do not push, deploy, trigger live refresh, or write online snapshot data without separate explicit confirmation.
