"""Convert international results history into the offline backtest CSV contract.

This module reads local files only. It writes match results and replayed
pre-match Elo ratings; historical odds columns are intentionally left absent.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from worldcup.collectors.eloratings import parse_elo_team_aliases
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.elo_replay import (
    DEFAULT_HOME_ADV,
    DEFAULT_INITIAL_RATING,
    ReplayMatch,
    load_results,
    replay,
)

OUTPUT_COLUMNS = (
    "match_id",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_elo_before",
    "away_elo_before",
    "neutral",
)


def known_canonicals(alias_text: str) -> set[str]:
    return {canonicalize_team(name) for name in parse_elo_team_aliases(alias_text)}


def _slug(match: ReplayMatch) -> str:
    return f"{match.date}_{match.home_team}_{match.away_team}".replace(" ", "_").lower()


def convert(
    matches: list[ReplayMatch],
    known: set[str],
    since: str,
    initial: float = DEFAULT_INITIAL_RATING,
    home_adv: float = DEFAULT_HOME_ADV,
) -> list[dict]:
    replayed, _ = replay(matches, initial=initial, home_adv=home_adv)
    rows: list[dict] = []
    for match, rating_home, rating_away in replayed:
        if match.date < since:
            continue
        if (
            canonicalize_team(match.home_team) not in known
            or canonicalize_team(match.away_team) not in known
        ):
            continue
        rows.append(
            {
                "match_id": _slug(match),
                "kickoff_at_utc": f"{match.date}T12:00:00Z",
                "home_team": match.home_team,
                "away_team": match.away_team,
                "home_score": match.home_score,
                "away_score": match.away_score,
                "home_elo_before": round(rating_home, 1),
                "away_elo_before": round(rating_away, 1),
                "neutral": 1 if match.neutral else 0,
            }
        )
    return rows


def write_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build backtest CSV from historical results")
    parser.add_argument("--source", default="data/probe/intl_results_martj42.csv")
    parser.add_argument("--aliases", default="data/probe/elo_teams.tsv")
    parser.add_argument("--out", default="data/local/backtest/intl_history.csv")
    parser.add_argument("--since", default="2010-01-01")
    args = parser.parse_args(argv)

    matches = load_results(args.source)
    known = known_canonicals(Path(args.aliases).read_text(encoding="utf-8"))
    rows = convert(matches, known, since=args.since)
    write_csv(rows, args.out)
    print(
        json.dumps(
            {
                "source_rows": len(matches),
                "output_rows": len(rows),
                "since": args.since,
                "out": args.out,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
