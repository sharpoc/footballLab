from __future__ import annotations

from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.decision_settlement import settle_match_decision, summarize_decision_records


FORMAL_SCOPE = "observed_schema_v2_match_pick_only"


def _observed_decision(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema_version") == 2
        and value.get("label") in {"MATCH_PICK", "NO_CLEAN_MARKET"}
    )


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(key) or "") for key in (
        "kickoff_at_utc", "home_canonical", "away_canonical",
    ))


def _record_matches(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> bool:
    return (
        _identity(existing) == _identity(incoming)
        and existing.get("result") == incoming.get("result")
        and existing.get("closing_match_decision") == incoming.get("closing_match_decision")
    )


def _payload(competition_id: str, records: Mapping[str, dict[str, Any]], missing: set[str]) -> dict[str, Any]:
    ordered_records = [records[event_id] for event_id in sorted(records)]
    missing_ids = sorted(missing.difference(records))
    summary = summarize_decision_records(ordered_records, skipped_no_closing=len(missing_ids))
    return {
        "schema_version": 2,
        "competition_id": competition_id,
        "statistics_scope": FORMAL_SCOPE,
        "matches": ordered_records,
        "decision_tally": summary["decision_tally"],
        "decision_sample": summary["sample"],
        "decision_coverage": summary["coverage"],
        "skipped_no_closing": len(missing_ids),
        "missing_closing_event_ids": missing_ids,
    }


def build_league_postmatch(
    closing_payload: dict[str, Any],
    result_payload: dict[str, Any],
    competition_id: str,
) -> dict[str, Any]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("postmatch_competition_not_allowed")
    if closing_payload.get("competition_id") != competition_id or result_payload.get("competition_id") != competition_id:
        raise ValueError("postmatch_competition_mismatch")
    closings = closing_payload.get("closings") or {}
    if not isinstance(closings, Mapping):
        raise ValueError("postmatch_closings_invalid")
    results = result_payload.get("results") or []
    if not isinstance(results, list):
        raise ValueError("postmatch_results_invalid")
    records: dict[str, dict[str, Any]] = {}
    missing_closing: set[str] = set()
    for result in results:
        if not isinstance(result, Mapping):
            raise ValueError("postmatch_results_invalid")
        if result.get("result_scope") != "football_90min":
            continue
        event_id = str(result.get("source_event_id") or "")
        if not event_id:
            raise ValueError("postmatch_event_id_missing")
        if event_id in records or event_id in missing_closing:
            raise ValueError(f"postmatch_duplicate_result: {event_id}")
        closing = closings.get(event_id)
        if not isinstance(closing, Mapping) or not _observed_decision(closing.get("closing_match_decision")):
            missing_closing.add(event_id)
            continue
        identity_fields = ("competition_id", "source_event_id", "kickoff_at_utc", "home_canonical", "away_canonical")
        if any(str(closing.get(key)) != str(result.get(key)) for key in identity_fields):
            raise ValueError(f"postmatch_identity_mismatch: {event_id}")
        score = {"home_score": result["home_score"], "away_score": result["away_score"]}
        decision = closing.get("closing_match_decision")
        records[event_id] = {
            **closing,
            "competition": {"id": competition_id},
            "result": score,
            "closing_match_decision_result": settle_match_decision(decision, score),
        }
    return _payload(competition_id, records, missing_closing)


def _existing_records(existing: Mapping[str, Any], competition_id: str) -> tuple[dict[str, dict[str, Any]], set[str]]:
    if (
        existing.get("schema_version") != 2
        or existing.get("competition_id") != competition_id
        or existing.get("statistics_scope") != FORMAL_SCOPE
    ):
        raise ValueError("postmatch_existing_invalid")
    values = existing.get("matches")
    if not isinstance(values, list):
        raise ValueError("postmatch_existing_invalid")
    records: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("postmatch_existing_invalid")
        event_id = str(value.get("source_event_id") or "").strip()
        if not event_id or event_id in records or not _observed_decision(value.get("closing_match_decision")):
            raise ValueError("postmatch_existing_invalid")
        records[event_id] = dict(value)
    missing_values = existing.get("missing_closing_event_ids")
    if missing_values is None and int(existing.get("skipped_no_closing") or 0) == 0:
        missing_values = []
    if not isinstance(missing_values, list):
        raise ValueError("postmatch_existing_invalid")
    missing = {str(event_id).strip() for event_id in missing_values}
    if not all(missing) or len(missing) != len(missing_values) or missing.intersection(records):
        raise ValueError("postmatch_existing_invalid")
    if int(existing.get("skipped_no_closing") or 0) != len(missing):
        raise ValueError("postmatch_existing_invalid")
    return records, missing


def merge_league_postmatch(
    existing: dict[str, Any] | None,
    closing_payload: dict[str, Any],
    result_payload: dict[str, Any],
    competition_id: str,
) -> dict[str, Any]:
    """Append immutable formal settlements without inventing a missing closing."""
    if existing is None:
        records: dict[str, dict[str, Any]] = {}
        missing: set[str] = set()
    else:
        records, missing = _existing_records(existing, competition_id)
    incoming = build_league_postmatch(closing_payload, result_payload, competition_id)
    for record in incoming["matches"]:
        event_id = record["source_event_id"]
        prior = records.get(event_id)
        if prior is not None:
            if not _record_matches(prior, record):
                raise ValueError(f"postmatch_result_conflict: {event_id}")
            continue
        records[event_id] = record
        missing.discard(event_id)
    for event_id in incoming["missing_closing_event_ids"]:
        if event_id not in records:
            missing.add(event_id)
    return _payload(competition_id, records, missing)
