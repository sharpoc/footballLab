from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from worldcup.league_odds_refresh import run_league_odds_refresh
from worldcup.league_runner import build_league_snapshot_from_cache
from worldcup.local_runner import write_snapshot
from worldcup.publish import publish_snapshot
from worldcup.publish_outbox import attempt_publish, load_pending_publish
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.scheduler import make_run_id
from worldcup.csl_closing_coverage import closing_archive_candidates
from worldcup.csl_closing_coverage_runner import run_closing_coverage
from worldcup.csl_eval_data import load_snapshots
from worldcup.csl_results_refresh import run_csl_results_refresh
from worldcup.csl_postmatch_shadow import run_csl_postmatch_shadow
from worldcup.csl_postmatch_sentinel import run_csl_postmatch_sentinel
from worldcup.csl_snapshot_archive import archive_snapshot
from worldcup.theoddsapi_keys import LOW_QUOTA_SWITCH_THRESHOLD

DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SPORT_KEY = "soccer_china_superleague"
DEFAULT_ENDPOINT = "https://football.celab.xin/api/ingest/snapshot"
DEFAULT_MIN_INTERVAL_SECONDS = 30 * 60
DEFAULT_DISCOVERY_INTERVAL_SECONDS = 24 * 3600
PICK_EXPIRY_REFRESH_LEAD_SECONDS = 20 * 60
QUOTA_LOW_REMAINING = 30
CSL_ANCHORS = (
    (90 * 60, "T-90", "赛前90分钟"),
    (25 * 60, "T-25", "赛前25分钟"),
)
LOW_QUOTA_ANCHORS = {"T-25"}
KNOWN_QUOTA_PROVIDERS = (
    "theoddsapi_primary",
    "theoddsapi_secondary",
    "theoddsapi_tertiary",
    "theoddsapi",
)

EnvLoader = Callable[[str | Path], dict[str, str]]
RefreshFn = Callable[..., dict[str, Any]]
SnapshotBuilder = Callable[..., dict[str, Any]]
PublishFn = Callable[..., dict[str, Any]]
ResultsRefreshFn = Callable[..., dict[str, Any]]
ArchiveFn = Callable[..., dict[str, Any]]
PostmatchShadowFn = Callable[..., dict[str, Any]]
PostmatchSentinelFn = Callable[..., dict[str, Any]]
ClosingCoverageFn = Callable[..., dict[str, Any]]

