from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.daily_competitions import (
    DailyCompetition,
    daily_competition_catalog,
    resolve_provider_catalog,
)
from worldcup.daily_selection import compute_daily_selection_window
from worldcup.combination_selection import build_combination_research
from worldcup.daily_selection import select_daily_top4
from worldcup.engine.odds import aggregate_market
from worldcup.models import MarketType

UTC = timezone.utc
BEIJING_ZONE = ZoneInfo("Asia/Shanghai")
DAILY_TIMEZONE = "Asia/Shanghai"
MAX_ODDS_AGE_SECONDS = 6 * 60 * 60

ANCHOR_POLICIES: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("T-6h", 6 * 60 * 60, ("h2h",)),
    ("T-90m", 90 * 60, ("h2h",)),
    ("T-25m", 25 * 60, ("h2h", "spreads", "totals")),
)


@dataclass(frozen=True)
class DailyOddsPlanResult:
    generated_at: str
    provider_catalog: tuple[DailyCompetition, ...]
    requests: tuple[dict[str, Any], ...]
    skipped: dict[str, dict[str, int]]
    excluded_rescheduled_events: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": self.generated_at,
            "timezone": DAILY_TIMEZONE,
            "provider_catalog": [item.to_dict() for item in self.provider_catalog],
            "requests": [dict(item) for item in self.requests],
            "request_count": len(self.requests),
            "estimated_credits": sum(int(item.get("estimated_credits") or 0) for item in self.requests),
            "skipped": {key: dict(value) for key, value in self.skipped.items()},
            "excluded_rescheduled_events": list(self.excluded_rescheduled_events),
        }


@dataclass(frozen=True)
class DailyOddsRefreshResult:
    generated_at: str
    provider_catalog: tuple[DailyCompetition, ...]
    requests: tuple[dict[str, Any], ...]
    request_count: int
    written: bool
    skipped: dict[str, dict[str, int]]
    excluded_rescheduled_events: tuple[str, ...]
    events: tuple[dict[str, Any], ...] = ()
    top4: tuple[dict[str, Any], ...] = ()
    successful_keys: tuple[str, ...] = ()
    failed_keys: tuple[str, ...] = ()
    estimated_credits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "generated_at": self.generated_at,
            "timezone": DAILY_TIMEZONE,
            "provider_catalog": [item.to_dict() for item in self.provider_catalog],
            "requests": [dict(item) for item in self.requests],
            "request_count": self.request_count,
            "estimated_credits": self.estimated_credits,
            "written": self.written,
            "skipped": {key: dict(value) for key, value in self.skipped.items()},
            "excluded_rescheduled_events": list(self.excluded_rescheduled_events),
            "events": [dict(item) for item in self.events],
            "top4": [dict(item) for item in self.top4],
            "successful_keys": list(self.successful_keys),
            "failed_keys": list(self.failed_keys),
        }


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _require_utc(value: Any) -> datetime:
    parsed = _parse_utc(value)
    if parsed is None:
        raise ValueError("now must be timezone-aware")
    return parsed


def _unwrap(value: Any) -> Any:
    return getattr(value, "json_body", value)


def _add_skip(skipped: dict[str, dict[str, int]], key: str, reason: str) -> None:
    skipped.setdefault(key, {})[reason] = skipped.setdefault(key, {}).get(reason, 0) + 1


def _state_contains(state: Any, key: str) -> bool:
    if state is None:
        return False
    if isinstance(state, Mapping):
        return bool(state.get(key))
    return key in state


def _state_add(state: Any, key: str) -> None:
    if state is None:
        return
    if hasattr(state, "commit"):
        state.commit(key)
    elif hasattr(state, "add"):
        state.add(key)
    elif hasattr(state, "__setitem__"):
        state[key] = True


def _safe_event_identity(event: dict[str, Any]) -> tuple[str, datetime, str, str] | None:
    event_id = str(event.get("id") or event.get("event_id") or "").strip()
    kickoff = _parse_utc(event.get("commence_time") or event.get("kickoff_at_utc"))
    if not event_id or kickoff is None:
        return None
    return (
        event_id,
        kickoff,
        str(event.get("home_team") or "").strip(),
        str(event.get("away_team") or "").strip(),
    )


