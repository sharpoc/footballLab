from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from worldcup.collectors.models import EloRating, Fixture, ParsedLineupContext, ParsedOddsEvent
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.models import OddsQuote

_WORLD_CUP_2026_HOST_VENUES = {
    "atlanta": "united_states",
    "boston": "united_states",
    "dallas": "united_states",
    "guadalajara": "mexico",
    "houston": "united_states",
    "kansas city": "united_states",
    "los angeles": "united_states",
    "mexico city": "mexico",
    "miami": "united_states",
    "monterrey": "mexico",
    "new jersey": "united_states",
    "new york": "united_states",
    "philadelphia": "united_states",
    "san francisco": "united_states",
    "seattle": "united_states",
    "toronto": "canada",
    "vancouver": "canada",
}


@dataclass(frozen=True)
class MatchAnalysisInput:
    fixture: Fixture
    odds_event: ParsedOddsEvent
    home_elo: EloRating
    away_elo: EloRating
    quotes: list[OddsQuote] = field(default_factory=list)
    neutral: bool = True
    home_advantage_elo: float = 0.0
    lineup_context: dict[str, Any] | None = None


@dataclass(frozen=True)
class BuildMatchInputsResult:
    inputs: list[MatchAnalysisInput]
    missing_odds: list[str] = field(default_factory=list)
    missing_elo: list[str] = field(default_factory=list)
    time_mismatches: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchAnalysis:
    match_input: MatchAnalysisInput
    lambdas: tuple[float, float]
    poisson_tail: float
    mu_total_used: float
    ou_line: float
    elo_1x2: dict[str, float]
    poisson_1x2: dict[str, float]
    combined_1x2: dict[str, float]
    ou_2_5: dict[str, float]
    handicap_dist: dict[int, float]
    mu_prior_used: float
    mu_market_used: float | None
    mu_market_weight: float
    total_mu_source: str
    same_market_total_anchor: bool
    probability_families: dict
    lineup_shadow: dict | None
    ou_total_shadow: dict | None
    market_1x2: dict
    market_ou_2_5: dict
    market_ah_main: dict | None


# Keep facade imports after public dataclasses so split modules can reference
# these types for annotations without a runtime circular import.
from worldcup.pipeline_analysis import analyze_match_input
from worldcup.pipeline_signals import (
    _aggregate_ah_main,
    _ah_validation_shadow,
    _main_ou_line,
    _round_metric,
    generate_value_signals,
)

def _match_label(fixture: Fixture) -> str:
    return f"{fixture.home_team_name} vs {fixture.away_team_name}"


def _event_key(kickoff_at_utc: datetime, home_canonical: str | None, away_canonical: str | None) -> tuple:
    return (kickoff_at_utc, home_canonical, away_canonical)


def _pair_key(home_canonical: str | None, away_canonical: str | None) -> tuple:
    return (home_canonical, away_canonical)


def _lineup_event_key(
    kickoff_at_utc: datetime | None,
    home_canonical: str | None,
    away_canonical: str | None,
) -> tuple:
    return (kickoff_at_utc, home_canonical, away_canonical)


def _lineup_matches_fixture(fixture: Fixture, context: ParsedLineupContext) -> bool:
    return (
        context.kickoff_at_utc is not None
        and context.kickoff_at_utc == fixture.kickoff_at_utc
        and context.home_canonical == fixture.home_canonical
        and context.away_canonical == fixture.away_canonical
    )


def _canonical_to_elo_code(elo_aliases: dict[str, str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for name, code in elo_aliases.items():
        mapping[canonicalize_team(name)] = code
    return mapping


def _world_cup_2026_host_canonical(venue_name: str | None) -> str | None:
    normalized = (venue_name or "").lower()
    for fragment, canonical in _WORLD_CUP_2026_HOST_VENUES.items():
        if fragment in normalized:
            return canonical
    return None


def _fixture_home_advantage_elo(fixture: Fixture, host_advantage_elo: float) -> float:
    host = _world_cup_2026_host_canonical(fixture.venue_name)
    if host is None:
        return 0.0
    if fixture.home_canonical == host and fixture.away_canonical != host:
        return host_advantage_elo
    if fixture.away_canonical == host and fixture.home_canonical != host:
        return -host_advantage_elo
    return 0.0


def build_match_inputs(
    fixtures: list[Fixture],
    odds_events: list[ParsedOddsEvent],
    elo_ratings: dict[str, EloRating],
    elo_aliases: dict[str, str],
    host_advantage_elo: float = 100.0,
    lineup_contexts: list[ParsedLineupContext] | None = None,
) -> BuildMatchInputsResult:
    event_by_key = {
        _event_key(event.kickoff_at_utc, event.home_canonical, event.away_canonical): event
        for event in odds_events
    }
    events_by_pair: dict[tuple, list[ParsedOddsEvent]] = {}
    for event in odds_events:
        events_by_pair.setdefault(_pair_key(event.home_canonical, event.away_canonical), []).append(event)
    lineup_by_key = {
        _lineup_event_key(context.kickoff_at_utc, context.home_canonical, context.away_canonical): context
        for context in lineup_contexts or []
        if context.kickoff_at_utc is not None
    }
    lineup_by_match_no = {
        context.source_match_no: context
        for context in lineup_contexts or []
        if context.source_match_no is not None
    }
    elo_code_by_canonical = _canonical_to_elo_code(elo_aliases)

    inputs: list[MatchAnalysisInput] = []
    missing_odds: list[str] = []
    missing_elo: list[str] = []
    time_mismatches: list[str] = []

    for fixture in fixtures:
        if fixture.has_placeholder_team:
            continue
        home_code = elo_code_by_canonical.get(fixture.home_canonical or "")
        away_code = elo_code_by_canonical.get(fixture.away_canonical or "")
        home_elo = elo_ratings.get(home_code or "")
        away_elo = elo_ratings.get(away_code or "")
        if home_elo is None:
            missing_elo.append(fixture.home_team_name)
        if away_elo is None:
            missing_elo.append(fixture.away_team_name)

        event = event_by_key.get(
            _event_key(fixture.kickoff_at_utc, fixture.home_canonical, fixture.away_canonical)
        )
        if event is None:
            pair_events = events_by_pair.get(_pair_key(fixture.home_canonical, fixture.away_canonical), [])
            if len(pair_events) == 1:
                event = pair_events[0]
                time_mismatches.append(_match_label(fixture))
        if event is None:
            missing_odds.append(_match_label(fixture))

        if event is None or home_elo is None or away_elo is None:
            continue

        lineup = None
        if fixture.source_match_no is not None:
            match_no_candidate = lineup_by_match_no.get(fixture.source_match_no)
            if match_no_candidate is not None and _lineup_matches_fixture(fixture, match_no_candidate):
                lineup = match_no_candidate
        if lineup is None:
            lineup = lineup_by_key.get(
                _lineup_event_key(fixture.kickoff_at_utc, fixture.home_canonical, fixture.away_canonical)
            )

        inputs.append(
            MatchAnalysisInput(
                fixture=fixture,
                odds_event=event,
                home_elo=home_elo,
                away_elo=away_elo,
                quotes=event.quotes,
                home_advantage_elo=_fixture_home_advantage_elo(fixture, host_advantage_elo),
                lineup_context=lineup.to_pipeline_context() if lineup is not None else None,
            )
        )

    return BuildMatchInputsResult(
        inputs=inputs,
        missing_odds=missing_odds,
        missing_elo=missing_elo,
        time_mismatches=time_mismatches,
    )
