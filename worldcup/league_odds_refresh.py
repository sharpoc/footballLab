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
            **base,
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
