import copy
import json
from pathlib import Path

from worldcup.collectors.league_fotmob_lineups import parse_confirmed_fotmob_lineups
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fotmob_lineups"
COMPETITION = "epl_2026_27"
FETCHED_AT = "2026-08-24T12:20:00Z"
PROVIDER_COMPETITION_ID = "47"
LOCAL_FIXTURES = [{
    "event_id": "odds-epl-1",
    "competition_id": COMPETITION,
    "kickoff_at_utc": "2026-08-24T13:00:00Z",
    "home_team": "Arsenal",
    "away_team": "Chelsea",
}]


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _registry():
    return LeagueTeamIdentityRegistry({
        COMPETITION: {"arsenal": ("Arsenal",), "chelsea": ("Chelsea",)},
    })


def _parse(calendar="calendar_confirmed.json", details="details_confirmed.json", **overrides):
    values = {
        "calendar_payload": _load(calendar),
        "details_by_match_id": {"1001": _load(details)},
        "competition_id": COMPETITION,
        "local_fixtures": LOCAL_FIXTURES,
        "registry": _registry(),
        "fetched_at": FETCHED_AT,
        "provider_competition_id": PROVIDER_COMPETITION_ID,
    }
    values.update(overrides)
    return parse_confirmed_fotmob_lineups(**values)


def test_confirmed_11_plus_11_is_canonically_joined_with_only_safe_fields():
    """Accepting without the strict canonical join would attach a lineup to the wrong local event."""
    result = _parse()

    assert result["rejected"] == []
    assert len(result["accepted"]) == 1
    accepted = result["accepted"][0]
    fingerprint = accepted.pop("lineup_fingerprint")
    assert len(fingerprint) == 64
    assert set(fingerprint) <= set("0123456789abcdef")
    assert accepted == {
        "schema_version": 1,
        "provider": "fotmob",
        "competition_id": COMPETITION,
        "event_id": "odds-epl-1",
        "source_match_id": "1001",
        "kickoff_at_utc": "2026-08-24T13:00:00+00:00",
        "fetched_at": "2026-08-24T12:20:00+00:00",
        "lineup_status": "confirmed",
        "home_canonical": "arsenal",
        "away_canonical": "chelsea",
        "home_formation": "4-3-3",
        "away_formation": "4-2-3-1",
        "home_starting": [
            {"player_id": str(i), "name": f"Home {i - 100}"} for i in range(101, 112)
        ],
        "away_starting": [
            {"player_id": str(i), "name": f"Away {i - 200}"} for i in range(201, 212)
        ],
    }
    assert not ({"raw", "raw_response", "headers", "request_headers", "cookie"} & set(accepted))


def test_real_fotmob_standard_11_plus_11_is_accepted_with_explicit_derived_basis():
    """Requiring a nonexistent lineupStatus field would discard FotMob's real standard starting XI."""
    details = _load("details_confirmed.json")
    lineup = details["content"]["lineup"]
    del lineup["lineupStatus"]
    lineup["lineupType"] = "standard"
    lineup["source"] = "enetpulse"
    details["general"].update({"started": False, "finished": False})
    details["header"] = {
        "status": {"started": False, "finished": False, "cancelled": False},
    }

    result = _parse(details_by_match_id={"1001": details})

    assert result["rejected"] == []
    assert len(result["accepted"]) == 1
    assert result["accepted"][0]["lineup_status"] == "confirmed"
    assert result["accepted"][0]["provider_lineup_type"] == "standard"
    assert result["accepted"][0]["confirmation_basis"] == "fotmob_standard_pregame_11v11"


def test_standard_lineup_requires_explicit_provider_pregame_state():
    """A standard lineup must not be promoted when provider state is missing or says play started."""
    for provider_state in (None, True):
        details = _load("details_confirmed.json")
        lineup = details["content"]["lineup"]
        del lineup["lineupStatus"]
        lineup["lineupType"] = "standard"
        if provider_state is not None:
            details["general"]["started"] = provider_state
            details["general"]["finished"] = False
            details["header"] = {
                "status": {"started": provider_state, "finished": False, "cancelled": False},
            }

        result = _parse(details_by_match_id={"1001": details})

        assert result["accepted"] == []
        assert result["rejected"][0]["reason"] == "lineup_status_unknown"


def test_standard_lineup_requires_each_provider_pregame_state_field():
    """Removing or flipping any one provider pregame flag must block derived acceptance."""
    fields = (
        ("general", "started"),
        ("general", "finished"),
        ("header", "started"),
        ("header", "finished"),
        ("header", "cancelled"),
    )
    for section, field in fields:
        for mutation in ("missing", True):
            details = _load("details_confirmed.json")
            lineup = details["content"]["lineup"]
            del lineup["lineupStatus"]
            lineup["lineupType"] = "standard"
            details["general"].update({"started": False, "finished": False})
            details["header"] = {
                "status": {"started": False, "finished": False, "cancelled": False},
            }
            target = details[section] if section == "general" else details["header"]["status"]
            if mutation == "missing":
                del target[field]
            else:
                target[field] = mutation

            result = _parse(details_by_match_id={"1001": details})

            assert result["accepted"] == []
            assert result["rejected"][0]["reason"] == "lineup_status_unknown"


