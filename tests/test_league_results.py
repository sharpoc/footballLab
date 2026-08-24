from worldcup.league_results import parse_verified_league_results


def _score_event() -> dict:
    return {
        "id": "epl-event-1",
        "sport_key": "soccer_epl",
        "commence_time": "2026-08-24T18:00:00Z",
        "completed": True,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "scores": [
            {"name": "Home FC", "score": "2"},
            {"name": "Away FC", "score": "0"},
        ],
        "last_update": "2026-08-24T20:00:00Z",
    }


def test_unverified_score_semantics_cannot_create_formal_result():
    parsed = parse_verified_league_results(
        [_score_event()], "epl_2026_27", score_semantics_verified=False
    )
    assert parsed["results"] == []
    assert parsed["pending"][0]["reason"] == "result_90min_semantics_unverified"


def test_verified_completed_integer_score_is_accepted():
    parsed = parse_verified_league_results(
        [_score_event()], "epl_2026_27", score_semantics_verified=True
    )
    assert parsed["results"][0]["home_score"] == 2
    assert parsed["results"][0]["away_score"] == 0
    assert parsed["results"][0]["result_scope"] == "football_90min"
