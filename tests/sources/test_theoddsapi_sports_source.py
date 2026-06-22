import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from worldcup.competitions import get_competition
from worldcup.sources.theoddsapi_sports import (
    build_sports_url,
    find_sports_for_competition,
    parse_sports_catalog,
)


def _sports_sample():
    return [
        {
            "key": "soccer_fifa_world_cup",
            "group": "Soccer",
            "title": "FIFA World Cup",
            "description": "FIFA World Cup 2026",
            "active": True,
            "has_outrights": False,
            "apiKey": "should-not-survive",
        },
        {
            "key": "soccer_epl",
            "group": "Soccer",
            "title": "EPL",
            "description": "English Premier League",
            "active": True,
            "has_outrights": False,
        },
        {
            "key": "soccer_china_superleague",
            "group": "Soccer",
            "title": "Chinese Super League",
            "description": "China Super League",
            "active": True,
            "has_outrights": False,
        },
    ]


def test_build_sports_url_does_not_log_or_embed_extra_params():
    url = build_sports_url("secret-key", all_sports=True)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert url.startswith("https://api.the-odds-api.com/v4/sports/")
    assert query == {"apiKey": ["secret-key"], "all": ["true"]}


def test_parse_sports_catalog_keeps_safe_summary_fields_only():
    summaries = parse_sports_catalog(_sports_sample())

    csl = next(item for item in summaries if item["key"] == "soccer_china_superleague")
    assert csl == {
        "key": "soccer_china_superleague",
        "group": "Soccer",
        "title": "Chinese Super League",
        "description": "China Super League",
        "active": True,
        "has_outrights": False,
    }
    assert all(set(item) == {"key", "group", "title", "description", "active", "has_outrights"} for item in summaries)
    assert all("apiKey" not in repr(item) for item in summaries)


def test_find_sports_for_competition_matches_candidates_and_search_terms():
    summaries = parse_sports_catalog(_sports_sample())

    matches = find_sports_for_competition(summaries, get_competition("csl_2026"))

    assert matches["competition_id"] == "csl_2026"
    assert matches["status"] == "sport_key_found"
    assert matches["matches"][0]["key"] == "soccer_china_superleague"


def test_saved_sports_sample_can_be_written_without_secrets():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "theoddsapi_sports.json"
        path.write_text(json.dumps(_sports_sample()), encoding="utf-8")

        summaries = parse_sports_catalog(json.loads(path.read_text(encoding="utf-8")))

        assert all("apiKey" not in repr(item) for item in summaries)
