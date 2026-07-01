from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from worldcup.engine import handicap, odds, poisson
from worldcup.models import Grade, MarketType, Signal

if TYPE_CHECKING:
    from worldcup.pipeline import MatchAnalysis


_HARD_VETO_REASONS = {
    "stale_odds",
    "few_books",
    "market_dispersion",
    "longshot_uncertainty",
    "unconfirmed_backup",
    "line_changed_unknown",
    "model_disagreement",
    "extreme_favorite_handicap",
}

_CANDIDATE_TIERS = {"S-candidate", "A-candidate", "B-candidate"}


@dataclass
class _Option:
    market_type: MarketType
    market: str
    selection: str
    line: float | None
    odds: float
    p_profit_model: float
    p_push_model: float
    p_loss_model: float
    p_profit_market: float | None
    p_no_loss_market: float | None
    n_books: int
    dispersion_ratio: float | None = None
    signal: Signal | None = None
    hard_vetoes: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    p_hit: float | None = None
    p_hit_safe: float | None = None
    p_no_loss_safe: float | None = None
    edge_safe: float | None = None
    ev_safe: float | None = None
    value_score: float = 0.0
    lean_score: float = 0.0


def _decision_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    user_cfg = cfg.get("match_decision") or {}
    quality_cfg = cfg.get("quality") or {}
    return {
        "min_books": int(user_cfg.get("min_books", cfg.get("odds", {}).get("min_books", 3))),
        "outlier_ratio": float(user_cfg.get("outlier_ratio", cfg.get("odds", {}).get("outlier_ratio", 2.0))),
        "dispersion_ratio_max": float(
            user_cfg.get(
                "dispersion_ratio_max",
                quality_cfg.get("odds_dispersion_ratio_max", 1.18),
            )
        ),
        "base_uncertainty": float(user_cfg.get("base_uncertainty", 0.02)),
        "medium_dispersion_min": float(user_cfg.get("medium_dispersion_min", 1.10)),
        "medium_dispersion_penalty": float(user_cfg.get("medium_dispersion_penalty", 0.02)),
        "marginal_books_penalty": float(user_cfg.get("marginal_books_penalty", 0.015)),
        "model_disagreement_mild_delta": float(user_cfg.get("model_disagreement_mild_delta", 0.08)),
        "model_disagreement_severe_delta": float(user_cfg.get("model_disagreement_severe_delta", 0.15)),
        "model_disagreement_mild_penalty": float(user_cfg.get("model_disagreement_mild_penalty", 0.015)),
        "model_disagreement_severe_penalty": float(user_cfg.get("model_disagreement_severe_penalty", 0.03)),
        "worldcup_market_weight": float(user_cfg.get("worldcup_market_weight", 0.55)),
        "worldcup_model_weight": float(user_cfg.get("worldcup_model_weight", 0.45)),
        "default_market_weight": float(user_cfg.get("default_market_weight", 0.55)),
        "default_model_weight": float(user_cfg.get("default_model_weight", 0.45)),
        "high_conf_min_p_hit_safe": float(user_cfg.get("high_conf_min_p_hit_safe", 0.58)),
        "high_conf_min_p_no_loss_safe": float(user_cfg.get("high_conf_min_p_no_loss_safe", 0.62)),
        "high_conf_min_odds": float(user_cfg.get("high_conf_min_odds", 1.30)),
        "high_conf_max_odds": float(user_cfg.get("high_conf_max_odds", 2.20)),
        "low_conf_min_odds": float(user_cfg.get("low_conf_min_odds", 1.25)),
        "low_conf_max_odds": float(user_cfg.get("low_conf_max_odds", 2.80)),
        "strong_value_max_odds": float(user_cfg.get("strong_value_max_odds", 4.00)),
    }


