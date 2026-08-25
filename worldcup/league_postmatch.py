from __future__ import annotations

from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.decision_settlement import settle_match_decision, summarize_decision_records
from worldcup.league_closing import _valid_existing_closings
from worldcup.league_result_store import _receipt as _committed_receipt
from worldcup.league_result_store import _row as _committed_row


FORMAL_SCOPE = "observed_schema_v2_match_pick_only"


def _validated_receipt(payload: Any, competition_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != 1
        or payload.get("competition_id") != competition_id
        or not isinstance(payload.get("results"), list)
    ):
        raise ValueError("postmatch_results_invalid")
    rows: dict[str, dict[str, Any]] = {}
    try:
        for value in payload["results"]:
            if not isinstance(value, Mapping):
                raise ValueError
            row = _committed_row(value, competition_id)
            if row["source_event_id"] in rows:
                raise ValueError
            rows[row["source_event_id"]] = row
    except (TypeError, ValueError):
        raise ValueError("postmatch_results_invalid") from None
    checked = _committed_receipt(competition_id, rows)
    if payload.get("fingerprint") != checked["fingerprint"]:
        raise ValueError("postmatch_results_invalid")
    return checked, rows


def _validated_closings(payload: Any, competition_id: str) -> dict[str, dict[str, Any]]:
    try:
        return _valid_existing_closings(payload, competition_id)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("postmatch_closings_invalid") from None


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(key) or "") for key in (
        "kickoff_at_utc", "home_canonical", "away_canonical",
    ))


def _scores(row: Mapping[str, Any]) -> dict[str, int]:
    return {"home_score": row["home_score"], "away_score": row["away_score"]}


def _missing_entry(row: Mapping[str, Any], receipt_fingerprint: str) -> dict[str, Any]:
    return {**dict(row), "accepted_result_receipt_fingerprint": receipt_fingerprint}


def _record(closing: Mapping[str, Any], result: Mapping[str, Any], receipt_fingerprint: str, competition_id: str) -> dict[str, Any]:
    score = _scores(result)
    decision = closing["closing_match_decision"]
    return {
        **dict(closing),
        "competition": {"id": competition_id},
        "accepted_result": dict(result),
        "accepted_result_receipt_fingerprint": receipt_fingerprint,
        "result": score,
        "closing_match_decision_result": settle_match_decision(decision, score),
    }


