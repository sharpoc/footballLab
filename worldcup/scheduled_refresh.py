from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from worldcup.refresh_runner import _load_env, refresh_cache_and_build_snapshot
from worldcup.scheduler import build_scheduler_report
from worldcup.theoddsapi_keys import LEGACY_PROVIDER, choose_key_slot


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_scheduled_refresh(
    now: str | None = None,
    live: bool = False,
    force: bool = False,
    env_path: str | Path = ".env",
    cache_dir: str | Path = "data/cache",
    snapshot_path: str | Path = "data/cache/analysis_snapshot.json",
    quota_path: str | Path = "data/cache/quota.json",
    api_key: str | None = None,
    refresh_fn: Callable[..., object] = refresh_cache_and_build_snapshot,
) -> dict:
    observed = now or _now_utc_iso()
    env = _load_env(env_path)
    report = build_scheduler_report(
        now=observed,
        snapshot_path=snapshot_path,
        quota_path=quota_path,
        env=None if api_key else env,
    )
    decision = report["decision"]

    if not live:
        return {
            "status": "dry_run",
            "force": force,
            "report": report,
            "refresh": None,
        }

    if not decision["should_refresh"] and not force:
        return {
            "status": "skipped",
            "force": force,
            "report": report,
            "refresh": None,
        }

    if api_key:
        key = api_key
        provider = LEGACY_PROVIDER
        slot = "legacy"
    else:
        selected = choose_key_slot(env, report.get("quota") or {})
        if selected is None:
            raise ValueError(
                "THE_ODDS_API_KEY_PRIMARY, THE_ODDS_API_KEY_SECONDARY, or THE_ODDS_API_KEY is missing or exhausted"
            )
        key = selected.api_key
        provider = selected.provider
        slot = selected.slot

    refresh_result = refresh_fn(
        api_key=key,
        cache_dir=cache_dir,
        snapshot_path=snapshot_path,
        quota_path=quota_path,
        observed_at=observed,
        theoddsapi_provider=provider,
    )
    return {
        "status": "refreshed",
        "force": force,
        "report": report,
        "refresh": {
            "snapshot_path": str(refresh_result.snapshot_path),
            "matches": refresh_result.snapshot["counts"]["matches"],
            "run_id": refresh_result.run_metadata.get("run_id"),
            "odds_api_key_slot": slot,
            "theoddsapi_provider": provider,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a scheduled refresh decision, defaulting to dry-run.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--now", default=None)
    parser.add_argument("--live", action="store_true", help="Call refresh_runner when the schedule is due.")
    parser.add_argument("--force", action="store_true", help="With --live, refresh even when not due.")
    args = parser.parse_args(argv)

    result = run_scheduled_refresh(
        now=args.now,
        live=args.live,
        force=args.force,
        env_path=args.env,
        cache_dir=args.cache_dir,
        snapshot_path=args.snapshot_path,
        quota_path=args.quota_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
