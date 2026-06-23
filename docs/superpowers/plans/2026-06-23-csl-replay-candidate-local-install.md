# CSL Replay Candidate Local Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely install the validated P9.4 CSL replay candidate into ignored local cache and prove the existing club-rating and league-runner paths can read it without lifting `club_rating_pending`.

**Architecture:** Treat `data/local/diagnostics/csl_results_replay_candidate.csv` as a vetted local input and copy it to the existing ignored cache contract `data/cache/club_results_csl_2026.csv` only after preflight checks pass. Use the current `worldcup.club_rating` loader and current `worldcup.league_runner` dry-run path for verification. Preserve `csl_2026.rating_policy="club_rating_pending"` and keep S/A signal caps active.

**Tech Stack:** Python standard library, existing `worldcup.club_rating`, existing `worldcup.league_runner`, ignored local `data/cache/` and `data/local/diagnostics/`, current `tests/run_tests.py`, no new dependency, no network calls.

---

## Scope And Safety

This plan follows P9.4 full local sample validation and P9.5 alias expansion. It does not fetch more CSL data. It only installs the already generated ignored replay candidate into the local ignored cache location expected by `load_club_rating_pool()`.

Do not modify business code unless a verification failure reveals a real defect and the user confirms a separate implementation change.

Do not modify:

- `worldcup/club_rating.py`
- `worldcup/league_runner.py`
- `worldcup/competitions.py`
- `worldcup/collectors/`
- tests
- scheduler, publish, ingest, ECS, LaunchAgent, `.env`, secret helpers, or quota files

Do not call The Odds API, consume quota, read `.env`, print secrets, deploy, publish, write ECS, update LaunchAgent, install dependencies, push, or lift `club_rating_pending`.

The local cache install is a local state change in an ignored path. Execute it only after the user explicitly confirms this plan.

This work supports research diagnostics only. It is not betting advice, does not output stake sizes, and does not recommend chasing, heavy positions, parlays, or execution actions.

## File Structure

Create or modify these tracked files:

- Create: `docs/superpowers/plans/2026-06-23-csl-replay-candidate-local-install.md`
- Modify: `RECENT_WORK.md` after verification

Create or modify these ignored local files during execution:

- Create: `data/cache/club_results_csl_2026.csv`
- Create: `data/local/diagnostics/csl_club_rating_install_check.json`
- Optionally create: `data/cache/league_analysis_snapshot.json` if a local CSL odds cache already exists

Do not overwrite an existing `data/cache/club_results_csl_2026.csv` without stopping to compare and asking the user how to proceed.

## Task 1: Preflight Candidate Integrity

**Files:**
- Read: `data/local/diagnostics/csl_results_replay_candidate.csv`
- Read: `data/cache/club_results_csl_2026.csv` if present

- [ ] **Step 1: Confirm worktree and local inputs**

Run:

```bash
git status --short
```

Expected: no unexpected tracked source-code changes. Existing plan or `RECENT_WORK.md` documentation changes may be present and must not be reverted.

Run:

```bash
test -f data/local/diagnostics/csl_results_replay_candidate.csv
```

Expected: exit code `0`.

Run:

```bash
test ! -f data/cache/club_results_csl_2026.csv
```

Expected: exit code `0`. If this fails, stop before copying. Run a comparison against the candidate and ask the user whether to keep, replace, or back up the existing ignored cache.

- [ ] **Step 2: Verify candidate shape and counts**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import csv
from collections import Counter
from pathlib import Path

path = Path("data/local/diagnostics/csl_results_replay_candidate.csv")
expected = [
    "competition_id",
    "season",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
]
with path.open(newline="", encoding="utf-8") as fh:
    reader = csv.DictReader(fh)
    fieldnames = reader.fieldnames or []
    rows = list(reader)

keys = [
    (
        row["competition_id"],
        row["season"],
        row["date"],
        row["home_team"],
        row["away_team"],
    )
    for row in rows
]
season_counts = Counter(row["season"] for row in rows)
neutral_count = sum(1 for row in rows if row["neutral"] == "1")
duplicate_count = len(keys) - len(set(keys))

