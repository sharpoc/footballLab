# CSL Postmatch Eval Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local-only CSL postmatch evaluation loop that joins archived CSL league snapshots with finished CSL results and reports model-vs-market evidence without lifting `club_rating_pending`.

**Architecture:** Keep the existing World Cup `eval_data` and `backtest` contracts intact. Add a focused CSL adapter that converts local CSL result rows plus pre-kickoff league snapshots into the existing backtest CSV shape, then let `worldcup.backtest` compute Brier, log loss, calibration, and market baselines. Extend `csl_pending_gate` only enough to read an optional market report summary while continuing to return `can_lift_club_rating_pending=false`.

**Tech Stack:** Python standard library, existing `worldcup.backtest`, existing `worldcup.club_rating`, existing unittest-style test runner.

---

## File Structure

- Create `worldcup/csl_eval_data.py`: CSL-specific joiner and CLI for local finished results plus local snapshot history.
- Create `tests/test_csl_eval_data.py`: focused tests for closing snapshot selection, CSV shape, backtest loader compatibility, CLI output, and no-network assumptions.
- Modify `worldcup/csl_pending_gate.py`: optional `market_report` parameter and CLI `--market-report`; no behavior may lift the pending gate.
- Modify `tests/test_csl_pending_gate.py`: tests proving market baseline is reflected when available and still keeps `can_lift_club_rating_pending=false`.
- Modify `README.md`: document the P9.15 local-only usage.
- Modify `RECENT_WORK.md`: record implementation and verification.

## Task 1: CSL Eval Data Joiner

**Files:**
- Create: `tests/test_csl_eval_data.py`
- Create: `worldcup/csl_eval_data.py`

- [ ] **Step 1: Write failing tests**

Add tests that build two in-memory CSL snapshots around kickoff, two CSL result rows, and assert:

```python
from worldcup.csl_eval_data import build_rows, closing_match_entry, write_csv

entry = closing_match_entry(snapshots, "2026-07-03", "yunnan_yukun", "henan")
assert entry["market"]["1x2"]["odds"]["home"] == 2.4

rows, skipped = build_rows(snapshots, results)
assert skipped == 1
assert rows[0]["match_id"] == "csl_2026:2026-07-03:yunnan_yukun:henan"
assert rows[0]["neutral"] == 0
assert rows[0]["odds_home"] == 2.4
```

Also write the CSV and load it with `worldcup.backtest.load_matches` to prove the adapter emits the existing backtest contract.

- [ ] **Step 2: Verify red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_eval_data -v
```

Expected: fails with `ModuleNotFoundError` or missing function from `worldcup.csl_eval_data`.

- [ ] **Step 3: Implement minimal joiner**

Implement:

```python
load_snapshots(history_dir)
closing_match_entry(snapshots, match_date, home_canonical, away_canonical)
build_rows(snapshots, results)
write_csv(rows, path)
main(argv=None)
```

The joiner must use the last snapshot before kickoff/date, must only read local files, and must write columns compatible with `worldcup.backtest.OUTPUT_COLUMNS`.

- [ ] **Step 4: Verify green**

Run the same unittest command. Expected: all `tests.test_csl_eval_data` tests pass.

## Task 2: Pending Gate Market Baseline

**Files:**
- Modify: `tests/test_csl_pending_gate.py`
- Modify: `worldcup/csl_pending_gate.py`

- [ ] **Step 1: Write failing tests**

Add tests that pass a synthetic backtest report:

```python
market_report = {
    "sample": {"n_1x2": 12, "n_matches": 12},
    "markets": {"1x2": {"market": {"n": 12, "brier": 0.51, "log_loss": 0.91}}},
}
report = build_pending_gate_report(results, source="club_results_csl_2026.csv", market_report=market_report)
assert report["sample"]["has_market_odds"] is True
assert report["checks"]["market_baseline_available"] is True
assert report["metrics"]["market_baseline"]["n"] == 12
assert report["can_lift_club_rating_pending"] is False
```

Add a CLI test for `--market-report` that reads a local JSON file and writes the market baseline into the report.

- [ ] **Step 2: Verify red**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_pending_gate -v
```

Expected: fails because `market_report` / `--market-report` is not implemented.

- [ ] **Step 3: Implement minimal pending gate extension**

Add a small extractor for `report["markets"]["1x2"]["market"]`. Treat a baseline as available only when `n > 0`. Keep `decision.can_lift_club_rating_pending=false` and top-level `can_lift_club_rating_pending=false` regardless of metrics.

- [ ] **Step 4: Verify green**

Run the same unittest command. Expected: all pending gate tests pass.

## Task 3: Docs And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Add README usage**

Document:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_eval_data \
  --history data/local/diagnostics/csl_history \
  --results data/cache/club_results_csl_2026.csv \
  --out data/local/backtest/csl_2026_eval.csv

/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest \
  --csv data/local/backtest/csl_2026_eval.csv \
  --min-sample 30 \
  --out data/local/backtest/csl_2026_report.json
```

State that this is local-only, does not fetch data, and does not lift `club_rating_pending`.

- [ ] **Step 2: Add RECENT_WORK note**

Add a top entry with changed modules and verification commands.

- [ ] **Step 3: Run verification**

Run:

```bash
git diff --check
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_eval_data tests.test_csl_pending_gate -v
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: whitespace check passes, focused tests pass, full suite passes.

## Anti-Adversarial Review

- Root cause: P9.14 could observe CSL but could not quantify model-vs-market evidence for finished CSL matches. P9.15 directly adds that evidence path.
- Scope control: Do not add live refresh, LaunchAgent, publishing, deployment, tuning, or `rating_policy` changes.
- Data risk: A report with zero joined closing samples must say `market_baseline_available=false`; it cannot be used to claim accuracy.
- Betting boundary: Outputs remain research-only and must not include stake, bankroll, chase-loss, or execution advice.
- Parameter risk: Do not tune model parameters from small CSL samples. Collect out-of-sample evidence first.
