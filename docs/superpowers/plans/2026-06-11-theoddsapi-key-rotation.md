# The Odds API Key Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative automatic rotation between primary and secondary The Odds API keys based on local quota ledger exhaustion.

**Architecture:** Add a small `worldcup.theoddsapi_keys` module responsible for env slot parsing, quota selection, and provider aliases. Thread the selected provider alias through scheduler reports, scheduled refresh/publish, refresh runner, and The Odds API source quota writes. Keep legacy `THE_ODDS_API_KEY` and `theoddsapi` provider behavior compatible.

**Tech Stack:** Python standard library, existing no-pytest runner.

**Validation command:**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

---

### Task 1: Key Slot Selection Module

**Files:**
- Create: `worldcup/theoddsapi_keys.py`
- Test: `tests/test_theoddsapi_keys.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_theoddsapi_keys.py` with tests for:

```python
from worldcup.theoddsapi_keys import (
    LEGACY_PROVIDER,
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    choose_key_slot,
    quota_remaining_for_scheduler,
)


def test_choose_key_slot_prefers_primary_when_not_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 12}, SECONDARY_PROVIDER: {"remaining": 497}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "primary"
    assert selected.provider == PRIMARY_PROVIDER
    assert selected.slot == "primary"


def test_choose_key_slot_uses_legacy_key_as_primary():
    env = {"THE_ODDS_API_KEY": "legacy-primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 12}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "legacy-primary"
    assert selected.provider == PRIMARY_PROVIDER


def test_choose_key_slot_rotates_to_secondary_when_primary_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 0}, SECONDARY_PROVIDER: {"remaining": 497}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "secondary"
    assert selected.provider == SECONDARY_PROVIDER
    assert selected.slot == "secondary"


def test_choose_key_slot_returns_none_when_all_configured_slots_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 0}, SECONDARY_PROVIDER: {"remaining": 0}}

    assert choose_key_slot(env, providers) is None


def test_unknown_remaining_is_usable():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary"}
    providers = {PRIMARY_PROVIDER: {"remaining": None}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.provider == PRIMARY_PROVIDER


def test_quota_remaining_for_scheduler_uses_selected_slot():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 0}, SECONDARY_PROVIDER: {"remaining": 42}, LEGACY_PROVIDER: {"remaining": 0}}

    assert quota_remaining_for_scheduler(providers, env) == 42


def test_quota_remaining_for_scheduler_returns_zero_when_all_configured_slots_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 0}, SECONDARY_PROVIDER: {"remaining": 0}, LEGACY_PROVIDER: {"remaining": 497}}

    assert quota_remaining_for_scheduler(providers, env) == 0


def test_quota_remaining_for_scheduler_falls_back_to_legacy_without_slots():
    assert quota_remaining_for_scheduler({LEGACY_PROVIDER: {"remaining": 17}}, {}) == 17
```

- [ ] **Step 2: Run and confirm RED**

Run the validation command. Expected: `FAIL test_theoddsapi_keys.py` because the module does not exist.

- [ ] **Step 3: Minimal implementation**

Create `worldcup/theoddsapi_keys.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

LEGACY_PROVIDER = "theoddsapi"
PRIMARY_PROVIDER = "theoddsapi_primary"
SECONDARY_PROVIDER = "theoddsapi_secondary"


@dataclass(frozen=True)
class KeySlotSelection:
    api_key: str
    provider: str
    slot: str


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def configured_key_slots(env: Mapping[str, str]) -> tuple[KeySlotSelection, ...]:
    primary = _clean(env.get("THE_ODDS_API_KEY_PRIMARY")) or _clean(env.get("THE_ODDS_API_KEY"))
    secondary = _clean(env.get("THE_ODDS_API_KEY_SECONDARY"))
    slots: list[KeySlotSelection] = []
    if primary:
        slots.append(KeySlotSelection(primary, PRIMARY_PROVIDER, "primary"))
    if secondary:
        slots.append(KeySlotSelection(secondary, SECONDARY_PROVIDER, "secondary"))
    return tuple(slots)


def _remaining(entry: Any) -> int | None:
    if not isinstance(entry, Mapping):
        return None
    value = entry.get("remaining")
    return value if isinstance(value, int) else None


def _is_exhausted(entry: Any) -> bool:
    remaining = _remaining(entry)
    return remaining is not None and remaining <= 0


def choose_key_slot(env: Mapping[str, str], providers: Mapping[str, Any]) -> KeySlotSelection | None:
    slots = configured_key_slots(env)
    if not slots:
        return None
    for slot in slots:
        if not _is_exhausted(providers.get(slot.provider)):
            return slot
    return None


def quota_remaining_for_scheduler(providers: Mapping[str, Any], env: Mapping[str, str] | None = None) -> int | None:
    env = env or {}
    slots = configured_key_slots(env)
    if slots:
        selected = choose_key_slot(env, providers)
        if selected is None:
            return 0
        return _remaining(providers.get(selected.provider))
    return _remaining(providers.get(LEGACY_PROVIDER))
```

- [ ] **Step 4: Run and confirm GREEN**

