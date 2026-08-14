# CSL Ops Check Live Source Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `worldcup.ops_check` report the current valid CSL odds-cache commit time and current scheduler-equivalent quota slot instead of allowing a stale legacy refresh diagnostic to override them.

**Architecture:** Keep `local.csl_live_odds.refresh_diagnostic` unchanged for auditability, add a safe `cache_updated_at` only after the odds cache passes its existing parser, and derive compact report fields through current-source-first per-field fallback. Quota selection is read-only and mirrors the CSL provider order and `>30`, `>0`, then `0` policy without loading `.env`.

**Tech Stack:** Python 3 standard library, existing `worldcup.ops_check`, custom runner `tests/run_tests.py`, Git.

## Global Constraints

- Modify production behavior only in `worldcup/ops_check.py` and tests only in `tests/test_ops_check.py`.
- Do not change scheduler, LaunchAgents, cadence, due calculation, public API, snapshots, model, settlement, database, dependencies, or deployment.
- Do not read `.env`, load API keys, call The Odds API, consume quota, publish, notify, deploy, or write ignored runtime artifacts.
- `cache_updated_at` means the local commit time of a cache that passed the existing parser; it is not bookmaker quote time.
- Preserve current `status/errors/warnings`, guards, runner fields, and CLI exit codes.
- Keep the legacy refresh diagnostic visible; use it only as per-field fallback.
- Accept only non-negative integer `remaining` values. Reject booleans, floats, strings, negatives, unknown providers, and extra fields for selection.
- Provider order is primary, secondary, tertiary, legacy. Select first `remaining > 30`, else first `> 0`, else first `== 0`.
- Do not add cache-age warnings or claim any model-performance improvement.

---

### Task 1: Implement and lock the effective CSL ops source contract

**Files:**
- Modify: `tests/test_ops_check.py:1-12,458-550,660-870`
- Modify: `worldcup/ops_check.py:1-65,232-341,474-539,1165-1216`

**Interfaces:**
- Consumes: `_csl_live_odds_summary(root: Path) -> dict[str, Any]`, `_safe_quota_providers(root: Path) -> dict[str, Any]`, and `build_ops_report(result: dict[str, Any]) -> dict[str, Any]`.
- Produces: `_safe_cache_updated_at(path: Path) -> str | None`, `_select_current_quota_provider(quota: dict[str, Any]) -> tuple[str, dict[str, Any]] | None`, and `local.csl_live_odds.cache_updated_at`.
- Preserves: `local.csl_live_odds.refresh_diagnostic` and all existing safety projections.

- [ ] **Step 1: Add deterministic test helpers**

Add imports and helpers near the existing `_write()` in `tests/test_ops_check.py`:

```python
import os
from datetime import datetime


def _set_mtime_utc(path: Path, value: str) -> None:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    os.utime(path, (timestamp, timestamp))


def _run_csl_ops_fixture(
    root: Path,
    providers: dict,
    diagnostic: dict | None = None,
    cache_updated_at: str = "2026-08-13T01:25:57+00:00",
) -> dict:
    launch_agent = root / "logs/xin.celab.football.scheduled-publish.plist"
    _write_minimal_ops_inputs(root, launch_agent)
    cache_path = root / "data/cache/theoddsapi_csl_2026_odds.json"
    _write(cache_path, json.dumps([_csl_live_odds_event()]))
    _set_mtime_utc(cache_path, cache_updated_at)
    _write(root / "data/cache/quota.json", json.dumps({"providers": providers}))
    if diagnostic is not None:
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(diagnostic),
        )
    return run_ops_check(
        root=root,
        public_base_url=None,
        remote_host=None,
        launch_agent_path=launch_agent,
        local_log_paths=[],
        pre_match_launch_agent_path=None,
        pre_match_log_paths=[],
    )
```

- [ ] **Step 2: Write all behavior tests before production code**

Add these tests after the existing CSL report digest test:

