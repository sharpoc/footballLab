from datetime import timezone

from worldcup.collectors.openfootball import parse_openfootball_fixtures


def test_parse_openfootball_fixture_converts_offset_to_utc():
    raw = {
        "name": "World Cup 2026",
        "matches": [
            {
                "round": "Matchday 1",
                "date": "2026-06-11",
                "time": "13:00 UTC-6",
                "team1": "Mexico",
                "team2": "South Africa",
                "group": "Group A",
                "ground": "Mexico City",
            }
        ],
    }
    fixtures = parse_openfootball_fixtures(raw)
    fixture = fixtures[0]
    assert fixture.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert fixture.kickoff_at_utc.tzinfo is timezone.utc
    assert fixture.home_team_name == "Mexico"
    assert fixture.away_team_name == "South Africa"
    assert fixture.home_canonical == "mexico"
    assert fixture.away_canonical == "south_africa"


def test_parse_openfootball_fixture_marks_placeholders():
    raw = {
        "matches": [
            {
                "round": "Final",
                "date": "2026-07-19",
                "time": "15:00 UTC-4",
                "team1": "W101",
                "team2": "W102",
                "ground": "New York/New Jersey (East Rutherford)",
            }
        ]
    }
    fixture = parse_openfootball_fixtures(raw)[0]
    assert fixture.has_placeholder_team is True
    assert fixture.home_canonical is None
    assert fixture.away_canonical is None
