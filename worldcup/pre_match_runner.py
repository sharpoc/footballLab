from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from worldcup.lineup_audit import build_lineup_audit, send_lineup_audit_notification
from worldcup.lineups_refresh import (
    DEFAULT_NOTIFICATION_STATE_PATH,
    DEFAULT_OUT_PATH,
    run_lineups_refresh,
)
from worldcup.scheduled_refresh import run_scheduled_refresh


DEFAULT_MIN_REFRESH_QUOTA = 1


def _decision_summary(report: dict | None) -> dict:
    decision = report.get("decision") if isinstance(report, dict) else {}
    if not isinstance(decision, dict):
        decision = {}
    return {
        "should_refresh": bool(decision.get("should_refresh")),
        "reason": decision.get("reason"),
        "policy_reason": decision.get("policy_reason"),
        "next_due_at": decision.get("next_due_at"),
    }


def _quota_remaining_from_report(report: dict | None) -> int | None:
    decision = report.get("decision") if isinstance(report, dict) else {}
    if not isinstance(decision, dict):
        return None
    quota_remaining = decision.get("quota_remaining")
    if isinstance(quota_remaining, bool):
        return None
    return quota_remaining if isinstance(quota_remaining, int) else None


def _build_refresh_guard(
    *,
    refresh_result: dict,
    min_quota_remaining: int,
    would_live_refresh: bool,
) -> dict:
    report = refresh_result.get("report") if isinstance(refresh_result, dict) else None
    quota_remaining = _quota_remaining_from_report(report)
    if quota_remaining is None:
        status = "review_required"
        reason = "quota_unknown"
    elif quota_remaining < min_quota_remaining:
        status = "blocked"
        reason = "quota_below_min"
    else:
        status = "allowed"
        reason = "quota_available"
    return {
        "status": status,
        "reason": reason,
        "mode": "dry_run",
        "would_force": True,
        "would_live_refresh": would_live_refresh,
        "quota_remaining": quota_remaining,
        "min_quota_remaining": min_quota_remaining,
        "decision": _decision_summary(report),
    }


def _post_information_refresh_state(
    *,
    newly_confirmed: int,
    refresh_after_lineups: bool,
    refresh_result: dict | None = None,
    refresh_guard: dict | None = None,
    blocked_by_guard: bool = False,
) -> dict:
    if newly_confirmed <= 0:
        return {"status": "skipped", "reason": "no_new_confirmed_lineups"}
    if blocked_by_guard:
        return {
            "status": "blocked",
            "reason": (refresh_guard or {}).get("reason") or "refresh_guard_blocked",
            "newly_confirmed": newly_confirmed,
            "guard": refresh_guard,
        }
    if not refresh_after_lineups:
        state = {
            "status": "required",
            "reason": "new_confirmed_lineups",
            "newly_confirmed": newly_confirmed,
        }
        if refresh_guard is not None:
            state["guard"] = refresh_guard
        return state
    state = refresh_result or {
        "status": "skipped",
        "reason": "refresh_result_missing",
    }
    if refresh_guard is not None:
        state = {**state, "guard": refresh_guard}
    return state


