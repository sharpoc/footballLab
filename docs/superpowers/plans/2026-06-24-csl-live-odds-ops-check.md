# CSL Live Odds Ops Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `worldcup.ops_check` guard that inspects local CSL live odds cache and diagnostics after a manually confirmed live refresh.

**Architecture:** Extend the existing `local` section in `worldcup.ops_check` with `local.csl_live_odds`. The check reads only ignored local files, never reads `.env`, never calls The Odds API, never writes a new artifact, and returns safe summaries instead of raw odds/bookmaker payloads.

**Tech Stack:** Python standard library, existing `worldcup.ops_check`, existing `worldcup.collectors.league_odds.parse_league_odds_events`, existing custom test runner.

---

## File Structure

- Modify: `worldcup/ops_check.py`
  - Add constants for the CSL live cache and diagnostics paths.
  - Add safe JSON payload reading for list-shaped odds cache.
  - Add `local.csl_live_odds` summary that parses the live cache through the existing league odds parser.
  - Extend `_count_issues()` so alias drift, synthetic cache, invalid odds, and runner diagnostic blockers are counted.
- Modify: `tests/test_ops_check.py`
  - Add CSL live odds fixtures.
  - Cover healthy live cache, missing cache warning, alias drift, synthetic marker, and runner diagnostic blockers.
- Modify: `RECENT_WORK.md`
  - Add the implementation outcome after execution, including validation and the fact that no live call or secret read happened.

## Safety Contract

- `python3 -m worldcup.ops_check --no-public --no-remote` remains read-only.
- The new check must not import or call `load_env`, must not inspect `.env`, and must not call `worldcup.sources.theoddsapi`.
- Missing `data/cache/theoddsapi_csl_2026_odds.json` is a warning, not an error, so clean machines and pre-live setups still pass.
- `has_synthetic_marker=true`, non-empty `club_alias_unmatched`, positive `invalid_odds_count`, runner `club_rating_missing`, runner `club_rating_invalid`, runner `club_rating_sample_too_small`, runner alias drift, runner invalid odds, or runner strong final grades are errors.
- Returned data may include counts, status strings, safe path strings, provider names, quota numbers, warning labels, and unmatched team names. It must not include bookmaker odds prices, raw markets, API keys, request URLs, HMAC material, `.env` values, or raw response payloads.

### Task 1: Add Healthy CSL Live Odds Ops Test

**Files:**
- Modify: `tests/test_ops_check.py`

- [ ] **Step 1: Add CSL test fixtures**

Insert these helpers after `_fake_fetcher()` in `tests/test_ops_check.py`:

```python
def _csl_live_odds_event(
    home_team: str = "Shanghai SIPG FC",
    away_team: str = "Beijing FC",
    event_id: str = "csl-event-1",
) -> dict:
    return {
        "id": event_id,
        "sport_key": "soccer_china_superleague",
        "commence_time": "2026-06-25T11:35:00Z",
        "home_team": home_team,
        "away_team": away_team,
        "bookmakers": [
            {
                "key": "safe_book",
                "last_update": "2026-06-24T01:51:18Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": home_team, "price": 2.05},
                            {"name": away_team, "price": 3.10},
                            {"name": "Draw", "price": 3.30},
                        ],
                    },
                    {
                        "key": "spreads",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": home_team, "price": 1.91, "point": -0.5},
                            {"name": away_team, "price": 1.95, "point": 0.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": "Over", "price": 1.88, "point": 2.5},
                            {"name": "Under", "price": 1.98, "point": 2.5},
                        ],
                    },
                ],
            }
        ],
    }


def _write_minimal_ops_inputs(root: Path, launch_agent: Path) -> None:
    snapshot = {
        "snapshot_at": "2026-06-10T10:07:25+00:00",
        "counts": {"matches": 72},
        "matches": [],
        "run": {"run_id": "20260610T100725Z-live"},
        "data_quality": {"source_errors": [], "stale_sources": []},
    }
    _write(root / "data/cache/analysis_snapshot.json", json.dumps(snapshot))
    _write(
        root / "data/cache/quota.json",
        json.dumps(
            {
                "providers": {
                    "theoddsapi_secondary": {
                        "remaining": 248,
                        "used": 252,
                        "last": 3,
                        "api_key": "must-not-leak",
                    }
                }
            }
        ),
    )
    _write_plist(launch_agent)
```

- [ ] **Step 2: Add the healthy test**

Append this test near the other `run_ops_check` tests:

