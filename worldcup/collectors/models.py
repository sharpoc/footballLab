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
class PlayerLineupEntry:
    name: str
    position: str | None = None
    player_id: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        out = {"name": self.name}
        if self.position is not None:
            out["position"] = self.position
        if self.player_id is not None:
            out["player_id"] = self.player_id
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class ParsedLineupContext:
    provider: str
    source: str | None
    source_match_no: int | None
    kickoff_at_utc: datetime | None
    home_team_name: str
    away_team_name: str
    home_canonical: str
    away_canonical: str
    confirmed_starting_xi: bool
    lineup_confirmed_at: datetime | None
    lineup_confidence: float | None = None
    home_starting: list[PlayerLineupEntry] = field(default_factory=list)
    home_bench: list[PlayerLineupEntry] = field(default_factory=list)
    home_absent: list[PlayerLineupEntry] = field(default_factory=list)
    away_starting: list[PlayerLineupEntry] = field(default_factory=list)
    away_bench: list[PlayerLineupEntry] = field(default_factory=list)
    away_absent: list[PlayerLineupEntry] = field(default_factory=list)
    home_formation: str | None = None
    away_formation: str | None = None
    home_attack_delta: float = 0.0
    home_defense_delta: float = 0.0
    home_goalkeeper_delta: float = 0.0
    away_attack_delta: float = 0.0
    away_defense_delta: float = 0.0
    away_goalkeeper_delta: float = 0.0

    def to_pipeline_context(self) -> dict:
        return {
            "provider": self.provider,
            "source": self.source,
            "source_match_no": self.source_match_no,
            "confirmed_starting_xi": self.confirmed_starting_xi,
            "lineup_confirmed_at": self.lineup_confirmed_at.isoformat()
            if self.lineup_confirmed_at
            else None,
            "lineup_confidence": self.lineup_confidence,
            "home_attack_delta": self.home_attack_delta,
            "home_defense_delta": self.home_defense_delta,
            "home_goalkeeper_delta": self.home_goalkeeper_delta,
            "away_attack_delta": self.away_attack_delta,
            "away_defense_delta": self.away_defense_delta,
            "away_goalkeeper_delta": self.away_goalkeeper_delta,
            "lineups": {
                "home": {
                    "team": self.home_team_name,
                    "canonical": self.home_canonical,
                    "formation": self.home_formation,
                    "starting_count": len(self.home_starting),
                    "bench_count": len(self.home_bench),
                    "absent_count": len(self.home_absent),
                    "starting": [player.to_dict() for player in self.home_starting],
                    "bench": [player.to_dict() for player in self.home_bench],
                    "absent": [player.to_dict() for player in self.home_absent],
                },
                "away": {
                    "team": self.away_team_name,
                    "canonical": self.away_canonical,
                    "formation": self.away_formation,
                    "starting_count": len(self.away_starting),
                    "bench_count": len(self.away_bench),
                    "absent_count": len(self.away_absent),
                    "starting": [player.to_dict() for player in self.away_starting],
                    "bench": [player.to_dict() for player in self.away_bench],
                    "absent": [player.to_dict() for player in self.away_absent],
                },
            },
        }


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
