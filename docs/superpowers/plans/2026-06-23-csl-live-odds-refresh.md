# CSL Live Odds Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guarded CSL live odds refresh path that can fetch real The Odds API CSL odds into local cache only after explicit live confirmation, then run the existing CSL league runner against that real cache.

**Architecture:** First generalize the existing The Odds API odds source so it can fetch any configured sport key while preserving the World Cup wrapper. Then add a small `worldcup.league_odds_refresh` CLI that is dry-run by default, requires an explicit CSL sport key when the competition registry has no pinned key, selects a quota-aware key slot without printing secrets, and refuses to overwrite an existing odds cache unless the operator explicitly opts in. The final live fetch and runner smoke are separate execution steps that require user confirmation because they consume quota.

**Tech Stack:** Python standard library, existing `worldcup.sources.theoddsapi`, existing `worldcup.theoddsapi_keys`, existing `worldcup.quota`, existing `worldcup.league_runner`, ignored `data/cache/` and `data/local/diagnostics/`, current `tests/run_tests.py`.

---

## Scope And Safety

P9.8 follows P9.7. The synthetic odds file has been moved out of the default runner path, so `data/cache/theoddsapi_csl_2026_odds.json` should be absent before live execution.

This plan has two phases:

- Implementation phase: code and tests only, no network, no `.env` read in dry-run tests, no quota use.
- Live phase: only after a separate user confirmation such as `确认 live` or `执行 live` for this exact plan.

Do not run the live command while writing or implementing the code phase unless the user gives that separate confirmation.

Do not print API keys, `.env` values, HMAC secrets, cookies, quota file contents beyond provider names and numeric quota entries, or raw request URLs containing `apiKey=`.

Do not deploy, publish, write ECS, update LaunchAgent, push, or lift `club_rating_pending`.

This work supports research diagnostics only. It is not betting advice, does not output stake sizes, and does not recommend chasing, heavy positions, parlays, or execution actions.

## File Structure

Create or modify tracked files:

- Modify: `worldcup/sources/theoddsapi.py`
- Create: `worldcup/league_odds_refresh.py`
- Modify: `tests/sources/test_theoddsapi_source.py`
- Create: `tests/test_league_odds_refresh.py`
- Modify: `RECENT_WORK.md` after verification

Create or modify ignored local files only during the live phase:

- Create: `data/cache/theoddsapi_csl_2026_odds.json`
- Update: `data/cache/quota.json`
- Create: `data/local/diagnostics/csl_live_odds_refresh.json`
- Create: `data/local/diagnostics/csl_live_league_runner_check.json`
- Optional create: `data/local/diagnostics/csl_live_league_snapshot.json`

Do not write live odds payloads under `docs/`, `tests/`, `data/probe/`, or any tracked path.

## Task 1: Generalize The Odds API Odds Source

**Files:**
- Modify: `worldcup/sources/theoddsapi.py`
- Modify: `tests/sources/test_theoddsapi_source.py`

- [ ] **Step 1: Add failing tests for a generic sport odds URL and fetch**

Append these tests to `tests/sources/test_theoddsapi_source.py`:

```python
def test_build_odds_url_accepts_custom_sport_key_without_logging_secret():
    from worldcup.sources.theoddsapi import build_odds_url

    url = build_odds_url(
        sport_key="soccer_china_superleague",
        api_key="fake-key",
        regions="eu",
        markets=("h2h", "spreads", "totals"),
    )

    assert "sports/soccer_china_superleague/odds" in url
    assert "markets=h2h%2Cspreads%2Ctotals" in url
    assert "oddsFormat=decimal" in url
    assert "dateFormat=iso" in url
    assert "apiKey=fake-key" in url


def test_fetch_odds_for_sport_writes_csl_cache_and_slot_quota():
    from worldcup.sources.theoddsapi import fetch_odds_for_sport

    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_csl_2026_odds.json"
        quota_path = Path(tmp) / "quota.json"

        result = fetch_odds_for_sport(
            api_key="fake-key",
            sport_key="soccer_china_superleague",
            transport=fake_transport,
            cache_path=cache_path,
            quota_path=quota_path,
            observed_at="2026-06-23T12:00:00+00:00",
            quota_provider=SECONDARY_PROVIDER,
        )

        assert "soccer_china_superleague/odds" in seen["url"]
        assert "apiKey=fake-key" in seen["url"]
        assert result.status == 200
        assert result.json_body == [{"id": "event-1"}]
        assert json.loads(cache_path.read_text()) == [{"id": "event-1"}]
        quota = json.loads(quota_path.read_text())
        assert quota["providers"][SECONDARY_PROVIDER]["remaining"] == 497
        assert quota["providers"][LEGACY_PROVIDER]["remaining"] == 497
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/sources/test_theoddsapi_source.py -v
```

