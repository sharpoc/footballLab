from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.club_rating import ClubRatingPool, load_club_rating_pool
from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.competitions import get_competition
from worldcup.config import load_config
from worldcup.match_decision import decide_match, prepare_match_input_for_pick
from worldcup.local_runner import (
    _analysis_to_dict,
    write_snapshot,
)
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input


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
            item["source_path"] = str(raw_payload_path)
            invalid.append(item)
    return {
        "invalid_odds_count": len(invalid),
        "invalid_odds_examples": invalid[:max_examples],
    }


def _placeholder_rating(team_key: str | None) -> EloRating:
    return EloRating(code=team_key or "club_rating_pending", rank=0, rating=1500)


@dataclass(frozen=True)
class _FixtureRatingResolution:
    home: EloRating
    away: EloRating
    missing: tuple[str, ...] = ()
    sample_too_small: tuple[str, ...] = ()


def _resolve_fixture_ratings(
    fixture: Fixture,
    rating_pool: ClubRatingPool | None,
    *,
    min_team_matches: int,
) -> _FixtureRatingResolution:
    if rating_pool is None:
        return _FixtureRatingResolution(
            home=_placeholder_rating(fixture.home_canonical),
            away=_placeholder_rating(fixture.away_canonical),
        )

    home = rating_pool.rating_for(fixture.home_canonical)
    away = rating_pool.rating_for(fixture.away_canonical)
    missing = tuple(
        code
        for code, rating in (
            (fixture.home_canonical, home),
            (fixture.away_canonical, away),
        )
        if code is not None and rating is None
    )
    sample_too_small = tuple(
        code
        for code, rating in (
            (fixture.home_canonical, home),
            (fixture.away_canonical, away),
        )
        if code is not None and rating is not None and rating.matches < min_team_matches
    )
    canonical_missing = fixture.home_canonical is None or fixture.away_canonical is None
    if canonical_missing or missing or sample_too_small:
        return _FixtureRatingResolution(
            home=_placeholder_rating(fixture.home_canonical),
            away=_placeholder_rating(fixture.away_canonical),
            missing=missing,
            sample_too_small=sample_too_small,
        )
    assert home is not None
    assert away is not None
    return _FixtureRatingResolution(
        home=EloRating(code=home.code, rank=0, rating=home.rating),
        away=EloRating(code=away.code, rank=0, rating=away.rating),
    )


def _ratings_for_fixture(
    fixture: Fixture,
    rating_pool: ClubRatingPool | None,
) -> tuple[EloRating, EloRating, list[str]]:
    resolved = _resolve_fixture_ratings(
        fixture,
        rating_pool,
        min_team_matches=0,
    )
    return resolved.home, resolved.away, list(resolved.missing)


def _match_input_from_fixture_event(
    fixture: Fixture,
    odds_event: ParsedOddsEvent,
    rating_pool: ClubRatingPool | None = None,
    min_team_matches: int = 0,
) -> tuple[MatchAnalysisInput, list[str], list[str]]:
    resolved = _resolve_fixture_ratings(
        fixture,
        rating_pool,
        min_team_matches=min_team_matches,
    )
    return (
        MatchAnalysisInput(
            fixture=fixture,
            odds_event=odds_event,
            home_elo=resolved.home,
            away_elo=resolved.away,
            quotes=odds_event.quotes,
            neutral=False,
        ),
        list(resolved.missing),
        list(resolved.sample_too_small),
    )


def competition_analysis_config(cfg: dict[str, Any], competition_id: str) -> dict[str, Any]:
    if competition_id != "csl_2026":
        return cfg
    model = cfg.get("csl_model") if isinstance(cfg.get("csl_model"), dict) else {}
    resolved = copy.deepcopy(cfg)
    resolved["elo"]["home_adv"] = float(model.get("home_adv", resolved["elo"]["home_adv"]))
    resolved["poisson"]["mu_total"] = float(
        model.get("mu_total", resolved["poisson"]["mu_total"])
    )
    resolved["poisson"]["mu_market_weight"] = float(
        model.get("mu_market_weight", resolved["poisson"].get("mu_market_weight", 0.0))
    )
    resolved["ensemble"]["w_elo"] = float(
        model.get("w_elo", resolved["ensemble"]["w_elo"])
    )
    resolved["ensemble"]["w_poisson"] = float(
        model.get("w_poisson", resolved["ensemble"]["w_poisson"])
    )
    return resolved


