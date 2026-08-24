from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("closing_timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _valid_decision(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema_version") == 2
        and value.get("label") in {"MATCH_PICK", "NO_CLEAN_MARKET"}
    )


def select_league_closings(
    snapshots: Iterable[dict[str, Any]],
    competition_id: str,
) -> dict[str, Any]:
    identities: dict[str, tuple[str, str, str]] = {}
    selected: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for snapshot in snapshots:
        competition = snapshot.get("competition") or {}
        if competition.get("id") != competition_id:
            raise ValueError("closing_competition_mismatch")
        snapshot_at = _utc(snapshot.get("snapshot_at"))
        for match in snapshot.get("matches") or []:
            event_id = str(match.get("source_event_id") or "").strip()
            home = str(match.get("home_canonical") or "").strip()
            away = str(match.get("away_canonical") or "").strip()
            kickoff = _utc(match.get("kickoff_at_utc"))
            if not event_id or not home or not away:
                raise ValueError("closing_identity_missing")
            identity = (kickoff.isoformat(), home, away)
            previous_identity = identities.setdefault(event_id, identity)
            if previous_identity != identity:
                raise ValueError(f"closing_identity_conflict: {event_id}")
            if snapshot_at >= kickoff or not _valid_decision(match.get("match_decision")):
                continue
            previous = selected.get(event_id)
            if previous is None or snapshot_at > previous[0]:
                selected[event_id] = (
                    snapshot_at,
                    {
                        "competition_id": competition_id,
                        "source_event_id": event_id,
                        "kickoff_at_utc": kickoff.isoformat(),
                        "home_team": match.get("home_team"),
                        "away_team": match.get("away_team"),
                        "home_canonical": home,
                        "away_canonical": away,
                        "closing_snapshot_at": snapshot_at.isoformat(),
                        "closing_match_decision": match["match_decision"],
                    },
                )
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "closings": {event_id: row for event_id, (_, row) in sorted(selected.items())},
    }


class LeagueClosingStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def commit(self, payload: dict[str, Any]) -> str:
        competition_id = str(payload.get("competition_id") or "")
        if not competition_id or self.path.parent.name != competition_id:
            raise ValueError("closing_store_partition_mismatch")
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if self.path.exists() and self.path.read_bytes() == encoded:
                return "unchanged"
            fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return "stored"
