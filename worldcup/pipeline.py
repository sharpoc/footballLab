from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from typing import Any

from worldcup.collectors.models import EloRating, Fixture, ParsedLineupContext, ParsedOddsEvent
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.engine import elo, ensemble, handicap, odds, poisson, value
from worldcup.models import Grade, MarketType, OddsQuote, Signal

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


def _adjusted_dr(match_input: MatchAnalysisInput, cfg: dict) -> float:
    dr = match_input.home_elo.rating - match_input.away_elo.rating
    if match_input.home_advantage_elo:
        dr += match_input.home_advantage_elo
    elif not match_input.neutral:
        dr += cfg["elo"]["home_adv"]
    return dr


def _elo_home_advantage(match_input: MatchAnalysisInput, cfg: dict) -> float | None:
    if match_input.home_advantage_elo:
        return match_input.home_advantage_elo
    if not match_input.neutral:
        return cfg["elo"]["home_adv"]
    return None


def _model_probability_block(
    *,
    dr: float,
    cfg: dict,
    mu_total: float,
    ou_line: float,
    elo_1x2: dict[str, float],
    role: str,
    provenance: dict,
    mu_source: str,
    mu_prior: float | None = None,
    mu_market: float | None = None,
    mu_market_weight: float | None = None,
    same_market_total_anchor: bool | None = None,
) -> dict:
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_total)
    matrix, tail = poisson.score_matrix(lh, la, cfg["poisson"])
    poisson_1x2 = poisson.probs_1x2(matrix)
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2,
        poisson_1x2,
        cfg["ensemble"]["w_elo"],
        cfg["ensemble"]["w_poisson"],
    )
    p_over = poisson.prob_over(matrix, ou_line)
    block = {
        "provenance": {"role": role, **provenance},
        "mu_total": mu_total,
        "mu_source": mu_source,
        "ou_line": ou_line,
        "lambdas": {"home": lh, "away": la},
        "poisson_tail": tail,
        "elo_1x2": elo_1x2,
        "poisson_1x2": poisson_1x2,
        "combined_1x2": combined_1x2,
        "ou": {"line": ou_line, "probs": {"over": p_over, "under": 1.0 - p_over}},
    }
    if mu_prior is not None:
        block["mu_prior"] = mu_prior
    if mu_market is not None:
        block["mu_market"] = mu_market
    if mu_market_weight is not None:
        block["mu_market_weight"] = mu_market_weight
    if same_market_total_anchor is not None:
        block["same_market_total_anchor"] = same_market_total_anchor
    return block


def _market_only_probability_block(market_1x2: dict, market_ou: dict, market_ah: dict | None) -> dict:
    return {
        "provenance": {
            "family": "market_only",
            "role": "baseline_diagnostic",
            "uses_same_match_market_probability": True,
            "uses_market_total_anchor": False,
            "uses_market_line": True,
            "allowed_for_value_signal": False,
        },
        "1x2": dict(market_1x2.get("market_probs") or {}),
        "ou": {
            "line": market_ou.get("line"),
            "probs": dict(market_ou.get("market_probs") or {}),
        },
        "ah": {
            "line_home": (market_ah or {}).get("line_home"),
            "status": "diagnostic_only",
            "probs": {},
        },
    }


def _probability_families(
    *,
    dr: float,
    cfg: dict,
    mu_prior: float,
    mu_market: float | None,
    mu_market_weight: float,
    total_mu_source: str,
    same_market_total_anchor: bool,
    mu_total: float,
    ou_line: float,
    elo_1x2: dict[str, float],
    market_1x2: dict,
    market_ou: dict,
    market_ah: dict | None,
) -> dict:
    raw_family = _model_probability_block(
        dr=dr,
        cfg=cfg,
        mu_total=mu_prior,
        ou_line=ou_line,
        elo_1x2=elo_1x2,
        role="value_candidate",
        provenance={
            "family": "model_raw",
            "uses_same_match_market_probability": False,
            "uses_market_total_anchor": False,
            "uses_market_line": True,
            "allowed_for_value_signal": True,
            "activation": "shadow_only",
        },
        mu_source="prior",
    )
    market_total_family = _model_probability_block(
        dr=dr,
        cfg=cfg,
        mu_total=mu_total,
        ou_line=ou_line,
        elo_1x2=elo_1x2,
        role="legacy_active_model",
        provenance={
            "family": "model_market_total",
            "uses_same_match_market_probability": same_market_total_anchor,
            "uses_market_total_anchor": same_market_total_anchor,
            "uses_market_line": True,
            "allowed_for_value_signal": True,
            "same_market_value_restrictions": ["no_strong_ou_when_market_total_anchor"],
        },
        mu_source=total_mu_source,
        mu_prior=mu_prior,
        mu_market=mu_market,
        mu_market_weight=mu_market_weight,
        same_market_total_anchor=same_market_total_anchor,
    )
    return {
        "schema_version": 1,
        "active_signal_family": "model_market_total",
        "recommended_future_signal_family": "model_raw",
        "families": {
            "model_raw": raw_family,
            "model_market_total": market_total_family,
            "market_only": _market_only_probability_block(market_1x2, market_ou, market_ah),
        },
    }


