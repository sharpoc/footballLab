import math
from dataclasses import replace
from datetime import datetime, timezone

from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.openfootball import parse_openfootball_fixtures
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.config import load_config
from worldcup.models import Grade, MarketType, OddsQuote
from worldcup.pipeline import (
    MatchAnalysisInput,
    analyze_match_input,
    build_match_inputs,
    generate_value_signals,
)


def test_build_match_inputs_matches_fixture_odds_and_elo_aliases():
    fixtures = parse_openfootball_fixtures(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-12",
                    "time": "20:00 UTC-6",
                    "team1": "Czech Republic",
                    "team2": "USA",
                    "ground": "Example Stadium",
                }
            ]
        }
    )
    events = parse_theoddsapi_events(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-13T02:00:00Z",
                "home_team": "Czech Republic",
                "away_team": "USA",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Czech Republic", "price": 2.2},
                                    {"name": "USA", "price": 3.2},
                                    {"name": "Draw", "price": 3.0},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    ratings = parse_elo_ratings("10\t10\tCZ\t1800\n11\t11\tUS\t1790\n")
    aliases = parse_elo_team_aliases("CZ\tCzechia\nUS\tUnited States\tUSA\n")

    result = build_match_inputs(fixtures, events, ratings, aliases)

    assert len(result.inputs) == 1
    item = result.inputs[0]
    assert item.fixture.home_team_name == "Czech Republic"
    assert item.odds_event.source_event_id == "event-1"
    assert item.home_elo.rating == 1800
    assert item.away_elo.rating == 1790
    assert len(item.quotes) == 3
    assert result.missing_elo == []
    assert result.missing_odds == []


def test_build_match_inputs_reports_missing_odds_for_confirmed_fixture():
    fixtures = parse_openfootball_fixtures(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    ratings = parse_elo_ratings("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    aliases = parse_elo_team_aliases("MX\tMexico\nZA\tSouth Africa\n")

    result = build_match_inputs(fixtures, [], ratings, aliases)

    assert result.inputs == []
    assert result.missing_odds == ["Mexico vs South Africa"]


def test_build_match_inputs_marks_home_host_advantage_for_world_cup_venue():
    fixtures = parse_openfootball_fixtures(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    events = parse_theoddsapi_events(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-11T19:00:00Z",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "bookmakers": [],
            }
        ]
    )
    ratings = parse_elo_ratings("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    aliases = parse_elo_team_aliases("MX\tMexico\nZA\tSouth Africa\n")

    result = build_match_inputs(fixtures, events, ratings, aliases)

    assert result.inputs[0].home_advantage_elo > 0


def test_build_match_inputs_marks_away_host_advantage_for_world_cup_venue():
    fixtures = parse_openfootball_fixtures(
        {
            "matches": [
                {
                    "round": "Matchday 3",
                    "date": "2026-06-25",
                    "time": "19:00 UTC-7",
                    "team1": "Turkey",
                    "team2": "USA",
                    "ground": "Los Angeles (Inglewood)",
                }
            ]
        }
    )
    events = parse_theoddsapi_events(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-26T02:00:00Z",
                "home_team": "Turkey",
                "away_team": "USA",
                "bookmakers": [],
            }
        ]
    )
    ratings = parse_elo_ratings("1\t1\tTR\t1800\n2\t2\tUS\t1800\n")
    aliases = parse_elo_team_aliases("TR\tTurkey\nUS\tUnited States\tUSA\n")

    result = build_match_inputs(fixtures, events, ratings, aliases)
    analysis = analyze_match_input(result.inputs[0], load_config())

    assert result.inputs[0].home_advantage_elo < 0
    assert analysis.lambdas[1] > analysis.lambdas[0]


def test_analyze_match_input_generates_model_and_market_outputs():
    match_input = _sample_match_input_with_three_markets()

    analysis = analyze_match_input(match_input, load_config())

    assert set(analysis.combined_1x2) == {"home", "draw", "away"}
    assert round(sum(analysis.combined_1x2.values()), 12) == 1.0
    assert set(analysis.ou_2_5) == {"over", "under"}
    assert round(sum(analysis.ou_2_5.values()), 12) == 1.0
    assert set(analysis.market_1x2["market_probs"]) == {"home", "draw", "away"}
    assert set(analysis.market_ou_2_5["market_probs"]) == {"over", "under"}


def test_analyze_match_input_uses_most_quoted_half_goal_ou_line():
    match_input = _ou_multi_line_match_input(
        {
            2.5: ("book1", "book2", "book3"),
            3.5: ("book4", "book5", "book6", "book7", "book8"),
        }
    )

    analysis = analyze_match_input(match_input, load_config())
    signals = generate_value_signals(analysis, load_config())
    ou_lines = {signal.line for signal in signals if signal.market_type == MarketType.OU}

    assert analysis.ou_line == 3.5
    assert analysis.market_ou_2_5["line"] == 3.5
    assert analysis.market_ou_2_5["n_books_by_selection"] == {"over": 5, "under": 5}
    assert ou_lines == {3.5}


def test_analyze_match_input_does_not_special_case_three_and_half():
    match_input = _ou_multi_line_match_input(
        {
            1.5: ("book1", "book2", "book3", "book4"),
            2.5: ("book5",),
        }
    )

    analysis = analyze_match_input(match_input, load_config())

    assert analysis.ou_line == 1.5
    assert analysis.market_ou_2_5["line"] == 1.5


def test_generate_value_signals_outputs_1x2_ou_and_ah():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)

    signals = generate_value_signals(analysis, cfg)

    assert {signal.market_type for signal in signals} == {
        MarketType.X12,
        MarketType.OU,
        MarketType.AH,
    }
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )
    assert home_1x2.ev is not None
    assert home_1x2.edge is not None
    ah_home = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "home_-0.5"
    )
    assert ah_home.ev is not None
    assert ah_home.edge is None
    assert ah_home.line == -0.5


