from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.daily_sidecar as daily_sidecar
import worldcup.http_app as http_app


def _snapshot() -> dict:
    return {
        "schema_version": 2,
        "namespace": "daily_odds",
        "generated_at": "2026-08-04T08:00:00+00:00",
        "timezone": "Asia/Shanghai",
        "cycle": {},
        "provider_catalog": [],
        "requests": [],
        "events": [],
        "top4": [],
        "parlay_2": [],
        "parlay_3": [],
        "candidate_count": 0,
        "selected_count": 0,
        "coverage": [],
        "degradation_reasons": [],
        "combination_rejection_reasons": [],
        "skipped": {},
        "excluded_rescheduled_events": [],
        "quota": {},
    }


def test_production_cli_dry_run_does_not_call_provider_or_write():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        calls = []

        def fail_provider(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("dry-run must not call provider")

        output = StringIO()
        with redirect_stdout(output):
            code = daily_sidecar.main(
                [
                    "--dry-run",
                    "--data-dir",
                    str(tmp_path / "daily_odds"),
                ],
                provider_factory=fail_provider,
            )

        result = json.loads(output.getvalue())
        assert code == 0
        assert result["status"] == "dry_run"
        assert result["provider_calls"] == 0
        assert calls == []
        assert not list(tmp_path.rglob("*"))


def test_production_cli_live_selects_key_and_uses_persistent_paths():
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        calls = []
        env_path = tmp_path / ".env"
        env_path.write_text(
            "THE_ODDS_API_KEY_PRIMARY=primary-secret\n"
            "THE_ODDS_API_KEY_SECONDARY=secondary-secret\n",
            encoding="utf-8",
        )

        def fake_provider(**kwargs):
            calls.append(kwargs)
            return {
                "sports": [{"key": "soccer_china_superleague", "active": True}],
                "events": {"soccer_china_superleague": []},
                "odds": {},
                "quota": {"theoddsapi_primary": {"remaining": 80}},
            }

        original = daily_sidecar.run_daily_odds_refresh
        daily_sidecar.run_daily_odds_refresh = lambda **kwargs: {
            "status": "refreshed",
            "refresh": {"written": True},
            "plan": {},
        }
        try:
            output = StringIO()
            with redirect_stdout(output):
                code = daily_sidecar.main(
                    [
                        "--live",
                        "--env",
                        str(env_path),
                        "--data-dir",
                        str(tmp_path / "daily_odds"),
                        "--daily-budget-credits",
                        "85",
                    ],
                    provider_factory=fake_provider,
                )
        finally:
            daily_sidecar.run_daily_odds_refresh = original

        result = json.loads(output.getvalue())
        assert code == 0
        assert result["status"] == "refreshed"
        assert result["provider"] == "theoddsapi_primary"
        assert result["data_dir"] == str(tmp_path / "daily_odds")
        assert result["daily_budget_credits"] == 85
        assert calls and calls[0]["api_key"] == "primary-secret"
        assert "primary-secret" not in output.getvalue()
        assert "secondary-secret" not in output.getvalue()


def test_http_reader_uses_explicit_daily_data_dir():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "daily_odds"
        snapshot_path = data_dir / "daily_odds_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        previous = __import__("os").environ.get("WORLDCUP_DAILY_ODDS_DATA_DIR")
        __import__("os").environ["WORLDCUP_DAILY_ODDS_DATA_DIR"] = str(data_dir)
        try:
            loaded = http_app.load_daily_sidecar_snapshot()
        finally:
            if previous is None:
                __import__("os").environ.pop("WORLDCUP_DAILY_ODDS_DATA_DIR", None)
            else:
                __import__("os").environ["WORLDCUP_DAILY_ODDS_DATA_DIR"] = previous
        assert loaded is not None
        assert loaded["namespace"] == "daily_odds"


def test_systemd_sidecar_assets_are_installed_but_timer_disabled_by_default():
    service = Path("deploy/systemd/worldcup-daily-sidecar.service").read_text(encoding="utf-8")
    timer = Path("deploy/systemd/worldcup-daily-sidecar.timer").read_text(encoding="utf-8")
    assert "worldcup.daily_sidecar" in service
    assert "/var/lib/worldcup/daily_odds" in service
    assert "--live" in service
    assert "OnCalendar=" in timer
    assert "WantedBy=timers.target" in timer
    assert "enable" not in timer.lower()


def test_legacy_daily_picks_route_remains_distinct():
    assert http_app.handle_request.__module__ == "worldcup.http_app"
    source = Path("worldcup/http_app.py").read_text(encoding="utf-8")
    assert 'route == "/api/daily-picks"' in source
    assert 'route == "/api/daily-picks-sidecar"' in source
    assert 'route == "/api/daily-picks"' in source


def test_data_dir_is_production_default_only_when_explicit():
    import os

    previous = os.environ.get("WORLDCUP_DAILY_ODDS_DATA_DIR")
    os.environ.pop("WORLDCUP_DAILY_ODDS_DATA_DIR", None)
    try:
        assert daily_sidecar.default_data_dir() == Path("data/cache/daily_odds")
        os.environ["WORLDCUP_DAILY_ODDS_DATA_DIR"] = "/var/lib/worldcup/daily_odds"
        assert daily_sidecar.default_data_dir() == Path("/var/lib/worldcup/daily_odds")
    finally:
        if previous is None:
            os.environ.pop("WORLDCUP_DAILY_ODDS_DATA_DIR", None)
        else:
            os.environ["WORLDCUP_DAILY_ODDS_DATA_DIR"] = previous
