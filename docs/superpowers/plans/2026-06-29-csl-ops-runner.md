# CSL Ops Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `worldcup.csl_ops_runner`, a local CSL practical-loop command that safely inspects CSL state, optionally builds and archives local snapshots, writes observation reports, and optionally runs postmatch evaluation.

**Architecture:** Add one thin orchestration module that reuses existing CSL modules instead of duplicating domain logic. Default execution is offline dry-run; writing artifacts requires `--run-local`; The Odds API refresh requires explicit `--live-odds` and stays inside the existing `league_odds_refresh` safe fetch boundary. Output is a sanitized JSON summary with step statuses and safety flags.

**Tech Stack:** Python standard library, existing `worldcup` modules, local `tests/run_tests.py` runner.

**Implementation status:** Completed on 2026-06-29. The final dry-run implementation intentionally uses narrow local file inspection instead of the earlier draft `ops_check` dependency; this keeps default execution fully offline and avoids broad operational state reads. Final verification: `python3 -m unittest tests.test_csl_ops_runner -v` passed `20/20`; `python3 -m py_compile worldcup/csl_ops_runner.py` passed; project `tests/run_tests.py` passed `648/648`.

---

## Scope

- Do not tune model parameters, thresholds, grades, candidate rules, or probability math.
- Do not lift `club_rating_pending`.
- Do not publish, deploy, change LaunchAgent, or touch ECS.
- Do not call The Odds API in default dry-run or `--run-local` mode.
- Do not read `.env` unless `--live-odds` is explicitly passed.
- Keep generated artifacts under ignored `data/local/` or `data/cache/`.
- Keep summaries free of raw bookmaker rows, per-book prices, API keys, env values, HMAC secrets, request headers, and stake/betting instructions.

## Files

- Create: `worldcup/csl_ops_runner.py`
- Create: `tests/test_csl_ops_runner.py`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

## Task 1: Add Dry-Run And Safety Tests

**Files:**
- Create: `tests/test_csl_ops_runner.py`
- Create later: `worldcup/csl_ops_runner.py`

- [ ] **Step 1: Write failing dry-run tests**

Create `tests/test_csl_ops_runner.py`:

```python
from __future__ import annotations

import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _write_csl_odds_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    bookmakers = [
        {
            "key": "must-not-leak",
            "last_update": "2026-07-03T08:00:00Z",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Shanghai Port", "price": 2.35},
                        {"name": "Shandong Taishan", "price": 3.2},
                        {"name": "Draw", "price": 3.4},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "price": 1.95, "point": 2.5},
                        {"name": "Under", "price": 1.9, "point": 2.5},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Shanghai Port", "price": 1.92, "point": -0.5},
                        {"name": "Shandong Taishan", "price": 1.94, "point": 0.5},
                    ],
                },
            ],
        }
    ]
    (cache_dir / "theoddsapi_csl_2026_odds.json").write_text(
        json.dumps(
            [
                {
                    "id": "csl-event-1",
                    "sport_key": "soccer_china_superleague",
                    "commence_time": "2026-07-03T11:35:00Z",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "bookmakers": bookmakers,
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_results(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with (cache_dir / "club_results_csl_2026.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-07-03",
                "home_team": "Shanghai Port",
                "away_team": "Shandong Taishan",
                "home_score": "2",
                "away_score": "1",
                "neutral": "0",
            }
        )


def test_dry_run_reads_local_state_without_writing_or_loading_env():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        def fail_load_env(path: str) -> dict[str, str]:
            raise AssertionError("dry-run must not load env")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            load_env=fail_load_env,
        )

        assert summary["status"] in {"ok", "warn"}
        assert summary["mode"] == "dry_run"
        assert summary["competition_id"] == "csl_2026"
        assert summary["safety"] == {
            "read_env": False,
            "called_theoddsapi": False,
            "published": False,
            "deployed": False,
            "changed_launch_agent": False,
        }
        assert summary["steps"]["local_state"]["status"] in {"ok", "warn"}
        assert summary["steps"]["local_state"]["cache_exists"] is True
        assert not (root / "data/local/diagnostics/csl_live_league_snapshot.json").exists()
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()


def test_dry_run_summary_does_not_expose_raw_market_or_secret_text():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_csl_odds_cache(root / "data/cache")
        summary = runner.run_csl_ops(root=root, generated_at="2026-07-03T09:00:00Z")
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "must-not-leak",
            "bookmaker",
            "api_key",
            "secret",
            "HMAC",
            "下注金额",
            "stake amount",
        ):
            assert forbidden.lower() not in serialized.lower()


def test_cli_default_prints_dry_run_summary():
    from worldcup.csl_ops_runner import main

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_csl_odds_cache(root / "data/cache")
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = main(["--root", str(root), "--generated-at", "2026-07-03T09:00:00Z"])

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["mode"] == "dry_run"
        assert payload["safety"]["read_env"] is False
        assert payload["safety"]["called_theoddsapi"] is False
```

