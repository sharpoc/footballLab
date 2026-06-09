from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.openfootball import parse_openfootball_fixtures
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.config import load_config
from worldcup.models import MarketType
from worldcup.pipeline import analyze_match_input, build_match_inputs, generate_value_signals


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


def test_analyze_match_input_generates_model_and_market_outputs():
    match_input = _sample_match_input_with_three_markets()

    analysis = analyze_match_input(match_input, load_config())

    assert set(analysis.combined_1x2) == {"home", "draw", "away"}
    assert round(sum(analysis.combined_1x2.values()), 12) == 1.0
    assert set(analysis.ou_2_5) == {"over", "under"}
    assert round(sum(analysis.ou_2_5.values()), 12) == 1.0
    assert set(analysis.market_1x2["market_probs"]) == {"home", "draw", "away"}
    assert set(analysis.market_ou_2_5["market_probs"]) == {"over", "under"}


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
