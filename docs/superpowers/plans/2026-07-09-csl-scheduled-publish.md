# CSL Scheduled Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automatic CSL refresh/publish runner that uses T-90 and T-25 match anchors, refreshes the whole CSL competition in one call, throttles duplicate refreshes, and installs cleanly as a local LaunchAgent after explicit confirmation.

**Architecture:** A new `worldcup.csl_scheduled_publish` module owns the decision and publish flow. It reads the latest CSL snapshot, computes competition-level due status from match-level anchors, and only on `--live` reads `.env`, calls The Odds API, rebuilds the CSL prediction snapshot, and HMAC-ingests it. A separate `worldcup.csl_scheduled_launch_agent` module generates the plist so installation can be tested without loading launchd.

**Tech Stack:** Python 3.12 standard library, existing `league_odds_refresh`, `league_runner`, `publish`, `quota`, `.env` loader, LaunchAgent plist generation, current `tests/run_tests.py` style.

---

### Task 1: CSL Scheduled Publish Decision And Runner

**Files:**
- Create: `worldcup/csl_scheduled_publish.py`
- Test: `tests/test_csl_scheduled_publish.py`

- [ ] **Step 1: Add tests for due decisions**

Create tests that build small CSL snapshots and assert:

```python
def test_dry_run_t90_due_without_reading_env_or_fetching():
    # now is T-90 for one future match, last refresh is before the anchor.
    # Expected: status == "due", selected anchor == "T-90", no env/fetch/publish callbacks called.

def test_low_quota_skips_t90_but_allows_t25():
    # remaining=29 means T-90 is ignored, T-25 is still due.

def test_global_throttle_skips_duplicate_refresh_inside_min_interval():
    # last refresh 10 minutes ago, min interval 30 minutes, a match anchor is due.
    # Expected: status == "skipped", reason == "global_throttle".

def test_no_future_matches_uses_discovery_interval():
    # all old matches are post kickoff; if last refresh is older than 24h, due for discovery.
```

- [ ] **Step 2: Implement decision helpers**

Implement:

```python
CSL_ANCHORS = (
    (90 * 60, "T-90", "赛前90分钟"),
    (25 * 60, "T-25", "赛前25分钟"),
)
LOW_QUOTA_ANCHORS = ("T-25",)
DEFAULT_MIN_INTERVAL_SECONDS = 30 * 60
DEFAULT_DISCOVERY_INTERVAL_SECONDS = 24 * 3600
```

Decision rules:
- If quota remaining is `0`, skip with `quota_exhausted`.
- If quota remaining is below `30`, only consider `T-25`.
- A match anchor is due when `anchor_at <= now < kickoff_at` and `last_refresh_at < anchor_at`.
- If any match anchor is due, the action is one competition-level refresh.
- If `now - last_refresh_at < min_interval`, skip with `global_throttle`.
- If no future matches exist, refresh only when there is no last refresh or last refresh is older than the discovery interval.

- [ ] **Step 3: Implement dry-run/live runner**

Implement:

```python
def run_csl_scheduled_publish(
    *,
    now: str | None = None,
    live: bool = False,
    force: bool = False,
    env_path: str | Path = ".env",
    cache_dir: str | Path = "data/cache",
    quota_path: str | Path = "data/cache/quota.json",
    snapshot_path: str | Path = "data/cache/csl_publish_snapshot.json",
    diagnostics_snapshot_path: str | Path = "data/local/diagnostics/csl_live_league_snapshot.json",
    endpoint: str = "https://football.celab.xin/api/ingest/snapshot",
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    ...
) -> dict:
```

Dry-run returns the decision and never reads `.env`, never calls The Odds API, never writes files, never publishes. Live mode reads `.env` only when due or `--force`; then it calls `run_league_odds_refresh`, builds the snapshot, attaches `run` metadata, writes the two snapshot paths, and calls `publish_snapshot(live=True)`.

- [ ] **Step 4: Add CLI**

CLI arguments:

```bash
python3 -m worldcup.csl_scheduled_publish \
  --cache-dir data/cache \
  --quota-path data/cache/quota.json \
  --snapshot-path data/cache/csl_publish_snapshot.json \
  --diagnostics-snapshot-path data/local/diagnostics/csl_live_league_snapshot.json \
  --env .env \
  --endpoint https://football.celab.xin/api/ingest/snapshot
```

Add `--live`, `--force`, `--now`, `--min-interval-seconds`, `--discovery-interval-seconds`, and `--no-notify` only if needed by the implementation. The first implementation does not send notifications.

### Task 2: CSL LaunchAgent Generator

**Files:**
- Create: `worldcup/csl_scheduled_launch_agent.py`
- Test: `tests/test_csl_scheduled_launch_agent.py`

- [ ] **Step 1: Add LaunchAgent tests**

Assert generated plist:
- Label is `xin.celab.football.csl-scheduled-publish`.
- ProgramArguments call `python -m worldcup.csl_scheduled_publish --live`.
- Uses absolute project paths for cache, quota, snapshot, env, and endpoint.
- `StartInterval` defaults to `1800`.
- Logs go to `~/Library/Logs/worldcup/csl-scheduled-publish.out.log` and `.err.log`.
- `RunAtLoad` defaults to false.

- [ ] **Step 2: Implement builder and writer**

Create `build_csl_scheduled_launch_agent()` and `write_csl_scheduled_launch_agent()`, following the style in `worldcup.pre_match_launch_agent`.

- [ ] **Step 3: Add CLI**

CLI supports dry-run JSON output by default and writes the plist only when `--out` is provided. It must not load or kickstart launchd.

### Task 3: Documentation And Recent Work

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Document CSL auto refresh semantics**

README should say:
- CSL automatic refresh is competition-level execution triggered by match-level anchors.
- Anchors are T-90 and T-25.
- Global minimum interval is 30 minutes.
- Quota below 30 keeps only T-25.
- No future matches means one discovery refresh per 24h.
- `club_rating_pending` remains observation mode.

- [ ] **Step 2: Record implementation safely**

`RECENT_WORK.md` should record code and config status without raw odds, API key, HMAC secret, `.env` content, or betting/amount language.

### Task 4: Verification And Installation Gate

**Files:**
- No source changes unless verification exposes bugs.

- [ ] **Step 1: Focused tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_scheduled_publish tests.test_csl_scheduled_launch_agent -v
```

- [ ] **Step 2: Compile and whitespace checks**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/csl_scheduled_publish.py worldcup/csl_scheduled_launch_agent.py
git diff --check
```

- [ ] **Step 3: Dry-run CLI**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_scheduled_publish
```

Expected: JSON status is `dry_run`, no `.env` read, no network call, no publish.

- [ ] **Step 4: Plist dry-run**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_scheduled_launch_agent
```

Expected: JSON status is `dry_run`, `loaded=false`.

- [ ] **Step 5: Ask before installing**

Only after code verification, ask the user before writing `~/Library/LaunchAgents/xin.celab.football.csl-scheduled-publish.plist` and running `launchctl bootstrap`.

## Adversarial Self-Review

- Root cause: existing CSL refresh is manual; this plan adds a dedicated runner rather than overloading the World Cup scheduled publisher.
- Scope control: no model parameter changes, no `club_rating_pending` lift, no ECS deploy, no push.
- Quota risk: global competition refresh plus 30-minute throttle prevents per-match multiplicative calls; low quota keeps only T-25.
- Fixture source risk: CSL fixtures are still `odds_event_only`, so the runner includes a 24h discovery path for new events.
- Safety: dry-run does not read `.env`; live mode must not print secrets, raw odds, HMAC signatures, or bookmaker price rows.
