from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from worldcup.competitions import get_competition


_ANCHORS: tuple[tuple[str, int, tuple[str, ...], int], ...] = (
    ("T-25m", 25 * 60, ("h2h", "spreads", "totals"), 0),
    ("T-90m", 90 * 60, ("h2h",), 1),
    ("T-6h", 6 * 60 * 60, ("h2h",), 2),
)


def _utc(value: Any, *, error: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc)


def _anchor(seconds_to_kickoff: float) -> tuple[str, tuple[str, ...], int] | None:
    lower = 0
    for name, upper, markets, priority in _ANCHORS:
        if lower < seconds_to_kickoff <= upper:
            return name, markets, priority
        lower = upper
    return None


def _add_skip(skipped: dict[str, dict[str, int]], competition_id: str, reason: str, count: int = 1) -> None:
    bucket = skipped.setdefault(competition_id, {})
    bucket[reason] = bucket.get(reason, 0) + count


def plan_league_live_refresh(
    *,
    now: str,
    events_by_competition: Mapping[str, list[dict[str, Any]]],
    acceptance_by_competition: Mapping[str, str],
    quota_remaining: int | None,
) -> dict[str, Any]:
    now_dt = _utc(now, error="league_live_now_must_be_timezone_aware")
    if quota_remaining is not None and quota_remaining <= 0:
        return {
            "generated_at": now_dt.isoformat(),
            "requests": [],
            "estimated_credits": 0,
            "skipped": {},
            "stop_reason": "quota_exhausted",
        }

    skipped: dict[str, dict[str, int]] = {}
    candidates: list[dict[str, Any]] = []
    for competition_id, raw_events in events_by_competition.items():
        state = str(acceptance_by_competition.get(competition_id) or "disabled_until_live_acceptance")
        if state not in {"active", "probing"}:
            _add_skip(skipped, competition_id, "acceptance_blocked", len(raw_events) or 1)
            continue
        try:
            profile = get_competition(competition_id)
        except KeyError:
            _add_skip(skipped, competition_id, "unknown_competition", len(raw_events) or 1)
            continue
        if not profile.theoddsapi_sport_key:
            _add_skip(skipped, competition_id, "missing_sport_key", len(raw_events) or 1)
            continue

        parsed: list[tuple[str, datetime, dict[str, Any]]] = []
        event_kickoffs: dict[str, set[datetime]] = {}
        for event in raw_events:
            event_id = str(event.get("id") or event.get("event_id") or "").strip()
            if not event_id:
                _add_skip(skipped, competition_id, "invalid_event")
                continue
            try:
                kickoff = _utc(event.get("commence_time") or event.get("kickoff_at_utc"), error="invalid_kickoff")
            except (TypeError, ValueError):
                _add_skip(skipped, competition_id, "invalid_event")
                continue
            event_kickoffs.setdefault(event_id, set()).add(kickoff)
            parsed.append((event_id, kickoff, event))
        conflicts = {event_id for event_id, values in event_kickoffs.items() if len(values) > 1}
        if conflicts:
            _add_skip(
                skipped,
                competition_id,
                "event_identity_conflict",
                sum(event_id in conflicts for event_id, _, _ in parsed),
            )

        grouped: dict[tuple[str, tuple[str, ...], str], list[tuple[str, datetime]]] = {}
        for event_id, kickoff, event in parsed:
            if event_id in conflicts:
                continue
            if kickoff <= now_dt:
                _add_skip(skipped, competition_id, "post_kickoff")
                continue
            seconds = (kickoff - now_dt).total_seconds()
            selected = _anchor(seconds)
            reason = "scheduled_anchor"
            if state == "active":
                decision = event.get("match_decision")
                valid_until = decision.get("valid_until") if isinstance(decision, dict) else None
                if decision and decision.get("label") == "MATCH_PICK" and valid_until:
                    try:
                        guard_at = _utc(valid_until, error="invalid_valid_until") - timedelta(minutes=20)
                    except (TypeError, ValueError):
                        guard_at = None
                    if guard_at is not None and guard_at <= now_dt < kickoff:
                        selected = ("EXPIRY", ("h2h",), -1)
                        reason = "pick_expiry_guard"
            elif state == "probing":
                selected = ("PROBE", ("h2h",), 3)
                reason = "acceptance_probe"
            if selected is None:
                _add_skip(skipped, competition_id, "not_due")
                continue
            anchor, markets, priority = selected
            grouped.setdefault((anchor, markets, reason), []).append((event_id, kickoff))
            # priority is recovered below from anchor/reason to keep the grouping key serializable.

        for (anchor, markets, reason), events in grouped.items():
            events.sort(key=lambda item: (item[1], item[0]))
            priority = -1 if reason == "pick_expiry_guard" else next(
                (item[3] for item in _ANCHORS if item[0] == anchor),
                3,
            )
            candidates.append({
                "competition_id": competition_id,
                "sport_key": profile.theoddsapi_sport_key,
                "anchor": anchor,
                "reason": reason,
                "markets": list(markets),
                "event_ids": [event_id for event_id, _ in events],
                "next_kickoff_at": events[0][1].isoformat(),
                "estimated_credits": len(markets),
                "_priority": priority,
            })

    candidates.sort(key=lambda row: (
        row["_priority"],
        row["next_kickoff_at"],
        row["competition_id"],
        row["event_ids"][0],
    ))
    for row in candidates:
        row.pop("_priority", None)
    return {
        "generated_at": now_dt.isoformat(),
        "requests": candidates,
        "estimated_credits": sum(int(row["estimated_credits"]) for row in candidates),
        "skipped": skipped,
        "stop_reason": None,
    }
