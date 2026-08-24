import json

from worldcup.sources.league_fotmob_lineups import (
    build_fotmob_calendar_url,
    build_fotmob_details_url,
    fetch_fotmob_calendar,
    fetch_fotmob_details,
)


class FakeResponse:
    status = 200
    headers = {"set-cookie": "must-not-escape"}

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_calendar_url_and_injected_transport_return_only_decoded_payload():
    """Returning the HTTP response would leak headers and make parsing transport-dependent."""
    seen = []

    def transport(url):
        seen.append(url)
        return FakeResponse({"leagues": []})

    assert build_fotmob_calendar_url("20260824") == "https://www.fotmob.com/api/matches?date=20260824"
    result = fetch_fotmob_calendar(date="20260824", transport=transport)
    assert seen == ["https://www.fotmob.com/api/matches?date=20260824"]
    assert result == {"leagues": []}
    assert "headers" not in result


def test_details_url_encodes_match_id_and_uses_injected_transport():
    """Ignoring the injected transport would make offline tests perform a live provider request."""
    seen = []

    def transport(url):
        seen.append(url)
        return FakeResponse({"general": {"matchId": "10/01"}})

    assert build_fotmob_details_url("10/01") == "https://www.fotmob.com/api/matchDetails?matchId=10%2F01"
    result = fetch_fotmob_details(match_id="10/01", transport=transport)
    assert seen == ["https://www.fotmob.com/api/matchDetails?matchId=10%2F01"]
    assert result == {"general": {"matchId": "10/01"}}
