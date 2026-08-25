from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from worldcup.competitions import get_competition


_SCHEMAS = frozenset({"theoddsapi_scores_v1", "fotmob_league_results_v1"})
_SCOPE = "football_90min"


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_result_contract_evidence(
    *,
    competition_id: str,
    sport_key: str,
    provider_schema: str,
    score_scope: str,
    source_reference: str,
) -> dict[str, Any]:
    profile = get_competition(competition_id)
    core = {
        "competition_id": competition_id,
        "sport_key": str(sport_key),
        "provider_schema": str(provider_schema),
        "score_scope": str(score_scope),
        "source_reference": str(source_reference),
    }
    verified = (
        profile.theoddsapi_sport_key == core["sport_key"]
        and core["provider_schema"] in _SCHEMAS
        and core["score_scope"] == _SCOPE
        and bool(core["source_reference"].strip())
    )
    return {**core, "verified": verified, "fingerprint": _fingerprint(core)}


def verify_result_contract_evidence(evidence: Mapping[str, Any] | None, competition_id: str) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    try:
        profile = get_competition(competition_id)
    except KeyError:
        return False
    core = {
        "competition_id": str(evidence.get("competition_id") or ""),
        "sport_key": str(evidence.get("sport_key") or ""),
        "provider_schema": str(evidence.get("provider_schema") or ""),
        "score_scope": str(evidence.get("score_scope") or ""),
        "source_reference": str(evidence.get("source_reference") or ""),
    }
    return (
        evidence.get("verified") is True
        and core["competition_id"] == competition_id
        and core["sport_key"] == profile.theoddsapi_sport_key
        and core["provider_schema"] in _SCHEMAS
        and core["score_scope"] == _SCOPE
        and bool(core["source_reference"].strip())
        and str(evidence.get("fingerprint") or "") == _fingerprint(core)
    )
