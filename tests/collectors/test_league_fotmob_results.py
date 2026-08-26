from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from worldcup.collectors.league_fotmob_results import parse_fotmob_league_results
from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


COMPETITION = "epl_2026_27"
CAPTURED_AT = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-08-28T19:00:00Z"
FOTMOB_SAMPLE_SHA256 = "a861a1aa1c83b7193ea68a6705abc44647fb49194ee80a557356b55fe5bf1e00"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fotmob_results"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
    )


def _status(status: str, *, home_score: object = 2, away_score: object = 1) -> dict[str, object]:
    finished = status == "finished"
    reason = {
        "short": "FT" if finished else {"scheduled": "NS", "live": "1H", "postponed": "PP"}[status],
        "long": "Full-Time" if finished else status.title(),
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
            "matchTimeUTC": "Fri, Aug 28, 2026, 19:00 UTC",
            "matchTimeUTCDate": kickoff,
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
        },
        "header": {"status": {
            **_status(status, home_score=home_score, away_score=away_score),
            **({
                "halfs": {
                    "firstExtraHalfStarted": "",
                    "secondExtraHalfStarted": "",
                },
                "whoLostOnPenalties": None,
                "whoLostOnAggregated": "",
            } if status == "finished" else {}),
        }},
    }


def _details_with_real_proof() -> dict[str, object]:
    return _details()


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
    parsed = _parse(_load("calendar_finished.json"), {"1001": _load("details_1001_finished.json")})

    result = parsed["results"][0]
    assert set(parsed) == {"competition_id", "results", "pending", "source_events", "source_fingerprint"}
    assert result["result_scope"] == "football_90min"
    assert (result["home_score"], result["away_score"]) == (2, 3)
    assert result["home_canonical"] == "arsenal"
    assert result["away_canonical"] == "chelsea"
    assert result["kickoff_at_utc"] == "2026-08-24T19:00:00+00:00"
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


def test_legacy_evidence_cannot_authorize_the_fotmob_parser():
    """A valid The Odds API proof is not evidence that a FotMob result is a 90-minute final."""
    legacy = build_result_contract_evidence(
        competition_id=COMPETITION,
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )

    parsed = _parse(_calendar(status="finished"), {"1001": _details()}, result_contract_evidence=legacy)
    assert parsed["results"] == []
    assert parsed["pending"] == [{"source_event_id": "1001", "reason": "result_90min_semantics_unverified"}]


def test_scheduled_postponed_and_live_events_remain_pending():
    """Inferring completion from a score string would settle non-terminal matches."""
    for status in ("scheduled", "postponed", "live"):
        parsed = _parse(_calendar(status=status), {"1001": _details(status=status)})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "result_not_finished"}]


def test_duplicate_event_ids_fail_closed():
    """Taking the first repeated provider ID would make a result depend on payload ordering."""
    duplicate_event = _calendar(status="finished")
    duplicate_event["leagues"][0]["matches"].append(deepcopy(duplicate_event["leagues"][0]["matches"][0]))
    parsed = _parse(duplicate_event, {"1001": _details()})
    assert parsed["results"] == []
    assert parsed["pending"] == [{"source_event_id": "1001", "reason": "duplicate_source_event"}]


def test_multiple_target_competition_containers_fail_closed_even_with_distinct_events():
    """Selecting two same-league containers could silently mix separate provider partitions."""
    calendar = _calendar(status="finished")
    other_container = deepcopy(calendar["leagues"][0])
    other_container["matches"][0]["id"] = 1002
    other_container["matches"][0]["home"]["name"] = "Chelsea"
    other_container["matches"][0]["away"]["name"] = "Arsenal"
    calendar["leagues"].append(other_container)
    details = _details(event_id=1002, home="Chelsea", away="Arsenal")

    try:
        _parse(calendar, {"1001": _details(), "1002": details})
    except ValueError as exc:
        assert str(exc) == "fotmob_result_competition_container_duplicate"
    else:
        raise AssertionError("multiple target competition containers must fail closed")


def test_missing_details_and_invalid_scores_are_never_accepted():
    """Fallback to partial details or coercing scores could silently change formal settlement."""
    missing = _parse(_calendar(status="finished"), {})
    assert missing["results"] == []
    assert missing["pending"] == [{"source_event_id": "1001", "reason": "details_missing"}]

    for home_score, away_score in (("two", 1), (-1, 1), (True, 1)):
        parsed = _parse(_calendar(status="finished"), {"1001": _details(home_score=home_score, away_score=away_score)})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "invalid_90min_score"}]


def test_swapped_and_unknown_details_fail_closed():
    """Loose aliases or orientation could bind the wrong provider event."""
    cases = (
        _details(home="Chelsea", away="Arsenal"),
        _details(home="Unknown United"),
    )
    for details in cases:
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []


