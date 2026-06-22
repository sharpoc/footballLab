from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.club_rating import ClubRatingPool, load_club_rating_pool
from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.competitions import get_competition
from worldcup.config import load_config
from worldcup.local_runner import (
    _analysis_to_dict,
    cap_signals_for_pending_club_rating,
    write_snapshot,
)
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input, generate_value_signals


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _odds_cache_name(competition_id: str) -> str:
    return f"theoddsapi_{competition_id}_odds.json"


def _invalid_odds_quality(
    odds_events: list[ParsedOddsEvent],
    raw_payload_path: Path,
    max_examples: int = 10,
) -> dict[str, Any]:
    invalid = []
    for event in odds_events:
        for quote in event.invalid_odds:
            item = quote.to_dict()
            item["raw_payload_path"] = str(raw_payload_path)
            invalid.append(item)
    return {
        "invalid_odds_count": len(invalid),
        "invalid_odds_examples": invalid[:max_examples],
    }


def _placeholder_rating(team_key: str | None) -> EloRating:
    return EloRating(code=team_key or "club_rating_pending", rank=0, rating=1500)


def _ratings_for_fixture(
    fixture: Fixture,
    rating_pool: ClubRatingPool | None,
) -> tuple[EloRating, EloRating, list[str]]:
    if rating_pool is None:
        return (
            _placeholder_rating(fixture.home_canonical),
            _placeholder_rating(fixture.away_canonical),
            [],
        )

    home_rating = rating_pool.to_elo_rating(fixture.home_canonical)
    away_rating = rating_pool.to_elo_rating(fixture.away_canonical)
    missing = []
    if home_rating is None and fixture.home_canonical is not None:
        missing.append(fixture.home_canonical)
    if away_rating is None and fixture.away_canonical is not None:
        missing.append(fixture.away_canonical)
    if missing:
        return (
            _placeholder_rating(fixture.home_canonical),
            _placeholder_rating(fixture.away_canonical),
            missing,
        )
    return home_rating, away_rating, []


def _match_input_from_fixture_event(
    fixture: Fixture,
    odds_event: ParsedOddsEvent,
    rating_pool: ClubRatingPool | None = None,
) -> tuple[MatchAnalysisInput, list[str]]:
    home_elo, away_elo, missing = _ratings_for_fixture(fixture, rating_pool)
    return (
        MatchAnalysisInput(
            fixture=fixture,
            odds_event=odds_event,
            home_elo=home_elo,
            away_elo=away_elo,
            quotes=odds_event.quotes,
            neutral=False,
        ),
        missing,
    )


def build_league_snapshot_from_cache(
    cache_dir: str | Path,
    competition_id: str = "csl_2026",
    snapshot_at: str | None = None,
    cfg: dict | None = None,
    club_rating_min_matches: int = 20,
) -> dict:
    competition = get_competition(competition_id)
    cfg = cfg or load_config()
    observed_at = snapshot_at or _now_utc_iso()
    cache_path = Path(cache_dir) / _odds_cache_name(competition_id)
    parse_result = parse_league_odds_events(_read_json(cache_path), competition_id)
    club_rating_result = load_club_rating_pool(
        cache_dir,
        competition_id,
        min_matches=club_rating_min_matches,
        home_adv=float(cfg["elo"]["home_adv"]),
    )
    rating_pool = club_rating_result.pool

    matches = []
    missing_rating_teams: set[str] = set()
    club_rating_pending = competition.rating_policy == "club_rating_pending"
    for fixture, odds_event in zip(parse_result.fixtures, parse_result.odds_events):
        match_input, missing = _match_input_from_fixture_event(fixture, odds_event, rating_pool)
        missing_rating_teams.update(missing)
        analysis = analyze_match_input(match_input, cfg)
        signals = generate_value_signals(analysis, cfg, observed_at=observed_at)
        if club_rating_pending:
            signals = cap_signals_for_pending_club_rating(signals)
        matches.append(
            _analysis_to_dict(
                analysis,
                signals,
                competition_id=competition_id,
            )
        )

    warnings: list[str] = []
    if parse_result.fixture_source == "odds_event_only":
        warnings.append("odds_event_only")
    if club_rating_pending:
        warnings.append("club_rating_pending")
    if club_rating_result.quality.mode == "missing" or missing_rating_teams:
        warnings.append("club_rating_missing")
    if club_rating_result.quality.sample_too_small:
        warnings.append("club_rating_sample_too_small")
    if club_rating_result.quality.mode == "invalid":
        warnings.append("club_rating_invalid")

    club_quality = club_rating_result.quality.with_missing_teams(missing_rating_teams)

    return {
        "snapshot_at": observed_at,
        "competition": competition.snapshot_block(),
        "counts": {
            "fixtures": len(parse_result.fixtures),
            "odds_events": len(parse_result.odds_events),
            "match_inputs": len(matches),
            "matches": len(matches),
        },
        "data_quality": {
            "fixture_source": parse_result.fixture_source,
            "warnings": warnings,
            "club_alias_unmatched": parse_result.unmatched_clubs,
            "club_rating": club_quality.to_dict(),
            **_invalid_odds_quality(parse_result.odds_events, cache_path),
        },
        "matches": matches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local league snapshot from cached odds events.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--competition-id", "--competition", dest="competition_id", default="csl_2026")
    parser.add_argument("--snapshot-at", default=None)
    parser.add_argument("--out", default="data/cache/league_analysis_snapshot.json")
    parser.add_argument("--club-rating-min-matches", type=int, default=20)
    args = parser.parse_args(argv)

    snapshot = build_league_snapshot_from_cache(
        args.cache_dir,
        competition_id=args.competition_id,
        snapshot_at=args.snapshot_at,
        club_rating_min_matches=args.club_rating_min_matches,
    )
    write_snapshot(snapshot, args.out)
    print(f"wrote {args.out} with {snapshot['counts']['matches']} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