def _future_events(
    events: Iterable[dict[str, Any]],
    now: datetime,
) -> tuple[list[tuple[str, datetime, str, str]], tuple[str, ...], int]:
    by_id: dict[str, set[datetime]] = {}
    identities: dict[tuple[str, datetime], tuple[str, str]] = {}
    invalid = 0
    local_date = now.astimezone(BEIJING_ZONE).date()
    for raw in events:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        identity = _safe_event_identity(raw)
        if identity is None:
            invalid += 1
            continue
        event_id, kickoff, home, away = identity
        by_id.setdefault(event_id, set()).add(kickoff)
        identities[(event_id, kickoff)] = (home, away)
    rescheduled = tuple(sorted(event_id for event_id, values in by_id.items() if len(values) > 1))
    valid: list[tuple[str, datetime, str, str]] = []
    for event_id, kickoffs in by_id.items():
        if event_id in rescheduled:
            continue
        kickoff = next(iter(kickoffs))
        if kickoff <= now or kickoff.astimezone(BEIJING_ZONE).date() != local_date:
            continue
        home, away = identities[(event_id, kickoff)]
        if not home or not away:
            invalid += 1
            continue
        valid.append((event_id, kickoff, home, away))
    valid.sort(key=lambda item: (item[1], item[0]))
    return valid, rescheduled, invalid


def _current_anchor(kickoff: datetime, now: datetime) -> tuple[str, tuple[str, ...]] | None:
    elapsed = (kickoff - now).total_seconds()
    for index, (name, offset, markets) in enumerate(ANCHOR_POLICIES):
        next_offset = ANCHOR_POLICIES[index + 1][1] if index + 1 < len(ANCHOR_POLICIES) else 0
        if next_offset < elapsed <= offset:
            return name, markets
    return None


def _build_requests(
    *,
    now_dt: datetime,
    sports: Iterable[dict[str, Any]],
    events_by_sport: Mapping[str, Iterable[dict[str, Any]]],
    quota_remaining_by_key: Mapping[str, int | None] | None,
    state: Any = None,
    catalog: Iterable[DailyCompetition] | None,
    daily_budget_credits: int | None = None,
) -> DailyOddsPlanResult:
    base_catalog = tuple(catalog or daily_competition_catalog())
    provider_catalog = resolve_provider_catalog(base_catalog, list(sports))
    by_key = {
        item.sport_key: item
        for item in provider_catalog
        if item.status == "enabled" and item.sport_key
    }
    quota = quota_remaining_by_key or {}
    skipped: dict[str, dict[str, int]] = {}
    excluded_rescheduled: set[str] = set()
    requests: list[dict[str, Any]] = []
    budget_used = 0

    for sport_key, competition in by_key.items():
        future, rescheduled, invalid = _future_events(events_by_sport.get(sport_key, []), now_dt)
        excluded_rescheduled.update(rescheduled)
        if invalid:
            _add_skip(skipped, sport_key, "invalid_events")
        if not future:
            _add_skip(skipped, sport_key, "no_future_events")
            continue
        due_by_anchor: dict[str, list[tuple[str, datetime, str, str]]] = {}
        for event_id, kickoff, home, away in future:
            current = _current_anchor(kickoff, now_dt)
            if current is None:
                continue
            anchor, _markets = current
            due_by_anchor.setdefault(anchor, []).append((event_id, kickoff, home, away))
        if not due_by_anchor:
            _add_skip(skipped, sport_key, "not_due")
            continue
        selected_anchor, _event_items = min(
            due_by_anchor.items(),
            key=lambda item: next(offset for name, offset, _markets in ANCHOR_POLICIES if name == item[0]),
        )
        selected_markets = tuple(
            market
            for market in ("h2h", "spreads", "totals")
            if any(
                market in next(markets for name, _offset, markets in ANCHOR_POLICIES if name == anchor)
                for anchor in due_by_anchor
            )
        )
        event_ids = tuple(
            sorted({event_id for items in due_by_anchor.values() for event_id, _kickoff, _home, _away in items})
        )
        state_key = f"{now_dt.astimezone(BEIJING_ZONE):%Y-%m-%d}|{sport_key}|{selected_anchor}"
        if _state_contains(state, state_key):
            _add_skip(skipped, sport_key, "duplicate_anchor")
            continue
        remaining = quota.get(sport_key)
        cost = len(selected_markets)
        if remaining is None:
            _add_skip(skipped, sport_key, "quota_unknown")
            continue
        try:
            remaining_int = int(remaining)
        except (TypeError, ValueError):
            remaining_int = -1
        if remaining_int < cost:
            _add_skip(skipped, sport_key, "quota_exhausted" if remaining_int <= 0 else "quota_insufficient")
            continue
        if daily_budget_credits is not None and budget_used + cost > int(daily_budget_credits):
            _add_skip(skipped, sport_key, "daily_budget_exhausted")
            continue
        fixtures = [
            {
                "event_id": event_id,
                "sport_key": sport_key,
                "competition_id": competition.competition_id,
                "commence_time": kickoff.isoformat(),
                "home_team": home,
                "away_team": away,
            }
            for items in due_by_anchor.values()
            for event_id, kickoff, home, away in items
        ]
        requests.append(
            {
                "sport_key": sport_key,
                "competition_id": competition.competition_id,
                "anchor": selected_anchor,
                "markets": list(selected_markets),
                "estimated_credits": cost,
                "event_ids": list(event_ids),
                "event_count": len(event_ids),
                "fixtures": fixtures,
                "state_key": state_key,
            }
        )
        budget_used += cost

    return DailyOddsPlanResult(
        generated_at=now_dt.isoformat(),
        provider_catalog=provider_catalog,
        requests=tuple(requests),
        skipped=skipped,
        excluded_rescheduled_events=tuple(sorted(excluded_rescheduled)),
    )


