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
