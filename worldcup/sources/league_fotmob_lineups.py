from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_BASE_URL = "https://www.fotmob.com/api"
_FORBIDDEN_METADATA_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "raw",
    "raw_response",
    "request_headers",
    "secret",
    "token",
}


class FotMobProviderContractDrift(RuntimeError):
    pass


def _query_scalar(value: Any, *, error: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TypeError(error)
    text = str(value).strip()
    if not text:
        raise ValueError(error)
    return text


def build_fotmob_calendar_url(date: str) -> str:
    date_value = _query_scalar(date, error="fotmob_calendar_date_invalid")
    return f"{_BASE_URL}/data/matches?{urlencode({'date': date_value})}"


def build_fotmob_details_url(match_id: str | int) -> str:
    match_id_value = _query_scalar(match_id, error="fotmob_match_id_invalid")
    return f"{_BASE_URL}/data/matchDetails?{urlencode({'matchId': match_id_value})}"


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


def _contains_forbidden_metadata(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_METADATA_KEYS or _contains_forbidden_metadata(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_metadata(item) for item in value)
    return False


def _decoded_json(url: str, transport: Callable[[str], Any] | None) -> dict[str, Any]:
    try:
        response = (transport or _default_transport)(url)
    except HTTPError as exc:
        if exc.code == 404:
            raise FotMobProviderContractDrift("fotmob_provider_contract_drift_404") from None
        raise RuntimeError("fotmob_transport_failed") from None
    except Exception:
        raise RuntimeError("fotmob_transport_failed") from None
    if isinstance(response, (dict, list)) or not callable(getattr(response, "read", None)):
        raise TypeError("fotmob_transport_response_invalid")
    try:
        body = response.read()
    except Exception:
        raise RuntimeError("fotmob_response_read_failed") from None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("fotmob_response_json_invalid") from None
    if not isinstance(body, str):
        raise TypeError("fotmob_response_body_invalid")
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError):
        raise ValueError("fotmob_response_json_invalid") from None
    if not isinstance(decoded, dict):
        raise TypeError("fotmob_response_body_invalid")
    if _contains_forbidden_metadata(decoded):
        raise ValueError("fotmob_response_contains_forbidden_metadata")
    return decoded


def fetch_fotmob_calendar(
    *,
    date: str,
    transport: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    return _decoded_json(build_fotmob_calendar_url(date), transport)


def fetch_fotmob_details(
    *,
    match_id: str | int,
    transport: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    return _decoded_json(build_fotmob_details_url(match_id), transport)