Run the validation command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/theoddsapi_keys.py tests/test_theoddsapi_keys.py
git commit -m "feat: select theoddsapi key slots"
```

---

### Task 2: Slot-Aware Scheduler And Scheduled Refresh

**Files:**
- Modify: `worldcup/scheduler.py`
- Modify: `worldcup/scheduled_refresh.py`
- Modify: `worldcup/scheduled_publish.py`
- Test: `tests/test_scheduler.py`
- Test: `tests/test_scheduled_refresh.py`
- Test: `tests/test_scheduled_publish.py`

- [ ] **Step 1: Write failing tests**

Add scheduler tests that pass `env={"THE_ODDS_API_KEY_PRIMARY": "p", "THE_ODDS_API_KEY_SECONDARY": "s"}` to `build_scheduler_report` and verify primary exhausted plus secondary remaining schedules normally, while both exhausted returns `quota_exhausted`.

Add a scheduled refresh test where `.env` contains primary and secondary, quota has primary `0` and secondary `497`, and `refresh_fn` asserts it receives `api_key == "secondary-key"` and `theoddsapi_provider == "theoddsapi_secondary"`.

Update the existing scheduled publish due test to verify it does not pre-resolve `THE_ODDS_API_KEY` and lets scheduled refresh choose the slot when env has primary/secondary.

- [ ] **Step 2: Run and confirm RED**

Run the validation command. Expected: failures because `build_scheduler_report` has no `env` parameter and refresh does not pass `theoddsapi_provider`.

- [ ] **Step 3: Minimal implementation**

In `worldcup/scheduler.py`, import `quota_remaining_for_scheduler`, add optional `env: dict[str, str] | None = None` to `build_scheduler_report`, and replace direct legacy quota extraction with:

```python
quota_remaining = quota_remaining_for_scheduler(quota, env)
```

In `worldcup/scheduled_refresh.py`, load env once before building the report, pass env to `build_scheduler_report`, choose slot with `choose_key_slot`, and pass `theoddsapi_provider=selected.provider` into `refresh_fn`. Explicit `api_key` keeps legacy provider `theoddsapi`.

In `worldcup/scheduled_publish.py`, stop resolving `THE_ODDS_API_KEY` before `run_scheduled_refresh`; pass through explicit `api_key` only.

- [ ] **Step 4: Run and confirm GREEN**

Run the validation command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/scheduler.py worldcup/scheduled_refresh.py worldcup/scheduled_publish.py tests/test_scheduler.py tests/test_scheduled_refresh.py tests/test_scheduled_publish.py
git commit -m "feat: rotate scheduled refresh key by quota"
```

---

### Task 3: Slot-Aware Quota Writes In Refresh Source

**Files:**
- Modify: `worldcup/sources/theoddsapi.py`
- Modify: `worldcup/refresh_runner.py`
- Test: `tests/sources/test_theoddsapi_source.py`
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: Write failing tests**

Extend `test_fetch_worldcup_odds_uses_transport_and_writes_cache_and_quota` to call `fetch_worldcup_odds(..., quota_provider="theoddsapi_secondary")` and assert both `theoddsapi_secondary` and legacy `theoddsapi` entries exist with `remaining == 497`.

Extend refresh runner injected transport test to pass `theoddsapi_provider="theoddsapi_secondary"` and assert run metadata quota contains `theoddsapi_secondary`.

- [ ] **Step 2: Run and confirm RED**

Run the validation command. Expected: failures because `quota_provider` and `theoddsapi_provider` are unsupported.

- [ ] **Step 3: Minimal implementation**

In `fetch_worldcup_odds`, add `quota_provider: str = "theoddsapi"` and update the selected provider. If selected provider is not legacy, also update legacy `theoddsapi` with the same headers.

In `refresh_cache_and_build_snapshot`, add `theoddsapi_provider: str = "theoddsapi"`, pass it to `fetch_worldcup_odds`, and use selected provider remaining for the run decision.

- [ ] **Step 4: Run and confirm GREEN**

Run the validation command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add worldcup/sources/theoddsapi.py worldcup/refresh_runner.py tests/sources/test_theoddsapi_source.py tests/test_refresh_runner.py
git commit -m "feat: record quota by theoddsapi key slot"
```

---

### Task 4: Env Template, Docs, Recent Work

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`
- Test: `tests/test_readiness.py`

- [ ] **Step 1: Write/update tests**

Update readiness `.env.example` fixtures to include:

```text
THE_ODDS_API_KEY_PRIMARY=
THE_ODDS_API_KEY_SECONDARY=
```

Keep `THE_ODDS_API_KEY=` for compatibility.

- [ ] **Step 2: Update docs**

Update `.env.example` with empty variable names only. Add README note that two-key rotation is conservative and slot-aware; do not include real key values.

Add a top RECENT_WORK section: automatic key rotation implemented, slot vars, legacy compatibility, no live refresh, no key values committed.

- [ ] **Step 3: Final verification**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: all tests pass and `git diff --check` has no output.

- [ ] **Step 4: Commit**

```bash
git add .env.example README.md RECENT_WORK.md tests/test_readiness.py
git commit -m "docs: document theoddsapi key rotation"
```

---

## Scope Guard

- Do not write real API keys to repository files.
- Do not edit `.env` in commits.
- Do not push, deploy, or run live refresh.
- Do not add request-failure fallback to the secondary key.
