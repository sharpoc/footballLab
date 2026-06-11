from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import urlopen

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
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
    validator: Callable[[str], None],
) -> TextFetchResult:
    response = transport(url)
    status = int(getattr(response, "status", 200))
    text = response.read().decode("utf-8")
    if status >= 400:
        raise ValueError(f"failed to fetch Elo TSV: HTTP {status}")
    validator(text)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return TextFetchResult(
        status=status,
        text=text,
        headers=dict(getattr(response, "headers", {})),
        cache_path=cache_path,
    )


def _validate_world_tsv(text: str) -> None:
    if not parse_elo_ratings(text):
        raise ValueError("invalid Elo ratings TSV: parsed 0 rows")


def _validate_teams_tsv(text: str) -> None:
    if not parse_elo_team_aliases(text):
        raise ValueError("invalid Elo teams TSV: parsed 0 aliases")


def fetch_elo_files(
    cache_dir: str | Path,
    transport: Callable[[str], object] | None = None,
) -> EloFetchResult:
    cache_path = Path(cache_dir)
    fetch = transport or _default_transport
    return EloFetchResult(
        world=_fetch_text(ELO_WORLD_URL, cache_path / "elo_world.tsv", fetch, _validate_world_tsv),
        teams=_fetch_text(ELO_TEAMS_URL, cache_path / "elo_teams.tsv", fetch, _validate_teams_tsv),
    )
