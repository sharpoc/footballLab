from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.competitions import list_competitions
from worldcup.decision_settlement import settle_match_decision, summarize_decision_records
from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore

GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
FINISHED_MIN_SAMPLE = 20
DEFAULT_COMPETITION_ID = "fifa_world_cup_2026"
DEFAULT_COMPETITION_LABEL = "2026 世界杯"
SNAPSHOT_VIEW_SCAN_LIMIT = 50


def _active_competition_ids() -> list[str]:
    ids = [
        competition.id
        for competition in list_competitions()
        if competition.fixture_policy != "dry_run_probe"
    ]
    return ids or [DEFAULT_COMPETITION_ID]


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


def _competition_block_for_item(
    snapshot: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    return (
        _competition_from_mapping(item)
        or _competition_from_mapping(snapshot)
        or {"id": DEFAULT_COMPETITION_ID, "name": DEFAULT_COMPETITION_LABEL}
    )


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
    skipped = 0
    for snapshot in snapshots:
        competition = _competition_block_for_snapshot(snapshot)
        finished = snapshot.get("finished") or {}
        for match in finished.get("matches") or []:
            matches.append(_with_competition(match, competition))
        skipped += _as_int(finished.get("skipped_no_closing"))
    if not matches and skipped == 0:
        return {}
    summary = summarize_decision_records(matches, skipped_no_closing=skipped)
    return {
        "schema_version": 2,
        "matches": matches,
        "decision_tally": summary["decision_tally"],
        "decision_sample": summary["sample"],
        "decision_coverage": summary["coverage"],
        "skipped_no_closing": skipped,
    }


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
    per_competition_limit: int = 1,
) -> list[dict[str, Any]]:
    snapshot_store = store or SQLiteSnapshotStore(db_path)
    latest_by_competition = getattr(
        snapshot_store,
        "list_latest_snapshots_by_competition",
        None,
    )
    if callable(latest_by_competition):
        records = latest_by_competition(
            _active_competition_ids(),
            per_competition_limit=max(1, int(per_competition_limit)),
        )
        if records:
            return [record for record in records if record is not None]
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
    for record in _snapshot_records(
        db_path,
        store,
        scan_limit,
        per_competition_limit=limit,
    ):
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
    skipped = _as_int(finished.get("skipped_no_closing"))
    decision_summary = summarize_decision_records(
        records,
        min_sample=min_sample,
        skipped_no_closing=skipped,
    )
    return {
        "match_count": len(records),
        "skipped_no_closing": skipped,
        **decision_summary,
    }


_DECISION_PUBLIC_FIELDS = (
    "schema_version",
    "policy_version",
    "label",
    "market",
    "selection",
    "line",
    "odds",
    "p_hit_safe",
    "p_no_loss_safe",
    "computed_at",
    "odds_latest_at",
    "valid_until",
)


_LEGACY_PICK_LABELS = {
    "STRONG_VALUE",
    "VALUE_CANDIDATE",
    "HIGH_CONFIDENCE_LEAN",
    "LOW_CONFIDENCE_LEAN",
}


def _parse_public_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_no_pick(policy_version: str = "match_pick_v2") -> dict[str, Any]:
    return {
        "schema_version": 2,
        "policy_version": policy_version,
        "label": "NO_CLEAN_MARKET",
    }


def _public_match_identity(
    value: dict[str, Any],
    *,
    default_competition_id: str,
) -> tuple[str, str, str, str]:
    competition = value.get("competition") if isinstance(value.get("competition"), dict) else {}
    competition_id = str(
        competition.get("id")
        or value.get("competition_id")
        or default_competition_id
    )
    kickoff_raw = str(value.get("kickoff_at_utc") or "")
    kickoff = _parse_public_at(kickoff_raw)
    kickoff_key = kickoff.isoformat() if kickoff is not None else kickoff_raw
    return (
        competition_id,
        kickoff_key,
        str(value.get("home_team") or "").casefold(),
        str(value.get("away_team") or "").casefold(),
    )