```python
def test_csl_report_prefers_current_cache_and_low_quota_over_stale_diagnostic():
    with TemporaryDirectory() as tmp:
        result = _run_csl_ops_fixture(
            Path(tmp),
            providers={
                "theoddsapi_primary": {"remaining": 0, "last": 3},
                "theoddsapi_secondary": {"remaining": 29, "last": 3},
                "theoddsapi_tertiary": {"remaining": 29, "last": 3},
                "theoddsapi": {"remaining": 29, "last": 3},
            },
            diagnostic={
                "status": "fetched",
                "observed_at": "2026-06-29T02:32:31+00:00",
                "theoddsapi_provider": "theoddsapi_secondary",
                "quota_remaining": 34,
                "quota_last": 3,
            },
        )

    local = result["local"]["csl_live_odds"]
    report = result["report"]["csl_live_odds"]
    assert local["cache_updated_at"] == "2026-08-13T01:25:57+00:00"
    assert local["refresh_diagnostic"]["observed_at"] == "2026-06-29T02:32:31+00:00"
    assert report["observed_at"] == "2026-08-13T01:25:57+00:00"
    assert report["provider"] == "theoddsapi_secondary"
    assert report["quota_remaining"] == 29
    assert report["quota_last"] == 3


def test_csl_report_prefers_first_normal_quota_slot_over_low_slots():
    with TemporaryDirectory() as tmp:
        result = _run_csl_ops_fixture(
            Path(tmp),
            providers={
                "theoddsapi_primary": {"remaining": 0, "last": 3},
                "theoddsapi_secondary": {"remaining": 29, "last": 3},
                "theoddsapi_tertiary": {"remaining": 50, "last": 4},
            },
        )

    report = result["report"]["csl_live_odds"]
    assert report["provider"] == "theoddsapi_tertiary"
    assert report["quota_remaining"] == 50
    assert report["quota_last"] == 4


def test_csl_report_falls_back_only_for_missing_current_fields():
    with TemporaryDirectory() as tmp:
        result = _run_csl_ops_fixture(
            Path(tmp),
            providers={
                "theoddsapi_primary": {"remaining": -1, "last": 99},
                "theoddsapi_secondary": {"remaining": 29},
                "theoddsapi_tertiary": {"remaining": "50", "last": 99},
                "opaque-provider": {"remaining": 100, "secret": "must-not-leak"},
            },
            diagnostic={
                "status": "fetched",
                "observed_at": "2026-06-29T02:32:31+00:00",
                "theoddsapi_provider": "theoddsapi_tertiary",
                "quota_remaining": 34,
                "quota_last": 3,
                "secret": "must-not-leak",
            },
        )

    report = result["report"]["csl_live_odds"]
    assert report["observed_at"] == "2026-08-13T01:25:57+00:00"
    assert report["provider"] == "theoddsapi_secondary"
    assert report["quota_remaining"] == 29
    assert report["quota_last"] == 3
    assert "must-not-leak" not in str(result)
    assert "opaque-provider" not in str(result)


def test_csl_report_uses_diagnostic_quota_only_when_current_quota_is_unusable():
    with TemporaryDirectory() as tmp:
        result = _run_csl_ops_fixture(
            Path(tmp),
            providers={
                "theoddsapi_primary": {"remaining": -1},
                "theoddsapi_secondary": {"remaining": True},
                "theoddsapi_tertiary": {"remaining": 29.0},
            },
            diagnostic={
                "status": "fetched",
                "observed_at": "2026-06-29T02:32:31+00:00",
                "theoddsapi_provider": "theoddsapi_secondary",
                "quota_remaining": 34,
                "quota_last": 3,
            },
        )

    report = result["report"]["csl_live_odds"]
    assert report["observed_at"] == "2026-08-13T01:25:57+00:00"
    assert report["provider"] == "theoddsapi_secondary"
    assert report["quota_remaining"] == 34
    assert report["quota_last"] == 3


def test_csl_report_selects_first_zero_slot_when_all_valid_slots_are_exhausted():
    with TemporaryDirectory() as tmp:
        result = _run_csl_ops_fixture(
            Path(tmp),
            providers={
                "theoddsapi_primary": {"remaining": 0, "last": 3},
                "theoddsapi_secondary": {"remaining": 0, "last": 4},
            },
        )

    report = result["report"]["csl_live_odds"]
    assert report["provider"] == "theoddsapi_primary"
    assert report["quota_remaining"] == 0
    assert report["quota_last"] == 3
```

- [ ] **Step 3: Stabilize existing exact-output fixtures before RED**

In `test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload()` and `test_ops_check_summary_format_prints_daily_csl_digest_without_raw_payload()`, replace the anonymous cache write with:

```python
        cache_path = root / "data/cache/theoddsapi_csl_2026_odds.json"
        _write(cache_path, json.dumps([_csl_live_odds_event()]))
        _set_mtime_utc(cache_path, "2026-06-24T01:51:18+00:00")
```

Keep the report expectation `observed_at == "2026-06-24T01:51:18+00:00"`. Add to the CLI summary assertions:

```python
    assert "observed_at=2026-06-24T01:51:18+00:00" in text
```

These fixture changes are deterministic test setup, not production behavior.

- [ ] **Step 4: Run all new tests and verify RED**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
for name in (
    "test_csl_report_prefers_current_cache_and_low_quota_over_stale_diagnostic",
    "test_csl_report_prefers_first_normal_quota_slot_over_low_slots",
    "test_csl_report_falls_back_only_for_missing_current_fields",
    "test_csl_report_uses_diagnostic_quota_only_when_current_quota_is_unusable",
    "test_csl_report_selects_first_zero_slot_when_all_valid_slots_are_exhausted",
):
    getattr(module, name)()
PY
```

Expected: FAIL first at missing `local["cache_updated_at"]`. This proves the test catches the current stale-diagnostic implementation rather than a test typo.

- [ ] **Step 5: Implement cache commit time and quota selection**

Update `worldcup/ops_check.py` imports and constants:

```python
from datetime import datetime, timezone

from worldcup.theoddsapi_keys import (
    LEGACY_PROVIDER,
    LOW_QUOTA_SWITCH_THRESHOLD,
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    TERTIARY_PROVIDER,
)

CSL_QUOTA_PROVIDER_ORDER = (
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    TERTIARY_PROVIDER,
    LEGACY_PROVIDER,
)
```

Add after `_safe_quota_providers()`:

```python
def _safe_cache_updated_at(path: Path) -> str | None:
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified_at, tz=timezone.utc).isoformat()


