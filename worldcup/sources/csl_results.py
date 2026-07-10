from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable


CFL_OFFICIAL_2026_URL = (
    "https://api.cfl-china.cn/frontweb/api/matches/page?competition_code=CSL&"
    "tournament_calendar_id=e6818x4pwankpph8awr91m1hw&pageSize=300&curPage=1"
)
SEVENM_2026_FIXTURE_URL = "https://data.7m.com.cn/Matches_Data/152/gb/fixture.js"

BytesTransport = Callable[[str], bytes]


def _default_transport(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def fetch_cfl_official_results(
    url: str = CFL_OFFICIAL_2026_URL,
    *,
    transport: BytesTransport | None = None,
) -> dict[str, Any]:
    raw = (transport or _default_transport)(url)
    payload = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("cfl official response must be an object")
    return payload


def fetch_sevenm_fixture(
    url: str = SEVENM_2026_FIXTURE_URL,
    *,
    transport: BytesTransport | None = None,
) -> str:
    raw = (transport or _default_transport)(url)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030")