def plan_daily_odds_refresh(
    *,
    now: str | datetime,
    sports: Iterable[dict[str, Any]],
    events_by_sport: Mapping[str, Iterable[dict[str, Any]]],
    quota_remaining_by_key: Mapping[str, int | None] | None = None,
    state: Any = None,
    catalog: Iterable[DailyCompetition] | None = None,
    daily_budget_credits: int | None = None,
) -> DailyOddsPlanResult:
    """Build a sidecar plan from injected sports/events without provider or file side effects."""
    now_dt = _require_utc(now)
    return _build_requests(
        now_dt=now_dt,
        sports=sports,
        events_by_sport=events_by_sport,
        quota_remaining_by_key=quota_remaining_by_key,
        state=state,
        catalog=catalog,
        daily_budget_credits=daily_budget_credits,
    )


def _market_presence(raw: Mapping[str, Any], market: str) -> bool:
    if market in raw and raw.get(market):
        return True
    for bookmaker in raw.get("bookmakers") or []:
        for item in bookmaker.get("markets") or []:
            if str(item.get("key") or "").strip() == market and item.get("outcomes"):
                return True
    markets = raw.get("markets")
    return isinstance(markets, Mapping) and bool(markets.get(market))


def _raw_update_times(raw: Mapping[str, Any]) -> list[datetime]:
    values: list[datetime] = []
    for key in ("last_update", "last_update_at", "odds_updated_at"):
        parsed = _parse_utc(raw.get(key))
        if parsed is not None:
            values.append(parsed)
    for bookmaker in raw.get("bookmakers") or []:
        for key in ("last_update", "last_update_at"):
            parsed = _parse_utc(bookmaker.get(key))
            if parsed is not None:
                values.append(parsed)
        for market in bookmaker.get("markets") or []:
            parsed = _parse_utc(market.get("last_update"))
            if parsed is not None:
                values.append(parsed)
    return values


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) and 0.0 <= result <= 1.0 else None


def _direct_market_probs(raw: Mapping[str, Any]) -> dict[str, float] | None:
    candidates = [raw.get("h2h"), (raw.get("markets") or {}).get("h2h") if isinstance(raw.get("markets"), Mapping) else None]
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        probs = candidate.get("market_implied_probability") or candidate.get("probabilities") or candidate
        if not isinstance(probs, Mapping):
            continue
        result = {str(key): _number(value) for key, value in probs.items()}
        result = {key: value for key, value in result.items() if value is not None}
        if {"home", "draw", "away"}.issubset(result):
            return {key: float(result[key]) for key in ("home", "draw", "away")}
    return None


