import hashlib
import json
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.league_lineup_store import LeagueLineupStore


COMPETITION = "epl_2026_27"


def _lineup_fingerprint(row):
    identity = {
        "competition_id": row["competition_id"],
        "event_id": row["event_id"],
        "source_match_id": row["source_match_id"],
        "kickoff_at_utc": row["kickoff_at_utc"],
        "home_player_ids": sorted(player["player_id"] for player in row["home_starting"]),
        "away_player_ids": sorted(player["player_id"] for player in row["away_starting"]),
    }
    payload = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _confirmed(event_id="epl-event-1", source_match_id="1001", fingerprint=None, home_first_id="1"):
    row = {
        "schema_version": 1,
        "provider": "fotmob",
        "competition_id": COMPETITION,
        "event_id": event_id,
        "source_match_id": source_match_id,
        "kickoff_at_utc": "2026-08-24T13:00:00+00:00",
        "fetched_at": "2026-08-24T12:20:00+00:00",
        "lineup_status": "confirmed",
        "home_canonical": "arsenal",
        "away_canonical": "chelsea",
        "home_formation": "4-3-3",
        "away_formation": "4-2-3-1",
        "home_starting": [{"player_id": home_first_id, "name": "Home 1"}] + [
            {"player_id": str(number), "name": f"Home {number}"} for number in range(2, 12)
        ],
        "away_starting": [{"player_id": str(number), "name": f"Away {number}"} for number in range(21, 32)],
    }
    row["lineup_fingerprint"] = _lineup_fingerprint(row) if fingerprint is None else fingerprint
    return row


def _report(*accepted):
    return {"accepted": list(accepted), "rejected": []}


def test_confirmed_report_is_written_to_its_competition_partition():
    """Writing an arbitrary cache path would mix independent league evidence."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)

        assert store.commit_confirmed(COMPETITION, _report(_confirmed())) == "stored"

        path = Path(tmp) / "data/cache/leagues/lineups/epl_2026_27.json"
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "schema_version": 1,
            "competition_id": COMPETITION,
            "accepted": [_confirmed()],
        }
        assert store.read_competition(COMPETITION) == json.loads(path.read_text(encoding="utf-8"))


def test_store_rejects_non_formal_competitions_and_unverified_confirmed_rows():
    """Allowing arbitrary partitions or malformed rows could persist unverifiable lineup evidence."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        try:
            store.commit_confirmed("fifa_world_cup_2026", _report(_confirmed()))
        except ValueError as exc:
            assert str(exc) == "league_lineup_competition_not_allowed"
        else:
            raise AssertionError("non-formal competition must fail")

        malformed = _confirmed()
        malformed["competition_id"] = "laliga_2026_27"
        try:
            store.commit_confirmed(COMPETITION, _report(malformed))
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_report"
        else:
            raise AssertionError("cross-competition confirmed row must fail")


def test_confirmed_cache_is_idempotent_non_degrading_and_rejects_a_conflict():
    """Replacing accepted evidence with a missing or conflicting lineup would falsify the audit trail."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        original = _confirmed()
        assert store.commit_confirmed(COMPETITION, _report(original)) == "stored"
        assert store.commit_confirmed(COMPETITION, _report(original)) == "unchanged"
        assert store.commit_confirmed(COMPETITION, _report()) == "unchanged"
        assert store.read_competition(COMPETITION)["accepted"] == [original]

        try:
            store.commit_confirmed(COMPETITION, _report(_confirmed(home_first_id="99")))
        except ValueError as exc:
            assert str(exc) == "league_lineup_fingerprint_conflict"
        else:
            raise AssertionError("conflicting event fingerprint must fail")
        assert store.read_competition(COMPETITION)["accepted"] == [original]


def test_store_rejects_sensitive_report_fields_and_malformed_committed_cache():
    """Persisting provider headers or trusting corrupt cache data would bypass the safe-output boundary."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        sensitive = _confirmed()
        sensitive["home_starting"][0]["raw_response"] = "must-not-persist"
        try:
            store.commit_confirmed(COMPETITION, _report(sensitive))
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_report"
        else:
            raise AssertionError("nested raw response field must fail")

        safe_row_report_with_secret_rejection = {
            "accepted": [_confirmed()],
            "rejected": [{"headers": {"authorization": "must-not-persist"}}],
        }
        try:
            store.commit_confirmed(COMPETITION, safe_row_report_with_secret_rejection)
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_report"
        else:
            raise AssertionError("sensitive fields in ignored rejection diagnostics must fail")

        path = Path(tmp) / "data/cache/leagues/lineups/epl_2026_27.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"schema_version":1,', encoding="utf-8")
        try:
            store.read_competition(COMPETITION)
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_cache"
        else:
            raise AssertionError("malformed committed cache must fail closed")


