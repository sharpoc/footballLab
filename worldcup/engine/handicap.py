from __future__ import annotations


def diff_distribution(matrix: list[list[float]]) -> dict[int, float]:
    dist: dict[int, float] = {}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            diff = i - j
            dist[diff] = dist.get(diff, 0.0) + p
    return dist


def _ev_integer(dist: dict[int, float], line: float, odds: float) -> float:
    ev = 0.0
    for diff, p in dist.items():
        adj = diff + line
        if adj > 0:
            ev += p * (odds - 1)
        elif adj < 0:
            ev -= p
    return ev


def _ev_half(dist: dict[int, float], line: float, odds: float) -> float:
    ev = 0.0
    for diff, p in dist.items():
        adj = diff + line
        ev += p * (odds - 1 if adj > 0 else -1)
    return ev


def _line_kind(line: float) -> str:
    x4 = round(line * 4)
    if abs(line * 4 - x4) > 1e-9:
        raise ValueError("Asian handicap line must be a quarter-goal increment")
    if x4 % 4 == 0:
        return "integer"
    if x4 % 4 == 2:
        return "half"
    return "quarter"


def ev_handicap(dist: dict[int, float], line: float, odds: float) -> float:
    kind = _line_kind(line)
    if kind == "integer":
        return _ev_integer(dist, line, odds)
    if kind == "half":
        return _ev_half(dist, line, odds)
    lo, hi = line - 0.25, line + 0.25
    return 0.5 * ev_handicap(dist, lo, odds) + 0.5 * ev_handicap(dist, hi, odds)