Expected before implementation: the new tests fail because `build_odds_url` and `fetch_odds_for_sport` do not exist.

- [ ] **Step 2: Implement generic odds URL and fetch while preserving wrappers**

Edit `worldcup/sources/theoddsapi.py` so the public World Cup functions remain compatible and the new generic functions are available:

```python
def build_odds_url(
    sport_key: str,
    api_key: str,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> str:
    params = {
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": odds_format,
        "dateFormat": date_format,
        "apiKey": api_key,
    }
    return f"{BASE_URL}/sports/{sport_key}/odds?{urlencode(params)}"
```

Change `build_worldcup_odds_url()` to delegate:

```python
def build_worldcup_odds_url(
    api_key: str,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> str:
    return build_odds_url(
        sport_key=WORLD_CUP_SPORT_KEY,
        api_key=api_key,
        regions=regions,
        markets=markets,
        odds_format=odds_format,
        date_format=date_format,
    )
```

Add the generic fetch function:

```python
def fetch_odds_for_sport(
    api_key: str,
    sport_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    quota_provider: str = LEGACY_PROVIDER,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
) -> SourceFetchResult:
    url = build_odds_url(
        sport_key=sport_key,
        api_key=api_key,
        regions=regions,
        markets=markets,
    )
    response = (transport or _default_transport)(url)
    body = response.read()
    json_body = json.loads(body.decode("utf-8"))
    headers = dict(getattr(response, "headers", {}))

    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)

    quota_entry = None
    if quota_path is not None:
        quota_entry = update_quota_from_headers(
            quota_path,
            quota_provider,
            headers,
            estimated_last=len(markets),
            observed_at=observed_at,
        )
        if quota_provider != LEGACY_PROVIDER:
            update_quota_from_headers(
                quota_path,
                LEGACY_PROVIDER,
                headers,
                estimated_last=len(markets),
                observed_at=observed_at,
            )

    return SourceFetchResult(
        status=int(getattr(response, "status", 200)),
        json_body=json_body,
        headers=headers,
        cache_path=written_cache_path,
        quota_entry=quota_entry,
    )
```

Change `fetch_worldcup_odds()` to delegate to `fetch_odds_for_sport()`:

```python
def fetch_worldcup_odds(
    api_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    quota_provider: str = LEGACY_PROVIDER,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
) -> SourceFetchResult:
    return fetch_odds_for_sport(
        api_key=api_key,
        sport_key=WORLD_CUP_SPORT_KEY,
        transport=transport,
        cache_path=cache_path,
        quota_path=quota_path,
        observed_at=observed_at,
        quota_provider=quota_provider,
        regions=regions,
        markets=markets,
    )
```