- [ ] **Step 2: Run tests to verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: fail with `ModuleNotFoundError: No module named 'worldcup.csl_ops_runner'`.

## Task 2: Implement Dry-Run Runner Core

**Files:**
- Create: `worldcup/csl_ops_runner.py`
- Test: `tests/test_csl_ops_runner.py`

- [ ] **Step 1: Add module constants and helpers**

Create `worldcup/csl_ops_runner.py`:

```python
"""Local CSL operations runner.

Orchestrates CSL cache inspection, local snapshot generation, archive, reports,
and optional postmatch evaluation. Defaults to offline dry-run.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.csl_observation_report import (
    build_observation_report,
    default_report_path,
    write_report,
)
from worldcup.csl_postmatch_runner import run_postmatch
from worldcup.csl_snapshot_archive import archive_snapshot
from worldcup.league_odds_refresh import run_league_odds_refresh
from worldcup.league_runner import build_league_snapshot_from_cache
from worldcup.local_runner import write_snapshot
from worldcup.refresh_runner import _load_env

DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_CACHE_DIR = "data/cache"
DEFAULT_QUOTA_PATH = "data/cache/quota.json"
DEFAULT_SNAPSHOT_OUT = "data/local/diagnostics/csl_live_league_snapshot.json"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"
DEFAULT_SUMMARY_DIR = "data/local/diagnostics"
DEFAULT_OBSERVATION_FORMAT = "markdown"
KNOWN_QUOTA_PROVIDERS = ("theoddsapi_primary", "theoddsapi_secondary", "theoddsapi")

EnvLoader = Callable[[str], dict[str, str]]


def _parse_utc(value: str | None) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc).replace(microsecond=0)
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _utc_iso(value: str | None) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _stamp(value: str | None) -> str:
    return _parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def default_summary_path(root: str | Path, generated_at: str) -> Path:
    return Path(root) / DEFAULT_SUMMARY_DIR / f"csl_ops_runner_{_stamp(generated_at)}.json"


def _write_json(payload: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    if "api" in lowered and "key" in lowered:
        return exc.__class__.__name__
    if "secret" in lowered or "token" in lowered or "signature" in lowered:
        return exc.__class__.__name__
    return text[:200]


def _base_safety(*, read_env: bool = False, called_theoddsapi: bool = False) -> dict[str, bool]:
    return {
        "read_env": read_env,
        "called_theoddsapi": called_theoddsapi,
        "published": False,
        "deployed": False,
        "changed_launch_agent": False,
    }
```

- [ ] **Step 2: Add local state inspection**

Append:

```python
def _quota_remaining(quota_path: Path) -> int | float | None:
    try:
        payload = json.loads(quota_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return None
    known_remaining: list[int | float] = []
    for provider in KNOWN_QUOTA_PROVIDERS:
        value = providers.get(provider)
        if not isinstance(value, dict):
            continue
        remaining = value.get("remaining")
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            known_remaining.append(remaining)
            if remaining > 0:
                return remaining
    if known_remaining:
        return 0
    for provider in sorted(str(key) for key in providers):
        value = providers.get(provider)
        remaining = value.get("remaining") if isinstance(value, dict) else None
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            return remaining
    return None


def _local_state_step(
    root: Path,
    competition_id: str,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    quota_path: str | Path = DEFAULT_QUOTA_PATH,
    history: str | Path = DEFAULT_HISTORY,
) -> dict[str, Any]:
    cache_root = _resolve_under_root(root, cache_dir)
    cache_path = cache_root / f"theoddsapi_{competition_id}_odds.json"
    results_path = cache_root / f"club_results_{competition_id}.csv"
    history_path = _resolve_under_root(root, history)
    cache_state = _parse_cache_state(cache_path, competition_id)
    return {
        "status": cache_state.get("status"),
        "cache_exists": cache_state.get("cache_exists"),
        "results_exists": results_path.exists(),
        "history_snapshots": len(list(history_path.glob("snapshot_*.json"))) if history_path.exists() else 0,
        "events": cache_state.get("events"),
        "fixtures": cache_state.get("fixtures"),
        "odds_events": cache_state.get("odds_events"),
        "rating_policy": None,
        "club_rating_mode": None,
        "quota_remaining": _quota_remaining(_resolve_under_root(root, quota_path)),
        "warnings": cache_state.get("warnings") or [],
    }
```

- [ ] **Step 3: Add `run_csl_ops` dry-run behavior**

Append:

```python
def run_csl_ops(
    *,
    root: str | Path = ".",
    competition_id: str = DEFAULT_COMPETITION_ID,
    generated_at: str | None = None,
    run_local: bool = False,
    postmatch: bool = False,
    live_odds: bool = False,
    replace_existing: bool = True,
    env_path: str = ".env",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    quota_path: str | Path = DEFAULT_QUOTA_PATH,
    snapshot_out: str | Path = DEFAULT_SNAPSHOT_OUT,
    history: str | Path = DEFAULT_HISTORY,
    observation_format: str = DEFAULT_OBSERVATION_FORMAT,
    summary_out: str | Path | None = None,
    load_env: EnvLoader = _load_env,
) -> dict[str, Any]:
    root_path = Path(root)
    generated = _utc_iso(generated_at)
    steps: dict[str, Any] = {
        "local_state": _local_state_step(root_path, competition_id),
    }
    status = "ok"
    safety = _base_safety()
    mode = "dry_run"

    if live_odds and not run_local:
        status = "blocked"
        steps["live_odds"] = {"status": "blocked", "reason": "live_odds_requires_run_local"}

    if postmatch and not run_local:
        status = "blocked"
        steps["postmatch"] = {"status": "blocked", "reason": "postmatch_requires_run_local"}

    if not run_local:
        if steps["local_state"]["status"] in {"missing", "blocked", "error"}:
            status = "blocked"
        return {
            "schema_version": 1,
            "status": status,
            "mode": mode,
            "competition_id": competition_id,
            "generated_at": generated,
            "steps": steps,
            "paths": {},
            "data_quality": {},
            "postmatch": None,
            "safety": safety,
        }

    raise NotImplementedError("local run is implemented in Task 3")
```

- [ ] **Step 4: Add CLI for dry-run**