def _select_current_quota_provider(
    quota: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    providers = quota.get("providers") if isinstance(quota.get("providers"), dict) else {}

    def candidate(provider: str) -> dict[str, Any] | None:
        entry = providers.get(provider)
        if not isinstance(entry, dict):
            return None
        remaining = entry.get("remaining")
        if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
            return None
        return entry

    for predicate in (
        lambda remaining: remaining > LOW_QUOTA_SWITCH_THRESHOLD,
        lambda remaining: remaining > 0,
        lambda remaining: remaining == 0,
    ):
        for provider in CSL_QUOTA_PROVIDER_ORDER:
            entry = candidate(provider)
            if entry is not None and predicate(entry["remaining"]):
                return provider, entry
    return None
```

In the parser-success branch of `_csl_live_odds_summary()`, build the existing result first and add `cache_updated_at` only when `_safe_cache_updated_at(cache_path)` returns a string. Missing, malformed, and parser-error branches must not gain this field.

- [ ] **Step 6: Make compact report fields current-source-first**

Inside `_report_csl_live_odds()` replace refresh-only provider selection with:

```python
    selected_quota = _select_current_quota_provider(quota)
    if selected_quota is None:
        provider = refresh.get("theoddsapi_provider")
        provider_quota: dict[str, Any] = {}
    else:
        provider, provider_quota = selected_quota
```

Build the four fields with per-field fallback:

```python
        "observed_at": csl.get("cache_updated_at") or refresh.get("observed_at"),
        "provider": provider,
        "quota_remaining": _first_safe_number(
            provider_quota.get("remaining"),
            refresh.get("quota_remaining"),
        ),
        "quota_last": _first_safe_number(
            provider_quota.get("last"),
            refresh.get("quota_last"),
        ),
```

Do not change `_count_issues()`, `_report_csl_issue_codes()`, or `format_ops_report()`.

- [ ] **Step 7: Run all new tests and verify GREEN**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
for name in (
    "test_csl_report_prefers_current_cache_and_low_quota_over_stale_diagnostic",
    "test_csl_report_prefers_first_normal_quota_slot_over_low_slots",
    "test_csl_report_falls_back_only_for_missing_current_fields",
    "test_csl_report_uses_diagnostic_quota_only_when_current_quota_is_unusable",
    "test_csl_report_selects_first_zero_slot_when_all_valid_slots_are_exhausted",
):
    getattr(module, name)()
PY
```

Expected: exit 0 with no output.

- [ ] **Step 8: Run focused existing CLI/report tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path

path = Path("tests/test_ops_check.py")
spec = importlib.util.spec_from_file_location("test_ops_check", path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
module.test_run_ops_check_adds_csl_live_odds_report_digest_without_raw_payload()
module.test_ops_check_summary_format_prints_daily_csl_digest_without_raw_payload()
module.test_run_ops_check_sanitizes_csl_live_whitelisted_values()
PY
```

Expected: exit 0 with no output.

- [ ] **Step 9: Run syntax, whitespace, and full regression**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/ops_check.py tests/test_ops_check.py
git diff --check
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: syntax and diff checks exit 0; all tests pass with only the existing explicitly allowed optional FastAPI skip.

- [ ] **Step 10: Commit the independently green implementation**

```bash
git add worldcup/ops_check.py tests/test_ops_check.py
git diff --cached --check
git diff --cached --name-status
git commit -m "fix: prefer current CSL ops data sources"
```

Expected staged scope: exactly `worldcup/ops_check.py` and `tests/test_ops_check.py`.

---

### Task 2: Verify the real read-only operational contract

**Files:**
- Verify only: `worldcup/ops_check.py` and `tests/test_ops_check.py`
- Read only: `data/cache/theoddsapi_csl_2026_odds.json`
- Read only: `data/cache/quota.json`
- Read only: `data/local/diagnostics/csl_live_odds_refresh.json`

**Interfaces:**
- Consumes: the green Task 1 commit.
- Produces: fresh real-summary evidence and before/after proof that ignored inputs are unchanged.

- [ ] **Step 1: Capture runtime input metadata in terminal output only**

```bash
stat -f '%N %m %z' data/cache/theoddsapi_csl_2026_odds.json data/cache/quota.json data/local/diagnostics/csl_live_odds_refresh.json
shasum -a 256 data/cache/theoddsapi_csl_2026_odds.json data/cache/quota.json data/local/diagnostics/csl_live_odds_refresh.json
```

- [ ] **Step 2: Run the real local-only summary**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.ops_check --no-public --no-remote --format summary
```

Expected on the currently verified state: the CSL line uses the actual cache commit time instead of `2026-06-29` and the current selected quota instead of stale `34`. Existing log warnings may remain; no new issue code is introduced.

- [ ] **Step 3: Prove the real command was read-only**

```bash
stat -f '%N %m %z' data/cache/theoddsapi_csl_2026_odds.json data/cache/quota.json data/local/diagnostics/csl_live_odds_refresh.json
shasum -a 256 data/cache/theoddsapi_csl_2026_odds.json data/cache/quota.json data/local/diagnostics/csl_live_odds_refresh.json
```

Expected: all three mtimes, sizes, and SHA-256 values are identical before and after.

- [ ] **Step 4: Run final full verification**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git status --short --branch
git show --stat --oneline HEAD
git diff HEAD^..HEAD --check
```

Expected: all tests pass with only the allowed optional FastAPI skip; the implementation commit contains only `worldcup/ops_check.py` and `tests/test_ops_check.py`. The plan file may remain uncommitted unless the user separately confirms a documentation commit.

## Adversarial Review Before Execution

- **Root-cause coverage:** RED reproduces the actual 6/29 versus 8/13 and 34 versus 29 mismatch.
- **False-freshness risk:** mtime is explicitly local cache commit time, not bookmaker quote time; no age warning is added.
- **Quota drift risk:** normal, low, zero, invalid, and legacy-order behavior is locked before production implementation.
- **Fallback inversion risk:** tests prove fallback is per field.
- **Security risk:** selection consumes sanitized provider dictionaries; secret/raw-market non-leak assertions remain.
- **Cache corruption risk:** `cache_updated_at` appears only after parser success; malformed cache remains an error.
- **Side-effect risk:** Task 2 stats and hashes ignored inputs before and after the real command.
- **Scope risk:** no scheduler, API, provider, model, database, dependency, launchd, notification, deployment, or README change.
- **RECENT_WORK retention:** `RECENT_WORK.md` already exceeds 20 entries; do not silently archive or append.
- **Rollback:** revert the single implementation commit; no data or service rollback is required.

Review conclusion: every behavior test is RED before production implementation, the only commit is independently full-green, and the real command is verified read-only. No blocking issue remains.
