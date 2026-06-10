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


def replay_match(match: BacktestMatch, cfg: dict) -> dict:
    dr = match.home_elo_before - match.away_elo_before
    if not match.neutral:
        dr += cfg["elo"]["home_adv"]
    market_1x2 = devig(match.odds_1x2) if match.odds_1x2 else None
    market_ou = devig(match.odds_ou) if match.odds_ou else None
    mu_used = poisson.blended_mu(
        market_ou["over"] if market_ou else None, OU_LINE, cfg["poisson"]
    )
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_used)
    matrix, _ = poisson.score_matrix(lh, la, cfg["poisson"])
    poisson_1x2 = poisson.probs_1x2(matrix)
    elo_1x2 = elo.win_draw_loss(
        match.home_elo_before,
        match.away_elo_before,
        neutral=match.neutral,
        cfg=cfg["elo"],
    )
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2, poisson_1x2, cfg["ensemble"]["w_elo"], cfg["ensemble"]["w_poisson"]
    )
    p_over = poisson.prob_over(matrix, OU_LINE)
    return {
        "dr": dr,
        "mu_used": mu_used,
        "model_1x2": combined_1x2,
        "market_1x2": market_1x2,
        "model_ou": {"over": p_over, "under": 1.0 - p_over},
        "market_ou": market_ou,
        "diff_dist": handicap.diff_distribution(matrix),
    }


EV_BUCKETS = ((-9.0, 0.0), (0.0, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 9.0))
ODDS_BUCKETS = ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1000.0))
DR_BUCKETS = ((0.0, 100.0), (100.0, 200.0), (200.0, 300.0), (300.0, 10000.0))


def _mean_metrics(rows: list[tuple[dict[str, float], str]]) -> dict:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None}
    return {
        "n": len(rows),
        "brier": sum(brier_multiclass(p, o) for p, o in rows) / len(rows),
        "log_loss": sum(log_loss(p, o) for p, o in rows) / len(rows),
    }


def _bucket_rows(buckets: tuple, value: float) -> int | None:
    for idx, (lo, hi) in enumerate(buckets):
        if lo <= value < hi:
            return idx
    return None


