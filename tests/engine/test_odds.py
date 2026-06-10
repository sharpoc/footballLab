import math
from datetime import datetime, timezone

from worldcup.engine.odds import aggregate, aggregate_market, devig, filter_outliers, implied_prob
from worldcup.models import MarketType, OddsQuote


def test_implied_prob():
    assert implied_prob(2.0) == 0.5
    assert math.isclose(implied_prob(1.8), 1 / 1.8)


def test_devig_sums_to_one():
    raw = {"home": 1.8, "draw": 3.6, "away": 4.8}
    probs = devig(raw)
    assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-9)
    assert probs["home"] > probs["draw"] > probs["away"]


def test_filter_outliers_drops_extreme():
    assert filter_outliers([1.9, 2.0, 2.1, 10.0], ratio=2.0) == [1.9, 2.0, 2.1]


def test_aggregate_same_line_average():
    quotes = [
        OddsQuote("bk1", MarketType.OU, "over", 1.9, line=2.5),
        OddsQuote("bk2", MarketType.OU, "over", 2.1, line=2.5),
        OddsQuote("bk3", MarketType.OU, "over", 2.0, line=2.25),
    ]
    out = aggregate(quotes, MarketType.OU, "over", line=2.5)
    assert out["n_books"] == 2
    assert math.isclose(out["odds"], 2.0)


def test_aggregate_market_devigs_all_selections_same_line():
    quotes = [
        OddsQuote("bk1", MarketType.OU, "over", 1.9, line=2.5, fetched_at=datetime(2026, 6, 8, 1, tzinfo=timezone.utc)),
        OddsQuote("bk2", MarketType.OU, "over", 2.1, line=2.5, fetched_at=datetime(2026, 6, 8, 2, tzinfo=timezone.utc)),
        OddsQuote("bk1", MarketType.OU, "under", 1.8, line=2.5, fetched_at=datetime(2026, 6, 8, 3, tzinfo=timezone.utc)),
        OddsQuote("bk2", MarketType.OU, "under", 2.0, line=2.5, fetched_at=datetime(2026, 6, 8, 4, tzinfo=timezone.utc)),
        OddsQuote("bk1", MarketType.OU, "over", 1.7, line=2.25),
    ]
    out = aggregate_market(quotes, MarketType.OU, line=2.5, selections=["over", "under"])
    assert out["n_books_by_selection"] == {"over": 2, "under": 2}
    assert set(out["odds"]) == {"over", "under"}
    assert math.isclose(sum(out["market_probs"].values()), 1.0, abs_tol=1e-9)
    assert out["last_update_at"] == "2026-06-08T04:00:00+00:00"
