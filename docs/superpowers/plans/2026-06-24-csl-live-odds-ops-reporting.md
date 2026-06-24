# CSL Live Odds Ops Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing read-only CSL live odds health check visible as a daily ops digest, so one `worldcup.ops_check` run clearly shows whether CSL live odds drifted, became synthetic, lost aliases, or produced runner blockers.

**Architecture:** Keep P9.9's full `local.csl_live_odds` JSON as the machine-readable source of truth, then add a derived `report` block and an optional text formatter. The report is pure, read-only, and only summarizes already-sanitized fields from `run_ops_check`; it does not read `.env`, does not call The Odds API, does not write cache, does not notify, and does not deploy anything.

**Tech Stack:** Python standard library, existing `worldcup.ops_check`, existing custom `tests/run_tests.py` runner.

---

## File Structure

- Modify: `worldcup/ops_check.py`
  - Add pure report helpers that derive a compact `report.csl_live_odds` digest from existing `local.csl_live_odds`.
  - Add `format_ops_report(report)` for a concise human-readable ops summary.
  - Add CLI option `--format json|summary`, keeping `json` as default for compatibility.
- Modify: `tests/test_ops_check.py`
  - Add report-focused tests for healthy CSL live odds, missing cache, alias drift, runner blockers, and text output safety.
- Modify: `README.md`
  - Document that the default JSON now contains `report.csl_live_odds`, and that `--format summary` gives the daily-readable view.
- Modify after implementation: `RECENT_WORK.md`
  - Record implementation, validation, and the fact that no live call, quota use, deployment, or LaunchAgent change occurred.

## Scope Guard

- P9.10 does not change P9.9 issue detection or `_count_issues()` semantics except where tests prove the report and count disagree.
- P9.10 does not add WxPusher, LaunchAgent, cron, ECS deployment, public preview, CSL signal publishing, or `club_rating_pending` changes.
- Missing live cache stays a warning, not an error.
- `club_rating_pending` and `odds_event_only` stay visible as runner warnings, but they do not become report errors by themselves.
- Report output must not include raw bookmaker names, raw markets, decimal odds prices, API keys, request URLs, `.env` values, HMAC material, or raw response payloads.

## Current Baseline

Read-only baseline on 2026-06-24:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.ops_check --no-public --no-remote
```

Observed CSL live odds shape:

```text
ok=true
summary.errors=0
local.csl_live_odds.status=ok
local.csl_live_odds.events=8
local.csl_live_odds.club_alias_unmatched_count=0
local.csl_live_odds.invalid_odds_count=0
local.csl_live_odds.has_synthetic_marker=false
local.csl_live_odds.runner_check.status=ok
local.csl_live_odds.runner_check.counts.matches=8
local.csl_live_odds.runner_check.strong_grades=[]
local.csl_live_odds.runner_check.warnings=["club_rating_pending", "odds_event_only"]
```

### Task 1: Add Pure CSL Live Odds Report Digest

**Files:**
- Modify: `worldcup/ops_check.py`
- Test: `tests/test_ops_check.py`

- [ ] **Step 1: Add failing healthy report test**

Update the import in `tests/test_ops_check.py`:

```python
from worldcup.ops_check import format_ops_report, run_ops_check, scan_text
```

Append this test after `test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets`:

```python
def test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload():
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
                    "observed_at": "2026-06-24T01:51:18+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "theoddsapi_secondary",
                    "quota_remaining": 248,
                    "quota_last": 3,
                    "cache_path": "data/cache/theoddsapi_csl_2026_odds.json",
                    "bookmaker": "must-not-leak",
                    "price": 2.05,
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

    report = result["report"]["csl_live_odds"]
    assert result["report"]["status"] == "ok"
    assert report == {
        "status": "ok",
        "competition_id": "csl_2026",
        "events": 1,
        "fixtures": 1,
        "odds_events": 1,
        "sport_keys": ["soccer_china_superleague"],
        "observed_at": "2026-06-24T01:51:18+00:00",
        "provider": "theoddsapi_secondary",
        "quota_remaining": 248,
        "quota_last": 3,
        "has_synthetic_marker": False,
        "club_alias_unmatched_count": 0,
        "invalid_odds_count": 0,
        "runner_status": "ok",
        "runner_matches": 1,
        "runner_warnings": ["club_rating_pending", "odds_event_only"],
        "runner_errors_count": 0,
        "runner_strong_grades": [],
        "rating_policy": "club_rating_pending",
        "club_rating_mode": "sample_replay",
        "club_rating_matches_replayed": 840,
        "club_rating_teams_rated": 22,
        "issues": [],
    }
    assert "must-not-leak" not in str(result)
    assert "2.05" not in str(result)
    assert "bookmakers" not in str(result)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload()