- [ ] **Step 3: Verify source tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/sources/test_theoddsapi_source.py -v
```

Expected: all `tests/sources/test_theoddsapi_source.py` tests pass, including existing World Cup wrapper tests.

## Task 2: Add Guarded League Odds Refresh CLI

**Files:**
- Create: `worldcup/league_odds_refresh.py`
- Create: `tests/test_league_odds_refresh.py`

- [ ] **Step 1: Write failing CLI and runner-unit tests**

Create `tests/test_league_odds_refresh.py`:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_odds_refresh import resolve_sport_key, run_league_odds_refresh


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "3",
        "x-requests-remaining": "497",
        "x-requests-last": "3",
    }

    def read(self):
        return b'[{"id":"csl-event-1","sport_key":"soccer_china_superleague"}]'


def test_resolve_sport_key_requires_explicit_key_for_csl_candidates():
    try:
        resolve_sport_key("csl_2026")
    except ValueError as exc:
        assert str(exc) == "sport_key_required: csl_2026"
    else:
        raise AssertionError("expected sport_key_required")

    assert resolve_sport_key("csl_2026", "soccer_china_superleague") == "soccer_china_superleague"
    assert resolve_sport_key("fifa_world_cup_2026") == "soccer_fifa_world_cup"


def test_league_odds_refresh_dry_run_does_not_read_env_or_write_cache():
    called = {"transport": False}

    def fake_transport(_url):
        called["transport"] = True
        raise AssertionError("dry-run must not call transport")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_odds_refresh(
            live=False,
            env={},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            transport=fake_transport,
            observed_at="2026-06-23T12:00:00+00:00",
        )

        assert result["status"] == "dry_run"
        assert result["competition_id"] == "csl_2026"
        assert result["sport_key"] == "soccer_china_superleague"
        assert result["target_cache_path"].endswith("theoddsapi_csl_2026_odds.json")
        assert result["cache_exists"] is False
        assert called["transport"] is False
        assert not (root / "cache" / "theoddsapi_csl_2026_odds.json").exists()


def test_league_odds_refresh_blocks_existing_cache_without_replace():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = root / "cache" / "theoddsapi_csl_2026_odds.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("[]", encoding="utf-8")

        result = run_league_odds_refresh(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "primary-key"},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            observed_at="2026-06-23T12:00:00+00:00",
        )

        assert result == {
            "status": "blocked",
            "reason": "existing_cache",
            "target_cache_path": str(cache_path),
        }


def test_league_odds_refresh_live_writes_cache_and_quota_without_returning_key():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_odds_refresh(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "primary-key"},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            transport=fake_transport,
            observed_at="2026-06-23T12:00:00+00:00",
        )

        cache_path = root / "cache" / "theoddsapi_csl_2026_odds.json"
        assert "soccer_china_superleague/odds" in seen["url"]
        assert "apiKey=primary-key" in seen["url"]
        assert result["status"] == "fetched"
        assert result["events"] == 1
        assert result["cache_path"] == str(cache_path)
        assert result["slot"] == "primary"
        assert result["theoddsapi_provider"] == "theoddsapi_primary"
        assert "primary-key" not in json.dumps(result)
        assert json.loads(cache_path.read_text())[0]["id"] == "csl-event-1"
        quota = json.loads((root / "cache" / "quota.json").read_text())
        assert quota["providers"]["theoddsapi_primary"]["remaining"] == 497
        assert quota["providers"]["theoddsapi"]["remaining"] == 497
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_league_odds_refresh.py -v
```

Expected before implementation: fails because `worldcup.league_odds_refresh` does not exist.

- [ ] **Step 2: Implement `worldcup.league_odds_refresh`**

Create `worldcup/league_odds_refresh.py`:

