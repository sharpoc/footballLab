from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.league_acceptance import LeagueAcceptanceStore, acceptance_row_is_active
from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.ingest import build_ingest_request
from worldcup.league_lineup_notifications import (
    DEFAULT_SOURCE_FAILURE_THRESHOLD,
    LeagueLineupNotificationOutbox,
    _atomic_write as _atomic_write_notification_state,
    build_missing_lineup_event,
    build_published_refresh_event,
    build_quota_blocked_event,
    build_source_failure_event,
    build_source_recovery_event,
    _read_state as _read_notification_state,
)
from worldcup.league_lineups_refresh import (
    _pending_deliveries,
    _read_pending,
    _validate_state as _validate_lineup_state,
    run_league_lineups_refresh,
)
from worldcup.league_post_lineup_refresh import (
    PostLineupRefreshStateStore,
    _ack_token,
    _normalize_receipts,
    run_post_lineup_refresh,
)
from worldcup.league_team_identity import accepted_league_team_identity_registry
from worldcup.notifications import send_wxpusher_notification
from worldcup.publish import DEFAULT_ENDPOINT, _default_sender
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.secrets import validate_hmac_secret
from worldcup.sources.theoddsapi import DEFAULT_MARKETS, fetch_odds_for_sport
from worldcup.theoddsapi_keys import configured_key_slots


DEFAULT_LOCK_RELATIVE_PATH = Path("data/local/leagues/league_pre_match.lock")
STATE_RELATIVE_PATH = Path("data/local/leagues/league_pre_match_state.json")
DEFAULT_QUOTA_RELATIVE_PATH = Path("data/cache/quota.json")
_SAFE_REASON = re.compile(r"^[a-z][a-z0-9_]{0,80}$")
_TERMINAL_FIXTURE_STATUSES = frozenset({
    "POSTPONED",
    "CANCELLED",
    "CANCELED",
    "STARTED",
    "LIVE",
    "IN_PROGRESS",
    "FINISHED",
})
_MISSING_ELIGIBLE_FIXTURE_STATUSES = frozenset({"SCHEDULED"})
_QUOTA_BLOCK_REASONS = frozenset({
    "quota_unknown",
    "quota_below_minimum",
    "quota_exhausted",
    "quota_key_unavailable",
})
_LINEUP_STATUSES = frozenset({
    "blocked",
    "dry_run",
    "error",
    "no_due",
    "partial",
    "pending_delivery",
    "polled",
    "recovered",
    "refreshed",
})
_POST_STATUSES = frozenset({
    "already_acked",
    "blocked",
    "dry_run",
    "not_due",
    "partial",
    "publish_failed",
    "published",
    "refresh_failed",
})


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid_now") from None
    else:
        raise ValueError("invalid_now")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_now")
    return parsed.astimezone(timezone.utc)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_reason(value: Any, default: str) -> str:
    return value if isinstance(value, str) and _SAFE_REASON.fullmatch(value) else default


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _flags_are_safe(
    *,
    live_lineups: bool,
    write_lineups: bool,
    refresh_after_lineups: bool,
    live_refresh: bool,
    refresh_guard: bool,
    publish: bool,
    notify: bool,
) -> bool:
    if live_lineups != write_lineups:
        return False
    if refresh_after_lineups and not (live_lineups and write_lineups):
        return False
    if refresh_guard and not refresh_after_lineups:
        return False
    if live_refresh and not (refresh_after_lineups and refresh_guard and publish):
        return False
    if publish and not live_refresh:
        return False
    if notify and not publish:
        return False
    return True


def _project_decision(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 2:
        raise ValueError("league_pre_match_context_invalid")
    label = value.get("label")
    if label == "NO_CLEAN_MARKET":
        return {"schema_version": 2, "label": label}
    if label != "MATCH_PICK":
        raise ValueError("league_pre_match_context_invalid")
    market = value.get("market")
    selection = value.get("selection")
    line = value.get("line")
    probability = value.get("p_hit_safe")
    odds = value.get("odds")
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not math.isfinite(float(probability))
        or not 0 <= float(probability) <= 1
        or isinstance(odds, bool)
        or not isinstance(odds, (int, float))
        or not math.isfinite(float(odds))
        or float(odds) <= 1
    ):
        raise ValueError("league_pre_match_context_invalid")
    checked_line: float | None
    if market == "1X2" and selection in {"home", "draw", "away"} and line is None:
        checked_line = None
    elif market == "DNB" and selection in {"home", "away"} and line in {0, 0.0, -0.0}:
        checked_line = 0.0
    elif (
        market == "AH"
        and selection in {"home", "away"}
        and isinstance(line, (int, float))
        and not isinstance(line, bool)
        and math.isfinite(float(line))
        and float(line) != 0
    ):
        checked_line = float(line)
    elif (
        market == "OU"
        and selection in {"over", "under"}
        and isinstance(line, (int, float))
        and not isinstance(line, bool)
        and math.isfinite(float(line))
        and float(line) > 0
    ):
        checked_line = float(line)
    else:
        raise ValueError("league_pre_match_context_invalid")
    return {
        "schema_version": 2,
        "label": label,
        "market": market,
        "selection": selection,
        "line": checked_line,
        "p_hit_safe": float(probability),
        "odds": float(odds),
    }


def _safe_display(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 160:
        raise ValueError("league_pre_match_context_invalid")
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("league_pre_match_context_invalid")
    return text


def _safe_snapshot_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or any(not (char.isalnum() or char in "-_") for char in value)
    ):
        raise ValueError("league_pre_match_context_invalid")
    return value


