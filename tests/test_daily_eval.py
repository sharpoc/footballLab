import json
from pathlib import Path
from tempfile import TemporaryDirectory

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
