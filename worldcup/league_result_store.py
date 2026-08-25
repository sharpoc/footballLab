from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


def _utc(value: Any, *, error: str) -> str:
    if not isinstance(value, str):
        raise ValueError(error)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(error) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc).isoformat()


def _text(value: Any, *, error: str) -> str:
    if not isinstance(value, str) or not (cleaned := value.strip()):
        raise ValueError(error)
    return cleaned


def _score(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("league_result_store_score_invalid")
    return value


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row(value: Mapping[str, Any], competition_id: str) -> dict[str, Any]:
    if value.get("competition_id") != competition_id:
        raise ValueError("league_result_store_result_competition_mismatch")
    row = {
        "competition_id": competition_id,
        "source_event_id": _text(value.get("source_event_id"), error="league_result_store_event_id_invalid"),
        "kickoff_at_utc": _utc(value.get("kickoff_at_utc"), error="league_result_store_kickoff_invalid"),
        "home_team": _text(value.get("home_team"), error="league_result_store_team_invalid"),
        "away_team": _text(value.get("away_team"), error="league_result_store_team_invalid"),
        "home_canonical": _text(value.get("home_canonical"), error="league_result_store_identity_invalid"),
        "away_canonical": _text(value.get("away_canonical"), error="league_result_store_identity_invalid"),
        "home_score": _score(value.get("home_score")),
        "away_score": _score(value.get("away_score")),
        "captured_at": _utc(value.get("captured_at"), error="league_result_store_captured_at_invalid"),
        "result_scope": _text(value.get("result_scope"), error="league_result_store_scope_invalid"),
        "source_fingerprint": _text(value.get("source_fingerprint"), error="league_result_store_fingerprint_invalid"),
    }
    if row["result_scope"] != "football_90min":
        raise ValueError("league_result_store_scope_invalid")
    return row


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(row["kickoff_at_utc"]), str(row["home_canonical"]), str(row["away_canonical"]))


def _receipt(competition_id: str, rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    results = [dict(rows[event_id]) for event_id in sorted(rows)]
    core = {"schema_version": 1, "competition_id": competition_id, "results": results}
    return {**core, "fingerprint": _fingerprint(core)}


def _read(path: Path, competition_id: str) -> dict[str, Any]:
    if not path.exists():
        return _receipt(competition_id, {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("league_result_store_unreadable") from None
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1 or payload.get("competition_id") != competition_id:
        raise ValueError("league_result_store_invalid")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("league_result_store_invalid")
    rows: dict[str, dict[str, Any]] = {}
    for value in results:
        if not isinstance(value, Mapping):
            raise ValueError("league_result_store_invalid")
        row = _row(value, competition_id)
        if row["source_event_id"] in rows:
            raise ValueError("league_result_store_invalid")
        rows[row["source_event_id"]] = row
    checked = _receipt(competition_id, rows)
    if payload.get("fingerprint") != checked["fingerprint"]:
        raise ValueError("league_result_store_invalid")
    return checked


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_conflicts(conflicts: Mapping[str, str]) -> list[dict[str, str]]:
    return [
        {"source_event_id": event_id, "reason": conflicts[event_id]}
        for event_id in sorted(conflicts)
    ]


class LeagueResultStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def merge(self, payload: dict[str, Any]) -> dict[str, Any]:
        competition_id = payload.get("competition_id")
        if not isinstance(competition_id, str) or competition_id not in FORMAL_SINGLE_MATCH_IDS:
            raise ValueError("league_result_store_competition_not_allowed")
        if self.path.name != "results.json" or self.path.parent.name != competition_id:
            raise ValueError("league_result_store_partition_mismatch")
        values = payload.get("results")
        if not isinstance(values, list):
            raise ValueError("league_result_store_results_invalid")

        incoming: dict[str, dict[str, Any]] = {}
        conflicts: dict[str, str] = {}
        for value in values:
            if not isinstance(value, Mapping):
                raise ValueError("league_result_store_results_invalid")
            row = _row(value, competition_id)
            event_id = row["source_event_id"]
            if event_id in incoming:
                conflicts[event_id] = "duplicate_source_event"
            else:
                incoming[event_id] = row

        pending = payload.get("pending")
        if pending is not None and not isinstance(pending, list):
            raise ValueError("league_result_store_pending_invalid")
        regressions = {
            str(value.get("source_event_id"))
            for value in (pending or [])
            if isinstance(value, Mapping)
            and isinstance(value.get("source_event_id"), (str, int))
            and not isinstance(value.get("source_event_id"), bool)
            and value.get("reason") == "result_not_finished"
        }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = _read(self.path, competition_id)
            committed = {row["source_event_id"]: row for row in current["results"]}
            for event_id, row in incoming.items():
                previous = committed.get(event_id)
                if previous is None:
                    continue
                if _identity(previous) != _identity(row):
                    conflicts[event_id] = "identity_changed"
                elif (previous["home_score"], previous["away_score"]) != (row["home_score"], row["away_score"]):
                    conflicts[event_id] = "score_changed"
            for event_id in regressions & set(committed):
                conflicts[event_id] = "finished_regression"
            if conflicts:
                return {
                    "status": "conflict",
                    "added": 0,
                    "unchanged": sum(event_id in committed for event_id in incoming),
                    "conflicts": _safe_conflicts(conflicts),
                    "fingerprint": current["fingerprint"],
                }

            additions = {event_id: row for event_id, row in incoming.items() if event_id not in committed}
            if not additions:
                return {
                    "status": "unchanged",
                    "added": 0,
                    "unchanged": len(incoming),
                    "conflicts": [],
                    "fingerprint": current["fingerprint"],
                }
            updated = _receipt(competition_id, {**committed, **additions})
            _atomic_write(self.path, updated)
            return {
                "status": "stored",
                "added": len(additions),
                "unchanged": len(incoming) - len(additions),
                "conflicts": [],
                "fingerprint": updated["fingerprint"],
            }
