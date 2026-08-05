from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any, Iterable
from zoneinfo import ZoneInfo


BEIJING_ZONE = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
CYCLE_HOUR = 18


@dataclass(frozen=True)
class DailySelectionWindow:
    timezone_name: str
    start_local: datetime
    end_local: datetime
    start_at_utc: datetime
    end_at_utc: datetime

    @property
    def label(self) -> str:
        return (
            f"{self.start_local:%Y年%-m月%-d日 %H:%M} 至 "
            f"{self.end_local:%-m月%-d日 %H:%M}（北京时间）"
        )


@dataclass(frozen=True)
class LockedMatch:
    match: dict[str, Any]
    is_locked: bool
    reason: str


@dataclass(frozen=True)
class DailySelectionResult:
    window: DailySelectionWindow
    selected: tuple[dict[str, Any], ...]
    candidate_count: int
    selected_count: int
    excluded_count: int
    degradation_reasons: tuple[str, ...]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": {
                "timezone": self.window.timezone_name,
                "start_at": self.window.start_local.isoformat(),
                "end_at": self.window.end_local.isoformat(),
                "start_at_utc": self.window.start_at_utc.isoformat(),
                "end_at_utc": self.window.end_at_utc.isoformat(),
                "label": self.window.label,
            },
            "generated_at": self.generated_at,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "singles": [deepcopy(row) for row in self.selected],
            "degradation_reasons": list(self.degradation_reasons),
        }


