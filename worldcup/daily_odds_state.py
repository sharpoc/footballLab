from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class DailyOddsState:
    """Small atomic state file for committed daily odds request keys."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.keys: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if isinstance(value, dict) and isinstance(value.get("committed_keys"), list):
            self.keys = {str(item) for item in value["committed_keys"] if str(item).strip()}

    def __contains__(self, key: str) -> bool:
        return key in self.keys

    def commit(self, key: str) -> None:
        self.keys.add(str(key))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "namespace": "daily_odds_state",
            "committed_keys": sorted(self.keys),
        }
        fd, temporary = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def default_daily_odds_state_path(cache_dir: str | Path = "data/cache") -> Path:
    return Path(cache_dir) / "daily_odds" / "daily_odds_state.json"
