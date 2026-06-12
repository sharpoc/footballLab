from datetime import datetime, timezone

from worldcup.collectors.models import MatchResult
from worldcup.results_capture import upsert_results


def _result(home: str, away: str, hs: int, aw: int) -> MatchResult:
    return MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        home_team_name=home,
        away_team_name=away,
        home_canonical=home.lower(),
        away_canonical=away.lower(),
        home_score=hs,
        away_score=aw,
    )


def test_upsert_adds_new_results():
    rows, added, updated = upsert_results(
        [_result("Mexico", "South Africa", 2, 1)], [], "2026-06-11T22:00:00+00:00"
    )
    assert (added, updated) == (1, 0)
    assert rows[0]["home_score"] == "2"
    assert rows[0]["captured_at"] == "2026-06-11T22:00:00+00:00"


def test_upsert_is_idempotent_for_same_score():
    rows1, _, _ = upsert_results([_result("Mexico", "South Africa", 2, 1)], [], "t1")
    rows2, added, updated = upsert_results([_result("Mexico", "South Africa", 2, 1)], rows1, "t2")
    assert (added, updated) == (0, 0)
    assert rows2[0]["captured_at"] == "t1"


def test_upsert_updates_changed_score():
    rows1, _, _ = upsert_results([_result("Mexico", "South Africa", 1, 1)], [], "t1")
    rows2, added, updated = upsert_results([_result("Mexico", "South Africa", 2, 1)], rows1, "t2")
    assert (added, updated) == (0, 1)
    assert rows2[0]["home_score"] == "2"
    assert rows2[0]["captured_at"] == "t2"


def test_upsert_merges_same_day_pairing_despite_kickoff_minutes():
    first = MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        home_team_name="Mexico",
        away_team_name="South Africa",
        home_canonical="mexico",
        away_canonical="south_africa",
        home_score=2,
        away_score=0,
    )
    second = MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 30, tzinfo=timezone.utc),
        home_team_name="Mexico",
        away_team_name="South Africa",
        home_canonical="mexico",
        away_canonical="south_africa",
        home_score=2,
        away_score=1,
    )

    rows, added, updated = upsert_results([first], [], "2026-06-12T08:00:00+00:00")
    assert added == 1
    rows, added, updated = upsert_results([second], rows, "2026-06-12T09:00:00+00:00")

    assert added == 0
    assert updated == 1
    assert len(rows) == 1
    assert int(rows[0]["home_score"]) == 2 and int(rows[0]["away_score"]) == 1
