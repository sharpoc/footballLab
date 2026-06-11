from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worldcup.quota import load_quota_ledger
from worldcup.theoddsapi_keys import quota_remaining_for_scheduler

POLICY_VERSION = "free-tier-v2"
DEFAULT_INTERVAL_SECONDS = 86400
QUOTA_LOW_REMAINING = 30
QUOTA_LOW_INTERVAL_SECONDS = 86400
CRITICAL_LOW_QUOTA_ANCHORS = {"pre_90m_lineup_warmup", "pre_55m_lineup_main", "pre_25m_final_check"}
MATCH_ANCHORS = (
    (12 * 3600, "pre_12h_checkpoint", "T-12小时", "赛日早间检查"),
    (6 * 3600, "pre_6h_checkpoint", "T-6小时", "赛前状态检查"),
    (90 * 60, "pre_90m_lineup_warmup", "T-1小时30分", "阵容/伤停预热"),
    (55 * 60, "pre_55m_lineup_main", "T-55分钟", "首发主抓点"),
    (25 * 60, "pre_25m_final_check", "T-25分钟", "临场最终确认"),
)


@dataclass(frozen=True)
class RefreshDecision:
    should_refresh: bool
    reason: str
    policy_reason: str
    interval_seconds: int
    now: str
    last_refresh_at: str | None
    next_due_at: str | None
    next_kickoff_at: str | None
    quota_remaining: int | None
    match_plans: list[dict[str, Any]] = field(default_factory=list)
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"Expected timezone-aware datetime: {value}")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _select_interval(
    now: datetime,
    next_kickoff_at: datetime | None,
    quota_remaining: int | None,
) -> tuple[int, str]:
    if quota_remaining is not None and quota_remaining <= QUOTA_LOW_REMAINING:
        return QUOTA_LOW_INTERVAL_SECONDS, "quota_low"
    return DEFAULT_INTERVAL_SECONDS, "default"


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


def _cadence_label(policy_reason: str, interval_seconds: int) -> tuple[str, str]:
    labels = {
        "default": "常规",
        "quota_low": "低额度",
    }
    hours = interval_seconds // 3600 if interval_seconds % 3600 == 0 else None
    interval_text = f"{hours} 小时" if hours else f"{interval_seconds} 秒"
    label = labels.get(policy_reason, "按规则")
    return label, f"按{interval_text}间隔刷新"


def _align_cadence_due_to_kickoff_clock(
    cadence_due: datetime,
    kickoff_at: datetime,
) -> datetime:
    aligned = cadence_due.replace(
        minute=kickoff_at.minute,
        second=kickoff_at.second,
        microsecond=0,
    )
    if aligned < cadence_due:
        aligned += timedelta(hours=1)
    return aligned


def _select_match_interval(
    now: datetime,
    kickoff_at: datetime,
    quota_remaining: int | None,
) -> tuple[int, str]:
    if quota_remaining is not None and quota_remaining <= 0:
        return 0, "quota_exhausted"
    return _select_interval(now, kickoff_at, quota_remaining)


