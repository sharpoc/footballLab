from worldcup.league_postmatch import build_league_postmatch


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
