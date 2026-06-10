"""Offline backtest for the worldcup engine.

只读本地历史 CSV，不联网，不参与线上 pipeline。输出仅为研究指标，
不包含任何下注金额、仓位或执行建议。
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from math import log
from pathlib import Path

from worldcup.config import load_config
from worldcup.engine import elo, ensemble, handicap, poisson
from worldcup.engine.odds import devig

OUTCOMES = ("home", "draw", "away")
OU_LINE = 2.5
REQUIRED_COLUMNS = (
    "match_id",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_elo_before",
    "away_elo_before",
)


@dataclass(frozen=True)
class BacktestMatch:
    match_id: str
    kickoff_at_utc: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home_elo_before: float
    away_elo_before: float
    neutral: bool = True
    odds_1x2: dict[str, float] | None = None
    odds_ou: dict[str, float] | None = None
    ah_line: float | None = None
    odds_ah: dict[str, float] | None = None


def _opt_float(row: dict, key: str) -> float | None:
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def _market_dict(row: dict, keys: dict[str, str]) -> dict[str, float] | None:
    values = {name: _opt_float(row, column) for name, column in keys.items()}
    if any(v is None for v in values.values()):
        return None
    return values


def load_matches(path: str | Path) -> list[BacktestMatch]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"missing required columns: {missing}")
        matches: list[BacktestMatch] = []
        for line_no, row in enumerate(reader, start=2):
            for column in REQUIRED_COLUMNS:
                if not (row.get(column) or "").strip():
                    raise ValueError(f"row {line_no}: missing value for {column}")
            ah_line = _opt_float(row, "ah_line")
            odds_ah = _market_dict(row, {"home": "odds_ah_home", "away": "odds_ah_away"})
            matches.append(
                BacktestMatch(
                    match_id=row["match_id"].strip(),
                    kickoff_at_utc=row["kickoff_at_utc"].strip(),
                    home_team=row["home_team"].strip(),
                    away_team=row["away_team"].strip(),
                    home_score=int(row["home_score"]),
                    away_score=int(row["away_score"]),
                    home_elo_before=float(row["home_elo_before"]),
                    away_elo_before=float(row["away_elo_before"]),
                    neutral=(row.get("neutral") or "1").strip() != "0",
                    odds_1x2=_market_dict(
                        row, {"home": "odds_home", "draw": "odds_draw", "away": "odds_away"}
                    ),
                    odds_ou=_market_dict(row, {"over": "odds_over", "under": "odds_under"}),
                    ah_line=ah_line if odds_ah is not None else None,
                    odds_ah=odds_ah if ah_line is not None else None,
                )
            )
    return matches


def brier_multiclass(probs: dict[str, float], outcome: str) -> float:
    return sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())


def log_loss(probs: dict[str, float], outcome: str, eps: float = 1e-12) -> float:
    p = min(max(probs[outcome], eps), 1.0)
    return -log(p)


def calibration_bins(records: list[tuple[float, bool]], n_bins: int = 10) -> list[dict]:
    raw = [
        {"lo": i / n_bins, "hi": (i + 1) / n_bins, "n": 0, "p_sum": 0.0, "hits": 0}
        for i in range(n_bins)
    ]
    for p, hit in records:
        bucket = raw[min(int(p * n_bins), n_bins - 1)]
        bucket["n"] += 1
        bucket["p_sum"] += p
        bucket["hits"] += int(hit)
    return [
        {
            "range": [b["lo"], b["hi"]],
            "n": b["n"],
            "p_mean": b["p_sum"] / b["n"],
            "hit_rate": b["hits"] / b["n"],
        }
        for b in raw
        if b["n"]
    ]


def outcome_1x2(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def ah_realized_return(goal_diff: int, line: float, odds: float) -> float:
    """Realized profit per 1 unit stake for the home side of an AH bet."""
    return handicap.ev_handicap({goal_diff: 1.0}, line, odds)
