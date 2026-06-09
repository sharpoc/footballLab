from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from worldcup.sources.openfootball import TextFetchResult


ELO_WORLD_URL = "https://www.eloratings.net/World.tsv"
ELO_TEAMS_URL = "https://www.eloratings.net/en.teams.tsv"


@dataclass(frozen=True)
class EloFetchResult:
    world: TextFetchResult
    teams: TextFetchResult


def _default_transport(url: str):
    return urlopen(url, timeout=30)


def _fetch_text(
    url: str,
    cache_path: Path,
    transport: Callable[[str], object],
) -> TextFetchResult:
    response = transport(url)
    text = response.read().decode("utf-8")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return TextFetchResult(
        status=int(getattr(response, "status", 200)),
        text=text,
        headers=dict(getattr(response, "headers", {})),
        cache_path=cache_path,
    )


def fetch_elo_files(
    cache_dir: str | Path,
    transport: Callable[[str], object] | None = None,
) -> EloFetchResult:
    cache_path = Path(cache_dir)
    fetch = transport or _default_transport
    return EloFetchResult(
        world=_fetch_text(ELO_WORLD_URL, cache_path / "elo_world.tsv", fetch),
        teams=_fetch_text(ELO_TEAMS_URL, cache_path / "elo_teams.tsv", fetch),
    )
