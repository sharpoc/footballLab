from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MarketType(str, Enum):
    X12 = "1X2_90min"
    OU = "OverUnder_90min"
    AH = "AsianHandicap_90min"


class Grade(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    NO_MARKET_YET = "NO_MARKET_YET"
    ODDS_PENDING = "ODDS_PENDING"


@dataclass(frozen=True)
class OddsQuote:
    bookmaker: str
    market_type: MarketType
    selection: str
    odds: float
    line: float | None = None
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.fetched_at is not None and self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")


@dataclass(frozen=True)
class MarketProbs:
    market_type: MarketType
    probs: dict[str, float]
    line: float | None = None


@dataclass(frozen=True)
class Signal:
    market_type: MarketType
    selection: str
    grade: Grade
    ev: float | None
    edge: float | None
    status: str
    reasons: list[str] = field(default_factory=list)
    line: float | None = None
    raw_grade: Grade | None = None
    total_mu_source: str | None = None
    same_market_total_anchor: bool | None = None
    ah_market_validated: bool | None = None
    ah_validation_shadow: dict | None = None
    candidate_grade: str | None = None
    candidate_reasons: list[str] = field(default_factory=list)