def test_detail_event_id_must_match_calendar_event_id():
    """A detail response for another event must never settle the calendar event."""
    parsed = _parse(_calendar(status="finished"), {"1001": _details(event_id=1002)})

    assert parsed["results"] == []
    assert parsed["pending"] == [{"source_event_id": "1001", "reason": "details_event_mismatch"}]


def test_detail_iso_kickoff_is_required_and_display_value_is_ignored():
    """Localized display time is not a machine-readable identity field."""
    display_is_irrelevant = _details()
    display_is_irrelevant["general"]["matchTimeUTC"] = {"untrusted": "display"}
    accepted = _parse(_calendar(status="finished"), {"1001": display_is_irrelevant})
    assert len(accepted["results"]) == 1

    invalid_values = ("delete", None, 123, "not-a-time", "2026-08-28T19:00:00")
    for value in invalid_values:
        details = _details()
        if value == "delete":
            del details["general"]["matchTimeUTCDate"]
        else:
            details["general"]["matchTimeUTCDate"] = value
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "kickoff_invalid"}]


def test_detail_iso_kickoff_tolerance_is_inclusive_at_exactly_five_minutes():
    """The documented five-minute identity tolerance must not expand by one second."""
    boundary = _details(kickoff="2026-08-28T19:05:00Z")
    beyond = _details(kickoff="2026-08-28T19:05:01Z")

    accepted = _parse(_calendar(status="finished"), {"1001": boundary})
    rejected = _parse(_calendar(status="finished"), {"1001": beyond})

    assert len(accepted["results"]) == 1
    assert rejected["results"] == []
    assert rejected["pending"] == [{"source_event_id": "1001", "reason": "kickoff_mismatch"}]


def test_extra_half_proof_is_present_empty_and_type_safe():
    for field in ("firstExtraHalfStarted", "secondExtraHalfStarted"):
        for mutation in ("delete", "2026-08-28T21:05:00Z", [], {}):
            details = deepcopy(_details_with_real_proof())
            halfs = details["header"]["status"]["halfs"]
            if mutation == "delete":
                del halfs[field]
            else:
                halfs[field] = mutation
            parsed = _parse(_calendar(status="finished"), {"1001": details})
            assert parsed["results"] == []
            assert parsed["pending"][0]["reason"] == "result_90min_score_unverified"


def test_penalty_loser_proof_is_present_null_and_type_safe():
    for mutation in ("delete", "", "Chelsea", [], {}):
        details = deepcopy(_details_with_real_proof())
        status = details["header"]["status"]
        if mutation == "delete":
            del status["whoLostOnPenalties"]
        else:
            status["whoLostOnPenalties"] = mutation
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"][0]["reason"] == "result_90min_score_unverified"


def test_aggregate_loser_proof_is_present_empty_or_null_and_type_safe():
    for valid in ("", None):
        details = deepcopy(_details_with_real_proof())
        details["header"]["status"]["whoLostOnAggregated"] = valid
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert len(parsed["results"]) == 1

    for mutation in ("delete", "Chelsea", [], {}):
        details = deepcopy(_details_with_real_proof())
        status = details["header"]["status"]
        if mutation == "delete":
            del status["whoLostOnAggregated"]
        else:
            status["whoLostOnAggregated"] = mutation
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"][0]["reason"] == "result_90min_score_unverified"


def test_extra_half_container_must_be_a_mapping():
    for halfs in ("delete", None, "", [], True):
        details = deepcopy(_details_with_real_proof())
        status = details["header"]["status"]
        if halfs == "delete":
            del status["halfs"]
        else:
            status["halfs"] = halfs
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"][0]["reason"] == "result_90min_score_unverified"


def test_extra_time_or_penalty_only_fields_cannot_supply_a_90_minute_score():
    """Using AET, shootout, or alternate score fields would corrupt the formal 90-minute tally."""
    extra_time = _details()
    extra_time["header"]["status"]["reason"] = {"short": "AET", "long": "After extra time"}
    penalty_only = _details()
    del penalty_only["header"]["status"]["scoreStr"]
    penalty_only["header"]["teams"] = [{"score": 5}, {"score": 4}]
    penalty_only["header"]["status"]["reason"] = {"short": "PEN", "long": "Penalties"}

    for details in (extra_time, penalty_only):
        parsed = _parse(_calendar(status="finished"), {"1001": details})
        assert parsed["results"] == []
        assert parsed["pending"] == [{"source_event_id": "1001", "reason": "result_90min_score_unverified"}]


def test_calendar_and_detail_90_minute_terminal_score_must_agree():
    """Trusting only detail semantics or score can settle a disagreement between two FotMob response shapes."""
    calendar_aet = _calendar(status="finished")
    calendar_aet["leagues"][0]["matches"][0]["status"]["reason"] = {
        "short": "AET", "long": "After extra time",
    }
    detail_score_mismatch = _details(home_score=3, away_score=1)

    for calendar, details in (
        (calendar_aet, _details()),
        (_calendar(status="finished"), detail_score_mismatch),
    ):
        parsed = _parse(calendar, {"1001": details})
        assert parsed["results"] == []


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
