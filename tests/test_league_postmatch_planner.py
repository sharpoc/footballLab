from datetime import datetime, timezone
import hashlib
import json

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
        "home_team": "Home FC",
        "away_team": "Away FC",
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
    row = _result_row("finished-not-receipted", "2026-08-28T19:00:00Z")
    return {
        "accepted_results": {
            "epl_2026_27": _receipt("epl_2026_27", [row]),
        },
    }


def _result_row(event_id: str, kickoff: str, **overrides: object) -> dict:
    value = {
        "competition_id": "epl_2026_27",
        "source_event_id": event_id,
        "kickoff_at_utc": kickoff,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "home_canonical": "home_fc",
        "away_canonical": "away_fc",
        "home_score": 2,
        "away_score": 0,
        "captured_at": "2026-08-29T00:00:00Z",
        "result_scope": "football_90min",
        "source_fingerprint": "a" * 64,
    }
    value.update(overrides)
    return value


def _receipt(competition_id: str, rows: list[dict]) -> dict:
    ordered = []
    for row in sorted(rows, key=lambda row: row["source_event_id"]):
        normalized = dict(row)
        for key in ("kickoff_at_utc", "captured_at"):
            normalized[key] = normalized[key].replace("Z", "+00:00")
        ordered.append(normalized)
    core = {"schema_version": 1, "competition_id": competition_id, "results": ordered}
    encoded = json.dumps(core, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {**core, "fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


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


def test_planner_does_not_trust_forged_or_identity_conflicting_receipts():
    """A receipt hash or canonical-team mismatch must not suppress the provider check."""
    fixture = {"epl_2026_27": [_fixture("epl-1", "2026-08-28T19:00:00Z")]}
    forged = _receipt("epl_2026_27", [_result_row("epl-1", "2026-08-28T19:00:00Z")])
    forged["fingerprint"] = "0" * 64
    conflict = _receipt("epl_2026_27", [
        _result_row("epl-1", "2026-08-28T19:00:00Z", home_canonical="other_fc"),
    ])

    for receipt, reason in (
        (forged, "accepted_result_receipt_invalid"),
        (conflict, "accepted_result_identity_conflict"),
    ):
        plan = plan_league_postmatch(
            _acceptance(active=("epl_2026_27",)), fixture,
            {"accepted_results": {"epl_2026_27": receipt}},
            now=_utc("2026-08-29T00:30:00Z"),
        )
        assert [row["source_event_id"] for row in plan["due"]] == ["epl-1"]
        assert plan["blocked"]["epl_2026_27"][reason] == 1


def test_planner_requires_source_event_id_and_fails_closed_on_duplicate_identity():
    """Fallback event IDs or ambiguous duplicate fixtures could request the wrong result evidence."""
    now = _utc("2026-08-29T00:30:00Z")
    acceptance = _acceptance(active=("epl_2026_27",))
    fallback = _fixture("", "2026-08-28T19:00:00Z")
    fallback["event_id"] = "provider-only"
    duplicate = [_fixture("epl-1", "2026-08-28T19:00:00Z"), _fixture("epl-1", "2026-08-28T19:00:00Z")]
    conflict = [_fixture("epl-2", "2026-08-28T19:00:00Z"), _fixture("epl-2", "2026-08-28T20:00:00Z")]

    fallback_plan = plan_league_postmatch(acceptance, {"epl_2026_27": [fallback]}, {}, now=now)
    duplicate_plan = plan_league_postmatch(acceptance, {"epl_2026_27": duplicate}, {}, now=now)
    conflict_plan = plan_league_postmatch(acceptance, {"epl_2026_27": conflict}, {}, now=now)

    assert fallback_plan["due"] == []
    assert fallback_plan["blocked"] == {"epl_2026_27": {"strict_identity_missing": 1}}
    assert duplicate_plan["due"] == []
    assert duplicate_plan["blocked"] == {"epl_2026_27": {"fixture_duplicate_source_event": 2}}
    assert conflict_plan["due"] == []
    assert conflict_plan["blocked"] == {"epl_2026_27": {"fixture_identity_conflict": 2}}
