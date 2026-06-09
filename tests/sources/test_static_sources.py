from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.eloratings import fetch_elo_files
from worldcup.sources.openfootball import fetch_openfootball_2026


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_fetch_openfootball_2026_writes_json_cache():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse(b'{"name":"World Cup 2026","matches":[]}')

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "openfootball_2026.json"
        result = fetch_openfootball_2026(transport=fake_transport, cache_path=cache_path)

        assert "worldcup.json/master/2026/worldcup.json" in seen["url"]
        assert result.status == 200
        assert result.text == '{"name":"World Cup 2026","matches":[]}'
        assert cache_path.read_text() == '{"name":"World Cup 2026","matches":[]}'


def test_fetch_elo_files_writes_world_and_team_tsv_cache():
    def fake_transport(url):
        if url.endswith("World.tsv"):
            return FakeResponse(b"1\t1\tES\t2155\n")
        if url.endswith("en.teams.tsv"):
            return FakeResponse(b"ES\tSpain\n")
        raise AssertionError(url)

    with TemporaryDirectory() as tmp:
        result = fetch_elo_files(cache_dir=tmp, transport=fake_transport)

        assert result.world.status == 200
        assert result.teams.status == 200
        assert (Path(tmp) / "elo_world.tsv").read_text() == "1\t1\tES\t2155\n"
        assert (Path(tmp) / "elo_teams.tsv").read_text() == "ES\tSpain\n"
