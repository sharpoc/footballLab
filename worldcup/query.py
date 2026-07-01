from __future__ import annotations

from pathlib import Path
from typing import Any

from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore

GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
FINISHED_MIN_SAMPLE = 20


def load_latest_snapshot(
    db_path: str | Path = "data/local/worldcup.db",
    store: SnapshotStore | None = None,
) -> dict[str, Any] | None:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    latest = snapshot_store.latest_snapshot()
    if latest is None:
        return None
    return latest["snapshot"]


def load_recent_snapshots(
    db_path: str | Path = "data/local/worldcup.db",
    store: SnapshotStore | None = None,
    limit: int = 2,
) -> list[dict[str, Any]]:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    if hasattr(snapshot_store, "list_recent_snapshots"):
        records = snapshot_store.list_recent_snapshots(limit=limit)
    else:
        latest = snapshot_store.latest_snapshot()
        records = [latest] if latest is not None else []
    return [record["snapshot"] for record in records if record is not None]


def _top_grade(signals: list[dict[str, Any]]) -> str:
    grades = [signal.get("grade", "") for signal in signals]
    known = [grade for grade in grades if grade in GRADE_ORDER]
    if not known:
        return ""
    return max(known, key=lambda grade: GRADE_ORDER[grade])


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _score_label(result: dict[str, Any]) -> str:
    home = result.get("home_score")
    away = result.get("away_score")
    if home is None or away is None:
        return ""
    return f"{home} - {away}"


def _finished_signal_trend(record: dict[str, Any], signal: dict[str, Any]) -> list:
    market_map = {
        "1X2_90min": "1x2",
        "OverUnder_90min": "ou_2_5",
        "AsianHandicap_90min": "ah_main",
    }
    market_key = market_map.get(str(signal.get("market_type") or ""))
    if not market_key:
        return []
    selection = str(signal.get("selection") or "")
    if market_key == "ah_main":
        selection = selection.split("_", 1)[0]
    return (((record.get("odds_trend") or {}).get(market_key) or {}).get(selection)) or []


def summarize_finished_block(
    snapshot: dict[str, Any],
    min_sample: int = FINISHED_MIN_SAMPLE,
) -> dict[str, Any]:
    finished = snapshot.get("finished") or {}
    records = finished.get("matches") or []
    tally = finished.get("tally") or {}
    signal_count = sum(len(record.get("closing_signals") or []) for record in records)
    skipped = _as_int(finished.get("skipped_no_closing"))
    decided = 0
    for grade in ("S", "A"):
        entry = tally.get(grade) or {}
        decided += _as_int(entry.get("hit")) + _as_int(entry.get("miss"))
    total_results = len(records) + skipped
    return {
        "match_count": len(records),
        "signal_count": signal_count,
        "skipped_no_closing": skipped,
        "tally": tally,
        "coverage": {
            "finished_result_count": total_results,
            "closing_available_count": len(records),
            "missing_closing_count": skipped,
            "closing_coverage_rate": (len(records) / total_results if total_results else None),
        },
        "sample": {
            "min_sample": min_sample,
            "decided_strong_signal_count": decided,
            "sample_too_small": decided < min_sample,
        },
    }


def project_finished_rows(snapshot: dict[str, Any]) -> dict[str, Any]:
    finished = snapshot.get("finished") or {}
    records = finished.get("matches") or []
    matches: list[dict[str, Any]] = []
    for record in records:
        result = record.get("result") or {}
        signals = []
        for signal in record.get("closing_signals") or []:
            prediction = signal.get("prediction") or {}
            signals.append(
                {
                    "market_type": signal.get("market_type"),
                    "selection": signal.get("selection"),
                    "line": signal.get("line"),
                    "grade": signal.get("grade"),
                    "odds": signal.get("odds"),
                    "outcome": prediction.get("label") or "",
                    "prediction_status": prediction.get("status") or "",
                    "detail": prediction.get("detail") or "",
                    "trend_points": _finished_signal_trend(record, signal),
                }
            )
        home = record.get("home_team", "")
        away = record.get("away_team", "")
        matches.append(
            {
                "kickoff_at_utc": record.get("kickoff_at_utc", ""),
                "stage": record.get("stage", ""),
                "group": record.get("group", ""),
                "home_team": home,
                "away_team": away,
                "match_label": f"{home} vs {away}".strip(),
                "score": {
                    "home": result.get("home_score"),
                    "away": result.get("away_score"),
                },
                "score_label": _score_label(result),
                "closing_snapshot_at": record.get("closing_snapshot_at"),
                "signal_count": len(signals),
                "top_grade": _top_grade(signals),
                "signals": signals,
            }
        )
        closing_match_decision = record.get("closing_match_decision")
        if isinstance(closing_match_decision, dict):
            matches[-1]["closing_match_decision"] = dict(closing_match_decision)
    return {
        "schema_version": 1,
        "snapshot_at": snapshot.get("snapshot_at"),
        "summary": summarize_finished_block(snapshot),
        "matches": matches,
    }


def project_match_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    data_quality = snapshot.get("data_quality") or {}
    stale = bool(data_quality.get("stale_sources"))
    rows: list[dict[str, Any]] = []
    for match in snapshot.get("matches", []):
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        signals = match.get("signals") or []
        refresh_plan = match.get("refresh_plan") or {}
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
                "next_update_at": refresh_plan.get("next_update_at"),
                "next_update_label": refresh_plan.get("label"),
                "next_update_description": refresh_plan.get("description"),
                "stale": stale,
            }
        )
        match_decision = match.get("match_decision")
        if isinstance(match_decision, dict):
            rows[-1]["match_decision"] = dict(match_decision)
    return rows
