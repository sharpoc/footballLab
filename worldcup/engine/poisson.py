from __future__ import annotations

from math import exp


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lambdas(dr: float, cfg: dict, mu_total: float | None = None) -> tuple[float, float]:
    total = cfg["mu_total"] if mu_total is None else mu_total
    gd = _clamp(dr / cfg["gd_div"], -cfg["gd_clamp"], cfg["gd_clamp"])
    half = total / 2
    lh = _clamp(half + gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    la = _clamp(half - gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    return lh, la


def prob_total_over(mu: float, line: float) -> float:
    """P(total > line) when total goals ~ Poisson(mu); line must be k + 0.5."""
    k = int(line)
    pk = exp(-mu)
    cdf = pk
    for i in range(1, k + 1):
        pk = pk * mu / i
        cdf += pk
    return 1.0 - cdf


def implied_total_mu(p_over: float, line: float, lo: float = 0.1, hi: float = 8.0) -> float:
    p = _clamp(p_over, prob_total_over(lo, line), prob_total_over(hi, line))
    for _ in range(80):
        mid = (lo + hi) / 2
        if prob_total_over(mid, line) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def blended_mu(p_over_market: float | None, line: float, cfg: dict) -> float:
    """Blend market-implied total goals with the config prior.

    `mu_market_weight` 缺省为 0，行为与历史版本完全一致（恒用 mu_total 先验）。
    """
    base = cfg["mu_total"]
    weight = cfg.get("mu_market_weight", 0.0)
    if p_over_market is None or weight <= 0:
        return base
    mu_market = implied_total_mu(p_over_market, line)
    return weight * mu_market + (1.0 - weight) * base


def _pmf_series(lam: float, max_goals: int) -> list[float]:
    vals = [exp(-lam)]
    for k in range(1, max_goals + 1):
        vals.append(vals[-1] * lam / k)
    return vals


def _clamped_rho(rho: float, lh: float, la: float) -> float:
    if not rho:
        return 0.0
    lo = max(-1.0 / lh, -1.0 / la)
    hi = min(1.0 / (lh * la), 1.0)
    return _clamp(rho, lo, hi)


def score_matrix(lh: float, la: float, cfg: dict) -> tuple[list[list[float]], float]:
    n = cfg["max_goals"]
    ph = _pmf_series(lh, n)
    pa = _pmf_series(la, n)
    raw = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    tail = 1.0 - sum(sum(row) for row in raw)
    rho = _clamped_rho(cfg.get("dc_rho", 0.0), lh, la)
    if rho:
        raw[0][0] *= 1.0 - lh * la * rho
        raw[0][1] *= 1.0 + lh * rho
        raw[1][0] *= 1.0 + la * rho
        raw[1][1] *= 1.0 - rho
    total = sum(sum(row) for row in raw)
    matrix = [[raw[i][j] / total for j in range(n + 1)] for i in range(n + 1)]
    return matrix, tail


def probs_1x2(matrix: list[list[float]]) -> dict:
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return {"home": home, "draw": draw, "away": away}


def prob_over(matrix: list[list[float]], line: float) -> float:
    over = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i + j > line:
                over += p
    return over
