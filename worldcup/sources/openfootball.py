from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen


OPENFOOTBALL_2026_URL = "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"


@dataclass(frozen=True)
class TextFetchResult:
    status: int
    text: str
    headers: dict[str, str]
    cache_path: Path | None = None


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fetch_openfootball_2026(
    transport: Callable[[str], object] | None = None,
    cache_path: str | Path | None = None,
) -> TextFetchResult:
    response = (transport or _default_transport)(OPENFOOTBALL_2026_URL)
    text = response.read().decode("utf-8")
    written_cache_path = Path(cache_path) if cache_path is not None else None
    if written_cache_path is not None:
        _write_text(written_cache_path, text)
    return TextFetchResult(
        status=int(getattr(response, "status", 200)),
        text=text,
        headers=dict(getattr(response, "headers", {})),
        cache_path=written_cache_path,
    )
