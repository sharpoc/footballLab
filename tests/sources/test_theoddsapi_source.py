import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.theoddsapi import fetch_worldcup_odds


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "3",
        "x-requests-remaining": "497",
        "x-requests-last": "3",
    }

    def read(self):
        return b'[{"id":"event-1"}]'


def test_fetch_worldcup_odds_uses_transport_and_writes_cache_and_quota():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_wc_odds.json"
        quota_path = Path(tmp) / "quota.json"

        result = fetch_worldcup_odds(
            api_key="fake-key",
            transport=fake_transport,
            cache_path=cache_path,
            quota_path=quota_path,
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert "soccer_fifa_world_cup/odds" in seen["url"]
        assert "apiKey=fake-key" in seen["url"]
        assert "markets=h2h%2Cspreads%2Ctotals" in seen["url"]
        assert result.status == 200
        assert result.json_body == [{"id": "event-1"}]
        assert json.loads(cache_path.read_text()) == [{"id": "event-1"}]
        quota = json.loads(quota_path.read_text())
        assert quota["providers"]["theoddsapi"]["remaining"] == 497
