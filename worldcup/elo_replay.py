"""Replay eloratings-style ratings from historical international results.

Purely offline: reads local CSV files only, never contacts external services,
and does not participate in the live pipeline.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

from worldcup.engine.elo import expected_score

DEFAULT_INITIAL_RATING = 1500.0
DEFAULT_HOME_ADV = 100.0

_FINALS_60 = {"FIFA World Cup"}
_MAJOR_50 = {
    "Copa América",
    "Copa America",
    "UEFA Euro",
    "African Cup of Nations",
    "AFC Asian Cup",
    "CONCACAF Championship",
    "Gold Cup",
    "Oceania Nations Cup",
    "Confederations Cup",
}
_LEAGUE_40 = {"UEFA Nations League", "CONCACAF Nations League"}


def k_factor(tournament: str) -> float:
    if tournament in _FINALS_60:
        return 60.0
    if tournament in _MAJOR_50:
        return 50.0
    if tournament in _LEAGUE_40 or "qualification" in tournament.lower():
        return 40.0
    if tournament == "Friendly":
        return 20.0
    return 30.0


def goal_index(margin: int) -> float:
    m = abs(margin)
    if m <= 1:
        return 1.0
    if m == 2:
        return 1.5
    return (11.0 + m) / 8.0


def update_pair(
    rating_home: float,
    rating_away: float,
    home_score: int,
    away_score: int,
    k: float,
    neutral: bool,
    home_adv: float = DEFAULT_HOME_ADV,
) -> tuple[float, float]:
    dr = rating_home - rating_away + (0.0 if neutral else home_adv)
    we = expected_score(dr)
    if home_score > away_score:
        w = 1.0
    elif home_score == away_score:
        w = 0.5
    else:
        w = 0.0
    delta = k * goal_index(home_score - away_score) * (w - we)
    return rating_home + delta, rating_away - delta


@dataclass(frozen=True)
class ReplayMatch:
    date: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool


def load_results(path: str | Path) -> list[ReplayMatch]:
    out: list[ReplayMatch] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            home_score = (row.get("home_score") or "").strip()
            away_score = (row.get("away_score") or "").strip()
            if not home_score.isdigit() or not away_score.isdigit():
                continue
            out.append(
                ReplayMatch(
                    date=row["date"].strip(),
                    home_team=row["home_team"].strip(),
                    away_team=row["away_team"].strip(),
                    home_score=int(home_score),
                    away_score=int(away_score),
                    tournament=(row.get("tournament") or "").strip(),
                    neutral=(row.get("neutral") or "").strip().upper() == "TRUE",
                )
            )
    return out


def replay(
    matches: list[ReplayMatch],
    initial: float = DEFAULT_INITIAL_RATING,
    home_adv: float = DEFAULT_HOME_ADV,
) -> tuple[list[tuple[ReplayMatch, float, float]], dict[str, float]]:
    ratings: dict[str, float] = {}
    replayed: list[tuple[ReplayMatch, float, float]] = []
    for match in sorted(matches, key=lambda m: m.date):
        rating_home = ratings.get(match.home_team, initial)
        rating_away = ratings.get(match.away_team, initial)
        replayed.append((match, rating_home, rating_away))
        new_home, new_away = update_pair(
            rating_home,
            rating_away,
            match.home_score,
            match.away_score,
            k=k_factor(match.tournament),
            neutral=match.neutral,
            home_adv=home_adv,
        )
        ratings[match.home_team] = new_home
        ratings[match.away_team] = new_away
    return replayed, ratings


def main(argv: list[str] | None = None) -> int:
    from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
    from worldcup.collectors.team_aliases import canonicalize_team

    parser = argparse.ArgumentParser(description="Replay ratings and compare with official eloratings")
    parser.add_argument("--source", default="data/probe/intl_results_martj42.csv")
    parser.add_argument("--elo", default="data/probe/elo_world.tsv")
    parser.add_argument("--aliases", default="data/probe/elo_teams.tsv")
    parser.add_argument("--top", type=int, default=10, help="official top-N to check")
    parser.add_argument("--pool", type=int, default=30, help="replayed top-M pool for overlap")
    args = parser.parse_args(argv)

    replayed, ratings = replay(load_results(args.source))
    official = parse_elo_ratings(Path(args.elo).read_text(encoding="utf-8"))
    aliases = parse_elo_team_aliases(Path(args.aliases).read_text(encoding="utf-8"))
    code_by_canonical = {canonicalize_team(name): code for name, code in aliases.items()}

    replayed_by_code: dict[str, float] = {}
    for team, rating in ratings.items():
        code = code_by_canonical.get(canonicalize_team(team))
        if code is not None:
            replayed_by_code[code] = max(rating, replayed_by_code.get(code, rating))
    replay_rank = {
        code: idx + 1
        for idx, (code, _) in enumerate(
            sorted(replayed_by_code.items(), key=lambda item: -item[1])
        )
    }

    top_official = sorted(official.values(), key=lambda r: r.rank)[: args.top]
    lines = []
    hits = 0
    for entry in top_official:
        rank = replay_rank.get(entry.code)
        if rank is not None and rank <= args.pool:
            hits += 1
        lines.append(
            {
                "code": entry.code,
                "official_rank": entry.rank,
                "official_rating": entry.rating,
                "replay_rank": rank,
                "replay_rating": round(replayed_by_code.get(entry.code, 0.0), 1),
            }
        )
    print(
        json.dumps(
            {
                "matches_replayed": len(replayed),
                "teams_rated": len(ratings),
                "teams_mapped_to_codes": len(replayed_by_code),
                "official_top": args.top,
                "replay_pool": args.pool,
                "overlap_hits": hits,
                "detail": lines,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
