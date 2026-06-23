# CSL League Runner Synthetic Odds Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the CSL `league_runner` can consume the installed P9.6 local club-rating cache through a fully local synthetic odds smoke, while preserving `club_rating_pending` caps.

**Architecture:** Create a clearly marked synthetic The Odds API-shaped CSL odds cache in ignored `data/cache/`, run the existing `worldcup.league_runner` CLI against local cache only, and inspect the ignored snapshot output. Do not modify runner logic, competition policy, data collectors, scheduler, or real source files.

**Tech Stack:** Python standard library, existing `worldcup.league_runner`, existing `worldcup.club_rating`, ignored `data/cache/`, current `tests/run_tests.py`, no new dependency, no network calls.

---

## Scope And Safety

This plan follows P9.6. The installed local ignored file `data/cache/club_results_csl_2026.csv` already contains the validated 840-match replay candidate.

P9.7 only creates a synthetic local odds cache to exercise the runner input contract. The synthetic odds cache must never be described as real market data, closing odds, live odds, or evidence of a value signal. It is a wiring smoke artifact.

Do not modify:

- `worldcup/league_runner.py`
- `worldcup/club_rating.py`
- `worldcup/competitions.py`
- `worldcup/collectors/`
- tests
- scheduler, publish, ingest, ECS, LaunchAgent, `.env`, secret helpers, or quota files

Do not call The Odds API, consume quota, read `.env`, print secrets, deploy, publish, write ECS, update LaunchAgent, install dependencies, push, or lift `club_rating_pending`.

This work supports research diagnostics only. It is not betting advice, does not output stake sizes, and does not recommend chasing, heavy positions, parlays, or execution actions.

## File Structure

Create or modify these tracked files:

- Create: `docs/superpowers/plans/2026-06-23-csl-league-runner-synthetic-odds-smoke.md`
- Modify: `RECENT_WORK.md` after verification

Create or modify these ignored local files during execution:

- Create or replace: `data/cache/theoddsapi_csl_2026_odds.json`
- Create or replace: `data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json`
- Create: `data/local/diagnostics/csl_league_runner_synthetic_smoke.json`

Do not write synthetic odds under `data/probe/`, docs, tests, or any tracked path.

If an existing `data/cache/theoddsapi_csl_2026_odds.json` is present before execution, stop and inspect it. If it appears to be real cached odds, do not overwrite it. Instead ask the user whether to preserve it and run the smoke from a temporary cache directory.

## Synthetic Fixture Contract

Use two CSL teams that are rated by P9.6 and already accepted by the strict CSL alias gate:

- `Shanghai Port`
- `Shandong Taishan`

Use a future-ish timestamp only to satisfy the runner input contract:

- `commence_time`: `2026-06-24T11:35:00Z`
- `snapshot_at`: `2026-06-23T12:00:00+00:00`

Use three bookmakers to avoid low-book suppression dominating the smoke:

- `smoke_book_1`
- `smoke_book_2`
- `smoke_book_3`

Markets:

- `h2h`
- `totals` at `2.5`
- `spreads` at `Shanghai Port -0.5` / `Shandong Taishan +0.5`

These prices are synthetic wiring inputs:

- H2H home: `2.35`, `2.40`, `2.45`
- H2H draw: `3.40`, `3.35`, `3.30`
- H2H away: `3.20`, `3.15`, `3.10`
- OU over 2.5: `2.35`, `2.30`, `2.25`
- OU under 2.5: `1.62`, `1.65`, `1.68`
- AH home -0.5: `2.20`, `2.18`, `2.16`
- AH away +0.5: `1.72`, `1.74`, `1.76`

## Task 1: Preflight Local State

**Files:**
- Read: `data/cache/club_results_csl_2026.csv`
- Read: `data/cache/theoddsapi_csl_2026_odds.json` if present

- [ ] **Step 1: Confirm branch and tracked worktree**

Run:

```bash
git branch --show-current
git status --short
```

Expected: branch is `main` or a dedicated `codex/` execution branch, and there are no unexpected tracked changes.

- [ ] **Step 2: Confirm P9.6 club-rating cache exists**

Run:

```bash
test -f data/cache/club_results_csl_2026.csv
```

Expected: exit code `0`. If missing, stop and run P9.6 before this plan.

- [ ] **Step 3: Confirm club-rating cache still loads**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from worldcup.club_rating import load_club_rating_pool

