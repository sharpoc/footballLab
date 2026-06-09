from __future__ import annotations

from worldcup.collectors.models import EloRating


def parse_elo_ratings(text: str) -> dict[str, EloRating]:
    ratings: dict[str, EloRating] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            rank = int(parts[0])
            code = parts[2].strip()
            rating = int(parts[3])
        except ValueError:
            continue
        if code:
            ratings[code] = EloRating(code=code, rank=rank, rating=rating)
    return ratings


def parse_elo_team_aliases(text: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("\t") if part.strip()]
        if len(parts) < 2:
            continue
        code = parts[0]
        for name in parts[1:]:
            aliases[name] = code
    return aliases

