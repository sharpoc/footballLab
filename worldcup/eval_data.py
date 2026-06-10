"""Join archived snapshots with captured results into an odds-bearing backtest CSV.

只读 data/local/history/ 与 data/local/results/，不联网。
已知局限：neutral 一律按 1 处理（snapshot 未保存东道主修正），
AH 不进评估（snapshot market 块无 AH 聚合赔率）。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

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
    "odds_home",
    "odds_draw",
    "odds_away",
    "odds_over",
    "odds_under",
)


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_snapshots(history_dir: str | Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(history_dir).glob("snapshot_*.json"))
    ]


def closing_match_entry(
    snapshots: list[dict],
    kickoff_at_utc: str,
    home_canonical: str,
    away_canonical: str,
) -> dict | None:
    kickoff = _parse_at(kickoff_at_utc)
    best: dict | None = None
    best_at: datetime | None = None
    for snapshot in snapshots:
        snapshot_at = snapshot.get("snapshot_at")
        if not snapshot_at:
            continue
        at = _parse_at(snapshot_at)
        if at >= kickoff:
            continue
        for entry in snapshot.get("matches", []):
            if (
                entry.get("home_canonical") == home_canonical
                and entry.get("away_canonical") == away_canonical
                and (entry.get("kickoff_at_utc") or "")[:10] == kickoff_at_utc[:10]
            ):
                if best_at is None or at > best_at:
                    best, best_at = entry, at
    return best


def _market_odds(entry: dict, market: str, selections: tuple[str, ...]) -> dict[str, float]:
    odds = ((entry.get("market") or {}).get(market) or {}).get("odds") or {}
    if all(selection in odds for selection in selections):
        return {selection: odds[selection] for selection in selections}
    return {}


def build_rows(snapshots: list[dict], results_rows: list[dict]) -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    for result in results_rows:
        entry = closing_match_entry(
            snapshots,
            result["kickoff_at_utc"],
            result["home_canonical"],
            result["away_canonical"],
        )
        if entry is None:
            skipped += 1
            continue
        odds_1x2 = _market_odds(entry, "1x2", ("home", "draw", "away"))
        odds_ou = _market_odds(entry, "ou_2_5", ("over", "under"))
        rows.append(
            {
                "match_id": (
                    f"{result['kickoff_at_utc'][:10]}_"
                    f"{result['home_canonical']}_{result['away_canonical']}"
                ),
                "kickoff_at_utc": result["kickoff_at_utc"],
                "home_team": result["home_team"],
                "away_team": result["away_team"],
                "home_score": result["home_score"],
                "away_score": result["away_score"],
                "home_elo_before": entry["elo"]["home"],
                "away_elo_before": entry["elo"]["away"],
                "neutral": 1,
                "odds_home": odds_1x2.get("home", ""),
                "odds_draw": odds_1x2.get("draw", ""),
                "odds_away": odds_1x2.get("away", ""),
                "odds_over": odds_ou.get("over", ""),
                "odds_under": odds_ou.get("under", ""),
            }
        )
    return rows, skipped


def write_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build odds-bearing eval csv from local history")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--results", default="data/local/results/wc2026_results.csv")
    parser.add_argument("--out", default="data/local/backtest/wc2026_eval.csv")
    args = parser.parse_args(argv)

    with open(args.results, newline="", encoding="utf-8") as fh:
        results_rows = list(csv.DictReader(fh))
    snapshots = load_snapshots(args.history)
    rows, skipped = build_rows(snapshots, results_rows)
    write_csv(rows, args.out)
    print(
        json.dumps(
            {
                "snapshots": len(snapshots),
                "results": len(results_rows),
                "joined": len(rows),
                "skipped_no_closing": skipped,
                "out": args.out,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
