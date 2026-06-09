from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worldcup.quota import load_quota_ledger

POLICY_VERSION = "free-tier-v1"
DEFAULT_INTERVAL_SECONDS = 86400
TOURNAMENT_WINDOW_DAYS = 7
TOURNAMENT_INTERVAL_SECONDS = 21600
QUOTA_LOW_REMAINING = 30
QUOTA_LOW_INTERVAL_SECONDS = 86400


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

    if next_kickoff_at is not None:
        seconds_to_kickoff = (next_kickoff_at - now).total_seconds()
        if 0 <= seconds_to_kickoff <= timedelta(days=TOURNAMENT_WINDOW_DAYS).total_seconds():
            return TOURNAMENT_INTERVAL_SECONDS, "tournament_window"

    return DEFAULT_INTERVAL_SECONDS, "default"


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
) -> dict[str, Any]:
    snapshot = _read_json_if_exists(snapshot_path)
    quota = load_quota_ledger(quota_path).get("providers", {})
    quota_remaining = quota.get("theoddsapi", {}).get("remaining")
    last_refresh_at = _last_refresh_at_from_snapshot(snapshot)
    next_kickoff_at = _next_kickoff_at_from_snapshot(snapshot, now) if snapshot else None
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