result = load_club_rating_pool("data/cache", "csl_2026", min_matches=300)
print(result.quality.to_dict())
assert result.pool is not None
assert result.quality.mode == "sample_replay"
assert result.quality.matches_replayed == 840
assert result.quality.teams_rated == 22
assert result.quality.skipped_rows == 0
assert result.quality.sample_too_small is False
assert result.quality.errors == ()
PY
```

Expected: the installed P9.6 cache still returns `sample_replay`.

- [ ] **Step 4: Protect any existing odds cache**

Run:

```bash
test -f data/cache/theoddsapi_csl_2026_odds.json
printf 'odds_cache_exists=%s\n' "$?"
```

Expected for the normal P9.7 path: `odds_cache_exists=1`, which means the file is absent because `test -f` returned non-zero.

If the output is `odds_cache_exists=0`, inspect the first 80 lines:

```bash
sed -n '1,80p' data/cache/theoddsapi_csl_2026_odds.json
```

If it is not clearly synthetic smoke data, stop and ask the user how to proceed.

## Task 2: Create Synthetic Local Odds Cache

**Files:**
- Create or replace: `data/cache/theoddsapi_csl_2026_odds.json`

- [ ] **Step 1: Write the synthetic odds cache**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

cache_path = Path("data/cache/theoddsapi_csl_2026_odds.json")
cache_path.parent.mkdir(parents=True, exist_ok=True)

home_prices = [2.35, 2.40, 2.45]
draw_prices = [3.40, 3.35, 3.30]
away_prices = [3.20, 3.15, 3.10]
over_prices = [2.35, 2.30, 2.25]
under_prices = [1.62, 1.65, 1.68]
home_spread_prices = [2.20, 2.18, 2.16]
away_spread_prices = [1.72, 1.74, 1.76]

bookmakers = []
for idx in range(3):
    bookmakers.append(
        {
            "key": f"smoke_book_{idx + 1}",
            "title": f"Synthetic Smoke Book {idx + 1}",
            "last_update": f"2026-06-23T11:0{idx}:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": f"2026-06-23T11:1{idx}:00Z",
                    "outcomes": [
                        {"name": "Shanghai Port", "price": home_prices[idx]},
                        {"name": "Draw", "price": draw_prices[idx]},
                        {"name": "Shandong Taishan", "price": away_prices[idx]},
                    ],
                },
                {
                    "key": "totals",
                    "last_update": f"2026-06-23T11:2{idx}:00Z",
                    "outcomes": [
                        {"name": "Over", "price": over_prices[idx], "point": 2.5},
                        {"name": "Under", "price": under_prices[idx], "point": 2.5},
                    ],
                },
                {
                    "key": "spreads",
                    "last_update": f"2026-06-23T11:3{idx}:00Z",
                    "outcomes": [
                        {
                            "name": "Shanghai Port",
                            "price": home_spread_prices[idx],
                            "point": -0.5,
                        },
                        {
                            "name": "Shandong Taishan",
                            "price": away_spread_prices[idx],
                            "point": 0.5,
                        },
                    ],
                },
            ],
        }
    )

payload = [
    {
        "id": "synthetic-csl-smoke-001",
        "sport_key": "soccer_china_superleague",
        "sport_title": "Chinese Super League",
        "commence_time": "2026-06-24T11:35:00Z",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "bookmakers": bookmakers,
        "_synthetic_smoke": True,
        "_synthetic_note": "Local wiring smoke only; not real odds.",
    }
]

cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(cache_path)
PY
```

Expected: writes `data/cache/theoddsapi_csl_2026_odds.json`.

- [ ] **Step 2: Verify synthetic marker and ignored status**

Run:

```bash
rg -n '"_synthetic_smoke": true|"Local wiring smoke only; not real odds."' data/cache/theoddsapi_csl_2026_odds.json
```

Expected: both marker lines are present.

Run:

```bash
git status --short --ignored data/cache/theoddsapi_csl_2026_odds.json
```

Expected: output shows `!! data/cache/` or an ignored entry, not a tracked change.

## Task 3: Run League Runner Smoke

**Files:**
- Read: `data/cache/theoddsapi_csl_2026_odds.json`
- Read: `data/cache/club_results_csl_2026.csv`
- Create or replace: `data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json`

