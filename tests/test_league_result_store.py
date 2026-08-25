from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.league_result_store import LeagueResultStore


COMPETITION = "epl_2026_27"
EVENT_ID = "1001"


def _result(
    home_score: int = 2,
    away_score: int = 1,
    *,
    event_id: str = EVENT_ID,
    away_canonical: str = "chelsea",
) -> dict:
    source_fingerprint = hashlib.sha256(
        f"{event_id}:{home_score}:{away_score}:{away_canonical}".encode("utf-8")
    ).hexdigest()
    return {
        "competition_id": COMPETITION,
        "source_event_id": event_id,
        "kickoff_at_utc": "2026-08-28T19:00:00+00:00",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "home_canonical": "arsenal",
        "away_canonical": away_canonical,
        "home_score": home_score,
        "away_score": away_score,
        "captured_at": "2026-08-29T00:00:00+00:00",
        "result_scope": "football_90min",
        "source_fingerprint": source_fingerprint,
    }


def _results(*rows: dict) -> dict:
    return {
        "competition_id": COMPETITION,
        "results": list(rows or (_result(),)),
        "pending": [],
        "source_events": [
            {"source_event_id": row["source_event_id"], "outcome": "accepted"}
            for row in (rows or (_result(),))
        ],
    }


def _read_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def test_same_score_is_idempotent_and_changed_score_is_conflict():
    """Replacing an accepted score with a provider revision must never mutate the receipt."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")
        assert store.merge(_results(_result(2, 1)))["added"] == 1
        assert store.merge(_results(_result(2, 1)))["unchanged"] == 1

        changed = store.merge(_results(_result(3, 1)))

        assert changed["status"] == "conflict"
        assert (changed["added"], changed["unchanged"]) == (0, 0)
        assert changed["conflicts"] == [{"source_event_id": EVENT_ID, "reason": "score_changed"}]
        assert [(row["home_score"], row["away_score"]) for row in _read_rows(store.path)] == [(2, 1)]


def test_merge_preserves_accepted_rows_when_new_payload_omits_them():
    """Treating a partial provider response as a replacement would delete formal evidence."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")
        first = _result(event_id="1001")
        second = _result(event_id="1002")
        assert store.merge(_results(first, second))["added"] == 2

        result = store.merge(_results(first))

        assert result["status"] == "unchanged"
        assert {row["source_event_id"] for row in _read_rows(store.path)} == {"1001", "1002"}


def test_finished_regression_and_identity_change_are_conflicts_without_provider_content():
    """A terminal regression or canonical fixture change must be isolated without echoing raw data."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")
        assert store.merge(_results(_result()))["added"] == 1

        regression = store.merge({
            "competition_id": COMPETITION,
            "results": [],
            "pending": [{"source_event_id": EVENT_ID, "reason": "result_not_finished"}],
            "source_events": [{"source_event_id": EVENT_ID, "outcome": "pending", "reason": "result_not_finished"}],
        })
        identity = store.merge(_results(_result(away_canonical="different_fc")))

        assert regression["conflicts"] == [{"source_event_id": EVENT_ID, "reason": "finished_regression"}]
        assert identity["conflicts"] == [{"source_event_id": EVENT_ID, "reason": "identity_changed"}]
        assert (regression["added"], regression["unchanged"]) == (0, 0)
        assert (identity["added"], identity["unchanged"]) == (0, 0)
        assert all(set(conflict) == {"source_event_id", "reason"} for conflict in regression["conflicts"] + identity["conflicts"])
        assert _read_rows(store.path)[0]["away_canonical"] == "chelsea"


def test_duplicate_event_ids_fail_closed_without_partial_commit():
    """Accepting one duplicate based on list order would make a formal receipt nondeterministic."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")
        duplicate = _results(_result(), deepcopy(_result()))

        result = store.merge(duplicate)

        assert result["status"] == "conflict"
        assert result["added"] == 0
        assert result["conflicts"] == [{"source_event_id": EVENT_ID, "reason": "duplicate_source_event"}]
        assert not store.path.exists()


def test_parser_duplicate_pending_signal_is_an_explicit_safe_conflict():
    """Ignoring the parser's one-row duplicate signal would let ambiguous provider evidence look unchanged."""
    parser_output = {
        "competition_id": COMPETITION,
        "results": [],
        "pending": [{"source_event_id": EVENT_ID, "reason": "duplicate_source_event"}],
        "source_events": [{"source_event_id": EVENT_ID, "outcome": "pending", "reason": "duplicate_source_event"}],
        "source_fingerprint": "safe-parser-contract-fingerprint",
    }
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")

        result = store.merge(parser_output)

        assert result["status"] == "conflict"
        assert (result["added"], result["unchanged"]) == (0, 0)
        assert result["conflicts"] == [{"source_event_id": EVENT_ID, "reason": "duplicate_source_event"}]
        assert not store.path.exists()


def test_cross_partition_path_is_rejected_before_writing():
    """A result payload must not escape the directory named for its competition."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / "laliga_2026_27" / "results.json")

        try:
            store.merge(_results(_result()))
        except ValueError as exc:
            assert str(exc) == "league_result_store_partition_mismatch"
        else:
            raise AssertionError("cross-partition result path must fail")
        assert not store.path.exists()


def test_replace_failure_leaves_previous_receipt_intact():
    """A failed atomic replacement must not report or persist a newly accepted result."""
    with TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / COMPETITION / "results.json")
        assert store.merge(_results(_result()))["added"] == 1
        before = store.path.read_bytes()

        with patch("worldcup.league_result_store.os.replace", side_effect=OSError("disk full")):
            try:
                store.merge(_results(_result(event_id="1002")))
            except OSError as exc:
                assert str(exc) == "disk full"
            else:
                raise AssertionError("atomic replacement failure must propagate")

        assert store.path.read_bytes() == before
        assert not list(store.path.parent.glob(".results.json.*"))


def test_concurrent_writers_merge_distinct_events_without_lost_updates():
    """Reading before a process-wide lock would allow concurrent receipts to overwrite each other."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / COMPETITION / "results.json"
        payloads = [_results(_result(event_id="1001")), _results(_result(event_id="1002"))]

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda payload: LeagueResultStore(path).merge(payload), payloads))

        assert sorted(outcome["added"] for outcome in outcomes) == [1, 1]
        assert {row["source_event_id"] for row in _read_rows(path)} == {"1001", "1002"}
