"""Line-movement bucket report for the WC2022 backtest CSV.

研究口径：单位回报（per 1 unit stake，与 backtest ah_ev_buckets 一致）、命中率、Brier。
不含资金字段，不构成投注建议。纯离线：只读本地 CSV 与 config。
"""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from worldcup.backtest import (
    BacktestMatch,
    ah_realized_return,
    brier_multiclass,
    load_matches,
    outcome_1x2,
    replay_match,
)
from worldcup.config import load_config
from worldcup.engine import handicap

BUCKETS = ("0.00", "0.25", "0.50", ">=0.75")
ODDS_BUCKETS_1X2 = ("<2%", "2-5%", "5-10%", ">=10%")


def move_bucket(abs_move: float) -> str:
    if abs_move < 0.125:
        return "0.00"
    if abs_move < 0.375:
        return "0.25"
    if abs_move < 0.625:
        return "0.50"
    return ">=0.75"


def odds_move_bucket(rel_move: float) -> str:
    """Bucket |close - open| / open for home-win odds."""
    if rel_move < 0.02:
        return "<2%"
    if rel_move < 0.05:
        return "2-5%"
    if rel_move < 0.10:
        return "5-10%"
    return ">=10%"


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _open_lines_by_id(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        value = (row.get("open_ah_line") or "").strip()
        if value:
            out[row["match_id"]] = float(value)
    return out


def _open_home_odds_by_id(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        value = (row.get("open_odds_home") or "").strip()
        if value:
            out[row["match_id"]] = float(value)
    return out


def _load_matches_from_rows(rows: list[dict]) -> list[BacktestMatch]:
    if not rows:
        return []
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as fh:
            tmp_path = fh.name
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return load_matches(tmp_path)
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _ah_signal(match: BacktestMatch, replayed: dict) -> dict | None:
    if match.ah_line is None or not match.odds_ah:
        return None
    dist = replayed["diff_dist"]
    inverted = {-diff: prob for diff, prob in dist.items()}
    home_ev = handicap.ev_handicap(dist, match.ah_line, match.odds_ah["home"])
    away_ev = handicap.ev_handicap(inverted, -match.ah_line, match.odds_ah["away"])
    side, ev = ("home", home_ev) if home_ev >= away_ev else ("away", away_ev)
    if ev <= 0:
        return None

    goal_diff = match.home_score - match.away_score
    if side == "home":
        realized = ah_realized_return(goal_diff, match.ah_line, match.odds_ah["home"])
    else:
        realized = ah_realized_return(-goal_diff, -match.ah_line, match.odds_ah["away"])
    return {"side": side, "ev": ev, "realized": realized}


def _1x2_signal(match: BacktestMatch, replayed: dict) -> dict | None:
    if not match.odds_1x2:
        return None
    model = replayed["model_1x2"]
    best_side = None
    best_ev = 0.0
    for side in ("home", "draw", "away"):
        ev = model[side] * match.odds_1x2[side] - 1.0
        if ev > best_ev:
            best_side = side
            best_ev = ev
    if best_side is None:
        return None

    actual = outcome_1x2(match.home_score, match.away_score)
    realized = match.odds_1x2[best_side] - 1.0 if best_side == actual else -1.0
    return {"side": best_side, "ev": best_ev, "hit": best_side == actual, "realized": realized}


def _bucket_table(grouped: dict[str, list[tuple[BacktestMatch, dict]]], order: tuple[str, ...]) -> list[dict]:
    out = []
    for bucket in order:
        entries = grouped[bucket]
        if not entries:
            continue
        ah_signals = [signal for signal in (_ah_signal(match, replayed) for match, replayed in entries) if signal]
        x2_signals = [signal for signal in (_1x2_signal(match, replayed) for match, replayed in entries) if signal]
        briers = [
            brier_multiclass(replayed["model_1x2"], outcome_1x2(match.home_score, match.away_score))
            for match, replayed in entries
        ]
        out.append(
            {
                "bucket": bucket,
                "n_matches": len(entries),
                "model_brier_1x2": _mean(briers),
                "ah": {
                    "n_signals": len(ah_signals),
                    "mean_ev": _mean([signal["ev"] for signal in ah_signals]),
                    "mean_return": _mean([signal["realized"] for signal in ah_signals]),
                },
                "1x2": {
                    "n_signals": len(x2_signals),
                    "hit_rate": _mean([1.0 if signal["hit"] else 0.0 for signal in x2_signals]),
                    "mean_return": _mean([signal["realized"] for signal in x2_signals]),
                },
            }
        )
    return out


def build_report(rows: list[dict], cfg: dict | None = None) -> dict:
    """Build a line-movement report from wc2022_history.csv DictReader rows."""
    if cfg is None:
        cfg = load_config("config/settings.yaml")

    matches = _load_matches_from_rows(rows)
    open_lines = _open_lines_by_id(rows)
    open_home_odds = _open_home_odds_by_id(rows)
    grouped_ah: dict[str, list[tuple[BacktestMatch, dict]]] = {bucket: [] for bucket in BUCKETS}
    grouped_1x2: dict[str, list[tuple[BacktestMatch, dict]]] = {bucket: [] for bucket in ODDS_BUCKETS_1X2}
    n_with_both_ah = 0
    n_with_1x2 = 0

    for match in matches:
        replayed = None
        open_line = open_lines.get(match.match_id)
        if open_line is not None and match.ah_line is not None:
            n_with_both_ah += 1
            replayed = replay_match(match, cfg)
            grouped_ah[move_bucket(abs(match.ah_line - open_line))].append((match, replayed))

        open_home = open_home_odds.get(match.match_id)
        if open_home is not None and match.odds_1x2:
            n_with_1x2 += 1
            if replayed is None:
                replayed = replay_match(match, cfg)
            rel_move = abs(match.odds_1x2["home"] - open_home) / open_home
            grouped_1x2[odds_move_bucket(rel_move)].append((match, replayed))

    return {
        "sample": {
            "n_rows": len(rows),
            "n_with_both_ah_lines": n_with_both_ah,
            "n_with_1x2_open_close": n_with_1x2,
        },
        "by_abs_move": _bucket_table(grouped_ah, BUCKETS),
        "by_1x2_move": _bucket_table(grouped_1x2, ODDS_BUCKETS_1X2),
        "notes": [
            "research-only; unit returns per 1 stake; no staking advice",
            "model params are current config/settings.yaml values, not 2022-era",
            "open/close lines scraped from OddsPortal (one-off, personal research)",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="WC2022 line-movement bucket report")
    parser.add_argument("--csv", default="data/local/backtest/wc2022_history.csv")
    parser.add_argument("--out", default="data/local/backtest/line_move_report.json")
    args = parser.parse_args(argv)

    with open(args.csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    report = build_report(rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report["sample"], ensure_ascii=False))
    for key in ("by_1x2_move", "by_abs_move"):
        print(f"-- {key} --")
        for bucket in report[key]:
            print(json.dumps(bucket, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