def build_match_refresh_plan(
    now: str,
    last_refresh_at: str | None,
    match: dict[str, Any],
    quota_remaining: int | None,
) -> dict[str, Any]:
    now_dt = _parse_utc(now)
    kickoff_raw = str(match.get("kickoff_at_utc") or "").strip()
    kickoff_dt = _parse_utc(kickoff_raw) if kickoff_raw else None
    last_dt = _parse_utc(last_refresh_at) if last_refresh_at else None
    base = {
        "match_id": _match_id(match),
        "match_label": _match_label(match),
        "kickoff_at_utc": _iso_utc(kickoff_dt) if kickoff_dt else None,
        "quota_remaining": quota_remaining,
    }
    if kickoff_dt is None:
        return {
            **base,
            "next_update_at": None,
            "policy_reason": "missing_kickoff",
            "label": "缺少开赛时间",
            "description": "无法计算单场更新时间",
            "interval_seconds": None,
            "should_refresh": False,
        }
    if kickoff_dt <= now_dt:
        return {
            **base,
            "next_update_at": None,
            "policy_reason": "post_kickoff",
            "label": "赛前更新结束",
            "description": "比赛已经开赛或结束",
            "interval_seconds": None,
            "should_refresh": False,
        }

    interval_seconds, cadence_reason = _select_match_interval(now_dt, kickoff_dt, quota_remaining)
    if cadence_reason == "quota_exhausted":
        return {
            **base,
            "next_update_at": None,
            "policy_reason": "quota_exhausted",
            "label": "额度耗尽",
            "description": "等待额度恢复或人工刷新",
            "interval_seconds": 0,
            "should_refresh": False,
        }

    if last_dt is None:
        label, description = _cadence_label(cadence_reason, interval_seconds)
        return {
            **base,
            "next_update_at": _iso_utc(now_dt),
            "policy_reason": "no_previous_refresh",
            "label": label,
            "description": description,
            "interval_seconds": interval_seconds,
            "should_refresh": True,
        }

    candidates: list[tuple[datetime, int, str, str, str]] = []
    cadence_due = last_dt + timedelta(seconds=interval_seconds)
    cadence_next = _align_cadence_due_to_kickoff_clock(cadence_due, kickoff_dt)
    cadence_label, cadence_description = _cadence_label(cadence_reason, interval_seconds)
    candidates.append(
        (
            cadence_next,
            1,
            cadence_reason,
            cadence_label,
            cadence_description,
        )
    )

    low_quota = quota_remaining is not None and 0 < quota_remaining <= QUOTA_LOW_REMAINING
    for offset_seconds, reason, label, description in MATCH_ANCHORS:
        if low_quota and reason not in CRITICAL_LOW_QUOTA_ANCHORS:
            continue
        anchor_dt = kickoff_dt - timedelta(seconds=offset_seconds)
        if last_dt >= anchor_dt:
            continue
        candidates.append(
            (
                now_dt if anchor_dt <= now_dt else anchor_dt,
                0,
                reason,
                label,
                description,
            )
        )

    next_dt, _priority, reason, label, description = min(candidates, key=lambda item: (item[0], item[1]))
    return {
        **base,
        "next_update_at": _iso_utc(next_dt),
        "policy_reason": reason,
        "label": label,
        "description": description,
        "interval_seconds": interval_seconds,
        "should_refresh": next_dt <= now_dt,
    }


def build_match_refresh_plans(
    now: str,
    last_refresh_at: str | None,
    matches: list[dict[str, Any]],
    quota_remaining: int | None,
) -> list[dict[str, Any]]:
    plans = [
        build_match_refresh_plan(
            now=now,
            last_refresh_at=last_refresh_at,
            match=match,
            quota_remaining=quota_remaining,
        )
        for match in matches
    ]
    return sorted(
        plans,
        key=lambda plan: (
            plan.get("next_update_at") is None,
            str(plan.get("next_update_at") or ""),
            str(plan.get("kickoff_at_utc") or ""),
            str(plan.get("match_label") or ""),
        ),
    )


def build_match_refresh_decision(
    now: str,
    last_refresh_at: str | None,
    matches: list[dict[str, Any]],
    quota_remaining: int | None,
) -> RefreshDecision:
    now_dt = _parse_utc(now)
    last_dt = _parse_utc(last_refresh_at) if last_refresh_at else None
    plans = build_match_refresh_plans(
        now=now,
        last_refresh_at=last_refresh_at,
        matches=matches,
        quota_remaining=quota_remaining,
    )
    upcoming_kickoffs = [
        _parse_utc(str(plan["kickoff_at_utc"]))
        for plan in plans
        if plan.get("kickoff_at_utc") and _parse_utc(str(plan["kickoff_at_utc"])) >= now_dt
    ]
    next_kickoff = min(upcoming_kickoffs) if upcoming_kickoffs else None
    active = [plan for plan in plans if plan.get("next_update_at")]
    if not active:
        inactive_reason = "no_active_match_plan"
        if any(plan.get("policy_reason") == "quota_exhausted" for plan in plans):
            inactive_reason = "quota_exhausted"
        return RefreshDecision(
            should_refresh=False,
            reason="not_due",
            policy_reason=inactive_reason,
            interval_seconds=0,
            now=_iso_utc(now_dt),
            last_refresh_at=_iso_utc(last_dt) if last_dt else None,
            next_due_at=None,
            next_kickoff_at=_iso_utc(next_kickoff) if next_kickoff else None,
            quota_remaining=quota_remaining,
            match_plans=plans,
        )

    first = active[0]
    due_dt = _parse_utc(str(first["next_update_at"]))
    should_refresh = due_dt <= now_dt or bool(first.get("should_refresh"))
    return RefreshDecision(
        should_refresh=should_refresh,
        reason="due" if should_refresh else "not_due",
        policy_reason=str(first.get("policy_reason") or ""),
        interval_seconds=int(first.get("interval_seconds") or 0),
        now=_iso_utc(now_dt),
        last_refresh_at=_iso_utc(last_dt) if last_dt else None,
        next_due_at=_iso_utc(due_dt),
        next_kickoff_at=_iso_utc(next_kickoff) if next_kickoff else None,
        quota_remaining=quota_remaining,
        match_plans=plans,
    )