def test_failed_atomic_replace_keeps_the_prior_confirmed_cache_untouched():
    """A failed replacement must not destroy the last accepted lineup cache."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        original = _confirmed()
        assert store.commit_confirmed(COMPETITION, _report(original)) == "stored"
        next_event = _confirmed(event_id="epl-event-2", source_match_id="1002")
        with patch("worldcup.league_lineup_store.os.replace", side_effect=OSError("injected replace failure")):
            try:
                store.commit_confirmed(COMPETITION, _report(next_event))
            except OSError as exc:
                assert str(exc) == "injected replace failure"
            else:
                raise AssertionError("replace failure must surface")
        assert store.read_competition(COMPETITION)["accepted"] == [original]
        assert not list((Path(tmp) / "data/cache/leagues/lineups").glob("*.tmp"))


def test_fcntl_lock_serializes_a_competing_cache_commit():
    """Removing the exclusive lock would permit interleaved cache merge-and-replace writes."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        assert store.commit_confirmed(COMPETITION, _report(_confirmed())) == "stored"
        lock_path = Path(tmp) / "data/local/leagues/lineups/.lineup.lock"
        completed = threading.Event()
        result = []

        def commit() -> None:
            result.append(store.commit_confirmed(COMPETITION, _report(_confirmed(event_id="epl-event-2", source_match_id="1002"))))
            completed.set()

        with lock_path.open("a+", encoding="utf-8") as lock:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            thread = threading.Thread(target=commit)
            thread.start()
            time.sleep(0.05)
            assert completed.is_set() is False
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        thread.join(timeout=2)
        assert completed.is_set() is True
        assert result == ["stored"]


def test_state_requires_aware_timestamps_and_committed_confirmed_fingerprints():
    """Recording confirmation before cache durability would let restart state trigger unsupported refreshes."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        state = {
            "schema_version": 1,
            "events": {
                "epl_2026_27:epl-event-1": {
                    "last_polled_at": "2026-08-24T12:20:00+00:00",
                    "confirmed": True,
                    "accepted_fingerprint": _confirmed()["lineup_fingerprint"],
                },
            },
        }
        try:
            store.commit_state(state)
        except ValueError as exc:
            assert str(exc) == "league_lineup_state_cache_missing"
        else:
            raise AssertionError("state must follow confirmed cache")

        assert store.commit_confirmed(COMPETITION, _report(_confirmed())) == "stored"
        assert store.commit_state(state) == "stored"
        assert store.read_state() == state

        naive = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-1": {"last_polled_at": "2026-08-24T12:20:00", "confirmed": False},
        }}
        try:
            store.commit_state(naive)
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_state"
        else:
            raise AssertionError("naive persisted timestamp must fail")


def test_state_is_idempotent_and_fails_closed_for_malformed_committed_json():
    """Silently resetting a corrupt state file would re-enable duplicate provider polling after restart."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        state = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-1": {"last_polled_at": "2026-08-24T12:20:00+00:00", "confirmed": False},
        }}
        assert store.commit_state(state) == "stored"
        assert store.commit_state(state) == "unchanged"
        path = Path(tmp) / "data/local/leagues/lineup_state.json"
        path.write_text('{"schema_version":1,', encoding="utf-8")
        try:
            store.read_state()
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_state"
        else:
            raise AssertionError("malformed committed state must fail closed")


