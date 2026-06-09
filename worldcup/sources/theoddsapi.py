from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from worldcup.quota import update_quota_from_headers


BASE_URL = "https://api.the-odds-api.com/v4"
WORLD_CUP_SPORT_KEY = "soccer_fifa_world_cup"
DEFAULT_MARKETS = ("h2h", "spreads", "totals")


@dataclass(frozen=True)
class SourceFetchResult:
    status: int
    json_body: Any
    headers: dict[str, str]
    cache_path: Path | None = None
    quota_entry: dict | None = None


def build_worldcup_odds_url(
    api_key: str,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> str:
    params = {
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": odds_format,
        "dateFormat": date_format,
        "apiKey": api_key,
    }
    return f"{BASE_URL}/sports/{WORLD_CUP_SPORT_KEY}/odds?{urlencode(params)}"


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fetch_worldcup_odds(
    api_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
) -> SourceFetchResult:
    url = build_worldcup_odds_url(api_key=api_key, regions=regions, markets=markets)
    response = (transport or _default_transport)(url)
    body = response.read()
    json_body = json.loads(body.decode("utf-8"))
    headers = dict(getattr(response, "headers", {}))

    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)

    quota_entry = None
    if quota_path is not None:
        quota_entry = update_quota_from_headers(
            quota_path,
            "theoddsapi",
            headers,
            estimated_last=len(markets),
            observed_at=observed_at,
        )

    return SourceFetchResult(
        status=int(getattr(response, "status", 200)),
        json_body=json_body,
        headers=headers,
        cache_path=written_cache_path,
        quota_entry=quota_entry,
    )
