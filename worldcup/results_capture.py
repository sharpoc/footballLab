"""Capture finished-match scores from the cached openfootball feed.

只读本地缓存，不联网；输出写入被忽略的 data/local/results/。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from worldcup.collectors.models import MatchResult
from worldcup.collectors.openfootball import parse_openfootball_results

COLUMNS = (
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_canonical",
    "away_canonical",
    "home_score",
    "away_score",
    "captured_at",
)


def _key(row: dict) -> tuple:
    return (row["kickoff_at_utc"], row["home_canonical"], row["away_canonical"])


def _to_row(result: MatchResult, captured_at: str) -> dict:
    return {
        "kickoff_at_utc": result.kickoff_at_utc.isoformat(),
        "home_team": result.home_team_name,
        "away_team": result.away_team_name,
        "home_canonical": result.home_canonical or "",
        "away_canonical": result.away_canonical or "",
        "home_score": str(result.home_score),
        "away_score": str(result.away_score),
        "captured_at": captured_at,
    }


def upsert_results(
    results: list[MatchResult],
    existing_rows: list[dict],
    captured_at: str,
) -> tuple[list[dict], int, int]:
    by_key = {_key(row): dict(row) for row in existing_rows}
    added = updated = 0
    for result in results:
        row = _to_row(result, captured_at)
        key = _key(row)
        if key not in by_key:
            by_key[key] = row
            added += 1
        elif (by_key[key]["home_score"], by_key[key]["away_score"]) != (
            row["home_score"],
            row["away_score"],
        ):
            by_key[key] = row
            updated += 1
    return sorted(by_key.values(), key=_key), added, updated


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture finished scores into local results csv")
    parser.add_argument("--source", default="data/cache/openfootball_2026.json")
    parser.add_argument("--out", default="data/local/results/wc2026_results.csv")
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    results = parse_openfootball_results(raw)
    out = Path(args.out)
    rows, added, updated = upsert_results(
        results, _load_rows(out), datetime.now(timezone.utc).isoformat()
    )
    _write_rows(rows, out)
    print(
        json.dumps(
            {"finished": len(results), "added": added, "updated": updated, "total": len(rows)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