def project_match_decision(
    decision: Any,
    *,
    allow_legacy_history: bool = False,
    as_of: datetime | None = None,
) -> dict[str, Any] | None:
    if not isinstance(decision, dict):
        return None
    raw_label = str(decision.get("label") or "")
    if raw_label == "NO_CLEAN_MARKET":
        policy_version = str(decision.get("policy_version") or "match_pick_v2")
        if policy_version not in {"match_pick_v2", "match_pick_v3"}:
            policy_version = "match_pick_v2"
        return _public_no_pick(policy_version)
    try:
        schema_version = int(decision.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    if schema_version != 2:
        if not allow_legacy_history or raw_label not in _LEGACY_PICK_LABELS:
            return _public_no_pick()
        policy_version = "legacy_match_decision_v1"
    elif raw_label != "MATCH_PICK":
        return _public_no_pick()
    else:
        raw_policy_version = str(decision.get("policy_version") or "match_pick_v2")
        policy_version = (
            raw_policy_version
            if raw_policy_version in {"match_pick_v2", "match_pick_v3"}
            else "match_pick_v2"
        )
    if not decision.get("market") or not decision.get("selection"):
        return None
    if schema_version == 2 and as_of is not None:
        valid_until = _parse_public_at(decision.get("valid_until"))
        if valid_until is None or valid_until <= as_of.astimezone(timezone.utc):
            return _public_no_pick(policy_version)
    public = {
        key: deepcopy(decision.get(key))
        for key in _DECISION_PUBLIC_FIELDS
        if key in decision
    }
    public["schema_version"] = 2
    public["label"] = "MATCH_PICK"
    public["policy_version"] = policy_version
    return public


def _match_pick_blocked(snapshot: dict[str, Any], match: dict[str, Any]) -> bool:
    decision = match.get("match_decision") or {}
    if decision.get("policy_version") == "match_pick_v3":
        return False
    competition = _competition_from_mapping(match) or _competition_from_mapping(snapshot) or {}
    rating_policy = str(competition.get("rating_policy") or "")
    if rating_policy.endswith("_pending"):
        return True
    quality = snapshot.get("data_quality") or {}
    warnings = {str(value) for value in quality.get("warnings") or []}
    return bool(
        warnings
        & {
            "club_rating_pending",
            "club_rating_missing",
            "club_rating_sample_too_small",
            "club_rating_invalid",
        }
    )


def project_finished_rows(snapshot: dict[str, Any]) -> dict[str, Any]:
    finished = snapshot.get("finished") or {}
    records = finished.get("matches") or []
    matches: list[dict[str, Any]] = []
    for record in records:
        result = record.get("result") or {}
        home = record.get("home_team", "")
        away = record.get("away_team", "")
        competition = _competition_block_for_item(snapshot, record)
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
                "closing_match_decision": project_match_decision(
                    record.get("closing_match_decision"),
                    allow_legacy_history=True,
                ),
                "decision_outcome": settle_match_decision(
                    record.get("closing_match_decision"),
                    result,
                ),
            }
        )
    return {
        "schema_version": 2,
        "snapshot_at": snapshot.get("snapshot_at"),
        "summary": summarize_finished_block(snapshot),
        "matches": matches,
    }