def _validate_context(value: Any, competition_id: str, event_id: str) -> dict[str, Any]:
    expected = {
        "competition_id",
        "event_id",
        "home_team",
        "away_team",
        "kickoff_at_utc",
        "fixture_status",
        "acceptance_active",
        "snapshot_id",
        "match_decision",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("league_pre_match_context_invalid")
    if value.get("competition_id") != competition_id or value.get("event_id") != event_id:
        raise ValueError("league_pre_match_context_invalid")
    fixture_status = value.get("fixture_status")
    if not isinstance(fixture_status, str) or len(fixture_status) > 40:
        raise ValueError("league_pre_match_context_invalid")
    fixture_status = fixture_status.strip().upper()
    if not isinstance(value.get("acceptance_active"), bool):
        raise ValueError("league_pre_match_context_invalid")
    return {
        "competition_id": competition_id,
        "event_id": event_id,
        "home_team": _safe_display(value.get("home_team")),
        "away_team": _safe_display(value.get("away_team")),
        "kickoff_at_utc": _utc(value.get("kickoff_at_utc")).isoformat(),
        "fixture_status": fixture_status,
        "acceptance_active": value["acceptance_active"],
        "snapshot_id": _safe_snapshot_id(value.get("snapshot_id")),
        "match_decision": _project_decision(value.get("match_decision")),
    }


def _load_match_contexts(root: str | Path) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    root_path = Path(root)
    acceptance = LeagueAcceptanceStore(
        root_path / "data/local/leagues/acceptance.json"
    ).read()
    rows = acceptance.get("competitions") if isinstance(acceptance, Mapping) else {}
    if not isinstance(rows, Mapping):
        return contexts
    for competition_id in sorted(set(rows).intersection(FORMAL_SINGLE_MATCH_IDS)):
        active = acceptance_row_is_active(rows.get(competition_id), competition_id)
        if not active:
            continue
        path = root_path / "data/cache/leagues" / competition_id / "snapshot.json"
        if not path.exists():
            continue
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        competition = snapshot.get("competition") if isinstance(snapshot, Mapping) else None
        matches = snapshot.get("matches") if isinstance(snapshot, Mapping) else None
        snapshot_id = snapshot.get("snapshot_id") if isinstance(snapshot, Mapping) else None
        if (
            not isinstance(competition, Mapping)
            or competition.get("id") != competition_id
            or not isinstance(matches, list)
        ):
            continue
        for row in matches:
            if not isinstance(row, Mapping):
                continue
            event_id = row.get("source_event_id")
            if not isinstance(event_id, str) or not event_id.strip():
                continue
            fixture_status = str(
                row.get("fixture_status") or row.get("status") or ""
            ).strip().upper()
            if (
                fixture_status in _TERMINAL_FIXTURE_STATUSES
                or str(row.get("lineup_status") or "").strip().upper() == "CONFIRMED"
            ):
                continue
            try:
                checked = _validate_context(
                    {
                        "competition_id": competition_id,
                        "event_id": event_id,
                        "home_team": row.get("home_team"),
                        "away_team": row.get("away_team"),
                        "kickoff_at_utc": row.get("kickoff_at_utc"),
                        "fixture_status": fixture_status,
                        "acceptance_active": active,
                        "snapshot_id": snapshot_id,
                        "match_decision": row.get("match_decision"),
                    },
                    competition_id,
                    event_id,
                )
            except ValueError:
                continue
            contexts[f"{competition_id}:{event_id}"] = checked
    return contexts


def _validate_contexts(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ValueError("league_pre_match_context_invalid")
    checked: dict[str, dict[str, Any]] = {}
    for key, context in value.items():
        if not isinstance(key, str) or ":" not in key:
            raise ValueError("league_pre_match_context_invalid")
        competition_id, event_id = key.split(":", 1)
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not event_id:
            raise ValueError("league_pre_match_context_invalid")
        checked[key] = _validate_context(context, competition_id, event_id)
    return checked


def _eligible_contexts(
    contexts: Mapping[str, Mapping[str, Any]], now: datetime
) -> dict[str, dict[str, Any]]:
    return {
        key: dict(context)
        for key, context in contexts.items()
        if context.get("acceptance_active") is True
        and context.get("fixture_status") not in _TERMINAL_FIXTURE_STATUSES
        and _utc(context["kickoff_at_utc"]) > now
    }


def _validate_ack_key(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "competition_id", "event_id", "lineup_fingerprint"
    }:
        raise ValueError("league_pre_match_result_invalid")
    competition_id = value.get("competition_id")
    event_id = value.get("event_id")
    fingerprint = value.get("lineup_fingerprint")
    if (
        competition_id not in FORMAL_SINGLE_MATCH_IDS
        or not isinstance(event_id, str)
        or not event_id.strip()
        or not _valid_hash(fingerprint)
    ):
        raise ValueError("league_pre_match_result_invalid")
    return {
        "competition_id": competition_id,
        "event_id": event_id,
        "lineup_fingerprint": fingerprint,
    }


def _group_receipts(value: Any) -> dict[str, list[dict[str, Any]]]:
    normalized = _normalize_receipts(value)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        projected = {name: row[name] for name in (
            "event_id", "source_match_id", "kickoff_at_utc", "fetched_at",
            "lineup_fingerprint", "ack_key",
        )}
        grouped.setdefault(row["competition_id"], []).append(projected)
    return grouped


def _tokens(value: Mapping[str, list[Mapping[str, Any]]]) -> set[str]:
    return {
        _ack_token(row["ack_key"])
        for rows in value.values()
        for row in rows
    }


def _without_tokens(
    value: Mapping[str, list[Mapping[str, Any]]], excluded: set[str]
) -> dict[str, list[dict[str, Any]]]:
    return {
        competition_id: [
            dict(row) for row in rows if _ack_token(row["ack_key"]) not in excluded
        ]
        for competition_id, rows in value.items()
        if any(_ack_token(row["ack_key"]) not in excluded for row in rows)
    }


def _validate_lineup_result(value: Any) -> dict[str, Any]:
    required = {
        "status",
        "skipped",
        "rejection_reasons",
        "newly_confirmed",
        "next_due_at",
        "counts",
    }
    allowed = required | {"reason", "source_events"}
    if (
        not isinstance(value, Mapping)
        or set(value) < required
        or not set(value).issubset(allowed)
        or value.get("status") not in _LINEUP_STATUSES
    ):
        raise ValueError("lineup_result_invalid")
    counts = value.get("counts")
    count_names = {
        "fixture_count", "request_count", "calendar_fetch_count", "details_fetch_count",
        "accepted_count", "newly_confirmed_count", "rejection_count",
        "source_failure_count", "cache_commit_count", "state_commit_count",
    }
    if not isinstance(counts, Mapping) or set(counts) != count_names:
        raise ValueError("lineup_result_invalid")
    projected_counts: dict[str, int] = {}
    for name in sorted(count_names):
        count = counts[name]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("lineup_result_invalid")
        projected_counts[name] = count
    try:
        grouped = _group_receipts(value.get("newly_confirmed") or {})
    except ValueError as exc:
        raise ValueError("lineup_result_invalid") from exc
    def counters(candidate: Any) -> dict[str, dict[str, int]]:
        if not isinstance(candidate, Mapping):
            raise ValueError("lineup_result_invalid")
        projected: dict[str, dict[str, int]] = {}
        for competition_id, reasons in candidate.items():
            if competition_id not in FORMAL_SINGLE_MATCH_IDS or not isinstance(reasons, Mapping):
                raise ValueError("lineup_result_invalid")
            checked: dict[str, int] = {}
            for reason, count in reasons.items():
                if (
                    not isinstance(reason, str)
                    or not _SAFE_REASON.fullmatch(reason)
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 0
                ):
                    raise ValueError("lineup_result_invalid")
                checked[reason] = count
            projected[competition_id] = checked
        return projected

    rejection_reasons = counters(value.get("rejection_reasons"))
    skipped = counters(value.get("skipped"))
    source_events_value = value.get("source_events") or []
    if not isinstance(source_events_value, list):
        raise ValueError("lineup_result_invalid")
    source_events: list[dict[str, str]] = []
    source_event_keys: set[tuple[str, str]] = set()
    for row in source_events_value:
        if not isinstance(row, Mapping) or set(row) != {
            "competition_id", "event_id", "outcome"
        }:
            raise ValueError("lineup_result_invalid")
        competition_id = row.get("competition_id")
        event_id = row.get("event_id")
        outcome = row.get("outcome")
        key = (competition_id, event_id)
        if (
            competition_id not in FORMAL_SINGLE_MATCH_IDS
            or not isinstance(event_id, str)
            or not event_id.strip()
            or outcome not in {"failed", "succeeded"}
            or key in source_event_keys
        ):
            raise ValueError("lineup_result_invalid")
        source_event_keys.add(key)
        source_events.append({
            "competition_id": competition_id,
            "event_id": event_id,
            "outcome": outcome,
        })
    newly_confirmed_count = sum(len(rows) for rows in grouped.values())
    rejection_count = sum(sum(reasons.values()) for reasons in rejection_reasons.values())
    failed_evidence = any(row["outcome"] == "failed" for row in source_events)
    succeeded_evidence = any(row["outcome"] == "succeeded" for row in source_events)
    if (
        projected_counts["newly_confirmed_count"] != newly_confirmed_count
        or projected_counts["rejection_count"] != rejection_count
        or (failed_evidence and projected_counts["source_failure_count"] == 0)
        or (succeeded_evidence and projected_counts["request_count"] == 0)
    ):
        raise ValueError("lineup_result_invalid")
    reason = value.get("reason")
    return {
        "status": value["status"],
        "reason": _safe_reason(reason, "lineup_failed") if reason is not None else None,
        "newly_confirmed": grouped,
        "counts": projected_counts,
        "skipped": skipped,
        "rejection_reasons": rejection_reasons,
        "source_events": source_events,
        "next_due_at": value.get("next_due_at") if isinstance(value.get("next_due_at"), str) else None,
    }


def _validate_post_result(
    value: Any,
    *,
    submitted: Mapping[str, list[Mapping[str, Any]]],
    root: str | Path,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"status", "plan", "acks", "refresh", "publish"}
        or value.get("status") not in _POST_STATUSES
    ):
        raise ValueError("post_lineup_result_invalid")
    submitted_tokens = _tokens(submitted)
    plan = value.get("plan")
    if (
        not isinstance(plan, Mapping)
        or set(plan) != {"competition_ids", "receipt_count"}
        or plan.get("competition_ids") != sorted(submitted)
        or plan.get("receipt_count") != len(submitted_tokens)
    ):
        raise ValueError("post_lineup_result_invalid")
    acks = value.get("acks")
    if not isinstance(acks, Mapping) or set(acks) != {"durable", "retryable", "blocked"}:
        raise ValueError("post_lineup_result_invalid")
    checked_acks: dict[str, list[dict[str, Any]]] = {}
    ack_tokens: set[str] = set()
    for group in ("durable", "retryable", "blocked"):
        rows = acks[group]
        if not isinstance(rows, list):
            raise ValueError("post_lineup_result_invalid")
        checked_rows: list[dict[str, Any]] = []
        for row in rows:
            expected = {"ack_key"} if group == "durable" else {"ack_key", "reason"}
            if not isinstance(row, Mapping) or set(row) != expected:
                raise ValueError("post_lineup_result_invalid")
            ack_key = _validate_ack_key(row.get("ack_key"))
            token = _ack_token(ack_key)
            if token not in submitted_tokens or token in ack_tokens:
                raise ValueError("post_lineup_result_invalid")
            ack_tokens.add(token)
            item: dict[str, Any] = {"ack_key": ack_key}
            if group != "durable":
                reason = row.get("reason")
                if not isinstance(reason, str) or not _SAFE_REASON.fullmatch(reason):
                    raise ValueError("post_lineup_result_invalid")
                item["reason"] = reason
            checked_rows.append(item)
        checked_acks[group] = checked_rows
    if ack_tokens != submitted_tokens:
        raise ValueError("post_lineup_result_invalid")

    publication = value.get("publish")
    publish_status = None
    aggregate_snapshot_id = None
    component_snapshot_ids: dict[str, str] = {}
    if publication is not None and not isinstance(publication, Mapping):
        raise ValueError("post_lineup_result_invalid")
    if isinstance(publication, Mapping) and publication.get("status") == "published":
        if value["status"] not in {"partial", "publish_failed", "published"}:
            raise ValueError("post_lineup_result_invalid")
        if set(publication) != {"status", "publish", "aggregate"}:
            raise ValueError("post_lineup_result_invalid")
        receipt = publication.get("publish")
        aggregate = publication.get("aggregate")
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != {"status"}
            or receipt.get("status") not in {"stored", "duplicate"}
            or not isinstance(aggregate, Mapping)
            or set(aggregate) != {"snapshot_id", "run_id", "components"}
            or not isinstance(aggregate.get("components"), list)
        ):
            raise ValueError("post_lineup_result_invalid")
        publish_status = receipt["status"]
        aggregate_snapshot_id = _safe_snapshot_id(aggregate.get("snapshot_id"))
        _safe_snapshot_id(aggregate.get("run_id"))
        for component in aggregate["components"]:
            if (
                not isinstance(component, Mapping)
                or set(component) != {"competition_id", "snapshot_id"}
                or component.get("competition_id") not in FORMAL_SINGLE_MATCH_IDS
                or component.get("competition_id") in component_snapshot_ids
            ):
                raise ValueError("post_lineup_result_invalid")
            component_snapshot_ids[component["competition_id"]] = _safe_snapshot_id(
                component.get("snapshot_id")
            )
        if (
            value["status"] != "publish_failed"
            and not set(submitted).issubset(component_snapshot_ids)
        ):
            raise ValueError("post_lineup_result_invalid")
    elif isinstance(publication, Mapping):
        failed_publish = publication.get("publish")
        if (
            set(publication) != {"status", "reason", "publish", "aggregate"}
            or publication.get("status") != "publish_failed"
            or not isinstance(publication.get("reason"), str)
            or not _SAFE_REASON.fullmatch(publication["reason"])
            or publication.get("aggregate") is not None
            or (
                failed_publish is not None
                and (
                    not isinstance(failed_publish, Mapping)
                    or set(failed_publish) != {"status"}
                    or failed_publish.get("status")
                    not in {"error", "failed", "invalid", "rejected"}
                )
            )
        ):
            raise ValueError("post_lineup_result_invalid")

    if value["status"] == "published" and (
        publish_status is None
        or checked_acks["retryable"]
        or checked_acks["blocked"]
        or len(checked_acks["durable"]) != len(submitted_tokens)
    ):
        raise ValueError("post_lineup_result_invalid")
    if value["status"] == "already_acked" and (
        publication is not None
        or checked_acks["retryable"]
        or checked_acks["blocked"]
        or len(checked_acks["durable"]) != len(submitted_tokens)
    ):
        raise ValueError("post_lineup_result_invalid")
    if publish_status is not None and value["status"] not in {
        "partial", "publish_failed", "published"
    }:
        raise ValueError("post_lineup_result_invalid")

    status = value["status"]
    durable_count = len(checked_acks["durable"])
    retryable_count = len(checked_acks["retryable"])
    blocked_count = len(checked_acks["blocked"])
    unresolved_count = retryable_count + blocked_count
    failed_publication = (
        isinstance(publication, Mapping)
        and publication.get("status") == "publish_failed"
    )
    ack_commit_failures = [
        item for item in checked_acks["retryable"]
        if item.get("reason") == "ack_state_commit_failed"
    ]
    blocked_ack_commit_failures = [
        item for item in checked_acks["blocked"]
        if item.get("reason") == "ack_state_commit_failed"
    ]
    if ack_commit_failures or blocked_ack_commit_failures:
        if (
            blocked_ack_commit_failures
            or status != "publish_failed"
            or publish_status not in {"stored", "duplicate"}
        ):
            raise ValueError("post_lineup_result_invalid")
        try:
            task5_state = PostLineupRefreshStateStore(root).read()
            submitted_rows = {
                row["token"]: row for row in _normalize_receipts(submitted)
            }
        except Exception:
            raise ValueError("post_lineup_result_invalid") from None
        durable_tokens = {
            _ack_token(item["ack_key"]) for item in checked_acks["durable"]
        }
        current_tokens: set[str] = set()
        current_competitions: set[str] = set()
        for token, row in submitted_rows.items():
            if token in durable_tokens:
                continue
            state_row = task5_state["receipts"].get(token)
            if not isinstance(state_row, Mapping):
                continue
            competition_id = row["competition_id"]
            component_snapshot_id = component_snapshot_ids.get(competition_id)
            if state_row.get("phase") == "committed":
                if component_snapshot_id != state_row.get("snapshot_id"):
                    raise ValueError("post_lineup_result_invalid")
            elif state_row.get("phase") == "published":
                if not (
                    component_snapshot_id == state_row.get("snapshot_id")
                    and aggregate_snapshot_id
                    == state_row.get("aggregate_snapshot_id")
                    and publish_status == state_row.get("publish_status")
                ):
                    continue
            else:
                continue
            if component_snapshot_id is None:
                raise ValueError("post_lineup_result_invalid")
            current_tokens.add(token)
            current_competitions.add(competition_id)
        if (
            {_ack_token(item["ack_key"]) for item in ack_commit_failures}
            != current_tokens
            or set(component_snapshot_ids) != current_competitions
        ):
            raise ValueError("post_lineup_result_invalid")
    if status == "partial":
        if (
            durable_count == 0
            or (publish_status is None and unresolved_count == 0)
            or (failed_publication and retryable_count == 0)
        ):
            raise ValueError("post_lineup_result_invalid")
    elif status == "blocked":
        if durable_count or publication is not None or unresolved_count == 0:
            raise ValueError("post_lineup_result_invalid")
    elif status == "refresh_failed":
        if durable_count or publication is not None or retryable_count == 0:
            raise ValueError("post_lineup_result_invalid")
    elif status == "publish_failed":
        if retryable_count == 0:
            raise ValueError("post_lineup_result_invalid")
        if publish_status is None:
            if durable_count:
                raise ValueError("post_lineup_result_invalid")
        else:
            if not ack_commit_failures or any(
                item["ack_key"]["competition_id"] not in component_snapshot_ids
                for item in ack_commit_failures
            ):
                raise ValueError("post_lineup_result_invalid")
    elif status in {"dry_run", "not_due"}:
        raise ValueError("post_lineup_result_invalid")
    if failed_publication and status not in {"partial", "publish_failed"}:
        raise ValueError("post_lineup_result_invalid")

    for item in checked_acks["durable"]:
        competition_id = item["ack_key"]["competition_id"]
        if publish_status is not None and status != "publish_failed":
            evidence = {
                "publish_status": publish_status,
                "aggregate_snapshot_id": aggregate_snapshot_id,
                "component_snapshot_id": component_snapshot_ids.get(competition_id),
            }
        else:
            evidence = _published_evidence_from_state(root, item["ack_key"])
        if not isinstance(evidence, Mapping):
            raise ValueError("post_lineup_result_invalid")
        item.update(evidence)
    return {
        "status": value["status"],
        "acks": checked_acks,
        "publish_status": publish_status,
        "aggregate_snapshot_id": aggregate_snapshot_id,
        "component_snapshot_ids": component_snapshot_ids,
    }


