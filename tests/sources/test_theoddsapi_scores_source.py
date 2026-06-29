import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.theoddsapi_scores import fetch_worldcup_scores
from worldcup.sources.theoddsapi import SourceFetchError


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


def test_fetch_scores_reuses_safe_source_error_contract_without_writing_cache():
    class RateLimitResponse:
        status = 429
        headers = {"x-requests-remaining": "0"}

        def read(self):
            return b'{"message":"quota exceeded"}'

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            fetch_worldcup_scores(
                api_key="secret-score-key",
                transport=lambda _url: RateLimitResponse(),
                cache_path=root / "theoddsapi_scores.json",
                quota_path=root / "quota.json",
                quota_provider="theoddsapi_primary",
                max_attempts=3,
            )
        except SourceFetchError as exc:
            assert exc.reason == "quota_error"
            assert exc.status == 429
            assert exc.retryable is False
            assert exc.attempts == 1
            assert "secret-score-key" not in str(exc)
        else:
            raise AssertionError("expected SourceFetchError")

        assert not (root / "theoddsapi_scores.json").exists()
        assert not (root / "quota.json").exists()
