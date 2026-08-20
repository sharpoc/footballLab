"""Pure validation and event evaluation for the CSL postmatch sentinel."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterator

from worldcup.notifications import send_wxpusher_notification


REPORT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_MIN_SAMPLE = 50
RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"

DEFAULT_SHADOW_REPORT = "data/local/diagnostics/csl_postmatch_shadow.json"
DEFAULT_COVERAGE_REPORT = "data/local/diagnostics/csl_closing_coverage.json"
DEFAULT_STATE = "data/local/diagnostics/csl_postmatch_sentinel_state.json"
DEFAULT_LOCK = "data/local/diagnostics/csl_postmatch_sentinel.lock"

QUALITY_FIELDS = (
    "missing_closing_count",
    "missing_decision_count",
    "identity_mismatch_count",
    "invalid_decision_count",
    "result_source_blocked_count",
    "unresolved_count",
)
MONOTONIC_FIELDS = (
    "finished_result_count",
    "closing_available_count",
    "decision_available_count",
    "decision_count",
)

_TALLY_FIELDS = ("hit", "miss", "push", "no_pick")
_COVERAGE_FIELDS = (
    "finished_result_count",
    "closing_available_count",
    "decision_available_count",
    "identity_mismatch_count",
    "invalid_decision_count",
    "legacy_decision_count",
    "missing_closing_count",
    "missing_decision_count",
    "result_source_blocked_count",
    "unresolved_count",
)


class SentinelValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _strict_count(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise SentinelValidationError(code)
    return value


def _strict_positive_count(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise SentinelValidationError(code)
    return value


def _strict_float(value: object, code: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise SentinelValidationError(code)
    return float(value)


def _strict_bool(value: object, code: str) -> bool:
    if type(value) is not bool:
        raise SentinelValidationError(code)
    return value


def _object(value: object, code: str) -> dict[str, object]:
    if type(value) is not dict:
        raise SentinelValidationError(code)
    return value


def _parse_utc(value: object) -> str:
    if not isinstance(value, str):
        raise SentinelValidationError("report_generated_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SentinelValidationError("report_generated_at_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SentinelValidationError("report_generated_at_invalid")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SentinelValidationError("report_fingerprint_invalid")
    return value


def _validate_header(report: object, invalid_code: str) -> dict[str, object]:
    value = _object(report, invalid_code)
    if value.get("schema_version") != REPORT_SCHEMA_VERSION or type(value.get("schema_version")) is not int:
        raise SentinelValidationError(invalid_code)
    if value.get("competition_id") != DEFAULT_COMPETITION_ID:
        raise SentinelValidationError(invalid_code)
    if value.get("season") != DEFAULT_SEASON:
        raise SentinelValidationError(invalid_code)
    generated_at = _parse_utc(value.get("generated_at"))
    fingerprint = _fingerprint(value.get("input_fingerprint"))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "competition_id": DEFAULT_COMPETITION_ID,
        "season": DEFAULT_SEASON,
        "generated_at": generated_at,
        "input_fingerprint": fingerprint,
    }


def _validate_tally(value: object, invalid_code: str) -> dict[str, int]:
    tally = _object(value, invalid_code)
    return {field: _strict_count(tally.get(field), invalid_code) for field in _TALLY_FIELDS}


def _validate_sample(value: object, invalid_code: str) -> dict[str, object]:
    sample = _object(value, invalid_code)
    min_sample = _strict_positive_count(sample.get("min_sample"), invalid_code)
    if min_sample != DEFAULT_MIN_SAMPLE:
        raise SentinelValidationError(invalid_code)
    normalized: dict[str, object] = {
        "actionable": _strict_count(sample.get("actionable"), invalid_code),
        "decided": _strict_count(sample.get("decided"), invalid_code),
        "decision_count": _strict_count(sample.get("decision_count"), invalid_code),
        "min_sample": min_sample,
        "sample_too_small": _strict_bool(sample.get("sample_too_small"), invalid_code),
    }
    hit_rate = sample.get("hit_rate")
    normalized["hit_rate"] = (
        None if hit_rate is None else _strict_float(hit_rate, invalid_code)
    )
    normalized["pick_rate"] = _strict_float(sample.get("pick_rate"), invalid_code)
    return normalized


def _validate_sample_equations(sample: dict[str, object], tally: dict[str, int], code: str) -> None:
    decision_count = sample["decision_count"]
    decided = sample["decided"]
    actionable = sample["actionable"]
    min_sample = sample["min_sample"]
    if not all(type(value) is int for value in (decision_count, decided, actionable, min_sample)):
        raise SentinelValidationError(code)
    if decision_count != sum(tally.values()):
        raise SentinelValidationError(code)
    if decided != tally["hit"] + tally["miss"]:
        raise SentinelValidationError(code)
    if actionable != tally["hit"] + tally["miss"] + tally["push"]:
        raise SentinelValidationError(code)
    expected_pick_rate = actionable / decision_count if decision_count else 0.0
    if sample["pick_rate"] != expected_pick_rate:
        raise SentinelValidationError(code)
    expected_hit_rate = tally["hit"] / decided if decided else None
    if sample["hit_rate"] != expected_hit_rate:
        raise SentinelValidationError(code)
    if sample["sample_too_small"] != (decision_count < min_sample):
        raise SentinelValidationError(code)


def _validate_coverage_block(value: object, invalid_code: str) -> dict[str, int]:
    block = _object(value, invalid_code)
    normalized = {
        field: _strict_count(block.get(field), invalid_code) for field in _COVERAGE_FIELDS
    }
    if normalized["closing_available_count"] < normalized["decision_available_count"]:
        raise SentinelValidationError(invalid_code)
    if normalized["finished_result_count"] < normalized["closing_available_count"]:
        raise SentinelValidationError(invalid_code)
    return normalized


def _safe_matches(value: object, invalid_code: str) -> list[dict[str, object]]:
    if type(value) is not list:
        raise SentinelValidationError(invalid_code)
    matches: list[dict[str, object]] = []
    for item in value:
        if type(item) is not dict:
            raise SentinelValidationError(invalid_code)
        match_id = item.get("match_id")
        settlement = item.get("settlement")
        if not isinstance(match_id, str) or not match_id or type(settlement) is not dict:
            raise SentinelValidationError(invalid_code)
        status = settlement.get("status")
        if not isinstance(status, str) or not status:
            raise SentinelValidationError(invalid_code)
        matches.append({"match_id": match_id, "settlement": {"status": status}})
    return matches


def _validate_shadow_report(report: object) -> dict[str, object]:
    invalid_code = "shadow_report_invalid"
    header = _validate_header(report, invalid_code)
    source = _object(report, invalid_code)
    if source.get("status") != "ok":
        raise SentinelValidationError(invalid_code)
    tally = _validate_tally(source.get("decision_tally"), invalid_code)
    sample = _validate_sample(source.get("decision_sample"), invalid_code)
    _validate_sample_equations(sample, tally, invalid_code)
    coverage = _validate_coverage_block(source.get("decision_coverage"), invalid_code)
    if coverage["decision_available_count"] != sample["decision_count"]:
        raise SentinelValidationError(invalid_code)
    return {
        **header,
        "status": "ok",
        "decision_sample": sample,
        "decision_tally": tally,
        "decision_coverage": coverage,
        "matches": _safe_matches(source.get("matches"), invalid_code),
    }


def _validate_coverage_report(report: object) -> dict[str, object]:
    invalid_code = "coverage_report_invalid"
    header = _validate_header(report, invalid_code)
    source = _object(report, invalid_code)
    summary = _object(source.get("summary"), invalid_code)
    normalized_summary = {
        "finished_result_count": _strict_count(summary.get("finished_result_count"), invalid_code),
        "missing_count": _strict_count(summary.get("missing_count"), invalid_code),
        "observed_closing_count": _strict_count(summary.get("observed_closing_count"), invalid_code),
        "observed_current_decision_count": _strict_count(summary.get("observed_current_decision_count"), invalid_code),
        "observed_missing_current_decision_count": _strict_count(summary.get("observed_missing_current_decision_count"), invalid_code),
    }
    performance = _object(source.get("performance"), invalid_code)
    observed = _object(performance.get("observed"), invalid_code)
    if observed.get("official_headline_scope") != "observed_schema_v2_match_pick_only":
        raise SentinelValidationError(invalid_code)
    tally = _validate_tally(observed.get("decision_tally"), invalid_code)
    sample = _validate_sample(observed.get("decision_sample"), invalid_code)
    _validate_sample_equations(sample, tally, invalid_code)
    matches = source.get("matches")
    if type(matches) is not list:
        raise SentinelValidationError(invalid_code)
    return {
        **header,
        "summary": normalized_summary,
        "performance": {
            "observed": {
                "decision_sample": sample,
                "decision_tally": tally,
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            }
        },
        "matches": deepcopy(matches),
    }


def _validate_cross_report(shadow: dict[str, object], coverage: dict[str, object]) -> None:
    coverage_block = shadow["decision_coverage"]
    sample = shadow["decision_sample"]
    tally = shadow["decision_tally"]
    summary = coverage["summary"]
    observed = coverage["performance"]["observed"]
    if (
        shadow["generated_at"] != coverage["generated_at"]
        or summary["finished_result_count"] != coverage_block["finished_result_count"]
        or summary["observed_closing_count"] != coverage_block["closing_available_count"]
        or summary["observed_current_decision_count"] != coverage_block["decision_available_count"]
        or summary["observed_missing_current_decision_count"] != coverage_block["missing_decision_count"]
        or summary["missing_count"] != coverage_block["missing_closing_count"]
        or observed["decision_sample"] != sample
        or observed["decision_tally"] != tally
        or observed["official_headline_scope"] != "observed_schema_v2_match_pick_only"
    ):
        raise SentinelValidationError("coverage_shadow_mismatch")


def validate_postmatch_inputs(
    shadow_report: object,
    coverage_report: object,
) -> tuple[dict[str, object], dict[str, object]]:
    shadow = _validate_shadow_report(shadow_report)
    coverage = _validate_coverage_report(coverage_report)
    _validate_cross_report(shadow, coverage)
    return shadow, coverage


def _sentinel_projection(shadow: dict[str, object], coverage: dict[str, object]) -> dict[str, object]:
    coverage_block = shadow["decision_coverage"]
    sample = shadow["decision_sample"]
    issue_match_ids = {
        field: sorted(
            match["match_id"]
            for match in shadow["matches"]
            if match["settlement"]["status"] == field.removesuffix("_count")
        )
        for field in QUALITY_FIELDS
    }
    return {
        "quality": {field: coverage_block[field] for field in QUALITY_FIELDS},
        "monotonic": {
            "finished_result_count": coverage_block["finished_result_count"],
            "closing_available_count": coverage_block["closing_available_count"],
            "decision_available_count": coverage_block["decision_available_count"],
            "decision_count": sample["decision_count"],
        },
        "min_sample": sample["min_sample"],
        "issue_match_ids": issue_match_ids,
        "input_fingerprint": _event_id({
            "shadow": shadow["input_fingerprint"],
            "coverage": coverage["input_fingerprint"],
        }),
    }


def _event_id(payload: dict[str, object]) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _initial_state(current: dict[str, object], observed_at: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "competition_id": DEFAULT_COMPETITION_ID,
        "season": DEFAULT_SEASON,
        "baseline_quality": deepcopy(current["quality"]),
        "high_water": deepcopy(current["monotonic"]),
        "active_conditions": {},
        "threshold_notified": False,
        "last_input_fingerprint": current["input_fingerprint"],
        "last_observed_at": observed_at,
    }
    decision_count = current["monotonic"]["decision_count"]
    min_sample = current["min_sample"]
    if decision_count >= min_sample:
        state["threshold_notified"] = True
        return state, [
            _event(
                kind="threshold",
                code="decision_sample_reached_minimum",
                condition="threshold:decision_count",
                baseline_count=None,
                current_count=decision_count,
                match_ids_digest=None,
                observed_at=observed_at,
            )
        ]
    return state, []


def _state_count_map(value: object, fields: tuple[str, ...]) -> dict[str, int]:
    if type(value) is not dict:
        raise SentinelValidationError("sentinel_state_invalid")
    if set(value) != set(fields):
        raise SentinelValidationError("sentinel_state_invalid")
    return {
        field: _strict_count(value.get(field), "sentinel_state_invalid")
        for field in fields
    }


def _validate_state(value: object) -> dict[str, object]:
    state = _object(value, "sentinel_state_invalid")
    if state.get("schema_version") != STATE_SCHEMA_VERSION or type(state.get("schema_version")) is not int:
        raise SentinelValidationError("sentinel_state_invalid")
    if state.get("competition_id") != DEFAULT_COMPETITION_ID or state.get("season") != DEFAULT_SEASON:
        raise SentinelValidationError("sentinel_state_invalid")
    normalized = {
        "schema_version": STATE_SCHEMA_VERSION,
        "competition_id": DEFAULT_COMPETITION_ID,
        "season": DEFAULT_SEASON,
        "baseline_quality": _state_count_map(state.get("baseline_quality"), QUALITY_FIELDS),
        "high_water": _state_count_map(state.get("high_water"), MONOTONIC_FIELDS),
        "active_conditions": _object(state.get("active_conditions"), "sentinel_state_invalid"),
        "threshold_notified": _strict_bool(
            state.get("threshold_notified"), "sentinel_state_invalid"
        ),
        "last_input_fingerprint": _fingerprint(state.get("last_input_fingerprint")),
        "last_observed_at": _parse_utc(state.get("last_observed_at")),
    }
    active: dict[str, dict[str, object]] = {}
    for condition, active_value in normalized["active_conditions"].items():
        if not isinstance(condition, str) or type(active_value) is not dict:
            raise SentinelValidationError("sentinel_state_invalid")
        if set(active_value) != {"event_id", "current_count", "match_ids_digest"}:
            raise SentinelValidationError("sentinel_state_invalid")
        event_id = active_value.get("event_id")
        count = active_value.get("current_count")
        digest = active_value.get("match_ids_digest")
        if (
            not isinstance(event_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", event_id) is None
            or type(count) is not int
            or count < 0
            or (digest is not None and (not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None))
        ):
            raise SentinelValidationError("sentinel_state_invalid")
        active[condition] = {
            "event_id": event_id,
            "current_count": count,
            "match_ids_digest": digest,
        }
    normalized["active_conditions"] = active
    return normalized


def _match_ids_digest(match_ids: list[str]) -> str:
    return _event_id({"match_ids": match_ids})


def _event(
    *,
    kind: str,
    code: str,
    condition: str,
    baseline_count: int | None,
    current_count: int,
    match_ids_digest: str | None,
    observed_at: str,
) -> dict[str, object]:
    payload = {
        "kind": kind,
        "code": code,
        "condition": condition,
        "baseline_count": baseline_count,
        "current_count": current_count,
        "match_ids_digest": match_ids_digest,
    }
    return {
        "event_id": _event_id({**payload, "observed_at": observed_at}),
        **payload,
        "observed_at": observed_at,
    }


def _record_active(
    active: dict[str, dict[str, object]],
    event: dict[str, object],
) -> None:
    active[event["condition"]] = {
        "event_id": event["event_id"],
        "current_count": event["current_count"],
        "match_ids_digest": event["match_ids_digest"],
    }


def _transition_state(
    previous: dict[str, object], current: dict[str, object], observed_at: str
) -> tuple[dict[str, object], list[dict[str, object]]]:
    state = deepcopy(previous)
    active = state["active_conditions"]
    events: list[dict[str, object]] = []
    for field in QUALITY_FIELDS:
        baseline = state["baseline_quality"][field]
        current_count = current["quality"][field]
        condition = f"quality:{field}"
        previous_active = active.get(condition)
        digest = _match_ids_digest(current["issue_match_ids"][field])
        if current_count > baseline:
            if (
                previous_active is None
                or current_count > previous_active["current_count"]
                or (
                    current_count == previous_active["current_count"]
                    and previous_active["match_ids_digest"] != digest
                )
            ):
                event = _event(
                    kind="anomaly",
                    code=f"{field.removesuffix('_count')}_increased",
                    condition=condition,
                    baseline_count=baseline,
                    current_count=current_count,
                    match_ids_digest=digest,
                    observed_at=observed_at,
                )
                events.append(event)
                _record_active(active, event)
        elif previous_active is not None:
            events.append(
                _event(
                    kind="recovery",
                    code=f"{field.removesuffix('_count')}_recovered",
                    condition=condition,
                    baseline_count=baseline,
                    current_count=current_count,
                    match_ids_digest=digest,
                    observed_at=observed_at,
                )
            )
            del active[condition]

    for field in MONOTONIC_FIELDS:
        high_water = state["high_water"][field]
        current_count = current["monotonic"][field]
        condition = f"regression:{field}"
        previous_active = active.get(condition)
        if current_count < high_water:
            if previous_active is None or previous_active["current_count"] != current_count:
                event = _event(
                    kind="anomaly",
                    code=f"{field}_regressed",
                    condition=condition,
                    baseline_count=high_water,
                    current_count=current_count,
                    match_ids_digest=None,
                    observed_at=observed_at,
                )
                events.append(event)
                _record_active(active, event)
        else:
            if previous_active is not None:
                events.append(
                    _event(
                        kind="recovery",
                        code=f"{field}_recovered",
                        condition=condition,
                        baseline_count=high_water,
                        current_count=current_count,
                        match_ids_digest=None,
                        observed_at=observed_at,
                    )
                )
                del active[condition]
            if current_count > high_water:
                state["high_water"][field] = current_count

    decision_count = current["monotonic"]["decision_count"]
    min_sample = current["min_sample"]
    if not state["threshold_notified"] and decision_count >= min_sample:
        events.append(
            _event(
                kind="threshold",
                code="decision_sample_reached_minimum",
                condition="threshold:decision_count",
                baseline_count=None,
                current_count=decision_count,
                match_ids_digest=None,
                observed_at=observed_at,
            )
        )
        state["threshold_notified"] = True
    state["last_input_fingerprint"] = current["input_fingerprint"]
    state["last_observed_at"] = observed_at
    return state, events


def evaluate_postmatch_sentinel(
    *,
    shadow_report: dict[str, object],
    coverage_report: dict[str, object],
    previous_state: dict[str, object] | None,
    observed_at: str,
) -> dict[str, object]:
    shadow, coverage = validate_postmatch_inputs(shadow_report, coverage_report)
    current = _sentinel_projection(shadow, coverage)
    normalized_observed_at = _parse_utc(observed_at)
    if previous_state is None:
        next_state, new_events = _initial_state(current, normalized_observed_at)
    else:
        previous = _validate_state(previous_state)
        next_state, new_events = _transition_state(
            previous, current, normalized_observed_at
        )
    return {"state": next_state, "events": new_events}


_DELIVERY_STATUSES = {"pending", "sent", "failed", "suppressed"}
_EVENT_KINDS = {"anomaly", "recovery", "threshold"}
_SAFE_NAME = re.compile(r"[a-z0-9_:.\-]+")


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _resolve_paths(
    root: str | Path,
    shadow_path: str | Path,
    coverage_path: str | Path,
    state_path: str | Path,
    lock_path: str | Path,
) -> dict[str, Path]:
    base = Path(root)
    return {
        "shadow": _resolve_path(base, shadow_path),
        "coverage": _resolve_path(base, coverage_path),
        "state": _resolve_path(base, state_path),
        "lock": _resolve_path(base, lock_path),
    }


def _strict_json_loads(raw: bytes, code: str) -> object:
    def reject_constant(_value: str) -> None:
        raise SentinelValidationError(code)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SentinelValidationError(code)
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except SentinelValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SentinelValidationError(code) from exc


def _read_report(path: Path, report_name: str) -> object:
    code = f"{report_name}_report_unreadable"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SentinelValidationError(code) from exc
    return _strict_json_loads(raw, code)


def _validate_outbox_record(value: object) -> dict[str, object]:
    record = _object(value, "sentinel_state_invalid")
    allowed_keys = {
        "event_id",
        "kind",
        "code",
        "condition",
        "baseline_count",
        "current_count",
        "match_ids_digest",
        "observed_at",
        "delivery_status",
        "attempted_at",
        "lifecycle_index",
    }
    optional_keys = {"attempted_at", "lifecycle_index"}
    if not set(record).issubset(allowed_keys) or not (
        allowed_keys - optional_keys
    ).issubset(record):
        raise SentinelValidationError("sentinel_state_invalid")
    event_id = record.get("event_id")
    kind = record.get("kind")
    code = record.get("code")
    condition = record.get("condition")
    baseline_count = record.get("baseline_count")
    current_count = record.get("current_count")
    digest = record.get("match_ids_digest")
    delivery_status = record.get("delivery_status")
    lifecycle_index = record.get("lifecycle_index")
    if (
        not isinstance(event_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", event_id) is None
        or kind not in _EVENT_KINDS
        or not isinstance(code, str)
        or _SAFE_NAME.fullmatch(code) is None
        or not isinstance(condition, str)
        or _SAFE_NAME.fullmatch(condition) is None
        or (baseline_count is not None and (type(baseline_count) is not int or baseline_count < 0))
        or type(current_count) is not int
        or current_count < 0
        or (
            digest is not None
            and (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            )
        )
        or delivery_status not in _DELIVERY_STATUSES
        or (
            lifecycle_index is not None
            and (type(lifecycle_index) is not int or lifecycle_index <= 0)
        )
    ):
        raise SentinelValidationError("sentinel_state_invalid")
    event_payload = {
        "kind": kind,
        "code": code,
        "condition": condition,
        "baseline_count": baseline_count,
        "current_count": current_count,
        "match_ids_digest": digest,
    }
    normalized_observed_at = _parse_utc(record.get("observed_at"))
    legacy_event_id = _event_id(event_payload)
    lifecycle_event_id = _event_id(
        {**event_payload, "observed_at": normalized_observed_at}
    )
    expected_event_ids = {legacy_event_id, lifecycle_event_id}
    if lifecycle_index is not None:
        expected_event_ids = {
            _event_id(
                {
                    "base_event_id": lifecycle_event_id,
                    "lifecycle_index": lifecycle_index,
                }
            )
        }
    if event_id not in expected_event_ids:
        raise SentinelValidationError("sentinel_state_invalid")
    normalized = {
        "event_id": event_id,
        "kind": kind,
        "code": code,
        "condition": condition,
        "baseline_count": baseline_count,
        "current_count": current_count,
        "match_ids_digest": digest,
        "observed_at": normalized_observed_at,
        "delivery_status": delivery_status,
    }
    attempted_at = record.get("attempted_at")
    if attempted_at is not None:
        normalized["attempted_at"] = _parse_utc(attempted_at)
    if lifecycle_index is not None:
        normalized["lifecycle_index"] = lifecycle_index
    return normalized


def _validate_runner_state(value: object) -> dict[str, object]:
    source = _object(value, "sentinel_state_invalid")
    required_keys = {
        "schema_version",
        "competition_id",
        "season",
        "baseline_quality",
        "high_water",
        "active_conditions",
        "threshold_notified",
        "last_input_fingerprint",
        "last_observed_at",
        "outbox",
    }
    if set(source) != required_keys:
        raise SentinelValidationError("sentinel_state_invalid")
    normalized = _validate_state(source)
    outbox = source["outbox"]
    if type(outbox) is not list:
        raise SentinelValidationError("sentinel_state_invalid")
    records = [_validate_outbox_record(record) for record in outbox]
    event_ids = [record["event_id"] for record in records]
    if len(event_ids) != len(set(event_ids)):
        raise SentinelValidationError("sentinel_state_invalid")
    records_by_id = {record["event_id"]: record for record in records}
    for condition, active in normalized["active_conditions"].items():
        active_record = records_by_id.get(active["event_id"])
        if (
            active_record is None
            or (
                active_record["condition"] != condition
                or active_record["kind"] != "anomaly"
                or active_record["current_count"] != active["current_count"]
                or active_record["match_ids_digest"] != active["match_ids_digest"]
            )
        ):
            raise SentinelValidationError("sentinel_state_invalid")
    normalized["outbox"] = records
    return normalized


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SentinelValidationError("sentinel_state_unreadable") from exc
    try:
        return _validate_runner_state(
            _strict_json_loads(raw, "sentinel_state_unreadable")
        )
    except SentinelValidationError as exc:
        raise SentinelValidationError("sentinel_state_unreadable") from exc


def _observed_at(value: str | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return _parse_utc(value)


def _summary(
    status: str,
    *,
    event_count: int,
    notification_status: str,
    reason: str | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "event_count": event_count,
        "notification_status": notification_status,
    }
    if reason is not None:
        result["reason"] = reason
    if error_type is not None:
        result["error_type"] = error_type
    return result


def _state_unreadable_summary() -> dict[str, Any]:
    return _summary(
        "error",
        reason="sentinel_state_unreadable",
        error_type="SentinelValidationError",
        event_count=0,
        notification_status="not_attempted",
    )


def _meaningful_core(state: dict[str, object]) -> dict[str, object]:
    comparable = {
        key: deepcopy(value)
        for key, value in state.items()
        if key not in {"last_observed_at", "outbox"}
    }
    return comparable


def _input_recovery_events(
    previous_state: dict[str, object] | None,
    next_state: dict[str, object],
    observed_at: str,
) -> list[dict[str, object]]:
    if previous_state is None:
        return []
    events: list[dict[str, object]] = []
    for condition, active in sorted(previous_state["active_conditions"].items()):
        if not condition.startswith("input:"):
            continue
        reason = condition.removeprefix("input:")
        events.append(
            _event(
                kind="recovery",
                code=f"{reason}_recovered",
                condition=condition,
                baseline_count=None,
                current_count=0,
                match_ids_digest=None,
                observed_at=observed_at,
            )
        )
        next_state["active_conditions"].pop(condition, None)
    return events


def _evaluate_valid_reports(
    shadow_report: object,
    coverage_report: object,
    previous_state: dict[str, object] | None,
    observed_at: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    evaluated = evaluate_postmatch_sentinel(
        shadow_report=shadow_report,
        coverage_report=coverage_report,
        previous_state=previous_state,
        observed_at=observed_at,
    )
    next_state = evaluated["state"]
    events = list(evaluated["events"])
    events.extend(_input_recovery_events(previous_state, next_state, observed_at))
    return next_state, events


def _input_error_transition(
    previous_state: dict[str, object], reason: str, observed_at: str
) -> tuple[dict[str, object], list[dict[str, object]]]:
    next_state = deepcopy(previous_state)
    next_state.pop("outbox", None)
    condition = f"input:{reason}"
    active = next_state["active_conditions"]
    events: list[dict[str, object]] = []
    if condition not in active:
        event = _event(
            kind="anomaly",
            code=reason,
            condition=condition,
            baseline_count=None,
            current_count=0,
            match_ids_digest=None,
            observed_at=observed_at,
        )
        events.append(event)
        _record_active(active, event)
    next_state["last_observed_at"] = observed_at
    return next_state, events


def _load_and_evaluate(
    paths: dict[str, Path],
    previous_state: dict[str, object] | None,
    observed_at: str,
) -> tuple[dict[str, object] | None, list[dict[str, object]], str | None]:
    try:
        shadow = _read_report(paths["shadow"], "shadow")
        coverage = _read_report(paths["coverage"], "coverage")
        next_state, events = _evaluate_valid_reports(
            shadow, coverage, previous_state, observed_at
        )
        return next_state, events, None
    except SentinelValidationError as exc:
        if previous_state is None:
            return None, [], exc.code
        next_state, events = _input_error_transition(
            previous_state, exc.code, observed_at
        )
        return next_state, events, exc.code


def _run_dry(paths: dict[str, Path], *, observed_at: str | None) -> dict[str, Any]:
    try:
        previous_state = _read_state(paths["state"])
    except SentinelValidationError:
        return _state_unreadable_summary()
    try:
        timestamp = _observed_at(observed_at)
    except SentinelValidationError as exc:
        return _summary(
            "error",
            reason=exc.code,
            error_type="SentinelValidationError",
            event_count=0,
            notification_status="not_attempted",
        )
    _next_state, events, reason = _load_and_evaluate(
        paths, previous_state, timestamp
    )
    if _next_state is None:
        return _summary(
            "error",
            reason=reason,
            error_type="SentinelValidationError",
            event_count=0,
            notification_status="not_attempted",
        )
    return _summary(
        "dry_run_ready",
        reason=reason,
        event_count=len(events),
        notification_status="not_attempted",
    )


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_state_atomic(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        verified = _strict_json_loads(
            temp_path.read_bytes(), "sentinel_state_invalid"
        )
        _validate_runner_state(verified)
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _origin_was_suppressed(
    event: dict[str, object],
    previous_state: dict[str, object] | None,
) -> bool:
    if event["kind"] != "recovery" or previous_state is None:
        return False
    active = previous_state["active_conditions"].get(event["condition"])
    if active is None:
        return False
    origin_id = active["event_id"]
    return any(
        item["event_id"] == origin_id and item["delivery_status"] == "suppressed"
        for item in previous_state["outbox"]
    )


def _add_outbox_events(
    outbox: list[dict[str, object]],
    events: list[dict[str, object]],
    *,
    notify: bool,
    previous_state: dict[str, object] | None,
    next_state: dict[str, object],
) -> list[str]:
    new_statuses: list[str] = []
    existing_ids = {item["event_id"] for item in outbox}
    for event in events:
        suppressed = not notify or _origin_was_suppressed(event, previous_state)
        delivery_status = "suppressed" if suppressed else "pending"
        record = {**deepcopy(event), "delivery_status": delivery_status}
        base_event_id = record["event_id"]
        lifecycle_index = 0
        while record["event_id"] in existing_ids:
            lifecycle_index += 1
            record["event_id"] = _event_id(
                {
                    "base_event_id": base_event_id,
                    "lifecycle_index": lifecycle_index,
                }
            )
        if lifecycle_index:
            record["lifecycle_index"] = lifecycle_index
            active = next_state["active_conditions"].get(record["condition"])
            if active is not None and active["event_id"] == base_event_id:
                active["event_id"] = record["event_id"]
        outbox.append(record)
        existing_ids.add(record["event_id"])
        new_statuses.append(delivery_status)
    return new_statuses


def _notification_content(records: list[dict[str, object]]) -> str:
    severity = {"anomaly": 0, "recovery": 1, "threshold": 2}
    ordered = sorted(
        records,
        key=lambda item: (
            severity[item["kind"]],
            item["code"],
            item["event_id"],
        ),
    )
    lines = ["中超赛后数据监控"]
    visible = ordered[:5] if len(ordered) <= 5 else ordered[:4]
    for item in visible:
        baseline = item["baseline_count"]
        baseline_text = "-" if baseline is None else str(baseline)
        if item["kind"] == "threshold":
            detail = (
                f"threshold {item['code']} 正式样本 {item['current_count']}/"
                f"{DEFAULT_MIN_SAMPLE}，仅启动人工复盘"
            )
        else:
            detail = (
                f"{item['kind']} {item['code']} "
                f"{baseline_text}->{item['current_count']}"
            )
        lines.append(
            f"{detail} #{str(item['event_id'])[:10]} {item['observed_at']}"
        )
    if len(ordered) > 5:
        lines.append(f"其余 {len(ordered) - len(visible)} 个安全事件详情已留存本地状态。")
    lines.append(RESEARCH_NOTICE)
    return "\n".join(lines)


def _run_locked(
    paths: dict[str, Path],
    *,
    observed_at: str | None,
    notify: bool,
    notify_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    try:
        previous_state = _read_state(paths["state"])
    except SentinelValidationError:
        return _state_unreadable_summary()
    try:
        timestamp = _observed_at(observed_at)
    except SentinelValidationError as exc:
        return _summary(
            "error",
            reason=exc.code,
            error_type="SentinelValidationError",
            event_count=0,
            notification_status="not_attempted",
        )
    next_core, events, input_reason = _load_and_evaluate(
        paths, previous_state, timestamp
    )
    if next_core is None:
        return _summary(
            "error",
            reason=input_reason,
            error_type="SentinelValidationError",
            event_count=0,
            notification_status="not_attempted",
        )

    previous_outbox = (
        deepcopy(previous_state["outbox"]) if previous_state is not None else []
    )
    core_changed = (
        previous_state is None
        or _meaningful_core(previous_state) != _meaningful_core(next_core)
    )
    if previous_state is not None and not core_changed and not events:
        next_core["last_observed_at"] = previous_state["last_observed_at"]
    new_statuses = _add_outbox_events(
        previous_outbox,
        events,
        notify=notify,
        previous_state=previous_state,
        next_state=next_core,
    )
    state = {**next_core, "outbox": previous_outbox}
    state_changed = previous_state is None or core_changed or bool(events)

    retry_records = []
    if notify:
        retry_records = [
            item
            for item in state["outbox"]
            if item["delivery_status"] in {"pending", "failed"}
        ]
        if retry_records:
            for item in retry_records:
                item["delivery_status"] = "pending"
                item["attempted_at"] = timestamp
            state_changed = True

    if state_changed:
        _write_state_atomic(paths["state"], state)

    notification_status = (
        "suppressed"
        if new_statuses and all(status == "suppressed" for status in new_statuses)
        else "not_attempted"
    )
    if retry_records:
        try:
            delivery = notify_fn(
                _notification_content(retry_records),
                summary="中超赛后数据监控提醒",
            )
            sent = type(delivery) is dict and delivery.get("status") == "sent"
        except Exception:
            sent = False
        notification_status = "sent" if sent else "failed"
        for item in retry_records:
            item["delivery_status"] = notification_status
        _write_state_atomic(paths["state"], state)

    status = "stored" if state_changed else "unchanged"
    return _summary(
        status,
        reason=input_reason,
        event_count=len(events),
        notification_status=notification_status,
    )


def run_csl_postmatch_sentinel(
    *,
    root: str | Path = ".",
    shadow_path: str | Path = DEFAULT_SHADOW_REPORT,
    coverage_path: str | Path = DEFAULT_COVERAGE_REPORT,
    state_path: str | Path = DEFAULT_STATE,
    lock_path: str | Path = DEFAULT_LOCK,
    observed_at: str | None = None,
    write: bool = False,
    notify: bool = False,
    notify_fn: Callable[..., dict[str, Any]] = send_wxpusher_notification,
) -> dict[str, Any]:
    if notify and not write:
        raise ValueError("notify_requires_write")
    paths = _resolve_paths(root, shadow_path, coverage_path, state_path, lock_path)
    if not write:
        return _run_dry(paths, observed_at=observed_at)
    try:
        with _exclusive_lock(paths["lock"]):
            return _run_locked(
                paths,
                observed_at=observed_at,
                notify=notify,
                notify_fn=notify_fn,
            )
    except Exception:
        return _summary(
            "error",
            reason="sentinel_state_write_failed",
            error_type="SentinelStateWriteError",
            event_count=0,
            notification_status="not_attempted",
        )


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., dict[str, Any]] = run_csl_postmatch_sentinel,
) -> int:
    parser = argparse.ArgumentParser(description="Run the CSL postmatch sentinel")
    parser.add_argument("--root", default=".")
    parser.add_argument("--shadow-path", default=DEFAULT_SHADOW_REPORT)
    parser.add_argument("--coverage-path", default=DEFAULT_COVERAGE_REPORT)
    parser.add_argument("--state-path", default=DEFAULT_STATE)
    parser.add_argument("--lock-path", default=DEFAULT_LOCK)
    parser.add_argument("--observed-at", default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    result = runner(
        root=args.root,
        shadow_path=args.shadow_path,
        coverage_path=args.coverage_path,
        state_path=args.state_path,
        lock_path=args.lock_path,
        observed_at=args.observed_at,
        write=args.write,
        notify=args.notify,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"dry_run_ready", "stored", "unchanged"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
