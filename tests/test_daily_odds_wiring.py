import json
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.scheduled_refresh as scheduled_refresh
from worldcup.daily_odds_store import DailyOddsSnapshotWriter
from worldcup.daily_odds_refresh import plan_daily_odds_refresh


_CATALOG = [
    {
        "name": "中超",
        "status": "enabled",
        "reason": "test",
        "competition_id": "csl_2026",
        "sport_key": "soccer_china_superleague",
    }
]


def _events():
    return {
        "soccer_china_superleague": [
            {
                "id": "event-1",
                "commence_time": "2026-08-01T11:00:00Z",
                "home_team": "A",
                "away_team": "B",
            }
        ]
    }


def _payload():
    return {
        "schema_version": 1,
        "generated_at": "2026-08-01T06:00:00+00:00",
        "provider_catalog": _CATALOG,
        "requests": [
            {
                "sport_key": "soccer_china_superleague",
                "competition_id": "csl_2026",
                "anchor": "T-6h",
                "markets": ["h2h"],
                "event_ids": ["event-1"],
                "event_count": 1,
                "fixtures": [
                    {
                        "event_id": "event-1",
                        "sport_key": "soccer_china_superleague",
                        "competition_id": "csl_2026",
                        "commence_time": "2026-08-01T11:00:00+00:00",
                        "home_team": "A",
                        "away_team": "B",
                    }
                ],
                "internal": {
                    "odds_movement": {
                        "activation": "shadow_only",
                        "affects_selection": False,
                    }
                },
            }
        ],
        "skipped": {},
        "excluded_rescheduled_events": [],
        "opaque_provider_data": {"items": [{"key": "redacted"}]},
    }


def test_daily_snapshot_writer_is_atomic_isolated_and_does_not_store_raw_bookmakers():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(path)

        result = writer(_payload())

        assert result == path
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["namespace"] == "daily_odds"
        assert stored["requests"][0]["sport_key"] == "soccer_china_superleague"
        assert stored["requests"][0]["internal"]["odds_movement"]["activation"] == "shadow_only"
        assert "opaque_provider_data" not in json.dumps(stored)
        assert "bookmaker" not in json.dumps(stored).lower()
        assert not list(path.parent.glob("*.tmp"))
        assert not (root / "analysis_snapshot.json").exists()


def test_daily_snapshot_writer_dry_run_has_zero_file_writes():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(path, dry_run=True)

        assert writer(_payload()) is None
        assert not path.exists()
        assert not path.parent.exists()


def test_daily_snapshot_writer_rejects_identity_mismatch_and_rescheduled_event():
    with TemporaryDirectory() as tmp:
        writer = DailyOddsSnapshotWriter(Path(tmp) / "daily_odds" / "daily_odds_snapshot.json")

        mismatch = _payload()
        mismatch["requests"][0]["fixtures"][0]["competition_id"] = "wrong"
        try:
            writer(mismatch)
        except ValueError as exc:
            assert str(exc) == "daily_odds_identity_mismatch"
        else:
            raise AssertionError("expected identity mismatch")

        rescheduled = _payload()
        rescheduled["requests"][0]["fixtures"].append(
            {
                **rescheduled["requests"][0]["fixtures"][0],
                "commence_time": "2026-08-01T12:00:00+00:00",
            }
        )
        try:
            writer(rescheduled)
        except ValueError as exc:
            assert str(exc) == "daily_odds_rescheduled_event"
        else:
            raise AssertionError("expected rescheduled event rejection")


def test_daily_odds_planner_only_uses_injected_sports_and_events_and_estimates_credits():
    result = plan_daily_odds_refresh(
        now="2026-08-01T06:00:00+00:00",
        sports=[{"key": "soccer_china_superleague", "active": True}],
        events_by_sport=_events(),
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    payload = result.to_dict()
    assert payload["requests"][0]["sport_key"] == "soccer_china_superleague"
    assert payload["requests"][0]["anchor"] == "T-6h"
    assert payload["requests"][0]["markets"] == ["h2h"]
    assert payload["requests"][0]["estimated_credits"] == 1
    assert payload["requests"][0]["fixtures"][0]["event_id"] == "event-1"
    assert payload["requests"][0]["fixtures"][0]["competition_id"] == "csl_2026"


def test_scheduled_daily_odds_is_disabled_by_default_without_calling_dependencies():
    def fail(*_args, **_kwargs):
        raise AssertionError("disabled daily odds must not call dependencies")

    result = scheduled_refresh.run_daily_odds_refresh(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=fail,
        events_fetcher=fail,
        odds_fetcher=fail,
    )

    assert result == {
        "status": "disabled",
        "reason": "feature_disabled",
        "plan": None,
        "refresh": None,
    }


def test_scheduled_daily_odds_dry_run_is_explicit_and_does_not_call_odds_or_writer():
    calls = []

    def sports_fetcher():
        calls.append("sports")
        return [{"key": "soccer_china_superleague", "active": True}]

    def events_fetcher(sport_key):
        calls.append(("events", sport_key))
        return _events()[sport_key]

    def fail_odds(*_args, **_kwargs):
        raise AssertionError("dry-run must not call odds")

    def fail_writer(*_args, **_kwargs):
        raise AssertionError("dry-run must not write")

    result = scheduled_refresh.run_daily_odds_refresh(
        enabled=True,
        live=False,
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=sports_fetcher,
        events_fetcher=events_fetcher,
        odds_fetcher=fail_odds,
        snapshot_writer=fail_writer,
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    assert result["status"] == "dry_run"
    assert result["refresh"] is None
    assert result["plan"]["requests"][0]["estimated_credits"] == 1
    assert calls == ["sports", ("events", "soccer_china_superleague")]


def test_scheduled_daily_odds_explicit_live_calls_injected_odds_and_writer_only():
    calls = []
    writes = []

    result = scheduled_refresh.run_daily_odds_refresh(
        enabled=True,
        live=True,
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: [{"key": "soccer_china_superleague", "active": True}],
        events_fetcher=lambda sport_key: _events()[sport_key],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets))
        or {"opaque_provider_data": [{"key": "redacted"}]},
        snapshot_writer=lambda payload: writes.append(payload),
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    assert result["status"] == "refreshed"
    assert calls == [("soccer_china_superleague", ("h2h",))]
    assert len(writes) == 1
    assert "opaque_provider_data" not in json.dumps(writes[0])
    assert "bookmaker" not in json.dumps(writes[0]).lower()
    assert writes[0]["requests"][0]["fixtures"][0]["event_id"] == "event-1"
