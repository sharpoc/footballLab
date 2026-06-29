import json
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.league_odds_refresh as league_odds_refresh
from worldcup.league_odds_refresh import resolve_sport_key, run_league_odds_refresh


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "3",
        "x-requests-remaining": "497",
        "x-requests-last": "3",
    }

    def read(self):
        return b'[{"id":"csl-event-1","sport_key":"soccer_china_superleague"}]'


def test_resolve_sport_key_requires_explicit_key_for_csl_candidates():
    try:
        resolve_sport_key("csl_2026")
    except ValueError as exc:
        assert str(exc) == "sport_key_required: csl_2026"
    else:
        raise AssertionError("expected sport_key_required")

    assert resolve_sport_key("csl_2026", "soccer_china_superleague") == "soccer_china_superleague"
    assert resolve_sport_key("fifa_world_cup_2026") == "soccer_fifa_world_cup"


def test_league_odds_refresh_dry_run_does_not_read_env_or_write_cache():
    called = {"transport": False}

    def fake_transport(_url):
        called["transport"] = True
        raise AssertionError("dry-run must not call transport")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_odds_refresh(
            live=False,
            env={},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            transport=fake_transport,
            observed_at="2026-06-23T12:00:00+00:00",
        )

        assert result["status"] == "dry_run"
        assert result["competition_id"] == "csl_2026"
        assert result["sport_key"] == "soccer_china_superleague"
        assert result["target_cache_path"].endswith("theoddsapi_csl_2026_odds.json")
        assert result["cache_exists"] is False
        assert called["transport"] is False
        assert not (root / "cache" / "theoddsapi_csl_2026_odds.json").exists()


def test_league_odds_refresh_blocks_existing_cache_without_replace():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = root / "cache" / "theoddsapi_csl_2026_odds.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("[]", encoding="utf-8")

        result = run_league_odds_refresh(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "primary-key"},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            observed_at="2026-06-23T12:00:00+00:00",
        )

        assert result == {
            "status": "blocked",
            "reason": "existing_cache",
            "competition_id": "csl_2026",
            "sport_key": "soccer_china_superleague",
            "target_cache_path": str(cache_path),
            "cache_exists": True,
            "live": True,
        }


def test_league_odds_refresh_live_writes_cache_and_quota_without_returning_key():
    seen = {}

    def fake_transport(url):
        seen["url"] = url
        return FakeResponse()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_odds_refresh(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "primary-key"},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            transport=fake_transport,
            observed_at="2026-06-23T12:00:00+00:00",
        )

        cache_path = root / "cache" / "theoddsapi_csl_2026_odds.json"
        assert "soccer_china_superleague/odds" in seen["url"]
        assert "apiKey=primary-key" in seen["url"]
        assert result["status"] == "fetched"
        assert result["events"] == 1
        assert result["cache_path"] == str(cache_path)
        assert result["slot"] == "primary"
        assert result["theoddsapi_provider"] == "theoddsapi_primary"
        assert "primary-key" not in json.dumps(result)
        assert json.loads(cache_path.read_text())[0]["id"] == "csl-event-1"
        quota = json.loads((root / "cache" / "quota.json").read_text())
        assert quota["providers"]["theoddsapi_primary"]["remaining"] == 497
        assert quota["providers"]["theoddsapi"]["remaining"] == 497


def test_league_odds_refresh_live_returns_safe_error_without_key_or_cache():
    def fail_transport(_url):
        raise TimeoutError("network failed for primary-key")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_odds_refresh(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "primary-key"},
            competition_id="csl_2026",
            sport_key="soccer_china_superleague",
            cache_dir=root / "cache",
            quota_path=root / "cache" / "quota.json",
            transport=fail_transport,
            observed_at="2026-06-23T12:00:00+00:00",
        )

        assert result["status"] == "error"
        assert result["reason"] == "network_error"
        assert result["retryable"] is True
        assert result["attempts"] == 2
        assert "primary-key" not in json.dumps(result)
        assert not (root / "cache" / "theoddsapi_csl_2026_odds.json").exists()


def test_main_dry_run_does_not_load_env():
    def fail_load_env(_path):
        raise AssertionError("dry-run must not load env")

    old_load_env = league_odds_refresh._load_env
    league_odds_refresh._load_env = fail_load_env
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = league_odds_refresh.main(
                    [
                        "--competition",
                        "csl_2026",
                        "--sport-key",
                        "soccer_china_superleague",
                        "--cache-dir",
                        str(root / "cache"),
                        "--quota-path",
                        str(root / "cache" / "quota.json"),
                    ]
                )
            result = json.loads(stdout.getvalue())
            assert exit_code == 0
            assert result["status"] == "dry_run"
            assert not (root / "cache" / "theoddsapi_csl_2026_odds.json").exists()
    finally:
        league_odds_refresh._load_env = old_load_env