def load_daily_sidecar_snapshot(
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    from worldcup.daily_odds_refresh import load_daily_odds_payload

    if path is None:
        import os

        configured = os.environ.get("WORLDCUP_DAILY_ODDS_DATA_DIR", "").strip()
        path = (
            Path(configured) / "daily_odds_snapshot.json"
            if configured
            else Path("data/cache/daily_odds/daily_odds_snapshot.json")
        )
    return load_daily_odds_payload(path)


def project_daily_sidecar(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a public-safe, read-only projection of the isolated daily sidecar."""
    safe = {
        key: deepcopy(snapshot.get(key))
        for key in (
            "schema_version",
            "namespace",
            "generated_at",
            "timezone",
            "cycle",
            "candidate_count",
            "selected_count",
            "coverage",
            "degradation_reasons",
            "combination_rejection_reasons",
        )
        if key in snapshot
    }
    safe["singles"] = []
    for row in snapshot.get("top4") or []:
        if not isinstance(row, dict):
            continue
        item = {
            key: deepcopy(row.get(key))
            for key in (
                "match_id",
                "competition_id",
                "competition_label",
                "kickoff_at_utc",
                "home_team",
                "away_team",
                "market",
                "selection",
                "model_probability",
                "market_implied_probability",
                "edge",
                "last_update",
                "selection_reason",
            )
            if key in row
        }
        safe["singles"].append(item)
    safe["parlay_2"] = deepcopy(snapshot.get("parlay_2") or [])
    safe["parlay_3"] = deepcopy(snapshot.get("parlay_3") or [])
    safe["data_as_of"] = snapshot.get("generated_at")
    return safe


def project_daily_sidecar_api(snapshot: dict[str, Any]) -> dict[str, Any]:
    return project_daily_sidecar(snapshot)
def project_daily_picks(
    snapshot: dict[str, Any],
    *,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Build a safe daily-picks artifact from the existing public match projection."""
    from worldcup.combination_selection import build_combination_research
    from worldcup.daily_competitions import coverage_projection, enabled_competition_ids
    from worldcup.daily_selection import select_daily_top4

    generated_at = now or datetime.now(timezone.utc).isoformat()
    rows = project_match_rows(
        snapshot,
        as_of=_parse_public_at(generated_at),
    )
    selected = select_daily_top4(
        rows,
        now=generated_at,
        enabled_competition_ids=enabled_competition_ids(),
    )
    combinations = build_combination_research(selected.selected)
    combination_payload = combinations.to_dict()
    payload = selected.to_dict()
    payload.update(
        {
            "schema_version": 1,
            "data_as_of": snapshot.get("snapshot_at"),
            "singles": [deepcopy(row) for row in selected.selected],
            "coverage": coverage_projection(),
            "parlay_2": combination_payload["parlay_2"],
            "parlay_3": combination_payload["parlay_3"],
            "combination_rejection_reasons": list(combinations.rejection_reasons),
            "combination_degradation_reasons": list(combinations.degradation_reasons),
        }
    )
    return payload


def project_match_rows(
    snapshot: dict[str, Any],
    *,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    data_quality = snapshot.get("data_quality") or {}
    stale = bool(data_quality.get("stale_sources"))
    rows: list[dict[str, Any]] = []
    as_of = as_of or datetime.now(timezone.utc)
    default_competition_id = _competition_id_for_snapshot(snapshot)
    finished_identities = {
        _public_match_identity(
            record,
            default_competition_id=default_competition_id,
        )
        for record in ((snapshot.get("finished") or {}).get("matches") or [])
        if isinstance(record, dict)
    }
    for match in snapshot.get("matches", []):
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        fixture_status = str(match.get("fixture_status") or "SCHEDULED").upper()
        competition = _competition_block_for_item(snapshot, match)
        match_identity = _public_match_identity(
            match,
            default_competition_id=str(competition.get("id") or default_competition_id),
        )
        if fixture_status == "POSTPONED" or match_identity in finished_identities:
            continue
        refresh_plan = match.get("refresh_plan") or {}
        raw_decision = match.get("match_decision") or {}
        last_update_at = (
            raw_decision.get("odds_latest_at")
            or match.get("odds_updated_at")
            or raw_decision.get("computed_at")
            or snapshot.get("snapshot_at")
        )
        last_update_label = (
            "赔率更新"
            if raw_decision.get("odds_latest_at") or match.get("odds_updated_at")
            else "分析更新"
        )
        projected_decision = (
            _public_no_pick()
            if _match_pick_blocked(snapshot, match)
            else project_match_decision(match.get("match_decision"), as_of=as_of)
        )
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
                "fixture_status": fixture_status,
                "last_update_at": last_update_at,
                "last_update_label": last_update_label,
                "next_update_at": refresh_plan.get("next_update_at"),
                "next_update_label": refresh_plan.get("label"),
                "next_update_description": refresh_plan.get("description"),
                "stale": stale,
                "match_decision": projected_decision or _public_no_pick(),
            }
        )
    return rows