def _parse_lineup_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _latest_quote_observed_at(quotes: list[OddsQuote]) -> datetime | None:
    observed = [
        quote.fetched_at.astimezone(timezone.utc)
        for quote in quotes
        if quote.fetched_at is not None
    ]
    return max(observed) if observed else None


def _lineup_float(context: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(context.get(key, default))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, max_abs: float) -> float:
    return max(-max_abs, min(max_abs, value))


def _rounded_probs(probs: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in probs.items()}


def _edge_shadow(model_probs: dict[str, float], market: dict) -> dict[str, float | None]:
    market_probs = market.get("market_probs") or {}
    out: dict[str, float | None] = {}
    for key, model_prob in model_probs.items():
        market_prob = market_probs.get(key)
        out[key] = round(model_prob - market_prob, 6) if market_prob is not None else None
    return out


def _edge_delta(
    independent: dict[str, float | None],
    active: dict[str, float | None],
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key, independent_value in independent.items():
        active_value = active.get(key)
        out[key] = (
            round(independent_value - active_value, 6)
            if independent_value is not None and active_value is not None
            else None
        )
    return out


def _ou_total_shadow_block(
    *,
    probability_families: dict,
    active_ou_probs: dict[str, float],
    market_ou: dict,
    ou_line: float,
    mu_active: float,
    mu_independent: float,
    mu_market: float | None,
    mu_market_weight: float,
    same_market_total_anchor: bool,
) -> dict:
    families = probability_families.get("families") or {}
    raw_ou = (families.get("model_raw") or {}).get("ou") or {}
    independent_probs = dict(raw_ou.get("probs") or {})
    if not independent_probs:
        independent_probs = dict(active_ou_probs)
    active_edge = _edge_shadow(active_ou_probs, market_ou)
    independent_edge = _edge_shadow(independent_probs, market_ou)
    return {
        "schema_version": 1,
        "activation": "shadow_only",
        "active_family": probability_families.get("active_signal_family"),
        "shadow_family": "model_raw",
        "same_market_total_anchor": same_market_total_anchor,
        "line": ou_line,
        "mu_active": mu_active,
        "mu_independent": mu_independent,
        "mu_market": mu_market,
        "mu_market_weight": mu_market_weight,
        "probability_active": {"line": ou_line, "probs": dict(active_ou_probs)},
        "probability_independent": {"line": ou_line, "probs": independent_probs},
        "market_probability": {
            "line": market_ou.get("line"),
            "probs": dict(market_ou.get("market_probs") or {}),
        },
        "edge_active": active_edge,
        "edge_independent": independent_edge,
        "edge_delta_independent_minus_active": _edge_delta(independent_edge, active_edge),
    }


def _lineup_shadow_block(
    *,
    match_input: MatchAnalysisInput,
    cfg: dict,
    lambdas: tuple[float, float],
    elo_1x2: dict[str, float],
    combined_1x2: dict[str, float],
    ou_probs: dict[str, float],
    ou_line: float,
    market_1x2: dict,
    market_ou: dict,
) -> dict | None:
    context = match_input.lineup_context or {}
    if not context:
        return None

    max_abs = abs(float(cfg.get("quality", {}).get("lineup_log_lambda_delta_abs_max", 0.35)))
    home_log_delta = _clamp(
        _lineup_float(context, "home_attack_delta")
        - _lineup_float(context, "away_defense_delta")
        + _lineup_float(context, "away_goalkeeper_delta"),
        max_abs,
    )
    away_log_delta = _clamp(
        _lineup_float(context, "away_attack_delta")
        - _lineup_float(context, "home_defense_delta")
        + _lineup_float(context, "home_goalkeeper_delta"),
        max_abs,
    )

    adjusted_home = lambdas[0] * math.exp(home_log_delta)
    adjusted_away = lambdas[1] * math.exp(away_log_delta)
    matrix, tail = poisson.score_matrix(adjusted_home, adjusted_away, cfg["poisson"])
    adjusted_poisson_1x2 = poisson.probs_1x2(matrix)
    adjusted_combined_1x2 = ensemble.combine_1x2(
        elo_1x2,
        adjusted_poisson_1x2,
        cfg["ensemble"]["w_elo"],
        cfg["ensemble"]["w_poisson"],
    )
    adjusted_over = poisson.prob_over(matrix, ou_line)
    adjusted_ou = {"over": adjusted_over, "under": 1.0 - adjusted_over}

    lineup_confirmed_at = _parse_lineup_datetime(context.get("lineup_confirmed_at"))
    odds_observed_at = _latest_quote_observed_at(match_input.quotes)
    post_information_odds_available = (
        lineup_confirmed_at is not None
        and odds_observed_at is not None
        and odds_observed_at >= lineup_confirmed_at
    )
    confidence = context.get("lineup_confidence")
    lineup_confidence = None
    if confidence is not None:
        lineup_confidence = _clamp(_lineup_float(context, "lineup_confidence"), 1.0)
        if lineup_confidence < 0:
            lineup_confidence = 0.0

    return {
        "schema_version": 1,
        "activation": "shadow_only",
        "provider": context.get("provider"),
        "source": context.get("source"),
        "source_match_no": context.get("source_match_no"),
        "confirmed_starting_xi": bool(context.get("confirmed_starting_xi", False)),
        "lineup_confirmed_at": lineup_confirmed_at.isoformat() if lineup_confirmed_at else None,
        "odds_observed_at": odds_observed_at.isoformat() if odds_observed_at else None,
        "post_information_odds_available": post_information_odds_available,
        "lineup_confidence": lineup_confidence,
        "lineups": context.get("lineups"),
        "deltas": {
            "home_attack_delta": _round_metric(_lineup_float(context, "home_attack_delta")),
            "home_defense_delta": _round_metric(_lineup_float(context, "home_defense_delta")),
            "home_goalkeeper_delta": _round_metric(_lineup_float(context, "home_goalkeeper_delta")),
            "away_attack_delta": _round_metric(_lineup_float(context, "away_attack_delta")),
            "away_defense_delta": _round_metric(_lineup_float(context, "away_defense_delta")),
            "away_goalkeeper_delta": _round_metric(_lineup_float(context, "away_goalkeeper_delta")),
            "home_log_lambda_delta": _round_metric(home_log_delta),
            "away_log_lambda_delta": _round_metric(away_log_delta),
        },
        "lambda_base": {"home": _round_metric(lambdas[0]), "away": _round_metric(lambdas[1])},
        "lambda_adjusted": {"home": _round_metric(adjusted_home), "away": _round_metric(adjusted_away)},
        "poisson_tail": _round_metric(tail),
        "probability_before": {
            "1x2": _rounded_probs(combined_1x2),
            "ou": {"line": ou_line, "probs": _rounded_probs(ou_probs)},
        },
        "probability_after": {
            "1x2": _rounded_probs(adjusted_combined_1x2),
            "ou": {"line": ou_line, "probs": _rounded_probs(adjusted_ou)},
        },
        "edge_before": {
            "1x2": _edge_shadow(combined_1x2, market_1x2),
            "ou": _edge_shadow(ou_probs, market_ou),
        },
        "edge_after": {
            "1x2": _edge_shadow(adjusted_combined_1x2, market_1x2),
            "ou": _edge_shadow(adjusted_ou, market_ou),
        },
    }


def analyze_match_input(match_input: MatchAnalysisInput, cfg: dict) -> MatchAnalysis:
    dr = _adjusted_dr(match_input, cfg)
    ratio = cfg["odds"]["outlier_ratio"]
    ou_line = _main_ou_line(match_input.quotes, fallback_line=cfg.get("ou_main_line", 2.5))
    market_ou_2_5 = odds.aggregate_market(
        match_input.quotes,
        market_type=MarketType.OU,
        line=ou_line,
        selections=["over", "under"],
        ratio=ratio,
    )
    market_ou_2_5["line"] = ou_line
    p_over_available = market_ou_2_5["market_probs"].get("over")
    p_over_market = p_over_available
    if p_over_market is not None:
        ou_books = market_ou_2_5["n_books_by_selection"]
        if min(ou_books.get("over", 0), ou_books.get("under", 0)) < cfg["odds"]["min_books"]:
            p_over_market = None
    mu_prior_used = poisson.prior_mu(dr, cfg["poisson"])
    mu_market_weight = cfg["poisson"].get("mu_market_weight", 0.0)
    market_total_enabled = mu_market_weight > 0
    mu_market_used = (
        poisson.implied_total_mu(p_over_market, ou_line)
        if p_over_market is not None and market_total_enabled
        else None
    )
    same_market_total_anchor = mu_market_used is not None
    if same_market_total_anchor:
        total_mu_source = "market_informed"
    elif p_over_available is not None and market_total_enabled:
        total_mu_source = "fallback"
    else:
        total_mu_source = "prior"
    mu_total_used = poisson.blended_mu(
        p_over_market,
        ou_line,
        cfg["poisson"],
        dr=dr,
    )
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_total_used)
    matrix, tail = poisson.score_matrix(lh, la, cfg["poisson"])
    handicap_dist = handicap.diff_distribution(matrix)
    poisson_1x2 = poisson.probs_1x2(matrix)
    elo_1x2 = elo.win_draw_loss(
        match_input.home_elo.rating,
        match_input.away_elo.rating,
        neutral=match_input.neutral,
        cfg=cfg["elo"],
        home_advantage=_elo_home_advantage(match_input, cfg),
    )
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2,
        poisson_1x2,
        cfg["ensemble"]["w_elo"],
        cfg["ensemble"]["w_poisson"],
    )
    p_over = poisson.prob_over(matrix, ou_line)
    market_1x2 = odds.aggregate_market(
        match_input.quotes,
        market_type=MarketType.X12,
        line=None,
        selections=["home", "draw", "away"],
        ratio=ratio,
    )
    market_ah_main = _aggregate_ah_main(match_input.quotes, ratio)
    probability_families = _probability_families(
        dr=dr,
        cfg=cfg,
        mu_prior=mu_prior_used,
        mu_market=mu_market_used,
        mu_market_weight=mu_market_weight,
        total_mu_source=total_mu_source,
        same_market_total_anchor=same_market_total_anchor,
        mu_total=mu_total_used,
        ou_line=ou_line,
        elo_1x2=elo_1x2,
        market_1x2=market_1x2,
        market_ou=market_ou_2_5,
        market_ah=market_ah_main,
    )
    ou_total_shadow = _ou_total_shadow_block(
        probability_families=probability_families,
        active_ou_probs={"over": p_over, "under": 1.0 - p_over},
        market_ou=market_ou_2_5,
        ou_line=ou_line,
        mu_active=mu_total_used,
        mu_independent=mu_prior_used,
        mu_market=mu_market_used,
        mu_market_weight=mu_market_weight,
        same_market_total_anchor=same_market_total_anchor,
    )
    lineup_shadow = _lineup_shadow_block(
        match_input=match_input,
        cfg=cfg,
        lambdas=(lh, la),
        elo_1x2=elo_1x2,
        combined_1x2=combined_1x2,
        ou_probs={"over": p_over, "under": 1.0 - p_over},
        ou_line=ou_line,
        market_1x2=market_1x2,
        market_ou=market_ou_2_5,
    )
    return MatchAnalysis(
        match_input=match_input,
        lambdas=(lh, la),
        poisson_tail=tail,
        mu_total_used=mu_total_used,
        ou_line=ou_line,
        elo_1x2=elo_1x2,
        poisson_1x2=poisson_1x2,
        combined_1x2=combined_1x2,
        ou_2_5={"over": p_over, "under": 1.0 - p_over},
        handicap_dist=handicap_dist,
        mu_prior_used=mu_prior_used,
        mu_market_used=mu_market_used,
        mu_market_weight=mu_market_weight,
        total_mu_source=total_mu_source,
        same_market_total_anchor=same_market_total_anchor,
        probability_families=probability_families,
        lineup_shadow=lineup_shadow,
        ou_total_shadow=ou_total_shadow,
        market_1x2=market_1x2,
        market_ou_2_5=market_ou_2_5,
        market_ah_main=market_ah_main,
    )


