from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from worldcup.models import MarketType, OddsQuote


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
class InvalidOddsQuote:
    reason: str
    odds: float
    bookmaker: str
    market: str
    api_market_key: str
    market_type: MarketType
    selection: str
    outcome: str
    line: float | None
    match_id: str
    home_team: str
    away_team: str
    commence_time: str
    last_update: str | None

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "odds": self.odds,
            "bookmaker": self.bookmaker,
            "market": self.market,
            "api_market_key": self.api_market_key,
            "market_type": self.market_type.value,
            "selection": self.selection,
            "outcome": self.outcome,
            "line": self.line,
            "match_id": self.match_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "commence_time": self.commence_time,
            "last_update": self.last_update,
        }


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
    invalid_odds: list[InvalidOddsQuote] = field(default_factory=list)
