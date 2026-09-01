from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_acceptance import acceptance_row_is_active
from worldcup.league_team_identity import (
    LeagueTeamIdentityRegistry,
    league_team_identity_registry_fingerprint,
)


_MARKET_ORDER = ("h2h", "spreads", "totals")
_ANCHORS = (("T-25m", 25 * 60, ("h2h", "spreads", "totals"), 0), ("T-90m", 90 * 60, ("h2h",), 1), ("T-6h", 6 * 60 * 60, ("h2h",), 3))


def _utc(value: Any, error: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _skip(skipped: dict[str, dict[str, int]], competition_id: str, reason: str) -> None:
    bucket = skipped.setdefault(competition_id, {})
    bucket[reason] = bucket.get(reason, 0) + 1


def merge_market_requests(requests: list[dict]) -> list[dict]:
    """Merge due work into one request per competition without dropping markets."""
    grouped: dict[str, list[dict]] = {}
    for request in requests:
        if not isinstance(request, Mapping):
            raise ValueError("daily_market_request_invalid")
        competition_id = request.get("competition_id")
        event_ids = request.get("event_ids")
        markets = request.get("markets")
        if (
            not isinstance(competition_id, str) or competition_id not in FORMAL_SINGLE_MATCH_IDS
            or not isinstance(event_ids, list) or (not event_ids and request.get("anchors") != ["DISCOVERY"]) or not all(isinstance(event_id, str) and event_id for event_id in event_ids)
            or not isinstance(markets, list) or not markets or not all(market in _MARKET_ORDER for market in markets)
        ):
            raise ValueError("daily_market_request_invalid")
        grouped.setdefault(competition_id, []).append(request)

    merged: list[dict[str, Any]] = []
    for competition_id in sorted(grouped):
        rows = grouped[competition_id]
        markets = [market for market in _MARKET_ORDER if any(market in row["markets"] for row in rows)]
        event_ids = sorted({event_id for row in rows for event_id in row["event_ids"]})
        request: dict[str, Any] = {
            "competition_id": competition_id,
            "event_ids": event_ids,
            "markets": markets,
            "estimated_credits": len(markets),
        }
        sport_keys = {row.get("sport_key") for row in rows if row.get("sport_key") is not None}
        if len(sport_keys) > 1 or any(not isinstance(key, str) or not key for key in sport_keys):
            raise ValueError("daily_market_request_transport_conflict")
        if sport_keys:
            request["sport_key"] = sport_keys.pop()
        metadata: dict[str, Any] = {}
        for row in rows:
            value = row.get("anchor_metadata")
            if value is None:
                continue
            if not isinstance(value, Mapping):
                raise ValueError("daily_market_request_invalid")
            for event_id, detail in value.items():
                incoming = detail if isinstance(detail, list) else [detail]
                if not all(isinstance(signature, str) for signature in incoming):
                    raise ValueError("daily_market_request_invalid")
                existing = metadata.setdefault(event_id, [])
                if not isinstance(existing, list):
                    raise ValueError("daily_market_request_anchor_conflict")
                existing.extend(signature for signature in incoming if signature not in existing)
        if metadata:
            request["anchor_metadata"] = {event_id: metadata[event_id] for event_id in sorted(metadata)}
        merged.append(request)
    return merged


def _active_rows(acceptance: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = acceptance.get("competitions") if acceptance.get("schema_version") == 1 else None
    if not isinstance(rows, Mapping):
        return {}
    return {
        competition_id: row for competition_id, row in rows.items()
        if isinstance(competition_id, str) and competition_id in FORMAL_SINGLE_MATCH_IDS
        and acceptance_row_is_active(row, competition_id)
    }


def _canonical_event(competition_id: str, raw: Mapping[str, Any], registry: LeagueTeamIdentityRegistry, snapshot_id: str) -> dict[str, Any] | None:
    event_id = str(raw.get("source_event_id") or raw.get("event_id") or raw.get("id") or "").strip()
    kickoff_value = raw.get("kickoff_at_utc") or raw.get("kickoff") or raw.get("commence_time")
    if not event_id or not kickoff_value:
        return None
    try:
        kickoff = _utc(kickoff_value, "event_kickoff_invalid")
    except (TypeError, ValueError):
        return None
    home = raw.get("home_canonical")
    away = raw.get("away_canonical")
    if isinstance(home, str) and isinstance(away, str):
        allowed = set(registry._aliases.get(competition_id, {}).values())
        if home not in allowed or away not in allowed:
            return None
    else:
        fixture = registry.resolve_fixture(competition_id, str(raw.get("home_team") or raw.get("home") or ""), str(raw.get("away_team") or raw.get("away") or ""))
        if fixture["status"] != "verified":
            return None
        home, away = fixture["home_canonical"], fixture["away_canonical"]
    if not home or not away or home == away:
        return None
    event_snapshot_id = raw.get("source_snapshot_id", snapshot_id)
    if not isinstance(event_snapshot_id, str) or not event_snapshot_id.strip():
        return None
    event = {"event_id": event_id, "kickoff_at_utc": _iso(kickoff), "home_canonical": home, "away_canonical": away, "source_snapshot_id": event_snapshot_id.strip()}
    decision = raw.get("match_decision")
    if isinstance(decision, Mapping):
        event["match_decision"] = dict(decision)
    return event


def load_daily_events(root: Path, acceptance: dict, registry: LeagueTeamIdentityRegistry) -> dict:
    """Read only production event cache/snapshot, preserving strict identity evidence."""
    active = _active_rows(acceptance) if isinstance(acceptance, Mapping) else {}
    events: dict[str, list[dict[str, Any]]] = {}
    errors: list[dict[str, str]] = []
    for competition_id, row in sorted(active.items()):
        fingerprints = row.get("fingerprints")
        if not isinstance(fingerprints, Mapping) or fingerprints.get("team_identity") != league_team_identity_registry_fingerprint(registry, competition_id):
            errors.append({"competition_id": competition_id, "reason": "acceptance_identity_fingerprint_mismatch"})
            continue
        cache_dir = Path(root) / "data/cache/leagues" / competition_id
        events_path = cache_dir / "events.json"
        source: Mapping[str, Any] | None = None
        from_events_cache = events_path.exists()
        if from_events_cache:
            try:
                value = json.loads(events_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = None
            try:
                _utc(value.get("observed_at"), "events_observed_at_invalid") if isinstance(value, Mapping) else None
            except (TypeError, ValueError):
                value = None
            if not isinstance(value, Mapping) or value.get("schema_version") != 1 or value.get("competition_id") != competition_id or not isinstance(value.get("events"), list) or not isinstance(value.get("source_snapshot_id"), str) or not value.get("source_snapshot_id").strip():
                errors.append({"competition_id": competition_id, "reason": "production_events_invalid"})
                continue
            source = value
        else:
            snapshot_path = cache_dir / "snapshot.json"
            if not snapshot_path.exists():
                errors.append({"competition_id": competition_id, "reason": "production_events_missing"})
                continue
            try:
                value = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = None
            if not isinstance(value, Mapping) or not isinstance(value.get("snapshot_id"), str) or not value.get("snapshot_id").strip() or not isinstance(value.get("matches"), list) or (value.get("competition") or {}).get("id") != competition_id:
                errors.append({"competition_id": competition_id, "reason": "production_snapshot_invalid"})
                continue
            source = {"events": value["matches"], "source_snapshot_id": value["snapshot_id"].strip()}
        rows = source["events"]
        canonical = [_canonical_event(competition_id, event, registry, source["source_snapshot_id"]) for event in rows if isinstance(event, Mapping)]
        if len(canonical) != len(rows) or any(event is None for event in canonical):
            errors.append({"competition_id": competition_id, "reason": "production_event_identity_invalid"})
            continue
        event_rows = [event for event in canonical if event is not None]
        identity = {(event["event_id"], event["kickoff_at_utc"]) for event in event_rows}
        if len(identity) != len(event_rows) or len({event["event_id"] for event in event_rows}) != len(event_rows):
            errors.append({"competition_id": competition_id, "reason": "production_event_identity_conflict"})
            continue
        events[competition_id] = sorted(event_rows, key=lambda event: (event["kickoff_at_utc"], event["event_id"]))
    return {"competitions": sorted(events), "events": events, "errors": errors}


def _state_budget(state: Mapping[str, Any], now: datetime) -> tuple[int | None, str | None]:
    budgets = state.get("budgets", {})
    if not isinstance(budgets, Mapping):
        return None, "daily_refresh_state_invalid"
    day = (now + timedelta(hours=8)).date().isoformat()
    if day not in budgets:
        return 0, None
    value = budgets[day]
    if not isinstance(value, Mapping) or type(value.get("reserved_credits")) is not int or value["reserved_credits"] < 0:
        return None, "daily_refresh_state_invalid"
    return value["reserved_credits"], None


def _completed_signatures(state: Mapping[str, Any], competition_id: str) -> set[str] | None:
    competitions = state.get("competitions")
    if not isinstance(competitions, Mapping):
        return None
    row = competitions.get(competition_id, {})
    if not isinstance(row, Mapping):
        return None
    values = row.get("successful_anchors", [])
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return set(values)


def _select_anchor(now: datetime, event: Mapping[str, Any], quota_mode: str) -> list[tuple[str, tuple[str, ...], int, str]]:
    kickoff = _utc(event["kickoff_at_utc"], "event_kickoff_invalid")
    seconds = (kickoff - now).total_seconds()
    if seconds <= 0:
        return []
    selected: list[tuple[str, tuple[str, ...], int, str]] = []
    decision = event.get("match_decision")
    if quota_mode == "normal" and isinstance(decision, Mapping) and decision.get("label") == "MATCH_PICK":
        try:
            valid_until = _utc(decision.get("valid_until"), "decision_valid_until_invalid")
        except (TypeError, ValueError):
            valid_until = None
        snapshot_id = event.get("source_snapshot_id")
        if valid_until is not None and valid_until - timedelta(minutes=20) <= now < kickoff and isinstance(snapshot_id, str) and snapshot_id.strip():
            selected.append(("EXPIRY", ("h2h",), 2, f"{event['competition_id']}|{event['event_id']}|{event['kickoff_at_utc']}|EXPIRY|{_iso(valid_until)}|{snapshot_id.strip()}"))
    for name, upper, markets, priority in _ANCHORS:
        if seconds <= upper:
            if quota_mode == "low" and name != "T-25m":
                return selected
            markets = ("h2h",) if quota_mode == "low" else markets
            selected.append((name, markets, priority, f"{event['competition_id']}|{event['event_id']}|{event['kickoff_at_utc']}|{name}"))
            return selected
    return selected


def plan_daily_refresh(*, now: str, events: dict, acceptance: dict, state: dict, quota_mode: str, daily_credit_limit: int | None) -> dict:
    """Pure due planner. `budgets[Beijing date].reserved_credits` is consumed plus pending credit occupancy."""
    now_dt = _utc(now, "daily_refresh_now_must_be_timezone_aware")
    result: dict[str, Any] = {"requests": [], "estimated_credits": 0, "skipped": {}, "next_due_at": None, "live_blockers": []}
    if not isinstance(state, Mapping) or state.get("schema_version") != 1:
        result["live_blockers"].append("daily_refresh_state_invalid")
        return result
    competitions = state.get("competitions")
    if not isinstance(competitions, Mapping):
        result["live_blockers"].append("daily_refresh_state_invalid")
        return result
    for row in competitions.values():
        if not isinstance(row, Mapping) or not isinstance(row.get("last_attempt_signatures", {}), Mapping):
            result["live_blockers"].append("daily_refresh_state_invalid")
            return result
        for signature, attempted_at in row.get("last_attempt_signatures", {}).items():
            if not isinstance(signature, str) or not signature.strip() or not isinstance(attempted_at, str):
                result["live_blockers"].append("daily_refresh_state_invalid")
                return result
            try:
                _utc(attempted_at, "anchor_attempt_invalid")
            except (TypeError, ValueError):
                result["live_blockers"].append("daily_refresh_state_invalid")
                return result
    reserved, state_error = _state_budget(state, now_dt)
    if state_error:
        result["live_blockers"].append(state_error)
        return result
    if quota_mode not in {"normal", "low", "exhausted"}:
        result["live_blockers"].append("quota_mode_invalid")
        return result
    if daily_credit_limit is None:
        result["live_blockers"].append("daily_budget_unconfigured")
    elif type(daily_credit_limit) is not int or daily_credit_limit <= 0:
        result["live_blockers"].append("daily_credit_limit_invalid")
        return result
    active = _active_rows(acceptance) if isinstance(acceptance, Mapping) else {}
    candidates: list[dict[str, Any]] = []
    for competition_id, raw_events in sorted(events.items() if isinstance(events, Mapping) else []):
        if competition_id not in active:
            _skip(result["skipped"], str(competition_id), "acceptance_not_active")
            continue
        completed = _completed_signatures(state, competition_id)
        if completed is None:
            result["live_blockers"].append("daily_refresh_state_invalid")
            return result
        if not isinstance(raw_events, list):
            _skip(result["skipped"], competition_id, "events_invalid")
            continue
        if not raw_events:
            if quota_mode == "low":
                _skip(result["skipped"], competition_id, "discovery_low_quota_suppressed")
                continue
            row = state.get("competitions", {}).get(competition_id, {})
            if not isinstance(row, Mapping):
                result["live_blockers"].append("daily_refresh_state_invalid"); return result
            due = now_dt
            if row.get("next_discovery_at") is not None:
                try: due = _utc(row["next_discovery_at"], "discovery_due_invalid")
                except (TypeError, ValueError): result["live_blockers"].append("daily_refresh_state_invalid"); return result
            elif row.get("last_attempt_at") is not None:
                try:
                    attempted = _utc(row["last_attempt_at"], "discovery_attempt_invalid")
                    succeeded = _utc(row["last_success_at"], "discovery_success_invalid") if row.get("last_success_at") is not None else None
                except (TypeError, ValueError): result["live_blockers"].append("daily_refresh_state_invalid"); return result
                due = attempted + timedelta(hours=24) if succeeded is not None and succeeded >= attempted else attempted + timedelta(minutes=30)
            if due <= now_dt:
                candidates.append({"competition_id": competition_id, "event_ids": [], "markets": ["h2h"], "anchors": ["DISCOVERY"], "anchor_metadata": {}, "_priority": 4, "_kickoff": _iso(due), "_discovery": True})
            else:
                result["next_due_at"] = _iso(due) if result["next_due_at"] is None else min(result["next_due_at"], _iso(due))
            continue
        seen: dict[str, set[str]] = {}
        has_future = False
        for event in raw_events:
            if isinstance(event, Mapping) and event.get("event_id") and event.get("kickoff_at_utc"):
                seen.setdefault(str(event["event_id"]), set()).add(str(event["kickoff_at_utc"]))
        conflicts = {event_id for event_id, kickoffs in seen.items() if len(kickoffs) > 1}
        for raw in raw_events:
            if not isinstance(raw, Mapping) or not raw.get("event_id") or not raw.get("kickoff_at_utc"):
                _skip(result["skipped"], competition_id, "event_invalid"); continue
            event = dict(raw); event["competition_id"] = competition_id
            if str(event["event_id"]) in conflicts:
                _skip(result["skipped"], competition_id, "event_identity_conflict"); continue
            if not event.get("home_canonical") or not event.get("away_canonical") or event["home_canonical"] == event["away_canonical"]:
                _skip(result["skipped"], competition_id, "identity_unverified"); continue
            try:
                kickoff = _utc(event["kickoff_at_utc"], "event_kickoff_invalid")
            except (TypeError, ValueError):
                _skip(result["skipped"], competition_id, "event_invalid"); continue
            event["kickoff_at_utc"] = _iso(kickoff)
            if kickoff <= now_dt:
                _skip(result["skipped"], competition_id, "post_kickoff"); continue
            has_future = True
            for future_name, future_seconds, _markets, _priority in _ANCHORS:
                future_signature = f"{competition_id}|{event['event_id']}|{event['kickoff_at_utc']}|{future_name}"
                future_at = kickoff - timedelta(seconds=future_seconds)
                if future_at > now_dt and future_signature not in completed:
                    future_iso = _iso(future_at)
                    result["next_due_at"] = future_iso if result["next_due_at"] is None else min(result["next_due_at"], future_iso)
            decision = event.get("match_decision")
            if quota_mode == "normal" and isinstance(decision, Mapping) and decision.get("label") == "MATCH_PICK":
                try:
                    valid_until = _utc(decision.get("valid_until"), "decision_valid_until_invalid")
                    expiry_at = valid_until - timedelta(minutes=20)
                    expiry_due = expiry_at <= now_dt < kickoff
                    if isinstance(event.get("source_snapshot_id"), str) and event["source_snapshot_id"].strip():
                        expiry_signature = f"{competition_id}|{event['event_id']}|{event['kickoff_at_utc']}|EXPIRY|{_iso(valid_until)}|{event['source_snapshot_id'].strip()}"
                        if expiry_at > now_dt and expiry_at < kickoff and expiry_signature not in completed:
                            expiry_iso = _iso(expiry_at)
                            result["next_due_at"] = expiry_iso if result["next_due_at"] is None else min(result["next_due_at"], expiry_iso)
                except (TypeError, ValueError):
                    expiry_due = False
                if expiry_due and (not isinstance(event.get("source_snapshot_id"), str) or not event["source_snapshot_id"].strip()):
                    _skip(result["skipped"], competition_id, "expiry_source_snapshot_id_invalid")
            selected = _select_anchor(now_dt, event, quota_mode)
            if not selected:
                next_anchor = kickoff - timedelta(hours=6)
                if next_anchor > now_dt:
                    next_iso = _iso(next_anchor)
                    result["next_due_at"] = next_iso if result["next_due_at"] is None else min(result["next_due_at"], next_iso)
                _skip(result["skipped"], competition_id, "not_due"); continue
            due_rows = [item for item in selected if item[3] not in completed]
            attempts = state.get("competitions", {}).get(competition_id, {}).get("last_attempt_signatures", {})
            cooled_rows = []
            for item in due_rows:
                attempted_at = attempts.get(item[3])
                if attempted_at is None:
                    cooled_rows.append(item); continue
                try:
                    retry_at = _utc(attempted_at, "anchor_attempt_invalid") + timedelta(minutes=30)
                except (TypeError, ValueError):
                    result["live_blockers"].append("daily_refresh_state_invalid"); return result
                if retry_at > now_dt:
                    _skip(result["skipped"], competition_id, "anchor_retry_cooldown")
                    retry_iso = _iso(retry_at)
                    result["next_due_at"] = retry_iso if result["next_due_at"] is None else min(result["next_due_at"], retry_iso)
                else:
                    cooled_rows.append(item)
            due_rows = cooled_rows
            if not due_rows:
                if not any(item[3] in completed for item in selected):
                    continue
                _skip(result["skipped"], competition_id, "anchor_already_completed"); continue
            for anchor, markets, priority, signature in due_rows:
                candidates.append({"competition_id": competition_id, "event_ids": [str(event["event_id"])], "markets": list(markets), "anchors": [anchor], "anchor_metadata": {str(event["event_id"]): [signature]}, "_priority": priority, "_kickoff": _iso(kickoff)})
        if not has_future:
            if quota_mode == "low":
                _skip(result["skipped"], competition_id, "discovery_low_quota_suppressed")
                continue
            row = state.get("competitions", {}).get(competition_id, {})
            if not isinstance(row, Mapping):
                result["live_blockers"].append("daily_refresh_state_invalid"); return result
            due = now_dt
            if row.get("next_discovery_at") is not None:
                try: due = _utc(row["next_discovery_at"], "discovery_due_invalid")
                except (TypeError, ValueError): result["live_blockers"].append("daily_refresh_state_invalid"); return result
            elif row.get("last_attempt_at") is not None:
                try:
                    attempted = _utc(row["last_attempt_at"], "discovery_attempt_invalid")
                    succeeded = _utc(row["last_success_at"], "discovery_success_invalid") if row.get("last_success_at") is not None else None
                except (TypeError, ValueError): result["live_blockers"].append("daily_refresh_state_invalid"); return result
                due = attempted + timedelta(hours=24) if succeeded is not None and succeeded >= attempted else attempted + timedelta(minutes=30)
            if due <= now_dt:
                candidates.append({"competition_id": competition_id, "event_ids": [], "markets": ["h2h"], "anchors": ["DISCOVERY"], "anchor_metadata": {}, "_priority": 4, "_kickoff": _iso(due), "_discovery": True})
            else:
                next_iso = _iso(due)
                result["next_due_at"] = next_iso if result["next_due_at"] is None else min(result["next_due_at"], next_iso)
    if quota_mode == "exhausted":
        result["live_blockers"].append("quota_exhausted")
        return result
    candidates.sort(key=lambda row: (row["_priority"], row["_kickoff"], row["competition_id"], row["event_ids"][0] if row["event_ids"] else ""))
    merged = merge_market_requests(candidates)
    for request in merged:
        request["anchors"] = sorted({anchor for row in candidates if row["competition_id"] == request["competition_id"] for anchor in row["anchors"]}, key=lambda anchor: ("T-25m", "T-90m", "EXPIRY", "T-6h", "DISCOVERY").index(anchor))
        request["_priority"] = min(row["_priority"] for row in candidates if row["competition_id"] == request["competition_id"])
        request["_kickoff"] = min(row["_kickoff"] for row in candidates if row["competition_id"] == request["competition_id"])
    merged.sort(key=lambda request: (request["_priority"], request["_kickoff"], request["competition_id"]))
    remaining = None if daily_credit_limit is None else daily_credit_limit - (reserved or 0)
    accepted: list[dict[str, Any]] = []
    for request in merged:
        if remaining is not None and request["estimated_credits"] > remaining:
            for event_id in request["event_ids"] or [""]:
                _skip(result["skipped"], request["competition_id"], "daily_budget_exhausted")
            continue
        accepted.append(request)
        if remaining is not None:
            remaining -= request["estimated_credits"]
    result["requests"] = accepted
    result["estimated_credits"] = sum(request["estimated_credits"] for request in accepted)
    if candidates and accepted:
        due = _iso(now_dt)
        result["next_due_at"] = due if result["next_due_at"] is None else min(result["next_due_at"], due)
    elif candidates and not accepted:
        beijing = now_dt + timedelta(hours=8)
        next_budget = datetime.combine(beijing.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        result["next_due_at"] = _iso(next_budget)
    for request in result["requests"]:
        request.pop("_priority", None); request.pop("_kickoff", None)
    return result