def test_generate_value_signals_caps_1x2_when_models_disagree():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis = replace(
        analysis,
        elo_1x2={"home": 0.70, "draw": 0.18, "away": 0.12},
        poisson_1x2={"home": 0.44, "draw": 0.30, "away": 0.26},
        combined_1x2={"home": 0.62, "draw": 0.22, "away": 0.16},
    )
    analysis.market_1x2["n_books_by_selection"]["home"] = 3

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "model_disagreement" in home_1x2.reasons


def test_generate_value_signals_caps_when_market_dispersion_is_high():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis = replace(
        analysis,
        combined_1x2={"home": 0.62, "draw": 0.22, "away": 0.16},
        elo_1x2={"home": 0.62, "draw": 0.22, "away": 0.16},
        poisson_1x2={"home": 0.61, "draw": 0.23, "away": 0.16},
    )
    analysis.market_1x2["n_books_by_selection"]["home"] = 3
    analysis.market_1x2["dispersion_by_selection"] = {"home": 1.25}

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "market_dispersion" in home_1x2.reasons
    assert "model_disagreement" not in home_1x2.reasons


def test_generate_value_signals_passes_market_dispersion_to_ah():
    cfg = load_config()
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), cfg)
    analysis.match_input.quotes.extend(
        [
            OddsQuote("bk2", MarketType.AH, "home", 2.2, line=-0.5),
            OddsQuote("bk3", MarketType.AH, "home", 2.4, line=-0.5),
        ]
    )

    signals = generate_value_signals(analysis, cfg)
    ah_home = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "home_-0.5"
    )

    assert ah_home.grade == Grade.B
    assert "market_dispersion" in ah_home.reasons
    assert "model_disagreement" not in ah_home.reasons


def test_generate_value_signals_caps_1x2_when_market_and_ah_disagree():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="USA",
            away_team="Paraguay",
            home_canonical="united_states",
            away_canonical="paraguay",
            venue_name="Los Angeles (Inglewood)",
            home_elo=1726,
            away_elo=1834,
            home_advantage_elo=cfg["elo"]["home_adv"],
            h2h_odds={"home": 2.0654166666666667, "draw": 3.2449999999999997, "away": 4.027916666666667},
            ah_home_line=-0.5,
            ah_odds={"home": 2.08, "away": 1.8083333333333333},
        ),
        cfg,
    )

    signals = generate_value_signals(analysis, cfg)
    away_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "away"
    )

    assert away_1x2.grade == Grade.B
    assert "reverse_market" in away_1x2.reasons
    assert "ah_not_supporting_1x2" in away_1x2.reasons


