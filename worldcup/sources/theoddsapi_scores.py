from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from worldcup.quota import update_quota_from_headers
from worldcup.sources.theoddsapi import (
    BASE_URL,
    WORLD_CUP_SPORT_KEY,
    SourceFetchResult,
    fetch_json_from_url,
    _write_json,
)
from worldcup.theoddsapi_keys import LEGACY_PROVIDER


DEFAULT_DAYS_FROM = 2
ESTIMATED_LAST = 2


def build_worldcup_scores_url(api_key: str, days_from: int = DEFAULT_DAYS_FROM) -> str:
    params = {
        "daysFrom": days_from,
        "apiKey": api_key,
    }
    return f"{BASE_URL}/sports/{WORLD_CUP_SPORT_KEY}/scores/?{urlencode(params)}"


def fetch_worldcup_scores(
    api_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    quota_provider: str = LEGACY_PROVIDER,
    days_from: int = DEFAULT_DAYS_FROM,
    max_attempts: int = 2,
) -> SourceFetchResult:
    url = build_worldcup_scores_url(api_key=api_key, days_from=days_from)
    status, json_body, headers = fetch_json_from_url(
        url,
        transport=transport,
        max_attempts=max_attempts,
        redact_values=(api_key,),
    )

    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_json(written_cache_path, json_body)

    quota_entry = None
    if quota_path is not None:
        quota_entry = update_quota_from_headers(
            quota_path,
            quota_provider,
            headers,
            estimated_last=ESTIMATED_LAST,
            observed_at=observed_at,
        )
        if quota_provider != LEGACY_PROVIDER:
            update_quota_from_headers(
                quota_path,
                LEGACY_PROVIDER,
                headers,
                estimated_last=ESTIMATED_LAST,
                observed_at=observed_at,
            )

    return SourceFetchResult(
        status=status,
        json_body=json_body,
        headers=headers,
        cache_path=written_cache_path,
        quota_entry=quota_entry,
    )