def _value_cfg(cfg: dict) -> dict:
    value_cfg = dict(cfg["value"])
    value_cfg["min_books"] = cfg["odds"]["min_books"]
    quality_cfg = cfg.get("quality", {})
    if "odds_dispersion_ratio_max" in quality_cfg:
        value_cfg["odds_dispersion_ratio_max"] = quality_cfg["odds_dispersion_ratio_max"]
    return value_cfg


def _top_probability_key(probs: dict[str, float]) -> str | None:
    if not probs:
        return None
    return max(probs, key=probs.get)


def _model_disagreement(analysis: MatchAnalysis, selection: str, cfg: dict) -> bool:
    threshold = cfg.get("quality", {}).get("disagreement_prob_delta")
    if threshold is None:
        return False
    elo_top = _top_probability_key(analysis.elo_1x2)
    poisson_top = _top_probability_key(analysis.poisson_1x2)
    if elo_top is None or poisson_top is None:
        return False
    if elo_top != poisson_top:
        return True
    elo_prob = analysis.elo_1x2.get(selection)
    poisson_prob = analysis.poisson_1x2.get(selection)
    if elo_prob is None or poisson_prob is None:
        return False
    return abs(elo_prob - poisson_prob) >= threshold


def _host_side(match_input: MatchAnalysisInput) -> str | None:
    if match_input.home_advantage_elo > 0:
        return "home"
    if match_input.home_advantage_elo < 0:
        return "away"
    return None


