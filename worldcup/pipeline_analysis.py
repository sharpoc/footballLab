from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, TYPE_CHECKING

from worldcup.engine import elo, ensemble, handicap, odds, poisson
from worldcup.models import MarketType, OddsQuote
from worldcup.pipeline_signals import _aggregate_ah_main, _main_ou_line, _round_metric

if TYPE_CHECKING:
    from worldcup.pipeline import MatchAnalysis, MatchAnalysisInput


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
    from worldcup.pipeline import MatchAnalysis

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
