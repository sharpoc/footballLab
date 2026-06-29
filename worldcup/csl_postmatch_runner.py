"""Local CSL postmatch evaluation runner.

Runs the local-only CSL postmatch chain: archived snapshots plus local results
to eval CSV, backtest report, and pending gate report. It never fetches sources,
reads secrets, touches quota, publishes data, or lifts club_rating_pending.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldcup import backtest, csl_eval_data, csl_pending_gate
from worldcup.club_rating import load_club_results_csv
from worldcup.config import load_config

DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"
DEFAULT_RESULTS = "data/cache/club_results_csl_2026.csv"
DEFAULT_EVAL_OUT = "data/local/backtest/csl_2026_eval.csv"
DEFAULT_REPORT_OUT = "data/local/backtest/csl_2026_report.json"


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _write_json(payload: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def _pending_summary(report: dict[str, Any], path: Path) -> dict[str, Any]:
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    return {
        "decision_status": decision.get("status"),
        "can_lift_club_rating_pending": bool(report.get("can_lift_club_rating_pending")),
        "path": str(path),
    }


def run_postmatch(
    *,
    root: str | Path = ".",
    history: str | Path = DEFAULT_HISTORY,
    results: str | Path = DEFAULT_RESULTS,
    eval_out: str | Path = DEFAULT_EVAL_OUT,
    report_out: str | Path = DEFAULT_REPORT_OUT,
    gate_out: str | Path | None = None,
    competition_id: str = DEFAULT_COMPETITION_ID,
    generated_at: str | None = None,
    min_sample: int = 30,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
    config: str | Path | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    history_path = _resolve_under_root(root_path, history)
    results_path = _resolve_under_root(root_path, results)
    eval_path = _resolve_under_root(root_path, eval_out)
    report_path = _resolve_under_root(root_path, report_out)
    config_path = _resolve_under_root(root_path, config) if config is not None else None

    snapshots = csl_eval_data.load_snapshots(history_path)
    result_rows = load_club_results_csv(results_path, competition_id)
    eval_rows, skipped = csl_eval_data.build_rows(snapshots, result_rows)
    csl_eval_data.write_csv(eval_rows, eval_path)

    backtest_report = backtest.run_backtest(
        backtest.load_matches(eval_path),
        load_config(config_path),
        min_sample=min_sample,
    )
    _write_json(backtest_report, report_path)

    pending_report = csl_pending_gate.build_pending_gate_report(
        result_rows,
        source=results_path,
        competition_id=competition_id,
        generated_at=generated_at,
        warmup_matches=warmup_matches,
        min_eval_matches=min_eval_matches,
        market_report=backtest_report,
    )
    gate_path = (
        _resolve_under_root(root_path, gate_out)
        if gate_out is not None
        else csl_pending_gate.default_gate_path(root_path, pending_report["generated_at"], "json")
    )
    written_gate = csl_pending_gate.write_gate(pending_report, gate_path, "json")

    return {
        "competition_id": competition_id,
        "snapshots": len(snapshots),
        "results": len(result_rows),
        "joined": len(eval_rows),
        "skipped_no_closing": skipped,
        "eval_out": str(eval_path),
        "report_out": str(report_path),
        "backtest_sample": backtest_report["sample"],
        "pending_gate": _pending_summary(pending_report, written_gate),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the local-only CSL postmatch evaluation chain.",
        allow_abbrev=False,
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--results", default=DEFAULT_RESULTS)
    parser.add_argument("--eval-out", default=DEFAULT_EVAL_OUT)
    parser.add_argument("--report-out", default=DEFAULT_REPORT_OUT)
    parser.add_argument("--gate-out", default=None)
    parser.add_argument(
        "--competition",
        "--competition-id",
        dest="competition_id",
        default=DEFAULT_COMPETITION_ID,
    )
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--min-sample", type=int, default=30)
    parser.add_argument("--warmup-matches", type=int, default=300)
    parser.add_argument("--min-eval-matches", type=int, default=200)
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    summary = run_postmatch(
        root=args.root,
        history=args.history,
        results=args.results,
        eval_out=args.eval_out,
        report_out=args.report_out,
        gate_out=args.gate_out,
        competition_id=args.competition_id,
        generated_at=args.generated_at,
        min_sample=args.min_sample,
        warmup_matches=args.warmup_matches,
        min_eval_matches=args.min_eval_matches,
        config=args.config,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