def _quality_threshold(cfg: dict, key: str, default: float) -> float:
    return float(cfg.get("quality", {}).get(key, default))


def _ah_main_has_min_books(analysis: MatchAnalysis, cfg: dict) -> bool:
    market = analysis.market_ah_main
    if market is None:
        return False
    n_books = market.get("n_books_by_selection", {})
    return min(n_books.get("home", 0), n_books.get("away", 0)) >= cfg["odds"]["min_books"]


def _ah_main_line_home(analysis: MatchAnalysis) -> float | None:
    line = (analysis.market_ah_main or {}).get("line_home")
    return float(line) if line is not None else None


def _round_metric(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _quarter_lines(min_line: float = -6.0, max_line: float = 6.0) -> list[float]:
    start = int(round(min_line * 4))
    end = int(round(max_line * 4))
    return [step / 4 for step in range(start, end + 1)]


def _model_fair_ah_line(dist: dict[int, float]) -> float | None:
    viable = [
        line
        for line in _quarter_lines()
        if handicap.ev_handicap(dist, line, 2.0) >= -1e-9
    ]
    return min(viable) if viable else None


def _ah_validation_shadow(
    analysis: MatchAnalysis,
    side: str,
    market_line: float,
    side_dist: dict[int, float],
    dispersion_ratio: float | None,
    cfg: dict,
) -> dict:
    model_fair_line = _model_fair_ah_line(side_dist)
    fair_line_delta = market_line - model_fair_line if model_fair_line is not None else None
    threshold = cfg.get("quality", {}).get("odds_dispersion_ratio_max")
    line_consensus = _ah_main_has_min_books(analysis, cfg)
    dispersion_ok = threshold is None or dispersion_ratio is None or dispersion_ratio <= threshold
    candidate_validated = (
        line_consensus
        and dispersion_ok
        and fair_line_delta is not None
        and fair_line_delta >= 0.25
    )
    return {
        "schema_version": 1,
        "side": side,
        "market_line": _round_metric(market_line),
        "model_fair_line": _round_metric(model_fair_line),
        "fair_line_delta": _round_metric(fair_line_delta),
        "line_consensus": line_consensus,
        "dispersion_ok": dispersion_ok,
        "candidate_validated": candidate_validated,
        "activation": "shadow_only",
    }


def _ah_main_supports_x12(analysis: MatchAnalysis, selection: str, cfg: dict) -> bool:
    if selection not in ("home", "away") or not _ah_main_has_min_books(analysis, cfg):
        return False
    line_home = _ah_main_line_home(analysis)
    if line_home is None:
        return False
    if selection == "home":
        return line_home < 0
    return line_home > 0


def _x12_confidence_guard_reasons(analysis: MatchAnalysis, signal: Signal, cfg: dict) -> list[str]:
    if signal.market_type != MarketType.X12:
        return []

    reasons: list[str] = []
    selection = signal.selection
    if selection in ("home", "away"):
        market_leader = _top_probability_key(analysis.market_1x2.get("market_probs", {}))
        if market_leader is not None and market_leader != selection:
            reasons.append("reverse_market")
        if analysis.market_ah_main is None or not _ah_main_has_min_books(analysis, cfg):
            reasons.append("ah_cross_check_missing")
        elif not _ah_main_supports_x12(analysis, selection, cfg):
            reasons.append("ah_not_supporting_1x2")

    if selection == _host_side(analysis.match_input):
        p_market = analysis.market_1x2.get("market_probs", {}).get(selection)
        threshold = _quality_threshold(cfg, "host_x12_market_prob_min_for_strong", 0.60)
        if p_market is not None and p_market < threshold:
            reasons.append("host_market_confirmation")

    return reasons


def _big_handicap(analysis: MatchAnalysis, cfg: dict) -> bool:
    line_home = _ah_main_line_home(analysis)
    if line_home is None or not _ah_main_has_min_books(analysis, cfg):
        return False
    threshold = _quality_threshold(cfg, "extreme_favorite_ah_abs_line_min", 2.5)
    return abs(line_home) >= threshold


def _extreme_favorite_side(analysis: MatchAnalysis, cfg: dict) -> str | None:
    market_probs = analysis.market_1x2.get("market_probs", {})
    market_leader = _top_probability_key(market_probs)
    market_threshold = _quality_threshold(cfg, "extreme_favorite_market_prob_min", 0.85)
    if market_leader is not None and market_probs.get(market_leader, 0.0) >= market_threshold:
        return market_leader
    if _big_handicap(analysis, cfg):
        line_home = _ah_main_line_home(analysis)
        if line_home is not None:
            if line_home < 0:
                return "home"
            if line_home > 0:
                return "away"
    return None


def _ou_confidence_guard_reasons(analysis: MatchAnalysis, signal: Signal, cfg: dict) -> list[str]:
    if signal.market_type != MarketType.OU or signal.selection != "under":
        return []
    max_under_line = _quality_threshold(cfg, "big_handicap_under_line_max", 2.5)
    if signal.line is not None and signal.line <= max_under_line and _big_handicap(analysis, cfg):
        return ["under_vs_big_handicap"]
    return []


def _ah_signal_side(selection: str) -> str | None:
    if selection.startswith("home_"):
        return "home"
    if selection.startswith("away_"):
        return "away"
    return None


def _ah_confidence_guard_reasons(analysis: MatchAnalysis, signal: Signal, cfg: dict) -> list[str]:
    if signal.market_type != MarketType.AH:
        return []
    reasons: list[str] = []
    signal_side = _ah_signal_side(signal.selection)
    line_home = _ah_main_line_home(analysis)
    if line_home is None:
        return reasons
    zero_threshold = _quality_threshold(cfg, "ah_zero_abs_line_max_for_strong", 0.25)
    if signal.line is not None and abs(signal.line) <= zero_threshold:
        reasons.append("ah_zero_line_confirmation")
    favorite_side = _extreme_favorite_side(analysis, cfg)
    if favorite_side is not None and signal_side is not None and signal_side != favorite_side:
        reasons.append("extreme_favorite_handicap")
    if signal_side == _host_side(analysis.match_input):
        threshold = _quality_threshold(cfg, "host_ah_abs_line_min_for_strong", 1.0)
        if abs(line_home) < threshold:
            reasons.append("host_handicap_confirmation")
    return reasons


def _cap_signal_at_b(signal: Signal, reasons: list[str]) -> Signal:
    if not reasons:
        return signal
    merged_reasons = list(signal.reasons)
    for reason in reasons:
        if reason not in merged_reasons:
            merged_reasons.append(reason)
    grade = Grade.B if signal.grade in (Grade.S, Grade.A) else signal.grade
    return replace(signal, grade=grade, reasons=merged_reasons)


_CANDIDATE_HARD_VETO_REASONS = {
    "stale_odds",
    "few_books",
    "market_dispersion",
    "longshot_uncertainty",
    "unconfirmed_backup",
    "line_changed_unknown",
    "model_disagreement",
}


def _candidate_metadata(signal: Signal) -> tuple[str | None, list[str]]:
    raw_grade = signal.raw_grade or signal.grade
    if raw_grade not in (Grade.S, Grade.A) or signal.grade in (Grade.S, Grade.A):
        return None, []
    if any(reason in _CANDIDATE_HARD_VETO_REASONS for reason in signal.reasons):
        return None, []
    if signal.market_type != MarketType.AH or "ah_market_edge_missing" not in signal.reasons:
        return None, []
    shadow = signal.ah_validation_shadow or {}
    if shadow.get("candidate_validated") is not True:
        return None, []
    return (
        f"{raw_grade.value}-candidate",
        [
            "official_grade_capped_by_ah_market_edge_missing",
            "ah_validation_shadow_candidate_validated",
        ],
    )


def _apply_candidate_grades(signals: list[Signal]) -> list[Signal]:
    out: list[Signal] = []
    for signal in signals:
        candidate_grade, candidate_reasons = _candidate_metadata(signal)
        if candidate_grade is None:
            out.append(signal)
        else:
            out.append(
                replace(
                    signal,
                    candidate_grade=candidate_grade,
                    candidate_reasons=candidate_reasons,
                )
            )
    return out


def _apply_confidence_guards(analysis: MatchAnalysis, cfg: dict, signals: list[Signal]) -> list[Signal]:
    guarded: list[Signal] = []
    for signal in signals:
        reasons = [
            *_x12_confidence_guard_reasons(analysis, signal, cfg),
            *_ou_confidence_guard_reasons(analysis, signal, cfg),
            *_ah_confidence_guard_reasons(analysis, signal, cfg),
        ]
        guarded.append(_cap_signal_at_b(signal, reasons))
    return _apply_candidate_grades(guarded)


def _normalize_observed_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        observed = value
    else:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _line_matches(quote_line: float | None, line: float | None) -> bool:
    if line is None:
        return True
    return quote_line is not None and abs(quote_line - line) < 1e-9


def _odds_age_seconds(
    quotes: list[OddsQuote],
    market_type: MarketType,
    selection: str,
    line: float | None,
    observed_at: datetime | None,
) -> float | None:
    if observed_at is None:
        return None
    fetched_times = [
        quote.fetched_at.astimezone(timezone.utc)
        for quote in quotes
        if quote.market_type == market_type
        and quote.selection == selection
        and _line_matches(quote.line, line)
        and quote.fetched_at is not None
    ]
    if not fetched_times:
        return None
    latest_fetched_at = max(fetched_times)
    return max(0.0, (observed_at - latest_fetched_at).total_seconds())


def _signal_ctx(
    match_input: MatchAnalysisInput,
    market_type: MarketType,
    selection: str,
    line: float | None,
    n_books: int,
    observed_at: datetime | None,
    depends_on_backup: bool,
    model_disagreement: bool = False,
    odds_dispersion_ratio: float | None = None,
    total_mu_source: str | None = None,
    same_market_total_anchor: bool | None = None,
    ah_market_validated: bool | None = None,
    ah_validation_shadow: dict | None = None,
) -> dict:
    ctx = {
        "status": "OK",
        "n_books": n_books,
        "depends_on_backup": depends_on_backup,
        "line_changed_unknown": False,
    }
    odds_age_seconds = _odds_age_seconds(
        match_input.quotes,
        market_type,
        selection,
        line,
        observed_at,
    )
    if odds_age_seconds is not None:
        ctx["odds_age_seconds"] = odds_age_seconds
    if model_disagreement:
        ctx["model_disagreement"] = True
    if odds_dispersion_ratio is not None:
        ctx["odds_dispersion_ratio"] = odds_dispersion_ratio
    if total_mu_source is not None:
        ctx["total_mu_source"] = total_mu_source
    if same_market_total_anchor is not None:
        ctx["same_market_total_anchor"] = same_market_total_anchor
    if ah_market_validated is not None:
        ctx["ah_market_validated"] = ah_market_validated
    if ah_validation_shadow is not None:
        ctx["ah_validation_shadow"] = ah_validation_shadow
    return ctx


def _line_label(line: float) -> str:
    return f"{line:+g}" if line > 0 else f"{line:g}"


def _half_goal_line(line: float) -> bool:
    doubled = round(line * 2)
    return abs(line * 2 - doubled) < 1e-9 and doubled % 2 != 0


def _main_ou_line(quotes: list[OddsQuote], fallback_line: float = 2.5) -> float:
    over_counts: dict[float, int] = {}
    under_counts: dict[float, int] = {}
    for quote in quotes:
        if quote.market_type != MarketType.OU or quote.line is None:
            continue
        if not _half_goal_line(quote.line):
            continue
        if quote.selection == "over":
            over_counts[quote.line] = over_counts.get(quote.line, 0) + 1
        elif quote.selection == "under":
            under_counts[quote.line] = under_counts.get(quote.line, 0) + 1
    paired_counts = {
        line: min(over_counts.get(line, 0), under_counts.get(line, 0))
        for line in set(over_counts) | set(under_counts)
    }
    paired_counts = {line: count for line, count in paired_counts.items() if count > 0}
    if not paired_counts:
        return float(fallback_line)
    return sorted(
        paired_counts,
        key=lambda line: (-paired_counts[line], abs(line - fallback_line), line),
    )[0]


def _main_home_ah_line(quotes: list[OddsQuote]) -> float | None:
    counts: dict[float, int] = {}
    for quote in quotes:
        if quote.market_type == MarketType.AH and quote.selection == "home" and quote.line is not None:
            counts[quote.line] = counts.get(quote.line, 0) + 1
    if not counts:
        return None
    return sorted(counts, key=lambda line: (-counts[line], abs(line), line))[0]


def _aggregate_ah_main(quotes: list[OddsQuote], ratio: float) -> dict | None:
    home_line = _main_home_ah_line(quotes)
    if home_line is None:
        return None
    block: dict = {"line_home": home_line, "odds": {}, "n_books_by_selection": {}}
    for selection, line in (("home", home_line), ("away", -home_line)):
        agg = odds.aggregate(quotes, MarketType.AH, selection, line=line, ratio=ratio)
        block["n_books_by_selection"][selection] = agg["n_books"]
        if agg["odds"] is not None:
            block["odds"][selection] = agg["odds"]
    return block


def _invert_dist(dist: dict[int, float]) -> dict[int, float]:
    return {-diff: prob for diff, prob in dist.items()}


def _integer_market_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    market_type: MarketType,
    selections: list[str],
    model_probs: dict[str, float],
    market: dict,
    line: float | None = None,
    observed_at: datetime | None = None,
    depends_on_backup: bool = False,
    total_mu_source: str | None = None,
    same_market_total_anchor: bool | None = None,
) -> list[Signal]:
    value_cfg = _value_cfg(cfg)
    out: list[Signal] = []
    market_probs = market.get("market_probs", {})
    market_odds = market.get("odds", {})
    n_books = market.get("n_books_by_selection", {})
    dispersion_by_selection = market.get("dispersion_by_selection", {})
    for selection in selections:
        out.append(
            value.grade_signal(
                market_type,
                selection,
                model_probs[selection],
                market_probs.get(selection),
                market_odds.get(selection),
                _signal_ctx(
                    analysis.match_input,
                    market_type,
                    selection,
                    line,
                    n_books.get(selection, 0),
                    observed_at,
                    depends_on_backup,
                    model_disagreement=(
                        market_type == MarketType.X12 and _model_disagreement(analysis, selection, cfg)
                    ),
                    odds_dispersion_ratio=dispersion_by_selection.get(selection),
                    total_mu_source=total_mu_source,
                    same_market_total_anchor=same_market_total_anchor,
                ),
                value_cfg,
                line=line,
            )
        )
    return out


