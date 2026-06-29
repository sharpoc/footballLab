import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.theoddsapi import SourceFetchError, fetch_worldcup_odds
from worldcup.theoddsapi_keys import LEGACY_PROVIDER, SECONDARY_PROVIDER


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


def test_fetch_worldcup_odds_writes_slot_quota_and_legacy_alias():
    def fake_transport(_url):
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
            quota_provider=SECONDARY_PROVIDER,
        )

        quota = json.loads(quota_path.read_text())
        assert result.quota_entry == quota["providers"][SECONDARY_PROVIDER]
        assert quota["providers"][SECONDARY_PROVIDER]["remaining"] == 497
        assert quota["providers"][LEGACY_PROVIDER]["remaining"] == 497


def test_build_odds_url_accepts_custom_sport_key_without_logging_secret():
    from worldcup.sources.theoddsapi import build_odds_url

    url = build_odds_url(
        sport_key="soccer_china_superleague",
        api_key="fake-key",
        regions="eu",
        markets=("h2h", "spreads", "totals"),
    )

    assert "sports/soccer_china_superleague/odds" in url
    assert "markets=h2h%2Cspreads%2Ctotals" in url
    assert "oddsFormat=decimal" in url
    assert "dateFormat=iso" in url
    assert "apiKey=fake-key" in url


def test_fetch_odds_for_sport_writes_csl_cache_and_slot_quota():
    from worldcup.sources.theoddsapi import fetch_odds_for_sport

    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_csl_2026_odds.json"
        quota_path = Path(tmp) / "quota.json"

        result = fetch_odds_for_sport(
            api_key="fake-key",
            sport_key="soccer_china_superleague",
            transport=fake_transport,
            cache_path=cache_path,
            quota_path=quota_path,
            observed_at="2026-06-23T12:00:00+00:00",
            quota_provider=SECONDARY_PROVIDER,
        )

        assert "soccer_china_superleague/odds" in seen["url"]
        assert "apiKey=fake-key" in seen["url"]
        assert result.status == 200
        assert result.json_body == [{"id": "event-1"}]
        assert json.loads(cache_path.read_text()) == [{"id": "event-1"}]
        quota = json.loads(quota_path.read_text())
        assert quota["providers"][SECONDARY_PROVIDER]["remaining"] == 497
        assert quota["providers"][LEGACY_PROVIDER]["remaining"] == 497


def test_fetch_worldcup_odds_retries_transient_transport_error_then_succeeds():
    calls = []

    def flaky_transport(url):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("handshake timed out for fake-key")
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_wc_odds.json"

        result = fetch_worldcup_odds(
            api_key="fake-key",
            transport=flaky_transport,
            cache_path=cache_path,
            max_attempts=2,
        )

        assert len(calls) == 2
        assert result.json_body == [{"id": "event-1"}]
        assert json.loads(cache_path.read_text()) == [{"id": "event-1"}]


def test_fetch_worldcup_odds_does_not_retry_credential_error_or_write_cache():
    calls = []

    class UnauthorizedResponse:
        status = 401
        headers = {}

        def read(self):
            return b'{"message":"bad api key"}'

    def transport(url):
        calls.append(url)
        return UnauthorizedResponse()

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_wc_odds.json"
        quota_path = Path(tmp) / "quota.json"

        try:
            fetch_worldcup_odds(
                api_key="secret-key",
                transport=transport,
                cache_path=cache_path,
                quota_path=quota_path,
                max_attempts=3,
            )
        except SourceFetchError as exc:
            assert exc.reason == "credential_error"
            assert exc.status == 401
            assert exc.retryable is False
            assert exc.attempts == 1
            assert "apiKey=<redacted>" in exc.sanitized_url
            assert "secret-key" not in str(exc)
        else:
            raise AssertionError("expected SourceFetchError")

        assert len(calls) == 1
        assert not cache_path.exists()
        assert not quota_path.exists()


def test_fetch_worldcup_odds_invalid_json_raises_safe_error_without_cache_or_quota():
    class BadJsonResponse:
        status = 200
        headers = {"x-requests-remaining": "99"}

        def read(self):
            return b"not-json"

    with TemporaryDirectory() as tmp:
        cache_path = Path(tmp) / "theoddsapi_wc_odds.json"
        quota_path = Path(tmp) / "quota.json"

        try:
            fetch_worldcup_odds(
                api_key="secret-key",
                transport=lambda _url: BadJsonResponse(),
                cache_path=cache_path,
                quota_path=quota_path,
            )
        except SourceFetchError as exc:
            assert exc.reason == "invalid_json"
            assert exc.status == 200
            assert exc.retryable is False
            assert exc.attempts == 1
            assert "secret-key" not in str(exc)
        else:
            raise AssertionError("expected SourceFetchError")

        assert not cache_path.exists()
        assert not quota_path.exists()
