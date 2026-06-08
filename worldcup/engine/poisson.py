from __future__ import annotations

from math import exp


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lambdas(dr: float, cfg: dict) -> tuple[float, float]:
    gd = _clamp(dr / cfg["gd_div"], -cfg["gd_clamp"], cfg["gd_clamp"])
    half = cfg["mu_total"] / 2
    lh = _clamp(half + gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    la = _clamp(half - gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    return lh, la


def _pmf_series(lam: float, max_goals: int) -> list[float]:
    vals = [exp(-lam)]
    for k in range(1, max_goals + 1):
        vals.append(vals[-1] * lam / k)
    return vals


def score_matrix(lh: float, la: float, cfg: dict) -> tuple[list[list[float]], float]:
    n = cfg["max_goals"]
    ph = _pmf_series(lh, n)
    pa = _pmf_series(la, n)
    raw = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    total = sum(sum(row) for row in raw)
    tail = 1.0 - total
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