def test_real_fotmob_lineup_type_predicted_is_rejected_as_predicted():
    """The real predicted schema must never enter the standard starting-XI branch."""
    details = _load("details_confirmed.json")
    lineup = details["content"]["lineup"]
    del lineup["lineupStatus"]
    lineup["lineupType"] = "predicted"
    details["general"].update({"started": False, "finished": False})
    details["header"] = {
        "status": {"started": False, "finished": False, "cancelled": False},
    }

    result = _parse(details_by_match_id={"1001": details})

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "lineup_predicted"


def test_lineup_fingerprint_is_stable_when_provider_player_order_changes():
    """Hashing payload order would emit a false new-lineup event for the same starting XI."""
    reordered = _load("details_confirmed.json")
    lineup = reordered["content"]["lineup"]
    lineup["homeTeam"]["starters"].reverse()
    lineup["awayTeam"]["starters"].reverse()

    first = _parse()["accepted"][0]
    second = _parse(details_by_match_id={"1001": reordered})["accepted"][0]
    assert second["lineup_fingerprint"] == first["lineup_fingerprint"]
    assert second["home_starting"] == first["home_starting"]


def test_predicted_unknown_and_incomplete_lineups_have_exact_safe_reasons():
    """Treating 22 names as sufficient would let non-confirmed lineups change recommendations."""
    cases = (
        ("details_predicted.json", "lineup_predicted"),
        ("details_unknown.json", "lineup_status_unknown"),
        ("details_incomplete.json", "incomplete_starting_xi"),
    )
    for detail_name, reason in cases:
        result = _parse(details=detail_name)
        assert result["accepted"] == []
        assert result["rejected"] == [{
            "provider": "fotmob",
            "competition_id": COMPETITION,
            "source_match_id": "1001",
            "reason": reason,
        }]


def test_identity_join_rejects_swapped_and_unknown_clubs_without_slug_fallback():
    """A swapped or slug-normalized join would silently bind the wrong provider fixture."""
    swapped = _parse(calendar="calendar_swapped.json")
    unknown = _parse(calendar="calendar_unknown_club.json")

    assert swapped["accepted"] == []
    assert swapped["rejected"][0]["reason"] == "home_away_mismatch"
    assert unknown["accepted"] == []
    assert unknown["rejected"][0]["reason"] == "unmatched_team"


def test_duplicate_candidate_fails_closed_for_every_conflicting_match():
    """Taking the first duplicate would make identity depend on provider ordering."""
    details_two = _load("details_confirmed.json")
    details_two["general"]["matchId"] = 1002
    result = _parse(
        calendar="calendar_duplicate.json",
        details_by_match_id={"1001": _load("details_confirmed.json"), "1002": details_two},
    )

    assert result["accepted"] == []
    assert result["rejected"] == [
        {"provider": "fotmob", "competition_id": COMPETITION, "source_match_id": "1001", "reason": "duplicate_candidate"},
        {"provider": "fotmob", "competition_id": COMPETITION, "source_match_id": "1002", "reason": "duplicate_candidate"},
    ]


def test_kickoff_tolerance_is_inclusive_at_five_minutes_and_rejects_later():
    """A tolerance wider than five minutes could cross-bind nearby same-team fixtures."""
    boundary = _load("calendar_confirmed.json")
    boundary["leagues"][0]["matches"][0]["status"]["utcTime"] = "2026-08-24T13:05:00Z"
    boundary_details = _load("details_confirmed.json")
    boundary_details["general"]["matchTimeUTC"] = {"ignored": "display value"}
    boundary_details["general"]["matchTimeUTCDate"] = "2026-08-24T13:05:00Z"
    accepted = _parse(calendar_payload=boundary, details_by_match_id={"1001": boundary_details})
    rejected = _parse(calendar="calendar_kickoff_mismatch.json")

    assert len(accepted["accepted"]) == 1
    assert rejected["accepted"] == []
    assert rejected["rejected"][0]["reason"] == "kickoff_mismatch"


