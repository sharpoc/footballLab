import json
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.scheduled_refresh as scheduled_refresh
from worldcup.daily_odds_store import DailyOddsSnapshotWriter
from worldcup.daily_odds_refresh import plan_daily_odds_refresh, refresh_daily_odds


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
                "commence_time": "2026-08-01T09:00:00Z",
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
        "raw_bookmaker_payload": {"bookmakers": [{"key": "secret-book"}]},
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
        assert "raw_bookmaker_payload" not in json.dumps(stored)
        assert "bookmakers" not in json.dumps(stored)
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
        events_by_sport={"soccer_china_superleague": [_sidecar_event("event-1", "2026-08-01T09:00:00Z")]},
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
        or {"bookmakers": [{"key": "raw"}]},
        snapshot_writer=lambda payload: writes.append(payload),
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    assert result["status"] == "refreshed"
    assert calls == [("soccer_china_superleague", ("h2h",))]
    assert len(writes) == 1
    assert "bookmakers" not in json.dumps(writes[0])
    assert writes[0]["requests"][0]["fixtures"][0]["event_id"] == "event-1"


def _sidecar_event(event_id, commence_time, home=None, away=None):
    return {
        "id": event_id,
        "commence_time": commence_time,
        "home_team": home or f"Home {event_id}",
        "away_team": away or f"Away {event_id}",
    }


def _sidecar_odds(event, *, probability=0.70, valid_until="2026-08-05T18:00:00+00:00", selection="home"):
    return {
        "id": event["id"],
        "commence_time": event["commence_time"],
        "home_team": event["home_team"],
        "away_team": event["away_team"],
        "last_update": "2026-08-04T12:00:00+00:00",
        "valid_until": valid_until,
        "selection": selection,
        "h2h": {"probabilities": {"home": probability, "draw": 0.10, "away": 0.90 - probability}},
        "model_probability": {"home": probability, "draw": 0.10, "away": 0.90 - probability},
    }


def _run_live_sidecar(*, now, events, odds_by_id, writer, snapshot_path):
    return scheduled_refresh.run_daily_odds_refresh(
        enabled=True,
        live=True,
        now=now,
        snapshot_path=snapshot_path,
        state_path=snapshot_path.parent / "daily_odds_state.json",
        sports_fetcher=lambda: [{"key": "soccer_china_superleague", "active": True}],
        events_fetcher=lambda sport_key: events,
        odds_fetcher=lambda sport_key, markets: [odds_by_id[event_id] for event_id in odds_by_id],
        snapshot_writer=writer,
        quota_remaining_by_key={"soccer_china_superleague": 20},
    )


def test_daily_planner_discovers_next_morning_event_inside_beijing_cycle():
    event = _sidecar_event("next-morning", "2026-08-04T18:00:00+00:00")
    result = plan_daily_odds_refresh(
        now="2026-08-04T12:00:00+00:00",
        sports=[{"key": "soccer_china_superleague", "active": True}],
        events_by_sport={"soccer_china_superleague": [event]},
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )
    assert result.to_dict()["requests"][0]["event_ids"] == ["next-morning"]


def test_daily_planner_excludes_event_outside_beijing_cycle():
    event = _sidecar_event("next-cycle", "2026-08-05T11:00:00+00:00")
    result = plan_daily_odds_refresh(
        now="2026-08-04T12:00:00+00:00",
        sports=[{"key": "soccer_china_superleague", "active": True}],
        events_by_sport={"soccer_china_superleague": [event]},
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )
    assert result.to_dict()["requests"] == []
    assert result.to_dict()["skipped"]["soccer_china_superleague"]["no_future_events"] == 1


def test_zero_due_wave_rebuilds_top4_and_parlays_from_same_cycle_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(snapshot_path)
        events = [
            _sidecar_event("carry-1", "2026-08-04T18:00:00+00:00", "A", "B"),
            _sidecar_event("carry-2", "2026-08-04T18:00:00+00:00", "C", "D"),
        ]
        odds = {event["id"]: _sidecar_odds(event) for event in events}
        first = _run_live_sidecar(
            now="2026-08-04T12:00:00+00:00",
            events=events,
            odds_by_id=odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert first["refresh"]["top4"]
        assert first["refresh"]["combinations"]["parlay_2"]

        second = _run_live_sidecar(
            now="2026-08-04T12:15:00+00:00",
            events=events,
            odds_by_id=odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert [item["event_id"] for item in second["refresh"]["events"]] == ["carry-1", "carry-2"]
        assert [item["match_id"] for item in second["refresh"]["top4"]] == ["carry-1", "carry-2"]
        assert second["refresh"]["combinations"]["parlay_2"]


def test_snapshot_candidates_remove_started_expired_and_cycle_changed_events():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(snapshot_path)
        event = _sidecar_event("old-cycle", "2026-08-04T18:00:00+00:00")
        odds = {event["id"]: _sidecar_odds(event, valid_until="2026-08-05T09:30:00+00:00")}
        _run_live_sidecar(
            now="2026-08-04T12:00:00+00:00",
            events=[event],
            odds_by_id=odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        result = _run_live_sidecar(
            now="2026-08-05T11:15:00+00:00",
            events=[],
            odds_by_id={},
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert result["refresh"]["events"] == []
        assert result["refresh"]["top4"] == []
        assert result["refresh"]["combinations"]["parlay_2"] == []


def test_postponed_event_does_not_revive_from_previous_cycle_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(snapshot_path)
        event = _sidecar_event("postponed", "2026-08-04T18:00:00+00:00")
        odds = {event["id"]: _sidecar_odds(event)}
        _run_live_sidecar(
            now="2026-08-04T12:00:00+00:00",
            events=[event],
            odds_by_id=odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        postponed = {**event, "fixture_status": "POSTPONED"}
        result = _run_live_sidecar(
            now="2026-08-04T12:15:00+00:00",
            events=[postponed],
            odds_by_id={},
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert result["refresh"]["events"] == []
        assert result["refresh"]["top4"] == []


def test_failed_new_wave_does_not_revive_previous_event():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(snapshot_path)
        event = _sidecar_event("failed", "2026-08-04T18:00:00+00:00")
        _run_live_sidecar(
            now="2026-08-04T12:00:00+00:00",
            events=[event],
            odds_by_id={"failed": _sidecar_odds(event)},
            writer=writer,
            snapshot_path=snapshot_path,
        )
        result = _run_live_sidecar(
            now="2026-08-04T16:30:00+00:00",
            events=[event],
            odds_by_id={},
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert result["refresh"]["events"] == []
        assert result["refresh"]["top4"] == []


def test_new_wave_replaces_same_event_without_duplicate():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "daily_odds" / "daily_odds_snapshot.json"
        writer = DailyOddsSnapshotWriter(snapshot_path)
        event = _sidecar_event("updated", "2026-08-04T18:00:00+00:00", "A", "B")
        old_odds = {"updated": _sidecar_odds(event, probability=0.60)}
        _run_live_sidecar(
            now="2026-08-04T12:00:00+00:00",
            events=[event],
            odds_by_id=old_odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        new_odds = {"updated": _sidecar_odds(event, probability=0.80)}
        result = _run_live_sidecar(
            now="2026-08-04T16:30:00+00:00",
            events=[event],
            odds_by_id=new_odds,
            writer=writer,
            snapshot_path=snapshot_path,
        )
        assert len(result["refresh"]["events"]) == 1
        assert result["refresh"]["events"][0]["event_id"] == "updated"
        assert result["refresh"]["top4"][0]["prediction_probability"] == 0.80
