from datetime import datetime, timezone

from worldcup.config import load_config
from worldcup.models import MarketType, OddsQuote


def test_load_config_reads_known_keys():
    cfg = load_config()
    assert cfg["poisson"]["max_goals"] == 10
    assert cfg["elo"]["home_adv"] == 100
    assert cfg["elo"]["draw_min"] <= 0.12
    assert cfg["odds"]["min_books"] == 3
    assert cfg["value"]["longshot_market_prob_max"] == 0.12
    assert cfg["csl_model"] == {
        "rating_activation": "shadow_only",
        "min_total_matches": 300,
        "min_team_matches": 30,
        "rating_k": 30,
        "home_adv": 100,
        "mu_total": 2.6,
        "mu_market_weight": 0.7,
        "w_elo": 0.5,
        "w_poisson": 0.5,
        "latest_season_min_matches": 100,
        "market_baseline_min_matches": 200,
    }


def test_odds_quote_rejects_naive_datetime():
    try:
        OddsQuote("bk", MarketType.X12, "home", 2.0, fetched_at=datetime(2026, 6, 8))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("expected naive datetime to be rejected")


def test_odds_quote_accepts_timezone_aware_datetime():
    q = OddsQuote(
        "bk",
        MarketType.X12,
        "home",
        2.0,
        fetched_at=datetime(2026, 6, 8, tzinfo=timezone.utc),
    )
    assert q.fetched_at.tzinfo is timezone.utc
