import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.fifa_lineups import (
    fetch_fifa_calendar_matches,
    fetch_fifa_live_match,
)


class FakeResponse:
    status = 200
    headers = {"cache-control": "s-maxage=15"}

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_fetch_fifa_calendar_matches_uses_public_api_and_writes_cache():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse({"Results": [{"IdMatch": "400021440"}]})

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "calendar.json"
        result = fetch_fifa_calendar_matches(
            from_date="2026-06-18",
            to_date="2026-06-19",
            transport=fake_transport,
            cache_path=cache_path,
        )

        assert "api.fifa.com/api/v3/calendar/matches" in seen["url"]
        assert "idCompetition=17" in seen["url"]
        assert "idSeason=285023" in seen["url"]
        assert "from=2026-06-18" in seen["url"]
        assert "to=2026-06-19" in seen["url"]
        assert result.json_body == {"Results": [{"IdMatch": "400021440"}]}
        assert json.loads(cache_path.read_text()) == {"Results": [{"IdMatch": "400021440"}]}


def test_fetch_fifa_live_match_uses_match_identity_and_writes_cache():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse({"IdMatch": "400021504", "HomeTeam": {"Players": []}})

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "live.json"
        result = fetch_fifa_live_match(
            id_competition="17",
            id_season="285023",
            id_stage="289273",
            id_match="400021504",
            transport=fake_transport,
            cache_path=cache_path,
        )

        assert seen["url"].endswith("/live/football/17/285023/289273/400021504?language=en")
        assert result.status == 200
        assert result.json_body["IdMatch"] == "400021504"
        assert json.loads(cache_path.read_text())["IdMatch"] == "400021504"
