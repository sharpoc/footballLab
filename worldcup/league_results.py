from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from worldcup.competitions import get_competition
from worldcup.league_result_evidence import verify_result_contract_evidence
from worldcup.league_result_store import _receipt as _committed_receipt
from worldcup.league_result_store import _row as _committed_row
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


THEODDSAPI_RESULT_SCHEMA = "theoddsapi_scores_v1"


def _utc(value: Any) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("result_timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc).isoformat()


def parse_verified_league_results(
    raw: list[dict[str, Any]],
    competition_id: str,
    *,
    result_contract_evidence: dict[str, Any] | None = None,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
) -> dict[str, Any]:
    profile = get_competition(competition_id)
    results: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in raw:
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in seen:
            raise ValueError(f"result_event_identity_invalid: {event_id}")
        seen.add(event_id)
        if event.get("sport_key") != profile.theoddsapi_sport_key or event.get("completed") is not True:
            continue
        if not verify_result_contract_evidence(
            result_contract_evidence,
            competition_id,
            provider_schema=THEODDSAPI_RESULT_SCHEMA,
        ):
            pending.append({"source_event_id": event_id, "reason": "result_90min_semantics_unverified"})
            continue
        if identity_registry is None:
            pending.append({"source_event_id": event_id, "reason": "strict_team_identity_unavailable"})
            continue
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        identity = identity_registry.resolve_fixture(competition_id, home, away)
        if identity["status"] != "verified":
            pending.append({"source_event_id": event_id, "reason": str(identity["reason"] or "unmatched_team")})
            continue
        scores = {
            str(row.get("name") or "").strip(): row.get("score")
            for row in event.get("scores") or []
            if isinstance(row, dict)
        }
        home_raw, away_raw = scores.get(home), scores.get(away)
        if not home or not away or isinstance(home_raw, bool) or isinstance(away_raw, bool):
            continue
        if not str(home_raw).isdigit() or not str(away_raw).isdigit():
            continue
        results.append({
            "competition_id": competition_id,
            "source_event_id": event_id,
            "kickoff_at_utc": _utc(event.get("commence_time")),
            "home_team": home,
            "away_team": away,
            "home_canonical": identity["home_canonical"],
            "away_canonical": identity["away_canonical"],
            "home_score": int(home_raw),
            "away_score": int(away_raw),
            "captured_at": _utc(event.get("last_update") or event.get("commence_time")),
            "result_scope": "football_90min",
        })
    return {"competition_id": competition_id, "results": results, "pending": pending}


def adapt_theoddsapi_results_to_committed_receipt(
    raw: list[dict[str, Any]],
    competition_id: str,
    *,
    result_contract_evidence: dict[str, Any] | None,
    identity_registry: LeagueTeamIdentityRegistry,
) -> dict[str, Any]:
    """Parse legacy The Odds scores and cross the Task 2 receipt boundary explicitly."""
    parsed = parse_verified_league_results(
        raw,
        competition_id,
        result_contract_evidence=result_contract_evidence,
        identity_registry=identity_registry,
    )
    rows: dict[str, dict[str, Any]] = {}
    evidence_fingerprint = str((result_contract_evidence or {}).get("fingerprint") or "")
    for value in parsed["results"]:
        source_core = {
            "provider_schema": THEODDSAPI_RESULT_SCHEMA,
            "evidence_fingerprint": evidence_fingerprint,
            "result": value,
        }
        source_encoded = json.dumps(
            source_core,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        row = _committed_row(
            {
                **value,
                "source_fingerprint": hashlib.sha256(source_encoded.encode("utf-8")).hexdigest(),
            },
            competition_id,
        )
        rows[row["source_event_id"]] = row
    return {
        "provider_schema": THEODDSAPI_RESULT_SCHEMA,
        "receipt": _committed_receipt(competition_id, rows),
        "pending": list(parsed["pending"]),
    }