LOCAL_POLICY_FIELDS = {
    "closing_coverage_candidates",
    "closing_coverage_quality",
}
COVERAGE_OPERATION_ISSUES = {
    "quota_blocked",
    "provider_refresh_failed",
    "snapshot_archive_failed",
    "archive_validation_failed",
}
CLOSING_COVERAGE_STATUSES = {
    "stored",
    "unchanged",
    "dry_run",
    "blocked",
    "error",
    "stored_pending_cleanup",
    "unchanged_pending_cleanup",
}
CLOSING_COVERAGE_REASONS = {
    "coverage_report_invalid",
    "coverage_pending_invalid",
    "coverage_pending_commit_failed",
    "coverage_inputs_unavailable",
    "coverage_pending_owner_changed",
    "coverage_pending_cleanup_failed",
    "coverage_report_commit_failed",
    "coverage_path_conflict",
    "coverage_generated_at_invalid",
    "coverage_runner_failed",
}
POSTMATCH_SENTINEL_STATUSES = {
    "dry_run_ready",
    "stored",
    "unchanged",
    "error",
}
POSTMATCH_SENTINEL_REASONS = {
    "shadow_report_invalid",
    "coverage_report_invalid",
    "report_generated_at_invalid",
    "report_fingerprint_invalid",
    "coverage_shadow_mismatch",
    "sentinel_state_unreadable",
    "sentinel_state_write_failed",
}
POSTMATCH_SENTINEL_NOTIFICATION_STATUSES = {
    "not_attempted",
    "suppressed",
    "sent",
    "failed",
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Expected timezone-aware datetime: {value}")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _read_json_if_exists(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _last_refresh_at(snapshot: dict[str, Any]) -> str | None:
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    value = run.get("observed_at") or snapshot.get("snapshot_at")
    return str(value) if value else None


def _match_label(match: dict[str, Any]) -> str:
    home = str(match.get("home_team") or "").strip()
    away = str(match.get("away_team") or "").strip()
    return f"{home} vs {away}".strip()


def _match_id(match: dict[str, Any]) -> str:
    explicit = str(match.get("source_event_id") or match.get("match_id") or "").strip()
    if explicit:
        return explicit
    return "|".join(
        str(match.get(key) or "").strip()
        for key in ("kickoff_at_utc", "home_team", "away_team")
    )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _quota_remaining(quota_path: str | Path) -> int | None:
    try:
        providers = load_quota_ledger(quota_path).get("providers") or {}
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(providers, dict):
        return None
    for provider in KNOWN_QUOTA_PROVIDERS:
        entry = providers.get(provider)
        if not isinstance(entry, dict):
            continue
        remaining = _as_int(entry.get("remaining"))
        if remaining is not None and remaining > LOW_QUOTA_SWITCH_THRESHOLD:
            return remaining
    for provider in KNOWN_QUOTA_PROVIDERS:
        entry = providers.get(provider)
        if not isinstance(entry, dict):
            continue
        remaining = _as_int(entry.get("remaining"))
        if remaining is not None and remaining > 0:
            return remaining
    for provider in KNOWN_QUOTA_PROVIDERS:
        entry = providers.get(provider)
        if isinstance(entry, dict) and _as_int(entry.get("remaining")) is not None:
            return 0
    return None


def _allowed_anchors(quota_remaining: int | None) -> tuple[tuple[int, str, str], ...]:
    if quota_remaining is not None and 0 < quota_remaining <= QUOTA_LOW_REMAINING:
        return tuple(anchor for anchor in CSL_ANCHORS if anchor[1] in LOW_QUOTA_ANCHORS)
    return CSL_ANCHORS


def _future_matches(snapshot: dict[str, Any], now_dt: datetime) -> list[dict[str, Any]]:
    matches = snapshot.get("matches") if isinstance(snapshot.get("matches"), list) else []
    out = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        if str(match.get("fixture_status") or "").upper() == "POSTPONED":
            continue
        kickoff_raw = str(match.get("kickoff_at_utc") or "").strip()
        if not kickoff_raw:
            continue
        try:
            kickoff_dt = _parse_utc(kickoff_raw)
        except ValueError:
            continue
        if kickoff_dt > now_dt:
            out.append({"match": match, "kickoff_dt": kickoff_dt})
    return out


def _due_match_items(
    snapshot: dict[str, Any],
    now_dt: datetime,
    last_dt: datetime | None,
    quota_remaining: int | None,
) -> list[dict[str, Any]]:
    due = []
    for item in _future_matches(snapshot, now_dt):
        match = item["match"]
        kickoff_dt = item["kickoff_dt"]
        for offset_seconds, anchor, label in _allowed_anchors(quota_remaining):
            anchor_dt = kickoff_dt - timedelta(seconds=offset_seconds)
            if anchor_dt <= now_dt < kickoff_dt and (last_dt is None or last_dt < anchor_dt):
                due.append(
                    {
                        "match_id": _match_id(match),
                        "match_label": _match_label(match),
                        "kickoff_at_utc": _iso_utc(kickoff_dt),
                        "anchor": anchor,
                        "anchor_label": label,
                        "anchor_at": _iso_utc(anchor_dt),
                    }
                )
    return sorted(due, key=lambda item: (item["anchor_at"], item["kickoff_at_utc"], item["match_label"]))


def _next_due_at(
    snapshot: dict[str, Any],
    now_dt: datetime,
    last_dt: datetime | None,
    quota_remaining: int | None,
) -> str | None:
    candidates: list[datetime] = []
    for item in _future_matches(snapshot, now_dt):
        kickoff_dt = item["kickoff_dt"]
        for offset_seconds, _anchor, _label in _allowed_anchors(quota_remaining):
            anchor_dt = kickoff_dt - timedelta(seconds=offset_seconds)
            if anchor_dt > now_dt and (last_dt is None or last_dt < anchor_dt):
                candidates.append(anchor_dt)
    return _iso_utc(min(candidates)) if candidates else None


def _pick_expiry_items(
    snapshot: dict[str, Any],
    now_dt: datetime,
    last_dt: datetime | None,
    quota_remaining: int | None,
) -> list[dict[str, Any]]:
    if quota_remaining is not None and quota_remaining <= QUOTA_LOW_REMAINING:
        return []
    due: list[dict[str, Any]] = []
    for item in _future_matches(snapshot, now_dt):
        match = item["match"]
        decision = match.get("match_decision")
        if not isinstance(decision, dict) or decision.get("label") != "MATCH_PICK":
            continue
        valid_raw = str(decision.get("valid_until") or "").strip()
        if not valid_raw:
            continue
        try:
            valid_until = _parse_utc(valid_raw)
        except ValueError:
            continue
        guard_at = valid_until - timedelta(seconds=PICK_EXPIRY_REFRESH_LEAD_SECONDS)
        if last_dt is not None and last_dt >= guard_at:
            continue
        if guard_at <= now_dt:
            due.append(
                {
                    "match_id": _match_id(match),
                    "match_label": _match_label(match),
                    "kickoff_at_utc": _iso_utc(item["kickoff_dt"]),
                    "anchor": "PICK-EXPIRY",
                    "anchor_label": "首选鲜度保底",
                    "anchor_at": _iso_utc(guard_at),
                    "valid_until": _iso_utc(valid_until),
                }
            )
    return sorted(due, key=lambda value: (value["anchor_at"], value["kickoff_at_utc"]))


def _next_pick_expiry_due_at(
    snapshot: dict[str, Any],
    now_dt: datetime,
    last_dt: datetime | None,
    quota_remaining: int | None,
) -> str | None:
    if quota_remaining is not None and quota_remaining <= QUOTA_LOW_REMAINING:
        return None
    candidates: list[datetime] = []
    for item in _future_matches(snapshot, now_dt):
        decision = item["match"].get("match_decision")
        if not isinstance(decision, dict) or decision.get("label") != "MATCH_PICK":
            continue
        try:
            valid_until = _parse_utc(str(decision.get("valid_until") or ""))
        except ValueError:
            continue
        guard_at = valid_until - timedelta(seconds=PICK_EXPIRY_REFRESH_LEAD_SECONDS)
        if last_dt is None or last_dt < guard_at:
            candidates.append(max(now_dt, guard_at))
    return _iso_utc(min(candidates)) if candidates else None


def build_csl_publish_decision(
    *,
    snapshot: dict[str, Any],
    quota_remaining: int | None,
    now: str,
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    archived_snapshots: list[dict[str, Any]] | None = None,
    archive_history_status: str = "ok",
) -> dict[str, Any]:
    now_dt = _parse_utc(now)
    last_raw = _last_refresh_at(snapshot)
    last_dt = _parse_utc(last_raw) if last_raw else None
    anchor_due_matches = _due_match_items(snapshot, now_dt, last_dt, quota_remaining)
    expiry_due_matches = _pick_expiry_items(snapshot, now_dt, last_dt, quota_remaining)
    due_ids = {
        str(item.get("match_id") or "")
        for item in [*anchor_due_matches, *expiry_due_matches]
        if item.get("match_id")
    }
    if archive_history_status == "ok":
        coverage_candidates = closing_archive_candidates(
            snapshot=snapshot,
            archived_snapshots=archived_snapshots or [],
            due_match_ids=due_ids,
        )
        coverage_quality = {"history_status": "ok", "warning": None}
    else:
        coverage_candidates = []
        coverage_quality = {
            "history_status": "unreadable",
            "warning": "coverage_history_unreadable",
        }
    base = {
        "schema_version": 1,
        "competition_id": DEFAULT_COMPETITION_ID,
        "now": _iso_utc(now_dt),
        "last_refresh_at": _iso_utc(last_dt) if last_dt else None,
        "quota_remaining": quota_remaining,
        "min_interval_seconds": int(min_interval_seconds),
        "discovery_interval_seconds": int(discovery_interval_seconds),
        "anchors": [anchor for _offset, anchor, _label in _allowed_anchors(quota_remaining)],
        "closing_coverage_candidates": coverage_candidates,
        "closing_coverage_quality": coverage_quality,
    }
    if quota_remaining is not None and quota_remaining <= 0:
        return {
            **base,
            "should_refresh": False,
            "reason": "quota_exhausted",
            "due_matches": [],
            "next_due_at": None,
        }

    due_matches = [*anchor_due_matches, *expiry_due_matches]
    has_future = bool(_future_matches(snapshot, now_dt))
    discovery_due = False
    if not has_future:
        discovery_due = last_dt is None or now_dt - last_dt >= timedelta(
            seconds=discovery_interval_seconds
        )

    potential_refresh = bool(due_matches) or discovery_due
    if potential_refresh and last_dt is not None:
        elapsed = (now_dt - last_dt).total_seconds()
        if elapsed < min_interval_seconds:
            return {
                **base,
                "should_refresh": False,
                "reason": "global_throttle",
                "due_matches": due_matches,
                "next_due_at": _iso_utc(last_dt + timedelta(seconds=min_interval_seconds)),
                "throttle_remaining_seconds": int(min_interval_seconds - elapsed),
            }

    if anchor_due_matches:
        return {
            **base,
            "should_refresh": True,
            "reason": "match_anchor_due",
            "due_matches": due_matches,
            "next_due_at": _iso_utc(now_dt),
        }
    if expiry_due_matches:
        return {
            **base,
            "should_refresh": True,
            "reason": "pick_expiry_due",
            "due_matches": expiry_due_matches,
            "next_due_at": _iso_utc(now_dt),
        }
    if discovery_due:
        return {
            **base,
            "should_refresh": True,
            "reason": "discovery_due",
            "due_matches": [],
            "next_due_at": _iso_utc(now_dt),
        }
    anchor_next_due = _next_due_at(snapshot, now_dt, last_dt, quota_remaining)
    expiry_next_due = _next_pick_expiry_due_at(
        snapshot,
        now_dt,
        last_dt,
        quota_remaining,
    )
    next_candidates = [value for value in (anchor_next_due, expiry_next_due) if value]
    return {
        **base,
        "should_refresh": False,
        "reason": "not_due" if has_future else "discovery_not_due",
        "due_matches": [],
        "next_due_at": min(next_candidates) if next_candidates else None,
    }


def _safe_quota_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value[key]
        for key in ("remaining", "used", "last", "observed_at")
        if key in value and isinstance(value[key], (int, str)) and not isinstance(value[key], bool)
    }


