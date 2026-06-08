from __future__ import annotations


def expected_score(dr: float) -> float:
    return 1.0 / (10 ** (-dr / 400) + 1)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def win_draw_loss(elo_home: float, elo_away: float, neutral: bool, cfg: dict) -> dict:
    dr = elo_home - elo_away
    if not neutral:
        dr += cfg["home_adv"]
    we = expected_score(dr)
    p_draw = _clamp(
        cfg["base_draw"] - cfg["draw_k"] * abs(dr),
        cfg["draw_min"],
        cfg["draw_max"],
    )
    p_home = (1 - p_draw) * we
    p_away = (1 - p_draw) * (1 - we)
    return {"home": p_home, "draw": p_draw, "away": p_away}
