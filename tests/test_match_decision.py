from dataclasses import replace
from datetime import datetime, timedelta, timezone

from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.config import load_config
from worldcup.match_decision import (
    NO_PICK_LABEL,
    PICK_LABEL,
    _Option,
    _SettlementProbabilities,
    _best_available_option,
    _decision_cfg,
    _main_home_ah_line,
    _option_rank,
    _settlement_probabilities,
    decide_match,
    decide_match_pick,
)
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
    fetched_at: datetime | None = None,
    duplicate_quotes: bool = False,
):
    kickoff = datetime(2026, 6, 12, 19, 0, tzinfo=timezone.utc)
    h2h_odds = h2h_odds or {"home": 1.85, "draw": 3.6, "away": 4.8}
    quotes: list[OddsQuote] = []
    for book in books:
        book_quotes = [
            OddsQuote(book, MarketType.X12, "home", h2h_odds["home"], fetched_at=fetched_at),
            OddsQuote(book, MarketType.X12, "draw", h2h_odds["draw"], fetched_at=fetched_at),
            OddsQuote(book, MarketType.X12, "away", h2h_odds["away"], fetched_at=fetched_at),
        ]
        if ah_home_line is not None and ah_odds is not None:
            book_quotes.extend(
                [
                    OddsQuote(
                        book,
                        MarketType.AH,
                        "home",
                        ah_odds["home"],
                        line=ah_home_line,
                        fetched_at=fetched_at,
                    ),
                    OddsQuote(
                        book,
                        MarketType.AH,
                        "away",
                        ah_odds["away"],
                        line=-ah_home_line,
                        fetched_at=fetched_at,
                    ),
                ]
            )
        for extra_home_line, extra_ah_odds in extra_ah_markets:
            book_quotes.extend(
                [
                    OddsQuote(
                        book,
                        MarketType.AH,
                        "home",
                        extra_ah_odds["home"],
                        line=extra_home_line,
                        fetched_at=fetched_at,
                    ),
                    OddsQuote(
                        book,
                        MarketType.AH,
                        "away",
                        extra_ah_odds["away"],
                        line=-extra_home_line,
                        fetched_at=fetched_at,
                    ),
                ]
            )
        if ou_line is not None and ou_odds is not None:
            book_quotes.extend(
                [
                    OddsQuote(
                        book,
                        MarketType.OU,
                        "over",
                        ou_odds["over"],
                        line=ou_line,
                        fetched_at=fetched_at,
                    ),
                    OddsQuote(
                        book,
                        MarketType.OU,
                        "under",
                        ou_odds["under"],
                        line=ou_line,
                        fetched_at=fetched_at,
                    ),
                ]
            )
        quotes.extend(book_quotes)
        if duplicate_quotes:
            quotes.extend(book_quotes)
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


def _high_probability_analysis(**kwargs):
    return _priced_analysis(
        home_elo=2050,
        away_elo=1600,
        h2h_odds={"home": 1.34, "draw": 5.8, "away": 9.5},
        **kwargs,
    )


def _reanalyze_with_quotes(analysis, quotes: list[OddsQuote]):
    odds_event = replace(analysis.match_input.odds_event, quotes=quotes)
    match_input = replace(
        analysis.match_input,
        odds_event=odds_event,
        quotes=quotes,
    )
    return analyze_match_input(match_input, load_config())


def test_decision_v2_ignores_all_legacy_signal_grades():
    analysis = _high_probability_analysis()
    cfg = load_config()
    baseline = decide_match(analysis, [], cfg)

    for grade in (Grade.S, Grade.A, Grade.B, Grade.C):
        signal = Signal(MarketType.X12, "away", grade, 9.0, 9.0, "OK")
        assert decide_match(analysis, [signal], cfg) == baseline

    assert baseline["schema_version"] == 2
    assert baseline["label"] == PICK_LABEL
    assert "selected_signal_id" not in baseline
    assert "signal_source" not in baseline


def test_new_and_legacy_entry_points_are_equivalent():
    analysis = _high_probability_analysis()
    cfg = load_config()

    assert decide_match_pick(analysis, cfg) == decide_match(analysis, [], cfg)
    assert decide_match(analysis, cfg) == decide_match(analysis, [], cfg)


