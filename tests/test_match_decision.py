from datetime import datetime, timezone

from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.config import load_config
from worldcup.match_decision import decide_match
from worldcup.models import Grade, MarketType, OddsQuote, Signal
from worldcup.pipeline import MatchAnalysisInput, analyze_match_input


def _priced_analysis(
    *,
    home_elo: int = 1900,
    away_elo: int = 1750,
    books: tuple[str, ...] = ("book1", "book2", "book3"),
    h2h_odds: dict[str, float] | None = None,
    ah_home_line: float | None = None,
    ah_odds: dict[str, float] | None = None,
    extra_ah_markets: tuple[tuple[float, dict[str, float]], ...] = (),
    ou_line: float | None = None,
    ou_odds: dict[str, float] | None = None,
):
    kickoff = datetime(2026, 6, 12, 19, 0, tzinfo=timezone.utc)
    h2h_odds = h2h_odds or {"home": 1.85, "draw": 3.6, "away": 4.8}
    quotes: list[OddsQuote] = []
    for book in books:
        quotes.extend(
            [
                OddsQuote(book, MarketType.X12, "home", h2h_odds["home"]),
                OddsQuote(book, MarketType.X12, "draw", h2h_odds["draw"]),
                OddsQuote(book, MarketType.X12, "away", h2h_odds["away"]),
            ]
        )
        if ah_home_line is not None and ah_odds is not None:
            quotes.extend(
                [
                    OddsQuote(book, MarketType.AH, "home", ah_odds["home"], line=ah_home_line),
                    OddsQuote(book, MarketType.AH, "away", ah_odds["away"], line=-ah_home_line),
                ]
            )
        for extra_home_line, extra_ah_odds in extra_ah_markets:
            quotes.extend(
                [
                    OddsQuote(book, MarketType.AH, "home", extra_ah_odds["home"], line=extra_home_line),
                    OddsQuote(book, MarketType.AH, "away", extra_ah_odds["away"], line=-extra_home_line),
                ]
            )
        if ou_line is not None and ou_odds is not None:
            quotes.extend(
                [
                    OddsQuote(book, MarketType.OU, "over", ou_odds["over"], line=ou_line),
                    OddsQuote(book, MarketType.OU, "under", ou_odds["under"], line=ou_line),
                ]
            )
    fixture = Fixture(
        source_match_no=1,
        kickoff_at_utc=kickoff,
        kickoff_time_raw="19:00",
        home_team_name="Home",
        away_team_name="Away",
        home_canonical="home",
        away_canonical="away",
    )
    event = ParsedOddsEvent(
        source_event_id="event-1",
        sport_key="soccer_fifa_world_cup",
        kickoff_at_utc=kickoff,
        home_team_name="Home",
        away_team_name="Away",
        home_canonical="home",
        away_canonical="away",
        quotes=quotes,
    )
    return analyze_match_input(
        MatchAnalysisInput(
            fixture=fixture,
            odds_event=event,
            home_elo=EloRating("HH", 1, home_elo),
            away_elo=EloRating("AA", 2, away_elo),
            quotes=quotes,
        ),
        load_config(),
    )


def test_decide_match_prefers_official_strong_value_signal():
    analysis = _priced_analysis()
    signal = Signal(
        MarketType.X12,
        "home",
        Grade.A,
        0.06,
        0.03,
        "OK",
        raw_grade=Grade.A,
    )

    decision = decide_match(analysis, [signal], load_config())

    assert decision["label"] == "STRONG_VALUE"
    assert decision["market"] == "1X2"
    assert decision["selection"] == "home"
    assert decision["signal_source"] == "official"
    assert decision["selected_signal_id"] == "1X2_90min|home|"


