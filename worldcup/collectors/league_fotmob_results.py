from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_result_evidence import verify_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


_KICKOFF_TOLERANCE = timedelta(minutes=5)
_FOTMOB_COMPETITION_IDS = {
    "serie_a_2026_27": "55",
    "serie_a_brazil_2026": "268",
    "laliga_2026_27": "87",
    "epl_2026_27": "47",
    "bundesliga_2026_27": "54",
    "ligue_1_2026_27": "53",
}
_SCORE = re.compile(r"(0|[1-9][0-9]*)\s*-\s*(0|[1-9][0-9]*)\Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _provider_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _team_name(value: Any) -> str:
    name = _mapping(value).get("name")
    return name.strip() if isinstance(name, str) else ""


def _utc(value: Any, *, error: str) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError(error)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(error) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(error)
    return parsed.astimezone(timezone.utc)


def _safe_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _calendar_rows(payload: Mapping[str, Any]) -> list[tuple[str | None, Mapping[str, Any]]]:
    rows: list[tuple[str | None, Mapping[str, Any]]] = []
    for league in _mapping(payload).get("leagues") or []:
        league_block = _mapping(league)
        league_id = _provider_id(league_block.get("id"))
        for match in league_block.get("matches") or []:
            if isinstance(match, Mapping):
                rows.append((league_id, match))
    return rows


def _event_id(match: Mapping[str, Any]) -> str | None:
    return _provider_id(match.get("id"))


def _pending_row(source_event_id: str, reason: str) -> dict[str, str]:
    return {"source_event_id": source_event_id, "reason": reason}


def _source_event(source_event_id: str, outcome: str, reason: str | None = None) -> dict[str, str]:
    row = {"source_event_id": source_event_id, "outcome": outcome}
    if reason is not None:
        row["reason"] = reason
    return row


def _payload(
    competition_id: str,
    results: list[dict[str, Any]],
    pending: list[dict[str, str]],
) -> dict[str, Any]:
    results.sort(key=lambda row: row["source_event_id"])
    pending.sort(key=lambda row: (row["source_event_id"], row["reason"]))
    source_events = [
        _source_event(row["source_event_id"], "accepted") for row in results
    ] + [
        _source_event(row["source_event_id"], "pending", row["reason"]) for row in pending
    ]
    source_events.sort(key=lambda row: (row["source_event_id"], row["outcome"], row.get("reason", "")))
    return {
        "competition_id": competition_id,
        "results": results,
        "pending": pending,
        "source_events": source_events,
        "source_fingerprint": _safe_fingerprint({
            "competition_id": competition_id,
            "result_fingerprints": [row["source_fingerprint"] for row in results],
            "pending": pending,
        }),
    }


def _pending_all(calendar_payload: Mapping[str, Any], competition_id: str, reason: str) -> dict[str, Any]:
    event_ids = {_event_id(match) for _league_id, match in _calendar_rows(calendar_payload)}
    pending = [_pending_row(event_id, reason) for event_id in event_ids if event_id is not None]
    return _payload(competition_id, [], pending)


def _has_terminal_ft(status: Mapping[str, Any]) -> bool:
    return status.get("finished") is True and _mapping(status.get("reason")).get("short") == "FT"


def _empty_or_null_scalar(value: Any) -> bool:
    return value is None or value == ""


def _has_detail_90min_proof(status: Mapping[str, Any]) -> bool:
    halfs = status.get("halfs")
    if not isinstance(halfs, Mapping):
        return False
    return (
        _has_terminal_ft(status)
        and "firstExtraHalfStarted" in halfs and halfs.get("firstExtraHalfStarted") == ""
        and "secondExtraHalfStarted" in halfs and halfs.get("secondExtraHalfStarted") == ""
        and "whoLostOnPenalties" in status and status.get("whoLostOnPenalties") is None
        and "whoLostOnAggregated" in status
        and _empty_or_null_scalar(status.get("whoLostOnAggregated"))
    )


def _finished_90min(status: Mapping[str, Any]) -> tuple[int, int] | None:
    if not _has_terminal_ft(status):
        return None
    score = status.get("scoreStr")
    if not isinstance(score, str):
        return None
    matched = _SCORE.fullmatch(score.strip())
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


def _pending_reason_for_status(calendar: Mapping[str, Any], details: Mapping[str, Any]) -> str | None:
    calendar_status = _mapping(calendar.get("status"))
    details_status = _mapping(_mapping(details.get("header")).get("status"))
    if calendar_status.get("finished") is not True or details_status.get("finished") is not True:
        return "result_not_finished"
    if not _has_terminal_ft(calendar_status) or not _has_detail_90min_proof(details_status):
        return "result_90min_score_unverified"
    if _finished_90min(calendar_status) is None or _finished_90min(details_status) is None:
        return "invalid_90min_score"
    if _finished_90min(calendar_status) != _finished_90min(details_status):
        return "result_score_mismatch"
    return None


def _expected_rows(
    calendar_payload: Mapping[str, Any],
    detail_payloads: Mapping[str, Mapping[str, Any]],
    competition_id: str,
) -> list[Mapping[str, Any]]:
    expected_league_id = _FOTMOB_COMPETITION_IDS[competition_id]
    target_containers = [
        league for league in _mapping(calendar_payload).get("leagues") or []
        if _provider_id(_mapping(league).get("id")) == expected_league_id
    ]
    if len(target_containers) > 1:
        raise ValueError("fotmob_result_competition_container_duplicate")
    selected: list[Mapping[str, Any]] = []
    for league_id, match in _calendar_rows(calendar_payload):
        source_event_id = _event_id(match)
        if league_id == expected_league_id:
            selected.append(match)
        elif source_event_id is not None and source_event_id in detail_payloads:
            raise ValueError("fotmob_result_competition_mismatch")
    return selected


def _parse_verified_rows(
    calendar_payload: dict[str, Any],
    detail_payloads: Mapping[str, dict[str, Any]],
    competition_id: str,
    *,
    identity_registry: LeagueTeamIdentityRegistry,
    captured_at: datetime,
) -> dict[str, Any]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("fotmob_result_competition_not_allowed")
    captured = _utc(captured_at, error="fotmob_result_captured_at_must_be_timezone_aware")
    details = {
        str(key): value for key, value in detail_payloads.items()
        if isinstance(key, (str, int)) and not isinstance(key, bool) and isinstance(value, Mapping)
    }
    selected = _expected_rows(calendar_payload, details, competition_id)
    source_counts = Counter(event_id for match in selected if (event_id := _event_id(match)) is not None)
    results: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []

    for calendar in selected:
        source_event_id = _event_id(calendar)
        if source_event_id is None:
            continue
        if source_counts[source_event_id] != 1:
            if not any(row["source_event_id"] == source_event_id for row in pending):
                pending.append(_pending_row(source_event_id, "duplicate_source_event"))
            continue
        details_row = details.get(source_event_id)
        if details_row is None:
            pending.append(_pending_row(source_event_id, "details_missing"))
            continue
        general = _mapping(details_row.get("general"))
        if _provider_id(general.get("matchId")) != source_event_id:
            pending.append(_pending_row(source_event_id, "details_event_mismatch"))
            continue
        if _provider_id(general.get("leagueId")) != _FOTMOB_COMPETITION_IDS[competition_id]:
            raise ValueError("fotmob_result_competition_mismatch")
        status_reason = _pending_reason_for_status(calendar, details_row)
        if status_reason is not None:
            pending.append(_pending_row(source_event_id, status_reason))
            continue
        calendar_home, calendar_away = _team_name(calendar.get("home")), _team_name(calendar.get("away"))
        detail_home, detail_away = _team_name(general.get("homeTeam")), _team_name(general.get("awayTeam"))
        calendar_identity = identity_registry.resolve_fixture(competition_id, calendar_home, calendar_away)
        detail_identity = identity_registry.resolve_fixture(competition_id, detail_home, detail_away)
        if calendar_identity["status"] != "verified" or detail_identity["status"] != "verified":
            pending.append(_pending_row(source_event_id, "unmatched_team"))
            continue
        if (
            calendar_identity["home_canonical"] != detail_identity["home_canonical"]
            or calendar_identity["away_canonical"] != detail_identity["away_canonical"]
        ):
            pending.append(_pending_row(source_event_id, "home_away_mismatch"))
            continue
        try:
            calendar_kickoff = _utc(
                _mapping(calendar.get("status")).get("utcTime"),
                error="fotmob_result_kickoff_invalid",
            )
            detail_kickoff = _utc(
                general.get("matchTimeUTCDate"), error="fotmob_result_kickoff_invalid"
            )
        except ValueError:
            pending.append(_pending_row(source_event_id, "kickoff_invalid"))
            continue
        if abs(calendar_kickoff - detail_kickoff) > _KICKOFF_TOLERANCE:
            pending.append(_pending_row(source_event_id, "kickoff_mismatch"))
            continue
        score = _finished_90min(_mapping(_mapping(details_row.get("header")).get("status")))
        if score is None:
            pending.append(_pending_row(source_event_id, "result_90min_score_unverified"))
            continue
        core = {
            "competition_id": competition_id,
            "source_event_id": source_event_id,
            "kickoff_at_utc": calendar_kickoff.isoformat(),
            "home_team": calendar_home,
            "away_team": calendar_away,
            "home_canonical": calendar_identity["home_canonical"],
            "away_canonical": calendar_identity["away_canonical"],
            "home_score": score[0],
            "away_score": score[1],
            "captured_at": captured.isoformat(),
            "result_scope": "football_90min",
        }
        results.append({**core, "source_fingerprint": _safe_fingerprint(core)})
    return _payload(competition_id, results, pending)


def parse_fotmob_league_results(
    calendar_payload: dict[str, Any],
    detail_payloads: Mapping[str, dict[str, Any]],
    competition_id: str,
    *,
    result_contract_evidence: Mapping[str, Any] | None,
    identity_registry: LeagueTeamIdentityRegistry,
    captured_at: datetime,
) -> dict[str, Any]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("fotmob_result_competition_not_allowed")
    if not verify_result_contract_evidence(
        result_contract_evidence,
        competition_id,
        provider_schema="fotmob_league_results_v1",
    ):
        return _pending_all(calendar_payload, competition_id, "result_90min_semantics_unverified")
    return _parse_verified_rows(
        calendar_payload,
        detail_payloads,
        competition_id,
        identity_registry=identity_registry,
        captured_at=captured_at,
    )
