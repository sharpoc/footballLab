from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from worldcup.competitions import get_competition


_THEODDSAPI_SCHEMA = "theoddsapi_scores_v1"
_FOTMOB_SCHEMA = "fotmob_league_results_v1"
_SCHEMAS = frozenset({_THEODDSAPI_SCHEMA, _FOTMOB_SCHEMA})
_SCOPE = "football_90min"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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
    provider: str | None = None,
) -> dict[str, Any]:
    profile = get_competition(competition_id)
    core = {
        "competition_id": competition_id,
        "sport_key": str(sport_key),
        "provider_schema": str(provider_schema),
        "score_scope": str(score_scope),
        "source_reference": str(source_reference),
    }
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        core["provider"] = str(provider or "")
    verified = (
        profile.theoddsapi_sport_key == core["sport_key"]
        and core["provider_schema"] in _SCHEMAS
        and core["score_scope"] == _SCOPE
        and _source_reference_is_valid(core)
    )
    return {**core, "verified": verified, "fingerprint": _fingerprint(core)}


def _source_reference_is_valid(core: Mapping[str, str]) -> bool:
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        return core.get("provider") == "fotmob" and _SHA256.fullmatch(core["source_reference"]) is not None
    return bool(core["source_reference"].strip())


def verify_result_contract_evidence(
    evidence: Mapping[str, Any] | None,
    competition_id: str,
    *,
    provider_schema: str | None = None,
) -> bool:
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
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        core["provider"] = str(evidence.get("provider") or "")
    return (
        evidence.get("verified") is True
        and core["competition_id"] == competition_id
        and core["sport_key"] == profile.theoddsapi_sport_key
        and core["provider_schema"] in _SCHEMAS
        and (provider_schema is None or core["provider_schema"] == provider_schema)
        and core["score_scope"] == _SCOPE
        and _source_reference_is_valid(core)
        and str(evidence.get("fingerprint") or "") == _fingerprint(core)
    )
