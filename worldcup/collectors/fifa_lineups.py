from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import ParsedLineupContext, PlayerLineupEntry
from worldcup.collectors.team_aliases import canonicalize_team


PROVIDER = "fifa_public_api"
SOURCE = "fifa_live_football"
_POSITION_LABELS = {
    0: "GK",
    1: "DF",
    2: "MF",
    3: "FW",
}


def _localized_name(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    for item in values:
        if not isinstance(item, dict):
            continue
        value = str(item.get("Description") or "").strip()
        if value:
            return value
    return ""


def _parse_optional_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _position_label(value: Any) -> str | None:
    code = _int_value(value)
    if code is None:
        return None
    return _POSITION_LABELS.get(code, str(code))


def _player_name(player: dict[str, Any]) -> str:
    return _localized_name(player.get("PlayerName")) or _localized_name(player.get("ShortName"))


def _players_by_status(raw_players: Any, status: int) -> list[PlayerLineupEntry]:
    if not isinstance(raw_players, list):
        return []
    players: list[PlayerLineupEntry] = []
    for player in raw_players:
        if not isinstance(player, dict) or _int_value(player.get("Status")) != status:
            continue
        name = _player_name(player)
        if not name:
            continue
        players.append(
            PlayerLineupEntry(
                name=name,
                position=_position_label(player.get("Position")),
                player_id=str(player.get("IdPlayer")).strip() if player.get("IdPlayer") else None,
            )
        )
    return players


def parse_fifa_live_match(
    raw: dict[str, Any],
    *,
    fetched_at: str | datetime | None = None,
) -> ParsedLineupContext:
    home = raw.get("HomeTeam") if isinstance(raw.get("HomeTeam"), dict) else {}
    away = raw.get("AwayTeam") if isinstance(raw.get("AwayTeam"), dict) else {}
    home_name = _localized_name(home.get("TeamName"))
    away_name = _localized_name(away.get("TeamName"))
    home_starting = _players_by_status(home.get("Players"), 1)
    away_starting = _players_by_status(away.get("Players"), 1)
    home_bench = _players_by_status(home.get("Players"), 2)
    away_bench = _players_by_status(away.get("Players"), 2)
    confirmed = len(home_starting) == 11 and len(away_starting) == 11
    observed = (
        fetched_at
        if isinstance(fetched_at, datetime)
        else _parse_optional_utc(fetched_at)
    )
    if observed is not None and observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc) if observed else None

    return ParsedLineupContext(
        provider=PROVIDER,
        source=SOURCE,
        source_match_no=_int_value(raw.get("MatchNumber")),
        kickoff_at_utc=_parse_optional_utc(raw.get("Date")),
        home_team_name=home_name,
        away_team_name=away_name,
        home_canonical=canonicalize_team(home_name),
        away_canonical=canonicalize_team(away_name),
        confirmed_starting_xi=confirmed,
        lineup_confirmed_at=observed if confirmed else None,
        lineup_confidence=1.0 if confirmed else None,
        home_starting=home_starting,
        home_bench=home_bench,
        away_starting=away_starting,
        away_bench=away_bench,
        home_formation=str(home.get("Tactics")).strip() if home.get("Tactics") else None,
        away_formation=str(away.get("Tactics")).strip() if away.get("Tactics") else None,
    )