def _parse_aware(value: str | datetime, *, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid_{field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def compute_daily_selection_window(now: str | datetime) -> DailySelectionWindow:
    now_utc = _parse_aware(now, field="now")
    now_local = now_utc.astimezone(BEIJING_ZONE)
    cycle_date = now_local.date()
    if now_local.time() < time(CYCLE_HOUR, 0):
        cycle_date -= timedelta(days=1)
    start_local = datetime.combine(
        cycle_date,
        time(CYCLE_HOUR, 0),
        tzinfo=BEIJING_ZONE,
    )
    end_local = start_local + timedelta(days=1)
    return DailySelectionWindow(
        timezone_name="Asia/Shanghai",
        start_local=start_local,
        end_local=end_local,
        start_at_utc=start_local.astimezone(UTC),
        end_at_utc=end_local.astimezone(UTC),
    )


def kickoff_in_window(kickoff: str | datetime, window: DailySelectionWindow) -> bool:
    kickoff_utc = _parse_aware(kickoff, field="kickoff")
    return window.start_at_utc <= kickoff_utc < window.end_at_utc


def _now_utc(now: str | datetime) -> datetime:
    return _parse_aware(now, field="now")


def _match_id(row: dict[str, Any]) -> str:
    explicit = str(row.get("match_id") or row.get("source_event_id") or "").strip()
    if explicit:
        return explicit
    return "|".join(
        (
            str(row.get("competition_id") or ""),
            str(row.get("kickoff_at_utc") or ""),
            str(row.get("home_team") or ""),
            str(row.get("away_team") or ""),
        )
    )


def _decision(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("match_decision")
    return value if isinstance(value, dict) else {}


def _probability(decision: dict[str, Any]) -> float | None:
    for key in ("p_hit_safe", "prediction_probability"):
        value = decision.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(number) and 0.0 <= number <= 1.0:
            return number
    return None


def _market_probability(row: dict[str, Any], decision: dict[str, Any]) -> float | None:
    for source in (row, decision):
        for key in ("market_implied_probability", "p_market_safe", "market_probability"):
            value = source.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if isfinite(number) and 0.0 <= number <= 1.0:
                return number
    return None


def _valid_until(decision: dict[str, Any]) -> datetime | None:
    value = decision.get("valid_until")
    if not value:
        return None
    try:
        return _parse_aware(str(value), field="valid_until")
    except ValueError:
        return None


def _exclude_reason(row: dict[str, Any], now_utc: datetime, window: DailySelectionWindow, enabled: set[str]) -> str | None:
    competition_id = str(row.get("competition_id") or "").strip()
    if competition_id not in enabled:
        return "competition_disabled"
    fixture_status = str(row.get("fixture_status") or "SCHEDULED").upper()
    if fixture_status == "POSTPONED":
        return "fixture_postponed"
    if fixture_status in {"FINISHED", "COMPLETED", "CANCELLED"} or row.get("is_finished"):
        return "match_finished"
    kickoff_raw = row.get("kickoff_at_utc")
    try:
        kickoff = _parse_aware(str(kickoff_raw), field="kickoff")
    except ValueError:
        return "invalid_kickoff"
    if not (window.start_at_utc <= kickoff < window.end_at_utc):
        return "outside_cycle"
    if kickoff < now_utc:
        return "match_started"
    decision = _decision(row)
    if str(decision.get("label") or "") != "MATCH_PICK":
        return "no_clean_market"
    if not decision.get("market") or not decision.get("selection"):
        return "not_settleable"
    if _probability(decision) is None:
        return "missing_prediction_probability"
    valid_until = _valid_until(decision)
    if valid_until is None:
        return "invalid_valid_until"
    if valid_until <= now_utc:
        return "odds_expired"
    if str(row.get("settlement_status") or "").lower() in {"unsettleable", "invalid"}:
        return "not_settleable"
    return None


def _public_selection_row(row: dict[str, Any]) -> dict[str, Any]:
    decision = _decision(row)
    selected = {
        "match_id": _match_id(row),
        "competition_id": row.get("competition_id"),
        "competition_label": row.get("competition_label") or row.get("competition_name"),
        "kickoff_at_utc": row.get("kickoff_at_utc"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "market": decision.get("market"),
        "selection": decision.get("selection"),
        "line": decision.get("line"),
        "prediction_probability": _probability(decision),
        "market_implied_probability": _market_probability(row, decision),
        "reference_odds": decision.get("odds"),
        "valid_until": decision.get("valid_until"),
        "match_decision": {
            key: deepcopy(decision[key])
            for key in (
                "schema_version",
                "policy_version",
                "label",
                "market",
                "selection",
                "line",
                "odds",
                "p_hit_safe",
                "p_no_loss_safe",
                "computed_at",
                "odds_latest_at",
                "valid_until",
            )
            if key in decision
        },
    }
    return selected


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    probability = float(row.get("prediction_probability") or 0.0)
    try:
        kickoff = _parse_aware(str(row.get("kickoff_at_utc") or ""), field="kickoff")
    except ValueError:
        kickoff = datetime.max.replace(tzinfo=UTC)
    return (
        -probability,
        kickoff,
        str(row.get("competition_id") or ""),
        str(row.get("match_id") or ""),
    )


def filter_daily_candidates(
    rows: Iterable[dict[str, Any]],
    *,
    now: str | datetime,
    enabled_competition_ids: Iterable[str],
) -> tuple[tuple[dict[str, Any], ...], int]:
    now_utc = _now_utc(now)
    window = compute_daily_selection_window(now_utc)
    enabled = {str(value) for value in enabled_competition_ids}
    candidates: list[dict[str, Any]] = []
    excluded = 0
    for original in rows:
        row = original if isinstance(original, dict) else {}
        reason = _exclude_reason(row, now_utc, window, enabled)
        if reason is not None:
            excluded += 1
            continue
        candidates.append(deepcopy(row))
    return tuple(candidates), excluded


def select_daily_top4(
    rows: Iterable[dict[str, Any]],
    *,
    now: str | datetime,
    enabled_competition_ids: Iterable[str],
) -> DailySelectionResult:
    now_utc = _now_utc(now)
    window = compute_daily_selection_window(now_utc)
    candidates, excluded = filter_daily_candidates(
        rows,
        now=now_utc,
        enabled_competition_ids=enabled_competition_ids,
    )
    selected = [_public_selection_row(row) for row in candidates]
    selected.sort(key=_sort_key)
    candidate_count = len(selected)
    selected = selected[:4]
    degradation: list[str] = []
    if candidate_count < 4:
        degradation.append("fewer_than_4_candidates")
    return DailySelectionResult(
        window=window,
        selected=tuple(selected),
        candidate_count=candidate_count,
        selected_count=len(selected),
        excluded_count=excluded,
        degradation_reasons=tuple(degradation),
        generated_at=_iso(now_utc),
    )


def lock_match_for_cycle(row: dict[str, Any], *, now: str | datetime) -> LockedMatch:
    now_utc = _now_utc(now)
    try:
        kickoff = _parse_aware(str(row.get("kickoff_at_utc") or ""), field="kickoff")
    except ValueError:
        return LockedMatch(match=deepcopy(row), is_locked=False, reason="invalid_kickoff")
    if kickoff <= now_utc:
        return LockedMatch(match=deepcopy(row), is_locked=True, reason="match_started")
    return LockedMatch(match=deepcopy(row), is_locked=False, reason="pre_match")
