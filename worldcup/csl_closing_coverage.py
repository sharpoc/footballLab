"""Pure CSL closing coverage contracts and reconciliation helpers.

This module never reads files, secrets, databases, or network resources.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal

from worldcup.club_rating import ClubResult
from worldcup.csl_eval_data import ClosingMatch
from worldcup.decision_settlement import (
    settle_match_decision,
    summarize_decision_records,
)


ProvenanceClass = Literal["observed", "reconstructed", "none"]
CoverageStatus = Literal[
    "observed_current_decision",
    "observed_missing_current_decision",
    "reconstructed",
    "market_baseline_only",
    "manual_review",
    "missing",
]

STATUS_PROVENANCE: dict[str, ProvenanceClass] = {
    "observed_current_decision": "observed",
    "observed_missing_current_decision": "observed",
    "reconstructed": "reconstructed",
    "market_baseline_only": "none",
    "manual_review": "none",
    "missing": "none",
}
ALLOWED_REASON_CODES: dict[str, tuple[str, ...]] = {
    "observed_current_decision": ("observed_closing",),
    "observed_missing_current_decision": (
        "legacy_decision",
        "no_current_decision",
    ),
    "manual_review": (
        "identity_mismatch",
        "kickoff_conflict",
        "source_conflict",
        "duplicate_event_conflict",
    ),
    "reconstructed": ("reconstructed_eligible",),
    "market_baseline_only": (
        "quote_time_unverifiable",
        "insufficient_bookmakers",
        "no_complete_main_market",
        "aggregate_only",
    ),
    "missing": (
        "source_unavailable",
        "source_access_blocked",
        "source_unapproved",
        "kickoff_unverifiable",
        "no_market_record",
        "post_kickoff_only",
    ),
}
HISTORICAL_STATUS_PRIORITY = {
    "manual_review": 3,
    "reconstructed": 4,
    "market_baseline_only": 5,
    "missing": 6,
}


@dataclass(frozen=True)
class HistoricalCoverageEvidence:
    status: Literal[
        "reconstructed", "market_baseline_only", "manual_review", "missing"
    ]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class CoverageClassification:
    provenance_class: ProvenanceClass
    coverage_status: CoverageStatus
    reason_code: str
    reason_codes: tuple[str, ...]


def stable_match_id(result: ClubResult) -> str:
    return (
        f"{result.competition_id}:{result.date}:"
        f"{result.home_canonical}:{result.away_canonical}"
    )


def _current_decision(decision: Any) -> bool:
    return (
        isinstance(decision, dict)
        and decision.get("schema_version") == 2
        and decision.get("policy_version") == "match_pick_v3"
        and decision.get("label") in {"MATCH_PICK", "NO_CLEAN_MARKET"}
    )


def _classification(
    status: CoverageStatus, reason_codes: tuple[str, ...]
) -> CoverageClassification:
    allowed = ALLOWED_REASON_CODES[status]
    unique = set(reason_codes)
    for reason in unique:
        if reason not in allowed:
            raise ValueError(f"reason_not_allowed:{status}:{reason}")
    ordered = tuple(reason for reason in allowed if reason in unique)
    if not ordered:
        raise ValueError(f"missing_reason:{status}")
    return CoverageClassification(
        provenance_class=STATUS_PROVENANCE[status],
        coverage_status=status,
        reason_code=ordered[0],
        reason_codes=ordered,
    )


def classify_coverage(
    *,
    observed: ClosingMatch | None,
    historical: HistoricalCoverageEvidence
    | tuple[HistoricalCoverageEvidence, ...]
    | None = None,
) -> CoverageClassification:
    if observed is not None:
        decision = observed.entry.get("match_decision")
        if _current_decision(decision):
            return _classification(
                "observed_current_decision", ("observed_closing",)
            )
        reason = (
            "legacy_decision" if isinstance(decision, dict) else "no_current_decision"
        )
        return _classification("observed_missing_current_decision", (reason,))
    if historical is None:
        return _classification("missing", ("no_market_record",))
    candidates = (
        (historical,)
        if isinstance(historical, HistoricalCoverageEvidence)
        else historical
    )
    if not candidates:
        return _classification("missing", ("no_market_record",))
    classified = [
        _classification(item.status, item.reason_codes) for item in candidates
    ]
    winning_status = min(
        classified,
        key=lambda item: HISTORICAL_STATUS_PRIORITY[item.coverage_status],
    ).coverage_status
    return _classification(
        winning_status,
        tuple(
            reason
            for item in candidates
            if item.status == winning_status
            for reason in item.reason_codes
        ),
    )


def classification_dict(value: CoverageClassification) -> dict[str, Any]:
    return {
        "provenance_class": value.provenance_class,
        "coverage_status": value.coverage_status,
        "reason_code": value.reason_code,
        "reason_codes": list(value.reason_codes),
    }


INITIAL_OBSERVED_CUTOFF = "2026-06-29"
INITIAL_EXPECTED_GAPS = 128
INITIAL_MATCH_IDS_SHA256 = (
    "530acaa872d753c911861e2cab1e1bf6a2a0a87c595028d9c5e369523a7f6a40"
)


@dataclass(frozen=True)
class FixtureResolution:
    kickoff_at_utc: str | None
    source_match_ids: dict[str, str]
    reason_codes: tuple[str, ...]


def _utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone_required:{value}")
    return parsed.astimezone(timezone.utc).isoformat()


def _fixture_candidates(
    result: ClubResult, rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("season") == result.season
        and str(row.get("kickoff_at_utc") or "")[:10] == result.date
        and row.get("home_canonical") == result.home_canonical
        and row.get("away_canonical") == result.away_canonical
    ]


def resolve_fixture(
    result: ClubResult,
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
) -> FixtureResolution:
    official = _fixture_candidates(result, official_rows)
    sevenm = _fixture_candidates(result, sevenm_rows)
    if len(official) > 1 or len(sevenm) > 1:
        return FixtureResolution(None, {}, ("duplicate_event_conflict",))
    if len(official) != 1 or len(sevenm) != 1:
        return FixtureResolution(None, {}, ("identity_mismatch",))
    official_kickoff = _utc_iso(official[0]["kickoff_at_utc"])
    sevenm_kickoff = _utc_iso(sevenm[0]["kickoff_at_utc"])
    if official_kickoff != sevenm_kickoff:
        return FixtureResolution(None, {}, ("kickoff_conflict",))
    official_source_id = official[0].get("source_match_id")
    sevenm_source_id = sevenm[0].get("source_match_id")
    if (
        not isinstance(official_source_id, str)
        or not official_source_id.strip()
        or not isinstance(sevenm_source_id, str)
        or not sevenm_source_id.strip()
    ):
        return FixtureResolution(None, {}, ("identity_mismatch",))
    return FixtureResolution(
        kickoff_at_utc=official_kickoff,
        source_match_ids={
            "cfl_official": official_source_id.strip(),
            "sevenm": sevenm_source_id.strip(),
        },
        reason_codes=(),
    )


def select_observed_closing_exact(
    snapshots: list[dict[str, Any]],
    *,
    competition_id: str,
    kickoff_at_utc: str,
    home_canonical: str,
    away_canonical: str,
) -> ClosingMatch | None:
    kickoff = datetime.fromisoformat(
        kickoff_at_utc.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    selected: ClosingMatch | None = None
    selected_at: datetime | None = None
    for snapshot in snapshots:
        snapshot_at_raw = snapshot.get("snapshot_at")
        if not snapshot_at_raw:
            continue
        snapshot_at = datetime.fromisoformat(
            str(snapshot_at_raw).replace("Z", "+00:00")
        )
        if snapshot_at.tzinfo is None:
            continue
        snapshot_at = snapshot_at.astimezone(timezone.utc)
        if snapshot_at >= kickoff:
            continue
        for entry in snapshot.get("matches") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("fixture_status") or "").upper() == "POSTPONED":
                continue
            entry_competition = (
                ((entry.get("competition") or {}).get("id"))
                or ((snapshot.get("competition") or {}).get("id"))
            )
            if entry_competition != competition_id:
                continue
            try:
                entry_kickoff = _utc_iso(str(entry.get("kickoff_at_utc") or ""))
            except (TypeError, ValueError):
                continue
            if entry_kickoff != _utc_iso(kickoff_at_utc):
                continue
            if (
                entry.get("home_canonical") != home_canonical
                or entry.get("away_canonical") != away_canonical
            ):
                continue
            if selected_at is None or snapshot_at > selected_at:
                run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
                selected = ClosingMatch(
                    entry=entry,
                    snapshot_at=str(snapshot_at_raw),
                    snapshot_run_id=(
                        str(run.get("run_id")) if run.get("run_id") else None
                    ),
                )
                selected_at = snapshot_at
    return selected


def build_initial_missing_manifest(
    *,
    results: list[ClubResult],
    snapshots: list[dict[str, Any]],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    created_at: str,
    competition_id: str = "csl_2026",
    season: str = "2026",
    observed_cutoff: str = INITIAL_OBSERVED_CUTOFF,
    expected_count: int = INITIAL_EXPECTED_GAPS,
) -> dict[str, Any]:
    candidates = []
    for result in sorted(results, key=stable_match_id):
        if result.competition_id != competition_id or result.season != season:
            continue
        if result.date >= observed_cutoff:
            continue
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        if fixture.kickoff_at_utc is None:
            raise ValueError(
                f"initial_fixture_unverified:{stable_match_id(result)}:"
                f"{','.join(fixture.reason_codes)}"
            )
        observed = select_observed_closing_exact(
            snapshots,
            competition_id=competition_id,
            kickoff_at_utc=fixture.kickoff_at_utc,
            home_canonical=result.home_canonical,
            away_canonical=result.away_canonical,
        )
        if observed is not None:
            continue
        candidates.append(
            {
                "match_id": stable_match_id(result),
                "competition_id": competition_id,
                "season": season,
                "match_date": result.date,
                "kickoff_at_utc": fixture.kickoff_at_utc,
                "home_team": result.home_team,
                "away_team": result.away_team,
                "home_canonical": result.home_canonical,
                "away_canonical": result.away_canonical,
                "source_match_ids": fixture.source_match_ids,
                "provenance_class": "none",
                "coverage_status": "missing",
                "reason_code": "source_unapproved",
                "reason_codes": ["source_unapproved"],
                "probe_status": "awaiting_source_approval",
                "approved_source_ids": [],
                "expected_request_scope": "single_match_page_only",
            }
        )
    if len(candidates) != expected_count:
        raise ValueError(
            f"initial_gap_count_mismatch:{len(candidates)}:{expected_count}"
        )
    _validate_manifest_source_identities(candidates)
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "season": season,
        "created_at": _utc_iso(created_at),
        "observed_cutoff": observed_cutoff,
        "expected_match_count": expected_count,
        "membership_policy": "fixed_match_ids_v1",
        "matches": candidates,
    }


def manifest_match_ids(manifest: dict[str, Any]) -> frozenset[str]:
    rows = manifest.get("matches")
    if not isinstance(rows, list):
        raise ValueError("invalid_initial_manifest_matches")
    ids = [str(row.get("match_id") or "") for row in rows if isinstance(row, dict)]
    if (
        len(ids) != len(rows)
        or any(not value for value in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("invalid_initial_manifest_identity")
    expected = manifest.get("expected_match_count")
    if expected != len(ids):
        raise ValueError(f"initial_manifest_count_mismatch:{len(ids)}:{expected}")
    return frozenset(ids)


def initial_match_ids_sha256(ids: Collection[str]) -> str:
    encoded = json.dumps(
        sorted(ids),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_manifest_source_identities(rows: list[dict[str, Any]]) -> None:
    seen: dict[str, set[str]] = {
        "cfl_official": set(),
        "sevenm": set(),
    }
    for row in rows:
        source_match_ids = row.get("source_match_ids")
        if type(source_match_ids) is not dict or set(source_match_ids) != set(seen):
            raise ValueError("initial_manifest_source_identity_schema_mismatch")
        for source, source_id in source_match_ids.items():
            if type(source_id) is not str or not source_id.strip():
                raise ValueError(
                    f"initial_manifest_source_identity_invalid:{source}"
                )
            if source_id in seen[source]:
                raise ValueError(
                    f"initial_manifest_source_identity_duplicate:{source}"
                )
            seen[source].add(source_id)


def validate_initial_manifest(
    manifest: dict[str, Any],
    *,
    results: list[ClubResult],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    expected_count: int = INITIAL_EXPECTED_GAPS,
    expected_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
) -> frozenset[str]:
    root_keys = {
        "schema_version",
        "competition_id",
        "season",
        "created_at",
        "observed_cutoff",
        "expected_match_count",
        "membership_policy",
        "matches",
    }
    row_keys = {
        "match_id",
        "competition_id",
        "season",
        "match_date",
        "kickoff_at_utc",
        "home_team",
        "away_team",
        "home_canonical",
        "away_canonical",
        "source_match_ids",
        "provenance_class",
        "coverage_status",
        "reason_code",
        "reason_codes",
        "probe_status",
        "approved_source_ids",
        "expected_request_scope",
    }
    if type(manifest) is not dict or set(manifest) != root_keys:
        raise ValueError("initial_manifest_schema_mismatch")
    expected_metadata = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "observed_cutoff": INITIAL_OBSERVED_CUTOFF,
        "expected_match_count": expected_count,
        "membership_policy": "fixed_match_ids_v1",
    }
    for key, expected in expected_metadata.items():
        if manifest.get(key) != expected:
            raise ValueError(f"initial_manifest_metadata_mismatch:{key}")
    ids = manifest_match_ids(manifest)
    if len(ids) != expected_count:
        raise ValueError(
            f"initial_manifest_count_mismatch:{len(ids)}:{expected_count}"
        )
    if initial_match_ids_sha256(ids) != expected_ids_sha256:
        raise ValueError("initial_manifest_membership_hash_mismatch")
    by_id = {stable_match_id(result): result for result in results}
    for row in manifest["matches"]:
        if type(row) is not dict or set(row) != row_keys:
            raise ValueError("initial_manifest_row_schema_mismatch")
        match_id = str(row["match_id"])
        result = by_id.get(match_id)
        if result is None:
            raise ValueError(f"initial_manifest_result_missing:{match_id}")
        expected_identity = {
            "competition_id": result.competition_id,
            "season": result.season,
            "match_date": result.date,
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_canonical": result.home_canonical,
            "away_canonical": result.away_canonical,
            "provenance_class": "none",
            "coverage_status": "missing",
            "reason_code": "source_unapproved",
            "reason_codes": ["source_unapproved"],
            "probe_status": "awaiting_source_approval",
            "approved_source_ids": [],
            "expected_request_scope": "single_match_page_only",
        }
        for key, expected in expected_identity.items():
            if row.get(key) != expected:
                raise ValueError(f"initial_manifest_row_mismatch:{match_id}:{key}")
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        if fixture.kickoff_at_utc is None:
            raise ValueError(f"initial_manifest_fixture_unverified:{match_id}")
        if row.get("kickoff_at_utc") != fixture.kickoff_at_utc:
            raise ValueError(
                f"initial_manifest_row_mismatch:{match_id}:kickoff_at_utc"
            )
        if row.get("source_match_ids") != fixture.source_match_ids:
            raise ValueError(
                f"initial_manifest_row_mismatch:{match_id}:source_match_ids"
            )
    _validate_manifest_source_identities(manifest["matches"])
    return ids


def initial_manifest_fingerprint(manifest: dict[str, Any]) -> str:
    payload = {
        "schema_version": manifest.get("schema_version"),
        "competition_id": manifest.get("competition_id"),
        "season": manifest.get("season"),
        "observed_cutoff": manifest.get("observed_cutoff"),
        "expected_match_count": manifest.get("expected_match_count"),
        "membership_policy": manifest.get("membership_policy"),
        "matches": sorted(
            manifest.get("matches") or [],
            key=lambda row: str(row.get("match_id") or ""),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row["coverage_status"]) for row in rows)
    return {
        "finished_result_count": len(rows),
        "observed_closing_count": counts["observed_current_decision"]
        + counts["observed_missing_current_decision"],
        "observed_current_decision_count": counts["observed_current_decision"],
        "observed_missing_current_decision_count": counts[
            "observed_missing_current_decision"
        ],
        "reconstructed_count": counts["reconstructed"],
        "market_baseline_only_count": counts["market_baseline_only"],
        "manual_review_count": counts["manual_review"],
        "missing_count": counts["missing"],
    }


AUDIT_ISSUE_ORDER = (
    "closing_archive_missing",
    "quota_blocked",
    "provider_refresh_failed",
    "snapshot_archive_failed",
    "archive_validation_failed",
)


def normalize_audit_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for event in events:
        issue = str(event.get("issue_code") or "")
        if issue not in AUDIT_ISSUE_ORDER:
            raise ValueError(f"invalid_audit_issue_code:{issue}")
        kickoff = _utc_iso(str(event.get("kickoff_at_utc") or ""))
        home = str(event.get("home_canonical") or "").strip()
        away = str(event.get("away_canonical") or "").strip()
        observed_at = _utc_iso(str(event.get("observed_at") or ""))
        if not home or not away:
            raise ValueError("invalid_audit_event_identity")
        key = (kickoff, home, away, issue)
        candidate = {
            "observed_at": observed_at,
            "match_id": str(event.get("match_id") or ""),
            "kickoff_at_utc": kickoff,
            "home_canonical": home,
            "away_canonical": away,
            "issue_code": issue,
        }
        existing = normalized.get(key)
        if existing is None or (
            candidate["observed_at"], candidate["match_id"]
        ) < (
            existing["observed_at"], existing["match_id"]
        ):
            normalized[key] = candidate
    return sorted(
        normalized.values(),
        key=lambda row: (
            row["kickoff_at_utc"],
            row["home_canonical"],
            row["away_canonical"],
            AUDIT_ISSUE_ORDER.index(row["issue_code"]),
        ),
    )


def build_coverage_report(
    *,
    snapshots: list[dict[str, Any]],
    results: list[ClubResult],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    initial_manifest: dict[str, Any],
    generated_at: str,
    audit_events: list[dict[str, Any]] | None = None,
    competition_id: str = "csl_2026",
    season: str = "2026",
    min_sample: int = 50,
) -> dict[str, Any]:
    initial_ids = manifest_match_ids(initial_manifest)
    operational_events = normalize_audit_events(audit_events or [])
    rows: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    for result in sorted(results, key=stable_match_id):
        if result.competition_id != competition_id or result.season != season:
            continue
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        observed = (
            select_observed_closing_exact(
                snapshots,
                competition_id=competition_id,
                kickoff_at_utc=fixture.kickoff_at_utc,
                home_canonical=result.home_canonical,
                away_canonical=result.away_canonical,
            )
            if fixture.kickoff_at_utc is not None
            else None
        )
        if observed is not None:
            historical = None
        elif fixture.reason_codes:
            historical = HistoricalCoverageEvidence(
                status="manual_review",
                reason_codes=fixture.reason_codes,
            )
        elif stable_match_id(result) in initial_ids:
            historical = HistoricalCoverageEvidence(
                status="missing",
                reason_codes=("source_unapproved",),
            )
        else:
            historical = HistoricalCoverageEvidence(
                status="missing",
                reason_codes=("no_market_record",),
            )
        classification = classify_coverage(observed=observed, historical=historical)
        match_kickoff_raw = (
            observed.entry.get("kickoff_at_utc")
            if observed is not None
            else fixture.kickoff_at_utc
        )
        match_kickoff = _utc_iso(str(match_kickoff_raw)) if match_kickoff_raw else None
        event_codes = {
            event["issue_code"]
            for event in operational_events
            if event["kickoff_at_utc"] == match_kickoff
            and event["home_canonical"] == result.home_canonical
            and event["away_canonical"] == result.away_canonical
        }
        audit_issues = set(event_codes) if observed is None else set()
        if observed is None and stable_match_id(result) not in initial_ids:
            audit_issues.add("closing_archive_missing")
        row = {
            "match_id": stable_match_id(result),
            "competition_id": competition_id,
            "season": season,
            "match_date": result.date,
            "kickoff_at_utc": (
                observed.entry.get("kickoff_at_utc")
                if observed is not None
                else fixture.kickoff_at_utc
            ),
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_canonical": result.home_canonical,
            "away_canonical": result.away_canonical,
            **classification_dict(classification),
            "closing_snapshot_at": observed.snapshot_at if observed else None,
            "closing_snapshot_run_id": observed.snapshot_run_id if observed else None,
            "audit_issue_codes": [
                code for code in AUDIT_ISSUE_ORDER if code in audit_issues
            ],
            "operational_history_codes": [
                code for code in AUDIT_ISSUE_ORDER if code in event_codes
            ],
        }
        result_payload = {
            "home_score": result.home_score,
            "away_score": result.away_score,
        }
        decision = observed.entry.get("match_decision") if observed is not None else None
        is_current_match_pick = (
            observed is not None
            and classification.coverage_status == "observed_current_decision"
            and isinstance(decision, dict)
            and decision.get("label") == "MATCH_PICK"
        )
        row["settlement"] = (
            settle_match_decision(decision, result_payload)
            if is_current_match_pick
            else None
        )
        rows.append(row)
        if is_current_match_pick:
            settled.append(
                {
                    "closing_match_decision": decision,
                    "result": result_payload,
                }
            )
    canonical = summarize_decision_records(settled, min_sample=min_sample)
    report = {
        "schema_version": 1,
        "competition_id": competition_id,
        "season": season,
        "generated_at": _utc_iso(generated_at),
        "membership": {
            "initial_missing_count": len(initial_ids),
            "initial_missing_match_ids": sorted(initial_ids),
            "observed_cutoff": initial_manifest.get("observed_cutoff"),
        },
        "summary": _status_summary(rows),
        "reason_counts": dict(
            sorted(Counter(row["reason_code"] for row in rows).items())
        ),
        "month_counts": dict(
            sorted(Counter(row["match_date"][:7] for row in rows).items())
        ),
        "reason_by_month": {
            month: dict(
                sorted(
                    Counter(
                        row["reason_code"]
                        for row in rows
                        if row["match_date"][:7] == month
                    ).items()
                )
            )
            for month in sorted({row["match_date"][:7] for row in rows})
        },
        "audit_issue_counts": dict(
            sorted(
                Counter(
                    code for row in rows for code in row["audit_issue_codes"]
                ).items()
            )
        ),
        "operational_events": operational_events,
        "operational_event_counts": dict(
            sorted(
                Counter(event["issue_code"] for event in operational_events).items()
            )
        ),
        "performance": {
            "observed": {
                "decision_tally": canonical["decision_tally"],
                "decision_sample": canonical["sample"],
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            },
            "reconstructed": {
                "status": "not_implemented",
                "combined_with_observed": False,
            },
        },
        "matches": rows,
        "research_notice": "仅用于研究分析，不构成投注建议。",
    }
    report["input_fingerprint"] = coverage_input_fingerprint(report)
    return report


def coverage_input_fingerprint(report: dict[str, Any]) -> str:
    payload = {
        "schema_version": report.get("schema_version"),
        "competition_id": report.get("competition_id"),
        "season": report.get("season"),
        "membership": report.get("membership"),
        "operational_events": report.get("operational_events") or [],
        "matches": sorted(
            report.get("matches") or [],
            key=lambda row: str(row.get("match_id") or ""),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_match_id(match: dict[str, Any]) -> str:
    explicit = str(
        match.get("source_event_id") or match.get("match_id") or ""
    ).strip()
    return explicit or "|".join(
        str(match.get(key) or "").strip()
        for key in ("kickoff_at_utc", "home_team", "away_team")
    )


def closing_archive_candidates(
    *,
    snapshot: dict[str, Any],
    archived_snapshots: list[dict[str, Any]],
    due_match_ids: set[str],
) -> list[dict[str, Any]]:
    candidates = []
    for match in snapshot.get("matches") or []:
        if not isinstance(match, dict) or _snapshot_match_id(match) not in due_match_ids:
            continue
        kickoff = str(match.get("kickoff_at_utc") or "")
        home = str(match.get("home_canonical") or "")
        away = str(match.get("away_canonical") or "")
        competition = str(
            ((match.get("competition") or {}).get("id"))
            or ((snapshot.get("competition") or {}).get("id"))
            or ""
        )
        if not kickoff or not home or not away or not competition:
            candidates.append(
                {
                    "match_id": _snapshot_match_id(match),
                    "kickoff_at_utc": kickoff or None,
                    "home_canonical": home or None,
                    "away_canonical": away or None,
                    "issue_code": "closing_identity_incomplete",
                }
            )
            continue
        observed = select_observed_closing_exact(
            archived_snapshots,
            competition_id=competition,
            kickoff_at_utc=kickoff,
            home_canonical=home,
            away_canonical=away,
        )
        if observed is None:
            candidates.append(
                {
                    "match_id": _snapshot_match_id(match),
                    "kickoff_at_utc": _utc_iso(kickoff),
                    "home_canonical": home,
                    "away_canonical": away,
                    "issue_code": "closing_archive_missing",
                }
            )
    return sorted(
        candidates,
        key=lambda row: (str(row["kickoff_at_utc"]), row["match_id"]),
    )