def _round_metric(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _line_key(line: float | None) -> str:
    if line is None:
        return ""
    value = 0.0 if abs(line) < 1e-12 else line
    return f"{value:g}"


def _option_id(option: _Option) -> str:
    return f"{option.market_type.value}|{option.selection}|{_line_key(option.line)}"


def _signal_selection(signal: Signal) -> str:
    selection = signal.selection
    if selection.startswith("home_"):
        return "home"
    if selection.startswith("away_"):
        return "away"
    return selection


def _signal_line(signal: Signal) -> float | None:
    if signal.line is not None:
        return signal.line
    if "_" not in signal.selection:
        return None
    try:
        return float(signal.selection.split("_", 1)[1])
    except ValueError:
        return None


def _signal_id(signal: Signal) -> str:
    return f"{signal.market_type.value}|{_signal_selection(signal)}|{_line_key(_signal_line(signal))}"


def _signal_index(signals: list[Signal]) -> dict[str, Signal]:
    return {_signal_id(signal): signal for signal in signals}


def _invert_dist(dist: dict[int, float]) -> dict[int, float]:
    return {-diff: prob for diff, prob in dist.items()}


def _settlement_unit(score_margin: float, line: float) -> float:
    x4 = round(line * 4)
    if abs(line * 4 - x4) > 1e-9:
        raise ValueError("line must be a quarter increment")
    if x4 % 4 in (0, 2):
        adjusted = score_margin + line
        if adjusted > 1e-9:
            return 1.0
        if adjusted < -1e-9:
            return -1.0
        return 0.0
    return 0.5 * _settlement_unit(score_margin, line - 0.25) + 0.5 * _settlement_unit(
        score_margin,
        line + 0.25,
    )


def _outcome_probs(dist: dict[int, float], line: float) -> tuple[float, float, float]:
    profit = push = loss = 0.0
    for margin, prob in dist.items():
        unit = _settlement_unit(float(margin), line)
        if unit > 0:
            profit += prob
        elif unit < 0:
            loss += prob
        else:
            push += prob
    return profit, push, loss


def _total_distribution(matrix: list[list[float]]) -> dict[int, float]:
    dist: dict[int, float] = {}
    for home_goals, row in enumerate(matrix):
        for away_goals, prob in enumerate(row):
            total = home_goals + away_goals
            dist[total] = dist.get(total, 0.0) + prob
    return dist


def _settlement_ev(dist: dict[int, float], line: float, decimal_odds: float) -> float:
    ev = 0.0
    for margin, prob in dist.items():
        unit = _settlement_unit(float(margin), line)
        if unit > 0:
            ev += prob * unit * (decimal_odds - 1.0)
        elif unit < 0:
            ev += prob * unit
    return ev


def _ou_option_probs(total_dist: dict[int, float], selection: str, line: float) -> tuple[float, float, float]:
    if selection == "under":
        return _outcome_probs({-total: prob for total, prob in total_dist.items()}, line)
    return _outcome_probs(total_dist, -line)


def _ou_settlement_ev(total_dist: dict[int, float], selection: str, line: float, decimal_odds: float) -> float:
    if selection == "under":
        return _settlement_ev({-total: prob for total, prob in total_dist.items()}, line, decimal_odds)
    return _settlement_ev(total_dist, -line, decimal_odds)


def _paired_devig(odds_a: float | None, odds_b: float | None, key: str) -> float | None:
    if odds_a is None or odds_b is None:
        return None
    if odds_a <= 1.0 or odds_b <= 1.0:
        return None
    return odds.devig({key: odds_a, "other": odds_b})[key]


def _build_1x2_options(analysis: MatchAnalysis) -> list[_Option]:
    market = analysis.market_1x2
    out: list[_Option] = []
    for selection in ("home", "draw", "away"):
        decimal_odds = (market.get("odds") or {}).get(selection)
        p_model = analysis.combined_1x2.get(selection)
        if decimal_odds is None or p_model is None:
            continue
        out.append(
            _Option(
                market_type=MarketType.X12,
                market="1X2",
                selection=selection,
                line=None,
                odds=float(decimal_odds),
                p_profit_model=float(p_model),
                p_push_model=0.0,
                p_loss_model=max(0.0, 1.0 - float(p_model)),
                p_profit_market=(market.get("market_probs") or {}).get(selection),
                p_no_loss_market=(market.get("market_probs") or {}).get(selection),
                n_books=int((market.get("n_books_by_selection") or {}).get(selection, 0)),
                dispersion_ratio=(market.get("dispersion_by_selection") or {}).get(selection),
            )
        )
    return out


def _available_ou_lines(analysis: MatchAnalysis) -> list[float]:
    lines = {
        float(quote.line)
        for quote in analysis.match_input.quotes
        if quote.market_type == MarketType.OU and quote.line is not None
    }
    return sorted(lines)


def _build_ou_options(analysis: MatchAnalysis, cfg: dict[str, Any], decision_cfg: dict[str, Any]) -> list[_Option]:
    matrix, _tail = poisson.score_matrix(analysis.lambdas[0], analysis.lambdas[1], cfg["poisson"])
    total_dist = _total_distribution(matrix)
    out: list[_Option] = []
    for line in _available_ou_lines(analysis):
        market = odds.aggregate_market(
            analysis.match_input.quotes,
            MarketType.OU,
            line,
            ["over", "under"],
            ratio=decision_cfg["outlier_ratio"],
        )
        for selection in ("over", "under"):
            decimal_odds = (market.get("odds") or {}).get(selection)
            if decimal_odds is None:
                continue
            p_profit, p_push, p_loss = _ou_option_probs(total_dist, selection, line)
            out.append(
                _Option(
                    market_type=MarketType.OU,
                    market="OU",
                    selection=selection,
                    line=line,
                    odds=float(decimal_odds),
                    p_profit_model=p_profit,
                    p_push_model=p_push,
                    p_loss_model=p_loss,
                    p_profit_market=(market.get("market_probs") or {}).get(selection),
                    p_no_loss_market=(market.get("market_probs") or {}).get(selection),
                    n_books=int((market.get("n_books_by_selection") or {}).get(selection, 0)),
                    dispersion_ratio=(market.get("dispersion_by_selection") or {}).get(selection),
                )
            )
    return out


def _available_ah_options(analysis: MatchAnalysis) -> list[tuple[str, float]]:
    options = {
        (quote.selection, float(quote.line))
        for quote in analysis.match_input.quotes
        if quote.market_type == MarketType.AH
        and quote.selection in {"home", "away"}
        and quote.line is not None
    }
    return sorted(options, key=lambda item: (item[0], item[1]))


def _build_ah_options(analysis: MatchAnalysis, decision_cfg: dict[str, Any]) -> list[_Option]:
    out: list[_Option] = []
    for selection, line in _available_ah_options(analysis):
        target = odds.aggregate(
            analysis.match_input.quotes,
            MarketType.AH,
            selection,
            line=line,
            ratio=decision_cfg["outlier_ratio"],
        )
        decimal_odds = target.get("odds")
        if decimal_odds is None:
            continue
        opposite_selection = "away" if selection == "home" else "home"
        opposite = odds.aggregate(
            analysis.match_input.quotes,
            MarketType.AH,
            opposite_selection,
            line=-line,
            ratio=decision_cfg["outlier_ratio"],
        )
        market_profit = _paired_devig(float(decimal_odds), opposite.get("odds"), selection)
        side_dist = analysis.handicap_dist if selection == "home" else _invert_dist(analysis.handicap_dist)
        p_profit, p_push, p_loss = _outcome_probs(side_dist, line)
        market = "DNB" if abs(line) < 1e-12 else "AH"
        out.append(
            _Option(
                market_type=MarketType.AH,
                market=market,
                selection=selection,
                line=0.0 if abs(line) < 1e-12 else line,
                odds=float(decimal_odds),
                p_profit_model=p_profit,
                p_push_model=p_push,
                p_loss_model=p_loss,
                p_profit_market=market_profit,
                p_no_loss_market=min(1.0, market_profit + p_push) if market_profit is not None else None,
                n_books=int(target.get("n_books") or 0),
                dispersion_ratio=target.get("dispersion_ratio"),
            )
        )
    return out


def _market_model_weights(analysis: MatchAnalysis, decision_cfg: dict[str, Any]) -> tuple[float, float]:
    sport_key = analysis.match_input.odds_event.sport_key
    if sport_key == "soccer_fifa_world_cup":
        return decision_cfg["worldcup_model_weight"], decision_cfg["worldcup_market_weight"]
    return decision_cfg["default_model_weight"], decision_cfg["default_market_weight"]


def _uncertainty_penalty(option: _Option, decision_cfg: dict[str, Any]) -> float:
    penalty = decision_cfg["base_uncertainty"]
    ratio = option.dispersion_ratio
    if ratio is not None and ratio >= decision_cfg["medium_dispersion_min"]:
        penalty += decision_cfg["medium_dispersion_penalty"]
    if option.n_books == decision_cfg["min_books"]:
        penalty += decision_cfg["marginal_books_penalty"]
    if option.p_profit_market is not None:
        delta = abs(option.p_profit_model - option.p_profit_market)
        if delta >= decision_cfg["model_disagreement_severe_delta"]:
            penalty += decision_cfg["model_disagreement_severe_penalty"]
        elif delta >= decision_cfg["model_disagreement_mild_delta"]:
            penalty += decision_cfg["model_disagreement_mild_penalty"]
    return penalty


def _market_quality_score(option: _Option, decision_cfg: dict[str, Any]) -> float:
    if option.dispersion_ratio is None:
        return 0.8
    span = max(1e-9, decision_cfg["dispersion_ratio_max"] - 1.0)
    return max(0.0, min(1.0, 1.0 - ((option.dispersion_ratio - 1.0) / span)))


def _model_agreement_score(option: _Option) -> float:
    if option.p_profit_market is None:
        return 0.5
    delta = abs(option.p_profit_model - option.p_profit_market)
    return max(0.0, min(1.0, 1.0 - delta / 0.20))


def _score_option(option: _Option, analysis: MatchAnalysis, decision_cfg: dict[str, Any]) -> None:
    w_model, w_market = _market_model_weights(analysis, decision_cfg)
    p_market = option.p_profit_market if option.p_profit_market is not None else option.p_profit_model
    p_no_loss_market = (
        option.p_no_loss_market
        if option.p_no_loss_market is not None
        else option.p_profit_market
        if option.p_profit_market is not None
        else option.p_profit_model + option.p_push_model
    )
    p_hit = (w_model * option.p_profit_model) + (w_market * p_market)
    p_no_loss = (w_model * (option.p_profit_model + option.p_push_model)) + (
        w_market * p_no_loss_market
    )
    penalty = _uncertainty_penalty(option, decision_cfg)
    option.p_hit = p_hit
    option.p_hit_safe = max(0.0, p_hit - penalty)
    option.p_no_loss_safe = max(0.0, min(1.0, p_no_loss - penalty))
    option.edge_safe = (
        option.p_profit_model - option.p_profit_market - penalty
        if option.p_profit_market is not None
        else None
    )
    if option.signal is not None and option.signal.ev is not None:
        raw_ev = float(option.signal.ev)
    elif option.market_type == MarketType.AH:
        side_dist = analysis.handicap_dist if option.selection == "home" else _invert_dist(analysis.handicap_dist)
        raw_ev = handicap.ev_handicap(side_dist, option.line or 0.0, option.odds)
    elif option.market_type == MarketType.OU and option.line is not None:
        raw_ev = option.p_profit_model * (option.odds - 1.0) - option.p_loss_model
    else:
        raw_ev = option.p_profit_model * option.odds - 1.0
    option.ev_safe = raw_ev - penalty
    market_quality = _market_quality_score(option, decision_cfg)
    agreement = _model_agreement_score(option)
    full_loss_risk = option.p_loss_model
    risk_penalty = 0.05 * len(option.risk_flags)
    option.value_score = (
        100.0 * (option.edge_safe or 0.0)
        + 50.0 * (option.ev_safe or 0.0)
        + 10.0 * market_quality
        + 10.0 * agreement
        - risk_penalty
    )
    option.lean_score = (
        70.0 * (option.p_hit_safe or 0.0)
        + 20.0 * (option.p_no_loss_safe or 0.0)
        + 10.0 * market_quality
        + 5.0 * agreement
        - 20.0 * full_loss_risk
        - risk_penalty
        + _tie_break_score(option)
    )


def _tie_break_score(option: _Option) -> float:
    if option.market == "DNB":
        return 0.8
    if option.market == "AH" and option.line is not None and option.line > 0:
        return 0.7
    if option.market == "OU":
        return 0.4
    if option.market == "1X2" and option.p_profit_market is not None and option.p_profit_market >= 0.5:
        return 0.3
    if option.market == "AH":
        return 0.2
    return 0.0


def _apply_hard_vetoes(option: _Option, decision_cfg: dict[str, Any]) -> None:
    if option.odds <= 1.0:
        option.hard_vetoes.append("invalid_odds")
    if option.n_books < decision_cfg["min_books"]:
        option.hard_vetoes.append("bookmaker_count_too_low")
    if option.dispersion_ratio is not None and option.dispersion_ratio > decision_cfg["dispersion_ratio_max"]:
        option.hard_vetoes.append("severe_dispersion")
    if option.signal is not None:
        for reason in option.signal.reasons:
            if reason in _HARD_VETO_REASONS:
                option.hard_vetoes.append(reason)


def _attach_signals(options: list[_Option], signals: list[Signal]) -> None:
    indexed = _signal_index(signals)
    for option in options:
        option.signal = indexed.get(_option_id(option))


def _all_options(analysis: MatchAnalysis, cfg: dict[str, Any], decision_cfg: dict[str, Any]) -> list[_Option]:
    return [
        *_build_1x2_options(analysis),
        *_build_ou_options(analysis, cfg, decision_cfg),
        *_build_ah_options(analysis, decision_cfg),
    ]


def _best_value(options: list[_Option]) -> _Option:
    return max(options, key=lambda option: (option.value_score, option.lean_score, _tie_break_score(option)))


def _best_lean(options: list[_Option]) -> _Option:
    return max(options, key=lambda option: (option.lean_score, option.p_no_loss_safe or 0.0))


def _base_decision(label: str, option: _Option | None = None) -> dict[str, Any]:
    if option is None:
        return {
            "schema_version": 1,
            "label": label,
            "selected_signal_id": None,
            "market": None,
            "selection": None,
            "line": None,
            "odds": None,
            "p_hit": None,
            "p_hit_safe": None,
            "p_no_loss_safe": None,
            "edge_safe": None,
            "ev_safe": None,
            "signal_source": "diagnostic",
            "reasons": [],
            "risks": [],
        }
    return {
        "schema_version": 1,
        "label": label,
        "selected_signal_id": _option_id(option) if option.signal is not None else None,
        "market": option.market,
        "selection": option.selection,
        "line": option.line,
        "odds": _round_metric(option.odds),
        "p_hit": _round_metric(option.p_hit),
        "p_hit_safe": _round_metric(option.p_hit_safe),
        "p_no_loss_safe": _round_metric(option.p_no_loss_safe),
        "edge_safe": _round_metric(option.edge_safe),
        "ev_safe": _round_metric(option.ev_safe),
        "signal_source": "diagnostic",
        "reasons": list(option.reasons),
        "risks": list(option.risk_flags),
        "value_score": _round_metric(option.value_score),
        "lean_score": _round_metric(option.lean_score),
    }


def _decorate_reason(option: _Option, reason: str) -> None:
    if reason not in option.reasons:
        option.reasons.append(reason)


def _decorate_risk(option: _Option, risk: str) -> None:
    if risk not in option.risk_flags:
        option.risk_flags.append(risk)


def _make_decision(label: str, option: _Option, source: str) -> dict[str, Any]:
    _decorate_reason(option, "market_quality_pass")
    if source == "official":
        _decorate_reason(option, "official_value_signal")
    elif source == "candidate":
        _decorate_reason(option, "candidate_value_signal")
    else:
        _decorate_reason(option, "highest_safe_probability")
        if option.signal is None or option.signal.grade not in (Grade.S, Grade.A):
            _decorate_risk(option, "no_official_edge")
    decision = _base_decision(label, option)
    decision["signal_source"] = source
    return decision


def _parse_observed_at(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decide_match(
    analysis: MatchAnalysis,
    signals: list[Signal],
    cfg: dict[str, Any],
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    _parse_observed_at(observed_at)
    decision_cfg = _decision_cfg(cfg)
    options = _all_options(analysis, cfg, decision_cfg)
    _attach_signals(options, signals)

    clean_options: list[_Option] = []
    rejected_count = 0
    for option in options:
        _apply_hard_vetoes(option, decision_cfg)
        if option.hard_vetoes:
            rejected_count += 1
            continue
        _score_option(option, analysis, decision_cfg)
        clean_options.append(option)

    if not clean_options:
        decision = _base_decision("NO_CLEAN_MARKET")
        decision["reasons"] = ["no_clean_option"]
        decision["rejected_count"] = rejected_count
        return decision

    official = [
        option
        for option in clean_options
        if option.signal is not None
        and option.signal.grade in (Grade.S, Grade.A)
        and option.odds <= decision_cfg["strong_value_max_odds"]
    ]
    if official:
        return _make_decision("STRONG_VALUE", _best_value(official), "official")

    candidates = [
        option
        for option in clean_options
        if option.signal is not None and option.signal.candidate_grade in _CANDIDATE_TIERS
    ]
    if candidates:
        return _make_decision("VALUE_CANDIDATE", _best_value(candidates), "candidate")

    lean_pool = [
        option
        for option in clean_options
        if decision_cfg["low_conf_min_odds"] <= option.odds <= decision_cfg["low_conf_max_odds"]
    ]
    if not lean_pool:
        best = _best_lean(clean_options)
        _decorate_risk(best, "outside_preferred_odds_range")
        return _make_decision("LOW_CONFIDENCE_LEAN", best, "lean")

    best = _best_lean(lean_pool)
    high_odds_ok = decision_cfg["high_conf_min_odds"] <= best.odds <= decision_cfg["high_conf_max_odds"]
    high_prob_ok = (
        (best.p_hit_safe or 0.0) >= decision_cfg["high_conf_min_p_hit_safe"]
        and (best.p_no_loss_safe or 0.0) >= decision_cfg["high_conf_min_p_no_loss_safe"]
    )
    if high_odds_ok and high_prob_ok:
        return _make_decision("HIGH_CONFIDENCE_LEAN", best, "lean")
    _decorate_risk(best, "below_high_confidence_threshold")
    return _make_decision("LOW_CONFIDENCE_LEAN", best, "lean")
