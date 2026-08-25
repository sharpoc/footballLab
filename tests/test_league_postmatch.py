from copy import deepcopy
import hashlib
import json

from worldcup.league_postmatch import build_league_postmatch, merge_league_postmatch


def test_postmatch_settles_matching_observed_schema_v2_closing():
    closing = _closing("epl-event-1")
    results = _result("epl-event-1")
    block = build_league_postmatch(closing, results, "epl_2026_27")
    assert block["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}
    assert block["matches"][0]["closing_match_decision_result"]["status"] == "hit"


def _closing(event_id: str, *, selection: str = "home") -> dict:
    return {
        "schema_version": 1,
        "competition_id": "epl_2026_27",
        "closings": {event_id: {
            "competition_id": "epl_2026_27", "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Home FC", "away_team": "Away FC",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "closing_snapshot_at": "2026-08-24T17:59:00+00:00",
            "closing_match_decision": {
                "schema_version": 2, "label": "MATCH_PICK", "market": "1X2", "selection": selection,
            },
        }},
    }


def _result(event_id: str, *, home_score: int = 2, away_score: int = 0) -> dict:
    row = {
            "competition_id": "epl_2026_27", "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Home FC", "away_team": "Away FC",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "home_score": home_score, "away_score": away_score,
            "captured_at": "2026-08-24T20:00:00+00:00",
            "result_scope": "football_90min", "source_fingerprint": "b" * 64,
    }
    return _receipt("epl_2026_27", [row])


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


def test_cumulative_postmatch_is_idempotent_and_keeps_missing_closing_visible():
    """Replacing an accumulated block would hide a prior accepted result or count it twice."""
    first = merge_league_postmatch(None, _closing("epl-1"), _result("epl-1"), "epl_2026_27")
    empty = {"schema_version": 1, "competition_id": "epl_2026_27", "closings": {}}
    second = merge_league_postmatch(first, empty, _result("epl-2"), "epl_2026_27")
    rerun = merge_league_postmatch(second, empty, _result("epl-2"), "epl_2026_27")

    assert [row["source_event_id"] for row in second["matches"]] == ["epl-1"]
    assert second["missing_closing_event_ids"] == ["epl-2"]
    assert second["skipped_no_closing"] == 1
    assert rerun == second


def test_cumulative_postmatch_rejects_a_changed_accepted_score():
    """A later payload must not silently revise the score used for formal settlement."""
    existing = merge_league_postmatch(None, _closing("epl-1"), _result("epl-1"), "epl_2026_27")
    try:
        merge_league_postmatch(existing, _closing("epl-1"), _result("epl-1", home_score=3), "epl_2026_27")
    except ValueError as exc:
        assert str(exc) == "postmatch_result_conflict: epl-1"
    else:
        raise AssertionError("changed accepted score must fail closed")


def test_postmatch_rejects_non_formal_competition_partition():
    """Allowing a World Cup or CSL block here would contaminate six-league formal statistics."""
    closing = _closing("epl-1")
    results = _result("epl-1")
    closing["competition_id"] = "csl_2026"
    closing["closings"]["epl-1"]["competition_id"] = "csl_2026"
    results["competition_id"] = "csl_2026"
    results["results"][0]["competition_id"] = "csl_2026"

    try:
        merge_league_postmatch(None, closing, results, "csl_2026")
    except ValueError as exc:
        assert str(exc) == "postmatch_competition_not_allowed"
    else:
        raise AssertionError("non-formal competition must be rejected")


def test_postmatch_rejects_uncommitted_receipts_and_illegal_closings():
    """String scores, forged receipt hashes, or post-kickoff closings cannot create a formal settlement."""
    forged = _result("epl-1")
    forged["fingerprint"] = "0" * 64
    string_score = _result("epl-1")
    string_score["results"][0]["home_score"] = "2"
    post_kickoff = _closing("epl-1")
    post_kickoff["closings"]["epl-1"]["closing_snapshot_at"] = "2026-08-24T18:00:00+00:00"

    for closing, receipt in ((_closing("epl-1"), forged), (_closing("epl-1"), string_score), (post_kickoff, _result("epl-1"))):
        try:
            merge_league_postmatch(None, closing, receipt, "epl_2026_27")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid receipt or closing must fail closed")


def test_missing_closing_keeps_accepted_result_evidence_and_blocks_revision():
    """A later closing must settle the original accepted score, never a silently revised receipt."""
    missing = merge_league_postmatch(
        None, {"schema_version": 1, "competition_id": "epl_2026_27", "closings": {}},
        _result("epl-1"), "epl_2026_27",
    )
    assert missing["missing_closing_results"]["epl-1"]["home_score"] == 2

    try:
        merge_league_postmatch(missing, _closing("epl-1"), _result("epl-1", home_score=3), "epl_2026_27")
    except ValueError as exc:
        assert str(exc) == "postmatch_result_conflict: epl-1"
    else:
        raise AssertionError("revised missing-closing score must fail closed")


def test_existing_postmatch_embedded_partition_and_settlement_are_revalidated():
    """A tampered retained record must not leak into the next cumulative tally."""
    existing = merge_league_postmatch(None, _closing("epl-1"), _result("epl-1"), "epl_2026_27")
    cross_league = deepcopy(existing)
    cross_league["matches"][0]["competition_id"] = "laliga_2026_27"
    inconsistent = deepcopy(existing)
    inconsistent["matches"][0]["closing_match_decision_result"]["status"] = "miss"

    for invalid in (cross_league, inconsistent):
        try:
            merge_league_postmatch(invalid, _closing("epl-1"), _result("epl-1"), "epl_2026_27")
        except ValueError as exc:
            assert str(exc) == "postmatch_existing_invalid"
        else:
            raise AssertionError("tampered existing record must fail closed")
