from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_BASE_URL = "https://www.fotmob.com/api"


def build_fotmob_calendar_url(date: str) -> str:
    return f"{_BASE_URL}/matches?{urlencode({'date': str(date)})}"


def build_fotmob_details_url(match_id: str | int) -> str:
    return f"{_BASE_URL}/matchDetails?{urlencode({'matchId': str(match_id)})}"


def _default_transport(url: str) -> Any:
    request = Request(
        url,
        headers={
            "accept": "application/json, text/plain, */*",
            "user-agent": "football-research-lineup-collector/1.0",
            "x-fm-req": "1",
        },
    )
    return urlopen(request, timeout=20)


def _decoded_json(url: str, transport: Callable[[str], Any] | None) -> Any:
    response = (transport or _default_transport)(url)
    if isinstance(response, (dict, list)):
        return response
    body = response.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def fetch_fotmob_calendar(
    *,
    date: str,
    transport: Callable[[str], Any] | None = None,
) -> Any:
    return _decoded_json(build_fotmob_calendar_url(date), transport)


def fetch_fotmob_details(
    *,
    match_id: str | int,
    transport: Callable[[str], Any] | None = None,
) -> Any:
    return _decoded_json(build_fotmob_details_url(match_id), transport)
