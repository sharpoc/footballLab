import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_closing import LeagueClosingStore, merge_league_closings, select_league_closings


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


def test_closing_merge_never_replaces_with_post_kickoff_snapshot():
    """A post-kickoff snapshot must not rewrite the decision known at match start."""
    existing = select_league_closings([_snapshot("2026-08-24T17:00:00Z")], "epl_2026_27")
    post_kickoff = _snapshot("2026-08-24T18:01:00Z", odds=1.2)

    merged = merge_league_closings(existing, [post_kickoff], "epl_2026_27")

    assert merged == existing


def test_closing_merge_advances_only_to_a_later_legal_snapshot():
    """Ignoring a newer pre-kickoff snapshot would settle against stale market evidence."""
    existing = select_league_closings([_snapshot("2026-08-24T16:00:00Z", odds=1.9)], "epl_2026_27")

    merged = merge_league_closings(existing, [_snapshot("2026-08-24T17:00:00Z", odds=1.8)], "epl_2026_27")

    closing = merged["closings"]["epl-event-1"]
    assert closing["closing_snapshot_at"] == "2026-08-24T17:00:00+00:00"
    assert closing["closing_match_decision"]["odds"] == 1.8


def test_closing_merge_rejects_different_decisions_at_same_snapshot_time_in_any_order():
    """Choosing the first equal-time candidate would make a closing depend on history file ordering."""
    first = _snapshot("2026-08-24T17:00:00Z", odds=1.9)
    conflicting = _snapshot("2026-08-24T17:00:00Z", odds=1.8)

    for snapshots in ((first, conflicting), (conflicting, first)):
        try:
            merge_league_closings(None, snapshots, "epl_2026_27")
        except ValueError as exc:
            assert str(exc) == "closing_snapshot_conflict: epl-event-1"
        else:
            raise AssertionError("same-time conflicting decisions must fail closed")


def test_closing_merge_keeps_identical_same_time_decision_idempotent():
    """Equal-time snapshots with the same decision are duplicate history, not a conflict."""
    snapshot = _snapshot("2026-08-24T17:00:00Z", odds=1.9)
    initial = merge_league_closings(None, [snapshot], "epl_2026_27")

    merged = merge_league_closings(initial, [_snapshot("2026-08-24T17:00:00Z", odds=1.9)], "epl_2026_27")

    assert merged == initial


def _event_payload(event_id: str, snapshot_at: str, *, odds: float = 1.9) -> dict:
    snapshot = _snapshot(snapshot_at, odds=odds)
    match = snapshot["matches"][0]
    match["source_event_id"] = event_id
    match["home_team"] = f"{event_id} Home"
    match["away_team"] = f"{event_id} Away"
    match["home_canonical"] = f"{event_id}_home"
    match["away_canonical"] = f"{event_id}_away"
    return select_league_closings([snapshot], "epl_2026_27")


def test_closing_store_full_commit_rejects_deletion_and_nonmonotonic_changes():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "epl_2026_27" / "closing.json"
        store = LeagueClosingStore(path)
        initial = _event_payload("event-1", "2026-08-24T17:00:00Z", odds=1.9)
        store.commit(initial)

        empty = {"schema_version": 1, "competition_id": "epl_2026_27", "closings": {}}
        cases = [
            (empty, "closing_store_deletion"),
            (_event_payload("event-1", "2026-08-24T16:00:00Z", odds=2.0), "closing_snapshot_regression: event-1"),
            (_event_payload("event-1", "2026-08-24T17:00:00Z", odds=1.8), "closing_snapshot_conflict: event-1"),
        ]
        identity_changed = _event_payload("event-1", "2026-08-24T17:30:00Z", odds=1.8)
        identity_changed["closings"]["event-1"]["away_canonical"] = "changed-away"
        cases.append((identity_changed, "closing_identity_conflict: event-1"))

        before = path.read_bytes()
        for payload, reason in cases:
            try:
                store.commit(payload)
            except ValueError as exc:
                assert str(exc) == reason
            else:
                raise AssertionError(reason)
            assert path.read_bytes() == before


def test_closing_store_merge_serializes_stale_and_concurrent_writers_without_loss():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "epl_2026_27" / "closing.json"
        store = LeagueClosingStore(path)
        store.merge(_event_payload("event-1", "2026-08-24T17:30:00Z", odds=1.8))
        try:
            store.merge(_event_payload("event-1", "2026-08-24T17:00:00Z", odds=1.9))
        except ValueError as exc:
            assert str(exc) == "closing_snapshot_regression: event-1"
        else:
            raise AssertionError("a stale writer must not move the closing backward")

        barrier = threading.Barrier(3)
        failures: list[Exception] = []

        def writer(event_id: str) -> None:
            barrier.wait()
            try:
                LeagueClosingStore(path).merge(
                    _event_payload(event_id, "2026-08-24T17:15:00Z")
                )
            except Exception as exc:
                failures.append(exc)

        threads = [threading.Thread(target=writer, args=(event_id,)) for event_id in ("event-2", "event-3")]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        assert failures == []
        committed = json.loads(path.read_text())
        assert set(committed["closings"]) == {"event-1", "event-2", "event-3"}
