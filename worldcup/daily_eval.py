"""Daily post-match chain: results capture -> eval csv -> backtest -> digest.

默认只读 data/cache/，读写 data/local/；不联网、不消耗 The Odds API 额度、不写线上。
传 --live-scores 时会先调用 The Odds API scores 端点（约 2 credits）。
推送内容仅研究摘要，不含资金/下注字段。
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

from worldcup import backtest, eval_data, results_capture
from worldcup.finished_record import build_finished_block


DEFAULT_RESULTS_OUT = Path("data/local/results/wc2026_results.csv")
DEFAULT_FINISHED_STORE = Path("data/local/finished_record_store.json")


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
    finished_store: str | Path | None = None,
    min_sample: int = 30,
    extra_fresh: int = 0,
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
        "decision_tally": {"hit": 0, "miss": 0, "push": 0, "no_pick": 0},
    }
    fresh = (
        (results_stats.get("added", 0) or 0)
        + (results_stats.get("updated", 0) or 0)
        + max(extra_fresh, 0)
    )
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

    resolved_finished_store = (
        Path(finished_store)
        if finished_store is not None
        else DEFAULT_FINISHED_STORE
        if Path(results_out) == DEFAULT_RESULTS_OUT
        else Path(results_out).parent / "finished_record_store.json"
    )
    finished = build_finished_block(history_dir, results_out, resolved_finished_store)
    digest["decision_tally"] = dict(finished.get("decision_tally") or {})
    digest["decision_sample"] = dict(finished.get("decision_sample") or {})
    digest["decision_coverage"] = dict(finished.get("decision_coverage") or {})

    digest["status"] = "ok"
    return digest


def _fmt_metric(metrics: dict | None) -> str:
    if not metrics or not metrics.get("n"):
        return "-"
    return f"LogLoss {metrics['log_loss']:.4f} (n={metrics['n']})"


def build_digest_message(digest: dict) -> dict[str, str]:
    results = digest.get("results") or {}
    lines = [
        "世界杯赛后日报",
        f"完赛 {results.get('total', 0)} 场"
        f"（新增 {results.get('added', 0)}，更新 {results.get('updated', 0)}）",
    ]
    eval_stats = digest.get("eval")
    if eval_stats:
        lines.append(
            f"评估样本 {eval_stats.get('joined', 0)} 场"
            f"（无 closing 快照跳过 {eval_stats.get('skipped_no_closing', 0)}）"
        )
    backtest_stats = digest.get("backtest")
    if backtest_stats:
        lines.append(
            f"1X2 模型 {_fmt_metric(backtest_stats.get('model_1x2'))}，"
            f"市场 {_fmt_metric(backtest_stats.get('market_1x2'))}"
        )
        lines.append(
            f"OU 模型 {_fmt_metric(backtest_stats.get('model_ou'))}，"
            f"市场 {_fmt_metric(backtest_stats.get('market_ou'))}"
        )
        lines.append(f"AH 进评估 {backtest_stats.get('n_ah', 0)} 场")
        if backtest_stats.get("sample_too_small"):
            lines.append("样本不足（sample_too_small），指标仅记录不作结论")
    tally = digest.get("decision_tally") or {}
    lines.append(
        "本场首选："
        f"命中 {tally.get('hit', 0)} / 未中 {tally.get('miss', 0)} / "
        f"走水 {tally.get('push', 0)} / 暂无可靠首选 {tally.get('no_pick', 0)}"
    )
    lines.append("仅用于研究分析，不构成投注建议")
    return {
        "summary": f"世界杯赛后日报：完赛 {results.get('total', 0)} 场",
        "content": "\n".join(lines),
    }


def _fresh_count(stats: dict | None) -> int:
    if not stats:
        return 0
    return (stats.get("added", 0) or 0) + (stats.get("updated", 0) or 0)


def _merge_scores_into_results(digest: dict, scores_stats: dict) -> None:
    if scores_stats.get("status") != "captured":
        return
    results = dict(digest.get("results") or {})
    results["added"] = (results.get("added", 0) or 0) + (scores_stats.get("added", 0) or 0)
    results["updated"] = (results.get("updated", 0) or 0) + (scores_stats.get("updated", 0) or 0)
    digest["results"] = results


def main(
    argv: list[str] | None = None,
    notify_fn: Callable[..., dict] | None = None,
    scores_capture_fn: Callable[..., dict] | None = None,
) -> int:
    import argparse

    from worldcup.notifications import send_wxpusher_notification

    parser = argparse.ArgumentParser(description="Run daily post-match eval chain.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--results-out", default="data/local/results/wc2026_results.csv")
    parser.add_argument("--eval-out", default="data/local/backtest/wc2026_eval.csv")
    parser.add_argument("--report-out", default="data/local/backtest/wc2026_report.json")
    parser.add_argument("--finished-store", default=None)
    parser.add_argument("--min-sample", type=int, default=30)
    parser.add_argument("--notify", action="store_true", help="Send WxPusher digest.")
    parser.add_argument("--live-scores", action="store_true", help="Fetch The Odds API scores first.")
    parser.add_argument(
        "--allow-knockout-scores",
        action="store_true",
        help="Allow live score capture after manual 90-minute knockout score review.",
    )
    args = parser.parse_args(argv)

    scores_stats = None
    if args.live_scores:
        from worldcup.refresh_runner import _load_env
        from worldcup.scores_capture import run_scores_capture

        capture = scores_capture_fn or run_scores_capture
        scores_stats = capture(
            live=True,
            env=_load_env(".env"),
            cache_path=Path(args.cache_dir) / "theoddsapi_scores.json",
            quota_path=Path(args.cache_dir) / "quota.json",
            results_out=args.results_out,
            allow_knockout_scores=args.allow_knockout_scores,
        )

    digest = run_daily_eval(
        cache_dir=args.cache_dir,
        history_dir=args.history,
        results_out=args.results_out,
        eval_out=args.eval_out,
        report_out=args.report_out,
        finished_store=args.finished_store,
        min_sample=args.min_sample,
        extra_fresh=_fresh_count(scores_stats),
    )
    if scores_stats is not None:
        digest["scores"] = scores_stats
        _merge_scores_into_results(digest, scores_stats)
    notification = None
    if args.notify and digest.get("status") == "ok":
        message = build_digest_message(digest)
        send = notify_fn or send_wxpusher_notification
        notification = send(message["content"], summary=message["summary"])
    print(json.dumps({**digest, "notification": notification}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