def test_generate_value_signals_caps_1x2_without_ah_favorite_support():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Team A",
            away_team="Team B",
            home_canonical="team_a",
            away_canonical="team_b",
            venue_name="Neutral Venue",
            home_elo=2050,
            away_elo=1700,
            home_advantage_elo=0.0,
            h2h_odds={"home": 2.0, "draw": 3.5, "away": 3.8},
            ah_home_line=0.5,
            ah_odds={"home": 1.9, "away": 1.9},
        ),
        cfg,
    )

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "ah_not_supporting_1x2" in home_1x2.reasons


def test_generate_value_signals_caps_host_1x2_when_market_confirmation_is_thin():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Canada",
            away_team="Bosnia & Herzegovina",
            home_canonical="canada",
            away_canonical="bosnia_herzegovina",
            venue_name="Toronto",
            home_elo=1788,
            away_elo=1595,
            home_advantage_elo=cfg["elo"]["home_adv"],
            h2h_odds={"home": 1.8291666666666666, "draw": 3.5229166666666667, "away": 4.741666666666666},
            ah_home_line=-0.5,
            ah_odds={"home": 1.8379999999999999, "away": 2.054},
        ),
        cfg,
    )

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )

    assert home_1x2.grade == Grade.B
    assert "host_market_confirmation" in home_1x2.reasons


def test_generate_value_signals_caps_host_ah_when_handicap_confirmation_is_thin():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Canada",
            away_team="Bosnia & Herzegovina",
            home_canonical="canada",
            away_canonical="bosnia_herzegovina",
            venue_name="Toronto",
            home_elo=1788,
            away_elo=1595,
            home_advantage_elo=cfg["elo"]["home_adv"],
            h2h_odds={"home": 1.8291666666666666, "draw": 3.5229166666666667, "away": 4.741666666666666},
            ah_home_line=-0.5,
            ah_odds={"home": 1.8379999999999999, "away": 2.054},
        ),
        cfg,
    )

    signals = generate_value_signals(analysis, cfg)
    home_ah = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "home_-0.5"
    )

    assert home_ah.grade == Grade.B
    assert "host_handicap_confirmation" in home_ah.reasons


def test_generate_value_signals_keeps_host_signal_when_market_and_ah_confirm():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Mexico",
            away_team="South Africa",
            home_canonical="mexico",
            away_canonical="south_africa",
            venue_name="Mexico City",
            home_elo=1875,
            away_elo=1517,
            home_advantage_elo=cfg["elo"]["home_adv"],
            h2h_odds={"home": 1.4395833333333332, "draw": 4.429583333333333, "away": 8.668750000000001},
            ah_home_line=-1.0,
            ah_odds={"home": 1.7433333333333334, "away": 2.1266666666666665},
        ),
        cfg,
    )

    signals = generate_value_signals(analysis, cfg)
    home_1x2 = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.X12 and signal.selection == "home"
    )
    home_ah = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "home_-1"
    )

    assert home_1x2.grade == Grade.S
    assert "host_market_confirmation" not in home_1x2.reasons
    assert home_ah.grade == Grade.S
    assert "host_handicap_confirmation" not in home_ah.reasons


def test_generate_value_signals_caps_under_when_main_handicap_is_big():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Germany",
            away_team="Curacao",
            home_canonical="germany",
            away_canonical="curacao",
            venue_name="Houston",
            home_elo=1998,
            away_elo=1600,
            home_advantage_elo=0.0,
            h2h_odds={"home": 1.03, "draw": 18.0, "away": 40.0},
            ah_home_line=-3.5,
            ah_odds={"home": 1.79, "away": 2.048},
            ou_odds={"over": 2.6, "under": 1.7},
        ),
        cfg,
    )
    analysis = replace(
        analysis,
        ou_2_5={"over": 0.25, "under": 0.75},
        market_ou_2_5={
            "market_probs": {"over": 0.38, "under": 0.62},
            "odds": {"over": 2.6, "under": 1.7},
            "n_books_by_selection": {"over": 3, "under": 3},
            "dispersion_by_selection": {"over": 1.0, "under": 1.0},
        },
    )

    signals = generate_value_signals(analysis, cfg)
    under = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.OU and signal.selection == "under"
    )

    assert under.grade == Grade.B
    assert "under_vs_big_handicap" in under.reasons