def test_pick_is_evidence_ranked_and_uses_only_one_main_line():
    analysis = _high_probability_analysis(
        ah_home_line=0.0,
        ah_odds={"home": 1.38, "away": 3.1},
        extra_ah_markets=((-1.5, {"home": 2.55, "away": 1.48}),),
    )

    decision = decide_match_pick(analysis, load_config())

    assert decision["label"] == PICK_LABEL
    assert decision["market"] == "1X2"
    assert decision["selection"] == "home"
    assert decision["selected_option_id"] == "1X2_90min|home|"
    assert decision["p_hit_safe"] >= 0.58
    assert decision["p_no_loss_safe"] >= 0.62
    assert decision["reasons"] == [
        "main_market_only",
        "market_data_available",
        "evidence_ranked_near_top_probability",
    ]


def test_below_high_confidence_guardrail_still_returns_best_available_pick():
    analysis = _priced_analysis(
        home_elo=1750,
        away_elo=1750,
        h2h_odds={"home": 2.8, "draw": 3.2, "away": 2.8},
    )

    decision = decide_match_pick(analysis, load_config())

    assert decision["label"] == PICK_LABEL
    assert decision["market"] is not None
    assert decision["selection"] is not None
    assert "best_available_main_market" in decision["reasons"]
    assert "below_high_confidence_observation" in decision["risks"]


def test_incomplete_or_duplicate_books_are_penalized_without_dropping_the_match():
    one_book = _high_probability_analysis(books=("book1",), duplicate_quotes=True)

    decision = decide_match_pick(one_book, load_config())

    assert decision["label"] == PICK_LABEL
    assert "thin_market" in decision["risks"]
    assert decision["uncertainty_penalty"] > 0.02


def test_observed_at_excludes_stale_and_missing_quote_timestamps():
    observed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    stale = _high_probability_analysis(fetched_at=observed_at - timedelta(hours=4))
    missing_timestamp = _high_probability_analysis()

    stale_decision = decide_match_pick(
        stale,
        load_config(),
        observed_at=observed_at,
    )
    missing_decision = decide_match_pick(
        missing_timestamp,
        load_config(),
        observed_at=observed_at,
    )

    assert stale_decision["label"] == NO_PICK_LABEL
    assert stale_decision["reasons"] == ["no_clean_option"]
    assert missing_decision["label"] == NO_PICK_LABEL
    assert missing_decision["reasons"] == ["no_clean_option"]


def test_observed_at_rejects_future_quotes_to_prevent_lookahead():
    observed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    future = _high_probability_analysis(
        fetched_at=observed_at + timedelta(days=1),
    )

    decision = decide_match_pick(
        future,
        load_config(),
        observed_at=observed_at,
    )

    assert decision["label"] == NO_PICK_LABEL
    assert decision["reasons"] == ["no_clean_option"]


def test_stale_ou_quotes_do_not_influence_a_fresh_x12_pick():
    observed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    seed = _high_probability_analysis()
    fresh_x12: list[OddsQuote] = []
    stale_ou: list[OddsQuote] = []
    for book in ("book1", "book2", "book3"):
        fresh_x12.extend(
            [
                OddsQuote(book, MarketType.X12, "home", 1.34, fetched_at=observed_at),
                OddsQuote(book, MarketType.X12, "draw", 5.8, fetched_at=observed_at),
                OddsQuote(book, MarketType.X12, "away", 9.5, fetched_at=observed_at),
            ]
        )
        stale_ou.extend(
            [
                OddsQuote(
                    book,
                    MarketType.OU,
                    "over",
                    1.2,
                    line=2.5,
                    fetched_at=observed_at - timedelta(hours=10),
                ),
                OddsQuote(
                    book,
                    MarketType.OU,
                    "under",
                    5.0,
                    line=2.5,
                    fetched_at=observed_at - timedelta(hours=10),
                ),
            ]
        )

    fresh_only = _reanalyze_with_quotes(seed, fresh_x12)
    with_stale_ou = _reanalyze_with_quotes(seed, fresh_x12 + stale_ou)
    expected = decide_match_pick(
        fresh_only,
        load_config(),
        observed_at=observed_at,
    )
    actual = decide_match_pick(
        with_stale_ou,
        load_config(),
        observed_at=observed_at,
    )

    assert expected["label"] == actual["label"] == PICK_LABEL
    assert expected["selected_option_id"] == actual["selected_option_id"]
    assert expected["p_hit"] == actual["p_hit"]
    assert expected["p_hit_safe"] == actual["p_hit_safe"]