def _validate_archive_history_snapshot(snapshot: Any) -> None:
    if type(snapshot) is not dict:
        raise ValueError("archive_history_snapshot_invalid")
    snapshot_at = snapshot.get("snapshot_at")
    if type(snapshot_at) is not str:
        raise ValueError("archive_history_snapshot_at_invalid")
    _parse_utc(snapshot_at)
    competition = snapshot.get("competition")
    if (
        type(competition) is not dict
        or competition.get("id") != DEFAULT_COMPETITION_ID
    ):
        raise ValueError("archive_history_competition_invalid")
    matches = snapshot.get("matches")
    if type(matches) is not list:
        raise ValueError("archive_history_matches_invalid")
    for match in matches:
        if type(match) is not dict:
            raise ValueError("archive_history_match_invalid")
        kickoff = match.get("kickoff_at_utc")
        home = match.get("home_canonical")
        away = match.get("away_canonical")
        match_competition = match.get("competition")
        if type(kickoff) is not str:
            raise ValueError("archive_history_match_kickoff_invalid")
        _parse_utc(kickoff)
        if type(home) is not str or not home.strip():
            raise ValueError("archive_history_match_home_invalid")
        if type(away) is not str or not away.strip():
            raise ValueError("archive_history_match_away_invalid")
        if (
            type(match_competition) is not dict
            or match_competition.get("id") != DEFAULT_COMPETITION_ID
        ):
            raise ValueError("archive_history_match_competition_invalid")


def _load_archive_history_safe(history_path: Path) -> dict[str, Any]:
    try:
        snapshots = load_snapshots(history_path)
        if type(snapshots) is not list:
            raise ValueError("archive_history_invalid")
        for snapshot in snapshots:
            _validate_archive_history_snapshot(snapshot)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "status": "unreadable",
            "warning": "coverage_history_unreadable",
            "snapshots": [],
        }
    return {"status": "ok", "warning": None, "snapshots": snapshots}