def test_detail_iso_kickoff_is_required_and_display_value_is_ignored():
    """Only FotMob's ISO field can prove detail-to-local kickoff identity."""
    display_is_irrelevant = _load("details_confirmed.json")
    display_is_irrelevant["general"]["matchTimeUTC"] = None
    accepted = _parse(details_by_match_id={"1001": display_is_irrelevant})
    assert len(accepted["accepted"]) == 1

    for value in ("delete", None, "not-a-time", "2026-08-24T13:00:00"):
        details = _load("details_confirmed.json")
        if value == "delete":
            del details["general"]["matchTimeUTCDate"]
        else:
            details["general"]["matchTimeUTCDate"] = value
        parsed = _parse(details_by_match_id={"1001": details})
        assert parsed["accepted"] == []
        assert parsed["rejected"] == [{
            "provider": "fotmob",
            "competition_id": COMPETITION,
            "source_match_id": "1001",
            "reason": "invalid_kickoff",
        }]


def test_first_capture_at_or_after_kickoff_is_rejected():
    """Accepting a post-kickoff capture would fabricate pre-match evidence."""
    fetched_at = _load("post_kickoff_case.json")["fetched_at"]
    result = _parse(fetched_at=fetched_at)

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "post_kickoff"


def test_cross_league_calendar_cannot_be_relabelled_as_the_requested_competition():
    """Dropping the provider league ID would accept same-name clubs from another competition."""
    result = _parse(calendar="calendar_cross_league.json")

    assert result["accepted"] == []
    assert result["rejected"] == [{
        "provider": "fotmob",
        "competition_id": COMPETITION,
        "source_match_id": "1001",
        "reason": "competition_mismatch",
    }]


def test_provider_competition_identity_must_be_present_in_calendar_and_details():
    """Missing or conflicting provider competition evidence must not be inferred from team names."""
    missing_approved_id = _parse(provider_competition_id=None)
    missing_calendar_id = _parse(calendar="calendar_missing_competition.json")
    wrong_details_id = _parse(details="details_cross_league.json")

    assert missing_approved_id["accepted"] == []
    assert missing_approved_id["rejected"][0]["reason"] == "competition_identity_unverified"
    assert missing_calendar_id["accepted"] == []
    assert missing_calendar_id["rejected"][0]["reason"] == "competition_identity_missing"
    assert wrong_details_id["accepted"] == []
    assert wrong_details_id["rejected"][0]["reason"] == "competition_mismatch"


def test_local_fixture_competition_must_not_be_relabelled():
    """A local row scoped to another league cannot inherit the parser's requested competition."""
    wrong_local = [{**LOCAL_FIXTURES[0], "competition_id": "laliga_2026_27"}]
    result = _parse(local_fixtures=wrong_local)

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "competition_mismatch"


def test_duplicate_league_container_for_the_same_match_fails_closed():
    """The same provider match repeated across containers is not a unique competition join."""
    result = _parse(calendar="calendar_duplicate_container.json")

    assert result["accepted"] == []
    assert {row["reason"] for row in result["rejected"]} == {"duplicate_candidate"}


def test_non_scalar_player_id_is_rejected_without_stringifying_raw_metadata():
    """Stringifying a player-ID mapping would persist arbitrary raw metadata as public identity."""
    details = copy.deepcopy(_load("details_confirmed.json"))
    details["content"]["lineup"]["homeTeam"]["starters"][0]["id"] = {
        "raw_response": "SECRET_PLAYER_ID",
    }
    result = _parse(details_by_match_id={"1001": details})

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "invalid_player_identity"
    assert "SECRET_PLAYER_ID" not in json.dumps(result, ensure_ascii=False)


def test_non_scalar_names_and_formations_are_dropped_without_metadata_escape():
    """Nested objects in display fields must never be serialized into safe lineup output."""
    details = copy.deepcopy(_load("details_confirmed.json"))
    lineup = details["content"]["lineup"]
    lineup["homeTeam"]["formation"] = {"headers": {"authorization": "SECRET_FORMATION"}}
    lineup["homeTeam"]["starters"][0]["name"]["fullName"] = {
        "cookie": "SECRET_PLAYER_NAME",
    }
    result = _parse(details_by_match_id={"1001": details})

    assert len(result["accepted"]) == 1
    accepted = result["accepted"][0]
    assert accepted["home_formation"] is None
    assert accepted["home_starting"][0]["name"] is None
    encoded = json.dumps(result, ensure_ascii=False)
    assert "SECRET_FORMATION" not in encoded
    assert "SECRET_PLAYER_NAME" not in encoded
    assert "headers" not in encoded.casefold()
    assert "authorization" not in encoded.casefold()
    assert "cookie" not in encoded.casefold()


def test_non_scalar_local_event_id_cannot_enter_safe_output():
    """A mapping-shaped event ID must not be stringified into accepted or rejected rows."""
    local = [{**LOCAL_FIXTURES[0], "event_id": {"raw_response": "SECRET_EVENT"}}]
    result = _parse(local_fixtures=local)

    assert result["accepted"] == []
    assert "SECRET_EVENT" not in json.dumps(result, ensure_ascii=False)