def _model_probs(raw: Mapping[str, Any]) -> dict[str, float]:
    candidates = [raw.get("model_probability"), raw.get("model_probabilities")]
    model = raw.get("model")
    if isinstance(model, Mapping):
        candidates.extend([model.get("probability"), model.get("probabilities"), model.get("combined_1x2")])
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            result = {str(key): _number(value) for key, value in candidate.items()}
            result = {key: value for key, value in result.items() if value is not None}
            normalized = {key: result[key] for key in ("home", "draw", "away") if key in result}
            if normalized:
                return normalized
    scalar = raw.get("model_probability")
    scalar_number = _number(scalar)
    return {"home": scalar_number} if scalar_number is not None else {}


def _normalize_provider_event(
    raw: Mapping[str, Any],
    request: Mapping[str, Any],
    now_dt: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    event_id = str(raw.get("id") or raw.get("event_id") or "").strip()
    expected = {str(value) for value in request.get("event_ids") or []}
    if not event_id or event_id not in expected:
        return None, "provider_event_identity_mismatch"
    expected_fixture = next((item for item in request.get("fixtures") or [] if str(item.get("event_id")) == event_id), None)
    identity = _safe_event_identity({
        "id": event_id,
        "commence_time": raw.get("commence_time") or raw.get("kickoff_at_utc"),
        "home_team": raw.get("home_team"),
        "away_team": raw.get("away_team"),
    })
    if identity is None or expected_fixture is None:
        return None, "provider_event_identity_mismatch"
    _event_id, kickoff, home, away = identity
    if (
        kickoff.isoformat() != str(expected_fixture.get("commence_time"))
        or home != str(expected_fixture.get("home_team") or "").strip()
        or away != str(expected_fixture.get("away_team") or "").strip()
    ):
        return None, "provider_event_identity_mismatch"

    parsed_h2h: dict[str, float] | None = _direct_market_probs(raw)
    parsed_event = None
    if parsed_h2h is None:
        try:
            parsed = parse_league_odds_events([dict(raw)], str(request.get("competition_id") or ""))
            parsed_event = parsed.odds_events[0] if parsed.odds_events else None
        except (KeyError, TypeError, ValueError):
            parsed_event = None
        if parsed_event is not None:
            aggregate = aggregate_market(
                parsed_event.quotes,
                MarketType.X12,
                None,
                ["home", "draw", "away"],
            )
            if set(aggregate.get("market_probs") or {}) == {"home", "draw", "away"}:
                parsed_h2h = {
                    key: float(aggregate["market_probs"][key])
                    for key in ("home", "draw", "away")
                }
    if parsed_h2h is None:
        return None, "incomplete_h2h"
    for market in request.get("markets") or []:
        if not _market_presence(raw, str(market)):
            return None, f"incomplete_{market}"

    update_times = _raw_update_times(raw)
    last_update = max(update_times).isoformat() if update_times else now_dt.isoformat()
    if update_times and (now_dt - max(update_times)).total_seconds() > MAX_ODDS_AGE_SECONDS:
        return None, "odds_expired"
    model = _model_probs(raw)
    explicit_selection = str(raw.get("selection") or "").strip().lower()
    if explicit_selection in {"home", "draw", "away"}:
        selection = explicit_selection
    elif model:
        selection = max(model, key=model.get)
    else:
        selection = max(parsed_h2h, key=parsed_h2h.get)
    model_probability = model.get(selection, parsed_h2h.get(selection))
    market_probability = parsed_h2h.get(selection)
    if model_probability is None or market_probability is None:
        return None, "missing_probability"
    edge = float(model_probability) - float(market_probability)
    valid_until = _parse_utc(raw.get("valid_until")) or kickoff
    row = {
        "event_id": event_id,
        "match_id": event_id,
        "source_event_id": event_id,
        "competition_id": request.get("competition_id"),
        "competition_label": request.get("competition_id"),
        "sport_key": request.get("sport_key"),
        "kickoff_at_utc": kickoff.isoformat(),
        "home_team": home,
        "away_team": away,
        "market": "1X2",
        "selection": selection,
        "model_probability": round(float(model_probability), 6),
        "market_implied_probability": round(float(market_probability), 6),
        "edge": round(edge, 6),
        "last_update": last_update,
        "selection_reason": str(raw.get("selection_reason") or "market_consensus_fallback"),
        "markets": {
            "h2h": {
                "market_implied_probability": {
                    key: round(float(value), 6) for key, value in parsed_h2h.items()
                }
            }
        },
        "match_decision": {
            "schema_version": 2,
            "policy_version": "daily_sidecar_v1",
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": selection,
            "p_hit_safe": round(float(model_probability), 6),
            "valid_until": valid_until.isoformat(),
            "odds_latest_at": last_update,
        },
    }
    return row, None


def _normalize_response(
    response: Any,
    request: Mapping[str, Any],
    now_dt: datetime,
) -> tuple[list[dict[str, Any]], str | None]:
    raw = _unwrap(response)
    if isinstance(raw, Mapping):
        raw_events = raw.get("events")
        if raw_events is None and raw.get("id"):
            raw_events = [raw]
    else:
        raw_events = raw
    if raw_events is None:
        return [], None
    if not isinstance(raw_events, list):
        return [], "invalid_provider_response"
    if not raw_events and isinstance(raw, Mapping):
        return [], "incomplete_provider_events"
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    first_error: str | None = None
    for item in raw_events:
        if not isinstance(item, Mapping):
            first_error = first_error or "invalid_provider_event"
            continue
        event_id = str(item.get("id") or item.get("event_id") or "").strip()
        if event_id in seen:
            first_error = first_error or "duplicate_provider_event"
            continue
        seen.add(event_id)
        row, error = _normalize_provider_event(item, request, now_dt)
        if error is not None:
            first_error = first_error or error
            continue
        assert row is not None
        rows.append(row)
    expected = {str(value) for value in request.get("event_ids") or []}
    if expected and seen and expected != seen:
        first_error = first_error or "incomplete_provider_events"
    return rows, first_error


def _build_daily_payload(
    *,
    now_dt: datetime,
    plan: DailyOddsPlanResult,
    requests: list[dict[str, Any]],
    events: list[dict[str, Any]],
    skipped: dict[str, dict[str, int]],
    excluded_rescheduled: set[str],
    quota_remaining_by_key: Mapping[str, int | None] | None,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any]]:
    enabled_ids = tuple(
        item.competition_id
        for item in plan.provider_catalog
        if item.status == "enabled" and item.competition_id
    )
    selection = select_daily_top4(events, now=now_dt, enabled_competition_ids=enabled_ids)
    combinations = build_combination_research(selection.selected)
    cycle = compute_daily_selection_window(now_dt)
    quota = {
        str(request["sport_key"]): {
            "remaining": (quota_remaining_by_key or {}).get(str(request["sport_key"])),
            "estimated_credits": int(request.get("estimated_credits") or 0),
        }
        for request in requests
    }
    payload = {
        "schema_version": 2,
        "namespace": "daily_odds",
        "generated_at": now_dt.isoformat(),
        "timezone": DAILY_TIMEZONE,
        "cycle": {
            "start_at": cycle.start_local.isoformat(),
            "end_at": cycle.end_local.isoformat(),
            "start_at_utc": cycle.start_at_utc.isoformat(),
            "end_at_utc": cycle.end_at_utc.isoformat(),
        },
        "provider_catalog": [item.to_dict() for item in plan.provider_catalog],
        "requests": requests,
        "events": events,
        "top4": [dict(item) for item in selection.selected],
        "parlay_2": combinations.to_dict()["parlay_2"],
        "parlay_3": combinations.to_dict()["parlay_3"],
        "candidate_count": selection.candidate_count,
        "selected_count": selection.selected_count,
        "coverage": [item.to_dict() for item in plan.provider_catalog],
        "degradation_reasons": list(selection.degradation_reasons) + list(combinations.degradation_reasons),
        "combination_rejection_reasons": list(combinations.rejection_reasons),
        "skipped": skipped,
        "excluded_rescheduled_events": sorted(excluded_rescheduled),
        "quota": quota,
    }
    result_payload = {
        "top4": tuple(dict(item) for item in selection.selected),
        "combinations": combinations.to_dict(),
    }
    return payload, tuple(dict(item) for item in selection.selected), result_payload