def run_pre_match_cycle(
    *,
    now: str | None = None,
    live_lineups: bool = False,
    write_lineups: bool = False,
    notify_missing: bool = False,
    refresh_after_lineups: bool = False,
    live_refresh: bool = False,
    refresh_guard: bool = False,
    min_refresh_quota: int = DEFAULT_MIN_REFRESH_QUOTA,
    env_path: str | Path = ".env",
    cache_dir: str | Path = "data/cache",
    snapshot_path: str | Path = "data/cache/analysis_snapshot.json",
    history_dir: str | Path = "data/local/history",
    quota_path: str | Path = "data/cache/quota.json",
    lineups_out_path: str | Path = DEFAULT_OUT_PATH,
    notification_state_path: str | Path = DEFAULT_NOTIFICATION_STATE_PATH,
    notify_audit: bool = False,
    lineups_refresh_fn: Callable[..., dict] = run_lineups_refresh,
    lineup_audit_fn: Callable[..., dict] = build_lineup_audit,
    lineup_audit_notification_fn: Callable[..., dict] = send_lineup_audit_notification,
    scheduled_refresh_fn: Callable[..., dict] = run_scheduled_refresh,
) -> dict:
    lineups = lineups_refresh_fn(
        live=live_lineups,
        write=write_lineups,
        notify=notify_missing,
        now=now,
        out_path=lineups_out_path,
        notification_state_path=notification_state_path,
    )
    newly_confirmed = int(lineups.get("newly_confirmed") or 0)
    lineup_audit = None
    if notify_audit:
        report = lineup_audit_fn(
            lineups_path=lineups_out_path,
            snapshot_path=snapshot_path,
            history_dir=history_dir,
            notification_state_path=notification_state_path,
            generated_at=now,
        )
        lineup_audit = {
            "summary": report.get("summary") or {},
            "notification": lineup_audit_notification_fn(
                report,
                state_path=notification_state_path,
            ),
        }

    if newly_confirmed <= 0:
        return {
            "status": "dry_run" if lineups.get("status") == "dry_run" else "lineups_checked",
            "lineups": lineups,
            "lineup_audit": lineup_audit,
            "post_information_refresh": _post_information_refresh_state(
                newly_confirmed=newly_confirmed,
                refresh_after_lineups=refresh_after_lineups,
            ),
        }

    guard_result = None
    guard = None
    if refresh_guard:
        guard_result = scheduled_refresh_fn(
            now=now,
            live=False,
            force=True,
            env_path=env_path,
            cache_dir=cache_dir,
            snapshot_path=snapshot_path,
            quota_path=quota_path,
        )
        guard = _build_refresh_guard(
            refresh_result=guard_result,
            min_quota_remaining=max(0, int(min_refresh_quota)),
            would_live_refresh=bool(refresh_after_lineups and live_refresh),
        )

    if not refresh_after_lineups:
        return {
            "status": "post_information_refresh_required",
            "lineups": lineups,
            "lineup_audit": lineup_audit,
            "post_information_refresh": _post_information_refresh_state(
                newly_confirmed=newly_confirmed,
                refresh_after_lineups=False,
                refresh_guard=guard,
            ),
        }

    blocked_by_guard = bool(live_refresh and guard and guard.get("status") != "allowed")
    if blocked_by_guard:
        return {
            "status": "post_information_refresh_blocked",
            "lineups": lineups,
            "lineup_audit": lineup_audit,
            "post_information_refresh": _post_information_refresh_state(
                newly_confirmed=newly_confirmed,
                refresh_after_lineups=True,
                refresh_guard=guard,
                blocked_by_guard=True,
            ),
        }

    refresh = guard_result if guard_result is not None and not live_refresh else None
    if refresh is None:
        refresh = scheduled_refresh_fn(
            now=now,
            live=live_refresh,
            force=True,
            env_path=env_path,
            cache_dir=cache_dir,
            snapshot_path=snapshot_path,
            quota_path=quota_path,
        )
    return {
        "status": "post_information_refreshed"
        if refresh.get("status") == "refreshed"
        else "post_information_refresh_checked",
        "lineups": lineups,
        "lineup_audit": lineup_audit,
        "post_information_refresh": _post_information_refresh_state(
            newly_confirmed=newly_confirmed,
            refresh_after_lineups=True,
            refresh_result=refresh,
            refresh_guard=guard,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the pre-match lineup cycle. Defaults to a dry-run with no network, writes, notifications, or odds refresh."
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--live-lineups", action="store_true", help="Fetch FIFA public lineups.")
    parser.add_argument("--write-lineups", action="store_true", help="Write confirmed lineups to cache.")
    parser.add_argument("--notify-missing", action="store_true", help="Notify when official lineups are missing near kickoff.")
    parser.add_argument("--refresh-after-lineups", action="store_true", help="Run scheduled_refresh with --force after newly confirmed lineups.")
    parser.add_argument("--live-refresh", action="store_true", help="Allow the forced post-lineup refresh to call live sources and consume quota.")
    parser.add_argument("--refresh-guard", action="store_true", help="Dry-run the post-lineup refresh decision and block live refresh when quota is not usable.")
    parser.add_argument("--min-refresh-quota", type=int, default=DEFAULT_MIN_REFRESH_QUOTA, help="Minimum remaining The Odds API quota required by --refresh-guard.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--history-dir", default="data/local/history")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--lineups-out", default=DEFAULT_OUT_PATH)
    parser.add_argument("--notification-state-path", default=DEFAULT_NOTIFICATION_STATE_PATH)
    parser.add_argument("--notify-audit", action="store_true", help="Notify once for pre-kickoff lineup audit gaps.")
    parser.add_argument("--env", default=".env")
    args = parser.parse_args(argv)

    result = run_pre_match_cycle(
        now=args.now,
        live_lineups=args.live_lineups,
        write_lineups=args.write_lineups,
        notify_missing=args.notify_missing,
        refresh_after_lineups=args.refresh_after_lineups,
        live_refresh=args.live_refresh,
        refresh_guard=args.refresh_guard,
        min_refresh_quota=args.min_refresh_quota,
        env_path=args.env,
        cache_dir=args.cache_dir,
        snapshot_path=args.snapshot_path,
        history_dir=args.history_dir,
        quota_path=args.quota_path,
        lineups_out_path=args.lineups_out,
        notification_state_path=args.notification_state_path,
        notify_audit=args.notify_audit,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