def test_generate_value_signals_caps_ah_dog_against_extreme_favorite():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Germany",
            away_team="Curacao",
            home_canonical="germany",
            away_canonical="curacao",
            venue_name="Houston",
            home_elo=1998,
            away_elo=1600,
            home_advantage_elo=0.0,
            h2h_odds={"home": 1.03, "draw": 18.0, "away": 40.0},
            ah_home_line=-3.5,
            ah_odds={"home": 1.79, "away": 2.048},
        ),
        cfg,
    )
    analysis = replace(analysis, handicap_dist={3: 1.0})

    signals = generate_value_signals(analysis, cfg)
    away_ah = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "away_+3.5"
    )

    assert away_ah.grade == Grade.B
    assert "extreme_favorite_handicap" in away_ah.reasons


def test_generate_value_signals_caps_zero_handicap_strong_signal():
    cfg = load_config()
    analysis = analyze_match_input(
        _priced_match_input(
            home_team="Ivory Coast",
            away_team="Ecuador",
            home_canonical="ivory_coast",
            away_canonical="ecuador",
            venue_name="Philadelphia",
            home_elo=1700,
            away_elo=1850,
            home_advantage_elo=0.0,
            h2h_odds={"home": 3.3, "draw": 3.2, "away": 2.2},
            ah_home_line=0.0,
            ah_odds={"home": 2.29, "away": 1.66},
        ),
        cfg,
    )
    analysis = replace(analysis, handicap_dist={-1: 1.0})

    signals = generate_value_signals(analysis, cfg)
    away_ah = next(
        signal
        for signal in signals
        if signal.market_type == MarketType.AH and signal.selection == "away_-0"
    )

    assert away_ah.grade == Grade.B
    assert "ah_zero_line_confirmation" in away_ah.reasons


