import json
from pathlib import Path

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.openfootball import parse_openfootball_fixtures
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.pipeline import build_match_inputs


PROBE_DIR = Path(__file__).resolve().parents[2] / "data" / "probe"


def test_saved_probe_samples_parse_when_present():
    required = [
        PROBE_DIR / "openfootball_2026.json",
        PROBE_DIR / "theoddsapi_wc_odds.json",
        PROBE_DIR / "elo_world.tsv",
        PROBE_DIR / "elo_teams.tsv",
    ]
    if not all(path.exists() for path in required):
        return

    openfootball_raw = json.loads((PROBE_DIR / "openfootball_2026.json").read_text())
    fixtures = parse_openfootball_fixtures(openfootball_raw)
    assert len(fixtures) == 104
    assert fixtures[0].kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"

    odds_raw = json.loads((PROBE_DIR / "theoddsapi_wc_odds.json").read_text())
    events = parse_theoddsapi_events(odds_raw)
    assert len(events) == 72
    assert all(event.quotes for event in events)

    ratings = parse_elo_ratings((PROBE_DIR / "elo_world.tsv").read_text())
    aliases = parse_elo_team_aliases((PROBE_DIR / "elo_teams.tsv").read_text())
    assert ratings["ES"].rating > 0
    assert aliases["USA"] == "US"

    result = build_match_inputs(fixtures, events, ratings, aliases)
    assert len(result.inputs) == 72
    assert result.missing_elo == []
