from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import InvalidOddsQuote, ParsedOddsEvent
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.models import MarketType, OddsQuote


_MARKET_TYPES = {
    "h2h": MarketType.X12,
    "spreads": MarketType.AH,
    "totals": MarketType.OU,
}


def _parse_iso_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


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


def parse_theoddsapi_events(raw: list[dict[str, Any]]) -> list[ParsedOddsEvent]:
    events: list[ParsedOddsEvent] = []
    for item in raw:
        event_id = str(item.get("id", ""))
        commence_time = str(item["commence_time"])
        home = str(item.get("home_team", "")).strip()
        away = str(item.get("away_team", "")).strip()
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
                    selection = _selection_for_outcome(
                        market_type,
                        outcome_name,
                        home,
                        away,
                    )
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
        events.append(
            ParsedOddsEvent(
                source_event_id=event_id,
                sport_key=str(item.get("sport_key", "")),
                kickoff_at_utc=_parse_iso_utc(commence_time),
                home_team_name=home,
                away_team_name=away,
                home_canonical=canonicalize_team(home),
                away_canonical=canonicalize_team(away),
                quotes=quotes,
                invalid_odds=invalid_odds,
            )
        )
    return events