def test_outlier_filter_penalizes_thin_complete_market_without_dropping_match():
    observed_at = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
    seed = _high_probability_analysis()
    quotes: list[OddsQuote] = []
    for book, draw_odds in zip(
        ("book1", "book2", "book3"),
        (2.0, 10.0, 100.0),
    ):
        quotes.extend(
            [
                OddsQuote(book, MarketType.X12, "home", 1.34, fetched_at=observed_at),
                OddsQuote(
                    book,
                    MarketType.X12,
                    "draw",
                    draw_odds,
                    fetched_at=observed_at,
                ),
                OddsQuote(book, MarketType.X12, "away", 9.5, fetched_at=observed_at),
            ]
        )
    analysis = _reanalyze_with_quotes(seed, quotes)

    decision = decide_match_pick(
        analysis,
        load_config(),
        observed_at=observed_at,
    )

    assert decision["label"] == PICK_LABEL
    assert decision["market"] is not None
    assert decision["selection"] is not None


def test_decision_weights_are_normalized_and_probabilities_stay_bounded():
    analysis = _high_probability_analysis()
    cfg = load_config()
    base = dict(cfg.get("match_decision") or {})
    common = {
        **base,
        "min_p_hit_safe": 0.0,
        "min_p_no_loss_safe": 0.0,
    }
    overweighted_cfg = {
        **cfg,
        "match_decision": {
            **common,
            "worldcup_model_weight": 1.0,
            "worldcup_market_weight": 1.0,
        },
    }
    normalized_cfg = {
        **cfg,
        "match_decision": {
            **common,
            "worldcup_model_weight": 0.5,
            "worldcup_market_weight": 0.5,
        },
    }

    overweighted = decide_match_pick(analysis, overweighted_cfg)
    normalized = decide_match_pick(analysis, normalized_cfg)

    assert overweighted["label"] == normalized["label"] == PICK_LABEL
    assert overweighted["selected_option_id"] == normalized["selected_option_id"]
    assert overweighted["p_hit"] == normalized["p_hit"]
    assert overweighted["p_hit_safe"] == normalized["p_hit_safe"]
    assert 0.0 <= overweighted["p_hit_safe"] <= overweighted["p_hit"] <= 1.0
    assert overweighted["p_hit_safe"] <= overweighted["p_no_loss_safe"] <= 1.0


def test_ah_main_line_tie_prefers_the_balanced_market_line():
    quotes: list[OddsQuote] = []
    for book, home_odds, away_odds in (
        ("shallow1", 1.72, 2.14),
        ("shallow2", 1.66, 2.10),
        ("shallow3", 1.71, 2.23),
    ):
        quotes.extend(
            [
                OddsQuote(book, MarketType.AH, "home", home_odds, line=-0.5),
                OddsQuote(book, MarketType.AH, "away", away_odds, line=0.5),
            ]
        )
    for book, home_odds, away_odds in (
        ("balanced1", 1.98, 1.99),
        ("balanced2", 1.95, 1.95),
        ("balanced3", 1.89, 1.93),
    ):
        quotes.extend(
            [
                OddsQuote(book, MarketType.AH, "home", home_odds, line=-0.75),
                OddsQuote(book, MarketType.AH, "away", away_odds, line=0.75),
            ]
        )

    assert _main_home_ah_line(quotes) == -0.75


def test_quarter_line_is_not_promoted_above_a_market_supported_pick():
    analysis = _high_probability_analysis(
        ah_home_line=0.25,
        ah_odds={"home": 2.1, "away": 1.75},
    )

    decision = decide_match_pick(analysis, load_config())

    assert decision["label"] == PICK_LABEL
    assert decision["selected_option_id"] == "1X2_90min|home|"


def test_quarter_line_only_market_still_returns_a_penalized_pick():
    analysis = _high_probability_analysis(
        ah_home_line=-0.25,
        ah_odds={"home": 1.95, "away": 1.95},
    )
    ah_quotes = [
        quote for quote in analysis.match_input.quotes if quote.market_type == MarketType.AH
    ]
    analysis = replace(
        analysis,
        match_input=replace(analysis.match_input, quotes=ah_quotes),
    )

    decision = decide_match_pick(analysis, load_config())
    rating_fallback = decide_match_pick(
        analysis,
        load_config(),
        hard_blockers=["club_rating_pending"],
    )

    assert decision["label"] == PICK_LABEL
    assert decision["market"] == "AH"
    assert "quarter_line_market_probability_proxy" in decision["risks"]
    assert rating_fallback["label"] == PICK_LABEL
    assert "market_consensus_rating_fallback" in rating_fallback["reasons"]


