from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from worldcup.engine import handicap, odds, value
from worldcup.models import Grade, MarketType, OddsQuote, Signal

if TYPE_CHECKING:
    from worldcup.pipeline import MatchAnalysis, MatchAnalysisInput


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


def _x12_candidate_only_reasons(analysis: MatchAnalysis, signal: Signal, cfg: dict) -> list[str]:
    if signal.market_type != MarketType.X12 or signal.grade not in (Grade.S, Grade.A):
        return []
    if analysis.match_input.odds_event.sport_key != "soccer_fifa_world_cup":
        return []

    reasons: list[str] = []
    quality_cfg = cfg.get("quality", {})
    if signal.selection == "draw" and quality_cfg.get("x12_draw_official", True) is False:
        reasons.append("x12_draw_candidate_only")

    max_odds = quality_cfg.get("x12_official_odds_max")
    odds_value = (analysis.market_1x2.get("odds") or {}).get(signal.selection)
    if max_odds is not None and odds_value is not None and float(odds_value) > float(max_odds):
        reasons.append("x12_long_odds_candidate_only")
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
    "extreme_favorite_handicap",
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
            *_x12_candidate_only_reasons(analysis, signal, cfg),
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
