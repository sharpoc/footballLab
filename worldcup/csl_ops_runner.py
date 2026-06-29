"""Local CSL operations runner; defaults to offline dry-run."""
from __future__ import annotations

import argparse
import json
import math
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
DEFAULT_CSL_SPORT_KEY = "soccer_china_superleague"
DEFAULT_CACHE_DIR = "data/cache"
DEFAULT_QUOTA_PATH = "data/cache/quota.json"
DEFAULT_SNAPSHOT_OUT = "data/local/diagnostics/csl_live_league_snapshot.json"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"
DEFAULT_SUMMARY_DIR = "data/local/diagnostics"
DEFAULT_OBSERVATION_FORMAT = "markdown"
KNOWN_QUOTA_PROVIDERS = ("theoddsapi_primary", "theoddsapi_secondary", "theoddsapi")
WRITE_ALLOWED_PREFIXES = ("data/local", "data/cache")

EnvLoader = Callable[[str], dict[str, str]]


def _parse_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: str | None) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _stamp(value: str | None) -> str:
    return _parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root / candidate


def _relative_to_root(root: Path, path: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _is_allowed_write_path(root: Path, path: Path) -> bool:
    relative = _relative_to_root(root, path)
    if relative is None:
        return False
    normalized = relative.as_posix()
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in WRITE_ALLOWED_PREFIXES)


def _guard_write_path(root: Path, path: Path, label: str) -> dict[str, str] | None:
    if _is_allowed_write_path(root, path):
        return None
    return {"status": "blocked", "reason": "write_path_not_ignored", "path": str(path), "label": label}


