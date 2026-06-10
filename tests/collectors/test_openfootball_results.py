from worldcup.collectors.openfootball import parse_openfootball_results

DOC = {
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "13:00 UTC-6",
            "team1": "Mexico",
            "team2": "South Africa",
            "score1": 2,
            "score2": 1,
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "20:00 UTC-6",
            "team1": "South Korea",
            "team2": "Czech Republic",
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-12",
            "time": "13:00 UTC-6",
            "team1": "Canada",
            "team2": "Bosnia and Herzegovina",
            "score": {"ft": [0, 0]},
        },
        {
            "round": "Round of 32",
            "date": "2026-06-29",
            "time": "13:00 UTC-6",
            "team1": "1A",
            "team2": "3C/D/F",
            "score1": 1,
            "score2": 0,
        },
    ]
}


def test_parse_results_extracts_only_finished_real_matches():
    results = parse_openfootball_results(DOC)
    assert len(results) == 2
    first = results[0]
    assert first.home_team_name == "Mexico"
    assert (first.home_score, first.away_score) == (2, 1)
    assert first.home_canonical == "mexico"
    second = results[1]
    assert second.home_team_name == "Canada"
    assert (second.home_score, second.away_score) == (0, 0)


def test_parse_results_keeps_kickoff_utc():
    results = parse_openfootball_results(DOC)
    assert results[0].kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