def _ah_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    observed_at: datetime | None = None,
    depends_on_backup: bool = False,
) -> list[Signal]:
    home_line = _main_home_ah_line(analysis.match_input.quotes)
    if home_line is None:
        return []
    ratio = cfg["odds"]["outlier_ratio"]
    value_cfg = _value_cfg(cfg)
    out: list[Signal] = []

    home_agg = odds.aggregate(
        analysis.match_input.quotes,
        MarketType.AH,
        "home",
        line=home_line,
        ratio=ratio,
    )
    if home_agg["odds"] is not None:
        ah_ev = handicap.ev_handicap(analysis.handicap_dist, home_line, home_agg["odds"])
        validation = _ah_validation_shadow(
            analysis,
            "home",
            home_line,
            analysis.handicap_dist,
            home_agg["dispersion_ratio"],
            cfg,
        )
        out.append(
            value.grade_signal(
                MarketType.AH,
                f"home_{_line_label(home_line)}",
                0.0,
                None,
                home_agg["odds"],
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "home",
                    home_line,
                    home_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                    odds_dispersion_ratio=home_agg["dispersion_ratio"],
                    ah_market_validated=False,
                    ah_validation_shadow=validation,
                ),
                value_cfg,
                ah_ev=ah_ev,
                line=home_line,
            )
        )

    away_line = -home_line
    away_agg = odds.aggregate(
        analysis.match_input.quotes,
        MarketType.AH,
        "away",
        line=away_line,
        ratio=ratio,
    )
    if away_agg["odds"] is not None:
        away_dist = _invert_dist(analysis.handicap_dist)
        away_ev = handicap.ev_handicap(away_dist, away_line, away_agg["odds"])
        validation = _ah_validation_shadow(
            analysis,
            "away",
            away_line,
            away_dist,
            away_agg["dispersion_ratio"],
            cfg,
        )
        out.append(
            value.grade_signal(
                MarketType.AH,
                f"away_{_line_label(away_line)}",
                0.0,
                None,
                away_agg["odds"],
                _signal_ctx(
                    analysis.match_input,
                    MarketType.AH,
                    "away",
                    away_line,
                    away_agg["n_books"],
                    observed_at,
                    depends_on_backup,
                    odds_dispersion_ratio=away_agg["dispersion_ratio"],
                    ah_market_validated=False,
                    ah_validation_shadow=validation,
                ),
                value_cfg,
                ah_ev=away_ev,
                line=away_line,
            )
        )
    return out


def generate_value_signals(
    analysis: MatchAnalysis,
    cfg: dict,
    observed_at: datetime | str | None = None,
    stale_sources: list[str] | None = None,
) -> list[Signal]:
    observed = _normalize_observed_at(observed_at)
    depends_on_backup = bool(stale_sources)
    signals = [
        *_integer_market_signals(
            analysis,
            cfg,
            MarketType.X12,
            ["home", "draw", "away"],
            analysis.combined_1x2,
            analysis.market_1x2,
            observed_at=observed,
            depends_on_backup=depends_on_backup,
        ),
        *_integer_market_signals(
            analysis,
            cfg,
            MarketType.OU,
            ["over", "under"],
            analysis.ou_2_5,
            analysis.market_ou_2_5,
            line=analysis.ou_line,
            observed_at=observed,
            depends_on_backup=depends_on_backup,
            total_mu_source=analysis.total_mu_source,
            same_market_total_anchor=analysis.same_market_total_anchor,
        ),
        *_ah_signals(analysis, cfg, observed_at=observed, depends_on_backup=depends_on_backup),
    ]
    return _apply_confidence_guards(analysis, cfg, signals)