def _write_path_guards(
    *,
    root: Path,
    snapshot_out: str | Path,
    history: str | Path,
    summary_out: str | Path | None,
    generated_at: str,
    cache_dir: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    guards: dict[str, dict[str, str]] = {}
    paths = {
        "snapshot": _resolve_under_root(root, snapshot_out),
        "history": _resolve_under_root(root, history),
        "summary": _resolve_under_root(root, summary_out)
        if summary_out is not None
        else default_summary_path(root, generated_at),
    }
    if cache_dir is not None:
        paths["cache"] = _resolve_under_root(root, cache_dir)
    for label, path in paths.items():
        guard = _guard_write_path(root, path, label)
        if guard is not None:
            guards[label] = guard
    return guards


def default_summary_path(root: str | Path, generated_at: str) -> Path:
    return Path(root) / DEFAULT_SUMMARY_DIR / f"csl_ops_runner_{_stamp(generated_at)}.json"


def _write_json(payload: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def _safe_error(exc: Exception) -> dict[str, str]:
    return {"error_type": type(exc).__name__}


def _base_safety() -> dict[str, bool]:
    return {
        "read_env": False,
        "called_theoddsapi": False,
        "published": False,
        "deployed": False,
        "changed_launch_agent": False,
    }


def _safe_count(value: Any) -> int | float | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _quota_remaining(quota_path: Path) -> int | float | None:
    try:
        payload = json.loads(quota_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return None

    known_remaining: list[int | float] = []
    for provider in KNOWN_QUOTA_PROVIDERS:
        value = providers.get(provider)
        if not isinstance(value, dict):
            continue
        remaining = value.get("remaining")
        safe_remaining = _safe_count(remaining)
        if safe_remaining is not None:
            known_remaining.append(safe_remaining)
            if safe_remaining > 0:
                return safe_remaining
    if known_remaining:
        return 0

    for provider in sorted(str(key) for key in providers):
        value = providers.get(provider)
        if not isinstance(value, dict):
            continue
        safe_remaining = _safe_count(value.get("remaining"))
        if safe_remaining is not None and safe_remaining > 0:
            return safe_remaining
    for provider in sorted(str(key) for key in providers):
        value = providers.get(provider)
        if not isinstance(value, dict):
            continue
        safe_remaining = _safe_count(value.get("remaining"))
        if safe_remaining is not None:
            return 0
    return None


def _parse_cache_state(cache_path: Path, competition_id: str) -> dict[str, Any]:
    if not cache_path.exists():
        return {
            "status": "blocked",
            "reason": "missing_odds_cache",
            "cache_exists": False,
            "events": None,
            "fixtures": None,
            "odds_events": None,
            "warnings": [],
        }

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            return {
                "status": "blocked",
                "reason": "invalid_odds_cache_shape",
                "cache_exists": True,
                "events": None,
                "fixtures": None,
                "odds_events": None,
                "warnings": [],
            }
        parse_result = parse_league_odds_events(payload, competition_id)
    except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as exc:
        return {
            "status": "blocked",
            "reason": "invalid_odds_cache_payload",
            "cache_exists": True,
            "events": None,
            "fixtures": None,
            "odds_events": None,
            "warnings": [],
            **_safe_error(exc),
        }

    invalid_odds_count = sum(len(event.invalid_odds) for event in parse_result.odds_events)
    warnings: list[str] = []
    if parse_result.unmatched_clubs:
        warnings.append("club_alias_unmatched")
    if invalid_odds_count:
        warnings.append("invalid_decimal_odds")
    return {
        "status": "warn" if warnings else "ok",
        "cache_exists": True,
        "events": len(payload),
        "fixtures": len(parse_result.fixtures),
        "odds_events": len(parse_result.odds_events),
        "warnings": warnings,
    }


def _local_state_step(
    root: Path,
    competition_id: str,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    quota_path: str | Path = DEFAULT_QUOTA_PATH,
    history: str | Path = DEFAULT_HISTORY,
) -> dict[str, Any]:
    cache_root = _resolve_under_root(root, cache_dir)
    cache_path = cache_root / f"theoddsapi_{competition_id}_odds.json"
    quota_file = _resolve_under_root(root, quota_path)
    history_path = _resolve_under_root(root, history)

    cache_state = _parse_cache_state(cache_path, competition_id)
    warnings = cache_state.get("warnings") or []

    state = {
        "status": cache_state.get("status"),
        "cache_exists": cache_state.get("cache_exists"),
        "results_exists": (cache_root / f"club_results_{competition_id}.csv").exists(),
        "history_snapshots": len(list(history_path.glob("snapshot_*.json")))
        if history_path.exists()
        else 0,
        "events": _safe_count(cache_state.get("events")),
        "fixtures": _safe_count(cache_state.get("fixtures")),
        "odds_events": _safe_count(cache_state.get("odds_events")),
        "rating_policy": None,
        "club_rating_mode": None,
        "quota_remaining": _quota_remaining(quota_file),
        "warnings": warnings,
    }
    if "reason" in cache_state:
        state["reason"] = cache_state["reason"]
    if "error_type" in cache_state:
        state["error_type"] = cache_state["error_type"]
    return state


def _summary_status(steps: dict[str, Any]) -> str:
    warned = False
    for step in steps.values():
        if not isinstance(step, dict):
            return "blocked"
        if step.get("status") in {None, "blocked", "error"}:
            return "blocked"
        if step.get("status") == "warn" or step.get("warnings"):
            warned = True
    local_state = steps.get("local_state") if isinstance(steps.get("local_state"), dict) else {}
    if warned or local_state.get("status") == "warn":
        return "warn"
    return "ok"


def _snapshot_step(
    *,
    root: Path,
    cache_dir: str | Path,
    snapshot_out: str | Path,
    competition_id: str,
    generated_at: str,
    replace_existing: bool,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    cache_path = _resolve_under_root(root, cache_dir)
    snapshot_path = _resolve_under_root(root, snapshot_out)
    if snapshot_path.exists() and not replace_existing:
        return (
            {},
            {
                "status": "blocked",
                "reason": "snapshot_exists",
                "path": str(snapshot_path),
                "matches": None,
                "warnings": [],
            },
            snapshot_path,
        )

    snapshot = build_league_snapshot_from_cache(
        cache_path,
        competition_id=competition_id,
        snapshot_at=generated_at,
    )
    write_snapshot(snapshot, snapshot_path)
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    data_quality = (
        snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    )
    warnings = _safe_list(data_quality.get("warnings"))
    return (
        snapshot,
        {
            "status": "warn" if warnings else "ok",
            "path": str(snapshot_path),
            "matches": _safe_count(counts.get("matches")) or 0,
            "warnings": warnings,
        },
        snapshot_path,
    )


def _archive_step(
    *,
    snapshot_path: Path,
    history: str | Path,
    root: Path,
    competition_id: str,
) -> dict[str, Any]:
    return archive_snapshot(
        source=snapshot_path,
        history=_resolve_under_root(root, history),
        competition_id=competition_id,
        min_matches=1,
        dry_run=False,
    )


def _observation_step(
    *,
    root: Path,
    snapshot: dict[str, Any],
    generated_at: str,
    output_format: str,
) -> tuple[dict[str, Any], Path]:
    report = build_observation_report(snapshot, generated_at=generated_at)
    output_path = default_report_path(root, report["generated_at"], output_format)
    written = write_report(report, output_path, output_format)
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    return (
        {
            "status": report.get("status"),
            "path": str(written),
            "matches": _safe_count(counts.get("matches")) or 0,
            "raw_strong_candidates": _safe_count(counts.get("raw_strong_candidates")) or 0,
            "final_strong_grades": _safe_count(counts.get("final_strong_grades")) or 0,
        },
        written,
    )


def _postmatch_step(
    *,
    root: Path,
    history: str | Path,
    cache_dir: str | Path,
    competition_id: str,
    generated_at: str,
    min_sample: int,
    warmup_matches: int,
    min_eval_matches: int,
) -> dict[str, Any]:
    return run_postmatch(
        root=root,
        history=history,
        results=Path(cache_dir) / f"club_results_{competition_id}.csv",
        generated_at=generated_at,
        min_sample=min_sample,
        warmup_matches=warmup_matches,
        min_eval_matches=min_eval_matches,
    )


def _data_quality_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    competition = snapshot.get("competition") if isinstance(snapshot.get("competition"), dict) else {}
    data_quality = (
        snapshot.get("data_quality") if isinstance(snapshot.get("data_quality"), dict) else {}
    )
    club_rating = (
        data_quality.get("club_rating") if isinstance(data_quality.get("club_rating"), dict) else {}
    )
    return {
        "warnings": _safe_list(data_quality.get("warnings")),
        "fixture_source": data_quality.get("fixture_source"),
        "rating_policy": competition.get("rating_policy"),
        "club_rating_mode": club_rating.get("mode"),
        "club_rating_sample_too_small": club_rating.get("sample_too_small"),
    }


def _safe_live_odds_step(live_result: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in live_result.items()
        if key not in {"quota_entry", "theoddsapi_provider"}
    }


def _blocked_summary(
    *,
    status: str,
    mode: str,
    competition_id: str,
    generated_at: str,
    steps: dict[str, Any],
    safety: dict[str, bool],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "mode": mode,
        "competition_id": competition_id,
        "generated_at": generated_at,
        "steps": steps,
        "paths": {},
        "data_quality": {},
        "postmatch": None,
        "safety": safety,
    }


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
    postmatch_min_sample: int = 30,
    postmatch_warmup_matches: int = 300,
    postmatch_min_eval_matches: int = 200,
) -> dict[str, Any]:
    root_path = Path(root)
    generated = _utc_iso(generated_at)
    safety = _base_safety()

    if competition_id != DEFAULT_COMPETITION_ID:
        return {
            "schema_version": 1,
            "status": "blocked",
            "mode": "dry_run",
            "competition_id": competition_id,
            "generated_at": generated,
            "steps": {
                "competition": {"status": "blocked", "reason": "unsupported_competition"}
            },
            "paths": {},
            "data_quality": {},
            "postmatch": None,
            "safety": safety,
        }

    steps: dict[str, Any] = {
        "local_state": _local_state_step(
            root_path,
            competition_id,
            cache_dir=cache_dir,
            quota_path=quota_path,
            history=history,
        )
    }

    if live_odds and not run_local:
        steps["live_odds"] = {"status": "blocked", "reason": "live_odds_requires_run_local"}
    if postmatch and not run_local:
        steps["postmatch"] = {"status": "blocked", "reason": "postmatch_requires_run_local"}

    if not run_local:
        return _blocked_summary(
            status=_summary_status(steps),
            mode="dry_run",
            competition_id=competition_id,
            generated_at=generated,
            steps=steps,
            safety=safety,
        )

    write_guards = _write_path_guards(
        root=root_path,
        snapshot_out=snapshot_out,
        history=history,
        summary_out=summary_out,
        generated_at=generated,
        cache_dir=cache_dir if live_odds else None,
    )
    if write_guards:
        steps["write_paths"] = {"status": "blocked", "guards": write_guards}
        return _blocked_summary(
            status="blocked",
            mode="local",
            competition_id=competition_id,
            generated_at=generated,
            steps=steps,
            safety=safety,
        )

    mode = "local"
    if live_odds:
        mode = "live_odds_local"
        env = load_env(env_path)
        safety["read_env"] = True
        safety["called_theoddsapi"] = True
        live_result = run_league_odds_refresh(
            live=True,
            env=env,
            competition_id=competition_id,
            sport_key=DEFAULT_CSL_SPORT_KEY,
            cache_dir=_resolve_under_root(root_path, cache_dir),
            quota_path=_resolve_under_root(root_path, quota_path),
            replace_existing=replace_existing,
            transport=live_transport,
            observed_at=generated,
        )
        steps["live_odds"] = _safe_live_odds_step(live_result)
        if live_result.get("status") != "fetched":
            return _blocked_summary(
                status="blocked" if live_result.get("status") == "blocked" else "error",
                mode=mode,
                competition_id=competition_id,
                generated_at=generated,
                steps=steps,
                safety=safety,
            )
        steps["local_state"] = _local_state_step(
            root_path,
            competition_id,
            cache_dir=cache_dir,
            quota_path=quota_path,
            history=history,
        )

    if steps["local_state"].get("status") in {"blocked", "error"}:
        return _blocked_summary(
            status="blocked",
            mode=mode,
            competition_id=competition_id,
            generated_at=generated,
            steps=steps,
            safety=safety,
        )

    snapshot, snapshot_step, snapshot_path = _snapshot_step(
        root=root_path,
        cache_dir=cache_dir,
        snapshot_out=snapshot_out,
        competition_id=competition_id,
        generated_at=generated,
        replace_existing=replace_existing,
    )
    steps["snapshot"] = snapshot_step
    if snapshot_step.get("status") == "blocked":
        return _blocked_summary(
            status="blocked",
            mode=mode,
            competition_id=competition_id,
            generated_at=generated,
            steps=steps,
            safety=safety,
        )

    archive_step = _archive_step(
        snapshot_path=snapshot_path,
        history=history,
        root=root_path,
        competition_id=competition_id,
    )
    steps["archive"] = archive_step
    observation_step, observation_path = _observation_step(
        root=root_path,
        snapshot=snapshot,
        generated_at=generated,
        output_format=observation_format,
    )
    steps["observation"] = observation_step
    postmatch_summary = None
    if postmatch:
        postmatch_summary = _postmatch_step(
            root=root_path,
            history=history,
            cache_dir=cache_dir,
            competition_id=competition_id,
            generated_at=generated,
            min_sample=postmatch_min_sample,
            warmup_matches=postmatch_warmup_matches,
            min_eval_matches=postmatch_min_eval_matches,
        )
        steps["postmatch"] = {
            "status": "ok",
            "joined": _safe_count(postmatch_summary.get("joined")) or 0,
            "skipped_no_closing": _safe_count(postmatch_summary.get("skipped_no_closing")) or 0,
        }

    summary_path = (
        _resolve_under_root(root_path, summary_out)
        if summary_out is not None
        else default_summary_path(root_path, generated)
    )
    summary = {
        "schema_version": 1,
        "status": _summary_status(steps),
        "mode": mode,
        "competition_id": competition_id,
        "generated_at": generated,
        "steps": steps,
        "paths": {
            "snapshot": str(snapshot_path),
            "archive": str(archive_step.get("path")),
            "observation": str(observation_path),
            "summary": str(summary_path),
        },
        "data_quality": _data_quality_summary(snapshot),
        "postmatch": postmatch_summary,
        "safety": safety,
    }
    _write_json(summary, summary_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local CSL operations in offline dry-run mode.")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--competition", "--competition-id", dest="competition_id", default=DEFAULT_COMPETITION_ID
    )
    parser.add_argument("--generated-at")
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--postmatch", action="store_true")
    parser.add_argument("--live-odds", action="store_true")
    parser.add_argument("--no-replace-existing", action="store_false", dest="replace_existing", default=True)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    parser.add_argument("--quota-path", default=DEFAULT_QUOTA_PATH)
    parser.add_argument("--snapshot-out", default=DEFAULT_SNAPSHOT_OUT)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument(
        "--observation-format",
        choices=("markdown", "json"),
        default=DEFAULT_OBSERVATION_FORMAT,
    )
    parser.add_argument("--summary-out")
    parser.add_argument("--postmatch-min-sample", type=int, default=30)
    parser.add_argument("--postmatch-warmup-matches", type=int, default=300)
    parser.add_argument("--postmatch-min-eval-matches", type=int, default=200)
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
            load_env=_load_env,
            postmatch_min_sample=args.postmatch_min_sample,
            postmatch_warmup_matches=args.postmatch_warmup_matches,
            postmatch_min_eval_matches=args.postmatch_min_eval_matches,
        )
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        summary = {
            "schema_version": 1,
            "status": "blocked",
            "mode": "error",
            "competition_id": args.competition_id,
            "generated_at": None,
            "steps": {"error": {"status": "blocked", **_safe_error(exc)}},
            "paths": {},
            "data_quality": {},
            "postmatch": None,
            "safety": _base_safety(),
        }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("status") in {"ok", "warn"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