def test_extreme_handicap_only_market_still_returns_a_penalized_pick():
    analysis = _high_probability_analysis(
        ah_home_line=-2.5,
        ah_odds={"home": 1.95, "away": 1.95},
    )
    ah_quotes = [
        quote for quote in analysis.match_input.quotes if quote.market_type == MarketType.AH
    ]
    analysis = replace(
        analysis,
        match_input=replace(analysis.match_input, quotes=ah_quotes),
    )

    decision = decide_match_pick(analysis, load_config())

    assert decision["label"] == PICK_LABEL
    assert decision["market"] == "AH"
    assert "extreme_handicap" in decision["risks"]


def test_equal_hit_rate_prefers_lower_expected_loss_and_higher_no_loss_probability():
    risky_half_loss = _Option(
        market_type=MarketType.AH,
        market="AH",
        selection="home",
        line=-0.25,
        odds=1.9,
        model=_SettlementProbabilities(full_win=0.70, half_loss=0.30),
        p_hit_market=None,
        p_no_loss_market=None,
        n_books=5,
        dispersion_ratio=1.0,
        p_hit_safe=0.68,
        p_no_loss_safe=0.68,
    )
    low_loss_with_push = _Option(
        market_type=MarketType.AH,
        market="DNB",
        selection="home",
        line=0.0,
        odds=1.9,
        model=_SettlementProbabilities(full_win=0.70, push=0.29, full_loss=0.01),
        p_hit_market=None,
        p_no_loss_market=None,
        n_books=5,
        dispersion_ratio=1.0,
        p_hit_safe=0.68,
        p_no_loss_safe=0.97,
    )

    ranked = sorted([risky_half_loss, low_loss_with_push], key=_option_rank)

    assert ranked[0] is low_loss_with_push


def test_near_top_hit_candidates_use_market_evidence_instead_of_probability_alone():
    fragile_top = _Option(
        market_type=MarketType.X12,
        market="1X2",
        selection="home",
        line=None,
        odds=1.8,
        model=_SettlementProbabilities(full_win=0.62, full_loss=0.38),
        p_hit_market=0.58,
        p_no_loss_market=0.58,
        n_books=2,
        dispersion_ratio=1.17,
        p_hit_safe=0.60,
        p_no_loss_safe=0.60,
        evidence_score=0.25,
    )
    supported_near_top = _Option(
        market_type=MarketType.OU,
        market="OU",
        selection="under",
        line=2.5,
        odds=1.75,
        model=_SettlementProbabilities(full_win=0.60, full_loss=0.40),
        p_hit_market=0.59,
        p_no_loss_market=0.59,
        n_books=10,
        dispersion_ratio=1.03,
        p_hit_safe=0.59,
        p_no_loss_safe=0.59,
        evidence_score=0.92,
    )

    selected = _best_available_option(
        [fragile_top, supported_near_top],
        _decision_cfg(load_config()),
    )

    assert selected is supported_near_top


def test_stale_odds_blocks_but_missing_rating_uses_market_consensus_fallback():
    analysis = _high_probability_analysis()
    cfg = load_config()

    stale = decide_match_pick(analysis, cfg, stale_sources=["theoddsapi"])
    pending = decide_match_pick(analysis, cfg, hard_blockers=["club_rating_pending"])

    assert stale["label"] == NO_PICK_LABEL
    assert stale["reasons"] == ["stale_odds:theoddsapi"]
    assert pending["label"] == PICK_LABEL
    assert "market_consensus_rating_fallback" in pending["reasons"]
    assert "club_rating_pending" in pending["risks"]


def test_settlement_probabilities_keep_half_results_distinct():
    # At home -0.25: a one-goal win is full win, a draw is half loss,
    # and a one-goal defeat is full loss.
    settlement = _settlement_probabilities({1: 0.4, 0: 0.35, -1: 0.25}, -0.25)

    assert settlement.full_win == 0.4
    assert settlement.half_win == 0.0
    assert settlement.push == 0.0
    assert settlement.half_loss == 0.35
    assert settlement.full_loss == 0.25
    assert settlement.p_hit == 0.4
    assert settlement.p_no_loss == 0.4
    assert settlement.loss_units == 0.425


def test_dnb_market_probability_is_push_aware_and_bounded():
    analysis = _high_probability_analysis(
        ah_home_line=0.0,
        ah_odds={"home": 1.38, "away": 3.1},
    )
    cfg = load_config()
    cfg = {**cfg, "match_decision": {"min_p_hit_safe": 0.0, "min_p_no_loss_safe": 0.0}}

    decision = decide_match_pick(analysis, cfg)

    assert 0.0 <= decision["p_hit_safe"] <= decision["p_no_loss_safe"] <= 1.0
    assert sum(decision["model_settlement"].values()) > 0.999
