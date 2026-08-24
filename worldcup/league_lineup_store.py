from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


_FINGERPRINT_CHARS = frozenset("0123456789abcdef")
_FORBIDDEN_FIELD_PARTS = ("raw", "header", "secret", "authorization", "cookie", "api_key", "token")


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LeagueLineupStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _competition_path(self, competition_id: str) -> Path:
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            raise ValueError("league_lineup_competition_not_allowed")
        return self.root / "data/cache/leagues/lineups" / f"{competition_id}.json"

    @staticmethod
    def _cache_payload(competition_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(report, Mapping) or set(report) != {"accepted", "rejected"}:
            raise ValueError("league_lineup_invalid_report")
        _reject_forbidden_fields(report)
        accepted = report.get("accepted")
        rejected = report.get("rejected")
        if not isinstance(accepted, list) or not isinstance(rejected, list):
            raise ValueError("league_lineup_invalid_report")
        checked = [_validate_confirmed_row(competition_id, row) for row in accepted]
        return {
            "schema_version": 1,
            "competition_id": competition_id,
            "accepted": checked,
        }

    def read_competition(self, competition_id: str) -> dict[str, Any] | None:
        path = self._competition_path(competition_id)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("league_lineup_invalid_cache") from exc
        try:
            value = _validate_cache(competition_id, value)
        except ValueError as exc:
            raise ValueError("league_lineup_invalid_cache") from exc
        if value.get("competition_id") != competition_id:
            raise ValueError("league_lineup_invalid_cache")
        return value

    def commit_confirmed(self, competition_id: str, report: Mapping[str, Any]) -> str:
        path = self._competition_path(competition_id)
        payload_value = self._cache_payload(competition_id, report)
        payload = json.dumps(payload_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        lock_path = self.root / "data/local/leagues/lineups/.lineup.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self.read_competition(competition_id)
            merged = _merge_confirmed(current, payload_value)
            payload = json.dumps(merged, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            if path.exists() and path.read_text(encoding="utf-8") == payload:
                return "unchanged"
            _atomic_write(path, payload)
        return "stored"

    def read_state(self) -> dict[str, Any]:
        path = self.root / "data/local/leagues/lineup_state.json"
        if not path.exists():
            return {"schema_version": 1, "events": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("league_lineup_invalid_state") from exc
        return _validate_state(value)

    def commit_state(self, state: Mapping[str, Any]) -> str:
        checked = _validate_state(state)
        for key, row in checked["events"].items():
            if row["confirmed"]:
                competition_id, event_id = key.split(":", 1)
                cache = self.read_competition(competition_id)
                matching = [] if cache is None else [
                    candidate for candidate in cache["accepted"]
                    if candidate["event_id"] == event_id
                    and candidate["lineup_fingerprint"] == row["accepted_fingerprint"]
                ]
                if not matching:
                    raise ValueError("league_lineup_state_cache_missing")
        path = self.root / "data/local/leagues/lineup_state.json"
        payload = json.dumps(checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        lock_path = path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if path.exists() and path.read_text(encoding="utf-8") == payload:
                return "unchanged"
            _atomic_write(path, payload)
        return "stored"


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or any(part in key.casefold() for part in _FORBIDDEN_FIELD_PARTS):
                raise ValueError("league_lineup_invalid_report")
            _reject_forbidden_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_fields(item)


def _required_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("league_lineup_invalid_report")
    return value


def _aware_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("league_lineup_invalid_report")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("league_lineup_invalid_report") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("league_lineup_invalid_report")
    return value


def _validate_players(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list) or len(value) != 11:
        raise ValueError("league_lineup_invalid_report")
    players: list[dict[str, str | None]] = []
    for player in value:
        if not isinstance(player, Mapping) or set(player) != {"player_id", "name"}:
            raise ValueError("league_lineup_invalid_report")
        player_id = _required_text(player.get("player_id"))
        name = player.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("league_lineup_invalid_report")
        players.append({"player_id": player_id, "name": name})
    if len({player["player_id"] for player in players}) != 11:
        raise ValueError("league_lineup_invalid_report")
    return players


def _validate_confirmed_row(competition_id: str, row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError("league_lineup_invalid_report")
    expected = {
        "schema_version", "provider", "competition_id", "event_id", "source_match_id", "kickoff_at_utc",
        "fetched_at", "lineup_status", "home_canonical", "away_canonical", "home_formation", "away_formation",
        "home_starting", "away_starting", "lineup_fingerprint",
    }
    if set(row) != expected or row.get("schema_version") != 1 or row.get("provider") != "fotmob":
        raise ValueError("league_lineup_invalid_report")
    if row.get("competition_id") != competition_id or row.get("lineup_status") != "confirmed":
        raise ValueError("league_lineup_invalid_report")
    checked = dict(row)
    for name in ("event_id", "source_match_id", "home_canonical", "away_canonical"):
        checked[name] = _required_text(row.get(name))
    for name in ("kickoff_at_utc", "fetched_at"):
        checked[name] = _aware_timestamp(row.get(name))
    for name in ("home_formation", "away_formation"):
        if row.get(name) is not None and not isinstance(row.get(name), str):
            raise ValueError("league_lineup_invalid_report")
    checked["home_starting"] = _validate_players(row.get("home_starting"))
    checked["away_starting"] = _validate_players(row.get("away_starting"))
    if {player["player_id"] for player in checked["home_starting"]} & {
        player["player_id"] for player in checked["away_starting"]
    }:
        raise ValueError("league_lineup_invalid_report")
    fingerprint = row.get("lineup_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or not set(fingerprint).issubset(_FINGERPRINT_CHARS):
        raise ValueError("league_lineup_invalid_report")
    return checked


def _validate_cache(competition_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "competition_id", "accepted"}:
        raise ValueError("league_lineup_invalid_cache")
    if value.get("schema_version") != 1 or value.get("competition_id") != competition_id:
        raise ValueError("league_lineup_invalid_cache")
    accepted = value.get("accepted")
    if not isinstance(accepted, list):
        raise ValueError("league_lineup_invalid_cache")
    try:
        _reject_forbidden_fields(value)
        rows = [_validate_confirmed_row(competition_id, row) for row in accepted]
    except ValueError as exc:
        raise ValueError("league_lineup_invalid_cache") from exc
    if len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("league_lineup_invalid_cache")
    return {"schema_version": 1, "competition_id": competition_id, "accepted": rows}


def _merge_confirmed(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    current_rows = [] if current is None else current["accepted"]
    by_event = {row["event_id"]: row for row in current_rows}
    for row in incoming["accepted"]:
        previous = by_event.get(row["event_id"])
        if previous is not None and previous["lineup_fingerprint"] != row["lineup_fingerprint"]:
            raise ValueError("league_lineup_fingerprint_conflict")
        by_event.setdefault(row["event_id"], row)
    return {
        "schema_version": 1,
        "competition_id": incoming["competition_id"],
        "accepted": [by_event[event_id] for event_id in sorted(by_event)],
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "events"}:
        raise ValueError("league_lineup_invalid_state")
    if value.get("schema_version") != 1 or not isinstance(value.get("events"), Mapping):
        raise ValueError("league_lineup_invalid_state")
    try:
        _reject_forbidden_fields(value)
        events: dict[str, dict[str, Any]] = {}
        for key, row in value["events"].items():
            if not isinstance(key, str) or ":" not in key:
                raise ValueError("league_lineup_invalid_state")
            competition_id, event_id = key.split(":", 1)
            if competition_id not in FORMAL_SINGLE_MATCH_IDS or not event_id:
                raise ValueError("league_lineup_invalid_state")
            if not isinstance(row, Mapping) or not isinstance(row.get("confirmed"), bool):
                raise ValueError("league_lineup_invalid_state")
            expected = {"last_polled_at", "confirmed"}
            if row["confirmed"]:
                expected.add("accepted_fingerprint")
            if set(row) != expected:
                raise ValueError("league_lineup_invalid_state")
            checked = {
                "last_polled_at": _aware_timestamp(row.get("last_polled_at")),
                "confirmed": row["confirmed"],
            }
            if row["confirmed"]:
                fingerprint = row.get("accepted_fingerprint")
                if (
                    not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or not set(fingerprint).issubset(_FINGERPRINT_CHARS)
                ):
                    raise ValueError("league_lineup_invalid_state")
                checked["accepted_fingerprint"] = fingerprint
            events[key] = checked
    except ValueError as exc:
        raise ValueError("league_lineup_invalid_state") from exc
    return {"schema_version": 1, "events": {key: events[key] for key in sorted(events)}}