def _sample_match_input_with_three_markets():
    fixtures = parse_openfootball_fixtures(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    events = parse_theoddsapi_events(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-11T19:00:00Z",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "bookmakers": [
                    {
                        "key": "bk1",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Mexico", "price": 1.8},
                                    {"name": "South Africa", "price": 4.8},
                                    {"name": "Draw", "price": 3.6},
                                ],
                            },
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.9, "point": 2.5},
                                    {"name": "Under", "price": 2.0, "point": 2.5},
                                ],
                            },
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Mexico", "price": 1.9, "point": -0.5},
                                    {"name": "South Africa", "price": 1.9, "point": 0.5},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    )
    ratings = parse_elo_ratings("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    aliases = parse_elo_team_aliases("MX\tMexico\nZA\tSouth Africa\n")
    return build_match_inputs(fixtures, events, ratings, aliases).inputs[0]


def _priced_match_input(
    home_team: str,
    away_team: str,
    home_canonical: str,
    away_canonical: str,
    venue_name: str,
    home_elo: int,
    away_elo: int,
    home_advantage_elo: float,
    h2h_odds: dict[str, float],
    ah_home_line: float,
    ah_odds: dict[str, float],
    ou_odds: dict[str, float] | None = None,
) -> MatchAnalysisInput:
    kickoff = datetime(2026, 6, 13, 1, 0, tzinfo=timezone.utc)
    quotes: list[OddsQuote] = []
    for book in ("book1", "book2", "book3"):
        quotes.extend(
            [
                OddsQuote(book, MarketType.X12, "home", h2h_odds["home"]),
                OddsQuote(book, MarketType.X12, "draw", h2h_odds["draw"]),
                OddsQuote(book, MarketType.X12, "away", h2h_odds["away"]),
                OddsQuote(book, MarketType.AH, "home", ah_odds["home"], line=ah_home_line),
                OddsQuote(book, MarketType.AH, "away", ah_odds["away"], line=-ah_home_line),
            ]
        )
        if ou_odds is not None:
            quotes.extend(
                [
                    OddsQuote(book, MarketType.OU, "over", ou_odds["over"], line=2.5),
                    OddsQuote(book, MarketType.OU, "under", ou_odds["under"], line=2.5),
                ]
            )
    fixture = Fixture(
        source_match_no=1,
        kickoff_at_utc=kickoff,
        kickoff_time_raw="01:00",
        home_team_name=home_team,
        away_team_name=away_team,
        home_canonical=home_canonical,
        away_canonical=away_canonical,
        venue_name=venue_name,
    )
    event = ParsedOddsEvent(
        source_event_id="event-priced",
        sport_key="soccer_fifa_world_cup",
        kickoff_at_utc=kickoff,
        home_team_name=home_team,
        away_team_name=away_team,
        home_canonical=home_canonical,
        away_canonical=away_canonical,
        quotes=quotes,
    )
    return MatchAnalysisInput(
        fixture=fixture,
        odds_event=event,
        home_elo=EloRating("HH", 1, home_elo),
        away_elo=EloRating("AA", 2, away_elo),
        quotes=quotes,
        home_advantage_elo=home_advantage_elo,
    )


def _ou_match_input(
    over_odds: float,
    under_odds: float,
    with_ou: bool = True,
    books: tuple[str, ...] = ("book1", "book2", "book3"),
) -> MatchAnalysisInput:
    kickoff = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
    quotes = [
        OddsQuote("book1", MarketType.X12, "home", 2.5),
        OddsQuote("book1", MarketType.X12, "draw", 3.2),
        OddsQuote("book1", MarketType.X12, "away", 2.9),
    ]
    if with_ou:
        for book in books:
            quotes.append(OddsQuote(book, MarketType.OU, "over", over_odds, line=2.5))
            quotes.append(OddsQuote(book, MarketType.OU, "under", under_odds, line=2.5))
    fixture = Fixture(
        source_match_no=1,
        kickoff_at_utc=kickoff,
        kickoff_time_raw="18:00",
        home_team_name="Team A",
        away_team_name="Team B",
        home_canonical="team_a",
        away_canonical="team_b",
    )
    event = ParsedOddsEvent(
        source_event_id="event-ou",
        sport_key="soccer_fifa_world_cup",
        kickoff_at_utc=kickoff,
        home_team_name="Team A",
        away_team_name="Team B",
        home_canonical="team_a",
        away_canonical="team_b",
        quotes=quotes,
    )
    return MatchAnalysisInput(
        fixture=fixture,
        odds_event=event,
        home_elo=EloRating("AA", 1, 1800),
        away_elo=EloRating("BB", 2, 1800),
        quotes=quotes,
    )


def _ou_multi_line_match_input(line_books: dict[float, tuple[str, ...]]) -> MatchAnalysisInput:
    base = _ou_match_input(1.4, 3.0, with_ou=False)
    quotes = list(base.quotes)
    for line, books in line_books.items():
        for book in books:
            quotes.append(OddsQuote(book, MarketType.OU, "over", 1.92, line=line))
            quotes.append(OddsQuote(book, MarketType.OU, "under", 1.88, line=line))
    return replace(base, quotes=quotes, odds_event=replace(base.odds_event, quotes=quotes))


def test_ou_probability_varies_with_market_total():
    cfg = load_config()
    high_total = analyze_match_input(_ou_match_input(1.55, 2.45), cfg)
    low_total = analyze_match_input(_ou_match_input(2.45, 1.55), cfg)
    assert high_total.mu_total_used > low_total.mu_total_used
    assert high_total.ou_2_5["over"] > low_total.ou_2_5["over"]


def test_ou_falls_back_to_prior_without_market():
    cfg = load_config()
    analysis = analyze_match_input(_ou_match_input(0.0, 0.0, with_ou=False), cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"])


def test_mu_prior_uses_dr_when_no_ou_market():
    from worldcup.backtest import apply_overrides

    cfg = apply_overrides(load_config(), ["poisson.mu_dr_slope=0.002"])
    match_input = _ou_match_input(0.0, 0.0, with_ou=False)
    big_gap = MatchAnalysisInput(
        fixture=match_input.fixture,
        odds_event=match_input.odds_event,
        home_elo=EloRating("AA", 1, 2000),
        away_elo=EloRating("BB", 2, 1600),
        quotes=match_input.quotes,
    )
    analysis = analyze_match_input(big_gap, cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"] + 0.002 * 400)


def test_ou_anchor_requires_min_books():
    cfg = load_config()
    analysis = analyze_match_input(_ou_match_input(1.55, 2.45, books=("book1",)), cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"])


def test_analyze_match_input_aggregates_main_ah_market():
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), load_config())

    assert analysis.market_ah_main == {
        "line_home": -0.5,
        "odds": {"home": 1.9, "away": 1.9},
        "n_books_by_selection": {"home": 1, "away": 1},
    }


def test_analyze_match_input_without_ah_quotes_has_no_ah_market():
    match_input = _sample_match_input_with_three_markets()
    match_input = replace(
        match_input,
        quotes=[q for q in match_input.quotes if q.market_type != MarketType.AH],
    )

    analysis = analyze_match_input(match_input, load_config())

    assert analysis.market_ah_main is None
