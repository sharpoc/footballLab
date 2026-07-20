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


def test_parse_results_prefers_90_minute_ft_over_extra_time_and_legacy_scores():
    raw = {
        "matches": [
            {
                "round": "Semi-final",
                "date": "2026-07-15",
                "time": "15:00 UTC-4",
                "team1": "England",
                "team2": "Argentina",
                "score1": 2,
                "score2": 1,
                "score": {"ft": [1, 1], "et": [2, 1], "p": [4, 3]},
            }
        ]
    }

    compatible = parse_openfootball_results(raw)
    strict = parse_openfootball_results(raw, require_score_ft=True)

    assert (compatible[0].home_score, compatible[0].away_score) == (1, 1)
    assert (strict[0].home_score, strict[0].away_score) == (1, 1)


def test_strict_parse_ignores_legacy_score_without_ft():
    raw = {
        "matches": [
            {
                "round": "Semi-final",
                "date": "2026-07-15",
                "time": "15:00 UTC-4",
                "team1": "England",
                "team2": "Argentina",
                "score1": 2,
                "score2": 1,
            }
        ]
    }

    assert len(parse_openfootball_results(raw)) == 1
    assert parse_openfootball_results(raw, require_score_ft=True) == []


def test_strict_parse_ignores_invalid_ft_instead_of_falling_back_to_other_periods():
    raw = {
        "matches": [
            {
                "round": "Semi-final",
                "date": "2026-07-15",
                "time": "15:00 UTC-4",
                "team1": "England",
                "team2": "Argentina",
                "score": {"ft": ["1", 1], "et": [2, 1], "p": [4, 3]},
            }
        ]
    }

    assert parse_openfootball_results(raw, require_score_ft=True) == []


def test_strict_parse_rejects_boolean_and_negative_scores():
    raw = {
        "matches": [
            {
                "round": "Semi-final",
                "date": "2026-07-15",
                "time": "15:00 UTC-4",
                "team1": "England",
                "team2": "Argentina",
                "score": {"ft": [True, False]},
            },
            {
                "round": "Semi-final",
                "date": "2026-07-16",
                "time": "15:00 UTC-4",
                "team1": "Spain",
                "team2": "Brazil",
                "score": {"ft": [-1, 0]},
            },
        ]
    }

    assert parse_openfootball_results(raw, require_score_ft=True) == []
