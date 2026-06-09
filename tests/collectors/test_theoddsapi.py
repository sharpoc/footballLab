from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.models import MarketType


def test_parse_theoddsapi_maps_markets_to_odds_quotes():
    raw = [
        {
            "id": "event-1",
            "sport_key": "soccer_fifa_world_cup",
            "commence_time": "2026-06-11T19:00:00Z",
            "home_team": "Mexico",
            "away_team": "South Africa",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "last_update": "2026-06-08T14:25:34Z",
                    "markets": [
                        {
                            "key": "h2h",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Mexico", "price": 1.42},
                                {"name": "South Africa", "price": 8.69},
                                {"name": "Draw", "price": 4.59},
                            ],
                        },
                        {
                            "key": "spreads",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Mexico", "price": 2.05, "point": -1.25},
                                {"name": "South Africa", "price": 1.86, "point": 1.25},
                            ],
                        },
                        {
                            "key": "totals",
                            "last_update": "2026-06-08T14:25:34Z",
                            "outcomes": [
                                {"name": "Over", "price": 1.95, "point": 2.25},
                                {"name": "Under", "price": 1.93, "point": 2.25},
                            ],
                        },
                        {"key": "h2h_lay", "outcomes": [{"name": "Mexico", "price": 1.46}]},
                    ],
                }
            ],
        }
    ]
    event = parse_theoddsapi_events(raw)[0]
    assert event.source_event_id == "event-1"
    assert event.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert len(event.quotes) == 7
    assert {q.market_type for q in event.quotes} == {MarketType.X12, MarketType.AH, MarketType.OU}
    assert any(q.selection == "draw" and q.market_type == MarketType.X12 for q in event.quotes)
    assert any(q.selection == "home" and q.market_type == MarketType.AH and q.line == -1.25 for q in event.quotes)
    assert any(q.selection == "over" and q.market_type == MarketType.OU and q.line == 2.25 for q in event.quotes)