```python
def test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(
                {
                    "status": "fetched",
                    "events": 1,
                    "observed_at": "2026-06-24T01:51:18.952055+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "theoddsapi_secondary",
                    "quota_remaining": 248,
                    "quota_last": 3,
                    "cache_path": "data/cache/theoddsapi_csl_2026_odds.json",
                    "raw_price_should_not_leak": 1.91,
                    "secret": "must-not-leak",
                }
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "fixture_source": "odds_event_only",
                    "warnings": ["club_rating_pending", "odds_event_only"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "rating_policy": "club_rating_pending",
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": [],
                    },
                    "signals": 7,
                    "strong_grades": [],
                    "raw_market_should_not_leak": [{"price": 1.91}],
                    "secret": "must-not-leak",
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    csl = result["local"]["csl_live_odds"]
    assert result["ok"] is True
    assert csl["status"] == "ok"
    assert csl["competition_id"] == "csl_2026"
    assert csl["events"] == 1
    assert csl["sport_keys"] == ["soccer_china_superleague"]
    assert csl["has_synthetic_marker"] is False
    assert csl["club_alias_unmatched"] == []
    assert csl["invalid_odds_count"] == 0
    assert csl["quota"]["providers"]["theoddsapi_secondary"] == {
        "remaining": 248,
        "used": 252,
        "last": 3,
    }
    assert csl["refresh_diagnostic"]["status"] == "fetched"
    assert csl["runner_check"]["counts"]["matches"] == 1
    assert csl["runner_check"]["club_rating"]["mode"] == "sample_replay"
    assert csl["runner_check"]["strong_grades"] == []
    assert "must-not-leak" not in str(result)
    assert "raw_market_should_not_leak" not in str(result)
    assert "raw_price_should_not_leak" not in str(result)
```

- [ ] **Step 3: Run the focused test and verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
site = Path('/Users/eagod/Library/Python/3.9/lib/python/site-packages')
if site.exists():
    sys.path.append(str(site))
module_path = Path('tests/test_ops_check.py')
spec = importlib.util.spec_from_file_location('test_ops_check', module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets()
PY
```

Expected: FAIL with `KeyError: 'csl_live_odds'`.

### Task 2: Add Drift, Synthetic, Missing, and Runner Blocker Tests

**Files:**
- Modify: `tests/test_ops_check.py`

- [ ] **Step 1: Add failure-mode tests**

Append these tests after the healthy CSL test:

```python
def test_run_ops_check_flags_csl_live_alias_drift_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event(away_team="Unknown FC")]),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert result["local"]["csl_live_odds"]["club_alias_unmatched"] == ["Unknown FC"]


def test_run_ops_check_flags_csl_live_synthetic_marker_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        event = _csl_live_odds_event()
        event["_synthetic_smoke"] = True
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([event]))

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert result["local"]["csl_live_odds"]["has_synthetic_marker"] is True


def test_run_ops_check_treats_missing_csl_live_cache_as_warning_only():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is True
    assert result["summary"]["warnings"] == 1
    assert result["local"]["csl_live_odds"]["status"] == "missing"


def test_run_ops_check_flags_csl_live_runner_blockers_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {"fixtures": 1, "odds_events": 1, "match_inputs": 1, "matches": 1},
                    "warnings": ["club_rating_missing"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "fallback",
                        "matches_replayed": 0,
                        "teams_rated": 0,
                        "sample_too_small": True,
                        "errors": ["missing"],
                    },
                    "strong_grades": ["S"],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] >= 1
    runner = result["local"]["csl_live_odds"]["runner_check"]
    assert runner["warnings"] == ["club_rating_missing"]
    assert runner["strong_grades"] == ["S"]
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
site = Path('/Users/eagod/Library/Python/3.9/lib/python/site-packages')
if site.exists():
    sys.path.append(str(site))
