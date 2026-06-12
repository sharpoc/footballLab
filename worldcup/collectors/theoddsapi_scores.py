"""Parse The Odds API scores responses into MatchResult records."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import MatchResult
from worldcup.collectors.team_aliases import canonicalize_team


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_theoddsapi_scores(raw: list[dict[str, Any]]) -> list[MatchResult]:
    results: list[MatchResult] = []
    for event in raw or []:
        if not event.get("completed"):
            continue
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        commence = event.get("commence_time")
        if not home or not away or not commence:
            continue
        by_name = {
            str(score.get("name") or "").strip(): str(score.get("score") or "").strip()
            for score in (event.get("scores") or [])
        }
        home_score = by_name.get(home)
        away_score = by_name.get(away)
        if home_score is None or away_score is None:
            continue
        if not home_score.isdigit() or not away_score.isdigit():
            continue
        results.append(
            MatchResult(
                kickoff_at_utc=_parse_at(str(commence)),
                home_team_name=home,
                away_team_name=away,
                home_canonical=canonicalize_team(home),
                away_canonical=canonicalize_team(away),
                home_score=int(home_score),
                away_score=int(away_score),
            )
        )
    return results
