import json
from pathlib import Path

from worldcup.collectors.league_fotmob_lineups import parse_confirmed_fotmob_lineups
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "fotmob_lineups"
COMPETITION = "epl_2026_27"
FETCHED_AT = "2026-08-24T12:20:00Z"
LOCAL_FIXTURES = [{
    "event_id": "odds-epl-1",
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
    boundary_details["general"]["matchTimeUTC"] = "2026-08-24T13:05:00Z"
    accepted = _parse(calendar_payload=boundary, details_by_match_id={"1001": boundary_details})
    rejected = _parse(calendar="calendar_kickoff_mismatch.json")

    assert len(accepted["accepted"]) == 1
    assert rejected["accepted"] == []
    assert rejected["rejected"][0]["reason"] == "kickoff_mismatch"


def test_first_capture_at_or_after_kickoff_is_rejected():
    """Accepting a post-kickoff capture would fabricate pre-match evidence."""
    fetched_at = _load("post_kickoff_case.json")["fetched_at"]
    result = _parse(fetched_at=fetched_at)

    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "post_kickoff"