module_path = Path('tests/test_ops_check.py')
spec = importlib.util.spec_from_file_location('test_ops_check', module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in [
    'test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets',
    'test_run_ops_check_flags_csl_live_alias_drift_as_error',
    'test_run_ops_check_flags_csl_live_synthetic_marker_as_error',
    'test_run_ops_check_treats_missing_csl_live_cache_as_warning_only',
    'test_run_ops_check_flags_csl_live_runner_blockers_as_error',
]:
    getattr(module, name)()
PY
```

Expected: FAIL before implementation because `local.csl_live_odds` does not exist.

### Task 3: Implement Read-Only CSL Live Odds Summary

**Files:**
- Modify: `worldcup/ops_check.py`
- Test: `tests/test_ops_check.py`

- [ ] **Step 1: Add import and constants**

In `worldcup/ops_check.py`, add this import with the other project imports:

```python
from worldcup.collectors.league_odds import parse_league_odds_events
```

Add these constants after `DEFAULT_LINEUP_AUDIT_PATH`:

```python
DEFAULT_CSL_COMPETITION_ID = "csl_2026"
DEFAULT_CSL_LIVE_ODDS_CACHE_PATH = Path("data/cache/theoddsapi_csl_2026_odds.json")
DEFAULT_CSL_LIVE_REFRESH_DIAGNOSTIC_PATH = Path(
    "data/local/diagnostics/csl_live_odds_refresh.json"
)
DEFAULT_CSL_LIVE_RUNNER_CHECK_PATH = Path(
    "data/local/diagnostics/csl_live_league_runner_check.json"
)
SAFE_QUOTA_FIELDS = ("remaining", "used", "last")
SAFE_REFRESH_DIAGNOSTIC_FIELDS = (
    "status",
    "events",
    "observed_at",
    "has_synthetic_marker",
    "theoddsapi_provider",
    "quota_remaining",
    "quota_last",
    "cache_path",
)
SAFE_RUNNER_FIELDS = (
    "status",
    "counts",
    "fixture_source",
    "warnings",
    "errors",
    "club_alias_unmatched",
    "invalid_odds_count",
    "rating_policy",
    "signals",
    "strong_grades",
)
SAFE_CLUB_RATING_FIELDS = (
    "mode",
    "matches_replayed",
    "teams_rated",
    "skipped_rows",
    "sample_too_small",
    "errors",
)
CSL_RUNNER_BLOCKING_WARNINGS = {
    "club_rating_missing",
    "club_rating_invalid",
    "club_rating_sample_too_small",
}
```

- [ ] **Step 2: Add safe helper functions**

Insert these helpers after `_read_json()`:

```python
def _read_json_any(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _subset_dict(payload: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _safe_quota_providers(root: Path) -> dict[str, Any]:
    path = root / "data/cache/quota.json"
    quota = _read_json(path)
    if quota is None:
        return {"status": "missing", "path": str(path), "providers": {}}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    safe_providers: dict[str, Any] = {}
    for provider, value in providers.items():
        if isinstance(value, dict):
            safe_providers[str(provider)] = _subset_dict(value, SAFE_QUOTA_FIELDS)
    return {"status": "ok", "path": str(path), "providers": safe_providers}


def _safe_refresh_diagnostic(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_CSL_LIVE_REFRESH_DIAGNOSTIC_PATH
    payload = _read_json(path)
    if payload is None:
        return {"status": "missing", "path": str(path)}
    return {"path": str(path), **_subset_dict(payload, SAFE_REFRESH_DIAGNOSTIC_FIELDS)}


def _safe_runner_check(root: Path) -> dict[str, Any]:
    path = root / DEFAULT_CSL_LIVE_RUNNER_CHECK_PATH
    payload = _read_json(path)
    if payload is None:
        return {"status": "missing", "path": str(path)}
    result = {"path": str(path), **_subset_dict(payload, SAFE_RUNNER_FIELDS)}
    club_rating = payload.get("club_rating")
    if isinstance(club_rating, dict):
        result["club_rating"] = _subset_dict(club_rating, SAFE_CLUB_RATING_FIELDS)
    if "status" not in result:
        result["status"] = "ok"
    return result
```

- [ ] **Step 3: Add the CSL summary function**

Insert this function after `_safe_runner_check()`:

```python
def _csl_live_odds_summary(
    root: Path,
    competition_id: str = DEFAULT_CSL_COMPETITION_ID,
) -> dict[str, Any]:
    cache_path = root / DEFAULT_CSL_LIVE_ODDS_CACHE_PATH
    if not cache_path.exists():
        return {
            "status": "missing",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "live_odds_cache_missing",
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    payload = _read_json_any(cache_path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        return {
            "status": "error",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "invalid_odds_cache_shape",
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    try:
        parse_result = parse_league_odds_events(payload, competition_id)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "error",
            "competition_id": competition_id,
            "path": str(cache_path),
            "message": "invalid_odds_cache_payload",
            "error_type": type(exc).__name__,
            "quota": _safe_quota_providers(root),
            "refresh_diagnostic": _safe_refresh_diagnostic(root),
            "runner_check": _safe_runner_check(root),
        }

    return {
        "status": "ok",
        "competition_id": competition_id,
        "path": str(cache_path),
        "events": len(payload),
        "fixtures": len(parse_result.fixtures),
        "odds_events": len(parse_result.odds_events),
        "sport_keys": sorted(
            {
                str(item.get("sport_key"))
                for item in payload
                if str(item.get("sport_key", "")).strip()
            }
        ),
        "has_synthetic_marker": any(item.get("_synthetic_smoke") is True for item in payload),
        "club_alias_unmatched": parse_result.unmatched_clubs,
        "invalid_odds_count": sum(len(event.invalid_odds) for event in parse_result.odds_events),
        "quota": _safe_quota_providers(root),
        "refresh_diagnostic": _safe_refresh_diagnostic(root),
        "runner_check": _safe_runner_check(root),
    }
```

- [ ] **Step 4: Wire the summary into local checks**

Modify `_local_checks()` so the returned dictionary includes `csl_live_odds`:

```python
def _local_checks(
    root: Path,
    launch_agent_path: str | Path,
    local_log_paths: list[str | Path],
    pre_match_launch_agent_path: str | Path | None,
    pre_match_log_paths: list[str | Path],
    lineup_audit_path: str | Path,
) -> dict[str, Any]:
    return {
        "snapshot": _snapshot_summary(root / "data/cache/analysis_snapshot.json"),
        "quota": _quota_summary(root / "data/cache/quota.json"),
        "csl_live_odds": _csl_live_odds_summary(root),
        "finished": _finished_consistency(root),
        "history": summarize_history(root / "data/local/history", limit=3),
        "launch_agent": inspect_launch_agent(launch_agent_path),
        "logs": [_scan_log_file(Path(path).expanduser()) for path in local_log_paths],
        "pre_match": _pre_match_checks(
            root,
            launch_agent_path=pre_match_launch_agent_path,
            log_paths=pre_match_log_paths,
            lineup_audit_path=lineup_audit_path,
        ),
    }
```

- [ ] **Step 5: Run the focused healthy test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
site = Path('/Users/eagod/Library/Python/3.9/lib/python/site-packages')
if site.exists():
    sys.path.append(str(site))
module_path = Path('tests/test_ops_check.py')
spec = importlib.util.spec_from_file_location('test_ops_check', module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets()
PY
```

Expected: PASS.

### Task 4: Count CSL Live Odds Issues

**Files:**
- Modify: `worldcup/ops_check.py`
- Test: `tests/test_ops_check.py`

- [ ] **Step 1: Add issue-count helpers**

Insert these helpers before `_count_issues()`:

```python
def _list_values(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _runner_has_blocking_warning(runner: dict[str, Any]) -> bool:
    warnings = {str(item) for item in _list_values(runner.get("warnings"))}
    return bool(warnings & CSL_RUNNER_BLOCKING_WARNINGS)


def _csl_runner_has_error(runner: dict[str, Any]) -> bool:
    if runner.get("status") == "missing":
        return False
    if runner.get("status") == "error":
        return True
    return (
        _runner_has_blocking_warning(runner)
        or bool(_list_values(runner.get("club_alias_unmatched")))
        or _as_int(runner.get("invalid_odds_count")) > 0
        or bool(_list_values(runner.get("strong_grades")))
    )
```

- [ ] **Step 2: Extend `_count_issues()`**

Inside `_count_issues()`, immediately after `local = result.get("local") or {}`, add:

```python
    csl_live_odds = local.get("csl_live_odds") if isinstance(local.get("csl_live_odds"), dict) else {}
    csl_status = csl_live_odds.get("status")
    warnings += int(csl_status == "missing")
    errors += int(csl_status == "error")
    if csl_status == "ok":
        errors += int(csl_live_odds.get("has_synthetic_marker") is True)
        errors += int(bool(_list_values(csl_live_odds.get("club_alias_unmatched"))))
        errors += int(_as_int(csl_live_odds.get("invalid_odds_count")) > 0)
        runner = (
            csl_live_odds.get("runner_check")
            if isinstance(csl_live_odds.get("runner_check"), dict)
            else {}
        )
        warnings += int(runner.get("status") == "missing")
        errors += int(_csl_runner_has_error(runner))
```

- [ ] **Step 3: Run all focused CSL ops tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
import sys
from pathlib import Path
site = Path('/Users/eagod/Library/Python/3.9/lib/python/site-packages')
if site.exists():
    sys.path.append(str(site))
module_path = Path('tests/test_ops_check.py')
spec = importlib.util.spec_from_file_location('test_ops_check', module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in [
    'test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets',
    'test_run_ops_check_flags_csl_live_alias_drift_as_error',
    'test_run_ops_check_flags_csl_live_synthetic_marker_as_error',
    'test_run_ops_check_treats_missing_csl_live_cache_as_warning_only',
    'test_run_ops_check_flags_csl_live_runner_blockers_as_error',
]:
    getattr(module, name)()
PY
```

Expected: PASS.

- [ ] **Step 4: Run the full suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import runpy
import sys
from pathlib import Path
site = Path('/Users/eagod/Library/Python/3.9/lib/python/site-packages')
if site.exists():
    sys.path.append(str(site))
runpy.run_path('tests/run_tests.py', run_name='__main__')
PY
```

Expected: all tests pass. The current baseline before this plan was `538/538 tests passed`, so the expected new total is `543/543 tests passed`.

- [ ] **Step 5: Commit after tests pass**

Run:

```bash
git add worldcup/ops_check.py tests/test_ops_check.py
git commit -m "Add CSL live odds ops check"
```

Expected: commit created on the current feature branch. If the user has not confirmed committing for this execution phase, pause before this step and ask for confirmation.

### Task 5: Verify Real Local Diagnostic Shape Without Live Calls

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run local read-only ops check**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.ops_check --no-public --no-remote
```

Expected on this machine after P9.8 live fetch: JSON includes `local.csl_live_odds.status` as `ok`, `events` as `8`, `club_alias_unmatched` as `[]`, `invalid_odds_count` as `0`, `has_synthetic_marker` as `false`, and no raw bookmaker prices. Expected on a clean machine without the ignored cache: command still exits `0` with `local.csl_live_odds.status` as `missing` and one warning.

- [ ] **Step 2: Run formatting and whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 3: Update recent work**

Add this entry at the top of `RECENT_WORK.md`, replacing the test count with the observed full-suite output:

```markdown
## 2026-06-24 P9.9 CSL live odds 巡检

- `worldcup.ops_check` 新增只读 `local.csl_live_odds` 检查，读取 ignored live odds cache 与 diagnostics：`data/cache/theoddsapi_csl_2026_odds.json`、`data/cache/quota.json`、`data/local/diagnostics/csl_live_odds_refresh.json`、`data/local/diagnostics/csl_live_league_runner_check.json`。
- 巡检复用 `parse_league_odds_events()` 检测 CSL live alias 漂移和非法 decimal odds；只输出安全摘要，不输出 bookmaker 原始赔率、market payload、API key、`.env`、HMAC 或请求 URL。
- 缺少 live odds cache 只计 warning，避免干净机器或尚未执行 live 的环境失败；synthetic marker、未匹配 club alias、非法赔率、runner `club_rating_missing` / `club_rating_invalid` / `club_rating_sample_too_small`、runner 强等级输出均计 error。
- 本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot。
- 验证：新增 ops_check CSL focused tests 通过；项目标准 full `tests/run_tests.py` 返回 `543/543 tests passed`；`python3 -m worldcup.ops_check --no-public --no-remote` 只读通过；`git diff --check` 通过。
```

- [ ] **Step 4: Commit recent work after confirmation**

Run:

```bash
git add RECENT_WORK.md
git commit -m "Document CSL live odds ops check"
```

Expected: second commit created. If the user has not confirmed committing for this execution phase, pause before this step and ask for confirmation.

## Adversarial Self-Review

- Root cause coverage: P9.8 live fetch exposed real provider naming drift. This plan keeps the drift guard inside daily ops, so future alias changes are caught by the same read-only command used for other local health checks.
- Scope control: The plan does not alter `league_runner`, does not relax `club_rating_pending`, does not change signal grading, and does not publish CSL snapshots.
- Live and quota risk: No task calls The Odds API, reads `.env`, or writes live cache. The only live artifact consumed is an already-existing ignored local cache from a separately confirmed refresh.
- Secret handling: The implementation returns selected safe fields only. Tests deliberately place `must-not-leak` in quota and diagnostics to prove raw or sensitive fields are excluded.
- Clean-machine behavior: Missing CSL live cache is a warning, not an error, so `ops_check` remains useful before any live refresh has been performed.
- Research boundary: The check reports operational health only. It does not produce betting advice, stake sizes, chasing, parlay calls, or any execution recommendation.
- Validation path: Focused tests cover healthy, alias drift, synthetic, missing cache, and runner-blocker states; full suite and `git diff --check` guard regressions.
