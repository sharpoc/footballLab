from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class LeagueLiveStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def commit_snapshot(self, competition_id: str, snapshot: Mapping[str, Any]) -> str:
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            raise ValueError("league_live_competition_not_allowed")
        competition = snapshot.get("competition") if isinstance(snapshot.get("competition"), Mapping) else {}
        if competition.get("id") != competition_id:
            raise ValueError("league_live_snapshot_competition_mismatch")
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not snapshot_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in snapshot_id):
            raise ValueError("league_live_snapshot_id_invalid")
        payload = json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        current = self.root / "data/cache/leagues" / competition_id / "snapshot.json"
        history = self.root / "data/local/leagues" / competition_id / "history" / f"{snapshot_id}.json"
        lock_path = self.root / "data/local/leagues" / competition_id / ".snapshot.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            if current.exists() and history.exists():
                if current.read_text(encoding="utf-8") == payload and history.read_text(encoding="utf-8") == payload:
                    return "unchanged"
            if history.exists() and history.read_text(encoding="utf-8") != payload:
                raise ValueError("league_live_history_conflict")
            _atomic_write(history, payload)
            _atomic_write(current, payload)
        return "stored"
