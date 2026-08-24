from datetime import datetime, timezone

from worldcup.league_lineup_planner import plan_league_lineup_poll


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _active_report(*competition_ids):
    fingerprints = {
        "sport_catalog": "sport",
        "odds_sample": "odds",
        "team_identity": "identity",
        "result_contract": "result",
    }
    return {
        "schema_version": 1,
        "competitions": {
            competition_id: {
                "competition_id": competition_id,
                "state": "active",
                "fingerprints": fingerprints,
            }
            for competition_id in competition_ids
        },
    }


def _fixture(event_id, kickoff, **overrides):
    fixture = {"event_id": event_id, "kickoff_at_utc": kickoff}
    fixture.update(overrides)
    return fixture


def test_planner_returns_a_safe_zero_request_plan_without_fixtures():
    """Removing the empty-fixture gate would create unnecessary provider calls."""
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={},
        acceptance_report={"schema_version": 1, "competitions": {}},
        state={"events": {}},
    )

    assert result == {
        "generated_at": "2026-08-24T12:00:00+00:00",
        "requests": [],
        "skipped": {},
        "next_due_at": None,
        "counts": {"fixture_count": 0, "eligible_count": 0, "request_count": 0, "skipped_count": 0},
    }


def test_planner_waits_until_the_90_minute_poll_window():
    """Changing the 90-minute gate would cause provider calls too early."""
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={
            "epl_2026_27": [_fixture("epl-1", "2026-08-24T13:31:00Z")],
        },
        acceptance_report=_active_report("epl_2026_27"),
        state={"events": {}},
    )

    assert result["requests"] == []
    assert result["skipped"] == {"epl_2026_27": {"outside_poll_window": 1}}
    assert result["next_due_at"] == "2026-08-24T12:01:00+00:00"
    assert result["counts"] == {"fixture_count": 1, "eligible_count": 1, "request_count": 0, "skipped_count": 1}


def test_planner_throttles_90_to_45_minute_window_for_15_minutes():
    """Reducing the early-window interval would violate the polling budget."""
    fixtures = {"epl_2026_27": [_fixture("epl-1", "2026-08-24T13:00:00Z")]}
    throttled = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition=fixtures,
        acceptance_report=_active_report("epl_2026_27"),
        state={"events": {"epl_2026_27:epl-1": {"last_polled_at": "2026-08-24T11:50:00Z"}}},
    )
    due = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition=fixtures,
        acceptance_report=_active_report("epl_2026_27"),
        state={"events": {}},
    )

    assert throttled["requests"] == []
    assert throttled["skipped"] == {"epl_2026_27": {"poll_throttled": 1}}
    assert throttled["next_due_at"] == "2026-08-24T12:05:00+00:00"
    assert due["requests"] == [{
        "competition_id": "epl_2026_27",
        "event_id": "epl-1",
        "kickoff_at_utc": "2026-08-24T13:00:00+00:00",
        "poll_interval_seconds": 900,
    }]
    assert due["next_due_at"] == "2026-08-24T12:00:00+00:00"


def test_planner_uses_a_five_minute_interval_inside_45_minutes():
    """Keeping the early cadence after T-45 would miss confirmed lineups."""
    result = plan_league_lineup_poll(
        now=datetime(2026, 8, 24, 12, 15, tzinfo=timezone.utc),
        fixtures_by_competition={"epl_2026_27": [_fixture("epl-1", "2026-08-24T13:00:00Z")]},
        acceptance_report=_active_report("epl_2026_27"),
        state={"events": {"epl_2026_27:epl-1": {"last_polled_at": "2026-08-24T12:11:00Z"}}},
    )

    assert result["requests"] == []
    assert result["skipped"] == {"epl_2026_27": {"poll_throttled": 1}}
    assert result["next_due_at"] == "2026-08-24T12:16:00+00:00"


def test_planner_excludes_confirmed_and_terminal_fixtures():
    """Dropping any terminal gate would poll a match that cannot yield a lineup."""
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={
            "epl_2026_27": [
                _fixture("confirmed", "2026-08-24T13:00:00Z"),
                _fixture("postponed", "2026-08-24T13:00:00Z", fixture_status="POSTPONED"),
                _fixture("cancelled", "2026-08-24T13:00:00Z", fixture_status="CANCELLED"),
                _fixture("started", "2026-08-24T13:00:00Z", fixture_status="STARTED"),
            ],
        },
        acceptance_report=_active_report("epl_2026_27"),
        state={"events": {"epl_2026_27:confirmed": {"confirmed": True}}},
    )

    assert result["requests"] == []
    assert result["skipped"] == {"epl_2026_27": {
        "lineup_confirmed": 1,
        "fixture_postponed": 1,
        "fixture_cancelled": 1,
        "fixture_started": 1,
    }}
    assert result["next_due_at"] is None
    assert result["counts"] == {"fixture_count": 4, "eligible_count": 0, "request_count": 0, "skipped_count": 4}


def test_planner_rejects_non_active_acceptance_and_naive_now():
    """Treating incomplete acceptance evidence as active would enable an unaccepted league."""
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={"epl_2026_27": [_fixture("epl-1", "2026-08-24T13:00:00Z")]},
        acceptance_report={"schema_version": 1, "competitions": {"epl_2026_27": {"state": "probing"}}},
        state={"events": {}},
    )

    assert result["requests"] == []
    assert result["skipped"] == {"epl_2026_27": {"acceptance_not_active": 1}}
    try:
        plan_league_lineup_poll(
            now=datetime(2026, 8, 24, 12, 0),
            fixtures_by_competition={},
            acceptance_report={"schema_version": 1, "competitions": {}},
            state={"events": {}},
        )
    except ValueError as exc:
        assert str(exc) == "league_lineup_now_must_be_timezone_aware"
    else:
        raise AssertionError("naive now must fail")


def test_planner_honors_deserialized_poll_state_after_a_restart():
    """Ignoring persisted timestamps after restart would duplicate a provider request."""
    restored_state = {
        "schema_version": 1,
        "events": {
            "epl_2026_27:epl-1": {
                "last_polled_at": "2026-08-24T11:58:00+00:00",
                "confirmed": False,
            },
        },
    }
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={"epl_2026_27": [_fixture("epl-1", "2026-08-24T12:30:00Z")]},
        acceptance_report=_active_report("epl_2026_27"),
        state=restored_state,
    )

    assert result["requests"] == []
    assert result["skipped"] == {"epl_2026_27": {"poll_throttled": 1}}
    assert result["next_due_at"] == "2026-08-24T12:03:00+00:00"


def test_planner_sorts_requests_by_competition_then_event_id():
    """Leaving iteration order observable would make dry-run plans non-deterministic."""
    result = plan_league_lineup_poll(
        now=NOW,
        fixtures_by_competition={
            "laliga_2026_27": [_fixture("z-event", "2026-08-24T13:00:00Z")],
            "epl_2026_27": [
                _fixture("z-event", "2026-08-24T13:00:00Z"),
                _fixture("a-event", "2026-08-24T13:00:00Z"),
            ],
        },
        acceptance_report=_active_report("laliga_2026_27", "epl_2026_27"),
        state={"events": {}},
    )

    assert [(row["competition_id"], row["event_id"]) for row in result["requests"]] == [
        ("epl_2026_27", "a-event"),
        ("epl_2026_27", "z-event"),
        ("laliga_2026_27", "z-event"),
    ]
