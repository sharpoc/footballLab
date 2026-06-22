from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.club_aliases import canonicalize_club, match_club_alias
from worldcup.collectors.models import Fixture, InvalidOddsQuote, ParsedOddsEvent
from worldcup.models import MarketType, OddsQuote


_MARKET_TYPES = {
    "h2h": MarketType.X12,
    "spreads": MarketType.AH,
    "totals": MarketType.OU,
}


@dataclass(frozen=True)
class LeagueOddsParseResult:
    fixtures: list[Fixture]
    odds_events: list[ParsedOddsEvent]
    fixture_source: str = "odds_event_only"
    unmatched_clubs: list[str] = field(default_factory=list)


def _parse_iso_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _selection_for_outcome(
    market_type: MarketType,
    outcome_name: str,
    home_team: str,
    away_team: str,
) -> str | None:
    if market_type in (MarketType.X12, MarketType.AH):
        if outcome_name == home_team:
            return "home"
        if outcome_name == away_team:
            return "away"
        if market_type == MarketType.X12 and outcome_name.lower() == "draw":
            return "draw"
        return None
    if market_type == MarketType.OU:
        lowered = outcome_name.lower()
        return lowered if lowered in ("over", "under") else None
    return None


def _quotes_for_event(
    item: dict[str, Any],
    home: str,
    away: str,
) -> tuple[list[OddsQuote], list[InvalidOddsQuote]]:
    event_id = str(item.get("id", ""))
    commence_time = str(item["commence_time"])
    quotes: list[OddsQuote] = []
    invalid_odds: list[InvalidOddsQuote] = []
    for bookmaker in item.get("bookmakers", []):
        bookmaker_key = str(bookmaker.get("key", "")).strip()
        if not bookmaker_key:
            continue
        bookmaker_updated_at = bookmaker.get("last_update")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", "")).strip()
            market_type = _MARKET_TYPES.get(market_key)
            if market_type is None:
                continue
            fetched_at_raw = market.get("last_update") or bookmaker_updated_at
            fetched_at = _parse_iso_utc(fetched_at_raw) if fetched_at_raw else None
            for outcome in market.get("outcomes", []):
                outcome_name = str(outcome.get("name", "")).strip()
                selection = _selection_for_outcome(market_type, outcome_name, home, away)
                price = outcome.get("price")
                if selection is None or price is None:
                    continue
                line = outcome.get("point") if market_type in (MarketType.AH, MarketType.OU) else None
                if market_type in (MarketType.AH, MarketType.OU) and line is None:
                    continue
                odds_value = float(price)
                line_value = float(line) if line is not None else None
                if odds_value <= 1.0:
                    invalid_odds.append(
                        InvalidOddsQuote(
                            reason="odds_decimal_lte_one",
                            odds=odds_value,
                            bookmaker=bookmaker_key,
                            market=market_key,
                            api_market_key=market_key,
                            market_type=market_type,
                            selection=selection,
                            outcome=outcome_name,
                            line=line_value,
                            match_id=event_id,
                            home_team=home,
                            away_team=away,
                            commence_time=commence_time,
                            last_update=str(fetched_at_raw) if fetched_at_raw else None,
                        )
                    )
                    continue
                quotes.append(
                    OddsQuote(
                        bookmaker=bookmaker_key,
                        market_type=market_type,
                        selection=selection,
                        odds=odds_value,
                        line=line_value,
                        fetched_at=fetched_at,
                    )
                )
    return quotes, invalid_odds


def parse_league_odds_events(
    raw: list[dict[str, Any]],
    competition_id: str,
) -> LeagueOddsParseResult:
    fixtures: list[Fixture] = []
    odds_events: list[ParsedOddsEvent] = []
    unmatched: list[str] = []
    for item in raw:
        kickoff = _parse_iso_utc(str(item["commence_time"]))
        home = str(item.get("home_team", "")).strip()
        away = str(item.get("away_team", "")).strip()
        home_match = match_club_alias(competition_id, home)
        away_match = match_club_alias(competition_id, away)
        if home_match.unmatched_name:
            unmatched.append(home_match.unmatched_name)
        if away_match.unmatched_name:
            unmatched.append(away_match.unmatched_name)
        home_canonical = home_match.canonical_key or canonicalize_club(competition_id, home)
        away_canonical = away_match.canonical_key or canonicalize_club(competition_id, away)
        fixtures.append(
            Fixture(
                source_match_no=None,
                kickoff_at_utc=kickoff,
                kickoff_time_raw=kickoff.isoformat(),
                home_team_name=home,
                away_team_name=away,
                home_canonical=home_canonical,
                away_canonical=away_canonical,
                group=None,
                stage=None,
                venue_name=None,
                has_placeholder_team=False,
            )
        )
        quotes, invalid_odds = _quotes_for_event(item, home, away)
        odds_events.append(
            ParsedOddsEvent(
                source_event_id=str(item.get("id", "")),
                sport_key=str(item.get("sport_key", "")),
                kickoff_at_utc=kickoff,
                home_team_name=home,
                away_team_name=away,
                home_canonical=home_canonical,
                away_canonical=away_canonical,
                quotes=quotes,
                invalid_odds=invalid_odds,
            )
        )
    return LeagueOddsParseResult(
        fixtures=fixtures,
        odds_events=odds_events,
        unmatched_clubs=sorted(set(unmatched)),
    )
