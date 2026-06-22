from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen


BASE_URL = "https://api.fifa.com/api/v3"
DEFAULT_COMPETITION_ID = "17"
DEFAULT_SEASON_ID = "285023"


@dataclass(frozen=True)
class SourceFetchResult:
    status: int
    json_body: Any
    headers: dict[str, str]
    cache_path: Path | None = None


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def build_fifa_calendar_matches_url(
    *,
    from_date: str,
    to_date: str,
    id_competition: str = DEFAULT_COMPETITION_ID,
    id_season: str = DEFAULT_SEASON_ID,
    language: str = "en",
    count: int = 100,
) -> str:
    params = {
        "idCompetition": id_competition,
        "idSeason": id_season,
        "from": from_date,
        "to": to_date,
        "language": language,
        "count": str(count),
    }
    return f"{BASE_URL}/calendar/matches?{urlencode(params)}"


def build_fifa_live_match_url(
    *,
    id_competition: str,
    id_season: str,
    id_stage: str,
    id_match: str,
    language: str = "en",
) -> str:
    params = urlencode({"language": language})
    return f"{BASE_URL}/live/football/{id_competition}/{id_season}/{id_stage}/{id_match}?{params}"


def _fetch_json(
    url: str,
    *,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
) -> SourceFetchResult:
    response = (transport or _default_transport)(url)
    body = response.read()
    json_body = json.loads(body.decode("utf-8"))
    headers = dict(getattr(response, "headers", {}))
    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)
    return SourceFetchResult(
        status=int(getattr(response, "status", 200)),
        json_body=json_body,
        headers=headers,
        cache_path=written_cache_path,
    )


def fetch_fifa_calendar_matches(
    *,
    from_date: str,
    to_date: str,
    id_competition: str = DEFAULT_COMPETITION_ID,
    id_season: str = DEFAULT_SEASON_ID,
    language: str = "en",
    count: int = 100,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
) -> SourceFetchResult:
    url = build_fifa_calendar_matches_url(
        from_date=from_date,
        to_date=to_date,
        id_competition=id_competition,
        id_season=id_season,
        language=language,
        count=count,
    )
    return _fetch_json(url, transport=transport, cache_path=cache_path)


def fetch_fifa_live_match(
    *,
    id_competition: str,
    id_season: str,
    id_stage: str,
    id_match: str,
    language: str = "en",
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
) -> SourceFetchResult:
    url = build_fifa_live_match_url(
        id_competition=id_competition,
        id_season=id_season,
        id_stage=id_stage,
        id_match=id_match,
        language=language,
    )
    return _fetch_json(url, transport=transport, cache_path=cache_path)