- [ ] **Step 1: Run runner from local cache only**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json --club-rating-min-matches 300 --snapshot-at 2026-06-23T12:00:00+00:00
```

Expected: exits `0` and prints:

```text
wrote data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json with 1 matches
```

- [ ] **Step 2: Inspect smoke snapshot**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

snapshot_path = Path("data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json")
snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
quality = snapshot["data_quality"]["club_rating"]
warnings = set(snapshot["data_quality"].get("warnings", []))
matches = snapshot.get("matches", [])
signals = [signal for match in matches for signal in match.get("signals", [])]
strong_grades = [signal.get("grade") for signal in signals if signal.get("grade") in {"S", "A"}]
pending_reasons = [
    signal
    for signal in signals
    if "club_rating_pending" in signal.get("reasons", [])
]
match = matches[0]
summary = {
    "snapshot_at": snapshot["snapshot_at"],
    "competition_id": snapshot["competition"]["id"],
    "matches": snapshot["counts"]["matches"],
    "counts": snapshot["counts"],
    "fixture_source": snapshot["data_quality"]["fixture_source"],
    "warnings": sorted(warnings),
    "club_alias_unmatched": snapshot["data_quality"]["club_alias_unmatched"],
    "invalid_odds_count": snapshot["data_quality"]["invalid_odds_count"],
    "rating_policy": snapshot["competition"]["rating_policy"],
    "club_rating": quality,
    "home_team": match["home_team"],
    "away_team": match["away_team"],
    "elo": match["elo"],
    "signals": len(signals),
    "strong_grades": strong_grades,
    "signals_with_pending_reason": len(pending_reasons),
}
out = Path("data/local/diagnostics/csl_league_runner_synthetic_smoke.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

assert snapshot["snapshot_at"] == "2026-06-23T12:00:00+00:00"
assert snapshot["competition"]["id"] == "csl_2026"
assert snapshot["competition"]["rating_policy"] == "club_rating_pending"
assert snapshot["counts"]["fixtures"] == 1
assert snapshot["counts"]["odds_events"] == 1
assert snapshot["counts"]["match_inputs"] == 1
assert snapshot["counts"]["matches"] == 1
assert snapshot["data_quality"]["fixture_source"] == "odds_event_only"
assert snapshot["data_quality"]["club_alias_unmatched"] == []
assert snapshot["data_quality"]["invalid_odds_count"] == 0
assert quality["mode"] == "sample_replay"
assert quality["matches_replayed"] == 840
assert quality["teams_rated"] == 22
assert quality["sample_too_small"] is False
assert quality["errors"] == []
assert "club_rating_pending" in warnings
assert "club_rating_sample_too_small" not in warnings
assert "club_rating_missing" not in warnings
assert match["home_team"] == "Shanghai Port"
assert match["away_team"] == "Shandong Taishan"
assert match["elo"]["home"] != 1500
assert match["elo"]["away"] != 1500
assert strong_grades == []
assert signals == [] or pending_reasons != []
PY
```

Expected: the runner uses replayed club ratings, writes one match, keeps `club_rating_pending`, and emits no S/A final grades.

- [ ] **Step 3: Confirm diagnostic output is ignored**

Run:

```bash
git status --short --ignored data/cache/theoddsapi_csl_2026_odds.json data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json data/local/diagnostics/csl_league_runner_synthetic_smoke.json
```

Expected: outputs only ignored paths, not tracked changes.

## Task 4: Verification And Recent Work

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run focused tests**

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

Expected: full suite passes and prints the final passed test count.

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

Expected tracked changes are limited to `RECENT_WORK.md` and this plan document unless the user separately approved code fixes.

- [ ] **Step 3: Update recent work**

Add a top entry to `RECENT_WORK.md` with:

- synthetic odds cache path: `data/cache/theoddsapi_csl_2026_odds.json`
- snapshot path: `data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json`
- diagnostic path: `data/local/diagnostics/csl_league_runner_synthetic_smoke.json`
- synthetic boundary: local wiring smoke only, not real odds
- club-rating quality metrics from the smoke
- `club_rating_pending` cap confirmation
- verification command result
- explicit safety notes: no The Odds API, no `.env`, no quota, no deploy, no LaunchAgent, no push, no `rating_policy` change

Do not trim older `RECENT_WORK.md` entries unless the user separately approves archival or compression.

## Task 5: Final Report And Commit Gate

- [ ] **Step 1: Summarize the execution result**

Report:

- whether synthetic odds cache was created
- whether runner wrote `data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json`
- exact club-rating quality metrics
- whether `club_rating_pending` remained active
- whether S/A final grades stayed absent
- verification results
- tracked file diff summary

- [ ] **Step 2: Ask before commit**

Do not commit automatically unless the user confirms. If the user asks to commit after clean verification, commit only tracked documentation changes and do not stage ignored cache files.

Recommended commit message after user confirmation:

```bash
git commit -m "Plan CSL league runner synthetic smoke"
```

## Adversarial Self-Review

- Synthetic odds prove only local runner wiring. They do not prove real market coverage, price freshness, bookmaker quality, closing accuracy, or model edge.
- The synthetic odds file lives in the same ignored filename that a real local odds cache would use. Preflight must stop before overwriting any existing real cache.
- Strong CSL signals must remain capped by `club_rating_pending`; this plan must not change `worldcup/competitions.py`.
- The smoke uses a single fixture, so it cannot support calibration, backtest, or signal-quality conclusions.
- The synthetic payload includes marker fields, but downstream parser ignores unknown fields. Verification must preserve a separate diagnostic JSON that records the synthetic boundary.
- All generated smoke artifacts must stay under ignored paths and must not be committed.
