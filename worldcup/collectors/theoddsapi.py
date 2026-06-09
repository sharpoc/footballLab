from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import ParsedOddsEvent
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
        home = str(item.get("home_team", "")).strip()
        away = str(item.get("away_team", "")).strip()
        quotes: list[OddsQuote] = []
        for bookmaker in item.get("bookmakers", []):
            bookmaker_key = str(bookmaker.get("key", "")).strip()
            if not bookmaker_key:
                continue
            bookmaker_updated_at = bookmaker.get("last_update")
            for market in bookmaker.get("markets", []):
                market_type = _MARKET_TYPES.get(market.get("key"))
                if market_type is None:
                    continue
                fetched_at_raw = market.get("last_update") or bookmaker_updated_at
                fetched_at = _parse_iso_utc(fetched_at_raw) if fetched_at_raw else None
                for outcome in market.get("outcomes", []):
                    selection = _selection_for_outcome(
                        market_type,
                        str(outcome.get("name", "")).strip(),
                        home,
                        away,
                    )
                    price = outcome.get("price")
                    if selection is None or price is None:
                        continue
                    line = outcome.get("point") if market_type in (MarketType.AH, MarketType.OU) else None
                    if market_type in (MarketType.AH, MarketType.OU) and line is None:
                        continue
                    quotes.append(
                        OddsQuote(
                            bookmaker=bookmaker_key,
                            market_type=market_type,
                            selection=selection,
                            odds=float(price),
                            line=float(line) if line is not None else None,
                            fetched_at=fetched_at,
                        )
                    )
        events.append(
            ParsedOddsEvent(
                source_event_id=str(item.get("id", "")),
                sport_key=str(item.get("sport_key", "")),
                kickoff_at_utc=_parse_iso_utc(str(item["commence_time"])),
                home_team_name=home,
                away_team_name=away,
                home_canonical=canonicalize_team(home),
                away_canonical=canonicalize_team(away),
                quotes=quotes,
            )
        )
    return events

