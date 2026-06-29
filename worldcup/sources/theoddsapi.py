from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from worldcup.quota import update_quota_from_headers
from worldcup.theoddsapi_keys import LEGACY_PROVIDER


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


class SourceFetchError(RuntimeError):
    def __init__(
        self,
        reason: str,
        sanitized_url: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        attempts: int = 1,
        detail: str | None = None,
    ) -> None:
        self.reason = reason
        self.sanitized_url = sanitized_url
        self.status = status
        self.retryable = retryable
        self.attempts = attempts
        self.detail = detail
        super().__init__(str(self))

    def __str__(self) -> str:
        parts = [
            self.reason,
            f"attempts={self.attempts}",
            f"retryable={str(self.retryable).lower()}",
            f"url={self.sanitized_url}",
        ]
        if self.status is not None:
            parts.insert(1, f"status={self.status}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " ".join(parts)


def build_worldcup_odds_url(
    api_key: str,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    odds_format: str = "decimal",
    date_format: str = "iso",
) -> str:
    return build_odds_url(
        sport_key=WORLD_CUP_SPORT_KEY,
        api_key=api_key,
        regions=regions,
        markets=markets,
        odds_format=odds_format,
        date_format=date_format,
    )


def build_odds_url(
    sport_key: str,
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
    return f"{BASE_URL}/sports/{sport_key}/odds?{urlencode(params)}"


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def sanitize_url_for_log(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, "<redacted>" if key.lower() == "apikey" else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    sanitized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    return sanitized.replace("apiKey=%3Credacted%3E", "apiKey=<redacted>")


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return re.sub(r"apiKey=[^&\s]+", "apiKey=<redacted>", redacted)


def _status_reason(status: int) -> tuple[str, bool]:
    if status in {401, 403}:
        return "credential_error", False
    if status == 429:
        return "quota_error", False
    if status >= 500:
        return "transient_http_error", True
    return "http_error", False


def _bounded_attempts(max_attempts: int) -> int:
    return max(1, int(max_attempts))


def fetch_json_from_url(
    url: str,
    transport: Callable[[str], Any] | None = None,
    *,
    max_attempts: int = 2,
    redact_values: tuple[str, ...] = (),
) -> tuple[int, Any, dict[str, str]]:
    fetch = transport or _default_transport
    attempts_allowed = _bounded_attempts(max_attempts)
    sanitized_url = sanitize_url_for_log(url)
    for attempt in range(1, attempts_allowed + 1):
        try:
            response = fetch(url)
        except Exception as exc:
            detail = _redact_text(str(exc), redact_values)
            error = SourceFetchError(
                "network_error",
                sanitized_url,
                retryable=True,
                attempts=attempt,
                detail=detail,
            )
            if attempt < attempts_allowed:
                continue
            raise error from exc

        status = int(getattr(response, "status", 200))
        headers = dict(getattr(response, "headers", {}))
        if status < 200 or status >= 300:
            reason, retryable = _status_reason(status)
            error = SourceFetchError(
                reason,
                sanitized_url,
                status=status,
                retryable=retryable,
                attempts=attempt,
            )
            if retryable and attempt < attempts_allowed:
                continue
            raise error

        try:
            body = response.read()
        except Exception as exc:
            detail = _redact_text(str(exc), redact_values)
            error = SourceFetchError(
                "network_error",
                sanitized_url,
                status=status,
                retryable=True,
                attempts=attempt,
                detail=detail,
            )
            if attempt < attempts_allowed:
                continue
            raise error from exc

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceFetchError(
                "invalid_encoding",
                sanitized_url,
                status=status,
                retryable=False,
                attempts=attempt,
            ) from exc

        try:
            return status, json.loads(text), headers
        except json.JSONDecodeError as exc:
            raise SourceFetchError(
                "invalid_json",
                sanitized_url,
                status=status,
                retryable=False,
                attempts=attempt,
            ) from exc

    raise AssertionError("unreachable")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def fetch_worldcup_odds(
    api_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    quota_provider: str = LEGACY_PROVIDER,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    max_attempts: int = 2,
) -> SourceFetchResult:
    return fetch_odds_for_sport(
        api_key=api_key,
        sport_key=WORLD_CUP_SPORT_KEY,
        transport=transport,
        cache_path=cache_path,
        quota_path=quota_path,
        observed_at=observed_at,
        quota_provider=quota_provider,
        regions=regions,
        markets=markets,
        max_attempts=max_attempts,
    )


def fetch_odds_for_sport(
    api_key: str,
    sport_key: str,
    transport: Callable[[str], Any] | None = None,
    cache_path: str | Path | None = None,
    quota_path: str | Path | None = None,
    observed_at: str | None = None,
    quota_provider: str = LEGACY_PROVIDER,
    regions: str = "eu",
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    max_attempts: int = 2,
) -> SourceFetchResult:
    url = build_odds_url(api_key=api_key, sport_key=sport_key, regions=regions, markets=markets)
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
            estimated_last=len(markets),
            observed_at=observed_at,
        )
        if quota_provider != LEGACY_PROVIDER:
            update_quota_from_headers(
                quota_path,
                LEGACY_PROVIDER,
                headers,
                estimated_last=len(markets),
                observed_at=observed_at,
            )

    return SourceFetchResult(
        status=status,
        json_body=json_body,
        headers=headers,
        cache_path=written_cache_path,
        quota_entry=quota_entry,
    )