def _invalid_closing_coverage_result() -> dict[str, Any]:
    return {
        "status": "error",
        "reason": "invalid_closing_coverage_result",
        "error_type": "ValueError",
    }


def _stable_error_type(value: Any) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 128
        and value.isascii()
        and (value[0].isalpha() or value[0] == "_")
        and all(character.isalnum() or character in "._" for character in value)
    )


def _sha256_text(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_closing_coverage(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        return _invalid_closing_coverage_result()
    status = value.get("status")
    if type(status) is not str or status not in CLOSING_COVERAGE_STATUSES:
        return _invalid_closing_coverage_result()
    reason = value.get("reason")
    if reason is not None and (
        type(reason) is not str or reason not in CLOSING_COVERAGE_REASONS
    ):
        return _invalid_closing_coverage_result()
    competition_id = value.get("competition_id")
    if competition_id is not None and (
        type(competition_id) is not str
        or competition_id != DEFAULT_COMPETITION_ID
    ):
        return _invalid_closing_coverage_result()
    season = value.get("season")
    if season is not None and (type(season) is not str or season != "2026"):
        return _invalid_closing_coverage_result()
    fingerprint = value.get("input_fingerprint")
    if fingerprint is not None and not _sha256_text(fingerprint):
        return _invalid_closing_coverage_result()
    error_type = value.get("error_type")
    if error_type is not None and not _stable_error_type(error_type):
        return _invalid_closing_coverage_result()
    counts = {}
    for key in (
        "finished_result_count",
        "observed_closing_count",
        "observed_current_decision_count",
        "missing_count",
    ):
        item = value.get(key)
        if item is not None:
            if type(item) is not int or item < 0:
                return _invalid_closing_coverage_result()
            counts[key] = item
    sample_too_small = value.get("sample_too_small")
    if sample_too_small is not None and type(sample_too_small) is not bool:
        return _invalid_closing_coverage_result()
    safe: dict[str, Any] = {"status": status}
    for key, item in (
        ("reason", reason),
        ("competition_id", competition_id),
        ("season", season),
        ("input_fingerprint", fingerprint),
        ("error_type", error_type),
    ):
        if item is not None:
            safe[key] = item
    safe.update(counts)
    if sample_too_small is not None:
        safe["sample_too_small"] = sample_too_small
    return safe


def _coverage_audit_events(
    candidates: list[dict[str, Any]],
    *,
    observed_at: str,
    issue_code: str,
) -> list[dict[str, Any]]:
    if issue_code not in COVERAGE_OPERATION_ISSUES:
        return []
    events: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kickoff = candidate.get("kickoff_at_utc")
        home = candidate.get("home_canonical")
        away = candidate.get("away_canonical")
        if not kickoff or not home or not away:
            continue
        event = {
            "observed_at": observed_at,
            "match_id": str(candidate.get("match_id") or ""),
            "kickoff_at_utc": str(kickoff),
            "home_canonical": str(home),
            "away_canonical": str(away),
            "issue_code": issue_code,
        }
        key = (
            event["kickoff_at_utc"],
            event["home_canonical"],
            event["away_canonical"],
            issue_code,
        )
        prior = events.get(key)
        if prior is None or event["match_id"] < prior["match_id"]:
            events[key] = event
    return [events[key] for key in sorted(events)]


def _run_closing_coverage_safe(
    *,
    closing_coverage_fn: ClosingCoverageFn,
    closing_coverage_root: str | Path,
    history_path: Path,
    cache_dir: str | Path,
    observed_at: str,
    audit_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        value = closing_coverage_fn(
            root=closing_coverage_root,
            history=history_path,
            results_path=Path(cache_dir)
            / f"club_results_{DEFAULT_COMPETITION_ID}.csv",
            write=True,
            generated_at=observed_at,
            audit_events=audit_events or [],
        )
    except Exception as exc:
        return {
            "status": "error",
            "reason": "csl_closing_coverage_failed",
            "error_type": type(exc).__name__,
        }
    try:
        return _safe_closing_coverage(value)
    except Exception:
        return _invalid_closing_coverage_result()


def _safe_results_refresh(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_results_refresh_result"}
    safe: dict[str, Any] = {"status": str(value.get("status") or "error")}
    for key in ("reason", "latest_result_date", "error_type"):
        if value.get(key) is not None:
            safe[key] = str(value[key])
    for key in (
        "existing_matches",
        "verified_current_season_matches",
        "total_matches",
        "primary_matches",
        "check_matches",
        "verified_matches",
        "regression_count",
    ):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
            safe[key] = value[key]
    fixture_status = value.get("fixture_status")
    if isinstance(fixture_status, dict):
        safe_fixture_status: dict[str, Any] = {
            "status": str(fixture_status.get("status") or "error")
        }
        if fixture_status.get("reason") is not None:
            safe_fixture_status["reason"] = str(fixture_status["reason"])
        for key in ("active_matches", "postponed_matches"):
            if isinstance(fixture_status.get(key), int) and not isinstance(
                fixture_status.get(key), bool
            ):
                safe_fixture_status[key] = fixture_status[key]
        safe["fixture_status"] = safe_fixture_status
    return safe


def _safe_archive_summary(value: Any) -> dict[str, Any]:
    invalid = {
        "status": "error",
        "reason": "invalid_snapshot_archive_result",
        "error_type": "ValueError",
    }
    try:
        if type(value) is not dict:
            return invalid
        status = dict.get(value, "status")
        if type(status) is not str or status not in {
            "created",
            "duplicate",
            "dry_run",
            "error",
        }:
            return invalid
        reason = dict.get(value, "reason")
        error_type = dict.get(value, "error_type")
        if status == "error":
            if reason not in {
                "snapshot_archive_failed",
                "archive_validation_failed",
            }:
                return invalid
            safe: dict[str, Any] = {"status": status, "reason": reason}
            if error_type is not None:
                if not _stable_error_type(error_type):
                    return invalid
                safe["error_type"] = error_type
            return safe
        if reason is not None or error_type is not None:
            return invalid

        safe = {"status": status}
        snapshot_at = dict.get(value, "snapshot_at")
        if snapshot_at is not None:
            if type(snapshot_at) is not str:
                return invalid
            safe["snapshot_at"] = _iso_utc(_parse_utc(snapshot_at))
        for key in ("created", "duplicate"):
            item = dict.get(value, key)
            if item is not None:
                if type(item) is not bool:
                    return invalid
                safe[key] = item
        matches = dict.get(value, "matches")
        if matches is not None:
            if type(matches) is not int or matches < 0:
                return invalid
            safe["matches"] = matches
        return safe
    except Exception:
        return invalid


def _public_policy_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in decision.items()
        if key not in LOCAL_POLICY_FIELDS
    }


def _safe_postmatch_shadow(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_postmatch_shadow_result"}
    safe: dict[str, Any] = {"status": str(value.get("status") or "error")}
    for key in (
        "reason",
        "competition_id",
        "input_fingerprint_prefix",
        "error_type",
    ):
        if value.get(key) is not None:
            safe[key] = str(value[key])
    for key in ("results", "closing_available", "decided"):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
            safe[key] = value[key]
    if isinstance(value.get("sample_too_small"), bool):
        safe["sample_too_small"] = value["sample_too_small"]
    return safe


def _safe_postmatch_sentinel(value: Any) -> dict[str, Any]:
    invalid = {"status": "error", "reason": "invalid_postmatch_sentinel_result"}
    if type(value) is not dict:
        return invalid
    status = value.get("status")
    reason = value.get("reason")
    competition_id = value.get("competition_id")
    notification_status = value.get("notification_status")
    error_type = value.get("error_type")
    event_count = value.get("event_count")
    if type(status) is not str or status not in POSTMATCH_SENTINEL_STATUSES:
        return invalid
    if reason is not None and (
        type(reason) is not str or reason not in POSTMATCH_SENTINEL_REASONS
    ):
        return invalid
    if competition_id is not None and (
        type(competition_id) is not str
        or competition_id != DEFAULT_COMPETITION_ID
    ):
        return invalid
    if (
        type(notification_status) is not str
        or notification_status not in POSTMATCH_SENTINEL_NOTIFICATION_STATUSES
    ):
        return invalid
    if error_type is not None and not _stable_error_type(error_type):
        return invalid
    if type(event_count) is not int or event_count < 0:
        return invalid
    safe = {
        "status": status,
        "event_count": event_count,
        "notification_status": notification_status,
    }
    for key, item in (
        ("reason", reason),
        ("competition_id", competition_id),
        ("error_type", error_type),
    ):
        if item is not None:
            safe[key] = item
    return safe


def _attach_run_metadata(
    snapshot: dict[str, Any],
    *,
    observed_at: str,
    decision: dict[str, Any],
    refresh_result: dict[str, Any],
    quota_path: str | Path,
) -> dict[str, Any]:
    provider = str(refresh_result.get("theoddsapi_provider") or "theoddsapi")
    quota_entry = _safe_quota_entry(refresh_result.get("quota_entry"))
    snapshot["run"] = {
        "schema_version": 1,
        "run_id": make_run_id(observed_at, "csl-live"),
        "mode": "csl_scheduled_publish",
        "observed_at": observed_at,
        "policy": _public_policy_decision(decision),
        "quota": {provider: quota_entry} if quota_entry else {},
        "quota_path": str(quota_path),
        "source_cache_path": "data/cache/theoddsapi_csl_2026_odds.json",
    }
    return snapshot


def _runner_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any]:
    competition = (
        snapshot.get("competition")
        if isinstance(snapshot.get("competition"), dict)
        else {}
    )
    quality = (
        snapshot.get("data_quality")
        if isinstance(snapshot.get("data_quality"), dict)
        else {}
    )
    matches = snapshot.get("matches") if isinstance(snapshot.get("matches"), list) else []
    picks = 0
    no_pick = 0
    postponed = 0
    rating_fallback_picks = 0
    for match in matches:
        if not isinstance(match, dict):
            continue
        if str(match.get("fixture_status") or "").upper() == "POSTPONED":
            postponed += 1
            continue
        decision = match.get("match_decision")
        if not isinstance(decision, dict):
            continue
        if decision.get("label") == "MATCH_PICK":
            picks += 1
            risks = decision.get("risks") if isinstance(decision.get("risks"), list) else []
            if "market_only_rating_fallback" in risks and "model_settlement" not in decision:
                rating_fallback_picks += 1
        elif decision.get("label") == "NO_CLEAN_MARKET":
            no_pick += 1
    club_rating = (
        quality.get("club_rating") if isinstance(quality.get("club_rating"), dict) else {}
    )
    return {
        "status": "ok",
        "snapshot_at": snapshot.get("snapshot_at"),
        "competition_id": competition.get("id"),
        "fixture_source": quality.get("fixture_source"),
        "rating_policy": competition.get("rating_policy"),
        "counts": snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {},
        "warnings": quality.get("warnings") if isinstance(quality.get("warnings"), list) else [],
        "club_alias_unmatched": (
            quality.get("club_alias_unmatched")
            if isinstance(quality.get("club_alias_unmatched"), list)
            else []
        ),
        "invalid_odds_count": int(quality.get("invalid_odds_count") or 0),
        "club_rating": club_rating,
        "match_picks": picks,
        "rating_fallback_picks": rating_fallback_picks,
        "rating_unsafe_picks": picks - rating_fallback_picks,
        "postponed": postponed,
        "no_pick": no_pick,
        "missing_decisions": len(matches) - picks - no_pick - postponed,
    }


def run_csl_scheduled_publish(
    *,
    now: str | None = None,
    live: bool = False,
    force: bool = False,
    env_path: str | Path = ".env",
    cache_dir: str | Path = "data/cache",
    quota_path: str | Path = "data/cache/quota.json",
    snapshot_path: str | Path = "data/cache/csl_publish_snapshot.json",
    diagnostics_snapshot_path: str | Path = "data/local/diagnostics/csl_live_league_snapshot.json",
    runner_diagnostics_path: str | Path | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    load_env: EnvLoader = _load_env,
    refresh_fn: RefreshFn = run_league_odds_refresh,
    snapshot_builder: SnapshotBuilder = build_league_snapshot_from_cache,
    publish_fn: PublishFn = publish_snapshot,
    results_refresh_fn: ResultsRefreshFn = run_csl_results_refresh,
    postmatch_shadow_fn: PostmatchShadowFn = run_csl_postmatch_shadow,
    postmatch_shadow_root: str | Path = ".",
    postmatch_sentinel_fn: PostmatchSentinelFn = run_csl_postmatch_sentinel,
    postmatch_sentinel_root: str | Path = ".",
    notify: bool = True,
    closing_coverage_fn: ClosingCoverageFn = run_closing_coverage,
    closing_coverage_root: str | Path = ".",
    archive_fn: ArchiveFn = archive_snapshot,
    archive_history_path: str | Path | None = None,
) -> dict[str, Any]:
    observed = now or _now_utc_iso()
    snapshot = _read_json_if_exists(snapshot_path)
    quota_remaining = _quota_remaining(quota_path)
    history_path = (
        Path(archive_history_path)
        if archive_history_path is not None
        else Path(diagnostics_snapshot_path).with_name("csl_history")
    )
    archive_history = _load_archive_history_safe(history_path)
    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=quota_remaining,
        now=observed,
        min_interval_seconds=min_interval_seconds,
        discovery_interval_seconds=discovery_interval_seconds,
        archived_snapshots=archive_history["snapshots"],
        archive_history_status=str(archive_history["status"]),
    )

    if not live:
        return {
            "status": "dry_run",
            "force": force,
            "decision": decision,
            "postmatch_sentinel": {
                "status": "not_run",
                "reason": "scheduler_dry_run",
            },
            "refresh": None,
            "publish": None,
        }

    pending = load_pending_publish(snapshot_path)
    closing_coverage: dict[str, Any] | None = None
    if not force and not decision["should_refresh"]:
        audit_events = (
            _coverage_audit_events(
                decision["closing_coverage_candidates"],
                observed_at=observed,
                issue_code="quota_blocked",
            )
            if decision.get("reason") == "quota_exhausted"
            else []
        )
        closing_coverage = _run_closing_coverage_safe(
            closing_coverage_fn=closing_coverage_fn,
            closing_coverage_root=closing_coverage_root,
            history_path=history_path,
            cache_dir=cache_dir,
            observed_at=observed,
            audit_events=audit_events,
        )
        if pending is None:
            return {
                "status": "skipped",
                "force": force,
                "decision": decision,
                "closing_coverage": closing_coverage,
                "postmatch_sentinel": {
                    "status": "not_run",
                    "reason": "scheduled_refresh_not_due",
                },
                "refresh": None,
                "publish": None,
            }

    # Fail-fast: validate secret before any refresh/publish/network side effects
    env = load_env(env_path)
    resolved_secret = env.get("INGEST_HMAC_SECRET")
    if not resolved_secret:
        return {
            "status": "blocked",
            "reason": "missing_ingest_hmac_secret",
            "force": force,
            "decision": decision,
            **(
                {"closing_coverage": closing_coverage}
                if closing_coverage is not None
                else {}
            ),
            "postmatch_sentinel": {
                "status": "not_run",
                "reason": "ingest_secret_unavailable",
            },
            "refresh": None,
            "publish": None,
        }
    from worldcup.secrets import validate_hmac_secret
    try:
        validate_hmac_secret(resolved_secret)
    except ValueError:
        return {
            "status": "blocked",
            "reason": "weak_ingest_hmac_secret",
            "force": force,
            "decision": decision,
            **(
                {"closing_coverage": closing_coverage}
                if closing_coverage is not None
                else {}
            ),
            "postmatch_sentinel": {
                "status": "not_run",
                "reason": "ingest_secret_invalid",
            },
            "refresh": None,
            "publish": None,
        }

    if not force and not decision["should_refresh"] and pending is not None:
        if pending.get("status") != "pending":
            return {
                "status": "publish_pending_invalid",
                "reason": pending.get("reason"),
                "force": force,
                "decision": decision,
                "closing_coverage": closing_coverage,
                "postmatch_sentinel": {
                    "status": "not_run",
                    "reason": "pending_publish_retry",
                },
                "refresh": None,
                "publish": None,
            }
        retried = attempt_publish(
            snapshot_path=snapshot_path,
            endpoint=endpoint,
            secret=resolved_secret,
            timestamp=observed,
            publish_fn=publish_fn,
            stage=False,
        )
        return {
            "status": "republished" if retried["status"] == "published" else retried["status"],
            "force": force,
            "decision": decision,
            "closing_coverage": closing_coverage,
            "postmatch_sentinel": {
                "status": "not_run",
                "reason": "pending_publish_retry",
            },
            "refresh": None,
            "publish": retried.get("publish"),
            "pending": retried.get("pending"),
        }

    try:
        results_refresh = _safe_results_refresh(
            results_refresh_fn(
                live=True,
                write=True,
                competition_id=DEFAULT_COMPETITION_ID,
                replay_path=Path(cache_dir) / f"club_results_{DEFAULT_COMPETITION_ID}.csv",
                raw_dir=Path(cache_dir) / "csl_results_sources",
            )
        )
    except (OSError, ValueError, TimeoutError, ConnectionError) as exc:
        results_refresh = {
            "status": "error",
            "reason": "results_refresh_failed_using_existing_cache",
            "error_type": type(exc).__name__,
        }
    closing_coverage = _run_closing_coverage_safe(
        closing_coverage_fn=closing_coverage_fn,
        closing_coverage_root=closing_coverage_root,
        history_path=history_path,
        cache_dir=cache_dir,
        observed_at=observed,
    )
    result_source_status = str(results_refresh.get("status") or "error")
    if result_source_status in {"updated", "verified"}:
        try:
            postmatch_shadow = _safe_postmatch_shadow(
                postmatch_shadow_fn(
                    root=postmatch_shadow_root,
                    history=history_path,
                    results=Path(cache_dir)
                    / f"club_results_{DEFAULT_COMPETITION_ID}.csv",
                    competition_id=DEFAULT_COMPETITION_ID,
                    season="2026",
                    generated_at=observed,
                    source_status=result_source_status,
                    write=True,
                )
            )
        except Exception as exc:
            postmatch_shadow = {
                "status": "error",
                "reason": "csl_postmatch_shadow_failed",
                "error_type": type(exc).__name__,
            }
    else:
        postmatch_shadow = {
            "status": "blocked",
            "reason": "result_source_not_accepted",
        }
    if postmatch_shadow.get("status") in {"stored", "unchanged"}:
        try:
            postmatch_sentinel = _safe_postmatch_sentinel(
                postmatch_sentinel_fn(
                    root=postmatch_sentinel_root,
                    observed_at=observed,
                    write=True,
                    notify=notify,
                )
            )
        except Exception as exc:
            error_type = type(exc).__name__
            postmatch_sentinel = {
                "status": "error",
                "reason": "csl_postmatch_sentinel_failed",
                "error_type": (
                    error_type if _stable_error_type(error_type) else "Exception"
                ),
            }
    else:
        postmatch_sentinel = {
            "status": "not_run",
            "reason": "postmatch_shadow_not_successful",
        }
    refresh = refresh_fn(
        live=True,
        env=env,
        competition_id=DEFAULT_COMPETITION_ID,
        sport_key=DEFAULT_SPORT_KEY,
        cache_dir=cache_dir,
        quota_path=quota_path,
        replace_existing=True,
        observed_at=observed,
    )
    if refresh.get("status") != "fetched":
        issue_code = (
            "quota_blocked"
            if refresh.get("reason") == "quota_exhausted"
            else "provider_refresh_failed"
        )
        closing_coverage = _run_closing_coverage_safe(
            closing_coverage_fn=closing_coverage_fn,
            closing_coverage_root=closing_coverage_root,
            history_path=history_path,
            cache_dir=cache_dir,
            observed_at=observed,
            audit_events=_coverage_audit_events(
                decision["closing_coverage_candidates"],
                observed_at=observed,
                issue_code=issue_code,
            ),
        )
        return {
            "status": "blocked" if refresh.get("status") == "blocked" else "error",
            "force": force,
            "decision": decision,
            "results_refresh": results_refresh,
            "closing_coverage": closing_coverage,
            "postmatch_shadow": postmatch_shadow,
            "postmatch_sentinel": postmatch_sentinel,
            "refresh": refresh,
            "publish": None,
        }

    built = snapshot_builder(
        cache_dir,
        competition_id=DEFAULT_COMPETITION_ID,
        snapshot_at=observed,
    )
    if int((built.get("counts") or {}).get("matches") or 0) <= 0:
        return {
            "status": "blocked",
            "reason": "empty_csl_snapshot",
            "force": force,
            "decision": decision,
            "results_refresh": results_refresh,
            "closing_coverage": closing_coverage,
            "postmatch_shadow": postmatch_shadow,
            "postmatch_sentinel": postmatch_sentinel,
            "refresh": refresh,
            "publish": None,
        }
    built = _attach_run_metadata(
        built,
        observed_at=observed,
        decision=decision,
        refresh_result=refresh,
        quota_path=quota_path,
    )
    data_quality = built.setdefault("data_quality", {})
    if isinstance(data_quality, dict):
        data_quality["club_results_refresh"] = results_refresh
        if postmatch_shadow.get("status") == "error":
            warnings = data_quality.setdefault("warnings", [])
            if (
                isinstance(warnings, list)
                and "csl_postmatch_shadow_failed" not in warnings
            ):
                warnings.append("csl_postmatch_shadow_failed")
        if results_refresh.get("status") not in {"updated", "verified"}:
            warnings = data_quality.setdefault("warnings", [])
            if isinstance(warnings, list) and "club_results_refresh_failed" not in warnings:
                warnings.append("club_results_refresh_failed")
            stale_sources = data_quality.setdefault("stale_sources", [])
            if isinstance(stale_sources, list):
                stale_names = ["csl_results"]
                fixture_refresh = results_refresh.get("fixture_status") or {}
                if fixture_refresh.get("status") not in {"updated", "verified"}:
                    stale_names.append("csl_fixture_status")
                for source_name in stale_names:
                    if source_name not in stale_sources:
                        stale_sources.append(source_name)
    write_snapshot(built, diagnostics_snapshot_path)
    write_snapshot(built, snapshot_path)
    runner_path = (
        Path(runner_diagnostics_path)
        if runner_diagnostics_path is not None
        else Path(diagnostics_snapshot_path).with_name("csl_live_league_runner_check.json")
    )
    write_snapshot(_runner_diagnostic(built), runner_path)
    try:
        archive = _safe_archive_summary(
            archive_fn(
                source=diagnostics_snapshot_path,
                history=history_path,
                competition_id=DEFAULT_COMPETITION_ID,
                min_matches=1,
                dry_run=False,
            )
        )
    except (OSError, ValueError) as exc:
        archive = {
            "status": "error",
            "reason": (
                "archive_validation_failed"
                if isinstance(exc, ValueError)
                else "snapshot_archive_failed"
            ),
            "error_type": type(exc).__name__,
        }
    if archive.get("status") not in {"created", "duplicate"}:
        try:
            built_match_ids = {
                _match_id(match)
                for match in built.get("matches") or []
                if isinstance(match, dict)
            }
            archive_affected = closing_archive_candidates(
                snapshot=built,
                archived_snapshots=[],
                due_match_ids=built_match_ids,
            )
            archive_issue = (
                "archive_validation_failed"
                if archive.get("reason") == "archive_validation_failed"
                else "snapshot_archive_failed"
            )
            audit_events = _coverage_audit_events(
                archive_affected,
                observed_at=observed,
                issue_code=archive_issue,
            )
            closing_coverage = _run_closing_coverage_safe(
                closing_coverage_fn=closing_coverage_fn,
                closing_coverage_root=closing_coverage_root,
                history_path=history_path,
                cache_dir=cache_dir,
                observed_at=observed,
                audit_events=audit_events,
            )
        except Exception as exc:
            closing_coverage = {
                "status": "error",
                "reason": "csl_closing_coverage_failed",
                "error_type": type(exc).__name__,
            }
        if isinstance(data_quality, dict):
            warnings = data_quality.setdefault("warnings", [])
            if isinstance(warnings, list) and "snapshot_archive_failed" not in warnings:
                warnings.append("snapshot_archive_failed")
        write_snapshot(built, diagnostics_snapshot_path)
        write_snapshot(built, snapshot_path)
        write_snapshot(_runner_diagnostic(built), runner_path)

    attempted = attempt_publish(
        snapshot_path=snapshot_path,
        endpoint=endpoint,
        secret=resolved_secret,
        timestamp=observed,
        publish_fn=publish_fn,
        stage=True,
    )
    if attempted["status"] != "published":
        return {
            "status": attempted["status"],
            "force": force,
            "decision": decision,
            "results_refresh": results_refresh,
            "closing_coverage": closing_coverage,
            "postmatch_shadow": postmatch_shadow,
            "postmatch_sentinel": postmatch_sentinel,
            "archive": archive,
            "refresh": refresh,
            "publish": attempted.get("publish"),
            "pending": attempted.get("pending"),
        }
    publish = attempted["publish"]
    return {
        "status": "published",
        "force": force,
        "decision": decision,
        "results_refresh": results_refresh,
        "closing_coverage": closing_coverage,
        "postmatch_shadow": postmatch_shadow,
        "postmatch_sentinel": postmatch_sentinel,
        "archive": archive,
        "refresh": {
            "status": refresh.get("status"),
            "competition_id": refresh.get("competition_id"),
            "sport_key": refresh.get("sport_key"),
            "events": refresh.get("events"),
            "theoddsapi_provider": refresh.get("theoddsapi_provider"),
            "quota_entry": _safe_quota_entry(refresh.get("quota_entry")),
        },
        "publish": publish,
        "snapshot": {
            "path": str(snapshot_path),
            "diagnostics_path": str(diagnostics_snapshot_path),
            "run_id": built["run"]["run_id"],
            "snapshot_at": built.get("snapshot_at"),
            "matches": (built.get("counts") or {}).get("matches"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh and publish CSL snapshots when match anchors are due. Defaults to dry-run."
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Disable local CSL postmatch sentinel notifications only.",
    )
    parser.add_argument("--env", default=".env")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--snapshot-path", default="data/cache/csl_publish_snapshot.json")
    parser.add_argument(
        "--diagnostics-snapshot-path",
        default="data/local/diagnostics/csl_live_league_snapshot.json",
    )
    parser.add_argument(
        "--archive-history-path",
        default=None,
        help="Defaults beside the diagnostics snapshot as csl_history/.",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--min-interval-seconds", type=int, default=DEFAULT_MIN_INTERVAL_SECONDS)
    parser.add_argument(
        "--discovery-interval-seconds",
        type=int,
        default=DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    )
    args = parser.parse_args(argv)
    result = run_csl_scheduled_publish(
        now=args.now,
        live=args.live,
        force=args.force,
        notify=not args.no_notify,
        env_path=args.env,
        cache_dir=args.cache_dir,
        quota_path=args.quota_path,
        snapshot_path=args.snapshot_path,
        diagnostics_snapshot_path=args.diagnostics_snapshot_path,
        archive_history_path=args.archive_history_path,
        endpoint=args.endpoint,
        min_interval_seconds=args.min_interval_seconds,
        discovery_interval_seconds=args.discovery_interval_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"dry_run", "skipped", "published", "republished"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
