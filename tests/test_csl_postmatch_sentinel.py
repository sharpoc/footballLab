from __future__ import annotations

from copy import deepcopy

from worldcup.csl_postmatch_sentinel import (
    SentinelValidationError,
    evaluate_postmatch_sentinel,
    validate_postmatch_inputs,
)


def _reports(
    *,
    decision_count: int = 38,
    missing_closing: int = 128,
    missing_decision: int = 8,
    finished_result_count: int | None = None,
    closing_available_count: int | None = None,
    generated_at: str = "2026-08-15T10:36:37Z",
    min_sample: int = 50,
):
    tally = {"hit": decision_count, "miss": 0, "push": 0, "no_pick": 0}
    sample = {
        "actionable": decision_count,
        "decided": decision_count,
        "decision_count": decision_count,
        "hit_rate": 1.0 if decision_count else None,
        "min_sample": min_sample,
        "pick_rate": 1.0 if decision_count else 0.0,
        "sample_too_small": decision_count < min_sample,
    }
    closing_available = (
        decision_count + missing_decision
        if closing_available_count is None
        else closing_available_count
    )
    finished_results = (
        closing_available + missing_closing
        if finished_result_count is None
        else finished_result_count
    )
    coverage_block = {
        "finished_result_count": finished_results,
        "closing_available_count": closing_available,
        "decision_available_count": decision_count,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "legacy_decision_count": 0,
        "missing_closing_count": missing_closing,
        "missing_decision_count": missing_decision,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    matches = [
        {
            "match_id": f"csl_2026:missing:{index}",
            "settlement": {"status": "missing_closing"},
        }
        for index in range(missing_closing)
    ] + [
        {
            "match_id": f"csl_2026:no-decision:{index}",
            "settlement": {"status": "missing_decision"},
        }
        for index in range(missing_decision)
    ]
    shadow = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "a" * 64,
        "status": "ok",
        "decision_sample": sample,
        "decision_tally": tally,
        "decision_coverage": coverage_block,
        "matches": matches,
    }
    coverage = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "b" * 64,
        "summary": {
            "finished_result_count": coverage_block["finished_result_count"],
            "missing_count": missing_closing,
            "observed_closing_count": coverage_block["closing_available_count"],
            "observed_current_decision_count": decision_count,
            "observed_missing_current_decision_count": missing_decision,
        },
        "performance": {
            "observed": {
                "decision_sample": deepcopy(sample),
                "decision_tally": deepcopy(tally),
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            }
        },
        "matches": [],
    }
    return shadow, coverage


def test_validate_inputs_rejects_cross_report_mismatch():
    shadow, coverage = _reports()
    coverage["summary"]["observed_current_decision_count"] = 37
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "coverage_shadow_mismatch"
    else:
        raise AssertionError("mismatched reports must fail closed")


def test_validate_inputs_rejects_min_sample_below_fixed_threshold():
    shadow, coverage = _reports(decision_count=38, min_sample=1)
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "shadow_report_invalid"
    else:
        raise AssertionError("min_sample below 50 must fail closed")


def test_validate_inputs_rejects_min_sample_above_fixed_threshold():
    shadow, coverage = _reports(decision_count=50, min_sample=51)
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "shadow_report_invalid"
    else:
        raise AssertionError("min_sample above 50 must fail closed")


def test_first_evaluation_baselines_existing_128_and_8_without_alerting():
    shadow, coverage = _reports()
    result = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )
    assert result["events"] == []
    assert result["state"]["baseline_quality"] == {
        "missing_closing_count": 128,
        "missing_decision_count": 8,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    assert result["state"]["high_water"]["decision_count"] == 38


def test_new_gap_is_alerted_once_then_expansion_and_recovery_are_distinct():
    shadow, coverage = _reports()
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]

    expanded_shadow, expanded_coverage = _reports(
        missing_closing=129,
        finished_result_count=175,
        closing_available_count=46,
    )
    first = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow,
        coverage_report=expanded_coverage,
        previous_state=baseline,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in first["events"]] == [
        "missing_closing_increased"
    ]

    unchanged = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow,
        coverage_report=expanded_coverage,
        previous_state=first["state"],
        observed_at="2026-08-20T02:00:00Z",
    )
    assert unchanged["events"] == []

    wider_shadow, wider_coverage = _reports(
        missing_closing=130,
        finished_result_count=176,
        closing_available_count=46,
    )
    wider = evaluate_postmatch_sentinel(
        shadow_report=wider_shadow,
        coverage_report=wider_coverage,
        previous_state=unchanged["state"],
        observed_at="2026-08-20T03:00:00Z",
    )
    assert [event["kind"] for event in wider["events"]] == ["anomaly"]

    recovered_shadow, recovered_coverage = _reports(
        decision_count=40,
        missing_closing=128,
        missing_decision=8,
        finished_result_count=176,
        closing_available_count=48,
    )
    recovered = evaluate_postmatch_sentinel(
        shadow_report=recovered_shadow,
        coverage_report=recovered_coverage,
        previous_state=wider["state"],
        observed_at="2026-08-20T04:00:00Z",
    )
    assert [event["kind"] for event in recovered["events"]] == ["recovery"]


def test_count_regression_keeps_high_water_until_recovery():
    shadow, coverage = _reports(decision_count=38)
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]
    lower_shadow, lower_coverage = _reports(decision_count=37)
    lower = evaluate_postmatch_sentinel(
        shadow_report=lower_shadow,
        coverage_report=lower_coverage,
        previous_state=baseline,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert lower["state"]["high_water"]["decision_count"] == 38
    assert "decision_count_regressed" in {event["code"] for event in lower["events"]}


def test_threshold_crosses_once_and_hit_rate_change_does_not_alert():
    before_shadow, before_coverage = _reports(decision_count=49)
    state = evaluate_postmatch_sentinel(
        shadow_report=before_shadow,
        coverage_report=before_coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]
    at_shadow, at_coverage = _reports(decision_count=50)
    crossed = evaluate_postmatch_sentinel(
        shadow_report=at_shadow,
        coverage_report=at_coverage,
        previous_state=state,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in crossed["events"]] == [
        "decision_sample_reached_minimum"
    ]
    changed = deepcopy(at_shadow)
    changed["decision_tally"] = {"hit": 25, "miss": 25, "push": 0, "no_pick": 0}
    changed["decision_sample"].update({"hit_rate": 0.5, "decided": 50})
    changed_coverage = deepcopy(at_coverage)
    changed_coverage["performance"]["observed"]["decision_tally"] = deepcopy(
        changed["decision_tally"]
    )
    changed_coverage["performance"]["observed"]["decision_sample"] = deepcopy(
        changed["decision_sample"]
    )
    second = evaluate_postmatch_sentinel(
        shadow_report=changed,
        coverage_report=changed_coverage,
        previous_state=crossed["state"],
        observed_at="2026-08-20T02:00:00Z",
    )
    assert second["events"] == []
