"""Local-only CSL closing coverage runner with default zero-write behavior."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
from tempfile import mkstemp
from typing import Any, Callable
from uuid import uuid4

from worldcup.club_rating import ClubResult, load_club_results_csv
from worldcup.collectors.csl_result_sources import (
    parse_cfl_official_fixture_rows,
    parse_sevenm_fixture_rows,
)
from worldcup.csl_closing_coverage import (
    ALLOWED_REASON_CODES,
    AUDIT_ISSUE_ORDER,
    INITIAL_EXPECTED_GAPS,
    INITIAL_MATCH_IDS_SHA256,
    STATUS_PROVENANCE,
    build_coverage_report,
    build_initial_missing_manifest,
    coverage_input_fingerprint,
    initial_match_ids_sha256,
    manifest_match_ids,
    normalize_audit_events,
    validate_initial_manifest,
)
from worldcup.csl_eval_data import load_snapshots
from worldcup.sources.csl_results import (
    CFL_OFFICIAL_2026_URL,
    SEVENM_2026_FIXTURE_URL,
)


DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_RESULTS = "data/cache/club_results_csl_2026.csv"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"
DEFAULT_RAW_DIR = "data/cache/csl_results_sources"
DEFAULT_INITIAL_MANIFEST = (
    "data/local/backfill/csl_2026/initial_missing_manifest.json"
)
DEFAULT_REPORT = "data/local/diagnostics/csl_closing_coverage.json"
DEFAULT_PENDING = "data/local/diagnostics/csl_closing_coverage_pending.json"
DEFAULT_LOCK = "data/local/diagnostics/csl_closing_coverage.lock"
REPORT_MIN_SAMPLE = 50
REPORT_STATUS_ORDER = (
    "observed_current_decision",
    "observed_missing_current_decision",
    "reconstructed",
    "market_baseline_only",
    "manual_review",
    "missing",
)
REPORT_ROOT_KEYS = {
    "schema_version",
    "competition_id",
    "season",
    "generated_at",
    "membership",
    "summary",
    "reason_counts",
    "month_counts",
    "reason_by_month",
    "audit_issue_counts",
    "operational_events",
    "operational_event_counts",
    "performance",
    "matches",
    "research_notice",
    "input_fingerprint",
}
REPORT_MATCH_KEYS = {
    "match_id",
    "competition_id",
    "season",
    "match_date",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_canonical",
    "away_canonical",
    "provenance_class",
    "coverage_status",
    "reason_code",
    "reason_codes",
    "closing_snapshot_at",
    "closing_snapshot_run_id",
    "audit_issue_codes",
    "operational_history_codes",
    "settlement",
}
REPORT_EVENT_KEYS = {
    "observed_at",
    "match_id",
    "kickoff_at_utc",
    "home_canonical",
    "away_canonical",
    "issue_code",
}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def _validate_timestamp(value: Any, reason: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(reason)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(reason)


def _validate_snapshots(snapshots: list[Any]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("history_snapshot_object_required")
        if not isinstance(snapshot.get("matches"), list):
            raise ValueError("history_snapshot_matches_required")
        if any(not isinstance(match, dict) for match in snapshot["matches"]):
            raise ValueError("history_snapshot_match_object_required")
        _validate_timestamp(
            snapshot.get("snapshot_at"), "history_snapshot_at_required"
        )
        competition = snapshot.get("competition")
        if (
            not isinstance(competition, dict)
            or competition.get("id") != DEFAULT_COMPETITION_ID
        ):
            raise ValueError("history_snapshot_competition_invalid")
        validated.append(snapshot)
    return validated


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _exact_dict(value: Any, keys: set[str], reason: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(reason)
    return value


def _nonempty_string(value: Any, reason: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(reason)
    return value


def _nonnegative_int(value: Any, reason: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(reason)
    return value


def _validate_count_map(value: Any, reason: str) -> None:
    if type(value) is not dict:
        raise ValueError(reason)
    for key, count in value.items():
        _nonempty_string(key, reason)
        _nonnegative_int(count, reason)


def _validate_ordered_codes(value: Any, *, allowed: tuple[str, ...], reason: str) -> list[str]:
    if type(value) is not list or any(type(code) is not str for code in value):
        raise ValueError(reason)
    if value != [code for code in allowed if code in set(value)]:
        raise ValueError(reason)
    return value


def _validate_settlement(value: Any) -> None:
    settlement = _exact_dict(
        value,
        {"status", "label", "detail", "settlement_class"},
        "coverage_report_settlement_schema_mismatch",
    )
    status = settlement.get("status")
    labels = {"hit": "命中", "miss": "未中", "push": "走水"}
    classes = {
        "hit": {"full_win", "half_win"},
        "miss": {"full_loss", "half_loss"},
        "push": {"push"},
    }
    if status not in labels or settlement.get("label") != labels[status]:
        raise ValueError("coverage_report_settlement_status_invalid")
    if settlement.get("settlement_class") not in classes[status]:
        raise ValueError("coverage_report_settlement_class_invalid")
    detail = settlement.get("detail")
    if type(detail) is not str or re.fullmatch(r"全场 (0|[1-9]\d*)-(0|[1-9]\d*)", detail) is None:
        raise ValueError("coverage_report_settlement_score_invalid")


def _validate_event_rows(events: Any) -> list[dict[str, Any]]:
    if type(events) is not list:
        raise ValueError("coverage_report_events_invalid")
    for event in events:
        _exact_dict(
            event,
            REPORT_EVENT_KEYS,
            "coverage_report_event_schema_mismatch",
        )
        for key in ("match_id", "home_canonical", "away_canonical"):
            _nonempty_string(event.get(key), "coverage_report_event_identity_invalid")
        _validate_timestamp(
            event.get("observed_at"), "coverage_report_event_time_invalid"
        )
        _validate_timestamp(
            event.get("kickoff_at_utc"), "coverage_report_event_kickoff_invalid"
        )
        if event.get("issue_code") not in AUDIT_ISSUE_ORDER:
            raise ValueError("coverage_report_event_issue_invalid")
    if normalize_audit_events(events) != events:
        raise ValueError("coverage_report_events_not_normalized")
    return events


def _validate_membership(value: Any, match_ids: set[str]) -> dict[str, Any]:
    membership = _exact_dict(
        value,
        {"initial_missing_count", "initial_missing_match_ids", "observed_cutoff"},
        "coverage_report_membership_schema_mismatch",
    )
    count = _nonnegative_int(
        membership.get("initial_missing_count"),
        "coverage_report_membership_count_invalid",
    )
    if count != INITIAL_EXPECTED_GAPS:
        raise ValueError("coverage_report_membership_count_invalid")
    ids = membership.get("initial_missing_match_ids")
    if (
        type(ids) is not list
        or any(type(match_id) is not str or not match_id for match_id in ids)
        or ids != sorted(set(ids))
        or len(ids) != count
        or not set(ids).issubset(match_ids)
    ):
        raise ValueError("coverage_report_membership_identity_invalid")
    if initial_match_ids_sha256(ids) != INITIAL_MATCH_IDS_SHA256:
        raise ValueError("coverage_report_membership_hash_invalid")
    if membership.get("observed_cutoff") != "2026-06-29":
        raise ValueError("coverage_report_membership_cutoff_invalid")
    return membership


def _validate_performance_schema(value: Any) -> None:
    performance = _exact_dict(
        value,
        {"observed", "reconstructed"},
        "coverage_report_performance_schema_mismatch",
    )
    observed = _exact_dict(
        performance.get("observed"),
        {"decision_tally", "decision_sample", "official_headline_scope"},
        "coverage_report_observed_performance_schema_mismatch",
    )
    tally = _exact_dict(
        observed.get("decision_tally"),
        {"hit", "miss", "push", "no_pick"},
        "coverage_report_tally_schema_mismatch",
    )
    for count in tally.values():
        _nonnegative_int(count, "coverage_report_tally_invalid")
    sample = _exact_dict(
        observed.get("decision_sample"),
        {
            "min_sample",
            "decided",
            "actionable",
            "decision_count",
            "sample_too_small",
            "hit_rate",
            "pick_rate",
        },
        "coverage_report_sample_schema_mismatch",
    )
    for key in ("min_sample", "decided", "actionable", "decision_count"):
        _nonnegative_int(sample.get(key), "coverage_report_sample_count_invalid")
    if type(sample.get("sample_too_small")) is not bool:
        raise ValueError("coverage_report_sample_flag_invalid")
    for key in ("hit_rate", "pick_rate"):
        rate = sample.get(key)
        if rate is not None and (
            type(rate) not in {int, float} or isinstance(rate, bool) or not 0 <= rate <= 1
        ):
            raise ValueError("coverage_report_sample_rate_invalid")
    if observed.get("official_headline_scope") != "observed_schema_v2_match_pick_only":
        raise ValueError("coverage_report_headline_scope_invalid")
    reconstructed = _exact_dict(
        performance.get("reconstructed"),
        {"status", "combined_with_observed"},
        "coverage_report_reconstructed_schema_mismatch",
    )
    if reconstructed != {
        "status": "not_implemented",
        "combined_with_observed": False,
    }:
        raise ValueError("coverage_report_reconstructed_invalid")


def _validate_match_rows(
    matches: Any,
    *,
    events: list[dict[str, Any]],
    initial_ids: set[str],
) -> list[dict[str, Any]]:
    if type(matches) is not list:
        raise ValueError("coverage_report_matches_invalid")
    seen: set[str] = set()
    for match in matches:
        _exact_dict(
            match,
            REPORT_MATCH_KEYS,
            "coverage_report_match_schema_mismatch",
        )
        for key in (
            "match_id",
            "match_date",
            "home_team",
            "away_team",
            "home_canonical",
            "away_canonical",
        ):
            _nonempty_string(match.get(key), "coverage_report_match_identity_invalid")
        match_id = match["match_id"]
        if match_id in seen:
            raise ValueError("coverage_report_match_identity_duplicate")
        seen.add(match_id)
        if match.get("competition_id") != DEFAULT_COMPETITION_ID:
            raise ValueError("coverage_report_match_competition_invalid")
        if match.get("season") != DEFAULT_SEASON:
            raise ValueError("coverage_report_match_season_invalid")
        expected_id = ":".join(
            (
                DEFAULT_COMPETITION_ID,
                match["match_date"],
                match["home_canonical"],
                match["away_canonical"],
            )
        )
        if match_id != expected_id:
            raise ValueError("coverage_report_match_identity_mismatch")
        try:
            datetime.strptime(match["match_date"], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("coverage_report_match_date_invalid") from exc
        kickoff = match.get("kickoff_at_utc")
        if kickoff is not None:
            _validate_timestamp(kickoff, "coverage_report_match_kickoff_invalid")

        status = match.get("coverage_status")
        if status not in REPORT_STATUS_ORDER:
            raise ValueError("coverage_report_match_status_invalid")
        if match.get("provenance_class") != STATUS_PROVENANCE[status]:
            raise ValueError("coverage_report_match_provenance_invalid")
        reasons = _validate_ordered_codes(
            match.get("reason_codes"),
            allowed=ALLOWED_REASON_CODES[status],
            reason="coverage_report_match_reasons_invalid",
        )
        if not reasons or match.get("reason_code") != reasons[0]:
            raise ValueError("coverage_report_match_reason_invalid")

        closing_at = match.get("closing_snapshot_at")
        closing_run = match.get("closing_snapshot_run_id")
        if status.startswith("observed_"):
            _validate_timestamp(
                closing_at, "coverage_report_closing_snapshot_at_invalid"
            )
            if kickoff is not None:
                closing_dt = datetime.fromisoformat(closing_at.replace("Z", "+00:00"))
                kickoff_dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
                if closing_dt >= kickoff_dt:
                    raise ValueError("coverage_report_closing_not_pre_kickoff")
        elif closing_at is not None or closing_run is not None:
            raise ValueError("coverage_report_nonobserved_closing_invalid")
        if closing_run is not None:
            _nonempty_string(
                closing_run, "coverage_report_closing_snapshot_run_id_invalid"
            )

        history_codes = _validate_ordered_codes(
            match.get("operational_history_codes"),
            allowed=AUDIT_ISSUE_ORDER,
            reason="coverage_report_match_history_invalid",
        )
        audit_codes = _validate_ordered_codes(
            match.get("audit_issue_codes"),
            allowed=AUDIT_ISSUE_ORDER,
            reason="coverage_report_match_audit_invalid",
        )
        matching_event_codes = {
            event["issue_code"]
            for event in events
            if event["kickoff_at_utc"] == kickoff
            and event["home_canonical"] == match["home_canonical"]
            and event["away_canonical"] == match["away_canonical"]
        }
        expected_history = [
            code for code in AUDIT_ISSUE_ORDER if code in matching_event_codes
        ]
        expected_audit_set = (
            set() if status.startswith("observed_") else set(matching_event_codes)
        )
        if not status.startswith("observed_") and match_id not in initial_ids:
            expected_audit_set.add("closing_archive_missing")
        expected_audit = [
            code for code in AUDIT_ISSUE_ORDER if code in expected_audit_set
        ]
        if history_codes != expected_history or audit_codes != expected_audit:
            raise ValueError("coverage_report_match_audit_mismatch")

        settlement = match.get("settlement")
        if settlement is not None:
            if status != "observed_current_decision":
                raise ValueError("coverage_report_settlement_scope_invalid")
            _validate_settlement(settlement)
    return matches


def _validate_pending(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "status",
        "attempt_id",
        "attempted_at",
        "input_fingerprint",
        "reason",
        "error_type",
    }
    if set(value) != required:
        raise ValueError("coverage_pending_schema_mismatch")
    if value.get("schema_version") != 1 or value.get("status") != "pending":
        raise ValueError("coverage_pending_metadata_mismatch")
    if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
        raise ValueError("coverage_pending_attempt_required")
    _validate_timestamp(value.get("attempted_at"), "coverage_pending_time_required")
    fingerprint = value.get("input_fingerprint")
    if fingerprint is not None and not _is_sha256(fingerprint):
        raise ValueError("coverage_pending_fingerprint_invalid")
    if value.get("reason") not in {
        "coverage_reconciliation_pending",
        "coverage_reconciliation_failed",
        "coverage_report_commit_pending",
        "coverage_report_commit_failed",
    }:
        raise ValueError("coverage_pending_reason_invalid")
    error_type = value.get("error_type")
    if error_type is not None and (
        not isinstance(error_type, str) or not error_type
    ):
        raise ValueError("coverage_pending_error_type_invalid")
    return value


def _derived_report_fields(
    matches: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    months: Counter[str] = Counter()
    reasons_by_month: dict[str, Counter[str]] = {}
    audit_issues: Counter[str] = Counter()
    settlement_statuses: Counter[str] = Counter()
    for match in matches:
        status = match.get("coverage_status")
        reason = match.get("reason_code")
        match_date = match.get("match_date")
        issues = match.get("audit_issue_codes")
        settlement = match.get("settlement")
        if status not in REPORT_STATUS_ORDER:
            raise ValueError("coverage_report_match_status_invalid")
        if not isinstance(reason, str) or not reason:
            raise ValueError("coverage_report_match_reason_invalid")
        if not isinstance(match_date, str) or len(match_date) < 7:
            raise ValueError("coverage_report_match_date_invalid")
        if not isinstance(issues, list) or any(
            not isinstance(issue, str) for issue in issues
        ):
            raise ValueError("coverage_report_match_audit_invalid")
        statuses[status] += 1
        reasons[reason] += 1
        month = match_date[:7]
        months[month] += 1
        reasons_by_month.setdefault(month, Counter())[reason] += 1
        audit_issues.update(issues)
        if settlement is not None:
            if not isinstance(settlement, dict):
                raise ValueError("coverage_report_settlement_invalid")
            settlement_status = settlement.get("status")
            if not isinstance(settlement_status, str):
                raise ValueError("coverage_report_settlement_status_invalid")
            settlement_statuses[settlement_status] += 1

    tally = {
        key: settlement_statuses[key]
        for key in ("hit", "miss", "push", "no_pick")
    }
    decided = tally["hit"] + tally["miss"]
    actionable = decided + tally["push"]
    decision_count = actionable + tally["no_pick"]
    observed_performance = {
        "decision_tally": tally,
        "decision_sample": {
            "min_sample": REPORT_MIN_SAMPLE,
            "decided": decided,
            "actionable": actionable,
            "decision_count": decision_count,
            "sample_too_small": decided < REPORT_MIN_SAMPLE,
            "hit_rate": tally["hit"] / decided if decided else None,
            "pick_rate": actionable / decision_count if decision_count else None,
        },
        "official_headline_scope": "observed_schema_v2_match_pick_only",
    }
    return {
        "summary": {
            "finished_result_count": len(matches),
            "observed_closing_count": statuses["observed_current_decision"]
            + statuses["observed_missing_current_decision"],
            "observed_current_decision_count": statuses[
                "observed_current_decision"
            ],
            "observed_missing_current_decision_count": statuses[
                "observed_missing_current_decision"
            ],
            "reconstructed_count": statuses["reconstructed"],
            "market_baseline_only_count": statuses["market_baseline_only"],
            "manual_review_count": statuses["manual_review"],
            "missing_count": statuses["missing"],
        },
        "reason_counts": dict(sorted(reasons.items())),
        "month_counts": dict(sorted(months.items())),
        "reason_by_month": {
            month: dict(sorted(reasons_by_month[month].items()))
            for month in sorted(reasons_by_month)
        },
        "audit_issue_counts": dict(sorted(audit_issues.items())),
        "operational_event_counts": dict(
            sorted(Counter(event["issue_code"] for event in events).items())
        ),
        "performance": {
            "observed": observed_performance,
            "reconstructed": {
                "status": "not_implemented",
                "combined_with_observed": False,
            },
        },
        "research_notice": "仅用于研究分析，不构成投注建议。",
    }


def _validate_report(value: dict[str, Any]) -> dict[str, Any]:
    _exact_dict(value, REPORT_ROOT_KEYS, "coverage_report_schema_mismatch")
    if value.get("schema_version") != 1:
        raise ValueError("coverage_report_schema_version_invalid")
    if value.get("competition_id") != DEFAULT_COMPETITION_ID:
        raise ValueError("coverage_report_competition_invalid")
    if value.get("season") != DEFAULT_SEASON:
        raise ValueError("coverage_report_season_invalid")
    _validate_timestamp(
        value.get("generated_at"), "coverage_report_generated_at_invalid"
    )
    if not _is_sha256(value.get("input_fingerprint")):
        raise ValueError("coverage_report_fingerprint_invalid")
    events = _validate_event_rows(value.get("operational_events"))
    raw_matches = value.get("matches")
    if type(raw_matches) is not list:
        raise ValueError("coverage_report_matches_invalid")
    raw_match_ids = [
        match.get("match_id")
        for match in raw_matches
        if type(match) is dict and type(match.get("match_id")) is str
    ]
    membership = _validate_membership(value.get("membership"), set(raw_match_ids))
    matches = _validate_match_rows(
        raw_matches,
        events=events,
        initial_ids=set(membership["initial_missing_match_ids"]),
    )
    summary = _exact_dict(
        value.get("summary"),
        {
            "finished_result_count",
            "observed_closing_count",
            "observed_current_decision_count",
            "observed_missing_current_decision_count",
            "reconstructed_count",
            "market_baseline_only_count",
            "manual_review_count",
            "missing_count",
        },
        "coverage_report_summary_schema_mismatch",
    )
    for count in summary.values():
        _nonnegative_int(count, "coverage_report_summary_count_invalid")
    for key in (
        "reason_counts",
        "month_counts",
        "audit_issue_counts",
        "operational_event_counts",
    ):
        _validate_count_map(value.get(key), f"coverage_report_{key}_invalid")
    reason_by_month = value.get("reason_by_month")
    if type(reason_by_month) is not dict:
        raise ValueError("coverage_report_reason_by_month_invalid")
    for month, reason_counts in reason_by_month.items():
        if type(month) is not str or re.fullmatch(r"\d{4}-\d{2}", month) is None:
            raise ValueError("coverage_report_reason_by_month_invalid")
        _validate_count_map(
            reason_counts, "coverage_report_reason_by_month_invalid"
        )
    _validate_performance_schema(value.get("performance"))
    if coverage_input_fingerprint(value) != value["input_fingerprint"]:
        raise ValueError("coverage_report_fingerprint_mismatch")
    for key, expected in _derived_report_fields(matches, events).items():
        if value.get(key) != expected:
            raise ValueError(f"coverage_report_derived_mismatch:{key}")
    return value


def _validate_manifest_document(
    manifest: dict[str, Any],
    *,
    results: list[ClubResult],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    expected_count: int,
    expected_ids_sha256: str,
) -> frozenset[str]:
    _validate_timestamp(
        manifest.get("created_at"), "initial_manifest_created_at_invalid"
    )
    return validate_initial_manifest(
        manifest,
        results=results,
        official_rows=official_rows,
        sevenm_rows=sevenm_rows,
        expected_count=expected_count,
        expected_ids_sha256=expected_ids_sha256,
    )


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _validate_write_paths(
    *,
    write_paths: list[Path],
    protected_files: list[Path],
    protected_directories: list[Path],
) -> None:
    if len(set(write_paths)) != len(write_paths):
        raise ValueError("write_path_alias")
    for target in write_paths:
        if target in protected_files or any(
            _is_within(target, directory) for directory in protected_directories
        ):
            raise ValueError("protected_input_alias")


def _identity_paths(
    root: Path,
    *,
    results_path: str | Path,
    history: str | Path,
    raw_dir: str | Path,
) -> tuple[Path, Path, Path, Path, Path]:
    results = _resolve(root, results_path)
    history_path = _resolve(root, history)
    raw = _resolve(root, raw_dir)
    return (
        results,
        history_path,
        raw,
        (raw / "cfl_official_2026.json").resolve(),
        (raw / "sevenm_2026_fixture.js").resolve(),
    )


def _load_identity_inputs(
    root: Path,
    *,
    results_path: str | Path,
    raw_dir: str | Path,
) -> tuple[list[ClubResult], list[dict[str, str]], list[dict[str, str]]]:
    results = load_club_results_csv(
        _resolve(root, results_path), DEFAULT_COMPETITION_ID
    )
    raw = _resolve(root, raw_dir)
    official_payload = _read_json_object(raw / "cfl_official_2026.json")
    sevenm_source = (raw / "sevenm_2026_fixture.js").read_text(encoding="utf-8")
    official_rows = parse_cfl_official_fixture_rows(
        official_payload,
        season=DEFAULT_SEASON,
        source_url=CFL_OFFICIAL_2026_URL,
    )
    sevenm_rows = parse_sevenm_fixture_rows(
        sevenm_source,
        season=DEFAULT_SEASON,
        source_url=SEVENM_2026_FIXTURE_URL,
    )
    return results, official_rows, sevenm_rows


def _load_inputs(
    root: Path,
    *,
    results_path: str | Path,
    history: str | Path,
    raw_dir: str | Path,
) -> tuple[
    list[ClubResult],
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    results, official_rows, sevenm_rows = _load_identity_inputs(
        root,
        results_path=results_path,
        raw_dir=raw_dir,
    )
    snapshots = _validate_snapshots(load_snapshots(_resolve(root, history)))
    return results, snapshots, official_rows, sevenm_rows


def _report_summary(
    report: dict[str, Any], *, status: str, write: bool
) -> dict[str, Any]:
    return {
        "status": status,
        "write": write,
        "competition_id": report["competition_id"],
        "season": report["season"],
        "input_fingerprint": report["input_fingerprint"],
        "finished_result_count": report["summary"]["finished_result_count"],
        "observed_closing_count": report["summary"]["observed_closing_count"],
        "observed_current_decision_count": report["summary"][
            "observed_current_decision_count"
        ],
        "missing_count": report["summary"]["missing_count"],
        "sample_too_small": report["performance"]["observed"][
            "decision_sample"
        ]["sample_too_small"],
    }


def _run_initial_manifest_locked(
    *,
    root: str | Path = ".",
    write: bool = False,
    created_at: str,
    expected_count: int = 128,
    expected_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
    results_path: str | Path = DEFAULT_RESULTS,
    history: str | Path = DEFAULT_HISTORY,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    output: str | Path = DEFAULT_INITIAL_MANIFEST,
) -> dict[str, Any]:
    base = {
        "write": write,
        "competition_id": DEFAULT_COMPETITION_ID,
        "season": DEFAULT_SEASON,
    }
    root_path = Path(root)
    target = _resolve(root_path, output)
    if target.exists():
        try:
            results, official_rows, sevenm_rows = _load_identity_inputs(
                root_path,
                results_path=results_path,
                raw_dir=raw_dir,
            )
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": "coverage_inputs_unavailable",
                "error_type": type(exc).__name__,
                **base,
            }
        try:
            existing = _read_json_object(target)
            existing_ids = manifest_match_ids(existing)
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": "initial_manifest_invalid",
                "error_type": type(exc).__name__,
                **base,
            }
        try:
            _validate_manifest_document(
                existing,
                results=results,
                official_rows=official_rows,
                sevenm_rows=sevenm_rows,
                expected_count=expected_count,
                expected_ids_sha256=expected_ids_sha256,
            )
        except Exception:
            return {
                "status": "blocked",
                "reason": "initial_manifest_identity_mismatch",
                **base,
            }
        return {
            "status": "unchanged",
            **base,
            "matches": len(existing_ids),
            "observed_cutoff": existing["observed_cutoff"],
        }
    try:
        results, snapshots, official_rows, sevenm_rows = _load_inputs(
            root_path,
            results_path=results_path,
            history=history,
            raw_dir=raw_dir,
        )
        candidate = build_initial_missing_manifest(
            results=results,
            snapshots=snapshots,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            created_at=created_at,
            expected_count=expected_count,
        )
        _validate_manifest_document(
            candidate,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            expected_count=expected_count,
            expected_ids_sha256=expected_ids_sha256,
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "coverage_inputs_unavailable",
            "error_type": type(exc).__name__,
            **base,
        }
    summary = {
        "status": "stored" if write else "dry_run",
        **base,
        "matches": len(manifest_match_ids(candidate)),
        "observed_cutoff": candidate["observed_cutoff"],
    }
    if write:
        try:
            write_json_atomic(target, candidate)
        except Exception as exc:
            return {
                "status": "error",
                "reason": "initial_manifest_commit_failed",
                "error_type": type(exc).__name__,
                **base,
            }
    return summary


def _cleanup_pending_if_owned(
    path: Path, attempt_id: str
) -> tuple[str, str | None]:
    if not path.exists():
        return "cleared", None
    try:
        current = _validate_pending(_read_json_object(path))
    except Exception as exc:
        return "owner_changed", type(exc).__name__
    if current["attempt_id"] != attempt_id:
        return "owner_changed", None
    try:
        path.unlink()
    except Exception as exc:
        return "error", type(exc).__name__
    return "cleared", None


def _pending_cleanup_summary(
    report: dict[str, Any],
    *,
    stored: bool,
    cleanup: tuple[str, str | None],
) -> dict[str, Any]:
    outcome, error_type = cleanup
    if outcome == "cleared":
        summary = _report_summary(
            report, status="stored" if stored else "unchanged", write=True
        )
        if not stored:
            summary["stale_pending_cleared"] = True
        return summary
    summary = _report_summary(
        report,
        status="stored_pending_cleanup"
        if stored
        else "unchanged_pending_cleanup",
        write=True,
    )
    summary["reason"] = (
        "coverage_pending_owner_changed"
        if outcome == "owner_changed"
        else "coverage_pending_cleanup_failed"
    )
    if error_type is not None:
        summary["error_type"] = error_type
    return summary


def _run_closing_coverage_locked(
    *,
    root: str | Path = ".",
    write: bool = False,
    generated_at: str,
    results_path: str | Path = DEFAULT_RESULTS,
    history: str | Path = DEFAULT_HISTORY,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    initial_manifest_path: str | Path = DEFAULT_INITIAL_MANIFEST,
    report_path: str | Path = DEFAULT_REPORT,
    pending_path: str | Path = DEFAULT_PENDING,
    audit_events: list[dict[str, Any]] | None = None,
    expected_initial_count: int = INITIAL_EXPECTED_GAPS,
    expected_initial_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
    report_write: Callable[[Path, dict[str, Any]], None] = write_json_atomic,
) -> dict[str, Any]:
    root_path = Path(root)
    canonical_path = _resolve(root_path, report_path)
    recovery_path = _resolve(root_path, pending_path)
    existing = None
    if canonical_path.exists():
        try:
            existing = _validate_report(_read_json_object(canonical_path))
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": "coverage_report_invalid",
                "error_type": type(exc).__name__,
                "write": write,
            }
    prior_pending = None
    if write and recovery_path.exists():
        try:
            prior_pending = _validate_pending(_read_json_object(recovery_path))
        except Exception as exc:
            return {
                "status": "blocked",
                "reason": "coverage_pending_invalid",
                "error_type": type(exc).__name__,
                "write": True,
            }
    prior_events = existing.get("operational_events", []) if existing else []
    attempt_id = uuid4().hex
    initial_pending = {
        "schema_version": 1,
        "status": "pending",
        "attempt_id": attempt_id,
        "attempted_at": generated_at,
        "input_fingerprint": (
            prior_pending.get("input_fingerprint") if prior_pending else None
        ),
        "reason": "coverage_reconciliation_pending",
        "error_type": None,
    }
    if write:
        try:
            write_json_atomic(recovery_path, initial_pending)
        except Exception as exc:
            return {
                "status": "error",
                "reason": "coverage_pending_commit_failed",
                "error_type": type(exc).__name__,
                "write": True,
            }
    try:
        results, snapshots, official_rows, sevenm_rows = _load_inputs(
            root_path,
            results_path=results_path,
            history=history,
            raw_dir=raw_dir,
        )
        manifest = _read_json_object(_resolve(root_path, initial_manifest_path))
        _validate_manifest_document(
            manifest,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            expected_count=expected_initial_count,
            expected_ids_sha256=expected_initial_ids_sha256,
        )
        report = build_coverage_report(
            snapshots=snapshots,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            initial_manifest=manifest,
            generated_at=generated_at,
            audit_events=[*prior_events, *(audit_events or [])],
        )
        _validate_report(report)
    except Exception as exc:
        if write:
            try:
                write_json_atomic(
                    recovery_path,
                    {
                        **initial_pending,
                        "reason": "coverage_reconciliation_failed",
                        "error_type": type(exc).__name__,
                    },
                )
            except Exception:
                pass
        return {
            "status": "blocked",
            "reason": "coverage_inputs_unavailable",
            "error_type": type(exc).__name__,
            "write": write,
        }
    if not write:
        return _report_summary(report, status="dry_run", write=False)

    pending = {
        **initial_pending,
        "input_fingerprint": report["input_fingerprint"],
        "reason": "coverage_report_commit_pending",
    }
    try:
        write_json_atomic(recovery_path, pending)
    except Exception as exc:
        return {
            "status": "error",
            "reason": "coverage_pending_commit_failed",
            "error_type": type(exc).__name__,
            "write": True,
        }

    if (
        existing is not None
        and existing.get("input_fingerprint") == report["input_fingerprint"]
    ):
        return _pending_cleanup_summary(
            existing,
            stored=False,
            cleanup=_cleanup_pending_if_owned(recovery_path, attempt_id),
        )

    try:
        report_write(canonical_path, report)
    except Exception as exc:
        failed_pending = {
            **pending,
            "reason": "coverage_report_commit_failed",
            "error_type": type(exc).__name__,
        }
        try:
            write_json_atomic(recovery_path, failed_pending)
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "coverage_report_commit_failed",
            "error_type": type(exc).__name__,
            "write": True,
            "input_fingerprint": report["input_fingerprint"],
        }
    return _pending_cleanup_summary(
        report,
        stored=True,
        cleanup=_cleanup_pending_if_owned(recovery_path, attempt_id),
    )


def run_initial_manifest(
    *,
    root: str | Path = ".",
    lock_path: str | Path = DEFAULT_LOCK,
    **kwargs: Any,
) -> dict[str, Any]:
    write = bool(kwargs.get("write", False))
    try:
        root_path = Path(root).resolve()
        results, history, raw, official, sevenm = _identity_paths(
            root_path,
            results_path=kwargs.get("results_path", DEFAULT_RESULTS),
            history=kwargs.get("history", DEFAULT_HISTORY),
            raw_dir=kwargs.get("raw_dir", DEFAULT_RAW_DIR),
        )
        lock = _resolve(root_path, lock_path)
        output = _resolve(
            root_path, kwargs.get("output", DEFAULT_INITIAL_MANIFEST)
        )
        _validate_write_paths(
            write_paths=[output, lock],
            protected_files=[results, official, sevenm],
            protected_directories=[history, raw],
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "initial_manifest_path_conflict",
            "error_type": type(exc).__name__,
            "write": write,
            "competition_id": DEFAULT_COMPETITION_ID,
            "season": DEFAULT_SEASON,
        }
    try:
        if not write:
            return _run_initial_manifest_locked(root=root_path, **kwargs)
        with exclusive_file_lock(lock):
            return _run_initial_manifest_locked(root=root_path, **kwargs)
    except Exception as exc:
        return {
            "status": "error" if write else "blocked",
            "reason": "initial_manifest_runner_failed",
            "error_type": type(exc).__name__,
            "write": write,
            "competition_id": DEFAULT_COMPETITION_ID,
            "season": DEFAULT_SEASON,
        }


def run_closing_coverage(
    *,
    root: str | Path = ".",
    lock_path: str | Path = DEFAULT_LOCK,
    **kwargs: Any,
) -> dict[str, Any]:
    write = bool(kwargs.get("write", False))
    try:
        root_path = Path(root).resolve()
        results, history, raw, official, sevenm = _identity_paths(
            root_path,
            results_path=kwargs.get("results_path", DEFAULT_RESULTS),
            history=kwargs.get("history", DEFAULT_HISTORY),
            raw_dir=kwargs.get("raw_dir", DEFAULT_RAW_DIR),
        )
        manifest = _resolve(
            root_path,
            kwargs.get("initial_manifest_path", DEFAULT_INITIAL_MANIFEST),
        )
        report = _resolve(root_path, kwargs.get("report_path", DEFAULT_REPORT))
        pending = _resolve(
            root_path, kwargs.get("pending_path", DEFAULT_PENDING)
        )
        lock = _resolve(root_path, lock_path)
        _validate_write_paths(
            write_paths=[report, pending, lock],
            protected_files=[results, official, sevenm, manifest],
            protected_directories=[history, raw],
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "coverage_path_conflict",
            "error_type": type(exc).__name__,
            "write": write,
        }
    try:
        _validate_timestamp(
            kwargs.get("generated_at"), "coverage_generated_at_invalid"
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "coverage_generated_at_invalid",
            "error_type": type(exc).__name__,
            "write": write,
        }
    try:
        if not write:
            return _run_closing_coverage_locked(root=root_path, **kwargs)
        with exclusive_file_lock(lock):
            return _run_closing_coverage_locked(root=root_path, **kwargs)
    except Exception as exc:
        return {
            "status": "error" if write else "blocked",
            "reason": "coverage_runner_failed",
            "error_type": type(exc).__name__,
            "write": write,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit local CSL observed closing coverage. Defaults to dry-run."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--initial-manifest", action="store_true")
    parser.add_argument("--write-initial-manifest", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write_initial_manifest and args.write:
        parser.error("--write-initial-manifest cannot be combined with --write")
    observed = args.generated_at or datetime.now(timezone.utc).isoformat()
    if args.initial_manifest or args.write_initial_manifest:
        result = run_initial_manifest(
            root=args.root,
            write=args.write_initial_manifest,
            created_at=observed,
        )
    else:
        result = run_closing_coverage(
            root=args.root,
            write=args.write,
            generated_at=observed,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return (
        0
        if result.get("status")
        in {
            "dry_run",
            "stored",
            "unchanged",
            "unchanged_pending_cleanup",
            "stored_pending_cleanup",
        }
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
