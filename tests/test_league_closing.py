from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_closing import LeagueClosingStore, select_league_closings


def _snapshot(snapshot_at: str, *, kickoff: str = "2026-08-24T18:00:00Z", odds: float = 1.9) -> dict:
    competition = {"id": "epl_2026_27", "name": "英超 2026/27"}
    return {
        "snapshot_at": snapshot_at,
        "competition": competition,
        "matches": [{
            "source_event_id": "epl-event-1",
            "kickoff_at_utc": kickoff,
            "home_team": "Home FC",
            "away_team": "Away FC",
            "home_canonical": "home_fc",
            "away_canonical": "away_fc",
            "competition": competition,
            "match_decision": {
                "schema_version": 2,
                "label": "MATCH_PICK",
                "market": "1X2",
                "selection": "home",
                "odds": odds,
            },
        }],
    }


def test_closing_uses_last_legal_snapshot_strictly_before_kickoff():
    payload = select_league_closings([
        _snapshot("2026-08-24T10:00:00Z", odds=1.9),
        _snapshot("2026-08-24T17:59:59Z", odds=1.8),
        _snapshot("2026-08-24T18:00:00Z", odds=1.7),
    ], "epl_2026_27")

    closing = payload["closings"]["epl-event-1"]
    assert closing["closing_snapshot_at"] == "2026-08-24T17:59:59+00:00"
    assert closing["closing_match_decision"]["odds"] == 1.8


def test_conflicting_event_identity_fails_closed():
    conflict = _snapshot("2026-08-24T12:00:00Z")
    conflict["matches"][0]["away_canonical"] = "different_fc"
    try:
        select_league_closings([_snapshot("2026-08-24T10:00:00Z"), conflict], "epl_2026_27")
    except ValueError as exc:
        assert str(exc) == "closing_identity_conflict: epl-event-1"
    else:
        raise AssertionError("expected identity conflict")


def test_closing_store_is_atomic_and_idempotent():
    payload = select_league_closings([_snapshot("2026-08-24T17:00:00Z")], "epl_2026_27")
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "epl_2026_27" / "closing.json"
        store = LeagueClosingStore(path)
        assert store.commit(payload) == "stored"
        before = path.read_bytes()
        assert store.commit(payload) == "unchanged"
        assert path.read_bytes() == before
