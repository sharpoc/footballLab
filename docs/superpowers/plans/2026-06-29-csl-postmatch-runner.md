# CSL Postmatch Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one local-only command that runs CSL postmatch evaluation from archived snapshots through backtest and pending gate reporting.

**Architecture:** Create `worldcup.csl_postmatch_runner` as a small orchestrator that calls existing Python APIs instead of shelling out. It reads local history/results, writes ignored local artifacts, and prints a safe JSON summary.

**Tech Stack:** Python standard library, existing `worldcup.csl_eval_data`, `worldcup.backtest`, `worldcup.csl_pending_gate`, existing `tests/run_tests.py`.

---

### Task 1: Runner API and CLI

**Files:**
- Create: `worldcup/csl_postmatch_runner.py`
- Test: `tests/test_csl_postmatch_runner.py`

- [ ] **Step 1: Write failing tests**

Create tests that build a temporary CSL history snapshot and `club_results_csl_2026.csv`, then call:

```python
from worldcup.csl_postmatch_runner import main, run_postmatch
```

Assert `run_postmatch(...)` writes:

```text
data/local/backtest/csl_2026_eval.csv
data/local/backtest/csl_2026_report.json
data/local/diagnostics/csl_pending_gate_20260704T000000Z.json
```

Assert the summary contains:

```python
{
    "competition_id": "csl_2026",
    "snapshots": 1,
    "results": 1,
    "joined": 1,
    "skipped_no_closing": 0,
    "backtest_sample": {"n_matches": 1},
    "pending_gate": {
        "decision_status": "keep_pending",
        "can_lift_club_rating_pending": False,
    },
}
```

Also assert the printed CLI summary does not contain raw `bookmaker`, `odds`, `api_key`, or `secret`.

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util, inspect
from pathlib import Path
path = Path('tests/test_csl_postmatch_runner.py')
spec = importlib.util.spec_from_file_location('tests.test_csl_postmatch_runner', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for name, fn in inspect.getmembers(module, inspect.isfunction):
    if name.startswith('test_'):
        fn()
PY
```

Expected: import fails with `No module named 'worldcup.csl_postmatch_runner'`.

- [ ] **Step 3: Implement minimal runner**

Create functions:

```python
def run_postmatch(
    *,
    root: str | Path = ".",
    history: str | Path = "data/local/diagnostics/csl_history",
    results: str | Path = "data/cache/club_results_csl_2026.csv",
    eval_out: str | Path = "data/local/backtest/csl_2026_eval.csv",
    report_out: str | Path = "data/local/backtest/csl_2026_report.json",
    gate_out: str | Path | None = None,
    competition_id: str = "csl_2026",
    generated_at: str | None = None,
    min_sample: int = 30,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
) -> dict:
```

Implementation calls:

```python
snapshots = csl_eval_data.load_snapshots(history_path)
results_rows = load_club_results_csv(results_path, competition_id)
rows, skipped = csl_eval_data.build_rows(snapshots, results_rows)
csl_eval_data.write_csv(rows, eval_path)
backtest_report = backtest.run_backtest(
    backtest.load_matches(eval_path),
    load_config(None),
    min_sample=min_sample,
)
pending_report = csl_pending_gate.build_pending_gate_report(
    results_rows,
    source=results_path,
    competition_id=competition_id,
    generated_at=generated_at,
    warmup_matches=warmup_matches,
    min_eval_matches=min_eval_matches,
    market_report=backtest_report,
)
```

Write reports with existing JSON writers and return a safe summary containing counts, output paths, sample metrics, and pending decision only.

- [ ] **Step 4: Run focused tests and verify green**

Run the same focused test command. Expected: all `test_csl_postmatch_runner.py` tests pass.

### Task 2: Documentation and Recent Work

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README**

Replace the three manual postmatch commands in the CSL Postmatch Eval Loop with:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_postmatch_runner \
  --history data/local/diagnostics/csl_history \
  --results data/cache/club_results_csl_2026.csv \
  --eval-out data/local/backtest/csl_2026_eval.csv \
  --report-out data/local/backtest/csl_2026_report.json \
  --min-sample 30 \
  --warmup-matches 300 \
  --min-eval-matches 200
```

Keep the separate `csl_snapshot_archive --dry-run` and archive steps before it.

- [ ] **Step 2: Update RECENT_WORK**

Add a top entry stating P9.17 is local-only, does not network, does not read `.env`, does not call The Odds API, and does not lift `club_rating_pending`.

### Task 3: Verification

**Files:**
- Read-only verification across the repository.

- [ ] **Step 1: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 2: Run project tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 3: Inspect git status**

Run:

```bash
git status --short --branch
```

Expected: only planned code/docs/test/plan files are modified or untracked; ignored `data/local/` artifacts are not staged.

## Self-Review

- Spec coverage: The plan covers the runner command, local-only boundaries, README workflow, recent work entry, and verification.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type consistency: The planned `run_postmatch` signature and summary fields are used consistently across tests and implementation.