def test_decide_match_uses_validated_candidate_before_lean():
    analysis = _priced_analysis(
        h2h_odds={"home": 1.8, "draw": 3.7, "away": 5.0},
        ah_home_line=0.0,
        ah_odds={"home": 1.55, "away": 2.55},
    )
    candidate = Signal(
        MarketType.AH,
        "home_0",
        Grade.B,
        0.09,
        None,
        "OK",
        ["ah_market_edge_missing"],
        line=0.0,
        raw_grade=Grade.S,
        candidate_grade="S-candidate",
        candidate_reasons=["ah_validation_shadow_candidate_validated"],
    )

    decision = decide_match(analysis, [candidate], load_config())

    assert decision["label"] == "VALUE_CANDIDATE"
    assert decision["market"] == "DNB"
    assert decision["selection"] == "home"
    assert decision["line"] == 0.0
    assert decision["signal_source"] == "candidate"


def test_decide_match_prefers_high_confidence_lean_over_low_safe_candidate():
    analysis = _priced_analysis(
        home_elo=2050,
        away_elo=1600,
        h2h_odds={"home": 1.34, "draw": 5.8, "away": 9.5},
        ah_home_line=0.0,
        ah_odds={"home": 1.38, "away": 3.1},
        extra_ah_markets=(
            (-1.5, {"home": 2.55, "away": 1.48}),
        ),
    )
    low_safe_candidate = Signal(
        MarketType.AH,
        "away_1.5",
        Grade.B,
        0.08,
        None,
        "OK",
        ["ah_market_edge_missing"],
        line=1.5,
        raw_grade=Grade.S,
        candidate_grade="S-candidate",
        candidate_reasons=["ah_validation_shadow_candidate_validated"],
    )

    decision = decide_match(analysis, [low_safe_candidate], load_config())

    assert decision["label"] == "HIGH_CONFIDENCE_LEAN"
    assert decision["market"] == "DNB"
    assert decision["selection"] == "home"
    assert decision["line"] == 0.0
    assert decision["signal_source"] == "lean"
    assert decision["p_hit_safe"] >= 0.58
    assert decision["p_no_loss_safe"] >= 0.62


def test_decide_match_demotes_low_safe_candidate_to_low_confidence_lean():
    analysis = _priced_analysis(
        home_elo=2050,
        away_elo=1600,
        h2h_odds={"home": 1.18, "draw": 7.0, "away": 14.0},
        ah_home_line=-1.5,
        ah_odds={"home": 2.55, "away": 1.48},
    )
    low_safe_candidate = Signal(
        MarketType.AH,
        "away_1.5",
        Grade.B,
        0.08,
        None,
        "OK",
        ["ah_market_edge_missing"],
        line=1.5,
        raw_grade=Grade.S,
        candidate_grade="S-candidate",
        candidate_reasons=["ah_validation_shadow_candidate_validated"],
    )

    decision = decide_match(analysis, [low_safe_candidate], load_config())

    assert decision["label"] == "LOW_CONFIDENCE_LEAN"
    assert decision["signal_source"] == "lean"
    assert decision["market"] != "1X2"
    assert (
        decision["p_hit_safe"] < 0.58
        or decision["p_no_loss_safe"] < 0.62
    )


def test_decide_match_selects_high_confidence_lean_when_no_value_signal():
    analysis = _priced_analysis(
        home_elo=2050,
        away_elo=1600,
        h2h_odds={"home": 1.34, "draw": 5.8, "away": 9.5},
        ah_home_line=0.0,
        ah_odds={"home": 1.38, "away": 3.1},
    )

    decision = decide_match(analysis, [], load_config())

    assert decision["label"] == "HIGH_CONFIDENCE_LEAN"
    assert decision["market"] == "DNB"
    assert decision["selection"] == "home"
    assert decision["odds"] >= 1.3
    assert decision["p_hit_safe"] >= 0.58
    assert decision["p_no_loss_safe"] >= 0.62
    assert decision["signal_source"] == "lean"
    assert "highest_safe_probability" in decision["reasons"]


def test_decide_match_returns_no_clean_market_when_every_option_has_hard_veto():
    analysis = _priced_analysis(books=("book1",))

    decision = decide_match(analysis, [], load_config())

    assert decision["label"] == "NO_CLEAN_MARKET"
    assert decision["market"] is None
    assert decision["selection"] is None
    assert decision["reasons"] == ["no_clean_option"]
    assert decision["rejected_count"] > 0
