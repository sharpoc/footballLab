from __future__ import annotations

from pathlib import Path
from typing import Any

from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore

GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def load_latest_snapshot(
    db_path: str | Path = "data/local/worldcup.db",
    store: SnapshotStore | None = None,
) -> dict[str, Any] | None:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    latest = snapshot_store.latest_snapshot()
    if latest is None:
        return None
    return latest["snapshot"]


def _top_grade(signals: list[dict[str, Any]]) -> str:
    grades = [signal.get("grade", "") for signal in signals]
    known = [grade for grade in grades if grade in GRADE_ORDER]
    if not known:
        return ""
    return max(known, key=lambda grade: GRADE_ORDER[grade])


def project_match_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    data_quality = snapshot.get("data_quality") or {}
    stale = bool(data_quality.get("stale_sources"))
    rows: list[dict[str, Any]] = []
    for match in snapshot.get("matches", []):
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        signals = match.get("signals") or []
        rows.append(
            {
                "kickoff_at_utc": match.get("kickoff_at_utc", ""),
                "stage": match.get("stage", ""),
                "group": match.get("group", ""),
                "home_team": home,
                "away_team": away,
                "match_label": f"{home} vs {away}".strip(),
                "signal_count": len(signals),
                "top_grade": _top_grade(signals),
                "stale": stale,
            }
        )
    return rows