def build_league_snapshot_from_cache(
    cache_dir: str | Path,
    competition_id: str = "csl_2026",
    snapshot_at: str | None = None,
    cfg: dict | None = None,
    club_rating_min_matches: int | None = None,
    club_rating_min_team_matches: int | None = None,
) -> dict:
    competition = get_competition(competition_id)
    cfg = cfg or load_config()
    observed_at = snapshot_at or _now_utc_iso()
    csl_model_cfg = cfg.get("csl_model") or {}
    analysis_cfg = competition_analysis_config(cfg, competition_id)
    resolved_min_matches = int(
        club_rating_min_matches
        if club_rating_min_matches is not None
        else csl_model_cfg.get("min_total_matches", 300)
    )
    resolved_min_team_matches = int(
        club_rating_min_team_matches
        if club_rating_min_team_matches is not None
        else csl_model_cfg.get("min_team_matches", 30)
    )
    cache_path = Path(cache_dir) / _odds_cache_name(competition_id)
    parse_result = parse_league_odds_events(_read_json(cache_path), competition_id)
    club_rating_result = load_club_rating_pool(
        cache_dir,
        competition_id,
        min_matches=resolved_min_matches,
        min_team_matches=resolved_min_team_matches,
        k=float(csl_model_cfg.get("rating_k", 30)),
        home_adv=float(csl_model_cfg.get("home_adv", cfg["elo"]["home_adv"])),
    )
    rating_pool = club_rating_result.pool

    matches = []
    missing_rating_teams: set[str] = set()
    ineligible_rating_teams: set[str] = set()
    club_rating_pending = competition.rating_policy == "club_rating_pending"
    rating_mode = club_rating_result.quality.mode
    for fixture, odds_event in zip(parse_result.fixtures, parse_result.odds_events):
        match_input, missing, ineligible = _match_input_from_fixture_event(
            fixture,
            odds_event,
            rating_pool,
            min_team_matches=resolved_min_team_matches,
        )
        missing_rating_teams.update(missing)
        ineligible_rating_teams.update(ineligible)
        pick_input = prepare_match_input_for_pick(match_input, analysis_cfg, observed_at)
        analysis = analyze_match_input(pick_input, analysis_cfg)
        decision_blockers: list[str] = []
        if club_rating_pending:
            decision_blockers.append("club_rating_pending")
        if rating_mode != "sample_replay":
            decision_blockers.append(f"club_rating_{rating_mode}")
        if missing:
            decision_blockers.append("club_rating_missing_team")
        if ineligible:
            decision_blockers.append("club_rating_team_sample_too_small")
        match_decision = decide_match(
            analysis,
            analysis_cfg,
            observed_at=observed_at,
            hard_blockers=decision_blockers,
        )
        matches.append(
            _analysis_to_dict(
                analysis,
                competition_id=competition_id,
                match_decision=match_decision,
            )
        )

    warnings: list[str] = []
    if parse_result.fixture_source == "odds_event_only":
        warnings.append("odds_event_only")
    if club_rating_pending:
        warnings.append("club_rating_pending")
    club_quality = club_rating_result.quality.with_missing_teams(missing_rating_teams)
    if club_quality.mode == "missing" or missing_rating_teams:
        warnings.append("club_rating_missing")
    if club_quality.mode == "sample_too_small":
        warnings.append("club_rating_sample_too_small")
    if club_quality.mode == "invalid":
        warnings.append("club_rating_invalid")
    if ineligible_rating_teams:
        warnings.append("club_rating_team_sample_too_small")
    warnings = sorted(set(warnings))
    club_quality_dict = club_quality.to_dict()
    club_quality_dict.update(
        {
            "activation": str(csl_model_cfg.get("rating_activation", "shadow_only")),
            "fixture_ineligible_teams": sorted(ineligible_rating_teams),
        }
    )

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
            "club_rating": club_quality_dict,
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
    parser.add_argument("--club-rating-min-matches", type=int, default=None)
    parser.add_argument("--club-rating-min-team-matches", type=int, default=None)
    args = parser.parse_args(argv)

    snapshot = build_league_snapshot_from_cache(
        args.cache_dir,
        competition_id=args.competition_id,
        snapshot_at=args.snapshot_at,
        club_rating_min_matches=args.club_rating_min_matches,
        club_rating_min_team_matches=args.club_rating_min_team_matches,
    )
    write_snapshot(snapshot, args.out)
    print(f"wrote {args.out} with {snapshot['counts']['matches']} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