Append:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local CSL practical operations loop.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--competition", "--competition-id", dest="competition_id", default=DEFAULT_COMPETITION_ID)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--postmatch", action="store_true")
    parser.add_argument("--live-odds", action="store_true")
    parser.add_argument(
        "--no-replace-existing",
        action="store_false",
        dest="replace_existing",
        default=True,
        help="In live mode, block instead of replacing an existing CSL odds cache.",
    )
    parser.add_argument("--env", default=".env")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--quota-path", default=DEFAULT_QUOTA_PATH)
    parser.add_argument("--snapshot-out", default=DEFAULT_SNAPSHOT_OUT)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--observation-format", choices=("markdown", "json"), default=DEFAULT_OBSERVATION_FORMAT)
    parser.add_argument("--summary-out", default=None)
    args = parser.parse_args(argv)

    try:
        summary = run_csl_ops(
            root=args.root,
            competition_id=args.competition_id,
            generated_at=args.generated_at,
            run_local=args.run_local,
            postmatch=args.postmatch,
            live_odds=args.live_odds,
            replace_existing=args.replace_existing,
            env_path=args.env,
            cache_dir=args.cache_dir,
            quota_path=args.quota_path,
            snapshot_out=args.snapshot_out,
            history=args.history,
            observation_format=args.observation_format,
            summary_out=args.summary_out,
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        summary = {
            "schema_version": 1,
            "status": "error",
            "mode": "error",
            "competition_id": args.competition_id,
            "generated_at": _utc_iso(args.generated_at),
            "error": _safe_error(exc),
            "safety": _base_safety(read_env=False, called_theoddsapi=False),
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("status") in {"ok", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run dry-run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: dry-run tests pass; local-run tests have not been added yet.

## Task 3: Add Local Snapshot, Archive, Report Flow

**Files:**
- Modify: `tests/test_csl_ops_runner.py`
- Modify: `worldcup/csl_ops_runner.py`

- [ ] **Step 1: Add failing local-run test**

Append to `tests/test_csl_ops_runner.py`:

```python
def test_run_local_writes_snapshot_archive_observation_and_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
        )

        snapshot = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        archive = root / "data/local/diagnostics/csl_history/snapshot_20260703T090000Z-live.json"
        observation = root / "data/cache/csl_observation_report_20260703T090000Z.md"
        summary_path = root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json"

        assert summary["status"] in {"ok", "warn"}
        assert summary["mode"] == "local"
        assert snapshot.exists()
        assert archive.exists()
        assert observation.exists()
        assert summary_path.exists()
        assert summary["steps"]["snapshot"]["matches"] == 1
        assert summary["steps"]["archive"]["status"] in {"created", "duplicate"}
        assert summary["steps"]["observation"]["matches"] == 1
        assert summary["paths"]["snapshot"] == str(snapshot)
        assert summary["paths"]["archive"] == str(archive)
        assert summary["paths"]["observation"] == str(observation)
        assert summary["paths"]["summary"] == str(summary_path)
        assert summary["safety"]["read_env"] is False
        assert summary["safety"]["called_theoddsapi"] is False

        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("must-not-leak", "bookmaker", "api_key", "secret", "下注金额"):
            assert forbidden.lower() not in serialized.lower()
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner.test_run_local_writes_snapshot_archive_observation_and_summary -v
```

Expected: fail with `NotImplementedError: local run is implemented in Task 3`.

- [ ] **Step 3: Implement local flow helpers**

In `worldcup/csl_ops_runner.py`, add before `run_csl_ops`:

```python
def _snapshot_step(
    *,
    root: Path,
    cache_dir: str | Path,
    snapshot_out: str | Path,
    competition_id: str,
    generated_at: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    cache_path = _resolve_under_root(root, cache_dir)
    snapshot_path = _resolve_under_root(root, snapshot_out)
    snapshot = build_league_snapshot_from_cache(
        cache_path,
        competition_id=competition_id,
        snapshot_at=generated_at,
    )
    write_snapshot(snapshot, snapshot_path)
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    data_quality = snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    return (
        snapshot,
        {
            "status": "ok",
            "path": str(snapshot_path),
            "matches": counts.get("matches", 0),
            "warnings": data_quality.get("warnings", []),
        },
        snapshot_path,
    )


def _observation_step(
    *,
    root: Path,
    snapshot: dict[str, Any],
    generated_at: str,
    output_format: str,
) -> tuple[dict[str, Any], Path]:
    report = build_observation_report(snapshot, generated_at=generated_at)
    path = default_report_path(root, report["generated_at"], output_format)
    written = write_report(report, path, output_format)
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    return (
        {
            "status": report.get("status"),
            "path": str(written),
            "matches": counts.get("matches", 0),
            "raw_strong_candidates": counts.get("raw_strong_candidates", 0),
            "final_strong_grades": counts.get("final_strong_grades", 0),
        },
        written,
    )
```

- [ ] **Step 4: Replace local-run placeholder in `run_csl_ops`**

Replace `raise NotImplementedError("local run is implemented in Task 3")` with:

```python
    mode = "local"
    paths: dict[str, str] = {}
    data_quality: dict[str, Any] = {}

    snapshot, snapshot_step, snapshot_path = _snapshot_step(
        root=root_path,
        cache_dir=cache_dir,
        snapshot_out=snapshot_out,
        competition_id=competition_id,
        generated_at=generated,
    )
    steps["snapshot"] = snapshot_step
    paths["snapshot"] = str(snapshot_path)
    data_quality = snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}

    archive_step = archive_snapshot(
        source=snapshot_path,
        history=_resolve_under_root(root_path, history),
        competition_id=competition_id,
        dry_run=False,
    )
    steps["archive"] = archive_step
    paths["archive"] = archive_step["path"]

    observation_step, observation_path = _observation_step(
        root=root_path,
        snapshot=snapshot,
        generated_at=generated,
        output_format=observation_format,
    )
    steps["observation"] = observation_step
    paths["observation"] = str(observation_path)

    warnings = data_quality.get("warnings") if isinstance(data_quality.get("warnings"), list) else []
    if warnings:
        status = "warn"

    summary = {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "competition_id": competition_id,
        "generated_at": generated,
        "steps": steps,
        "paths": paths,
        "data_quality": {
            "warnings": warnings,
            "fixture_source": data_quality.get("fixture_source"),
            "invalid_odds_count": data_quality.get("invalid_odds_count", 0),
        },
        "postmatch": None,
        "safety": safety,
    }
    summary_path = _resolve_under_root(root_path, summary_out) if summary_out else default_summary_path(root_path, generated)
    written_summary = _write_json(summary, summary_path)
    summary["paths"]["summary"] = str(written_summary)
    _write_json(summary, written_summary)
    return summary
```

- [ ] **Step 5: Run local-run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: all `tests.test_csl_ops_runner` tests pass.

## Task 4: Add Optional Live Odds Refresh Guard

**Files:**
- Modify: `tests/test_csl_ops_runner.py`
- Modify: `worldcup/csl_ops_runner.py`

- [ ] **Step 1: Add failing live-mode tests with injected transport**

Append to `tests/test_csl_ops_runner.py`:

```python
def test_live_odds_requires_run_local():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        summary = runner.run_csl_ops(
            root=Path(tmp),
            generated_at="2026-07-03T09:00:00Z",
            live_odds=True,
        )

        assert summary["status"] == "blocked"
        assert summary["steps"]["live_odds"]["reason"] == "live_odds_requires_run_local"
        assert summary["safety"]["read_env"] is False
        assert summary["safety"]["called_theoddsapi"] is False


def test_live_odds_uses_env_only_when_explicit_and_keeps_summary_safe():
    import worldcup.csl_ops_runner as runner

    calls = {"load_env": 0}

    def load_env(path: str) -> dict[str, str]:
        calls["load_env"] += 1
        return {"THE_ODDS_API_KEY": "secret-key-value"}

    def transport(url: str) -> object:
        class Response:
            status = 200
            headers = {"x-requests-last": "1", "x-requests-remaining": "99", "x-requests-used": "1"}

            def read(self) -> bytes:
                return json.dumps(
                    [
                        {
                            "id": "csl-event-1",
                            "sport_key": "soccer_china_superleague",
                            "commence_time": "2026-07-03T11:35:00Z",
                            "home_team": "Shanghai Port",
                            "away_team": "Shandong Taishan",
                            "bookmakers": [
                                {
                                    "key": "must-not-leak",
                                    "markets": [
                                        {
                                            "key": "h2h",
                                            "outcomes": [
                                                {"name": "Shanghai Port", "price": 2.35},
                                                {"name": "Shandong Taishan", "price": 3.2},
                                                {"name": "Draw", "price": 3.4},
                                            ],
                                        },
                                        {
                                            "key": "totals",
                                            "outcomes": [
                                                {"name": "Over", "price": 1.95, "point": 2.5},
                                                {"name": "Under", "price": 1.9, "point": 2.5},
                                            ],
                                        },
                                        {
                                            "key": "spreads",
                                            "outcomes": [
                                                {"name": "Shanghai Port", "price": 1.92, "point": -0.5},
                                                {"name": "Shandong Taishan", "price": 1.94, "point": 0.5},
                                            ],
                                        },
                                    ],
                                }
                            ],
                        }
                    ]
                ).encode("utf-8")
        return Response()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_results(root / "data/cache")
        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            live_odds=True,
            load_env=load_env,
            live_transport=transport,
        )

        assert calls["load_env"] == 1
        assert summary["mode"] == "live_odds_local"
        assert summary["steps"]["live_odds"]["status"] == "fetched"
        assert summary["safety"]["read_env"] is True
        assert summary["safety"]["called_theoddsapi"] is True
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "secret-key-value" not in serialized
        assert "must-not-leak" not in serialized
```

- [ ] **Step 2: Run live tests to verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner.test_live_odds_requires_run_local tests.test_csl_ops_runner.test_live_odds_uses_env_only_when_explicit_and_keeps_summary_safe -v
```

Expected: second test fails because `run_csl_ops` does not accept `live_transport`.

- [ ] **Step 3: Add `live_transport` parameter and refresh step**

In `worldcup/csl_ops_runner.py`, keep the existing `run_csl_ops` parameters from Task 2 and add `live_transport` after `load_env`:

```python
from typing import Any, Callable

def run_csl_ops(
    *,
    root: str | Path = ".",
    competition_id: str = DEFAULT_COMPETITION_ID,
    generated_at: str | None = None,
    run_local: bool = False,
    postmatch: bool = False,
    live_odds: bool = False,
    replace_existing: bool = True,
    env_path: str = ".env",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    quota_path: str | Path = DEFAULT_QUOTA_PATH,
    snapshot_out: str | Path = DEFAULT_SNAPSHOT_OUT,
    history: str | Path = DEFAULT_HISTORY,
    observation_format: str = DEFAULT_OBSERVATION_FORMAT,
    summary_out: str | Path | None = None,
    load_env: EnvLoader = _load_env,
    live_transport: Callable[[str], object] | None = None,
) -> dict[str, Any]:
```

Before local snapshot generation, after mode initialization, add:

```python
    if live_odds:
        mode = "live_odds_local"
        env = load_env(env_path)
        safety = _base_safety(read_env=True, called_theoddsapi=True)
        live_result = run_league_odds_refresh(
            live=True,
            env=env,
            competition_id=competition_id,
            cache_dir=_resolve_under_root(root_path, cache_dir),
            quota_path=_resolve_under_root(root_path, quota_path),
            replace_existing=replace_existing,
            transport=live_transport,
            observed_at=generated,
        )
        steps["live_odds"] = {
            key: value
            for key, value in live_result.items()
            if key not in {"quota_entry"}
        }
        if live_result.get("status") != "fetched":
            return {
                "schema_version": 1,
                "status": "blocked" if live_result.get("status") == "blocked" else "error",
                "mode": mode,
                "competition_id": competition_id,
                "generated_at": generated,
                "steps": steps,
                "paths": {},
                "data_quality": {},
                "postmatch": None,
                "safety": safety,
            }
```

- [ ] **Step 4: Run CSL ops tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: all `tests.test_csl_ops_runner` tests pass.

## Task 5: Add Optional Postmatch Flow

**Files:**
- Modify: `tests/test_csl_ops_runner.py`
- Modify: `worldcup/csl_ops_runner.py`

- [ ] **Step 1: Add failing postmatch test**

Append to `tests/test_csl_ops_runner.py`:

```python
def test_run_local_postmatch_writes_eval_report_and_keeps_pending_gate_false():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            postmatch=True,
            postmatch_min_sample=1,
            postmatch_warmup_matches=0,
        )

        assert summary["postmatch"]["joined"] == 1
        assert summary["postmatch"]["pending_gate"]["can_lift_club_rating_pending"] is False
        assert (root / "data/local/backtest/csl_2026_eval.csv").exists()
        assert (root / "data/local/backtest/csl_2026_report.json").exists()
        assert (root / "data/local/diagnostics/csl_pending_gate_20260703T090000Z.json").exists()
```

- [ ] **Step 2: Run test to verify red**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner.test_run_local_postmatch_writes_eval_report_and_keeps_pending_gate_false -v
```

Expected: fail because `postmatch_min_sample` is not accepted or `postmatch` is not populated.

- [ ] **Step 3: Add postmatch parameters and execution**

In `run_csl_ops` signature add:

```python
    postmatch_min_sample: int = 30,
    postmatch_warmup_matches: int = 300,
    postmatch_min_eval_matches: int = 200,
```

Before summary creation, add:

```python
    postmatch_summary = None
    if postmatch:
        postmatch_summary = run_postmatch(
            root=root_path,
            history=history,
            results=Path(cache_dir) / f"club_results_{competition_id}.csv",
            generated_at=generated,
            min_sample=postmatch_min_sample,
            warmup_matches=postmatch_warmup_matches,
            min_eval_matches=postmatch_min_eval_matches,
        )
        steps["postmatch"] = {
            "status": "ok",
            "joined": postmatch_summary.get("joined", 0),
            "skipped_no_closing": postmatch_summary.get("skipped_no_closing", 0),
        }
```

Set `"postmatch": postmatch_summary` in the summary dict.

- [ ] **Step 4: Add CLI arguments**

In `main`, add:

```python
    parser.add_argument("--postmatch-min-sample", type=int, default=30)
    parser.add_argument("--postmatch-warmup-matches", type=int, default=300)
    parser.add_argument("--postmatch-min-eval-matches", type=int, default=200)
```

Pass them into `run_csl_ops`.

- [ ] **Step 5: Run CSL ops tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: all CSL ops runner tests pass.

## Task 6: Error Paths And CLI Exit Codes

**Files:**
- Modify: `tests/test_csl_ops_runner.py`
- Modify: `worldcup/csl_ops_runner.py`

- [ ] **Step 1: Add missing-cache and invalid-cache tests**

Append:

```python
def test_run_local_missing_cache_returns_safe_error_without_writing_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
        )

        assert summary["status"] in {"blocked", "error"}
        assert "snapshot" in summary["steps"]
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "api_key" not in serialized
        assert "secret" not in serialized
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()


def test_cli_returns_nonzero_for_run_local_missing_cache():
    from worldcup.csl_ops_runner import main

    with TemporaryDirectory() as tmp:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "--root",
                    tmp,
                    "--generated-at",
                    "2026-07-03T09:00:00Z",
                    "--run-local",
                ]
            )

        assert exit_code == 2
        payload = json.loads(stdout.getvalue())
        assert payload["status"] in {"blocked", "error"}
```

- [ ] **Step 2: Implement safe exception handling around local steps**

Wrap snapshot/archive/observation/postmatch blocks in `try/except (FileNotFoundError, ValueError, json.JSONDecodeError)` and return:

```python
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        steps.setdefault("snapshot", {"status": "error", "error": _safe_error(exc)})
        return {
            "schema_version": 1,
            "status": "error",
            "mode": mode,
            "competition_id": competition_id,
            "generated_at": generated,
            "steps": steps,
            "paths": {},
            "data_quality": {},
            "postmatch": None,
            "safety": safety,
        }
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: all CSL ops runner tests pass.

## Task 7: README And Recent Work

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README CSL Postmatch Eval Loop section**

Add after the current CSL Postmatch Eval Loop commands:

```markdown
### CSL Ops Runner

P9.23 新增 `worldcup.csl_ops_runner`，把中超日常实战运行收束成一条本地命令。默认 dry-run 只读本地状态，不写文件、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布、不部署、不改 LaunchAgent：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner
```

使用当前本地 odds cache 生成 snapshot、归档赛前快照并写观察报告：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local
```

完赛结果人工确认并写入 `data/cache/club_results_csl_2026.csv` 后，可加 `--postmatch` 串起 eval CSV、backtest report 和 pending gate：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local --postmatch
```

`--live-odds --run-local` 会读取 `.env` 并消耗 The Odds API quota，必须单独确认后再执行。该 runner 不解除 `club_rating_pending`，准确率判断仍以 closing snapshot 覆盖率、`skipped_no_closing`、Brier、Log Loss、calibration 和 model-vs-market 为准。
```

- [ ] **Step 2: Update RECENT_WORK.md after implementation**

Add:

```markdown
## 2026-06-29 P9.23 CSL ops runner

- 新增 `worldcup.csl_ops_runner`：默认 dry-run 只读本地 CSL 状态；`--run-local` 使用本地 cache 生成 snapshot、归档赛前快照、写观察报告；`--postmatch` 可串起本地赛后 eval/backtest/pending gate。
- `--live-odds` 仍需显式传入，才会读取 `.env` 并进入 The Odds API refresh；默认和 `--run-local` 不联网、不消耗 quota。
- 输出 summary 只含安全计数、路径、warnings、pending gate 摘要和 safety flags，不输出 raw bookmaker、per-book price、API key、secret、env 值、资金或执行建议。
- 本轮不改模型参数、不解除 `club_rating_pending`、不发布、不部署、不改 LaunchAgent。
- 验证：`tests.test_csl_ops_runner` 通过；项目标准 `tests/run_tests.py` 全量通过，并记录实际通过数量。
```

## Task 8: Final Verification

**Files:**
- All files changed in this plan.

- [ ] **Step 1: Run focused tests**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_csl_ops_runner -v
```

Expected: all `tests.test_csl_ops_runner` tests pass.

- [ ] **Step 2: Run full suite**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 3: Run whitespace check**

```bash
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 4: Run local dry-run smoke**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner
```

Expected: JSON with `mode="dry_run"`, `safety.read_env=false`, `safety.called_theoddsapi=false`.

- [ ] **Step 5: Run local artifact smoke only if user confirms writing ignored local artifacts**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local
```

Expected: JSON with `mode="local"`, snapshot/archive/observation paths under `data/local/` or `data/cache/`, and `safety.called_theoddsapi=false`.

Do not run `--live-odds` unless the user separately confirms live quota usage.

## Adversarial Review

- Root cause: this plan improves CSL practical accuracy by preserving pre-match evidence and enabling honest postmatch review, not by prematurely tuning model math.
- Scope creep: the runner must not become a publisher, deployment command, LaunchAgent installer, or public CSL page generator.
- Live risk: The Odds API quota is limited; dry-run and local modes must not load `.env` or call the API.
- Data risk: if no closing snapshot exists, postmatch output must report `skipped_no_closing`; it cannot claim model accuracy.
- Semantic risk: `club_rating_pending` remains active. Any future lift requires a separate design, plan, and confirmation.
- Security risk: summaries and CLI output must stay sanitized. Tests must scan for raw bookmaker markers, `api_key`, `secret`, and stake language.
