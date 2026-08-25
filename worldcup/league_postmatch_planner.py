from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_acceptance import acceptance_row_is_active


def _utc(value: Any, *, error: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc)


def _active_competitions(acceptance: Mapping[str, Any]) -> set[str]:
    rows = acceptance.get("competitions") if acceptance.get("schema_version") == 1 else None
    if not isinstance(rows, Mapping):
        return set()
    return {
        competition_id
        for competition_id, row in rows.items()
        if competition_id in FORMAL_SINGLE_MATCH_IDS and acceptance_row_is_active(row, competition_id)
    }


def _accepted_result_ids(state: Mapping[str, Any], competition_id: str) -> set[str]:
    receipts = state.get("accepted_results")
    receipt = receipts.get(competition_id) if isinstance(receipts, Mapping) else None
    rows = receipt.get("results") if isinstance(receipt, Mapping) else None
    if not isinstance(rows, list):
        return set()
    return {
        event_id
        for row in rows
        if isinstance(row, Mapping)
        and row.get("result_scope") == "football_90min"
        and (event_id := str(row.get("source_event_id") or "").strip())
    }


def _add_blocked(blocked: dict[str, dict[str, int]], competition_id: str, reason: str) -> None:
    counters = blocked.setdefault(competition_id, {})
    counters[reason] = counters.get(reason, 0) + 1


def _event_id(fixture: Mapping[str, Any]) -> str:
    return str(fixture.get("source_event_id") or fixture.get("event_id") or fixture.get("id") or "").strip()


def plan_league_postmatch(
    acceptance: Mapping[str, Any],
    fixtures: Mapping[str, list[dict[str, Any]]],
    state: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Plan only provider checks; a past kickoff is never a settlement signal."""
    now_dt = _utc(now, error="league_postmatch_now_must_be_timezone_aware")
    active = _active_competitions(acceptance)
    blocked: dict[str, dict[str, int]] = {}
    due: list[dict[str, str]] = []
    next_candidates: list[datetime] = []
    competitions: dict[str, dict[str, Any]] = {}

    for competition_id in sorted(fixtures):
        values = fixtures[competition_id]
        rows = values if isinstance(values, list) else []
        competition_blocked: dict[str, int] = {}
        due_count = 0
        accepted = _accepted_result_ids(state, competition_id)
        for fixture in rows:
            if not isinstance(fixture, Mapping):
                _add_blocked(blocked, competition_id, "fixture_invalid")
                continue
            if competition_id not in FORMAL_SINGLE_MATCH_IDS or competition_id not in active:
                _add_blocked(blocked, competition_id, "acceptance_not_active")
                continue
            event_id = _event_id(fixture)
            home = str(fixture.get("home_canonical") or "").strip()
            away = str(fixture.get("away_canonical") or "").strip()
            if not event_id or not home or not away:
                _add_blocked(blocked, competition_id, "strict_identity_missing")
                continue
            try:
                kickoff = _utc(fixture.get("kickoff_at_utc") or fixture.get("commence_time"), error="invalid_kickoff")
            except (TypeError, ValueError):
                _add_blocked(blocked, competition_id, "invalid_kickoff")
                continue
            status = str(fixture.get("fixture_status") or fixture.get("status") or "").strip().upper()
            if status == "POSTPONED":
                _add_blocked(blocked, competition_id, "fixture_postponed")
                continue
            if status in {"CANCELLED", "CANCELED"}:
                _add_blocked(blocked, competition_id, "fixture_cancelled")
                continue
            if event_id in accepted:
                _add_blocked(blocked, competition_id, "accepted_result_exists")
                continue
            if kickoff > now_dt:
                next_candidates.append(kickoff)
                continue
            due.append({
                "competition_id": competition_id,
                "source_event_id": event_id,
                "kickoff_at_utc": kickoff.isoformat(),
                "home_canonical": home,
                "away_canonical": away,
            })
            due_count += 1
            next_candidates.append(now_dt)
        competition_blocked = dict(blocked.get(competition_id) or {})
        competitions[competition_id] = {
            "fixture_count": len(rows),
            "due_count": due_count,
            "blocked": competition_blocked,
        }
    due.sort(key=lambda row: (row["competition_id"], row["source_event_id"]))
    return {
        "generated_at": now_dt.isoformat(),
        "due": due,
        "blocked": {competition_id: blocked[competition_id] for competition_id in sorted(blocked)},
        "competitions": competitions,
        "next_due_at": min(next_candidates).isoformat() if next_candidates else None,
    }
