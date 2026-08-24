from __future__ import annotations

from typing import Any

from worldcup.decision_settlement import settle_match_decision, summarize_decision_records


def build_league_postmatch(
    closing_payload: dict[str, Any],
    result_payload: dict[str, Any],
    competition_id: str,
) -> dict[str, Any]:
    if closing_payload.get("competition_id") != competition_id or result_payload.get("competition_id") != competition_id:
        raise ValueError("postmatch_competition_mismatch")
    closings = closing_payload.get("closings") or {}
    records: list[dict[str, Any]] = []
    missing_closing = 0
    for result in result_payload.get("results") or []:
        if result.get("result_scope") != "football_90min":
            continue
        event_id = str(result.get("source_event_id") or "")
        closing = closings.get(event_id)
        if not isinstance(closing, dict):
            missing_closing += 1
            continue
        identity_fields = ("competition_id", "source_event_id", "kickoff_at_utc", "home_canonical", "away_canonical")
        if any(str(closing.get(key)) != str(result.get(key)) for key in identity_fields):
            raise ValueError(f"postmatch_identity_mismatch: {event_id}")
        score = {"home_score": result["home_score"], "away_score": result["away_score"]}
        decision = closing.get("closing_match_decision")
        records.append({
            **closing,
            "competition": {"id": competition_id},
            "result": score,
            "closing_match_decision_result": settle_match_decision(decision, score),
        })
    summary = summarize_decision_records(records, skipped_no_closing=missing_closing)
    return {
        "schema_version": 2,
        "competition_id": competition_id,
        "statistics_scope": "observed_schema_v2_match_pick_only",
        "matches": records,
        "decision_tally": summary["decision_tally"],
        "decision_sample": summary["sample"],
        "decision_coverage": summary["coverage"],
        "skipped_no_closing": missing_closing,
    }
