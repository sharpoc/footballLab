from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


UTC = timezone.utc
_LEGACY_SNAPSHOT_NAMES = {
    "analysis_snapshot.json",
    "league_analysis_snapshot.json",
}
_SAFE_CATALOG_FIELDS = ("name", "status", "reason", "competition_id", "sport_key")
_SAFE_FIXTURE_FIELDS = (
    "event_id",
    "sport_key",
    "competition_id",
    "commence_time",
    "home_team",
    "away_team",
)
_SAFE_EVENT_FIELDS = (
    "event_id",
    "match_id",
    "source_event_id",
    "competition_id",
    "competition_label",
    "sport_key",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "market",
    "selection",
    "model_probability",
    "market_implied_probability",
    "edge",
    "last_update",
    "selection_reason",
    "markets",
    "match_decision",
)


def _parse_utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("daily_odds_invalid_commence_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("daily_odds_invalid_commence_time")
    return parsed.astimezone(UTC)


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_catalog(catalog: Any) -> list[dict[str, Any]]:
    if not isinstance(catalog, list):
        raise ValueError("daily_odds_invalid_provider_catalog")
    result: list[dict[str, Any]] = []
    for item in catalog:
        if not isinstance(item, Mapping):
            raise ValueError("daily_odds_invalid_provider_catalog")
        result.append({key: item.get(key) for key in _SAFE_CATALOG_FIELDS})
    return result


def _safe_fixture(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("daily_odds_invalid_fixture")
    fixture = {key: value.get(key) for key in _SAFE_FIXTURE_FIELDS}
    if not fixture["event_id"] or not fixture["sport_key"] or not fixture["competition_id"]:
        raise ValueError("daily_odds_invalid_fixture")
    fixture["event_id"] = _safe_text(fixture["event_id"])
    fixture["sport_key"] = _safe_text(fixture["sport_key"])
    fixture["competition_id"] = _safe_text(fixture["competition_id"])
    fixture["home_team"] = _safe_text(fixture["home_team"])
    fixture["away_team"] = _safe_text(fixture["away_team"])
    fixture["commence_time"] = _parse_utc(fixture["commence_time"]).isoformat()
    return fixture


def _safe_request(
    value: Any,
    catalog_by_key: Mapping[str, Mapping[str, Any]],
    seen_events: dict[tuple[str, str], tuple[datetime, str, str]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("daily_odds_invalid_request")
    sport_key = _safe_text(value.get("sport_key"))
    competition_id = _safe_text(value.get("competition_id"))
    catalog = catalog_by_key.get(sport_key)
    if catalog is None or catalog.get("status") != "enabled":
        raise ValueError("daily_odds_identity_mismatch")
    if competition_id != _safe_text(catalog.get("competition_id")):
        raise ValueError("daily_odds_identity_mismatch")

    fixtures_raw = value.get("fixtures")
    if not isinstance(fixtures_raw, list) or not fixtures_raw:
        raise ValueError("daily_odds_invalid_request")
    fixtures = [_safe_fixture(item) for item in fixtures_raw]
    event_ids = tuple(sorted({_safe_text(item.get("event_id")) for item in fixtures}))
    declared_event_ids = tuple(sorted({_safe_text(item) for item in (value.get("event_ids") or [])}))
    if declared_event_ids != event_ids or int(value.get("event_count") or 0) != len(event_ids):
        raise ValueError("daily_odds_identity_mismatch")

    for fixture in fixtures:
        if (
            fixture["sport_key"] != sport_key
            or fixture["competition_id"] != competition_id
            or not fixture["home_team"]
            or not fixture["away_team"]
        ):
            raise ValueError("daily_odds_identity_mismatch")
        identity_key = (sport_key, fixture["event_id"])
        identity = (
            _parse_utc(fixture["commence_time"]),
            fixture["home_team"],
            fixture["away_team"],
        )
        previous = seen_events.get(identity_key)
        if previous is not None and previous != identity:
            if previous[0] != identity[0]:
                raise ValueError("daily_odds_rescheduled_event")
            raise ValueError("daily_odds_identity_mismatch")
        seen_events[identity_key] = identity

    markets = tuple(_safe_text(item) for item in (value.get("markets") or []))
    if not markets or any(item not in {"h2h", "spreads", "totals"} for item in markets):
        raise ValueError("daily_odds_invalid_markets")
    internal_raw = value.get("internal") if isinstance(value.get("internal"), Mapping) else {}
    movement_raw = internal_raw.get("odds_movement")
    movement = {}
    if isinstance(movement_raw, Mapping):
        for key in ("activation", "role", "affects_selection"):
            if key in movement_raw:
                movement[key] = movement_raw[key]
        movement["visibility"] = "internal"

    return {
        "sport_key": sport_key,
        "competition_id": competition_id,
        "anchor": _safe_text(value.get("anchor")),
        "markets": list(markets),
        "event_ids": list(event_ids),
        "event_count": len(event_ids),
        "fixtures": fixtures,
        "internal": {"odds_movement": movement} if movement else {},
    }


def _safe_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("daily_odds_invalid_event")
    event = {key: value.get(key) for key in _SAFE_EVENT_FIELDS if key in value}
    event_id = _safe_text(event.get("event_id") or event.get("match_id"))
    if not event_id:
        raise ValueError("daily_odds_invalid_event")
    event["event_id"] = event_id
    event["match_id"] = event.get("match_id") or event_id
    event["kickoff_at_utc"] = _parse_utc(
        event.get("kickoff_at_utc") or value.get("commence_time")
    ).isoformat()
    event["home_team"] = _safe_text(event.get("home_team"))
    event["away_team"] = _safe_text(event.get("away_team"))
    if not event["home_team"] or not event["away_team"]:
        raise ValueError("daily_odds_invalid_event")
    return event


def _safe_public_selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("daily_odds_invalid_selection")
    return {
        key: value.get(key)
        for key in (
            "match_id",
            "competition_id",
            "competition_label",
            "kickoff_at_utc",
            "home_team",
            "away_team",
            "market",
            "selection",
            "model_probability",
            "market_implied_probability",
            "edge",
            "last_update",
            "selection_reason",
            "match_decision",
        )
        if key in value
    }


def _safe_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    catalog = _safe_catalog(payload.get("provider_catalog"))
    catalog_by_key = {
        _safe_text(item.get("sport_key")): item
        for item in catalog
        if _safe_text(item.get("sport_key"))
    }
    seen_events: dict[tuple[str, str], tuple[datetime, str, str]] = {}
    requests_raw = payload.get("requests")
    if not isinstance(requests_raw, list):
        raise ValueError("daily_odds_invalid_requests")
    requests = [_safe_request(item, catalog_by_key, seen_events) for item in requests_raw]
    excluded = [_safe_text(item) for item in (payload.get("excluded_rescheduled_events") or [])]
    requested_event_ids = {
        event_id for request in requests for event_id in request["event_ids"]
    }
    if requested_event_ids.intersection(excluded):
        raise ValueError("daily_odds_rescheduled_event")

    safe_events: list[dict[str, Any]] = []
    raw_events = payload.get("events")
    if raw_events is not None:
        if not isinstance(raw_events, list):
            raise ValueError("daily_odds_invalid_events")
        for item in raw_events:
            safe_events.append(_safe_event(item))

    by_event_id = {str(item["event_id"]): item for item in safe_events}
    if len(by_event_id) != len(safe_events):
        raise ValueError("daily_odds_duplicate_event")
    for event_id, identity in seen_events.items():
        event = by_event_id.get(event_id[1])
        if event is None:
            continue
        event_identity = (
            _parse_utc(event["kickoff_at_utc"]),
            event["home_team"],
            event["away_team"],
        )
        if identity != event_identity:
            raise ValueError("daily_odds_rescheduled_event")

    previous_events: dict[str, tuple[datetime, str, str]] = {}
    if hasattr(payload, "get"):
        pass

    skipped: dict[str, dict[str, int]] = {}
    raw_skipped = payload.get("skipped")
    if isinstance(raw_skipped, Mapping):
        for sport_key, reasons in raw_skipped.items():
            if not isinstance(reasons, Mapping):
                continue
            skipped[_safe_text(sport_key)] = {
                _safe_text(reason): int(count)
                for reason, count in reasons.items()
            }

    generated_at = _parse_utc(payload.get("generated_at")).isoformat()
    safe = {
        "schema_version": int(payload.get("schema_version") or 1),
        "namespace": "daily_odds",
        "generated_at": generated_at,
        "timezone": _safe_text(payload.get("timezone") or "Asia/Shanghai"),
        "cycle": payload.get("cycle") if isinstance(payload.get("cycle"), Mapping) else {},
        "provider_catalog": catalog,
        "requests": requests,
        "events": safe_events,
        "top4": [
            _safe_public_selection(item)
            for item in (payload.get("top4") or [])
        ],
        "parlay_2": payload.get("parlay_2") if isinstance(payload.get("parlay_2"), list) else [],
        "parlay_3": payload.get("parlay_3") if isinstance(payload.get("parlay_3"), list) else [],
        "candidate_count": int(payload.get("candidate_count") or len(safe_events)),
        "selected_count": int(payload.get("selected_count") or len(payload.get("top4") or [])),
        "coverage": payload.get("coverage") if isinstance(payload.get("coverage"), list) else catalog,
        "degradation_reasons": [
            _safe_text(item) for item in (payload.get("degradation_reasons") or [])
        ],
        "combination_rejection_reasons": [
            _safe_text(item) for item in (payload.get("combination_rejection_reasons") or [])
        ],
        "skipped": skipped,
        "excluded_rescheduled_events": sorted(set(excluded)),
        "quota": payload.get("quota") if isinstance(payload.get("quota"), Mapping) else {},
    }
    return safe


def _write_json_atomic(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class DailyOddsSnapshotWriter:
    """Write only safe daily-odds metadata into its isolated namespace."""

    def __init__(self, path: str | Path, *, dry_run: bool = False) -> None:
        self.path = Path(path)
        if self.path.name in _LEGACY_SNAPSHOT_NAMES or self.path.parent.name != "daily_odds":
            raise ValueError("daily_odds_path_isolation")
        self.dry_run = bool(dry_run)

    def __call__(self, payload: dict[str, Any]) -> Path | None:
        safe = _safe_projection(payload)
        if self.dry_run:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("daily_odds_existing_snapshot_invalid") from exc
            previous_events = {
                str(item.get("event_id")): (
                    _parse_utc(item.get("kickoff_at_utc")),
                    _safe_text(item.get("home_team")),
                    _safe_text(item.get("away_team")),
                )
                for item in (previous.get("events") or [])
                if isinstance(item, Mapping) and item.get("event_id")
            }
            for item in safe.get("events") or []:
                event_id = str(item.get("event_id"))
                identity = (
                    _parse_utc(item.get("kickoff_at_utc")),
                    _safe_text(item.get("home_team")),
                    _safe_text(item.get("away_team")),
                )
                old = previous_events.get(event_id)
                if old is not None and old != identity:
                    raise ValueError("daily_odds_rescheduled_event")
        content = json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _write_json_atomic(self.path, content)
        return self.path


def default_daily_odds_snapshot_path(cache_dir: str | Path = "data/cache") -> Path:
    return Path(cache_dir) / "daily_odds" / "daily_odds_snapshot.json"
