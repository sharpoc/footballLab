from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore

GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
FINISHED_MIN_SAMPLE = 20
DEFAULT_COMPETITION_ID = "fifa_world_cup_2026"
DEFAULT_COMPETITION_LABEL = "2026 世界杯"
SNAPSHOT_VIEW_SCAN_LIMIT = 500


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


def _competition_from_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    competition = value.get("competition")
    if not isinstance(competition, dict):
        return None
    competition_id = str(competition.get("id") or "").strip()
    if not competition_id:
        return None
    return dict(competition)


def _competition_block_for_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    competition = _competition_from_mapping(snapshot)
    if competition is not None:
        return competition
    for match in snapshot.get("matches") or []:
        competition = _competition_from_mapping(match)
        if competition is not None:
            return competition
    for match in (snapshot.get("finished") or {}).get("matches") or []:
        competition = _competition_from_mapping(match)
        if competition is not None:
            return competition
    return {"id": DEFAULT_COMPETITION_ID, "name": DEFAULT_COMPETITION_LABEL}


def _competition_id_for_snapshot(snapshot: dict[str, Any]) -> str:
    return str(_competition_block_for_snapshot(snapshot).get("id") or DEFAULT_COMPETITION_ID)


def _with_competition(value: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(value)
    if not isinstance(item.get("competition"), dict):
        item["competition"] = dict(competition)
    return item


def _sum_counts(snapshots: list[dict[str, Any]], matches: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {}
    for snapshot in snapshots:
        for key, value in (snapshot.get("counts") or {}).items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                counts[key] = counts.get(key, 0) + value
    counts["matches"] = len(matches)
    return counts


def _merge_data_quality(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for snapshot in snapshots:
        for key, value in (snapshot.get("data_quality") or {}).items():
            if isinstance(value, list):
                target = merged.setdefault(key, [])
                for item in value:
                    if item not in target:
                        target.append(deepcopy(item))
            elif value and key not in merged:
                merged[key] = deepcopy(value)
    return merged


def _merge_tally(tallies: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, int]] = {}
    for tally in tallies:
        for grade, entry in tally.items():
            if not isinstance(entry, dict):
                continue
            target = merged.setdefault(str(grade), {})
            for status, value in entry.items():
                target[str(status)] = target.get(str(status), 0) + _as_int(value)
    return merged


def _merge_finished_blocks(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    tallies: list[dict[str, Any]] = []
    skipped = 0
    match_level_tallies: list[dict[str, Any]] = []
    for snapshot in snapshots:
        competition = _competition_block_for_snapshot(snapshot)
        finished = snapshot.get("finished") or {}
        for match in finished.get("matches") or []:
            matches.append(_with_competition(match, competition))
        if isinstance(finished.get("tally"), dict):
            tallies.append(finished["tally"])
        if isinstance(finished.get("match_level_tally"), dict):
            match_level_tallies.append(finished["match_level_tally"])
        skipped += _as_int(finished.get("skipped_no_closing"))
    if not matches and not tallies and skipped == 0:
        return {}
    finished: dict[str, Any] = {
        "matches": matches,
        "tally": _merge_tally(tallies),
        "skipped_no_closing": skipped,
    }
    if match_level_tallies:
        finished["match_level_tally"] = _merge_tally(match_level_tallies)
    return finished


def _merge_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not snapshots:
        return None
    if len(snapshots) == 1:
        return deepcopy(snapshots[0])

    competitions = [_competition_block_for_snapshot(snapshot) for snapshot in snapshots]
    matches: list[dict[str, Any]] = []
    for snapshot, competition in zip(snapshots, competitions):
        for match in snapshot.get("matches") or []:
            matches.append(_with_competition(match, competition))

    snapshot_at_values = [str(snapshot.get("snapshot_at") or "") for snapshot in snapshots]
    merged: dict[str, Any] = {
        "snapshot_at": max(snapshot_at_values) if snapshot_at_values else None,
        "run": {
            "run_id": "multi_competition_latest",
            "competitions": [competition.get("id") for competition in competitions],
            "snapshot_count": len(snapshots),
        },
        "competition": {
            "id": "multi_competition",
            "name": "全部赛事",
        },
        "counts": _sum_counts(snapshots, matches),
        "data_quality": _merge_data_quality(snapshots),
        "matches": matches,
    }
    finished = _merge_finished_blocks(snapshots)
    if finished:
        merged["finished"] = finished
    return merged


def _snapshot_records(
    db_path: str | Path,
    store: SnapshotStore | None,
    scan_limit: int,
) -> list[dict[str, Any]]:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    if hasattr(snapshot_store, "list_recent_snapshots"):
        return [
            record
            for record in snapshot_store.list_recent_snapshots(limit=scan_limit)
            if record is not None
        ]
    latest = snapshot_store.latest_snapshot()
    return [latest] if latest is not None else []


def load_recent_snapshot_views(
    db_path: str | Path = "data/local/worldcup.db",
    store: SnapshotStore | None = None,
    limit: int = 2,
    scan_limit: int = SNAPSHOT_VIEW_SCAN_LIMIT,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    competition_order: list[str] = []
    for record in _snapshot_records(db_path, store, scan_limit):
        snapshot = record.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        competition_id = _competition_id_for_snapshot(snapshot)
        if competition_id not in buckets:
            buckets[competition_id] = []
            competition_order.append(competition_id)
        if len(buckets[competition_id]) < limit:
            buckets[competition_id].append(snapshot)

    views: list[dict[str, Any]] = []
    for index in range(max(1, int(limit))):
        component_snapshots = [
            buckets[competition_id][index]
            for competition_id in competition_order
            if index < len(buckets[competition_id])
        ]
        merged = _merge_snapshots(component_snapshots)
        if merged is not None:
            views.append(merged)
    return views


def load_latest_snapshot_view(
    db_path: str | Path = "data/local/worldcup.db",
    store: SnapshotStore | None = None,
    scan_limit: int = SNAPSHOT_VIEW_SCAN_LIMIT,
) -> dict[str, Any] | None:
    snapshots = load_recent_snapshot_views(
        db_path=db_path,
        store=store,
        limit=1,
        scan_limit=scan_limit,
    )
    return snapshots[0] if snapshots else None


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
        competition = _competition_block_for_snapshot({"matches": [record]})
        matches.append(
            {
                "kickoff_at_utc": record.get("kickoff_at_utc", ""),
                "stage": record.get("stage", ""),
                "group": record.get("group", ""),
                "home_team": home,
                "away_team": away,
                "match_label": f"{home} vs {away}".strip(),
                "competition_id": competition.get("id"),
                "competition_label": competition.get("name") or competition.get("label"),
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
        competition = _competition_block_for_snapshot({"matches": [match]})
        rows.append(
            {
                "kickoff_at_utc": match.get("kickoff_at_utc", ""),
                "stage": match.get("stage", ""),
                "group": match.get("group", ""),
                "home_team": home,
                "away_team": away,
                "match_label": f"{home} vs {away}".strip(),
                "competition_id": competition.get("id"),
                "competition_label": competition.get("name") or competition.get("label"),
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
