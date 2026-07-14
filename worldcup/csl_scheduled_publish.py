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
from worldcup.csl_results_refresh import run_csl_results_refresh
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
) -> dict[str, Any]:
    now_dt = _parse_utc(now)
    last_raw = _last_refresh_at(snapshot)
    last_dt = _parse_utc(last_raw) if last_raw else None
    base = {
        "schema_version": 1,
        "competition_id": DEFAULT_COMPETITION_ID,
        "now": _iso_utc(now_dt),
        "last_refresh_at": _iso_utc(last_dt) if last_dt else None,
        "quota_remaining": quota_remaining,
        "min_interval_seconds": int(min_interval_seconds),
        "discovery_interval_seconds": int(discovery_interval_seconds),
        "anchors": [anchor for _offset, anchor, _label in _allowed_anchors(quota_remaining)],
    }
    if quota_remaining is not None and quota_remaining <= 0:
        return {
            **base,
            "should_refresh": False,
            "reason": "quota_exhausted",
            "due_matches": [],
            "next_due_at": None,
        }

    anchor_due_matches = _due_match_items(snapshot, now_dt, last_dt, quota_remaining)
    expiry_due_matches = _pick_expiry_items(snapshot, now_dt, last_dt, quota_remaining)
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
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_archive_result"}
    safe: dict[str, Any] = {"status": str(value.get("status") or "error")}
    for key in ("path", "snapshot_at"):
        if value.get(key) is not None:
            safe[key] = str(value[key])
    for key in ("created", "duplicate"):
        if isinstance(value.get(key), bool):
            safe[key] = value[key]
    if isinstance(value.get("matches"), int) and not isinstance(value.get("matches"), bool):
        safe["matches"] = value["matches"]
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
        "policy": decision,
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
    archive_fn: ArchiveFn = archive_snapshot,
    archive_history_path: str | Path | None = None,
) -> dict[str, Any]:
    observed = now or _now_utc_iso()
    snapshot = _read_json_if_exists(snapshot_path)
    quota_remaining = _quota_remaining(quota_path)
    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=quota_remaining,
        now=observed,
        min_interval_seconds=min_interval_seconds,
        discovery_interval_seconds=discovery_interval_seconds,
    )

    if not live:
        return {
            "status": "dry_run",
            "force": force,
            "decision": decision,
            "refresh": None,
            "publish": None,
        }

    pending = load_pending_publish(snapshot_path)
    if not force and not decision["should_refresh"] and pending is not None:
        if pending.get("status") != "pending":
            return {
                "status": "publish_pending_invalid",
                "reason": pending.get("reason"),
                "force": force,
                "decision": decision,
                "refresh": None,
                "publish": None,
            }
        env = load_env(env_path)
        secret = env.get("INGEST_HMAC_SECRET")
        if not secret:
            return {
                "status": "blocked",
                "reason": "missing_ingest_hmac_secret",
                "force": force,
                "decision": decision,
                "refresh": None,
                "publish": None,
            }
        retried = attempt_publish(
            snapshot_path=snapshot_path,
            endpoint=endpoint,
            secret=secret,
            timestamp=observed,
            publish_fn=publish_fn,
            stage=False,
        )
        return {
            "status": "republished" if retried["status"] == "published" else retried["status"],
            "force": force,
            "decision": decision,
            "refresh": None,
            "publish": retried.get("publish"),
            "pending": retried.get("pending"),
        }

    if not force and not decision["should_refresh"]:
        return {
            "status": "skipped",
            "force": force,
            "decision": decision,
            "refresh": None,
            "publish": None,
        }

    env = load_env(env_path)
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
        return {
            "status": "blocked" if refresh.get("status") == "blocked" else "error",
            "force": force,
            "decision": decision,
            "results_refresh": results_refresh,
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
    history_path = (
        Path(archive_history_path)
        if archive_history_path is not None
        else Path(diagnostics_snapshot_path).with_name("csl_history")
    )
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
            "reason": "snapshot_archive_failed",
            "error_type": type(exc).__name__,
        }
    if archive.get("status") not in {"created", "duplicate"}:
        if isinstance(data_quality, dict):
            warnings = data_quality.setdefault("warnings", [])
            if isinstance(warnings, list) and "snapshot_archive_failed" not in warnings:
                warnings.append("snapshot_archive_failed")
        write_snapshot(built, diagnostics_snapshot_path)
        write_snapshot(built, snapshot_path)
        write_snapshot(_runner_diagnostic(built), runner_path)

    secret = env.get("INGEST_HMAC_SECRET")
    if not secret:
        return {
            "status": "blocked",
            "reason": "missing_ingest_hmac_secret",
            "force": force,
            "decision": decision,
            "results_refresh": results_refresh,
            "archive": archive,
            "refresh": refresh,
            "publish": None,
        }
    attempted = attempt_publish(
        snapshot_path=snapshot_path,
        endpoint=endpoint,
        secret=secret,
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
