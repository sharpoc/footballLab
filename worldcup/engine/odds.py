from __future__ import annotations

from datetime import timezone
from statistics import median

from worldcup.models import MarketType, OddsQuote


def implied_prob(odds: float) -> float:
    if odds <= 1.0:
        raise ValueError("decimal odds must be > 1.0")
    return 1.0 / odds


def devig(raw_odds: dict[str, float]) -> dict[str, float]:
    raw = {k: implied_prob(v) for k, v in raw_odds.items()}
    total = sum(raw.values())
    if total <= 0:
        raise ValueError("raw odds must produce positive implied probability")
    return {k: v / total for k, v in raw.items()}


def filter_outliers(odds_list: list[float], ratio: float) -> list[float]:
    if not odds_list:
        return []
    if ratio <= 1.0:
        raise ValueError("ratio must be > 1.0")
    m = median(odds_list)
    return [o for o in odds_list if (1.0 / ratio) <= (o / m) <= ratio]


def aggregate(
    quotes: list[OddsQuote],
    market_type: MarketType,
    selection: str,
    line: float | None = None,
    ratio: float = 2.0,
) -> dict:
    matched = [
        q.odds
        for q in quotes
        if q.market_type == market_type and q.selection == selection and q.line == line
    ]
    cleaned = filter_outliers(matched, ratio)
    n = len(cleaned)
    if not cleaned:
        return {
            "odds": None,
            "n_books": 0,
            "min_odds": None,
            "max_odds": None,
            "dispersion_ratio": None,
        }
    min_odds = min(cleaned)
    max_odds = max(cleaned)
    return {
        "odds": sum(cleaned) / n,
        "n_books": n,
        "min_odds": min_odds,
        "max_odds": max_odds,
        "dispersion_ratio": max_odds / min_odds,
    }


def aggregate_market(
    quotes: list[OddsQuote],
    market_type: MarketType,
    line: float | None,
    selections: list[str],
    ratio: float = 2.0,
) -> dict:
    odds: dict[str, float] = {}
    n_books: dict[str, int] = {}
    dispersion_by_selection: dict[str, float] = {}
    fetched_times = [
        q.fetched_at.astimezone(timezone.utc)
        for q in quotes
        if q.market_type == market_type
        and q.selection in selections
        and q.line == line
        and q.fetched_at is not None
    ]
    last_update_at = max(fetched_times).isoformat() if fetched_times else None
    for selection in selections:
        agg = aggregate(quotes, market_type, selection, line=line, ratio=ratio)
        n_books[selection] = agg["n_books"]
        if agg["odds"] is not None:
            odds[selection] = agg["odds"]
        if agg["dispersion_ratio"] is not None:
            dispersion_by_selection[selection] = agg["dispersion_ratio"]
    base = {
        "odds": odds,
        "market_probs": {},
        "n_books_by_selection": n_books,
        "dispersion_by_selection": dispersion_by_selection,
        "last_update_at": last_update_at,
    }
    if set(odds) != set(selections):
        return base
    return {**base, "market_probs": devig(odds)}