```python
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from worldcup.competitions import get_competition
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.sources.theoddsapi import DEFAULT_MARKETS, fetch_odds_for_sport
from worldcup.theoddsapi_keys import choose_key_slot


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_name(competition_id: str) -> str:
    return f"theoddsapi_{competition_id}_odds.json"


def resolve_sport_key(competition_id: str, sport_key: str | None = None) -> str:
    competition = get_competition(competition_id)
    if sport_key:
        return sport_key
    if competition.theoddsapi_sport_key:
        return competition.theoddsapi_sport_key
    if len(competition.theoddsapi_candidate_keys) == 1:
        return competition.theoddsapi_candidate_keys[0]
    raise ValueError(f"sport_key_required: {competition_id}")


def run_league_odds_refresh(
    live: bool,
    env: dict[str, str],
    competition_id: str = "csl_2026",
    sport_key: str | None = None,
    cache_dir: str | Path = "data/cache",
    quota_path: str | Path = "data/cache/quota.json",
    replace_existing: bool = False,
    transport: Callable[[str], object] | None = None,
    observed_at: str | None = None,
) -> dict:
    resolved_sport_key = resolve_sport_key(competition_id, sport_key)
    cache_path = Path(cache_dir) / _cache_name(competition_id)
    observed = observed_at or _now_utc_iso()
    base = {
        "competition_id": competition_id,
        "sport_key": resolved_sport_key,
        "target_cache_path": str(cache_path),
        "cache_exists": cache_path.exists(),
        "live": live,
    }

    if not live:
        return {
            "status": "dry_run",
            "note": "pass --live after explicit user confirmation to fetch real odds and consume quota",
            **base,
        }

    if cache_path.exists() and not replace_existing:
        return {
            "status": "blocked",
            "reason": "existing_cache",
            "target_cache_path": str(cache_path),
        }

    providers = load_quota_ledger(quota_path).get("providers", {})
    selected = choose_key_slot(env, providers)
    if selected is None:
        return {
            "status": "blocked",
            "reason": "missing_or_exhausted_key",
            **base,
        }

    fetch_result = fetch_odds_for_sport(
        api_key=selected.api_key,
        sport_key=resolved_sport_key,
        transport=transport,
        cache_path=cache_path,
        quota_path=quota_path,
        observed_at=observed,
        quota_provider=selected.provider,
        markets=DEFAULT_MARKETS,
    )
    return {
        "status": "fetched",
        "competition_id": competition_id,
        "sport_key": resolved_sport_key,
        "cache_path": str(cache_path),
        "events": len(fetch_result.json_body) if isinstance(fetch_result.json_body, list) else 0,
        "slot": selected.slot,
        "theoddsapi_provider": selected.provider,
        "quota_entry": fetch_result.quota_entry,
        "observed_at": observed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch league odds from The Odds API, defaulting to dry-run.")
    parser.add_argument("--competition", "--competition-id", dest="competition_id", default="csl_2026")
    parser.add_argument("--sport-key", default=None)
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--observed-at", default=None)
    parser.add_argument("--live", action="store_true", help="Fetch real odds and consume The Odds API quota.")
    parser.add_argument("--replace-existing", action="store_true", help="Allow overwriting an existing odds cache.")
    args = parser.parse_args(argv)

    env = _load_env(args.env) if args.live else {}
    result = run_league_odds_refresh(
        live=args.live,
        env=env,
        competition_id=args.competition_id,
        sport_key=args.sport_key,
        cache_dir=args.cache_dir,
        quota_path=args.quota_path,
        replace_existing=args.replace_existing,
        observed_at=args.observed_at,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Verify CLI tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/test_league_odds_refresh.py -v
```

Expected: all `tests/test_league_odds_refresh.py` tests pass.

## Task 3: Dry-Run The CSL Refresh Command Without Network

**Files:**
- Read: `data/cache/theoddsapi_csl_2026_odds.json`
- Read: `data/local/diagnostics/theoddsapi_csl_2026_odds.synthetic_smoke.json`

- [ ] **Step 1: Confirm default CSL odds cache is absent**

Run:

```bash
test ! -f data/cache/theoddsapi_csl_2026_odds.json
```

Expected: exit code `0`. If this fails, stop and inspect the file before continuing.

- [ ] **Step 2: Confirm synthetic backup remains outside the default path**

Run:

```bash
test -f data/local/diagnostics/theoddsapi_csl_2026_odds.synthetic_smoke.json
rg -n '"_synthetic_smoke": true|"Local wiring smoke only; not real odds."' data/local/diagnostics/theoddsapi_csl_2026_odds.synthetic_smoke.json
```

Expected: backup exists and has both marker lines.