PY
```

Expected: FAIL with `ImportError` for `format_ops_report` or `KeyError: 'report'`.

- [ ] **Step 3: Add report helpers**

Insert these helpers in `worldcup/ops_check.py` after `_count_issues()`:

```python
def _report_status_from_counts(summary: dict[str, Any]) -> str:
    if _as_int(summary.get("errors")) > 0:
        return "error"
    if _as_int(summary.get("warnings")) > 0:
        return "warn"
    return "ok"


def _first_safe_number(*values: Any) -> int | float | None:
    for value in values:
        if _is_safe_number(value):
            return value
    return None


def _report_csl_issue_codes(csl: dict[str, Any], runner: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    status = csl.get("status")
    if status == "missing":
        issues.append("live_odds_cache_missing")
        return issues
    if status == "error":
        message = _safe_string(csl.get("message")) or "live_odds_cache_error"
        issues.append(message)
        return issues
    if status != "ok":
        issues.append("live_odds_status_unknown")
        return issues
    if csl.get("has_synthetic_marker") is True:
        issues.append("synthetic_live_cache")
    if _as_int(csl.get("club_alias_unmatched_count")) > 0:
        issues.append("club_alias_unmatched")
    if _as_int(csl.get("invalid_odds_count")) > 0:
        issues.append("invalid_decimal_odds")
    if runner.get("status") == "missing":
        issues.append("runner_diagnostic_missing")
    elif _csl_runner_has_error(runner):
        if runner.get("status") == "error":
            issues.append("runner_status_error")
        if _runner_has_blocking_warning(runner):
            issues.append("runner_blocking_warning")
        if _as_int(runner.get("club_alias_unmatched_count")) > 0:
            issues.append("runner_club_alias_unmatched")
        if _as_int(runner.get("invalid_odds_count")) > 0:
            issues.append("runner_invalid_odds")
        if _as_int(runner.get("errors_count")) > 0:
            issues.append("runner_errors")
        club_rating = runner.get("club_rating") if isinstance(runner.get("club_rating"), dict) else {}
        if _as_int(club_rating.get("errors_count")) > 0:
            issues.append("runner_club_rating_errors")
        if _list_values(runner.get("strong_grades")):
            issues.append("runner_strong_grades_present")
    return issues


def _report_csl_live_odds(csl: dict[str, Any]) -> dict[str, Any]:
    runner = csl.get("runner_check") if isinstance(csl.get("runner_check"), dict) else {}
    refresh = (
        csl.get("refresh_diagnostic")
        if isinstance(csl.get("refresh_diagnostic"), dict)
        else {}
    )
    quota = csl.get("quota") if isinstance(csl.get("quota"), dict) else {}
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}
    provider = refresh.get("theoddsapi_provider")
    provider_quota = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    club_rating = runner.get("club_rating") if isinstance(runner.get("club_rating"), dict) else {}
    counts = runner.get("counts") if isinstance(runner.get("counts"), dict) else {}
    issues = _report_csl_issue_codes(csl, runner)
    status = "ok"
    if csl.get("status") == "missing" or runner.get("status") == "missing":
        status = "warn"
    if any(issue != "live_odds_cache_missing" and issue != "runner_diagnostic_missing" for issue in issues):
        status = "error"
    report: dict[str, Any] = {
        "status": status,
        "competition_id": csl.get("competition_id"),
        "events": csl.get("events"),
        "fixtures": csl.get("fixtures"),
        "odds_events": csl.get("odds_events"),
        "sport_keys": csl.get("sport_keys") or [],
        "observed_at": refresh.get("observed_at"),
        "provider": provider,
        "quota_remaining": _first_safe_number(
            refresh.get("quota_remaining"),
            provider_quota.get("remaining"),
        ),
        "quota_last": _first_safe_number(refresh.get("quota_last"), provider_quota.get("last")),
        "has_synthetic_marker": csl.get("has_synthetic_marker"),
        "club_alias_unmatched_count": _as_int(csl.get("club_alias_unmatched_count")),
        "invalid_odds_count": _as_int(csl.get("invalid_odds_count")),
        "runner_status": runner.get("status"),
        "runner_matches": counts.get("matches"),
        "runner_warnings": runner.get("warnings") or [],
        "runner_errors_count": _as_int(runner.get("errors_count")),
        "runner_strong_grades": runner.get("strong_grades") or [],
        "rating_policy": runner.get("rating_policy"),
        "club_rating_mode": club_rating.get("mode"),
        "club_rating_matches_replayed": club_rating.get("matches_replayed"),
        "club_rating_teams_rated": club_rating.get("teams_rated"),
        "issues": issues,
    }
    return {key: value for key, value in report.items() if value is not None}


