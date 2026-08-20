"""Pure validation and event evaluation for the CSL postmatch sentinel."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re


REPORT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_MIN_SAMPLE = 50
RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"

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
    normalized: dict[str, object] = {
        "actionable": _strict_count(sample.get("actionable"), invalid_code),
        "decided": _strict_count(sample.get("decided"), invalid_code),
        "decision_count": _strict_count(sample.get("decision_count"), invalid_code),
        "min_sample": _strict_positive_count(sample.get("min_sample"), invalid_code),
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
        "event_id": _event_id(payload),
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