- [ ] **Step 3: Run dry-run CLI**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_odds_refresh --competition csl_2026 --sport-key soccer_china_superleague --cache-dir data/cache --quota-path data/cache/quota.json --observed-at 2026-06-23T12:00:00+00:00
```

Expected JSON:

```json
{
  "cache_exists": false,
  "competition_id": "csl_2026",
  "live": false,
  "note": "pass --live after explicit user confirmation to fetch real odds and consume quota",
  "sport_key": "soccer_china_superleague",
  "status": "dry_run",
  "target_cache_path": "data/cache/theoddsapi_csl_2026_odds.json"
}
```

This command must not read `.env`, must not call The Odds API, and must not create `data/cache/theoddsapi_csl_2026_odds.json`.

## Task 4: Separately Confirm And Execute Live CSL Odds Fetch

**Files:**
- Create: `data/cache/theoddsapi_csl_2026_odds.json`
- Update: `data/cache/quota.json`
- Create: `data/local/diagnostics/csl_live_odds_refresh.json`

This task requires a separate user confirmation after Tasks 1-3 pass. Stop and ask:

```text
Ready for P9.8 live fetch. This will read .env and call The Odds API for csl_2026 using sport_key=soccer_china_superleague, likely consuming 3 credits. Reply 确认 live to proceed.
```

Do not continue until the user confirms.

- [ ] **Step 1: Recheck default cache is absent immediately before live**

Run:

```bash
test ! -f data/cache/theoddsapi_csl_2026_odds.json
```

Expected: exit code `0`.

- [ ] **Step 2: Execute live fetch after confirmation**

Run only after user confirms:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_odds_refresh --competition csl_2026 --sport-key soccer_china_superleague --cache-dir data/cache --quota-path data/cache/quota.json --observed-at 2026-06-23T12:00:00+00:00 --live
```

Expected:

- status is `fetched`
- output does not include an API key
- `cache_path` is `data/cache/theoddsapi_csl_2026_odds.json`
- `sport_key` is `soccer_china_superleague`
- `quota_entry.remaining` is present as a number or null

If the command returns `blocked`, stop and report the reason.

- [ ] **Step 3: Persist a safe live-fetch diagnostic**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

