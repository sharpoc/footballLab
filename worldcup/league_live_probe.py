from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.competitions import get_competition
from worldcup.league_acceptance import evaluate_league_acceptance
from worldcup.league_result_evidence import verify_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


_SENSITIVE_KEYS = {"apikey", "api_key", "authorization", "cookie", "set-cookie", "url", "headers"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in value.items()
            if str(key).casefold() not in _SENSITIVE_KEYS
        }
    return value


def _remaining(headers: Any) -> int | None:
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("x-requests-remaining") or headers.get("X-Requests-Remaining")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate_league_probe_bundle(
    bundle: Mapping[str, Any],
    *,
    identity_registry: LeagueTeamIdentityRegistry,
    result_contract_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    competition_id = str(bundle.get("competition_id") or "")
    profile = get_competition(competition_id)
    sport_key = str(bundle.get("sport_key") or "")
    odds = bundle.get("odds") if isinstance(bundle.get("odds"), list) else []
    sport_ok = sport_key == profile.theoddsapi_sport_key
    odds_ok = bool(odds)
    unmatched: list[str] = []
    identities: list[dict[str, Any]] = []
    for event in odds:
        if not isinstance(event, Mapping) or event.get("sport_key") != sport_key or not str(event.get("id") or ""):
            odds_ok = False
            continue
        identity = identity_registry.resolve_fixture(
            competition_id,
            str(event.get("home_team") or ""),
            str(event.get("away_team") or ""),
        )
        identities.append(identity)
        if identity["status"] != "verified":
            unmatched.extend(
                name for name, canonical in (
                    (str(event.get("home_team") or ""), identity["home_canonical"]),
                    (str(event.get("away_team") or ""), identity["away_canonical"]),
                ) if canonical is None
            )
        complete_h2h = False
        for bookmaker in event.get("bookmakers") or []:
            for market in bookmaker.get("markets") or []:
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes") or []
                prices = [row.get("price") for row in outcomes if isinstance(row, Mapping)]
                if len(prices) == 3 and all(isinstance(price, (int, float)) and not isinstance(price, bool) and price > 1 for price in prices):
                    complete_h2h = True
        odds_ok = odds_ok and complete_h2h
    result_ok = verify_result_contract_evidence(result_contract_evidence, competition_id)
    evidence = {
        "competition_id": competition_id,
        "sport_catalog": {"verified": sport_ok, "fingerprint": _fingerprint([competition_id, sport_key]) if sport_ok else ""},
        "odds_sample": {"verified": odds_ok, "fingerprint": _fingerprint(odds) if odds_ok else ""},
        "team_identity": {
            "verified": not unmatched and len(identities) == len(odds) and bool(odds),
            "fingerprint": _fingerprint(identities) if not unmatched and identities else "",
            "unmatched_count": len(set(unmatched)),
        },
        "result_contract": {
            "verified": result_ok,
            "fingerprint": str((result_contract_evidence or {}).get("fingerprint") or "") if result_ok else "",
        },
    }
    return evaluate_league_acceptance(competition_id, evidence)


def run_league_live_probe(
    *,
    root: str | Path,
    plan: Mapping[str, Any],
    live: bool = False,
    write: bool = False,
    env_loader: Callable[[], Mapping[str, str]] | None = None,
    payload_fetcher: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    requests = plan.get("requests") if isinstance(plan.get("requests"), list) else []
    estimated = int(plan.get("estimated_credits") or 0)
    if not live:
        return {"status": "dry_run", "request_count": len(requests), "estimated_credits": estimated}
    if not write:
        return {"status": "blocked", "reason": "probe_write_not_confirmed"}
    if env_loader is None or payload_fetcher is None:
        return {"status": "blocked", "reason": "probe_dependencies_missing"}
    env = env_loader()
    if not isinstance(env, Mapping) or not any(str(key).startswith("THE_ODDS_API_KEY_") for key in env):
        return {"status": "blocked", "reason": "probe_key_unavailable"}

    stored: list[str] = []
    failed: dict[str, str] = {}
    latest_remaining: int | None = None
    for request in requests:
        competition_id = str(request.get("competition_id") or "")
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            failed[competition_id or "unknown"] = "competition_not_allowed"
            continue
        try:
            response = payload_fetcher(request)
            headers = response.get("headers") if isinstance(response, Mapping) else None
            latest_remaining = _remaining(headers)
            bundle = {
                "schema_version": 1,
                "competition_id": competition_id,
                "sport_key": str(request.get("sport_key") or ""),
                "anchor": str(request.get("anchor") or ""),
                "markets": list(request.get("markets") or []),
                "odds": _sanitize(response.get("odds") or []),
                "scores": _sanitize(response.get("scores") or []),
                "quota": {"remaining": latest_remaining},
            }
            path = Path(root) / "data/probe/leagues" / competition_id / "probe.json"
            _write_atomic(path, bundle)
            stored.append(competition_id)
        except (OSError, TypeError, ValueError) as exc:
            failed[competition_id] = type(exc).__name__
        if latest_remaining is not None and latest_remaining <= 0:
            break
    status = "stored" if stored and not failed else "partial" if stored else "error"
    return {
        "status": status,
        "stored_competitions": stored,
        "failed_competitions": failed,
        "quota_remaining": latest_remaining,
    }
