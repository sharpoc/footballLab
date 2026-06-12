import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.theoddsapi_scores import fetch_worldcup_scores


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "73",
        "x-requests-remaining": "427",
        "x-requests-last": "2",
    }

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_fetch_scores_writes_cache_and_slot_quota():
    body = json.dumps(
        [
            {
                "id": "e1",
                "commence_time": "2026-06-11T19:00:00Z",
                "completed": True,
                "home_team": "Mexico",
                "away_team": "South Africa",
                "scores": [
                    {"name": "Mexico", "score": "2"},
                    {"name": "South Africa", "score": "0"},
                ],
            }
        ]
    ).encode()
    captured = {}

    def transport(url):
        captured["url"] = url
        return FakeResponse(body)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = fetch_worldcup_scores(
            api_key="fake-key",
            transport=transport,
            cache_path=root / "theoddsapi_scores.json",
            quota_path=root / "quota.json",
            observed_at="2026-06-12T08:00:00+00:00",
            quota_provider="theoddsapi_primary",
        )

        assert "scores/?daysFrom=2" in captured["url"]
        assert "fake-key" in captured["url"]
        assert result.status == 200
        assert json.loads((root / "theoddsapi_scores.json").read_text())[0]["completed"] is True
        quota = json.loads((root / "quota.json").read_text())["providers"]
        assert quota["theoddsapi_primary"]["remaining"] == 427
        assert quota["theoddsapi"]["remaining"] == 427