def _project_lineups(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": value["status"],
        **({"reason": value["reason"]} if value.get("reason") else {}),
        "counts": dict(value["counts"]),
        "rejection_reasons": dict(value["rejection_reasons"]),
        "next_due_at": value.get("next_due_at"),
    }


def _project_post(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": value["status"],
        "acks": {
            name: [
                {
                    "ack_key": dict(row["ack_key"]),
                    **({"reason": row["reason"]} if "reason" in row else {}),
                }
                for row in value["acks"][name]
            ]
            for name in ("durable", "retryable", "blocked")
        },
        "publish_status": value.get("publish_status"),
    }


def _empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "receipts": {}, "source_episodes": {}}


def _validate_job(value: Any, token: str) -> dict[str, Any]:
    expected = {
        "competition_id", "event_id", "source_match_id", "kickoff_at_utc", "fetched_at",
        "lineup_fingerprint", "ack_key", "home_team", "away_team",
        "previous_snapshot_id", "previous_decision", "current_snapshot_id",
        "aggregate_snapshot_id", "publish_status", "current_decision",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("league_pre_match_state_invalid")
    ack_key = _validate_ack_key(value.get("ack_key"))
    if _ack_token(ack_key) != token:
        raise ValueError("league_pre_match_state_invalid")
    kickoff = _utc(value.get("kickoff_at_utc")).isoformat()
    fetched = _utc(value.get("fetched_at")).isoformat()
    if _utc(fetched) >= _utc(kickoff):
        raise ValueError("league_pre_match_state_invalid")
    if (
        value.get("competition_id") != ack_key["competition_id"]
        or value.get("event_id") != ack_key["event_id"]
        or value.get("lineup_fingerprint") != ack_key["lineup_fingerprint"]
    ):
        raise ValueError("league_pre_match_state_invalid")
    current_decision = value.get("current_decision")
    current_snapshot_id = value.get("current_snapshot_id")
    aggregate_snapshot_id = value.get("aggregate_snapshot_id")
    publish_status = value.get("publish_status")
    bound_values = (current_decision, current_snapshot_id, aggregate_snapshot_id, publish_status)
    if all(item is None for item in bound_values):
        projected_current = None
        projected_current_snapshot = None
        projected_aggregate_snapshot = None
        projected_publish_status = None
    elif any(item is None for item in bound_values) or publish_status not in {"stored", "duplicate"}:
        raise ValueError("league_pre_match_state_invalid")
    else:
        projected_current = _project_decision(current_decision)
        projected_current_snapshot = _safe_snapshot_id(current_snapshot_id)
        projected_aggregate_snapshot = _safe_snapshot_id(aggregate_snapshot_id)
        projected_publish_status = publish_status
    return {
        "competition_id": ack_key["competition_id"],
        "event_id": ack_key["event_id"],
        "source_match_id": _safe_display(value.get("source_match_id")),
        "kickoff_at_utc": kickoff,
        "fetched_at": fetched,
        "lineup_fingerprint": ack_key["lineup_fingerprint"],
        "ack_key": ack_key,
        "home_team": _safe_display(value.get("home_team")),
        "away_team": _safe_display(value.get("away_team")),
        "previous_snapshot_id": _safe_snapshot_id(value.get("previous_snapshot_id")),
        "previous_decision": _project_decision(value.get("previous_decision")),
        "current_snapshot_id": projected_current_snapshot,
        "aggregate_snapshot_id": projected_aggregate_snapshot,
        "publish_status": projected_publish_status,
        "current_decision": projected_current,
    }


def _validate_episode(value: Any, key: str) -> dict[str, Any]:
    expected = {
        "competition_id", "event_id", "episode_id", "generation", "failure_count",
        "failure_threshold", "active", "failure_pending", "failure_notified",
        "recovery_pending", "failure_notification_fingerprint",
        "recovery_notification_fingerprint", "home_team", "away_team",
        "kickoff_at_utc", "started_at", "last_failure_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected or ":" not in key:
        raise ValueError("league_pre_match_state_invalid")
    competition_id, event_id = key.split(":", 1)
    count = value.get("failure_count")
    threshold = value.get("failure_threshold")
    generation = value.get("generation")
    active = value.get("active")
    failure_pending = value.get("failure_pending")
    failure_notified = value.get("failure_notified")
    recovery_pending = value.get("recovery_pending")
    failure_notification_fingerprint = value.get(
        "failure_notification_fingerprint"
    )
    recovery_notification_fingerprint = value.get(
        "recovery_notification_fingerprint"
    )
    if (
        value.get("competition_id") != competition_id
        or value.get("event_id") != event_id
        or competition_id not in FORMAL_SINGLE_MATCH_IDS
        or not event_id
        or not _valid_hash(value.get("episode_id"))
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
        or isinstance(threshold, bool)
        or not isinstance(threshold, int)
        or threshold < 1
        or not isinstance(active, bool)
        or not isinstance(failure_pending, bool)
        or not isinstance(failure_notified, bool)
        or not isinstance(recovery_pending, bool)
        or (failure_pending and failure_notified)
        or (not active and (failure_pending or recovery_pending))
        or (recovery_pending and not (failure_pending or failure_notified))
        or (count < threshold and (failure_pending or failure_notified or recovery_pending))
        or (active and count >= threshold and not (failure_pending or failure_notified))
        or (
            count < threshold
            and (
                failure_notification_fingerprint is not None
                or recovery_notification_fingerprint is not None
            )
        )
        or (
            count >= threshold
            and not _valid_hash(failure_notification_fingerprint)
        )
        or (
            recovery_pending
            and not _valid_hash(recovery_notification_fingerprint)
        )
        or (
            recovery_notification_fingerprint is not None
            and not _valid_hash(recovery_notification_fingerprint)
        )
    ):
        raise ValueError("league_pre_match_state_invalid")
    return {
        "competition_id": competition_id,
        "event_id": event_id,
        "episode_id": value["episode_id"],
        "generation": generation,
        "failure_count": count,
        "failure_threshold": threshold,
        "active": active,
        "failure_pending": failure_pending,
        "failure_notified": failure_notified,
        "recovery_pending": recovery_pending,
        "failure_notification_fingerprint": failure_notification_fingerprint,
        "recovery_notification_fingerprint": recovery_notification_fingerprint,
        "home_team": _safe_display(value.get("home_team")),
        "away_team": _safe_display(value.get("away_team")),
        "kickoff_at_utc": _utc(value.get("kickoff_at_utc")).isoformat(),
        "started_at": _utc(value.get("started_at")).isoformat(),
        "last_failure_at": _utc(value.get("last_failure_at")).isoformat(),
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "receipts", "source_episodes"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("receipts"), Mapping)
        or not isinstance(value.get("source_episodes"), Mapping)
    ):
        raise ValueError("league_pre_match_state_invalid")
    receipts = {
        token: _validate_job(job, token)
        for token, job in value["receipts"].items()
        if isinstance(token, str)
    }
    if len(receipts) != len(value["receipts"]):
        raise ValueError("league_pre_match_state_invalid")
    episodes = {
        key: _validate_episode(episode, key)
        for key, episode in value["source_episodes"].items()
        if isinstance(key, str)
    }
    if len(episodes) != len(value["source_episodes"]):
        raise ValueError("league_pre_match_state_invalid")
    return {
        "schema_version": 1,
        "receipts": {key: receipts[key] for key in sorted(receipts)},
        "source_episodes": {key: episodes[key] for key in sorted(episodes)},
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
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


class LeaguePreMatchStateStore:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / STATE_RELATIVE_PATH
        self.lock_path = self.path.with_suffix(".lock")

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("league_pre_match_state_invalid") from exc
        return _validate_state(value)

    def commit(self, value: Mapping[str, Any]) -> None:
        checked = _validate_state(value)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            _atomic_write(self.path, checked)


def _jobs_as_receipts(state: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for job in state["receipts"].values():
        if job.get("current_decision") is not None:
            continue
        grouped.setdefault(job["competition_id"], []).append({
            name: job[name] for name in (
                "event_id", "source_match_id", "kickoff_at_utc", "fetched_at",
                "lineup_fingerprint", "ack_key",
            )
        })
    return _group_receipts(grouped)


def _merge_receipts(*groups: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    seen: dict[tuple[str, str], str] = {}
    for grouped in groups:
        for competition_id, rows in grouped.items():
            for row in rows:
                identity = (competition_id, row["event_id"])
                fingerprint = row["lineup_fingerprint"]
                if identity in seen and seen[identity] != fingerprint:
                    raise ValueError("lineup_receipt_conflict")
                seen[identity] = fingerprint
                if not any(existing["lineup_fingerprint"] == fingerprint for existing in merged.setdefault(competition_id, [])):
                    merged[competition_id].append(dict(row))
    return _group_receipts(merged)


def _stage_jobs(
    *,
    store: Any,
    state: Mapping[str, Any],
    receipts: Mapping[str, list[Mapping[str, Any]]],
    contexts: Mapping[str, Any],
) -> dict[str, Any]:
    updated = {
        "schema_version": 1,
        "receipts": {key: dict(value) for key, value in state["receipts"].items()},
        "source_episodes": {key: dict(value) for key, value in state["source_episodes"].items()},
    }
    changed = False
    for row in _normalize_receipts(receipts):
        token = row["token"]
        if token in updated["receipts"]:
            continue
        context = _validate_context(
            contexts.get(f"{row['competition_id']}:{row['event_id']}"),
            row["competition_id"],
            row["event_id"],
        )
        if context["kickoff_at_utc"] != row["kickoff_at_utc"]:
            raise ValueError("league_pre_match_context_invalid")
        updated["receipts"][token] = {
            "competition_id": row["competition_id"],
            "event_id": row["event_id"],
            "source_match_id": row["source_match_id"],
            "kickoff_at_utc": row["kickoff_at_utc"],
            "fetched_at": row["fetched_at"],
            "lineup_fingerprint": row["lineup_fingerprint"],
            "ack_key": dict(row["ack_key"]),
            "home_team": context["home_team"],
            "away_team": context["away_team"],
            "previous_snapshot_id": context["snapshot_id"],
            "previous_decision": dict(context["match_decision"]),
            "current_snapshot_id": None,
            "aggregate_snapshot_id": None,
            "publish_status": None,
            "current_decision": None,
        }
        changed = True
    checked = _validate_state(updated)
    if changed:
        store.commit(checked)
    return checked


def _bind_published_jobs(
    *,
    root: str | Path,
    store: Any,
    state: Mapping[str, Any],
    post: Mapping[str, Any],
    contexts: Mapping[str, Any],
) -> dict[str, Any]:
    updated = {
        "schema_version": 1,
        "receipts": {key: dict(value) for key, value in state["receipts"].items()},
        "source_episodes": {key: dict(value) for key, value in state["source_episodes"].items()},
    }
    changed = False
    for item in post["acks"]["durable"]:
        token = _ack_token(item["ack_key"])
        job = updated["receipts"].get(token)
        if not isinstance(job, Mapping):
            raise ValueError("league_pre_match_receipt_binding_invalid")
        context = None
        try:
            candidate = _validate_context(
                contexts.get(f"{job['competition_id']}:{job['event_id']}"),
                job["competition_id"],
                job["event_id"],
            )
            if candidate["snapshot_id"] == item.get("component_snapshot_id"):
                context = candidate
        except ValueError:
            pass
        if context is None:
            context = _load_history_context(
                root=root,
                job=job,
                snapshot_id=item.get("component_snapshot_id"),
            )
        binding = {
            "current_snapshot_id": item["component_snapshot_id"],
            "aggregate_snapshot_id": item["aggregate_snapshot_id"],
            "publish_status": item["publish_status"],
            "current_decision": dict(context["match_decision"]),
        }
        if job.get("current_decision") is not None:
            if any(job.get(name) != value for name, value in binding.items()):
                raise ValueError("league_pre_match_receipt_binding_invalid")
            continue
        updated["receipts"][token] = {**dict(job), **binding}
        changed = True
    checked = _validate_state(updated)
    if changed:
        store.commit(checked)
    return checked


def _load_history_context(
    *,
    root: str | Path,
    job: Mapping[str, Any],
    snapshot_id: Any,
) -> dict[str, Any]:
    checked_snapshot_id = _safe_snapshot_id(snapshot_id)
    competition_id = job["competition_id"]
    event_id = job["event_id"]
    path = (
        Path(root)
        / "data/local/leagues"
        / competition_id
        / "history"
        / f"{checked_snapshot_id}.json"
    )
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("league_pre_match_receipt_binding_invalid") from exc
    competition = snapshot.get("competition") if isinstance(snapshot, Mapping) else None
    matches = snapshot.get("matches") if isinstance(snapshot, Mapping) else None
    if (
        not isinstance(competition, Mapping)
        or competition.get("id") != competition_id
        or snapshot.get("snapshot_id") != checked_snapshot_id
        or not isinstance(matches, list)
    ):
        raise ValueError("league_pre_match_receipt_binding_invalid")
    matching = [
        row for row in matches
        if isinstance(row, Mapping) and row.get("source_event_id") == event_id
    ]
    if len(matching) != 1:
        raise ValueError("league_pre_match_receipt_binding_invalid")
    row = matching[0]
    context = _validate_context(
        {
            "competition_id": competition_id,
            "event_id": event_id,
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "kickoff_at_utc": row.get("kickoff_at_utc"),
            "fixture_status": str(
                row.get("fixture_status") or row.get("status") or ""
            ).strip().upper(),
            "acceptance_active": True,
            "snapshot_id": checked_snapshot_id,
            "match_decision": row.get("match_decision"),
        },
        competition_id,
        event_id,
    )
    if any(
        context[name] != job[name]
        for name in ("home_team", "away_team", "kickoff_at_utc")
    ):
        raise ValueError("league_pre_match_receipt_binding_invalid")
    return context


def _published_evidence_from_state(
    root: str | Path, ack_key: Mapping[str, Any]
) -> dict[str, str] | None:
    try:
        state = PostLineupRefreshStateStore(root).read()
        row = state.get("receipts", {}).get(_ack_token(ack_key))
    except Exception:
        return None
    if isinstance(row, Mapping) and row.get("phase") == "published":
        status = row.get("publish_status")
        try:
            component_snapshot_id = _safe_snapshot_id(row.get("snapshot_id"))
            aggregate_snapshot_id = _safe_snapshot_id(row.get("aggregate_snapshot_id"))
        except ValueError:
            return None
        if status in {"stored", "duplicate"}:
            return {
                "publish_status": status,
                "component_snapshot_id": component_snapshot_id,
                "aggregate_snapshot_id": aggregate_snapshot_id,
            }
    return None


def _notification_projection(event: Mapping[str, Any], result: Any) -> dict[str, Any]:
    status = result.get("status") if isinstance(result, Mapping) else "failed"
    if status not in {"already_sent", "dry_run", "failed", "sent", "skipped"}:
        status = "failed"
    return {
        "event_type": event["event_type"],
        "event_fingerprint": event["event_fingerprint"],
        "status": status,
    }


def _deliver_event(
    outbox: Any,
    event: Mapping[str, Any],
    *,
    notifier: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], bool]:
    try:
        result = outbox.deliver(event, notify=True, notifier=notifier)
    except Exception:
        return _notification_projection(event, None), False
    if (
        not isinstance(result, Mapping)
        or set(result) != {"status", "event_fingerprint"}
        or result.get("status") not in {"already_sent", "failed", "sent"}
        or result.get("event_fingerprint") != event["event_fingerprint"]
    ):
        return _notification_projection(event, None), False
    durable = result["status"] in {"already_sent", "sent"}
    if result["status"] == "failed":
        path = getattr(outbox, "path", None)
        try:
            pending = _read_notification_state(Path(path))["pending"]
            durable = event["event_fingerprint"] in pending
        except Exception:
            durable = False
    return _notification_projection(event, result), durable


def _retry_notifications(
    outbox: Any,
    *,
    notifier: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        value = outbox.retry_pending(notify=True, notifier=notifier)
    except Exception:
        return {"status": "failed"}
    if (
        not isinstance(value, Mapping)
        or set(value) != {"status", "sent", "failed"}
        or value.get("status") != "complete"
        or any(
            isinstance(value.get(name), bool)
            or not isinstance(value.get(name), int)
            or value[name] < 0
            for name in ("sent", "failed")
        )
    ):
        return {"status": "failed"}
    return {
        "status": "complete",
        "sent": value["sent"],
        "failed": value["failed"],
    }


def _source_failure_event_for_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    return build_source_failure_event(
        competition_id=episode["competition_id"],
        event_id=episode["event_id"],
        home_team=episode["home_team"],
        away_team=episode["away_team"],
        kickoff_at_utc=episode["kickoff_at_utc"],
        source_fingerprint=episode["episode_id"],
        failure_count=episode["failure_count"],
        failure_threshold=episode["failure_threshold"],
    )


def _source_recovery_event_for_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    return build_source_recovery_event(
        competition_id=episode["competition_id"],
        event_id=episode["event_id"],
        home_team=episode["home_team"],
        away_team=episode["away_team"],
        kickoff_at_utc=episode["kickoff_at_utc"],
        source_fingerprint=episode["episode_id"],
    )


def _source_notification_is_current(
    event: Mapping[str, Any], state: Mapping[str, Any]
) -> bool:
    event_type = event.get("event_type")
    if event_type not in {"sustained_source_failure", "source_recovery"}:
        return True
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    competition_id = payload.get("competition_id")
    event_id = payload.get("event_id")
    if not isinstance(competition_id, str) or not isinstance(event_id, str):
        return False
    episode = state.get("source_episodes", {}).get(
        f"{competition_id}:{event_id}"
    )
    if (
        not isinstance(episode, Mapping)
        or episode.get("active") is not True
        or payload.get("source_fingerprint") != episode.get("episode_id")
    ):
        return False
    fingerprint = event.get("event_fingerprint")
    if event_type == "sustained_source_failure":
        return (
            episode.get("failure_pending") is True
            and fingerprint == episode.get("failure_notification_fingerprint")
        )
    return (
        episode.get("failure_notified") is True
        and episode.get("recovery_pending") is True
        and fingerprint == episode.get("recovery_notification_fingerprint")
    )


def _notification_paths(outbox: Any) -> tuple[Path, Path] | None:
    path = getattr(outbox, "path", None)
    lock_path = getattr(outbox, "lock_path", None)
    if not isinstance(path, (str, Path)) or not isinstance(lock_path, (str, Path)):
        return None
    return Path(path), Path(lock_path)


def _read_outbox_state(outbox: Any) -> dict[str, Any] | None:
    paths = _notification_paths(outbox)
    if paths is None:
        return None
    path, lock_path = paths
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _read_notification_state(path)


def _reconcile_source_notification_receipts(
    *, state: Mapping[str, Any], outbox: Any
) -> dict[str, Any]:
    notification_state = _read_outbox_state(outbox)
    if notification_state is None:
        return _validate_state(state)
    sent = notification_state["sent"]
    updated = {
        "schema_version": 1,
        "receipts": {
            key: dict(value) for key, value in state["receipts"].items()
        },
        "source_episodes": {
            key: dict(value) for key, value in state["source_episodes"].items()
        },
    }
    for key, episode in updated["source_episodes"].items():
        failure_fingerprint = episode.get("failure_notification_fingerprint")
        recovery_fingerprint = episode.get("recovery_notification_fingerprint")
        if (
            episode.get("active") is True
            and episode.get("failure_pending") is True
            and failure_fingerprint in sent
        ):
            episode = {
                **episode,
                "failure_pending": False,
                "failure_notified": True,
            }
        if (
            episode.get("active") is True
            and episode.get("failure_notified") is True
            and episode.get("recovery_pending") is True
            and recovery_fingerprint in sent
        ):
            episode = {
                **episode,
                "active": False,
                "recovery_pending": False,
            }
        updated["source_episodes"][key] = episode
    return _validate_state(updated)


def _prune_superseded_source_pending(
    *, state: Mapping[str, Any], outbox: Any
) -> int:
    paths = _notification_paths(outbox)
    if paths is None:
        return 0
    path, lock_path = paths
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        notification_state = _read_notification_state(path)
        retained = {
            fingerprint: event
            for fingerprint, event in notification_state["pending"].items()
            if _source_notification_is_current(event, state)
        }
        removed = len(notification_state["pending"]) - len(retained)
        if removed:
            _atomic_write_notification_state(
                path,
                {
                    "schema_version": 1,
                    "pending": retained,
                    "sent": notification_state["sent"],
                },
            )
        return removed


def _notification_events_for_post(
    *,
    root: str | Path,
    post: Mapping[str, Any],
    state: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    events: list[tuple[dict[str, Any], str]] = []
    for item in post["acks"]["blocked"]:
        if item.get("reason") not in _QUOTA_BLOCK_REASONS:
            continue
        token = _ack_token(item["ack_key"])
        job = state["receipts"].get(token)
        if not isinstance(job, Mapping):
            continue
        event = build_quota_blocked_event(
            competition_id=job["competition_id"],
            event_id=job["event_id"],
            home_team=job["home_team"],
            away_team=job["away_team"],
            kickoff_at_utc=job["kickoff_at_utc"],
            source_fingerprint=job["lineup_fingerprint"],
        )
        events.append((event, token))
    return events


def _notification_events_for_bound_receipts(
    state: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    events: list[tuple[dict[str, Any], str]] = []
    for token, job in sorted(state["receipts"].items()):
        if job.get("current_decision") is None:
            continue
        event = build_published_refresh_event(
            competition_id=job["competition_id"],
            event_id=job["event_id"],
            home_team=job["home_team"],
            away_team=job["away_team"],
            kickoff_at_utc=job["kickoff_at_utc"],
            lineup_fingerprint=job["lineup_fingerprint"],
            confirmed_at=job["fetched_at"],
            publish_status=job["publish_status"],
            previous_decision=job["previous_decision"],
            current_decision=job["current_decision"],
        )
        if event is not None:
            events.append((event, token))
    return events


def _confirmed_keys(root: str | Path) -> set[str]:
    path = Path(root) / "data/local/leagues/lineup_state.json"
    if not path.exists():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        events = _validate_lineup_state(value)["events"]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_lineup_state") from exc
    return {
        key for key, row in events.items() if row["confirmed"] is True
    }


def _in_window(context: Mapping[str, Any], now: datetime, minutes: int) -> bool:
    delta = (_utc(context["kickoff_at_utc"]) - now).total_seconds()
    return 0 < delta <= minutes * 60


def _update_source_episodes(
    *,
    state: Mapping[str, Any],
    lineups: Mapping[str, Any],
    contexts: Mapping[str, Any],
    now: datetime,
    configured_threshold: int,
) -> dict[str, Any]:
    updated = {
        "schema_version": 1,
        "receipts": {key: dict(value) for key, value in state["receipts"].items()},
        "source_episodes": {key: dict(value) for key, value in state["source_episodes"].items()},
    }
    evidence = {
        f"{row['competition_id']}:{row['event_id']}": row["outcome"]
        for row in lineups["source_events"]
    }
    for key, context in sorted(contexts.items()):
        if not _in_window(context, now, 45):
            continue
        competition_id = context["competition_id"]
        current = updated["source_episodes"].get(key)
        outcome = evidence.get(key)
        if outcome == "failed":
            if not isinstance(current, Mapping) or current.get("active") is not True:
                previous_episode_id = (
                    current.get("episode_id") if isinstance(current, Mapping) else ""
                )
                previous_generation = (
                    int(current.get("generation", 0))
                    if isinstance(current, Mapping)
                    else 0
                )
                generation = previous_generation + 1
                current = {
                    "competition_id": competition_id,
                    "event_id": context["event_id"],
                    "episode_id": _digest(
                        f"{key}|{generation}|{now.isoformat()}|{previous_episode_id}"
                    ),
                    "generation": generation,
                    "failure_count": 0,
                    "failure_threshold": configured_threshold,
                    "active": True,
                    "failure_pending": False,
                    "failure_notified": False,
                    "recovery_pending": False,
                    "failure_notification_fingerprint": None,
                    "recovery_notification_fingerprint": None,
                    "home_team": context["home_team"],
                    "away_team": context["away_team"],
                    "kickoff_at_utc": context["kickoff_at_utc"],
                    "started_at": now.isoformat(),
                    "last_failure_at": now.isoformat(),
                }
            current = {
                **dict(current),
                "failure_count": int(current["failure_count"]) + 1,
                "recovery_pending": False,
                "last_failure_at": now.isoformat(),
            }
            if (
                current["failure_count"] >= current["failure_threshold"]
                and current.get("failure_notified") is not True
            ):
                current["failure_pending"] = True
            if current["failure_count"] >= current["failure_threshold"]:
                failure_event = _source_failure_event_for_episode(current)
                current["failure_notification_fingerprint"] = failure_event[
                    "event_fingerprint"
                ]
            updated["source_episodes"][key] = current
        elif (
            outcome == "succeeded"
            and isinstance(current, Mapping)
            and current.get("active") is True
        ):
            if (
                current.get("failure_pending") is True
                or current.get("failure_notified") is True
            ):
                recovery = {
                    **dict(current),
                    "recovery_pending": True,
                }
                recovery_event = _source_recovery_event_for_episode(recovery)
                recovery["recovery_notification_fingerprint"] = recovery_event[
                    "event_fingerprint"
                ]
                updated["source_episodes"][key] = recovery
            else:
                updated["source_episodes"][key] = {
                    **dict(current),
                    "active": False,
                    "recovery_pending": False,
                }
    return _validate_state(updated)


def _pending_failure_events(
    state: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    events: list[tuple[dict[str, Any], str]] = []
    for key, episode in sorted(state["source_episodes"].items()):
        if episode.get("active") is not True or episode.get("failure_pending") is not True:
            continue
        event = _source_failure_event_for_episode(episode)
        if (
            event["event_fingerprint"]
            != episode.get("failure_notification_fingerprint")
        ):
            raise ValueError("league_pre_match_state_invalid")
        events.append((event, key))
    return events


def _pending_recovery_events(
    state: Mapping[str, Any],
) -> list[tuple[dict[str, Any], str]]:
    events: list[tuple[dict[str, Any], str]] = []
    for key, episode in sorted(state["source_episodes"].items()):
        if (
            episode.get("active") is not True
            or episode.get("failure_notified") is not True
            or episode.get("recovery_pending") is not True
        ):
            continue
        event = _source_recovery_event_for_episode(episode)
        if (
            event["event_fingerprint"]
            != episode.get("recovery_notification_fingerprint")
        ):
            raise ValueError("league_pre_match_state_invalid")
        events.append((event, key))
    return events


def _missing_events(
    root: str | Path,
    contexts: Mapping[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    confirmed = _confirmed_keys(root)
    events: list[dict[str, Any]] = []
    for key, context in sorted(contexts.items()):
        if (
            key in confirmed
            or context.get("acceptance_active") is not True
            or context.get("fixture_status") not in _MISSING_ELIGIBLE_FIXTURE_STATUSES
            or not _in_window(context, now, 20)
        ):
            continue
        events.append(build_missing_lineup_event(
            competition_id=context["competition_id"],
            event_id=context["event_id"],
            home_team=context["home_team"],
            away_team=context["away_team"],
            kickoff_at_utc=context["kickoff_at_utc"],
            source_fingerprint=_digest(f"missing|{key}|{context['kickoff_at_utc']}"),
        ))
    return events


def _call_post(
    fn: Callable[..., Mapping[str, Any]],
    *,
    root: str | Path,
    now: str,
    receipts: Mapping[str, Any],
    live: bool,
    env_loader: Callable[[], Any] | None,
    quota_loader: Callable[[], Any] | None,
    odds_fetcher: Callable[..., Any] | None,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    identity_registry: Any,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "root": root,
        "now": now,
        "newly_confirmed": receipts,
        "live": live,
    }
    if env_loader is not None:
        kwargs["env_loader"] = env_loader
    if quota_loader is not None:
        kwargs["quota_loader"] = quota_loader
    if odds_fetcher is not None:
        kwargs["odds_fetcher"] = odds_fetcher
    if publish_fn is not None:
        kwargs["publish_fn"] = publish_fn
    if identity_registry is not None:
        kwargs["identity_registry"] = identity_registry
    return _validate_post_result(
        fn(**kwargs),
        submitted=receipts,
        root=root,
    )


def run_league_pre_match(
    *,
    root: str | Path,
    now: Any,
    live_lineups: bool = False,
    write_lineups: bool = False,
    refresh_after_lineups: bool = False,
    live_refresh: bool = False,
    refresh_guard: bool = False,
    publish: bool = False,
    notify: bool = False,
    source_failure_threshold: int = DEFAULT_SOURCE_FAILURE_THRESHOLD,
    lineup_refresh_fn: Callable[..., Mapping[str, Any]] = run_league_lineups_refresh,
    post_lineup_refresh_fn: Callable[..., Mapping[str, Any]] = run_post_lineup_refresh,
    match_context_loader: Callable[[str | Path], Mapping[str, Any]] = _load_match_contexts,
    state_store_factory: Callable[[str | Path], Any] = LeaguePreMatchStateStore,
    outbox_factory: Callable[[str | Path], Any] = LeagueLineupNotificationOutbox,
    env_loader: Callable[[], Any] | None = None,
    quota_loader: Callable[[], Any] | None = None,
    odds_fetcher: Callable[..., Any] | None = None,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    notifier: Callable[..., Mapping[str, Any]] = send_wxpusher_notification,
    identity_registry: Any = None,
    live_preflight: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _flags_are_safe(
        live_lineups=live_lineups,
        write_lineups=write_lineups,
        refresh_after_lineups=refresh_after_lineups,
        live_refresh=live_refresh,
        refresh_guard=refresh_guard,
        publish=publish,
        notify=notify,
    ):
        return {"status": "blocked", "reason": "unsafe_flag_combination", "lock": "not_acquired"}
    try:
        now_dt = _utc(now)
    except ValueError:
        return {"status": "blocked", "reason": "invalid_now", "lock": "not_acquired"}
    if (
        isinstance(source_failure_threshold, bool)
        or not isinstance(source_failure_threshold, int)
        or source_failure_threshold < 1
    ):
        return {"status": "blocked", "reason": "invalid_failure_threshold", "lock": "not_acquired"}

    if not live_lineups:
        try:
            lineups = _validate_lineup_result(lineup_refresh_fn(
                root=root, now=now_dt.isoformat(), live=False, write=False
            ))
        except Exception:
            return {
                "status": "lineup_failed",
                "reason": "lineup_result_invalid",
                "lock": "not_required",
                "notifications": [],
            }
        return {
            "status": "dry_run",
            "lock": "not_required",
            "lineups": _project_lineups(lineups),
            "pending_retry": None,
            "post_lineup_refresh": None,
            "notification_retry": None,
            "notifications": [],
        }

    lock_path = Path(root) / DEFAULT_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "status": "locked",
                "reason": "single_instance_lock_contended",
                "lock": "contended",
            }

        if live_refresh and live_preflight is not None:
            try:
                preflight = live_preflight()
            except Exception:
                preflight = None
            if not isinstance(preflight, Mapping) or preflight.get("status") != "ready":
                reason = (
                    _safe_reason(preflight.get("reason"), "live_preflight_failed")
                    if isinstance(preflight, Mapping)
                    else "live_preflight_failed"
                )
                return {
                    "status": "blocked",
                    "reason": reason,
                    "lock": "acquired",
                    "notifications": [],
                }

        notifications: list[dict[str, Any]] = []
        notification_retry = None
        state_store = None
        state = _empty_state()
        contexts_current: dict[str, dict[str, Any]] = {}
        if notify:
            try:
                state_store = state_store_factory(root)
                state = _validate_state(state_store.read())
                contexts_current = _eligible_contexts(
                    _validate_contexts(match_context_loader(root)), now_dt
                )
            except Exception:
                return {
                    "status": "state_failed",
                    "reason": "receipt_state_invalid",
                    "lock": "acquired",
                    "notifications": [],
                }

        try:
            task4_pending = _pending_deliveries(_read_pending(root))
            task7_pending = _jobs_as_receipts(state) if notify else {}
            pending = _merge_receipts(task4_pending, task7_pending)
        except Exception:
            return {
                "status": "pending_failed",
                "reason": "lineup_pending_invalid",
                "lock": "acquired",
                "notifications": notifications,
            }

        pending_result = None
        post_results: list[dict[str, Any]] = []
        attempted_tokens: set[str] = set()
        if refresh_after_lineups and pending:
            if notify:
                try:
                    state = _stage_jobs(
                        store=state_store,
                        state=state,
                        receipts=pending,
                        contexts=contexts_current,
                    )
                except Exception:
                    return {
                        "status": "state_failed",
                        "reason": "receipt_state_commit_failed",
                        "lock": "acquired",
                        "notifications": notifications,
                    }
            try:
                pending_result = _call_post(
                    post_lineup_refresh_fn,
                    root=root,
                    now=now_dt.isoformat(),
                    receipts=pending,
                    live=live_refresh,
                    env_loader=env_loader,
                    quota_loader=quota_loader,
                    odds_fetcher=odds_fetcher,
                    publish_fn=publish_fn if publish else None,
                    identity_registry=identity_registry,
                )
            except Exception:
                return {
                    "status": "post_refresh_failed",
                    "reason": "post_lineup_result_invalid",
                    "lock": "acquired",
                    "notifications": notifications,
                }
            if notify:
                try:
                    contexts_current = _eligible_contexts(
                        _validate_contexts(match_context_loader(root)), now_dt
                    )
                    state = _bind_published_jobs(
                        root=root,
                        store=state_store,
                        state=state,
                        post=pending_result,
                        contexts=contexts_current,
                    )
                except Exception:
                    return {
                        "status": "state_failed",
                        "reason": "receipt_binding_commit_failed",
                        "lock": "acquired",
                        "notifications": notifications,
                    }
            post_results.append(pending_result)
            attempted_tokens.update(_tokens(pending))

        try:
            lineups = _validate_lineup_result(lineup_refresh_fn(
                root=root, now=now_dt.isoformat(), live=True, write=True
            ))
        except Exception:
            return {
                "status": "lineup_failed",
                "reason": "lineup_result_invalid",
                "lock": "acquired",
                "notifications": notifications,
            }

        is_write_failure = lineups.get("reason") in {"cache_commit_failed", "state_commit_failed"}
        new_receipts = _without_tokens(lineups["newly_confirmed"], attempted_tokens)
        post_result = None
        if refresh_after_lineups and new_receipts and not is_write_failure:
            if notify:
                try:
                    state = _stage_jobs(
                        store=state_store,
                        state=state,
                        receipts=new_receipts,
                        contexts=contexts_current,
                    )
                except Exception:
                    return {
                        "status": "state_failed",
                        "reason": "receipt_state_commit_failed",
                        "lock": "acquired",
                        "notifications": notifications,
                    }
            try:
                post_result = _call_post(
                    post_lineup_refresh_fn,
                    root=root,
                    now=now_dt.isoformat(),
                    receipts=new_receipts,
                    live=live_refresh,
                    env_loader=env_loader,
                    quota_loader=quota_loader,
                    odds_fetcher=odds_fetcher,
                    publish_fn=publish_fn if publish else None,
                    identity_registry=identity_registry,
                )
            except Exception:
                return {
                    "status": "post_refresh_failed",
                    "reason": "post_lineup_result_invalid",
                    "lock": "acquired",
                    "notifications": notifications,
                }
            if notify:
                try:
                    contexts_current = _eligible_contexts(
                        _validate_contexts(match_context_loader(root)), now_dt
                    )
                    state = _bind_published_jobs(
                        root=root,
                        store=state_store,
                        state=state,
                        post=post_result,
                        contexts=contexts_current,
                    )
                except Exception:
                    return {
                        "status": "state_failed",
                        "reason": "receipt_binding_commit_failed",
                        "lock": "acquired",
                        "notifications": notifications,
                    }
            post_results.append(post_result)

        if notify:
            try:
                outbox = outbox_factory(root)
            except Exception:
                outbox = None
            source_notification_ready = outbox is not None
            if outbox is not None:
                try:
                    reconciled_state = _reconcile_source_notification_receipts(
                        state=state,
                        outbox=outbox,
                    )
                    if reconciled_state != state:
                        state_store.commit(reconciled_state)
                        state = reconciled_state
                except Exception:
                    source_notification_ready = False

            try:
                updated_state = _update_source_episodes(
                    state=state,
                    lineups=lineups,
                    contexts=contexts_current,
                    now=now_dt,
                    configured_threshold=source_failure_threshold,
                )
                state_store.commit(updated_state)
                state = updated_state
            except Exception:
                source_notification_ready = False

            if source_notification_ready:
                try:
                    _prune_superseded_source_pending(state=state, outbox=outbox)
                except Exception:
                    source_notification_ready = False
            notification_retry = (
                _retry_notifications(outbox, notifier=notifier)
                if source_notification_ready
                else {"status": "failed"}
            )

            completed_tokens: set[str] = set()
            receipt_events: list[tuple[dict[str, Any], str]] = []
            for checked_post in post_results:
                try:
                    receipt_events.extend(_notification_events_for_post(
                        root=root,
                        post=checked_post,
                        state=state,
                    ))
                except Exception:
                    notification_retry = {"status": "failed"}
            try:
                receipt_events.extend(_notification_events_for_bound_receipts(state))
            except Exception:
                notification_retry = {"status": "failed"}
            for event, token in receipt_events:
                if outbox is None:
                    projected, durable_outbox = _notification_projection(event, None), False
                else:
                    projected, durable_outbox = _deliver_event(
                        outbox, event, notifier=notifier
                    )
                notifications.append(projected)
                if durable_outbox:
                    completed_tokens.add(token)
            if completed_tokens:
                candidate = {
                    **state,
                    "receipts": {
                        token: job for token, job in state["receipts"].items()
                        if token not in completed_tokens
                    },
                }
                try:
                    checked_candidate = _validate_state(candidate)
                    state_store.commit(checked_candidate)
                    state = checked_candidate
                except Exception:
                    notification_retry = {"status": "failed"}

            try:
                failure_events = _pending_failure_events(state)
            except Exception:
                failure_events = []
                notification_retry = {"status": "failed"}
            failure_notified_keys: set[str] = set()
            for event, episode_key in failure_events:
                if outbox is None:
                    projected = _notification_projection(event, None)
                else:
                    projected, _durable_outbox = _deliver_event(
                        outbox, event, notifier=notifier
                    )
                notifications.append(projected)
                if projected["status"] in {"already_sent", "sent"}:
                    failure_notified_keys.add(episode_key)
            if failure_notified_keys:
                candidate = {
                    **state,
                    "source_episodes": {
                        key: (
                            {
                                **episode,
                                "failure_pending": False,
                                "failure_notified": True,
                            }
                            if key in failure_notified_keys else episode
                        )
                        for key, episode in state["source_episodes"].items()
                    },
                }
                try:
                    checked_candidate = _validate_state(candidate)
                    state_store.commit(checked_candidate)
                    state = checked_candidate
                except Exception:
                    notification_retry = {"status": "failed"}

            try:
                missing_events = _missing_events(root, contexts_current, now_dt)
            except Exception:
                missing_events = []
                notification_retry = {"status": "failed"}
            for event in missing_events:
                if outbox is None:
                    projected = _notification_projection(event, None)
                else:
                    projected, _durable = _deliver_event(outbox, event, notifier=notifier)
                notifications.append(projected)

            recovered_keys: set[str] = set()
            try:
                recovery_events = _pending_recovery_events(state)
            except Exception:
                recovery_events = []
                notification_retry = {"status": "failed"}
            for event, episode_key in recovery_events:
                if outbox is None:
                    projected, durable_outbox = _notification_projection(event, None), False
                else:
                    projected, durable_outbox = _deliver_event(
                        outbox, event, notifier=notifier
                    )
                notifications.append(projected)
                if projected["status"] in {"already_sent", "sent"}:
                    recovered_keys.add(episode_key)
            if recovered_keys:
                candidate = {
                    **state,
                    "source_episodes": {
                        key: (
                            {**episode, "active": False, "recovery_pending": False}
                            if key in recovered_keys else episode
                        )
                        for key, episode in state["source_episodes"].items()
                    },
                }
                try:
                    checked_candidate = _validate_state(candidate)
                    state_store.commit(checked_candidate)
                    state = checked_candidate
                except Exception:
                    notification_retry = {"status": "failed"}

        if is_write_failure:
            final_status = "lineup_failed"
            final_reason = lineups.get("reason") or "lineup_failed"
        elif post_result is not None:
            final_status = post_result["status"]
            final_reason = None
        elif pending_result is not None:
            final_status = pending_result["status"]
            final_reason = None
        elif lineups["status"] == "error":
            final_status = "lineup_failed"
            final_reason = lineups.get("reason") or "lineup_failed"
        else:
            final_status = "lineups_checked"
            final_reason = None
        return {
            "status": final_status,
            **({"reason": final_reason} if final_reason else {}),
            "lock": "acquired",
            "lineups": _project_lineups(lineups),
            "pending_retry": _project_post(pending_result) if pending_result else None,
            "post_lineup_refresh": _project_post(post_result) if post_result else None,
            "notification_retry": notification_retry,
            "notifications": notifications,
        }


run_league_pre_match_cycle = run_league_pre_match


def _cli_odds_fetcher(
    *, root: str | Path, quota_path: str | Path, observed_at: str
) -> Callable[[str, Mapping[str, str]], Any]:
    def fetch(sport_key: str, env: Mapping[str, str]) -> Any:
        slots = configured_key_slots(env)
        if len(slots) != 1:
            raise ValueError("selected_quota_slot_invalid")
        selected = slots[0]
        result = fetch_odds_for_sport(
            api_key=selected.api_key,
            sport_key=sport_key,
            quota_path=Path(root) / quota_path,
            observed_at=observed_at,
            quota_provider=selected.provider,
            markets=DEFAULT_MARKETS,
        )
        if not isinstance(result.json_body, list):
            raise ValueError("odds_payload_invalid")
        return result.json_body

    return fetch


def _cli_publisher(
    *, env_path: str | Path, endpoint: str, observed_at: str
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    def publish(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        env = _load_env(env_path)
        secret = env.get("INGEST_HMAC_SECRET")
        if not secret or endpoint == DEFAULT_ENDPOINT:
            return {"status": "failed"}
        request = build_ingest_request(
            snapshot=dict(snapshot), endpoint=endpoint, secret=secret, timestamp=observed_at
        )
        try:
            response = _default_sender(request)
        except Exception:
            return {"status": "failed"}
        if not isinstance(response, Mapping) or response.get("http_status") not in range(200, 300):
            return {"status": "failed"}
        try:
            body = json.loads(str(response.get("body") or ""))
        except json.JSONDecodeError:
            return {"status": "failed"}
        status = body.get("status") if isinstance(body, Mapping) else None
        return {"status": status if status in {"stored", "duplicate"} else "failed"}

    return publish


def _cli_live_preflight(
    *, env_loader: Callable[[], Mapping[str, str]], endpoint: str
) -> Callable[[], Mapping[str, Any]]:
    def check() -> Mapping[str, Any]:
        if endpoint == DEFAULT_ENDPOINT or not endpoint.startswith("https://"):
            return {"status": "blocked", "reason": "publish_endpoint_invalid"}
        env = env_loader()
        if not isinstance(env, Mapping):
            return {"status": "blocked", "reason": "live_env_invalid"}
        secret = env.get("INGEST_HMAC_SECRET")
        try:
            validate_hmac_secret(secret)
        except ValueError:
            return {"status": "blocked", "reason": "publish_secret_invalid"}
        return {"status": "ready"}

    return check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the six-league confirmed-lineup pre-match cycle. Defaults to a zero-side-effect dry-run."
        )
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--now", default=None)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--quota-path", default=str(DEFAULT_QUOTA_RELATIVE_PATH))
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--source-failure-threshold", type=int, default=DEFAULT_SOURCE_FAILURE_THRESHOLD)
    parser.add_argument("--live-lineups", action="store_true")
    parser.add_argument("--write-lineups", action="store_true")
    parser.add_argument("--refresh-after-lineups", action="store_true")
    parser.add_argument("--live-refresh", action="store_true")
    parser.add_argument("--refresh-guard", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    observed_at = args.now or _now_utc_iso()
    root = Path(args.root)
    env_path = root / args.env
    quota_path = Path(args.quota_path)
    env_cache: dict[str, Mapping[str, str]] = {}

    def load_live_env() -> Mapping[str, str]:
        if "value" not in env_cache:
            env_cache["value"] = _load_env(env_path)
        return env_cache["value"]

    result = run_league_pre_match(
        root=root,
        now=observed_at,
        live_lineups=args.live_lineups,
        write_lineups=args.write_lineups,
        refresh_after_lineups=args.refresh_after_lineups,
        live_refresh=args.live_refresh,
        refresh_guard=args.refresh_guard,
        publish=args.publish,
        notify=args.notify,
        source_failure_threshold=args.source_failure_threshold,
        env_loader=load_live_env,
        quota_loader=lambda: load_quota_ledger(root / quota_path),
        odds_fetcher=_cli_odds_fetcher(
            root=root, quota_path=quota_path, observed_at=observed_at
        ),
        publish_fn=_cli_publisher(
            env_path=env_path, endpoint=args.endpoint, observed_at=observed_at
        ),
        identity_registry=accepted_league_team_identity_registry(),
        live_preflight=_cli_live_preflight(
            env_loader=load_live_env, endpoint=args.endpoint
        ),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result.get("status") == "blocked" and result.get("reason") == "unsafe_flag_combination" else 0


if __name__ == "__main__":
    raise SystemExit(main())
