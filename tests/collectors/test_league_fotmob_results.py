from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from worldcup.collectors.league_fotmob_results import parse_fotmob_league_results
from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


COMPETITION = "epl_2026_27"
CAPTURED_AT = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-08-28T19:00:00Z"


def _registry() -> LeagueTeamIdentityRegistry:
    return LeagueTeamIdentityRegistry({
        COMPETITION: {"arsenal": ("Arsenal",), "chelsea": ("Chelsea",)},
    })


def _fotmob_evidence(competition_id: str) -> dict[str, object]:
    return build_result_contract_evidence(
        competition_id=competition_id,
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="saved-fotmob-finished-sample-sha256",
    )


def _status(status: str, *, home_score: object = 2, away_score: object = 1) -> dict[str, object]:
    finished = status == "finished"
    reason = {
        "short": "FT" if finished else {"scheduled": "NS", "live": "1H", "postponed": "PP"}[status],
        "long": "Full-Time" if finished else status.title(),
        "extraTime": False,
    }
    return {
        "utcTime": KICKOFF,
        "started": status in {"live", "finished"},
        "cancelled": status == "postponed",
        "finished": finished,
        "scoreStr": f"{home_score} - {away_score}",
        "reason": reason,
    }


def _calendar(
    *,
    status: str,
    league_id: int = 47,
    event_id: object = 1001,
    home: str = "Arsenal",
    away: str = "Chelsea",
    kickoff: str = KICKOFF,
) -> dict[str, object]:
    match = {
        "id": event_id,
        "home": {"name": home},
        "away": {"name": away},
        "status": {**_status(status), "utcTime": kickoff},
    }
    return {"leagues": [{"id": league_id, "name": "Premier League", "matches": [match]}]}


def _details(
    *,
    home_score: object = 2,
    away_score: object = 1,
    league_id: int = 47,
    event_id: object = 1001,
    home: str = "Arsenal",
    away: str = "Chelsea",
    kickoff: str = KICKOFF,
    status: str = "finished",
) -> dict[str, object]:
    return {
        "general": {
            "matchId": event_id,
            "leagueId": league_id,
            "matchTimeUTC": kickoff,
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
        },
        "header": {"status": _status(status, home_score=home_score, away_score=away_score)},
    }


def _parse(calendar: dict[str, object], details: dict[str, dict[str, object]], **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "result_contract_evidence": _fotmob_evidence(COMPETITION),
        "identity_registry": _registry(),
        "captured_at": CAPTURED_AT,
    }
    values.update(overrides)
    return parse_fotmob_league_results(calendar, details, COMPETITION, **values)


def test_finished_integer_score_with_strict_identity_is_accepted():
    """Dropping terminal, identity, or score validation could write a non-90-minute result."""
    parsed = _parse(_calendar(status="finished"), {"1001": _details()})

    result = parsed["results"][0]
    assert set(parsed) == {"competition_id", "results", "pending", "source_events", "source_fingerprint"}
    assert result["result_scope"] == "football_90min"
    assert (result["home_score"], result["away_score"]) == (2, 1)
    assert result["home_canonical"] == "arsenal"
    assert result["away_canonical"] == "chelsea"
    assert result["kickoff_at_utc"] == "2026-08-28T19:00:00+00:00"
    assert result["captured_at"] == "2026-08-29T00:00:00+00:00"
    assert len(result["source_fingerprint"]) == 64
    assert parsed["pending"] == []


def test_unverified_semantics_and_wrong_competition_fail_closed():
    """A caller cannot self-assert 90-minute semantics or relabel another league."""
    args = (_calendar(status="finished"), {"1001": _details()})
    assert _parse(*args, result_contract_evidence=None)["results"] == []

    wrong_calendar = _calendar(status="finished", league_id=87)
    try:
        _parse(wrong_calendar, {"1001": _details()})
    except ValueError as exc:
        assert str(exc) == "fotmob_result_competition_mismatch"
    else:
        raise AssertionError("wrong competition must fail closed")


def test_scheduled_postponed_and_live_events_remain_pending():
    """Inferring completion from a score string would settle non-terminal matches."""
    for status in ("scheduled", "postponed", "live"):
        parsed = _parse(_calendar(status=status), {"1001": _details(status=status)})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "result_not_finished"}]


def test_duplicate_event_ids_and_containers_fail_closed():
    """Taking the first repeated provider ID would make a result depend on payload ordering."""
    duplicate_event = _calendar(status="finished")
    duplicate_event["leagues"][0]["matches"].append(deepcopy(duplicate_event["leagues"][0]["matches"][0]))
    duplicate_container = _calendar(status="finished")
    duplicate_container["leagues"].append(deepcopy(duplicate_container["leagues"][0]))

    for calendar in (duplicate_event, duplicate_container):
        parsed = _parse(calendar, {"1001": _details()})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "duplicate_source_event"}]


def test_missing_details_and_invalid_scores_are_never_accepted():
    """Fallback to partial details or coercing scores could silently change formal settlement."""
    missing = _parse(_calendar(status="finished"), {})
    assert missing["results"] == []
    assert missing["pending"] == [{"source_event_id": "1001", "reason": "details_missing"}]

    for home_score, away_score in (("two", 1), (-1, 1), (True, 1)):
        parsed = _parse(_calendar(status="finished"), {"1001": _details(home_score=home_score, away_score=away_score)})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "invalid_90min_score"}]


def test_swapped_unknown_and_kickoff_mismatched_details_fail_closed():
    """Loose aliases, orientation, or times could bind the wrong provider event."""
    cases = (
        _details(home="Chelsea", away="Arsenal"),
        _details(home="Unknown United"),
        _details(kickoff="2026-08-28T19:06:00Z"),
    )
    for details in cases:
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []


def test_extra_time_or_penalty_only_fields_cannot_supply_a_90_minute_score():
    """Using AET, shootout, or alternate score fields would corrupt the formal 90-minute tally."""
    extra_time = _details()
    extra_time["header"]["status"]["reason"] = {"short": "AET", "long": "After extra time", "extraTime": True}
    penalty_only = _details()
    del penalty_only["header"]["status"]["scoreStr"]
    penalty_only["header"]["teams"] = [{"score": 5}, {"score": 4}]
    penalty_only["header"]["status"]["reason"] = {"short": "PEN", "long": "Penalties", "extraTime": False}

    for details in (extra_time, penalty_only):
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "result_90min_score_unverified"}]


def test_naive_capture_time_is_rejected_before_any_result_is_emitted():
    """A local-time capture timestamp would make immutable evidence ordering ambiguous."""
    try:
        _parse(
            _calendar(status="finished"),
            {"1001": _details()},
            captured_at=datetime(2026, 8, 29, 0, 0),
        )
    except ValueError as exc:
        assert str(exc) == "fotmob_result_captured_at_must_be_timezone_aware"
    else:
        raise AssertionError("naive captured_at must be rejected")
