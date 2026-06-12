import json
from pathlib import Path

from worldcup.collectors.theoddsapi_scores import parse_theoddsapi_scores

PROBE_DIR = Path(__file__).resolve().parents[2] / "data" / "probe"

SAMPLE = [
    {
        "id": "e1",
        "commence_time": "2026-06-11T19:00:00Z",
        "completed": True,
        "home_team": "Mexico",
        "away_team": "South Africa",
        "scores": [
            {"name": "Mexico", "score": "2"},
            {"name": "South Africa", "score": "0"},
        ],
        "last_update": "2026-06-12T01:11:15Z",
    },
    {
        "id": "e2",
        "commence_time": "2026-06-12T02:00:00Z",
        "completed": False,
        "home_team": "South Korea",
        "away_team": "Czech Republic",
        "scores": None,
        "last_update": "2026-06-12T01:11:15Z",
    },
    {
        "id": "e3",
        "commence_time": "2026-06-12T19:00:00Z",
        "completed": True,
        "home_team": "Canada",
        "away_team": "Bosnia and Herzegovina",
        "scores": [{"name": "Canada", "score": "1"}],
        "last_update": "2026-06-13T01:00:00Z",
    },
]


def test_parse_scores_returns_completed_results_only():
    results = parse_theoddsapi_scores(SAMPLE)

    assert len(results) == 1
    result = results[0]
    assert result.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert result.home_team_name == "Mexico"
    assert result.away_team_name == "South Africa"
    assert result.home_canonical == "mexico"
    assert result.away_canonical == "south_africa"
    assert result.home_score == 2
    assert result.away_score == 0


def test_parse_scores_skips_incomplete_score_arrays():
    results = parse_theoddsapi_scores([SAMPLE[2]])
    assert results == []


def test_saved_scores_probe_sample_parses_when_present():
    path = PROBE_DIR / "theoddsapi_scores_sample.json"
    if not path.exists():
        return
    results = parse_theoddsapi_scores(json.loads(path.read_text(encoding="utf-8")))
    assert any(
        r.home_canonical == "mexico" and r.home_score == 2 and r.away_score == 0
        for r in results
    )
