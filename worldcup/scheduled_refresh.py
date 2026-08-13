from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.daily_competitions import DailyCompetition, resolve_provider_catalog
from worldcup.daily_odds_refresh import plan_daily_odds_refresh, refresh_daily_odds
from worldcup.daily_odds_store import DailyOddsSnapshotWriter, default_daily_odds_snapshot_path
from worldcup.daily_odds_state import DailyOddsState, default_daily_odds_state_path
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
    env = {} if (not live or api_key) else _load_env(env_path)
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
                "THE_ODDS_API_KEY_PRIMARY, THE_ODDS_API_KEY_SECONDARY, "
                "THE_ODDS_API_KEY_TERTIARY, or THE_ODDS_API_KEY is missing or exhausted"
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



def run_daily_odds_refresh(
    *,
    enabled: bool = False,
    live: bool = False,
    now: str | None = None,
    cache_dir: str | Path = "data/cache",
    snapshot_path: str | Path | None = None,
    sports_fetcher: Callable[[], Any] | None = None,
    events_fetcher: Callable[[str], Any] | None = None,
    odds_fetcher: Callable[[str, tuple[str, ...]], Any] | None = None,
    snapshot_writer: Callable[[dict[str, Any]], Any] | None = None,
    quota_remaining_by_key: Mapping[str, int | None] | None = None,
    state: Any = None,
    catalog: tuple[DailyCompetition, ...] | None = None,
    state_path: str | Path | None = None,
    daily_budget_credits: int | None = None,
) -> dict[str, Any]:
    """Explicitly run the daily-odds sidecar; disabled by default and never auto-scheduled."""
    if not enabled:
        return {
            "status": "disabled",
            "reason": "feature_disabled",
            "plan": None,
            "refresh": None,
        }
    if sports_fetcher is None or events_fetcher is None:
        raise ValueError("daily_odds_fetchers_required")
    observed = now or _now_utc_iso()
    sports = sports_fetcher()
    if not isinstance(sports, list):
        sports = []
    events_by_sport: dict[str, Any] = {}
    resolved = resolve_provider_catalog(catalog or (), sports) if catalog else None
    keys = [
        item.sport_key
        for item in (resolved or ())
        if item.status == "enabled" and item.sport_key
    ]
    if not keys:
        from worldcup.daily_competitions import daily_competition_catalog

        keys = [
            item.sport_key
            for item in resolve_provider_catalog(daily_competition_catalog(), sports)
            if item.status == "enabled" and item.sport_key
        ]
    for sport_key in keys:
        events_by_sport[sport_key] = events_fetcher(sport_key)

    committed_state = state
    if (
        live
        and committed_state is None
        and state_path is not False
        and (state_path is not None or snapshot_writer is None)
    ):
        committed_state = DailyOddsState(state_path or default_daily_odds_state_path(cache_dir))
    plan = plan_daily_odds_refresh(
        now=observed,
        sports=sports,
        events_by_sport=events_by_sport,
        quota_remaining_by_key=quota_remaining_by_key,
        state=committed_state,
        catalog=catalog,
        daily_budget_credits=daily_budget_credits,
    )
    plan_payload = plan.to_dict()
    if not live:
        return {
            "status": "dry_run",
            "reason": "live_not_enabled",
            "plan": plan_payload,
            "refresh": None,
        }
    if odds_fetcher is None:
        raise ValueError("daily_odds_odds_fetcher_required")
    writer = snapshot_writer
    if writer is None:
        writer = DailyOddsSnapshotWriter(
            snapshot_path or default_daily_odds_snapshot_path(cache_dir)
        )
    committed_state = state
    if (
        live
        and committed_state is None
        and state_path is not False
        and (state_path is not None or snapshot_writer is None)
    ):
        committed_state = DailyOddsState(state_path or default_daily_odds_state_path(cache_dir))
    refreshed = refresh_daily_odds(
        now=observed,
        sports_fetcher=lambda: sports,
        events_fetcher=lambda sport_key: events_by_sport.get(sport_key, []),
        odds_fetcher=odds_fetcher,
        snapshot_writer=writer,
        quota_remaining_by_key=quota_remaining_by_key,
        state=committed_state,
        catalog=catalog,
        daily_budget_credits=daily_budget_credits,
    )
    return {
        "status": "refreshed",
        "reason": "explicit_live",
        "plan": plan_payload,
        "refresh": refreshed.to_dict(),
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