def _payload(
    competition_id: str,
    records: Mapping[str, dict[str, Any]],
    missing: Mapping[str, dict[str, Any]],
    receipts: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    ordered_records = [records[event_id] for event_id in sorted(records)]
    missing_rows = {event_id: dict(missing[event_id]) for event_id in sorted(set(missing).difference(records))}
    summary = summarize_decision_records(ordered_records, skipped_no_closing=len(missing_rows))
    return {
        "schema_version": 2,
        "competition_id": competition_id,
        "statistics_scope": FORMAL_SCOPE,
        "matches": ordered_records,
        "decision_tally": summary["decision_tally"],
        "decision_sample": summary["sample"],
        "decision_coverage": summary["coverage"],
        "skipped_no_closing": len(missing_rows),
        "missing_closing_event_ids": sorted(missing_rows),
        "missing_closing_results": missing_rows,
        "accepted_result_receipts": {fingerprint: dict(receipts[fingerprint]) for fingerprint in sorted(receipts)},
    }


def build_league_postmatch(
    closing_payload: dict[str, Any],
    result_payload: dict[str, Any],
    competition_id: str,
) -> dict[str, Any]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("postmatch_competition_not_allowed")
    closings = _validated_closings(closing_payload, competition_id)
    receipt, results = _validated_receipt(result_payload, competition_id)
    receipt_fingerprint = receipt["fingerprint"]
    records: dict[str, dict[str, Any]] = {}
    missing: dict[str, dict[str, Any]] = {}
    for event_id, result in results.items():
        closing = closings.get(event_id)
        if closing is None:
            missing[event_id] = _missing_entry(result, receipt_fingerprint)
            continue
        if closing["source_event_id"] != event_id or _identity(closing) != _identity(result):
            raise ValueError(f"postmatch_identity_mismatch: {event_id}")
        records[event_id] = _record(closing, result, receipt_fingerprint, competition_id)
    return _payload(competition_id, records, missing, {receipt_fingerprint: receipt})


def _receipt_row(
    row: Any,
    receipt_fingerprint: Any,
    receipts: Mapping[str, dict[str, Any]],
    competition_id: str,
) -> dict[str, Any]:
    if not isinstance(receipt_fingerprint, str) or receipt_fingerprint not in receipts:
        raise ValueError("postmatch_existing_invalid")
    try:
        checked = _committed_row(row, competition_id)
    except (TypeError, ValueError):
        raise ValueError("postmatch_existing_invalid") from None
    receipt_rows = {value["source_event_id"]: value for value in receipts[receipt_fingerprint]["results"]}
    if receipt_rows.get(checked["source_event_id"]) != checked:
        raise ValueError("postmatch_existing_invalid")
    return checked


def _existing_records(
    existing: Mapping[str, Any], competition_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if (
        existing.get("schema_version") != 2
        or existing.get("competition_id") != competition_id
        or existing.get("statistics_scope") != FORMAL_SCOPE
        or not isinstance(existing.get("matches"), list)
    ):
        raise ValueError("postmatch_existing_invalid")
    receipt_values = existing.get("accepted_result_receipts")
    if receipt_values is None and not existing["matches"] and not existing.get("missing_closing_event_ids"):
        receipt_values = {}
    if not isinstance(receipt_values, Mapping):
        raise ValueError("postmatch_existing_invalid")
    receipts: dict[str, dict[str, Any]] = {}
    for fingerprint, value in receipt_values.items():
        receipt, _ = _validated_receipt(value, competition_id)
        if not isinstance(fingerprint, str) or fingerprint != receipt["fingerprint"]:
            raise ValueError("postmatch_existing_invalid")
        receipts[fingerprint] = receipt
    records: dict[str, dict[str, Any]] = {}
    for value in existing["matches"]:
        if not isinstance(value, Mapping):
            raise ValueError("postmatch_existing_invalid")
        event_id = value.get("source_event_id")
        if not isinstance(event_id, str) or not event_id or event_id in records or value.get("competition_id") != competition_id:
            raise ValueError("postmatch_existing_invalid")
        if not isinstance(value.get("competition"), Mapping) or value["competition"].get("id") != competition_id:
            raise ValueError("postmatch_existing_invalid")
        closing = _validated_closings({
            "schema_version": 1, "competition_id": competition_id, "closings": {event_id: dict(value)},
        }, competition_id)[event_id]
        result = _receipt_row(
            value.get("accepted_result"), value.get("accepted_result_receipt_fingerprint"), receipts, competition_id,
        )
        if _identity(closing) != _identity(result) or value.get("result") != _scores(result):
            raise ValueError("postmatch_existing_invalid")
        expected = settle_match_decision(closing["closing_match_decision"], _scores(result))
        if value.get("closing_match_decision_result") != expected:
            raise ValueError("postmatch_existing_invalid")
        records[event_id] = dict(value)
    missing_ids = existing.get("missing_closing_event_ids")
    missing_values = existing.get("missing_closing_results")
    if missing_ids is None and int(existing.get("skipped_no_closing") or 0) == 0:
        missing_ids, missing_values = [], {}
    if not isinstance(missing_ids, list) or not isinstance(missing_values, Mapping):
        raise ValueError("postmatch_existing_invalid")
    if sorted(missing_ids) != sorted(missing_values) or len(set(missing_ids)) != len(missing_ids):
        raise ValueError("postmatch_existing_invalid")
    missing: dict[str, dict[str, Any]] = {}
    for event_id in missing_ids:
        if not isinstance(event_id, str) or not event_id or event_id in records:
            raise ValueError("postmatch_existing_invalid")
        value = missing_values[event_id]
        if not isinstance(value, Mapping) or value.get("source_event_id") != event_id:
            raise ValueError("postmatch_existing_invalid")
        _receipt_row(value, value.get("accepted_result_receipt_fingerprint"), receipts, competition_id)
        missing[event_id] = dict(value)
    if int(existing.get("skipped_no_closing") or 0) != len(missing):
        raise ValueError("postmatch_existing_invalid")
    return records, missing, receipts


def _canonical_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = value.get("accepted_result")
    row = dict(candidate) if isinstance(candidate, Mapping) else dict(value)
    row.pop("accepted_result_receipt_fingerprint", None)
    return row


def _same_evidence(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return _canonical_evidence(first) == _canonical_evidence(second)


def _same_record(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    return (
        _same_evidence(first, second)
        and first.get("competition_id") == second.get("competition_id")
        and first.get("source_event_id") == second.get("source_event_id")
        and _identity(first) == _identity(second)
        and first.get("closing_snapshot_at") == second.get("closing_snapshot_at")
        and first.get("closing_match_decision") == second.get("closing_match_decision")
        and first.get("result") == second.get("result")
        and first.get("closing_match_decision_result") == second.get("closing_match_decision_result")
    )


def merge_league_postmatch(
    existing: dict[str, Any] | None,
    closing_payload: dict[str, Any],
    result_payload: dict[str, Any],
    competition_id: str,
) -> dict[str, Any]:
    """Append immutable verified formal settlements without inventing a missing closing."""
    if existing is None:
        records: dict[str, dict[str, Any]] = {}
        missing: dict[str, dict[str, Any]] = {}
        receipts: dict[str, dict[str, Any]] = {}
    else:
        records, missing, receipts = _existing_records(existing, competition_id)
    incoming = build_league_postmatch(closing_payload, result_payload, competition_id)
    incoming_records, incoming_missing, incoming_receipts = _existing_records(incoming, competition_id)
    for fingerprint, receipt in incoming_receipts.items():
        if fingerprint in receipts and receipts[fingerprint] != receipt:
            raise ValueError("postmatch_existing_invalid")
        receipts[fingerprint] = receipt
    for event_id, record in incoming_records.items():
        prior = records.get(event_id)
        prior_missing = missing.get(event_id)
        if prior is not None:
            if not _same_record(prior, record):
                raise ValueError(f"postmatch_result_conflict: {event_id}")
            continue
        if prior_missing is not None and not _same_evidence(prior_missing, record):
            raise ValueError(f"postmatch_result_conflict: {event_id}")
        records[event_id] = record
        missing.pop(event_id, None)
    for event_id, row in incoming_missing.items():
        if event_id in records:
            if not _same_evidence(records[event_id], row):
                raise ValueError(f"postmatch_result_conflict: {event_id}")
            continue
        prior = missing.get(event_id)
        if prior is not None and not _same_evidence(prior, row):
            raise ValueError(f"postmatch_result_conflict: {event_id}")
        missing[event_id] = row
    return _payload(competition_id, records, missing, receipts)
