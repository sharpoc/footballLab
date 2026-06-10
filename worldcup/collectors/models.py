from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from worldcup.models import OddsQuote


@dataclass(frozen=True)
class TeamAliasResult:
    raw_name: str
    canonical_key: str | None
    unmatched_name: str | None = None


@dataclass(frozen=True)
class TeamAlias:
    canonical_key: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class Fixture:
    source_match_no: int | None
    kickoff_at_utc: datetime
    kickoff_time_raw: str
    home_team_name: str
    away_team_name: str
    home_canonical: str | None
    away_canonical: str | None
    group: str | None = None
    stage: str | None = None
    venue_name: str | None = None
    has_placeholder_team: bool = False


@dataclass(frozen=True)
class MatchResult:
    kickoff_at_utc: datetime
    home_team_name: str
    away_team_name: str
    home_canonical: str | None
    away_canonical: str | None
    home_score: int
    away_score: int


@dataclass(frozen=True)
class EloRating:
    code: str
    rank: int
    rating: int


@dataclass(frozen=True)
class ParsedOddsEvent:
    source_event_id: str
    sport_key: str
    kickoff_at_utc: datetime
    home_team_name: str
    away_team_name: str
    home_canonical: str | None
    away_canonical: str | None
    quotes: list[OddsQuote] = field(default_factory=list)
