import json
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.refresh_runner as refresh_runner
from worldcup.daily_eval import run_daily_eval


def _seed_project(root: Path, with_score: bool) -> dict:
    cache = root / "cache"
    history = root / "history"
    local = root / "local"
    cache.mkdir()
    history.mkdir()
    local.mkdir()
    match = {
        "round": "Matchday 1",
        "date": "2026-06-11",
        "time": "13:00 UTC-6",
        "team1": "Mexico",
        "team2": "South Africa",
        "ground": "Mexico City",
    }
    if with_score:
        match["score1"] = 2
        match["score2"] = 0
    (cache / "openfootball_2026.json").write_text(json.dumps({"matches": [match]}))
    (history / "snapshot_20260611T120000Z-live.json").write_text(
        json.dumps(
            {
                "snapshot_at": "2026-06-11T12:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "home_canonical": "mexico",
                        "away_canonical": "south_africa",
                        "elo": {"home": 1875, "away": 1700},
                        "market": {
                            "1x2": {"odds": {"home": 1.8, "draw": 3.6, "away": 4.8}},
                            "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                        },
                    }
                ],
            }
        )
    )
    (cache / "analysis_snapshot.json").write_text(
        json.dumps(
            {
                "snapshot_at": "2026-06-12T04:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "result": {"status": "finished", "home_score": 2, "away_score": 0},
                        "signals": [
                            {
                                "market_type": "1X2_90min",
                                "selection": "home",
                                "grade": "S",
                                "line": None,
                            }
                        ],
                    }
                ],
            }
        )
    )
    return {
        "cache_dir": cache,
        "history_dir": history,
        "results_out": local / "results.csv",
        "eval_out": local / "eval.csv",
        "report_out": local / "report.json",
    }


def test_daily_eval_runs_chain_and_builds_digest():
    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)

        digest = run_daily_eval(min_sample=1, **paths)

        assert digest["status"] == "ok"
        assert digest["results"]["total"] == 1
        assert digest["results"]["added"] == 1
        assert digest["eval"]["joined"] == 1
        assert digest["backtest"]["n_matches"] == 1
        assert digest["backtest"]["model_1x2"]["n"] == 1
        assert digest["signal_tally"]["S"] == {"命中": 1}
        assert Path(paths["report_out"]).exists()


def test_daily_eval_skips_backtest_without_results():
    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=False)

        digest = run_daily_eval(min_sample=1, **paths)

        assert digest["status"] == "no_new_results"
        assert digest["results"]["total"] == 0
        assert digest["eval"] is None
        assert digest["backtest"] is None
        assert not Path(paths["report_out"]).exists()


def test_daily_eval_notify_sends_digest_once():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)
        calls = []

        def notify_fn(content, *, summary):
            calls.append({"content": content, "summary": summary})
            return {"status": "sent", "exit_code": 0}

        code = main(
            [
                "--cache-dir",
                str(paths["cache_dir"]),
                "--history",
                str(paths["history_dir"]),
                "--results-out",
                str(paths["results_out"]),
                "--eval-out",
                str(paths["eval_out"]),
                "--report-out",
                str(paths["report_out"]),
                "--min-sample",
                "1",
                "--notify",
            ],
            notify_fn=notify_fn,
        )

        assert code == 0
        assert len(calls) == 1
        assert "赛后日报" in calls[0]["summary"]
        assert "S 级" in calls[0]["content"]
        assert "命中" in calls[0]["content"]
        assert "研究" in calls[0]["content"]


def test_daily_eval_no_notify_without_new_results():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=False)
        calls = []

        def notify_fn(content, *, summary):
            calls.append(summary)
            return {"status": "sent", "exit_code": 0}

        code = main(
            [
                "--cache-dir",
                str(paths["cache_dir"]),
                "--history",
                str(paths["history_dir"]),
                "--results-out",
                str(paths["results_out"]),
                "--eval-out",
                str(paths["eval_out"]),
                "--report-out",
                str(paths["report_out"]),
                "--min-sample",
                "1",
                "--notify",
            ],
            notify_fn=notify_fn,
        )

        assert code == 0
        assert calls == []


