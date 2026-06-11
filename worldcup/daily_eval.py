"""Daily post-match chain: results capture -> eval csv -> backtest -> digest.

只读 data/cache/，读写 data/local/；不联网、不消耗 The Odds API 额度、不写线上。
推送内容仅研究摘要，不含资金/下注字段。
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

from worldcup import backtest, eval_data, results_capture
from worldcup.ledger import _prediction_result

TRACKED_GRADES = ("S", "A")


def _run_json_cli(fn: Callable[[list[str]], int], argv: list[str]) -> dict:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = fn(argv)
    if code != 0:
        raise RuntimeError(f"{fn.__module__}.main exited with {code}")

    raw = buffer.getvalue().strip()
    if not raw:
        return {}
    lines = [line for line in raw.splitlines() if line.strip()]
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return json.loads(raw)


def signal_tally(snapshot: dict) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {grade: {} for grade in TRACKED_GRADES}
    for match in snapshot.get("matches", []):
        if ((match.get("result") or {}).get("status")) != "finished":
            continue
        for signal in match.get("signals", []):
            grade = str(signal.get("grade") or "")
            if grade not in tally:
                continue
            outcome = _prediction_result(match, signal)
            if not outcome:
                continue
            label = str(outcome.get("label") or "")
            if label:
                tally[grade][label] = tally[grade].get(label, 0) + 1
    return tally


def _market_summary(report: dict, market: str) -> dict:
    block = (report.get("markets") or {}).get(market) or {}
    return {
        "model": block.get("model"),
        "model_matched": block.get("model_matched"),
        "market": block.get("market"),
    }


def run_daily_eval(
    cache_dir: str | Path = "data/cache",
    history_dir: str | Path = "data/local/history",
    results_out: str | Path = "data/local/results/wc2026_results.csv",
    eval_out: str | Path = "data/local/backtest/wc2026_eval.csv",
    report_out: str | Path = "data/local/backtest/wc2026_report.json",
    min_sample: int = 30,
) -> dict[str, Any]:
    cache = Path(cache_dir)
    results_stats = _run_json_cli(
        results_capture.main,
        ["--source", str(cache / "openfootball_2026.json"), "--out", str(results_out)],
    )

    digest: dict[str, Any] = {
        "results": results_stats,
        "eval": None,
        "backtest": None,
        "signal_tally": {grade: {} for grade in TRACKED_GRADES},
    }
    fresh = (results_stats.get("added", 0) or 0) + (results_stats.get("updated", 0) or 0)
    if results_stats.get("total", 0) <= 0 or fresh <= 0:
        digest["status"] = "no_new_results"
        return digest

    eval_stats = _run_json_cli(
        eval_data.main,
        [
            "--history",
            str(history_dir),
            "--results",
            str(results_out),
            "--out",
            str(eval_out),
        ],
    )
    digest["eval"] = eval_stats

    if eval_stats.get("joined", 0) > 0:
        _run_json_cli(
            backtest.main,
            [
                "--csv",
                str(eval_out),
                "--min-sample",
                str(min_sample),
                "--out",
                str(report_out),
            ],
        )
        report = json.loads(Path(report_out).read_text(encoding="utf-8"))
        digest["backtest"] = {
            "n_matches": report["sample"]["n_matches"],
            "n_ah": report["sample"]["n_ah"],
            "sample_too_small": report["sample"]["sample_too_small"],
            "model_1x2": _market_summary(report, "1x2")["model"],
            "market_1x2": _market_summary(report, "1x2")["market"],
            "model_ou": _market_summary(report, "ou_2_5")["model"],
            "market_ou": _market_summary(report, "ou_2_5")["market"],
        }

    snapshot_path = cache / "analysis_snapshot.json"
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        digest["signal_tally"] = signal_tally(snapshot)

    digest["status"] = "ok"
    return digest
