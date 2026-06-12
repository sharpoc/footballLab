"""Normalize OddsHarvester (OddsPortal) WC2022 exports for backtest CSV.

一次性离线 backfill 工具：只读本地爬取产物，不联网。
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from worldcup.collectors.team_aliases import canonicalize_team


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