print(
    {
        "fieldnames": fieldnames,
        "rows": len(rows),
        "season_counts": dict(sorted(season_counts.items())),
        "neutral_count": neutral_count,
        "duplicate_count": duplicate_count,
    }
)
assert fieldnames == expected
assert len(rows) == 840
assert dict(sorted(season_counts.items())) == {
    "2023": 240,
    "2024": 240,
    "2025": 240,
    "2026": 120,
}
assert neutral_count == 12
assert duplicate_count == 0
PY
```

Expected: prints the exact counts above and exits `0`.

- [ ] **Step 3: Smoke replay directly from the candidate**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path

from worldcup.club_rating import load_club_results_csv, replay_club_ratings

candidate = Path("data/local/diagnostics/csl_results_replay_candidate.csv")
results = load_club_results_csv(candidate, "csl_2026")
pool = replay_club_ratings(results, "csl_2026")

print(
    {
        "parsed_results": len(results),
        "matches_replayed": pool.matches_replayed,
        "teams_rated": len(pool.ratings),
    }
)
assert len(results) == 840
assert pool.matches_replayed == 840
assert len(pool.ratings) >= 16
PY
```

Expected: candidate is accepted by existing club-rating replay and rates at least the active 16-club league footprint.

## Task 2: Install The Local Ignored Cache

**Files:**
- Create: `data/cache/club_results_csl_2026.csv`

- [ ] **Step 1: Copy the candidate into the expected cache contract**

Run:

```bash
mkdir -p data/cache
```

Run:

```bash
cp data/local/diagnostics/csl_results_replay_candidate.csv data/cache/club_results_csl_2026.csv
```

Run:

```bash
cmp -s data/local/diagnostics/csl_results_replay_candidate.csv data/cache/club_results_csl_2026.csv
```

Expected: `cmp` exits `0`.

- [ ] **Step 2: Verify ignored cache is not tracked**

Run:

```bash
git status --short --ignored
```

Expected: `data/cache/club_results_csl_2026.csv` appears only under ignored output if shown, not as a tracked change.

## Task 3: Validate Club Rating Load

**Files:**
- Read: `data/cache/club_results_csl_2026.csv`
- Create: `data/local/diagnostics/csl_club_rating_install_check.json`

- [ ] **Step 1: Load through the production cache contract**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

from worldcup.club_rating import load_club_rating_pool