cache_path = Path("data/cache/theoddsapi_csl_2026_odds.json")
payload = json.loads(cache_path.read_text(encoding="utf-8"))
summary = {
    "status": "cache_present",
    "cache_path": str(cache_path),
    "events": len(payload) if isinstance(payload, list) else 0,
    "has_synthetic_marker": any(
        isinstance(item, dict) and item.get("_synthetic_smoke") is True
        for item in payload
    ) if isinstance(payload, list) else False,
}
out = Path("data/local/diagnostics/csl_live_odds_refresh.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
assert summary["has_synthetic_marker"] is False
PY
```

Expected: diagnostic contains no raw odds rows and confirms the live cache is not synthetic.

## Task 5: Run League Runner Against Real CSL Odds Cache

**Files:**
- Read: `data/cache/theoddsapi_csl_2026_odds.json`
- Read: `data/cache/club_results_csl_2026.csv`
- Create: `data/local/diagnostics/csl_live_league_snapshot.json`
- Create: `data/local/diagnostics/csl_live_league_runner_check.json`

- [ ] **Step 1: Run runner to diagnostics path**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/local/diagnostics/csl_live_league_snapshot.json --club-rating-min-matches 300 --snapshot-at 2026-06-23T12:00:00+00:00
```

Expected: exits `0`. The printed match count may be `0` if The Odds API has no current CSL odds; that is a valid fetch result but not a signal-quality proof.

- [ ] **Step 2: Inspect runner output**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

snapshot = json.loads(Path("data/local/diagnostics/csl_live_league_snapshot.json").read_text(encoding="utf-8"))
quality = snapshot["data_quality"]["club_rating"]
warnings = set(snapshot["data_quality"].get("warnings", []))
matches = snapshot.get("matches", [])
signals = [signal for match in matches for signal in match.get("signals", [])]
strong_grades = [signal.get("grade") for signal in signals if signal.get("grade") in {"S", "A"}]
summary = {
    "snapshot_at": snapshot["snapshot_at"],
    "competition_id": snapshot["competition"]["id"],
    "rating_policy": snapshot["competition"]["rating_policy"],
    "counts": snapshot["counts"],
    "fixture_source": snapshot["data_quality"]["fixture_source"],
    "club_alias_unmatched": snapshot["data_quality"]["club_alias_unmatched"],
    "invalid_odds_count": snapshot["data_quality"]["invalid_odds_count"],
    "warnings": sorted(warnings),
    "club_rating": quality,
    "signals": len(signals),
    "strong_grades": strong_grades,
}
out = Path("data/local/diagnostics/csl_live_league_runner_check.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

assert snapshot["competition"]["id"] == "csl_2026"
assert snapshot["competition"]["rating_policy"] == "club_rating_pending"
assert quality["mode"] == "sample_replay"
assert quality["matches_replayed"] == 840
assert quality["teams_rated"] == 22
assert quality["sample_too_small"] is False
assert quality["errors"] == []
assert "club_rating_pending" in warnings
assert "club_rating_sample_too_small" not in warnings
assert "club_rating_missing" not in warnings
assert strong_grades == []
PY
```

Expected: `club_rating_pending` still caps final S/A grades. If `counts.matches` is `0`, record that real fetch succeeded but there were no current CSL events in the returned odds payload.

## Task 6: Full Verification And Recent Work

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run the full suite**

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

Expected tracked changes are limited to the source/test files listed in this plan and `RECENT_WORK.md`. Ignored live cache and diagnostics must not appear as tracked changes.

- [ ] **Step 3: Update recent work**

Add a top entry to `RECENT_WORK.md` with:

- whether code-only implementation completed
- whether live fetch was executed or still waiting for confirmation
- sport key used: `soccer_china_superleague`
- live cache path if executed
- quota provider and remaining if executed, without API key
- runner result if executed: match count, club-rating quality, warnings, strong-grade cap
- explicit safety notes: no API key printed, no `.env` values printed, no deploy, no LaunchAgent, no push, no `rating_policy` change

Do not trim older `RECENT_WORK.md` entries unless the user separately approves archival or compression.

## Task 7: Final Report And Commit Gate

- [ ] **Step 1: Summarize result**

Report:

- code changes
- dry-run result
- live result or live wait state
- real cache path status
- runner smoke status
- verification results
- tracked file diff summary

- [ ] **Step 2: Ask before commit**

Do not commit automatically unless the user confirms. If the user asks to commit after clean verification, stage only tracked source, tests, plan, and recent work files. Do not stage `data/cache/` or `data/local/`.

Recommended commit messages:

```bash
git commit -m "Add guarded CSL odds refresh"
```

If only this plan and `RECENT_WORK.md` were written in the current turn, use:

```bash
git commit -m "Plan CSL live odds refresh"
```

## Adversarial Self-Review

- Existing `fetch_worldcup_odds()` is World Cup-specific. P9.8 must not fake CSL by calling the World Cup endpoint.
- CSL config has candidate sport keys but no pinned `theoddsapi_sport_key`. Live CLI must require explicit `--sport-key soccer_china_superleague` until a separate plan pins the key in `worldcup/competitions.py`.
- Live fetch may return an empty list if The Odds API has no current CSL odds. That is a real source result, not a model failure, and must be recorded separately from runner quality.
- The live command reads `.env` and consumes quota. It requires separate confirmation after code-only dry-run passes.
- The default cache path must be absent or explicitly replaced before live fetch. Do not overwrite unknown existing odds cache silently.
- The runner output must go to diagnostics for P9.8 smoke, not the public preview/export path.
- `club_rating_pending` remains active. P9.8 must not change `worldcup/competitions.py` or present CSL S/A output as production-ready.