def test_state_commit_preserves_malformed_existing_state_instead_of_repairing_it():
    """Replacing corrupt state during a normal commit would erase the polling safety evidence."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        path = Path(tmp) / "data/local/leagues/lineup_state.json"
        path.parent.mkdir(parents=True)
        malformed = b'{"schema_version":1,'
        path.write_bytes(malformed)

        try:
            store.commit_state({"schema_version": 1, "events": {}})
        except ValueError as exc:
            assert str(exc) == "league_lineup_invalid_state"
        else:
            raise AssertionError("corrupt existing state must block commit")
        assert path.read_bytes() == malformed


def test_state_merge_only_preserves_confirmations_newer_polls_and_concurrent_events():
    """Overwriting a stale full-state snapshot would re-poll confirmed events or lose another writer's event."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        confirmed = _confirmed()
        assert store.commit_confirmed(COMPETITION, _report(confirmed)) == "stored"
        latest = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-1": {
                "last_polled_at": "2026-08-24T12:20:00+00:00",
                "confirmed": True,
                "accepted_fingerprint": confirmed["lineup_fingerprint"],
            },
        }}
        assert store.commit_state(latest) == "stored"

        stale = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-1": {
                "last_polled_at": "2026-08-24T12:00:00+00:00",
                "confirmed": False,
            },
        }}
        assert store.commit_state(stale) == "unchanged"
        assert store.read_state() == latest
        assert store.commit_state({"schema_version": 1, "events": {}}) == "unchanged"
        assert store.read_state() == latest

        first = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-2": {"last_polled_at": "2026-08-24T12:21:00+00:00", "confirmed": False},
        }}
        second = {"schema_version": 1, "events": {
            "epl_2026_27:epl-event-3": {"last_polled_at": "2026-08-24T12:22:00+00:00", "confirmed": False},
        }}
        threads = [threading.Thread(target=store.commit_state, args=(value,)) for value in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            assert thread.is_alive() is False
        assert set(store.read_state()["events"]) == {
            "epl_2026_27:epl-event-1", "epl_2026_27:epl-event-2", "epl_2026_27:epl-event-3",
        }


def test_cache_rejects_post_kickoff_and_forged_fingerprint_evidence():
    """A shape-valid arbitrary hash or post-kickoff capture is not valid pre-match confirmation evidence."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        post_kickoff = _confirmed()
        post_kickoff["fetched_at"] = "2026-08-24T13:00:00+00:00"
        post_kickoff["lineup_fingerprint"] = _lineup_fingerprint(post_kickoff)
        forged = _confirmed(fingerprint="f" * 64)
        for candidate in (post_kickoff, forged):
            try:
                store.commit_confirmed(COMPETITION, _report(candidate))
            except ValueError as exc:
                assert str(exc) == "league_lineup_invalid_report"
            else:
                raise AssertionError("unbound confirmed evidence must fail")


def test_cache_keeps_a_one_to_one_source_match_binding_across_commits():
    """Binding one FotMob match to two local events would defeat the parser's unique identity join."""
    with TemporaryDirectory() as tmp:
        store = LeagueLineupStore(tmp)
        assert store.commit_confirmed(COMPETITION, _report(_confirmed())) == "stored"
        duplicate_source = _confirmed(event_id="epl-event-2", source_match_id="1001")
        try:
            store.commit_confirmed(COMPETITION, _report(duplicate_source))
        except ValueError as exc:
            assert str(exc) == "league_lineup_source_match_conflict"
        else:
            raise AssertionError("provider match must have one local event")