result = load_club_rating_pool("data/cache", "csl_2026", min_matches=300)
quality = result.quality.to_dict()
summary = {
    "quality": quality,
    "pool_present": result.pool is not None,
    "rated_teams": sorted(result.pool.ratings) if result.pool is not None else [],
}
out = Path("data/local/diagnostics/csl_club_rating_install_check.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
assert result.pool is not None
assert result.quality.mode == "sample_replay"
assert result.quality.matches_replayed == 840
assert result.quality.skipped_rows == 0
assert result.quality.sample_too_small is False
assert result.quality.errors == ()
assert result.quality.teams_rated >= 16
PY
```

Expected: `quality.mode` is `sample_replay`, `matches_replayed=840`, `skipped_rows=0`, `sample_too_small=false`, and no loader errors.

- [ ] **Step 2: Inspect the diagnostic file**

Run:

```bash
sed -n '1,220p' data/local/diagnostics/csl_club_rating_install_check.json
```

Expected: diagnostic JSON contains only replay quality and rated club keys. It must not include secrets, API keys, odds payloads, `.env` values, or stake fields.

## Task 4: Validate League Runner Dry-Run Without Network

**Files:**
- Read: `data/cache/theoddsapi_csl_2026_odds.json` if present
- Optionally create: `data/cache/league_analysis_snapshot.json`

- [ ] **Step 1: Check for an existing local CSL odds cache**

Run:

```bash
test -f data/cache/theoddsapi_csl_2026_odds.json
```

Expected if exit code is `0`: proceed with the local runner smoke. If exit code is non-zero, skip this task and record `runner_smoke=skipped_missing_local_odds_cache`. Do not call The Odds API to create the odds cache.

- [ ] **Step 2: Run the league runner only when local odds cache exists**

Run only if Step 1 found the local odds cache:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json --club-rating-min-matches 300
```

Expected: command exits `0` and writes an ignored snapshot. It must read only local cache files.

- [ ] **Step 3: Assert pending policy remains active**

Run only if Step 2 produced `data/cache/league_analysis_snapshot.json`:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

snapshot = json.loads(Path("data/cache/league_analysis_snapshot.json").read_text(encoding="utf-8"))
quality = snapshot["data_quality"]["club_rating"]
warnings = set(snapshot["data_quality"].get("warnings", []))
signals = [
    signal
    for match in snapshot.get("matches", [])
    for signal in match.get("signals", [])
]
strong_grades = [
    signal.get("grade")
    for signal in signals
    if signal.get("grade") in {"S", "A"}
]
pending_reasons = [
    signal
    for signal in signals
    if "club_rating_pending" in signal.get("reasons", [])
]

print(
    {
        "club_rating_mode": quality.get("mode"),
        "matches_replayed": quality.get("matches_replayed"),
        "teams_rated": quality.get("teams_rated"),
        "warning_count": len(warnings),
        "has_club_rating_pending": "club_rating_pending" in warnings,
        "signals": len(signals),
        "signals_with_pending_reason": len(pending_reasons),
        "strong_grades": strong_grades,
    }
)
assert quality["mode"] == "sample_replay"
assert quality["matches_replayed"] == 840
assert quality["sample_too_small"] is False
assert "club_rating_pending" in warnings
assert "club_rating_sample_too_small" not in warnings
assert strong_grades == []
assert signals == [] or pending_reasons != []
PY
```

Expected: replayed club ratings are visible to the runner, but `club_rating_pending` remains in warnings and S/A grades remain capped.

## Task 5: Full Verification

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run the full local test suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import runpy
import sys
from pathlib import Path

site = Path("/Users/eagod/Library/Python/3.9/lib/python/site-packages")
if site.exists():
    sys.path.append(str(site))
runpy.run_path("tests/run_tests.py", run_name="__main__")
PY
```

Expected: full suite passes. Record the final passed test count in `RECENT_WORK.md`.

- [ ] **Step 2: Check whitespace and tracked changes**

Run:

```bash
git diff --check
```

Expected: exits `0`.

Run:

```bash
git status --short
```

Expected tracked changes after execution are limited to `RECENT_WORK.md` and this plan document, unless the user separately approved code fixes. Ignored cache and diagnostics must not appear as tracked changes.

- [ ] **Step 3: Update recent work**

Add a top entry to `RECENT_WORK.md` with:

- installed cache path: `data/cache/club_results_csl_2026.csv`
- candidate source: `data/local/diagnostics/csl_results_replay_candidate.csv`
- club-rating quality: mode, matches replayed, teams rated, skipped rows, sample-too-small flag, loader errors
- league-runner dry-run status: executed with local odds cache or skipped because local odds cache was missing
- verification commands and test result
- explicit safety notes: no The Odds API, no `.env`, no quota, no deploy, no LaunchAgent, no push, no `rating_policy` change, `club_rating_pending` still active

Do not trim older `RECENT_WORK.md` entries unless the user separately approves archival or compression.

## Task 6: Final Report And Commit Gate

- [ ] **Step 1: Summarize the execution result**

Report:

- whether `data/cache/club_results_csl_2026.csv` was installed
- exact club-rating quality metrics
- whether league-runner dry-run ran or was skipped
- whether `club_rating_pending` remained active
- verification results
- tracked file diff summary

- [ ] **Step 2: Ask before commit**

Do not commit automatically unless the user confirms. If the user asks to commit after a clean verification, commit only tracked documentation changes and do not stage ignored cache files.

Recommended commit message after user confirmation:

```bash
git commit -m "Plan CSL replay candidate local install"
```

## Adversarial Self-Review

- Installing the candidate is a local ignored state change, not a production data migration. It must be separately confirmed before execution.
- A clean `sample_replay` result proves the current loader can use the sample; it does not prove CSL strong signals are production-ready.
- This plan intentionally does not modify `worldcup/competitions.py`; `club_rating_pending` remains the guardrail until a later plan explicitly lifts it with enough validation.
- The league-runner smoke depends on an existing local odds cache. Missing local odds cache is a skip condition, not a reason to fetch live odds.
- The 2026 CSL season sample is partial by calendar date; replay quality must be reported as a research input, not as a final market edge proof.
- `data/cache/` and `data/local/diagnostics/` are ignored paths. The commit should contain only the plan and `RECENT_WORK.md`, unless the user approves code fixes.
- Source reuse and publication constraints still apply. Do not move raw source extracts or bulk replay files into tracked docs.
