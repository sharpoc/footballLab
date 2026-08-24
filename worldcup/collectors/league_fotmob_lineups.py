from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


_KICKOFF_TOLERANCE = timedelta(minutes=5)


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("fotmob_lineup_datetime_invalid") from None
    else:
        raise ValueError("fotmob_lineup_datetime_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("fotmob_lineup_datetime_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _team_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    block = _mapping(value)
    name = block.get("name")
    return name.strip() if isinstance(name, str) else ""


def _match_id(match: Mapping[str, Any]) -> str:
    for key in ("id", "matchId"):
        value = match.get(key)
        parsed = _provider_id(value)
        if parsed is not None:
            return parsed
    return ""


def _calendar_kickoff(match: Mapping[str, Any]) -> datetime:
    status = _mapping(match.get("status"))
    value = status.get("utcTime") or match.get("matchTimeUTC")
    return _utc(value)


def _provider_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _calendar_matches(payload: Any) -> list[tuple[str | None, Mapping[str, Any]]]:
    matches: list[tuple[str | None, Mapping[str, Any]]] = []
    for league in _mapping(payload).get("leagues") or []:
        league_block = _mapping(league)
        league_id = _provider_id(league_block.get("id")) or _provider_id(league_block.get("primaryId"))
        for match in league_block.get("matches") or []:
            if isinstance(match, Mapping):
                matches.append((league_id, match))
    return matches


def _fixture_name(fixture: Mapping[str, Any], side: str) -> str:
    for key in (f"{side}_team", f"{side}_team_name"):
        value = fixture.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _local_rows(
    competition_id: str,
    fixtures: Sequence[Mapping[str, Any]],
    registry: LeagueTeamIdentityRegistry,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        event_id = _provider_id(
            fixture.get("event_id")
            or fixture.get("source_event_id")
            or fixture.get("id")
        )
        try:
            kickoff = _utc(fixture.get("kickoff_at_utc") or fixture.get("commence_time"))
        except (TypeError, ValueError):
            continue
        identity = registry.resolve_fixture(
            competition_id,
            _fixture_name(fixture, "home"),
            _fixture_name(fixture, "away"),
        )
        if event_id is not None and identity["status"] == "verified":
            local_competition = fixture.get("competition_id")
            rows.append({
                "event_id": event_id,
                "kickoff": kickoff,
                "home_canonical": identity["home_canonical"],
                "away_canonical": identity["away_canonical"],
                "competition_matches": (
                    local_competition in (None, "") or local_competition == competition_id
                ),
            })
    return rows


def _rejection(competition_id: str, source_match_id: str, reason: str) -> dict[str, str]:
    return {
        "provider": "fotmob",
        "competition_id": competition_id,
        "source_match_id": source_match_id,
        "reason": reason,
    }


def _player_name(player: Mapping[str, Any]) -> str | None:
    name = player.get("name")
    if isinstance(name, Mapping):
        value = name.get("fullName") or name.get("name") or name.get("shortName")
    else:
        value = name
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _players(block: Any) -> list[dict[str, str | None]] | None:
    starters = _mapping(block).get("starters")
    if not isinstance(starters, list) or len(starters) != 11:
        return None
    players: list[dict[str, str | None]] = []
    for raw in starters:
        player = _mapping(raw)
        value = player.get("id") if player.get("id") not in (None, "") else player.get("playerId")
        player_id = _provider_id(value)
        if player_id is None:
            raise ValueError("invalid_player_identity")
        players.append({"player_id": player_id, "name": _player_name(player)})
    if len({player["player_id"] for player in players}) != 11:
        raise ValueError("invalid_player_identity")
    return sorted(players, key=lambda player: str(player["player_id"]))


def _lineup_status(lineup: Mapping[str, Any]) -> str:
    raw_status = str(lineup.get("lineupStatus") or "").strip().casefold()
    if raw_status == "confirmed":
        return "confirmed"
    if raw_status in {"predicted", "probable", "expected"}:
        return "predicted"
    if not raw_status and (lineup.get("isConfirmed") is True or lineup.get("confirmed") is True):
        return "confirmed"
    return "unknown"


def _fingerprint(
    *,
    competition_id: str,
    event_id: str,
    source_match_id: str,
    kickoff: datetime,
    home_players: list[dict[str, str | None]],
    away_players: list[dict[str, str | None]],
) -> str:
    identity = {
        "competition_id": competition_id,
        "event_id": event_id,
        "source_match_id": source_match_id,
        "kickoff_at_utc": kickoff.isoformat(),
        "home_player_ids": [player["player_id"] for player in home_players],
        "away_player_ids": [player["player_id"] for player in away_players],
    }
    canonical = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _details_rejection_reason(
    details: Any,
    *,
    source_match_id: str,
    local: Mapping[str, Any],
    competition_id: str,
    registry: LeagueTeamIdentityRegistry,
    provider_competition_id: str,
) -> tuple[str | None, Mapping[str, Any], Mapping[str, Any]]:
    root = _mapping(details)
    general = _mapping(root.get("general"))
    details_competition_id = _provider_id(general.get("leagueId"))
    if details_competition_id is None:
        return "competition_identity_missing", {}, {}
    if details_competition_id != provider_competition_id:
        return "competition_mismatch", {}, {}
    if _provider_id(general.get("matchId")) != source_match_id:
        return "details_match_id_mismatch", {}, {}
    identity = registry.resolve_fixture(
        competition_id,
        _team_name(general.get("homeTeam")),
        _team_name(general.get("awayTeam")),
    )
    if identity["status"] != "verified":
        return "unmatched_team", {}, {}
    if (
        identity["home_canonical"] != local["home_canonical"]
        or identity["away_canonical"] != local["away_canonical"]
    ):
        return "home_away_mismatch", {}, {}
    try:
        details_kickoff = _utc(general.get("matchTimeUTC"))
    except (TypeError, ValueError):
        return "invalid_kickoff", {}, {}
    if abs(details_kickoff - local["kickoff"]) > _KICKOFF_TOLERANCE:
        return "kickoff_mismatch", {}, {}
    lineup = _mapping(_mapping(root.get("content")).get("lineup"))
    home_block = _mapping(lineup.get("homeTeam"))
    away_block = _mapping(lineup.get("awayTeam"))
    try:
        home_players = _players(home_block)
        away_players = _players(away_block)
    except ValueError:
        return "invalid_player_identity", {}, {}
    if home_players is None or away_players is None:
        return "incomplete_starting_xi", {}, {}
    status = _lineup_status(lineup)
    if status == "predicted":
        return "lineup_predicted", {}, {}
    if status != "confirmed":
        return "lineup_status_unknown", {}, {}
    if {player["player_id"] for player in home_players} & {
        player["player_id"] for player in away_players
    }:
        return "invalid_player_identity", {}, {}
    return None, {
        "lineup": lineup,
        "home_block": home_block,
        "away_block": away_block,
    }, {"home_players": home_players, "away_players": away_players}


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def parse_confirmed_fotmob_lineups(
    *,
    calendar_payload: Any,
    details_by_match_id: Mapping[str, Any],
    competition_id: str,
    local_fixtures: Sequence[Mapping[str, Any]],
    registry: LeagueTeamIdentityRegistry,
    fetched_at: Any,
    provider_competition_id: str | int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("fotmob_lineup_competition_not_allowed")
    fetched = _utc(fetched_at)
    approved_provider_competition_id = _provider_id(provider_competition_id)
    local_rows = _local_rows(competition_id, local_fixtures, registry)
    rejected: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    calendar_rows = _calendar_matches(calendar_payload)
    calendar_source_counts = Counter(_match_id(match) for _league_id, match in calendar_rows)

    for calendar_competition_id, match in calendar_rows:
        source_match_id = _match_id(match)
        if not source_match_id:
            rejected.append(_rejection(competition_id, "", "invalid_match_id"))
            continue
        if calendar_source_counts[source_match_id] != 1:
            rejected.append(_rejection(competition_id, source_match_id, "duplicate_candidate"))
            continue
        if approved_provider_competition_id is None:
            rejected.append(_rejection(competition_id, source_match_id, "competition_identity_unverified"))
            continue
        if calendar_competition_id is None:
            rejected.append(_rejection(competition_id, source_match_id, "competition_identity_missing"))
            continue
        if calendar_competition_id != approved_provider_competition_id:
            rejected.append(_rejection(competition_id, source_match_id, "competition_mismatch"))
            continue
        identity = registry.resolve_fixture(
            competition_id,
            _team_name(match.get("home")),
            _team_name(match.get("away")),
        )
        if identity["status"] != "verified":
            rejected.append(_rejection(competition_id, source_match_id, "unmatched_team"))
            continue
        oriented = [
            row for row in local_rows
            if row["home_canonical"] == identity["home_canonical"]
            and row["away_canonical"] == identity["away_canonical"]
        ]
        if not oriented:
            reversed_rows = [
                row for row in local_rows
                if row["home_canonical"] == identity["away_canonical"]
                and row["away_canonical"] == identity["home_canonical"]
            ]
            reason = "home_away_mismatch" if reversed_rows else "fixture_not_found"
            rejected.append(_rejection(competition_id, source_match_id, reason))
            continue
        competition_rows = [row for row in oriented if row["competition_matches"]]
        if not competition_rows:
            rejected.append(_rejection(competition_id, source_match_id, "competition_mismatch"))
            continue
        try:
            kickoff = _calendar_kickoff(match)
        except (TypeError, ValueError):
            rejected.append(_rejection(competition_id, source_match_id, "invalid_kickoff"))
            continue
        within_tolerance = [
            row for row in competition_rows
            if abs(kickoff - row["kickoff"]) <= _KICKOFF_TOLERANCE
        ]
        if not within_tolerance:
            rejected.append(_rejection(competition_id, source_match_id, "kickoff_mismatch"))
            continue
        if len(within_tolerance) != 1:
            rejected.append(_rejection(competition_id, source_match_id, "duplicate_candidate"))
            continue
        candidates.append({
            "source_match_id": source_match_id,
            "local": within_tolerance[0],
        })

    source_counts = Counter(candidate["source_match_id"] for candidate in candidates)
    event_counts = Counter(candidate["local"]["event_id"] for candidate in candidates)
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        source_match_id = candidate["source_match_id"]
        local = candidate["local"]
        if source_counts[source_match_id] != 1 or event_counts[local["event_id"]] != 1:
            rejected.append(_rejection(competition_id, source_match_id, "duplicate_candidate"))
            continue
        if fetched >= local["kickoff"]:
            rejected.append(_rejection(competition_id, source_match_id, "post_kickoff"))
            continue
        details = details_by_match_id.get(source_match_id)
        if details is None:
            rejected.append(_rejection(competition_id, source_match_id, "details_missing"))
            continue
        reason, blocks, players = _details_rejection_reason(
            details,
            source_match_id=source_match_id,
            local=local,
            competition_id=competition_id,
            registry=registry,
            provider_competition_id=approved_provider_competition_id,
        )
        if reason is not None:
            rejected.append(_rejection(competition_id, source_match_id, reason))
            continue
        home_players = players["home_players"]
        away_players = players["away_players"]
        home_block = blocks["home_block"]
        away_block = blocks["away_block"]
        accepted.append({
            "schema_version": 1,
            "provider": "fotmob",
            "competition_id": competition_id,
            "event_id": local["event_id"],
            "source_match_id": source_match_id,
            "kickoff_at_utc": local["kickoff"].isoformat(),
            "fetched_at": fetched.isoformat(),
            "lineup_status": "confirmed",
            "home_canonical": local["home_canonical"],
            "away_canonical": local["away_canonical"],
            "home_formation": _optional_text(home_block.get("formation")),
            "away_formation": _optional_text(away_block.get("formation")),
            "home_starting": home_players,
            "away_starting": away_players,
            "lineup_fingerprint": _fingerprint(
                competition_id=competition_id,
                event_id=local["event_id"],
                source_match_id=source_match_id,
                kickoff=local["kickoff"],
                home_players=home_players,
                away_players=away_players,
            ),
        })

    accepted.sort(key=lambda row: (row["event_id"], row["source_match_id"]))
    rejected.sort(key=lambda row: (row["source_match_id"], row["reason"]))
    return {"accepted": accepted, "rejected": rejected}