def run_backtest(matches: list[BacktestMatch], cfg: dict, min_sample: int = 200) -> dict:
    model_1x2_rows: list[tuple[dict, str]] = []
    market_1x2_rows: list[tuple[dict, str]] = []
    uniform_1x2_rows: list[tuple[dict, str]] = []
    model_ou_rows: list[tuple[dict, str]] = []
    market_ou_rows: list[tuple[dict, str]] = []
    uniform_ou_rows: list[tuple[dict, str]] = []
    calibration_records: list[tuple[float, bool]] = []
    ev_buckets = [{"n": 0, "return_sum": 0.0} for _ in EV_BUCKETS]
    odds_buckets = [{"n": 0, "hits": 0, "implied_sum": 0.0} for _ in ODDS_BUCKETS]
    ah_buckets = [{"n": 0, "return_sum": 0.0} for _ in EV_BUCKETS]
    dr_buckets = [{"n": 0, "total_sum": 0, "mu_sum": 0.0} for _ in DR_BUCKETS]
    n_1x2 = n_ou = n_ah = 0

    uniform_1x2 = {k: 1 / 3 for k in OUTCOMES}
    uniform_ou = {"over": 0.5, "under": 0.5}

    for match in matches:
        replay = replay_match(match, cfg)
        result = outcome_1x2(match.home_score, match.away_score)
        total_goals = match.home_score + match.away_score
        ou_result = "over" if total_goals > OU_LINE else "under"

        model_1x2_rows.append((replay["model_1x2"], result))
        uniform_1x2_rows.append((uniform_1x2, result))
        model_ou_rows.append((replay["model_ou"], ou_result))
        uniform_ou_rows.append((uniform_ou, ou_result))
        for selection in OUTCOMES:
            calibration_records.append(
                (replay["model_1x2"][selection], selection == result)
            )

        if match.odds_1x2:
            n_1x2 += 1
            market_1x2_rows.append((replay["market_1x2"], result))
            for selection in OUTCOMES:
                odds_value = match.odds_1x2[selection]
                ev_value = replay["model_1x2"][selection] * odds_value - 1.0
                realized = (odds_value - 1.0) if selection == result else -1.0
                ev_idx = _bucket_rows(EV_BUCKETS, ev_value)
                if ev_idx is not None:
                    ev_buckets[ev_idx]["n"] += 1
                    ev_buckets[ev_idx]["return_sum"] += realized
                odds_idx = _bucket_rows(ODDS_BUCKETS, odds_value)
                if odds_idx is not None:
                    odds_buckets[odds_idx]["n"] += 1
                    odds_buckets[odds_idx]["hits"] += int(selection == result)
                    odds_buckets[odds_idx]["implied_sum"] += 1.0 / odds_value

        if match.odds_ou:
            n_ou += 1
            market_ou_rows.append((replay["market_ou"], ou_result))

        if match.odds_ah and match.ah_line is not None:
            n_ah += 1
            predicted = handicap.ev_handicap(
                replay["diff_dist"], match.ah_line, match.odds_ah["home"]
            )
            realized = ah_realized_return(
                match.home_score - match.away_score, match.ah_line, match.odds_ah["home"]
            )
            idx = _bucket_rows(EV_BUCKETS, predicted)
            if idx is not None:
                ah_buckets[idx]["n"] += 1
                ah_buckets[idx]["return_sum"] += realized

        dr_idx = _bucket_rows(DR_BUCKETS, abs(replay["dr"]))
        if dr_idx is not None:
            dr_buckets[dr_idx]["n"] += 1
            dr_buckets[dr_idx]["total_sum"] += total_goals
            dr_buckets[dr_idx]["mu_sum"] += replay["mu_used"]

    return {
        "sample": {
            "n_matches": len(matches),
            "n_1x2": n_1x2,
            "n_ou": n_ou,
            "n_ah": n_ah,
            "min_sample": min_sample,
            "sample_too_small": len(matches) < min_sample,
        },
        "markets": {
            "1x2": {
                "model": _mean_metrics(model_1x2_rows),
                "market": _mean_metrics(market_1x2_rows),
                "uniform": _mean_metrics(uniform_1x2_rows),
            },
            "ou_2_5": {
                "model": _mean_metrics(model_ou_rows),
                "market": _mean_metrics(market_ou_rows),
                "uniform": _mean_metrics(uniform_ou_rows),
            },
        },
        "calibration_1x2": calibration_bins(calibration_records),
        "ev_buckets_1x2": [
            {
                "range": list(EV_BUCKETS[i]),
                "n": b["n"],
                "mean_return": (b["return_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(ev_buckets)
        ],
        "odds_buckets_1x2": [
            {
                "range": list(ODDS_BUCKETS[i]),
                "n": b["n"],
                "hit_rate": (b["hits"] / b["n"]) if b["n"] else None,
                "implied_mean": (b["implied_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(odds_buckets)
        ],
        "ah_ev_buckets": [
            {
                "range": list(EV_BUCKETS[i]),
                "n": b["n"],
                "mean_return": (b["return_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(ah_buckets)
        ],
        "totals_by_abs_dr": [
            {
                "range": list(DR_BUCKETS[i]),
                "n": b["n"],
                "mean_total_goals": (b["total_sum"] / b["n"]) if b["n"] else None,
                "mean_mu_used": (b["mu_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(dr_buckets)
        ],
        "notes": "research metrics only; no staking advice",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline backtest for the worldcup engine")
    parser.add_argument("--csv", required=True, help="historical matches csv path")
    parser.add_argument("--config", default=None, help="settings.yaml path override")
    parser.add_argument("--out", default="data/local/backtest/report.json")
    parser.add_argument("--min-sample", type=int, default=200)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    report = run_backtest(load_matches(args.csv), cfg, min_sample=args.min_sample)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["sample"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
