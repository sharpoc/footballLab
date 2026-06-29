"""Join local CSL snapshots with finished CSL results into a backtest CSV.

This module is local-only: it reads archived snapshot JSON and local club result
CSV files, writes ignored backtest artifacts, and never fetches live data.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.club_rating import ClubResult, load_club_results_csv
from worldcup.eval_data import OUTPUT_COLUMNS

DEFAULT_COMPETITION_ID = "csl_2026"


def _parse_utc(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_snapshots(history_dir: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(history_dir).glob("snapshot_*.json"))
    ]


def closing_match_entry(
    snapshots: list[dict[str, Any]],
    match_date: str,
    home_canonical: str,
    away_canonical: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_at: datetime | None = None
    for snapshot in snapshots:
        snapshot_at = snapshot.get("snapshot_at")
        if not snapshot_at:
            continue
        at = _parse_utc(str(snapshot_at))
        for entry in snapshot.get("matches") or []:
            if not isinstance(entry, dict):
                continue
            kickoff_at = entry.get("kickoff_at_utc")
            if not kickoff_at or str(kickoff_at)[:10] != match_date:
                continue
            if entry.get("home_canonical") != home_canonical:
                continue
            if entry.get("away_canonical") != away_canonical:
                continue
            if at >= _parse_utc(str(kickoff_at)):
                continue
            if best_at is None or at > best_at:
                best = entry
                best_at = at
    return best


def _market_odds(entry: dict[str, Any], market: str, selections: tuple[str, ...]) -> dict[str, float]:
    odds = ((entry.get("market") or {}).get(market) or {}).get("odds") or {}
    if all(selection in odds for selection in selections):
        return {selection: odds[selection] for selection in selections}
    return {}


def _ou_fields(entry: dict[str, Any]) -> dict[str, Any]:
    block = ((entry.get("market") or {}).get("ou_2_5")) or {}
    odds = block.get("odds") or {}
    if "over" not in odds or "under" not in odds:
        return {"ou_line": "", "odds_over": "", "odds_under": ""}
    return {
        "ou_line": block.get("line", 2.5),
        "odds_over": odds["over"],
        "odds_under": odds["under"],
    }


def _ah_main_fields(entry: dict[str, Any]) -> dict[str, Any]:
    block = ((entry.get("market") or {}).get("ah_main")) or {}
    line = block.get("line_home")
    odds = block.get("odds") or {}
    if line is None or "home" not in odds or "away" not in odds:
        return {"ah_line": "", "odds_ah_home": "", "odds_ah_away": ""}
    return {
        "ah_line": line,
        "odds_ah_home": odds["home"],
        "odds_ah_away": odds["away"],
    }


def _elo_fields(entry: dict[str, Any]) -> tuple[Any, Any]:
    elo = entry.get("elo") if isinstance(entry.get("elo"), dict) else {}
    return elo.get("home", ""), elo.get("away", "")


def build_rows(
    snapshots: list[dict[str, Any]],
    results: list[ClubResult],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for result in results:
        entry = closing_match_entry(
            snapshots,
            result.date,
            result.home_canonical,
            result.away_canonical,
        )
        if entry is None:
            skipped += 1
            continue
        odds_1x2 = _market_odds(entry, "1x2", ("home", "draw", "away"))
        home_elo, away_elo = _elo_fields(entry)
        rows.append(
            {
                "match_id": (
                    f"{result.competition_id}:{result.date}:"
                    f"{result.home_canonical}:{result.away_canonical}"
                ),
                "kickoff_at_utc": entry.get("kickoff_at_utc") or f"{result.date}T00:00:00Z",
                "home_team": result.home_team,
                "away_team": result.away_team,
                "home_score": result.home_score,
                "away_score": result.away_score,
                "home_elo_before": home_elo,
                "away_elo_before": away_elo,
                "neutral": 1 if result.neutral else 0,
                "odds_home": odds_1x2.get("home", ""),
                "odds_draw": odds_1x2.get("draw", ""),
                "odds_away": odds_1x2.get("away", ""),
                **_ou_fields(entry),
                **_ah_main_fields(entry),
            }
        )
    return rows, skipped


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build CSL odds-bearing eval csv from local snapshot history.",
    )
    parser.add_argument("--history", default="data/local/diagnostics/csl_history")
    parser.add_argument("--results", default="data/cache/club_results_csl_2026.csv")
    parser.add_argument("--out", default="data/local/backtest/csl_2026_eval.csv")
    parser.add_argument("--competition-id", "--competition", default=DEFAULT_COMPETITION_ID)
    args = parser.parse_args(argv)

    snapshots = load_snapshots(args.history)
    results = load_club_results_csv(args.results, args.competition_id)
    rows, skipped = build_rows(snapshots, results)
    write_csv(rows, args.out)
    print(
        json.dumps(
            {
                "competition_id": args.competition_id,
                "snapshots": len(snapshots),
                "results": len(results),
                "joined": len(rows),
                "skipped_no_closing": skipped,
                "out": args.out,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
