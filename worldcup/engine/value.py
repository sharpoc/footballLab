from __future__ import annotations

from worldcup.models import Grade, MarketType, Signal


def ev(p_model: float, odds: float) -> float:
    return p_model * odds - 1


def edge(p_model: float, p_market: float) -> float:
    return p_model - p_market


def _base_grade_1x2_ou(ev_val: float, edge_val: float, cfg: dict) -> Grade:
    if ev_val >= cfg["s_ev"] and edge_val >= cfg["s_edge"]:
        return Grade.S
    if ev_val >= cfg["a_ev"] and edge_val >= cfg["a_edge"]:
        return Grade.A
    if ev_val >= cfg["b_ev"] and edge_val >= cfg["b_edge"]:
        return Grade.B
    return Grade.C


def _base_grade_ah(ev_val: float, cfg: dict) -> Grade:
    if ev_val >= cfg["s_ev"]:
        return Grade.S
    if ev_val >= cfg["a_ev"]:
        return Grade.A
    if ev_val >= cfg["b_ev"]:
        return Grade.B
    return Grade.C


def _cap_at_b(grade: Grade) -> Grade:
    return Grade.B if grade in (Grade.S, Grade.A) else grade


def _should_cap_longshot(p_market: float | None, grade: Grade, cfg: dict) -> bool:
    threshold = cfg.get("longshot_market_prob_max")
    return (
        threshold is not None
        and p_market is not None
        and p_market < threshold
        and grade in (Grade.S, Grade.A)
    )


def _should_cap_dispersion(ctx: dict, cfg: dict) -> bool:
    threshold = cfg.get("odds_dispersion_ratio_max")
    ratio = ctx.get("odds_dispersion_ratio")
    return (
        threshold is not None
        and ratio is not None
        and ctx.get("n_books", 0) >= cfg.get("min_books", 0)
        and ratio > threshold
    )


def grade_signal(
    market_type: MarketType,
    selection: str,
    p_model: float,
    p_market: float | None,
    odds: float | None,
    ctx: dict,
    cfg: dict,
    ah_ev: float | None = None,
    line: float | None = None,
) -> Signal:
    status = ctx.get("status", "OK")
    if status in ("NO_MARKET_YET", "ODDS_PENDING", "D"):
        grade = {
            "NO_MARKET_YET": Grade.NO_MARKET_YET,
            "ODDS_PENDING": Grade.ODDS_PENDING,
            "D": Grade.D,
        }[status]
        return Signal(market_type, selection, grade, None, None, status, [status], line)

    reasons: list[str] = []
    if market_type == MarketType.AH:
        if ah_ev is None:
            raise ValueError("AH requires ah_ev from settlement table")
        ev_val = ah_ev
        edge_val = None
        base = _base_grade_ah(ev_val, cfg)
    else:
        if p_market is None or odds is None:
            return Signal(market_type, selection, Grade.D, None, None, "D", ["missing_market"], line)
        ev_val = ev(p_model, odds)
        edge_val = edge(p_model, p_market)
        base = _base_grade_1x2_ou(ev_val, edge_val, cfg)

    age = ctx.get("odds_age_seconds")
    if age is not None and age > cfg["odds_max_age_seconds"]:
        base = _cap_at_b(base)
        reasons.append("stale_odds")
    if ctx.get("n_books", 0) < cfg["min_books"]:
        base = _cap_at_b(base)
        reasons.append("few_books")
    if market_type == MarketType.X12 and ctx.get("model_disagreement"):
        base = _cap_at_b(base)
        reasons.append("model_disagreement")
    if _should_cap_dispersion(ctx, cfg):
        base = _cap_at_b(base)
        reasons.append("market_dispersion")
    if market_type != MarketType.AH and _should_cap_longshot(p_market, base, cfg):
        base = _cap_at_b(base)
        reasons.append("longshot_uncertainty")
    if ctx.get("depends_on_backup"):
        base = _cap_at_b(base)
        reasons.append("unconfirmed_backup")
    if ctx.get("line_changed_unknown"):
        base = _cap_at_b(base)
        reasons.append("line_changed_unknown")

    return Signal(market_type, selection, base, ev_val, edge_val, "OK", reasons, line)
