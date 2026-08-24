from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.club_aliases import canonicalize_club
from worldcup.competitions import get_competition


def _utc(value: Any) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("result_timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc).isoformat()


def parse_verified_league_results(
    raw: list[dict[str, Any]],
    competition_id: str,
    *,
    score_semantics_verified: bool = False,
) -> dict[str, Any]:
    profile = get_competition(competition_id)
    results: list[dict[str, Any]] = []
    pending: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in raw:
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in seen:
            raise ValueError(f"result_event_identity_invalid: {event_id}")
        seen.add(event_id)
        if event.get("sport_key") != profile.theoddsapi_sport_key or event.get("completed") is not True:
            continue
        if not score_semantics_verified:
            pending.append({"source_event_id": event_id, "reason": "result_90min_semantics_unverified"})
            continue
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        scores = {
            str(row.get("name") or "").strip(): row.get("score")
            for row in event.get("scores") or []
            if isinstance(row, dict)
        }
        home_raw, away_raw = scores.get(home), scores.get(away)
        if not home or not away or isinstance(home_raw, bool) or isinstance(away_raw, bool):
            continue
        if not str(home_raw).isdigit() or not str(away_raw).isdigit():
            continue
        results.append({
            "competition_id": competition_id,
            "source_event_id": event_id,
            "kickoff_at_utc": _utc(event.get("commence_time")),
            "home_team": home,
            "away_team": away,
            "home_canonical": canonicalize_club(competition_id, home),
            "away_canonical": canonicalize_club(competition_id, away),
            "home_score": int(home_raw),
            "away_score": int(away_raw),
            "captured_at": _utc(event.get("last_update") or event.get("commence_time")),
            "result_scope": "football_90min",
        })
    return {"competition_id": competition_id, "results": results, "pending": pending}