def refresh_daily_odds(
    *,
    now: str | datetime,
    sports_fetcher: Callable[[], Any],
    events_fetcher: Callable[[str], Any],
    odds_fetcher: Callable[[str, tuple[str, ...]], Any],
    snapshot_writer: Callable[[dict[str, Any]], Any] | None = None,
    quota_remaining_by_key: Mapping[str, int | None] | None = None,
    state: Any = None,
    catalog: Iterable[DailyCompetition] | None = None,
    daily_budget_credits: int | None = None,
) -> DailyOddsRefreshResult:
    """Run one explicit, injected daily odds refresh wave without auto scheduling."""
    now_dt = _require_utc(now)
    base_catalog = tuple(catalog or daily_competition_catalog())
    sports = _unwrap(sports_fetcher())
    if not isinstance(sports, list):
        sports = []
    events_by_sport: dict[str, Any] = {}
    resolved = resolve_provider_catalog(base_catalog, sports)
    keys = [item.sport_key for item in resolved if item.status == "enabled" and item.sport_key]
    for sport_key in keys:
        try:
            events_by_sport[str(sport_key)] = _unwrap(events_fetcher(str(sport_key)))
        except Exception:
            events_by_sport[str(sport_key)] = []
    plan = _build_requests(
        now_dt=now_dt,
        sports=sports,
        events_by_sport=events_by_sport,
        quota_remaining_by_key=quota_remaining_by_key,
        state=state,
        catalog=base_catalog,
        daily_budget_credits=daily_budget_credits,
    )
    skipped = {key: dict(value) for key, value in plan.skipped.items()}
    excluded_rescheduled = set(plan.excluded_rescheduled_events)
    requests: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    successful: list[str] = []
    failed: list[str] = []
    for request in plan.requests:
        sport_key = str(request["sport_key"])
        state_key = str(
            request.get("state_key")
            or f"{now_dt.astimezone(BEIJING_ZONE):%Y-%m-%d}|{sport_key}|{request.get('anchor')}"
        )
        try:
            response = odds_fetcher(sport_key, tuple(request["markets"]))
            rows, error = _normalize_response(response, request, now_dt)
        except Exception:
            rows, error = [], "provider_fetch_error"
        if error is not None:
            _add_skip(skipped, sport_key, error)
            failed.append(state_key)
            rows = []
        request_copy = {
            key: value
            for key, value in request.items()
            if key not in {"state_key"}
        }
        request_copy["source_update"] = max(
            (row.get("last_update") for row in rows if row.get("last_update")),
            default=now_dt.isoformat(),
        )
        request_copy["internal"] = {
            "odds_movement": {
                "activation": "shadow_only",
                "role": "risk_metadata_only",
                "affects_selection": False,
            }
        }
        requests.append(request_copy)
        events.extend(rows)
        if error is None:
            successful.append(state_key)

    payload, top4, _result_payload = _build_daily_payload(
        now_dt=now_dt,
        plan=plan,
        requests=requests,
        events=events,
        skipped=skipped,
        excluded_rescheduled=excluded_rescheduled,
        quota_remaining_by_key=quota_remaining_by_key,
    )
    written = False
    if snapshot_writer is not None:
        snapshot_writer(payload)
        written = True
    for state_key in successful:
        _state_add(state, state_key)
    return DailyOddsRefreshResult(
        generated_at=now_dt.isoformat(),
        provider_catalog=plan.provider_catalog,
        requests=tuple(requests),
        request_count=len(requests),
        written=written,
        skipped=skipped,
        excluded_rescheduled_events=tuple(sorted(excluded_rescheduled)),
        events=tuple(events),
        top4=top4,
        successful_keys=tuple(successful),
        failed_keys=tuple(failed),
        estimated_credits=sum(int(item.get("estimated_credits") or 0) for item in requests),
    )


def load_daily_odds_payload(path: str | Path = "data/cache/daily_odds/daily_odds_snapshot.json") -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("namespace") == "daily_odds" else None
