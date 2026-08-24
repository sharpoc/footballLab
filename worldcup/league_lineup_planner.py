from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("league_lineup_now_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _add_skip(skipped: dict[str, dict[str, int]], competition_id: str, reason: str) -> None:
    counters = skipped.setdefault(competition_id, {})
    counters[reason] = counters.get(reason, 0) + 1


def _active_competitions(report: Mapping[str, Any]) -> set[str]:
    if report.get("schema_version") != 1:
        return set()
    rows = report.get("competitions")
    if not isinstance(rows, Mapping):
        return set()
    return {
        str(competition_id)
        for competition_id, row in rows.items()
        if isinstance(row, Mapping)
        and row.get("competition_id") == competition_id
        and row.get("state") == "active"
        and isinstance(row.get("fingerprints"), Mapping)
        and all(bool(str(row["fingerprints"].get(name) or "").strip()) for name in (
            "sport_catalog", "odds_sample", "team_identity", "result_contract"
        ))
    }


def _event_state(state: Mapping[str, Any], competition_id: str, event_id: str) -> Mapping[str, Any]:
    events = state.get("events")
    if not isinstance(events, Mapping):
        return {}
    row = events.get(f"{competition_id}:{event_id}")
    return row if isinstance(row, Mapping) else {}


def plan_league_lineup_poll(
    *,
    now: Any,
    fixtures_by_competition: Mapping[str, list[Mapping[str, Any]]],
    acceptance_report: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    now_dt = _utc(now)
    active_competitions = _active_competitions(acceptance_report)
    skipped: dict[str, dict[str, int]] = {}
    requests: list[dict[str, Any]] = []
    fixture_count = 0
    eligible_count = 0
    next_candidates: list[datetime] = []
    for competition_id in sorted(fixtures_by_competition):
        fixtures = fixtures_by_competition[competition_id]
        for fixture in fixtures:
            fixture_count += 1
            if competition_id not in active_competitions:
                _add_skip(skipped, competition_id, "acceptance_not_active")
                continue
            try:
                kickoff = _utc(fixture.get("kickoff_at_utc") or fixture.get("commence_time"))
            except (TypeError, ValueError):
                _add_skip(skipped, competition_id, "invalid_kickoff")
                continue
            event_id = str(fixture.get("event_id") or fixture.get("id") or fixture.get("source_event_id") or "").strip()
            if not event_id:
                _add_skip(skipped, competition_id, "invalid_event")
                continue
            event_state = _event_state(state, competition_id, event_id)
            if event_state.get("confirmed") is True or bool(str(event_state.get("accepted_fingerprint") or "").strip()):
                _add_skip(skipped, competition_id, "lineup_confirmed")
                continue
            if str(fixture.get("lineup_status") or "").strip().upper() == "CONFIRMED":
                _add_skip(skipped, competition_id, "lineup_confirmed")
                continue
            status = str(fixture.get("fixture_status") or fixture.get("status") or "").strip().upper()
            terminal_reasons = {
                "POSTPONED": "fixture_postponed",
                "CANCELLED": "fixture_cancelled",
                "CANCELED": "fixture_cancelled",
                "STARTED": "fixture_started",
                "LIVE": "fixture_started",
                "IN_PROGRESS": "fixture_started",
                "FINISHED": "fixture_started",
            }
            if status in terminal_reasons:
                _add_skip(skipped, competition_id, terminal_reasons[status])
                continue
            if kickoff <= now_dt:
                _add_skip(skipped, competition_id, "post_kickoff")
                continue
            eligible_count += 1
            if kickoff - now_dt > timedelta(minutes=90):
                _add_skip(skipped, competition_id, "outside_poll_window")
                next_candidates.append(kickoff - timedelta(minutes=90))
                continue
            interval = timedelta(minutes=5 if kickoff - now_dt <= timedelta(minutes=45) else 15)
            last_polled_at = event_state.get("last_polled_at")
            if last_polled_at:
                try:
                    last_polled_dt = _utc(last_polled_at)
                except (TypeError, ValueError):
                    last_polled_dt = None
                if last_polled_dt is not None and last_polled_dt + interval > now_dt:
                    due_at = last_polled_dt + interval
                    _add_skip(skipped, competition_id, "poll_throttled")
                    if due_at < kickoff:
                        next_candidates.append(due_at)
                    continue
            next_candidates.append(now_dt)
            requests.append({
                "competition_id": competition_id,
                "event_id": event_id,
                "kickoff_at_utc": kickoff.isoformat(),
                "poll_interval_seconds": int(interval.total_seconds()),
            })
    requests.sort(key=lambda row: (row["competition_id"], row["event_id"]))
    return {
        "generated_at": now_dt.isoformat(),
        "requests": requests,
        "skipped": skipped,
        "next_due_at": min(next_candidates).isoformat() if next_candidates else None,
        "counts": {
            "fixture_count": fixture_count,
            "eligible_count": eligible_count,
            "request_count": len(requests),
            "skipped_count": sum(sum(reasons.values()) for reasons in skipped.values()),
        },
    }