def test_daily_eval_without_live_scores_does_not_load_env():
    from worldcup.daily_eval import main

    def fail_load_env(_path):
        raise AssertionError("non-live daily eval must not load env")

    old_load_env = refresh_runner._load_env
    refresh_runner._load_env = fail_load_env
    try:
        with TemporaryDirectory() as tmp:
            paths = _seed_project(Path(tmp), with_score=True)
            code = main(
                [
                    "--cache-dir",
                    str(paths["cache_dir"]),
                    "--history",
                    str(paths["history_dir"]),
                    "--results-out",
                    str(paths["results_out"]),
                    "--eval-out",
                    str(paths["eval_out"]),
                    "--report-out",
                    str(paths["report_out"]),
                    "--min-sample",
                    "1",
                ],
            )

            assert code == 0
    finally:
        refresh_runner._load_env = old_load_env


def test_daily_eval_live_scores_runs_capture_first():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)
        capture_calls = []

        def scores_capture_fn(**kwargs):
            capture_calls.append(kwargs)
            return {"status": "captured", "completed": 1, "added": 1, "updated": 0}

        code = main(
            [
                "--cache-dir",
                str(paths["cache_dir"]),
                "--history",
                str(paths["history_dir"]),
                "--results-out",
                str(paths["results_out"]),
                "--eval-out",
                str(paths["eval_out"]),
                "--report-out",
                str(paths["report_out"]),
                "--min-sample",
                "1",
                "--live-scores",
            ],
            scores_capture_fn=scores_capture_fn,
        )

        assert code == 0
        assert len(capture_calls) == 1
        assert capture_calls[0]["live"] is True
        assert str(capture_calls[0]["results_out"]) == str(paths["results_out"])


def test_daily_eval_live_scores_can_forward_knockout_review_opt_in():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)
        capture_calls = []

        def scores_capture_fn(**kwargs):
            capture_calls.append(kwargs)
            return {"status": "captured", "completed": 0, "added": 0, "updated": 0}

        code = main(
            [
                "--cache-dir",
                str(paths["cache_dir"]),
                "--history",
                str(paths["history_dir"]),
                "--results-out",
                str(paths["results_out"]),
                "--eval-out",
                str(paths["eval_out"]),
                "--report-out",
                str(paths["report_out"]),
                "--min-sample",
                "1",
                "--live-scores",
                "--allow-knockout-scores",
            ],
            scores_capture_fn=scores_capture_fn,
        )

        assert code == 0
        assert capture_calls[0]["allow_knockout_scores"] is True


def test_daily_eval_live_scores_fresh_results_drive_eval_when_openfootball_lags():
    from datetime import datetime, timezone

    from worldcup.collectors.models import MatchResult
    from worldcup.daily_eval import main
    from worldcup.results_capture import _load_rows, _write_rows, upsert_results

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=False)

        def scores_capture_fn(**kwargs):
            out = Path(kwargs["results_out"])
            result = MatchResult(
                kickoff_at_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
                home_team_name="Mexico",
                away_team_name="South Africa",
                home_canonical="mexico",
                away_canonical="south_africa",
                home_score=2,
                away_score=0,
            )
            rows, added, updated = upsert_results(
                [result],
                _load_rows(out),
                "2026-06-12T08:00:00+00:00",
            )
            _write_rows(rows, out)
            return {"status": "captured", "completed": 1, "added": added, "updated": updated}

        code = main(
            [
                "--cache-dir",
                str(paths["cache_dir"]),
                "--history",
                str(paths["history_dir"]),
                "--results-out",
                str(paths["results_out"]),
                "--eval-out",
                str(paths["eval_out"]),
                "--report-out",
                str(paths["report_out"]),
                "--min-sample",
                "1",
                "--live-scores",
            ],
            scores_capture_fn=scores_capture_fn,
        )

        assert code == 0
        assert Path(paths["eval_out"]).exists()
        assert Path(paths["report_out"]).exists()
