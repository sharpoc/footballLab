from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import ParsedLineupContext, PlayerLineupEntry
from worldcup.collectors.team_aliases import canonicalize_team


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


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _players(raw: Any) -> list[PlayerLineupEntry]:
    if not isinstance(raw, list):
        return []
    players: list[PlayerLineupEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        players.append(
            PlayerLineupEntry(
                name=name,
                position=str(item.get("position")).strip() if item.get("position") else None,
                player_id=str(item.get("player_id")).strip() if item.get("player_id") else None,
                reason=str(item.get("reason")).strip() if item.get("reason") else None,
            )
        )
    return players


def _team_name(item: dict[str, Any], side: str) -> str:
    block = item.get(side)
    if isinstance(block, dict):
        team = str(block.get("team") or "").strip()
        if team:
            return team
    return str(item.get(f"{side}_team") or "").strip()


def _impact(item: dict[str, Any], side: str, key: str) -> float:
    block = item.get(side) if isinstance(item.get(side), dict) else {}
    impact = block.get("impact") if isinstance(block.get("impact"), dict) else {}
    return _float_value(impact.get(key), 0.0)


def parse_lineup_contexts(raw: dict[str, Any]) -> list[ParsedLineupContext]:
    provider = str(raw.get("provider") or "manual_json").strip() or "manual_json"
    contexts: list[ParsedLineupContext] = []
    for item in raw.get("matches") or []:
        if not isinstance(item, dict):
            continue
        home_team = _team_name(item, "home")
        away_team = _team_name(item, "away")
        home_canonical = str(item.get("home_canonical") or canonicalize_team(home_team) or "").strip()
        away_canonical = str(item.get("away_canonical") or canonicalize_team(away_team) or "").strip()
        if not home_team or not away_team or not home_canonical or not away_canonical:
            continue
        home_block = item.get("home") if isinstance(item.get("home"), dict) else {}
        away_block = item.get("away") if isinstance(item.get("away"), dict) else {}
        contexts.append(
            ParsedLineupContext(
                provider=str(item.get("provider") or provider),
                source=str(item.get("source")).strip() if item.get("source") else None,
                source_match_no=_int_value(item.get("source_match_no")),
                kickoff_at_utc=_parse_optional_utc(item.get("kickoff_at_utc")),
                home_team_name=home_team,
                away_team_name=away_team,
                home_canonical=home_canonical,
                away_canonical=away_canonical,
                confirmed_starting_xi=bool(item.get("confirmed_starting_xi", False)),
                lineup_confirmed_at=_parse_optional_utc(item.get("lineup_confirmed_at")),
                lineup_confidence=_optional_float(item.get("lineup_confidence")),
                home_starting=_players(home_block.get("starting")),
                home_bench=_players(home_block.get("bench")),
                home_absent=_players(home_block.get("absent")),
                away_starting=_players(away_block.get("starting")),
                away_bench=_players(away_block.get("bench")),
                away_absent=_players(away_block.get("absent")),
                home_formation=str(home_block.get("formation")).strip()
                if home_block.get("formation")
                else None,
                away_formation=str(away_block.get("formation")).strip()
                if away_block.get("formation")
                else None,
                home_attack_delta=_impact(item, "home", "attack_delta"),
                home_defense_delta=_impact(item, "home", "defense_delta"),
                home_goalkeeper_delta=_impact(item, "home", "goalkeeper_delta"),
                away_attack_delta=_impact(item, "away", "attack_delta"),
                away_defense_delta=_impact(item, "away", "defense_delta"),
                away_goalkeeper_delta=_impact(item, "away", "goalkeeper_delta"),
            )
        )
    return contexts