def build_ops_report(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    local = result.get("local") if isinstance(result.get("local"), dict) else {}
    csl = local.get("csl_live_odds") if isinstance(local.get("csl_live_odds"), dict) else {}
    return {
        "schema_version": 1,
        "status": _report_status_from_counts(summary),
        "errors": _as_int(summary.get("errors")),
        "warnings": _as_int(summary.get("warnings")),
        "csl_live_odds": _report_csl_live_odds(csl),
    }
```

- [ ] **Step 4: Attach report to `run_ops_check()`**

Change the end of `run_ops_check()` from:

```python
    summary = _count_issues(result)
    result["summary"] = summary
    result["ok"] = summary["errors"] == 0
    return result
```

to:

```python
    summary = _count_issues(result)
    result["summary"] = summary
    result["ok"] = summary["errors"] == 0
    result["report"] = build_ops_report(result)
    return result
```

- [ ] **Step 5: Run the focused test and verify it passes**

Run the command from Step 2 again.

Expected: PASS with no output.

### Task 2: Cover Warning and Error States in Report

**Files:**
- Modify: `tests/test_ops_check.py`
- Modify if needed: `worldcup/ops_check.py`

- [ ] **Step 1: Add report state tests**

Append these tests after the healthy report test:

```python
def test_csl_live_odds_report_marks_missing_cache_as_warning():
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

    csl_report = result["report"]["csl_live_odds"]
    assert result["ok"] is True
    assert csl_report["status"] == "warn"
    assert csl_report["issues"] == ["live_odds_cache_missing"]


def test_csl_live_odds_report_marks_alias_drift_as_error():
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

    csl_report = result["report"]["csl_live_odds"]
    assert result["ok"] is False
    assert csl_report["status"] == "error"
    assert csl_report["club_alias_unmatched_count"] == 1
    assert csl_report["issues"] == ["club_alias_unmatched"]


def test_csl_live_odds_report_marks_runner_blockers_as_error():
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
                    "strong_grades": [],
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

    csl_report = result["report"]["csl_live_odds"]
    assert result["ok"] is False
    assert csl_report["status"] == "error"
    assert csl_report["runner_warnings"] == ["club_rating_missing"]
    assert csl_report["issues"] == ["runner_blocking_warning", "runner_club_rating_errors"]
```

- [ ] **Step 2: Run focused report tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in [
    "test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload",
    "test_csl_live_odds_report_marks_missing_cache_as_warning",
    "test_csl_live_odds_report_marks_alias_drift_as_error",
    "test_csl_live_odds_report_marks_runner_blockers_as_error",
]:
    getattr(module, name)()
PY
```

Expected: PASS.

### Task 3: Add Human-Readable Summary Format

**Files:**
- Modify: `worldcup/ops_check.py`
- Modify: `tests/test_ops_check.py`

- [ ] **Step 1: Add failing text formatter test**

Add imports at the top of `tests/test_ops_check.py`:

```python
import io
from contextlib import redirect_stdout
```

Update the ops import:

```python
from worldcup.ops_check import format_ops_report, main as ops_check_main, run_ops_check, scan_text
```

Append this test near the report tests:

```python
def test_ops_check_summary_format_prints_daily_csl_digest_without_raw_payload():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        pre_match_launch_agent = logs_dir / "xin.celab.football.pre-match.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write_pre_match_plist(pre_match_launch_agent)
        _write(logs_dir / "scheduled-publish.out.log", "")
        _write(logs_dir / "scheduled-publish.err.log", "")
        _write(logs_dir / "pre-match.out.log", "")
        _write(logs_dir / "pre-match.err.log", "")
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([_csl_live_odds_event()]))
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(
                {
                    "status": "fetched",
                    "events": 1,
                    "observed_at": "2026-06-24T01:51:18+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "theoddsapi_secondary",
                    "quota_remaining": 248,
                    "quota_last": 3,
                }
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {"fixtures": 1, "odds_events": 1, "match_inputs": 1, "matches": 1},
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
                    "strong_grades": [],
                }
            ),
        )

        out = io.StringIO()
        with redirect_stdout(out):
            code = ops_check_main(
                [
                    "--root", str(root),
                    "--no-public",
                    "--no-remote",
                    "--launch-agent", str(launch_agent),
                    "--local-log", str(logs_dir / "scheduled-publish.out.log"),
                    "--local-log", str(logs_dir / "scheduled-publish.err.log"),
                    "--pre-match-launch-agent", str(pre_match_launch_agent),
                    "--pre-match-log", str(logs_dir / "pre-match.out.log"),
                    "--pre-match-log", str(logs_dir / "pre-match.err.log"),
                    "--format", "summary",
                ]
            )

    text = out.getvalue()
    assert code == 0
    assert "ops_check: ok errors=0 warnings=0" in text
    assert "CSL live odds: ok events=1 fixtures=1 odds_events=1" in text
    assert "provider=theoddsapi_secondary quota_remaining=248 quota_last=3" in text
    assert "guards: synthetic=false alias_unmatched=0 invalid_odds=0 issues=none" in text
    assert "runner: ok matches=1 rating_policy=club_rating_pending" in text
    assert "club_rating=sample_replay replayed=840 teams=22" in text
    assert "warnings=club_rating_pending,odds_event_only strong_grades=none" in text
    assert "bookmakers" not in text
    assert "safe_book" not in text
    assert "2.05" not in text
```

- [ ] **Step 2: Add formatter implementation**

Add these helpers after `build_ops_report()`:

```python
def _format_bool(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def _format_list(value: Any) -> str:
    items = [str(item) for item in value] if isinstance(value, list) else []
    return ",".join(items) if items else "none"


def format_ops_report(report: dict[str, Any]) -> str:
    csl = report.get("csl_live_odds") if isinstance(report.get("csl_live_odds"), dict) else {}
    lines = [
        (
            f"ops_check: {report.get('status')} "
            f"errors={_as_int(report.get('errors'))} "
            f"warnings={_as_int(report.get('warnings'))}"
        ),
        (
            f"CSL live odds: {csl.get('status')} "
            f"events={csl.get('events', 'n/a')} "
            f"fixtures={csl.get('fixtures', 'n/a')} "
            f"odds_events={csl.get('odds_events', 'n/a')}"
        ),
        (
            f"  observed_at={csl.get('observed_at', 'n/a')} "
            f"provider={csl.get('provider', 'n/a')} "
            f"quota_remaining={csl.get('quota_remaining', 'n/a')} "
            f"quota_last={csl.get('quota_last', 'n/a')}"
        ),
        (
            f"  guards: synthetic={_format_bool(csl.get('has_synthetic_marker'))} "
            f"alias_unmatched={_as_int(csl.get('club_alias_unmatched_count'))} "
            f"invalid_odds={_as_int(csl.get('invalid_odds_count'))} "
            f"issues={_format_list(csl.get('issues'))}"
        ),
        (
            f"  runner: {csl.get('runner_status', 'n/a')} "
            f"matches={csl.get('runner_matches', 'n/a')} "
            f"rating_policy={csl.get('rating_policy', 'n/a')} "
            f"club_rating={csl.get('club_rating_mode', 'n/a')} "
            f"replayed={csl.get('club_rating_matches_replayed', 'n/a')} "
            f"teams={csl.get('club_rating_teams_rated', 'n/a')}"
        ),
        (
            f"  warnings={_format_list(csl.get('runner_warnings'))} "
            f"strong_grades={_format_list(csl.get('runner_strong_grades'))}"
        ),
    ]
    return "\n".join(lines)
```

- [ ] **Step 3: Wire `--format`**

In `main()`, add:

```python
    parser.add_argument("--format", choices=("json", "summary"), default="json")
```

Change the print block from:

```python
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
```

to:

```python
    if args.format == "summary":
        print(format_ops_report(result["report"]))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1
```

- [ ] **Step 4: Run text formatter test**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.test_ops_check_summary_format_prints_daily_csl_digest_without_raw_payload()
PY
```

Expected: PASS.

### Task 4: Document Daily Usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update ops_check documentation**

In the `日常运维推荐使用一键只读检查命令` paragraph, add this sentence after the existing read-only guarantee:

```markdown
P9.10 起，默认 JSON 会包含顶层 `report.csl_live_odds` 日常摘要；需要人工快速巡检时可用 `python3 -m worldcup.ops_check --format summary` 输出短报告，展示 CSL live odds cache 状态、event/fixture 数、provider/quota 摘要、synthetic/alias/非法赔率 guard、runner 状态、`club_rating_pending`/`odds_event_only` warning 和强等级异常。
```

Add this command after the existing `python3 -m worldcup.ops_check` block:

```bash
python3 -m worldcup.ops_check --format summary
```

- [ ] **Step 2: Check docs do not imply a signal launch**

Confirm the same section still states that the command does not trigger refresh, publish, secret read, or quota use. Confirm no wording describes CSL output as betting advice, stake sizing, public signal publication, or `club_rating_pending` removal.

Expected: README documents report usage only.

### Task 5: Full Verification

**Files:**
- Modify after verification: `RECENT_WORK.md`

- [ ] **Step 1: Run focused ops tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in [
    "test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload",
    "test_csl_live_odds_report_marks_missing_cache_as_warning",
    "test_csl_live_odds_report_marks_alias_drift_as_error",
    "test_csl_live_odds_report_marks_runner_blockers_as_error",
    "test_ops_check_summary_format_prints_daily_csl_digest_without_raw_payload",
]:
    getattr(module, name)()
PY
```

Expected: PASS.

- [ ] **Step 2: Run full suite**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. Current P9.9 baseline was `553/553 tests passed`; after adding five tests, expected count is `558/558 tests passed`.

- [ ] **Step 3: Run read-only real local ops summary**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.ops_check --no-public --no-remote --format summary
```

Expected on this machine:

```text
ops_check: warn errors=0 warnings=2
CSL live odds: ok events=8 fixtures=8 odds_events=8
  observed_at=2026-06-24T01:51:18.952055+00:00 provider=theoddsapi_secondary quota_remaining=248 quota_last=3
  guards: synthetic=false alias_unmatched=0 invalid_odds=0 issues=none
  runner: ok matches=8 rating_policy=club_rating_pending club_rating=sample_replay replayed=840 teams=22
  warnings=club_rating_pending,odds_event_only strong_grades=none
```

The two warnings are expected local log warning counts from the current machine, not CSL live odds blockers.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 5: Update recent work**

Add this entry at the top of `RECENT_WORK.md`, replacing the test count with observed output:

```markdown
## 2026-06-24 P9.10 CSL live odds 巡检报告化实现

- `worldcup.ops_check` 新增顶层 `report` 摘要和 `--format summary`，从既有只读 `local.csl_live_odds` 安全字段生成 CSL live odds 日常巡检短报告。
- 短报告展示 cache status、events/fixtures/odds_events、provider/quota 摘要、synthetic/alias/非法赔率 guard、runner status、runner warnings、club rating replay 状态和 strong grades 异常；不输出 raw odds、bookmaker、market、price、URL、API key、HMAC 或 `.env` 值。
- P9.10 不改变 P9.9 issue 计数语义：缺少 live cache 仍为 warning；synthetic、alias drift、非法赔率、runner blocker、runner error、runner strong grades 仍为 error。
- 本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot。
- 验证：新增 report/summary 聚焦测试通过；项目标准 full `tests/run_tests.py` 返回 `558/558 tests passed`；只读 `python3 -m worldcup.ops_check --no-public --no-remote --format summary` 输出 CSL live odds `ok events=8`；`git diff --check` 通过。
```

- [ ] **Step 6: Commit only after explicit confirmation**

Run only after the user confirms commit:

```bash
git add worldcup/ops_check.py tests/test_ops_check.py README.md RECENT_WORK.md
git commit -m "Add CSL live odds ops report"
```

Expected: commit created locally. Push, ECS deploy, LaunchAgent changes, and live refresh remain out of scope and require separate confirmation.

## Adversarial Self-Review

- Root cause: P9.9 already detects drift; P9.10 fixes the remaining usability gap by making the signal readable in daily ops output instead of requiring manual JSON spelunking.
- Scope: The plan does not modify collectors, alias tables, runner modeling, grade policy, preview/public output, ingest, ECS, or scheduler state.
- Quota and secret risk: All new logic derives from the already-sanitized `run_ops_check` result; no `.env`, source client, live refresh, network call, or HMAC path is introduced.
- Report correctness: The report must not have its own hidden health rules that conflict with `_count_issues()`. Tests cover missing cache warning, alias drift error, runner blocker error, and healthy output.
- Clean-machine behavior: Missing `data/cache/theoddsapi_csl_2026_odds.json` stays non-blocking, so new contributors and machines before a confirmed live fetch can still run ops checks.
- Data interpretation: `club_rating_pending` remains an explicit warning/note, not a reason to show CSL outputs as formal betting signals. The report is operational health only.
- Security: Text output is intentionally count/status based. If a future diagnostic field contains a secret-like value, it must be dropped at P9.9's sanitization layer and must not appear in `report` or `--format summary`.
- Research boundary: No part of P9.10 should produce投注建议, 下注金额, 追损, 重注, 串关喊单, or execution advice.
