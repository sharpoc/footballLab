from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from worldcup.collectors.models import Fixture, MatchResult
from worldcup.collectors.team_aliases import canonicalize_team


_TIME_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+UTC(?P<offset>[+-]\d{1,2})$")
_PLACEHOLDER_RE = re.compile(r"^([WL]\d+|[12][A-L]|3[A-L](?:/[A-L])+)$")


def is_placeholder_team(name: str | None) -> bool:
    if not name:
        return True
    stripped = name.strip()
    if not stripped:
        return True
    return bool(_PLACEHOLDER_RE.match(stripped))


def _parse_openfootball_kickoff(date_value: str, time_value: str) -> datetime:
    match = _TIME_RE.match(time_value.strip())
    if not match:
        raise ValueError(f"unsupported openfootball time format: {time_value}")
    offset_hours = int(match.group("offset"))
    local_tz = timezone(timedelta(hours=offset_hours))
    local_dt = datetime(
        int(date_value[0:4]),
        int(date_value[5:7]),
        int(date_value[8:10]),
        int(match.group("hour")),
        int(match.group("minute")),
        tzinfo=local_tz,
    )
    return local_dt.astimezone(timezone.utc)


def parse_openfootball_fixtures(raw: dict[str, Any]) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for index, match in enumerate(raw.get("matches", []), start=1):
        home = str(match.get("team1", "")).strip()
        away = str(match.get("team2", "")).strip()
        home_placeholder = is_placeholder_team(home)
        away_placeholder = is_placeholder_team(away)
        raw_match_no = match.get("num")
        source_match_no = raw_match_no if isinstance(raw_match_no, int) else index
        time_raw = str(match["time"])
        fixtures.append(
            Fixture(
                source_match_no=source_match_no,
                kickoff_at_utc=_parse_openfootball_kickoff(str(match["date"]), time_raw),
                kickoff_time_raw=time_raw,
                home_team_name=home,
                away_team_name=away,
                home_canonical=None if home_placeholder else canonicalize_team(home),
                away_canonical=None if away_placeholder else canonicalize_team(away),
                group=match.get("group"),
                stage=match.get("round"),
                venue_name=match.get("ground"),
                has_placeholder_team=home_placeholder or away_placeholder,
            )
        )
    return fixtures


def _extract_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score1, score2 = match.get("score1"), match.get("score2")
    if isinstance(score1, int) and isinstance(score2, int):
        return score1, score2
    score = match.get("score")
    if isinstance(score, dict):
        ft = score.get("ft")
        if isinstance(ft, list) and len(ft) == 2 and all(isinstance(v, int) for v in ft):
            return ft[0], ft[1]
    return None


def parse_openfootball_results(raw: dict[str, Any]) -> list[MatchResult]:
    results: list[MatchResult] = []
    fixtures = parse_openfootball_fixtures(raw)
    for fixture, match in zip(fixtures, raw.get("matches", [])):
        if fixture.has_placeholder_team:
            continue
        score = _extract_score(match)
        if score is None:
            continue
        results.append(
            MatchResult(
                kickoff_at_utc=fixture.kickoff_at_utc,
                home_team_name=fixture.home_team_name,
                away_team_name=fixture.away_team_name,
                home_canonical=fixture.home_canonical,
                away_canonical=fixture.away_canonical,
                home_score=score[0],
                away_score=score[1],
            )
        )
    return results
