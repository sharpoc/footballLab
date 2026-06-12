"""Normalize OddsHarvester (OddsPortal) WC2022 exports for backtest CSV.

一次性离线 backfill 工具：只读本地爬取产物，不联网。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date as _date
import json
from pathlib import Path
import re

from worldcup.collectors.team_aliases import canonicalize_team

CSV_COLUMNS = (
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
    "ah_line",
    "odds_ah_home",
    "odds_ah_away",
    "open_odds_home",
    "open_odds_draw",
    "open_odds_away",
    "open_odds_over",
    "open_odds_under",
    "open_ah_line",
    "open_odds_ah_home",
    "open_odds_ah_away",
)


@dataclass
class NormalizedMatch:
    date: str
    home_canonical: str
    away_canonical: str
    home_team: str = ""
    away_team: str = ""
    home_score: int | None = None
    away_score: int | None = None
    open_1x2: dict | None = None
    close_1x2: dict | None = None
    open_ou: dict | None = None
    close_ou: dict | None = None
    open_ah_line: float | None = None
    open_ah: dict | None = None
    close_ah_line: float | None = None
    close_ah: dict | None = None


def _key(date: str, home_c: str, away_c: str) -> tuple[str, str, str]:
    return (date, home_c, away_c)


def _score(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text else None


def _base(raw: dict) -> NormalizedMatch:
    home = str(raw["home_team"]).strip()
    away = str(raw["away_team"]).strip()
    return NormalizedMatch(
        date=str(raw["match_date"])[:10],
        home_canonical=canonicalize_team(home),
        away_canonical=canonicalize_team(away),
        home_team=home,
        away_team=away,
        home_score=_score(raw.get("home_score")),
        away_score=_score(raw.get("away_score")),
    )


def _decimal(value) -> float | None:
    if value is None:
        return None
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", str(value))
    return float(match.group(1)) if match else None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _opening(entry: dict, idx: int) -> float | None:
    history = entry.get("odds_history_data") or []
    if idx >= len(history):
        return None
    return _decimal((history[idx].get("opening_odds") or {}).get("odds"))


def _aggregate(entries: list[dict], labels: tuple[tuple[str, str, int], ...]) -> tuple[dict | None, dict | None]:
    close: dict[str, float] = {}
    open_: dict[str, float] = {}
    for name, raw_key, idx in labels:
        close_values = []
        open_values = []
        for entry in entries:
            close_value = _decimal(entry.get(raw_key))
            if close_value is not None:
                close_values.append(close_value)
            open_value = _opening(entry, idx)
            if open_value is not None:
                open_values.append(open_value)
        close_mean = _mean(close_values)
        open_mean = _mean(open_values)
        if close_mean is not None:
            close[name] = close_mean
        if open_mean is not None:
            open_[name] = open_mean
    return (close if len(close) == len(labels) else None, open_ if len(open_) == len(labels) else None)


def normalize_1x2(raw: dict) -> NormalizedMatch:
    row = _base(raw)
    close, open_ = _aggregate(raw.get("1x2_market") or [], (("home", "1", 0), ("draw", "X", 1), ("away", "2", 2)))
    row.close_1x2 = close
    row.open_1x2 = open_
    return row


def normalize_ou(raw: dict, target_line: float = 2.5) -> NormalizedMatch:
    row = _base(raw)
    key = f"over_under_{str(target_line).replace('.', '_')}_market"
    close, open_ = _aggregate(raw.get(key) or [], (("over", "odds_over", 0), ("under", "odds_under", 1)))
    row.close_ou = close
    row.open_ou = open_
    return row


def _line_from_key(key: str) -> float | None:
    prefix = "asian_handicap_"
    suffix = "_market"
    if not (key.startswith(prefix) and key.endswith(suffix)):
        return None
    value = key[len(prefix) : -len(suffix)]
    sign = -1.0 if value.startswith("-") else 1.0
    value = value.lstrip("+-")
    return sign * float(value.replace("_", "."))


def _line_from_entry(key: str, entry: dict) -> float | None:
    name = str(entry.get("submarket_name") or "")
    match = re.search(r"Asian Handicap\s+([+-]?\d+(?:\.\d+)?)", name)
    if match:
        return float(match.group(1))
    return _line_from_key(key)


def _closest_line(lines: list[tuple[float, dict | None, dict | None]], when: str) -> tuple[float, dict] | None:
    best: tuple[float, dict] | None = None
    best_gap: float | None = None
    for line, open_values, close_values in lines:
        values = open_values if when == "open" else close_values
        if not values:
            continue
        gap = abs(float(values["home"]) - float(values["away"]))
        if best_gap is None or gap < best_gap:
            best = (line, values)
            best_gap = gap
    return best


def normalize_ah(raw: dict) -> NormalizedMatch:
    row = _base(raw)
    lines: list[tuple[float, dict | None, dict | None]] = []
    for key, entries in raw.items():
        if not (key.startswith("asian_handicap_") and key.endswith("_market")):
            continue
        by_line: dict[float, list[dict]] = {}
        for entry in entries or []:
            line = _line_from_entry(key, entry)
            if line is not None:
                by_line.setdefault(line, []).append(entry)
        for line, line_entries in by_line.items():
            close, open_ = _aggregate(line_entries, (("home", "team1_handicap", 0), ("away", "team2_handicap", 1)))
            lines.append((line, open_, close))

    open_main = _closest_line(lines, "open")
    if open_main:
        row.open_ah_line, row.open_ah = open_main
    close_main = _closest_line(lines, "close")
    if close_main:
        row.close_ah_line, row.close_ah = close_main
    return row


def merge_markets(
    rows_1x2: list[NormalizedMatch],
    rows_ah: list[NormalizedMatch],
    rows_ou: list[NormalizedMatch],
) -> list[NormalizedMatch]:
    by_key_ah = {_key(r.date, r.home_canonical, r.away_canonical): r for r in rows_ah}
    by_key_ou = {_key(r.date, r.home_canonical, r.away_canonical): r for r in rows_ou}
    out = []
    for row in rows_1x2:
        key = _key(row.date, row.home_canonical, row.away_canonical)
        ah = by_key_ah.get(key)
        if ah is not None:
            row.open_ah_line, row.open_ah = ah.open_ah_line, ah.open_ah
            row.close_ah_line, row.close_ah = ah.close_ah_line, ah.close_ah
        ou = by_key_ou.get(key)
        if ou is not None:
            row.open_ou, row.close_ou = ou.open_ou, ou.close_ou
        out.append(row)
    return out


def _date_near(a: str, b: str) -> bool:
    da, db = _date.fromisoformat(a), _date.fromisoformat(b)
    return abs((da - db).days) <= 1


def _fmt(value) -> str:
    return "" if value is None else str(value)


def join_with_history(normalized: list[NormalizedMatch], intl_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Use scraped WC2022 rows as the driver, matched to intl history by team/date."""
    index: dict[tuple[str, str], list[dict]] = {}
    for row in intl_rows:
        key = (canonicalize_team(row["home_team"]), canonicalize_team(row["away_team"]))
        index.setdefault(key, []).append(row)

    joined: list[dict] = []
    unmatched: list[dict] = []
    for match in normalized:
        candidates = index.get((match.home_canonical, match.away_canonical), [])
        hit = next((row for row in candidates if _date_near(row["kickoff_at_utc"][:10], match.date)), None)
        if hit is None:
            unmatched.append(
                {
                    "date": match.date,
                    "home_canonical": match.home_canonical,
                    "away_canonical": match.away_canonical,
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                }
            )
            continue

        rec = {key: hit.get(key, "") for key in CSV_COLUMNS[:9]}
        close_1x2, open_1x2 = match.close_1x2 or {}, match.open_1x2 or {}
        close_ou, open_ou = match.close_ou or {}, match.open_ou or {}
        close_ah, open_ah = match.close_ah or {}, match.open_ah or {}
        rec.update(
            {
                "odds_home": _fmt(close_1x2.get("home")),
                "odds_draw": _fmt(close_1x2.get("draw")),
                "odds_away": _fmt(close_1x2.get("away")),
                "odds_over": _fmt(close_ou.get("over")),
                "odds_under": _fmt(close_ou.get("under")),
                "ah_line": _fmt(match.close_ah_line),
                "odds_ah_home": _fmt(close_ah.get("home")),
                "odds_ah_away": _fmt(close_ah.get("away")),
                "open_odds_home": _fmt(open_1x2.get("home")),
                "open_odds_draw": _fmt(open_1x2.get("draw")),
                "open_odds_away": _fmt(open_1x2.get("away")),
                "open_odds_over": _fmt(open_ou.get("over")),
                "open_odds_under": _fmt(open_ou.get("under")),
                "open_ah_line": _fmt(match.open_ah_line),
                "open_odds_ah_home": _fmt(open_ah.get("home")),
                "open_odds_ah_away": _fmt(open_ah.get("away")),
            }
        )
        joined.append(rec)
    return joined, unmatched


