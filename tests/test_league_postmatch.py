from worldcup.league_postmatch import build_league_postmatch, merge_league_postmatch


def test_postmatch_settles_matching_observed_schema_v2_closing():
    closing = {
        "competition_id": "epl_2026_27",
        "closings": {"epl-event-1": {
            "competition_id": "epl_2026_27",
            "source_event_id": "epl-event-1",
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Home FC", "away_team": "Away FC",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "closing_snapshot_at": "2026-08-24T17:59:00+00:00",
            "closing_match_decision": {
                "schema_version": 2, "label": "MATCH_PICK",
                "market": "1X2", "selection": "home", "odds": 1.8,
            },
        }},
    }
    results = {"competition_id": "epl_2026_27", "results": [{
        "competition_id": "epl_2026_27", "source_event_id": "epl-event-1",
        "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
        "home_canonical": "home_fc", "away_canonical": "away_fc",
        "home_score": 2, "away_score": 0, "result_scope": "football_90min",
    }]}
    block = build_league_postmatch(closing, results, "epl_2026_27")
    assert block["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}
    assert block["matches"][0]["closing_match_decision_result"]["status"] == "hit"


def _closing(event_id: str, *, selection: str = "home") -> dict:
    return {
        "competition_id": "epl_2026_27",
        "closings": {event_id: {
            "competition_id": "epl_2026_27", "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "closing_snapshot_at": "2026-08-24T17:59:00+00:00",
            "closing_match_decision": {
                "schema_version": 2, "label": "MATCH_PICK", "market": "1X2", "selection": selection,
            },
        }},
    }


def _result(event_id: str, *, home_score: int = 2, away_score: int = 0) -> dict:
    return {
        "competition_id": "epl_2026_27",
        "results": [{
            "competition_id": "epl_2026_27", "source_event_id": event_id,
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "home_score": home_score, "away_score": away_score, "result_scope": "football_90min",
        }],
    }


def test_cumulative_postmatch_is_idempotent_and_keeps_missing_closing_visible():
    """Replacing an accumulated block would hide a prior accepted result or count it twice."""
    first = merge_league_postmatch(None, _closing("epl-1"), _result("epl-1"), "epl_2026_27")
    second = merge_league_postmatch(first, {"competition_id": "epl_2026_27", "closings": {}}, _result("epl-2"), "epl_2026_27")
    rerun = merge_league_postmatch(second, {"competition_id": "epl_2026_27", "closings": {}}, _result("epl-2"), "epl_2026_27")

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
