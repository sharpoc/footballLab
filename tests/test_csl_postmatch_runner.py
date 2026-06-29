from __future__ import annotations

import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_postmatch_runner import main as runner_main
from worldcup.csl_postmatch_runner import run_postmatch


def _snapshot() -> dict:
    return {
        "snapshot_at": "2026-07-03T11:30:00+00:00",
        "competition": {"id": "csl_2026"},
        "matches": [
            {
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "home_canonical": "yunnan_yukun",
                "away_canonical": "henan",
                "elo": {"home": 1556, "away": 1540},
                "market": {
                    "1x2": {
                        "bookmaker": "must-not-leak",
                        "odds": {"home": 2.4, "draw": 3.4, "away": 3.0},
                    },
                    "ou_2_5": {"line": 2.5, "odds": {"over": 1.9, "under": 1.95}},
                    "ah_main": {"line_home": -0.25, "odds": {"home": 1.88, "away": 2.02}},
                },
            }
        ],
    }


def _write_history(root: Path) -> Path:
    history = root / "data/local/diagnostics/csl_history"
    history.mkdir(parents=True)
    (history / "snapshot_20260703T113000Z-live.json").write_text(
        json.dumps(_snapshot(), ensure_ascii=False),
        encoding="utf-8",
    )
    return history


def _write_results(root: Path) -> Path:
    results = root / "data/cache/club_results_csl_2026.csv"
    results.parent.mkdir(parents=True)
    with results.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-07-03",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "home_score": "2",
                "away_score": "1",
                "neutral": "0",
            }
        )
    return results


def test_run_postmatch_writes_eval_backtest_and_pending_gate_artifacts():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_history(root)
        _write_results(root)

        summary = run_postmatch(
            root=root,
            generated_at="2026-07-04T00:00:00Z",
            min_sample=1,
            warmup_matches=0,
            min_eval_matches=200,
        )

        eval_out = root / "data/local/backtest/csl_2026_eval.csv"
        report_out = root / "data/local/backtest/csl_2026_report.json"
        gate_out = root / "data/local/diagnostics/csl_pending_gate_20260704T000000Z.json"
        assert eval_out.exists()
        assert report_out.exists()
        assert gate_out.exists()
        assert summary["competition_id"] == "csl_2026"
        assert summary["snapshots"] == 1
        assert summary["results"] == 1
        assert summary["joined"] == 1
        assert summary["skipped_no_closing"] == 0
        assert summary["backtest_sample"]["n_matches"] == 1
        assert summary["backtest_sample"]["sample_too_small"] is False
        assert summary["pending_gate"] == {
            "decision_status": "keep_pending",
            "can_lift_club_rating_pending": False,
            "path": str(gate_out),
        }

        rows = list(csv.DictReader(eval_out.open(newline="", encoding="utf-8")))
        assert rows[0]["match_id"] == "csl_2026:2026-07-03:yunnan_yukun:henan"
        backtest_report = json.loads(report_out.read_text(encoding="utf-8"))
        assert backtest_report["sample"]["n_1x2"] == 1
        pending_report = json.loads(gate_out.read_text(encoding="utf-8"))
        assert pending_report["checks"]["market_baseline_available"] is True
        assert pending_report["can_lift_club_rating_pending"] is False


def test_cli_prints_safe_summary_without_raw_market_payload():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_history(root)
        _write_results(root)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = runner_main(
                [
                    "--root",
                    str(root),
                    "--generated-at",
                    "2026-07-04T00:00:00Z",
                    "--min-sample",
                    "1",
                    "--warmup-matches",
                    "0",
                ]
            )

        assert exit_code == 0
        summary = json.loads(stdout.getvalue())
        assert summary["joined"] == 1
        assert summary["pending_gate"]["can_lift_club_rating_pending"] is False
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("bookmaker", "must-not-leak", "odds", "api_key", "secret"):
            assert forbidden not in serialized