def write_backtest_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def _load_export(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return data.get("matches", [])


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Join scraped WC2022 odds with intl history into backtest CSV")
    parser.add_argument("--raw-1x2", required=True, help="OddsHarvester 1x2 JSON export")
    parser.add_argument("--raw-ah", required=True, help="OddsHarvester Asian Handicap JSON export")
    parser.add_argument("--raw-ou", required=True, help="OddsHarvester Over/Under JSON export")
    parser.add_argument("--history", default="data/local/backtest/intl_history.csv")
    parser.add_argument("--out", default="data/local/backtest/wc2022_history.csv")
    args = parser.parse_args(argv)

    rows_1x2 = [normalize_1x2(item) for item in _load_export(args.raw_1x2)]
    rows_ah = [normalize_ah(item) for item in _load_export(args.raw_ah)]
    rows_ou = [normalize_ou(item) for item in _load_export(args.raw_ou)]
    merged = merge_markets(rows_1x2, rows_ah, rows_ou)

    with open(args.history, newline="", encoding="utf-8") as fh:
        intl_rows = list(csv.DictReader(fh))
    joined, unmatched = join_with_history(merged, intl_rows)
    write_backtest_csv(joined, args.out)
    print(json.dumps({"scraped": len(merged), "joined": len(joined), "unmatched": unmatched}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
