from __future__ import annotations

from dataclasses import replace
from typing import Any

from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.collectors.models import EloRating
from worldcup.competitions import CompetitionConfig, get_competition
from worldcup.config import load_config
from worldcup.local_runner import _analysis_to_dict
from worldcup.match_decision import decide_match, prepare_match_input_for_pick
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def _formal_profile(competition_id: str) -> CompetitionConfig:
    profile = get_competition(competition_id)
    if profile.pipeline_family != "league_v1":
        raise ValueError(f"unsupported_league_pipeline: {competition_id}")
    return profile


def _pending_rating(code: str | None) -> EloRating:
    return EloRating(code=code or "club_rating_pending", rank=0, rating=1500)


def build_league_competition_snapshot(
    raw_odds: list[dict[str, Any]],
    competition_id: str,
    observed_at: str,
    cfg: dict[str, Any] | None = None,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
) -> dict[str, Any]:
    profile = _formal_profile(competition_id)
    analysis_cfg = cfg or load_config()
    parsed = parse_league_odds_events(raw_odds, competition_id)
    matches: list[dict[str, Any]] = []
    unmatched = [] if identity_registry is not None else list(parsed.unmatched_clubs)

    for fixture, event in zip(parsed.fixtures, parsed.odds_events):
        if event.sport_key != profile.theoddsapi_sport_key:
            raise ValueError(f"sport_key_mismatch: {event.source_event_id}")
        if identity_registry is not None:
            identity = identity_registry.resolve_fixture(
                competition_id,
                fixture.home_team_name,
                fixture.away_team_name,
            )
            if identity["status"] != "verified":
                if identity["home_canonical"] is None:
                    unmatched.append(fixture.home_team_name)
                if identity["away_canonical"] is None:
                    unmatched.append(fixture.away_team_name)
                continue
            fixture = replace(
                fixture,
                home_canonical=str(identity["home_canonical"]),
                away_canonical=str(identity["away_canonical"]),
            )
        match_input = MatchAnalysisInput(
            fixture=fixture,
            odds_event=event,
            home_elo=_pending_rating(fixture.home_canonical),
            away_elo=_pending_rating(fixture.away_canonical),
            quotes=event.quotes,
            neutral=False,
        )
        prepared = prepare_match_input_for_pick(match_input, analysis_cfg, observed_at)
        analysis = analyze_match_input(prepared, analysis_cfg)
        decision = decide_match(
            analysis,
            analysis_cfg,
            observed_at=observed_at,
            hard_blockers=("club_rating_pending",),
        )
        matches.append(
            _analysis_to_dict(
                analysis,
                competition_id=competition_id,
                match_decision=decision,
            )
        )

    return {
        "snapshot_at": observed_at,
        "competition": profile.snapshot_block(),
        "counts": {
            "fixtures": len(parsed.fixtures),
            "odds_events": len(parsed.odds_events),
            "match_inputs": len(matches),
            "matches": len(matches),
        },
        "data_quality": {
            "fixture_source": parsed.fixture_source,
            "warnings": ["club_rating_pending", "market_consensus_fallback"],
            "club_alias_unmatched": sorted(set(unmatched)),
            "club_rating": {
                "mode": "pending",
                "activation": "market_consensus_only",
            },
        },
        "matches": matches,
    }