def build_refresh_decision(
    now: str,
    last_refresh_at: str | None,
    next_kickoff_at: str | None,
    quota_remaining: int | None,
) -> RefreshDecision:
    now_dt = _parse_utc(now)
    next_kickoff_dt = _parse_utc(next_kickoff_at) if next_kickoff_at else None
    interval_seconds, policy_reason = _select_interval(now_dt, next_kickoff_dt, quota_remaining)

    if last_refresh_at is None:
        return RefreshDecision(
            should_refresh=True,
            reason="no_previous_refresh",
            policy_reason=policy_reason,
            interval_seconds=interval_seconds,
            now=_iso_utc(now_dt),
            last_refresh_at=None,
            next_due_at=None,
            next_kickoff_at=_iso_utc(next_kickoff_dt) if next_kickoff_dt else None,
            quota_remaining=quota_remaining,
        )

    last_dt = _parse_utc(last_refresh_at)
    due_dt = last_dt + timedelta(seconds=interval_seconds)
    should_refresh = now_dt >= due_dt
    return RefreshDecision(
        should_refresh=should_refresh,
        reason="due" if should_refresh else "not_due",
        policy_reason=policy_reason,
        interval_seconds=interval_seconds,
        now=_iso_utc(now_dt),
        last_refresh_at=_iso_utc(last_dt),
        next_due_at=_iso_utc(due_dt),
        next_kickoff_at=_iso_utc(next_kickoff_dt) if next_kickoff_dt else None,
        quota_remaining=quota_remaining,
    )


def make_run_id(observed_at: str, mode: str) -> str:
    observed_dt = _parse_utc(observed_at)
    return f"{observed_dt.strftime('%Y%m%dT%H%M%SZ')}-{mode}"


def build_run_metadata(
    run_id: str,
    mode: str,
    observed_at: str,
    decision: RefreshDecision,
    quota: dict[str, Any],
    source_errors: list[dict],
    stale_sources: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "mode": mode,
        "observed_at": _iso_utc(_parse_utc(observed_at)),
        "policy_version": POLICY_VERSION,
        "policy": decision.to_dict(),
        "quota": quota,
        "source_errors": source_errors,
        "stale_sources": stale_sources,
    }


def _read_json_if_exists(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _last_refresh_at_from_snapshot(snapshot: dict[str, Any]) -> str | None:
    run = snapshot.get("run") or {}
    return run.get("observed_at") or snapshot.get("snapshot_at")


def _next_kickoff_at_from_snapshot(snapshot: dict[str, Any], observed_at: str) -> str | None:
    observed = _parse_utc(observed_at)
    upcoming: list[datetime] = []
    for match in snapshot.get("matches", []):
        kickoff_raw = match.get("kickoff_at_utc")
        if not kickoff_raw:
            continue
        kickoff = _parse_utc(kickoff_raw)
        if kickoff >= observed:
            upcoming.append(kickoff)
    if not upcoming:
        return None
    return _iso_utc(min(upcoming))


def build_scheduler_report(
    now: str,
    snapshot_path: str | Path = "data/cache/analysis_snapshot.json",
    quota_path: str | Path = "data/cache/quota.json",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    snapshot = _read_json_if_exists(snapshot_path)
    quota = load_quota_ledger(quota_path).get("providers", {})
    quota_remaining = quota_remaining_for_scheduler(quota, env)
    last_refresh_at = _last_refresh_at_from_snapshot(snapshot)
    next_kickoff_at = _next_kickoff_at_from_snapshot(snapshot, now) if snapshot else None
    matches = snapshot.get("matches") or []
    if matches:
        decision = build_match_refresh_decision(
            now=now,
            last_refresh_at=last_refresh_at,
            matches=matches,
            quota_remaining=quota_remaining,
        )
    else:
        decision = build_refresh_decision(
            now=now,
            last_refresh_at=last_refresh_at,
            next_kickoff_at=next_kickoff_at,
            quota_remaining=quota_remaining,
        )
    return {
        "schema_version": 1,
        "mode": "dry-run",
        "observed_at": decision.now,
        "snapshot_path": str(snapshot_path),
        "quota_path": str(quota_path),
        "last_refresh_at": decision.last_refresh_at,
        "next_kickoff_at": decision.next_kickoff_at,
        "quota": quota,
        "decision": decision.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the local refresh scheduling decision as JSON.")
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--now", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args(argv)

    report = build_scheduler_report(
        now=args.now,
        snapshot_path=args.snapshot_path,
        quota_path=args.quota_path,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
