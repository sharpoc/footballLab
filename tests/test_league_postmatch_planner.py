from datetime import datetime, timezone

from worldcup.league_postmatch_planner import plan_league_postmatch


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _acceptance(*, active: tuple[str, ...]) -> dict:
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
                "state": "active" if competition_id in active else "probing",
                "fingerprints": fingerprints,
            }
            for competition_id in ("epl_2026_27", "bundesliga_2026_27")
        },
    }


def _fixture(event_id: str, kickoff: str, **overrides: object) -> dict:
    value = {
        "source_event_id": event_id,
        "kickoff_at_utc": kickoff,
        "home_canonical": "home_fc",
        "away_canonical": "away_fc",
    }
    value.update(overrides)
    return value


def _fixtures() -> dict[str, list[dict]]:
    return {
        "epl_2026_27": [
            _fixture("epl-1", "2026-08-28T19:00:00Z"),
            _fixture("future", "2026-08-30T19:00:00Z"),
            _fixture("postponed", "2026-08-28T19:00:00Z", fixture_status="POSTPONED"),
            _fixture("cancelled", "2026-08-28T19:00:00Z", fixture_status="CANCELLED"),
            _fixture("finished-not-receipted", "2026-08-28T19:00:00Z", fixture_status="FINISHED"),
            _fixture("", "2026-08-28T19:00:00Z"),
        ],
        "bundesliga_2026_27": [_fixture("bundesliga-1", "2026-08-28T19:00:00Z")],
    }


def _state() -> dict:
    return {
        "accepted_results": {
            "epl_2026_27": {
                "results": [{
                    "source_event_id": "finished-not-receipted",
                    "result_scope": "football_90min",
                }],
            },
        },
    }


def test_planner_selects_only_active_started_unsettled_events():
    """Treating kickoff as completion would skip the provider check needed for a verified receipt."""
    plan = plan_league_postmatch(
        _acceptance(active=("epl_2026_27",)), _fixtures(), _state(),
        now=_utc("2026-08-29T00:30:00Z"),
    )

    assert [row["source_event_id"] for row in plan["due"]] == ["epl-1"]
    assert plan["blocked"] == {
        "bundesliga_2026_27": {"acceptance_not_active": 1},
        "epl_2026_27": {
            "accepted_result_exists": 1,
            "fixture_cancelled": 1,
            "fixture_postponed": 1,
            "strict_identity_missing": 1,
        },
    }
    assert plan["next_due_at"] == "2026-08-29T00:30:00+00:00"
    assert plan["competitions"]["epl_2026_27"]["due_count"] == 1


def test_planner_defers_future_fixture_and_rejects_naive_times():
    """A future or locally-naive kickoff must not trigger a result provider request."""
    future = plan_league_postmatch(
        _acceptance(active=("epl_2026_27",)),
        {"epl_2026_27": [_fixture("future", "2026-08-30T19:00:00Z")]},
        {},
        now=_utc("2026-08-29T00:30:00Z"),
    )
    assert future["due"] == []
    assert future["next_due_at"] == "2026-08-30T19:00:00+00:00"

    invalid = plan_league_postmatch(
        _acceptance(active=("epl_2026_27",)),
        {"epl_2026_27": [_fixture("bad-time", "2026-08-28T19:00:00")]},
        {},
        now=_utc("2026-08-29T00:30:00Z"),
    )
    assert invalid["due"] == []
    assert invalid["blocked"] == {"epl_2026_27": {"invalid_kickoff": 1}}

    try:
        plan_league_postmatch(_acceptance(active=()), {}, {}, now=datetime(2026, 8, 29, 0, 30))
    except ValueError as exc:
        assert str(exc) == "league_postmatch_now_must_be_timezone_aware"
    else:
        raise AssertionError("naive now must fail")
