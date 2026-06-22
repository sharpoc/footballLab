from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.models import MarketType


def _csl_odds_sample():
    return [
        {
            "id": "csl-event-1",
            "sport_key": "soccer_china_superleague",
            "commence_time": "2026-07-04T11:35:00Z",
            "home_team": "Shanghai Port",
            "away_team": "Beijing Guoan",
            "bookmakers": [
                {
                    "key": "bk1",
                    "last_update": "2026-07-04T02:00:00Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Shanghai Port", "price": 1.9},
                                {"name": "Beijing Guoan", "price": 3.8},
                                {"name": "Draw", "price": 3.4},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Shanghai Port", "price": 1.95, "point": -0.5},
                                {"name": "Beijing Guoan", "price": 1.85, "point": 0.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.91, "point": 2.5},
                                {"name": "Under", "price": 1.89, "point": 2.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]


def test_parse_league_odds_events_builds_event_only_fixtures_and_quotes():
    result = parse_league_odds_events(_csl_odds_sample(), competition_id="csl_2026")

    assert result.fixture_source == "odds_event_only"
    assert len(result.fixtures) == 1
    assert len(result.odds_events) == 1
    fixture = result.fixtures[0]
    event = result.odds_events[0]
    assert fixture.source_match_no is None
    assert fixture.home_team_name == "Shanghai Port"
    assert fixture.away_team_name == "Beijing Guoan"
    assert fixture.home_canonical == "shanghai_port"
    assert fixture.away_canonical == "beijing_guoan"
    assert fixture.stage is None
    assert fixture.group is None
    assert fixture.venue_name is None
    assert fixture.kickoff_time_raw == "2026-07-04T11:35:00+00:00"
    assert event.source_event_id == "csl-event-1"
    assert event.home_canonical == "shanghai_port"
    assert event.away_canonical == "beijing_guoan"
    assert {quote.market_type for quote in event.quotes} == {
        MarketType.X12,
        MarketType.AH,
        MarketType.OU,
    }


def test_parse_league_odds_events_records_unmatched_clubs():
    sample = _csl_odds_sample()
    sample[0]["home_team"] = "Unknown FC"

    result = parse_league_odds_events(sample, competition_id="csl_2026")

    assert result.unmatched_clubs == ["Unknown FC"]
    assert result.fixtures[0].home_canonical == "unknown_fc"
    assert result.odds_events[0].home_canonical == "unknown_fc"


def test_parse_league_odds_events_rejects_invalid_decimal_odds():
    sample = _csl_odds_sample()
    sample[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.0

    result = parse_league_odds_events(sample, competition_id="csl_2026")
    event = result.odds_events[0]

    assert not any(
        quote.market_type == MarketType.X12 and quote.selection == "home"
        for quote in event.quotes
    )
    assert len(event.invalid_odds) == 1
    invalid = event.invalid_odds[0]
    assert invalid.reason == "odds_decimal_lte_one"
    assert invalid.odds == 1.0
    assert invalid.bookmaker == "bk1"
    assert invalid.market_type == MarketType.X12
    assert invalid.selection == "home"
    assert invalid.outcome == "Shanghai Port"
