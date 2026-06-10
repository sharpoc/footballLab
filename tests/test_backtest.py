import math
from pathlib import Path

from worldcup.backtest import brier_multiclass, calibration_bins, log_loss

SAMPLE_CSV = Path(__file__).resolve().parent / "data" / "backtest_sample.csv"


def test_brier_perfect_prediction_is_zero():
    assert brier_multiclass({"home": 1.0, "draw": 0.0, "away": 0.0}, "home") == 0.0


def test_brier_uniform_three_way():
    probs = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    assert math.isclose(brier_multiclass(probs, "home"), 2 / 3, abs_tol=1e-9)


def test_log_loss_clamps_zero_probability():
    value = log_loss({"home": 0.0, "draw": 0.5, "away": 0.5}, "home")
    assert math.isfinite(value)
    assert value > 20


def test_calibration_bins_groups_and_rates():
    records = [(0.05, False), (0.05, True), (0.95, True), (0.95, True)]
    bins = calibration_bins(records, n_bins=10)
    assert len(bins) == 2
    low, high = bins
    assert low["n"] == 2 and math.isclose(low["hit_rate"], 0.5)
    assert high["n"] == 2 and math.isclose(high["hit_rate"], 1.0)
    assert math.isclose(high["p_mean"], 0.95)


def test_outcome_1x2():
    from worldcup.backtest import outcome_1x2

    assert outcome_1x2(2, 0) == "home"
    assert outcome_1x2(1, 1) == "draw"
    assert outcome_1x2(0, 3) == "away"


def test_ah_realized_return_win_push_quarter():
    from worldcup.backtest import ah_realized_return

    assert math.isclose(ah_realized_return(1, -0.5, 2.0), 1.0)
    assert math.isclose(ah_realized_return(0, 0.0, 1.9), 0.0)
    assert math.isclose(ah_realized_return(0, -0.25, 1.9), -0.5)
    assert math.isclose(ah_realized_return(-1, 0.5, 1.8), -1.0)


def test_load_matches_parses_sample():
    from worldcup.backtest import load_matches

    matches = load_matches(SAMPLE_CSV)
    assert len(matches) == 7
    first = matches[0]
    assert first.match_id == "m1"
    assert first.home_score == 2
    assert first.odds_1x2 == {"home": 1.60, "draw": 3.90, "away": 6.00}
    assert first.odds_ou == {"over": 1.85, "under": 1.95}
    assert first.ah_line == -1.0
    assert matches[2].neutral is False
    last = matches[-1]
    assert last.odds_ou is None and last.odds_ah is None and last.ah_line is None


def test_load_matches_missing_required_column_raises():
    import tempfile

    from worldcup.backtest import load_matches

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("match_id,home_team\nm1,Alpha\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "missing required columns" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_matches_missing_required_value_raises():
    import tempfile

    from worldcup.backtest import load_matches

    header = (
        "match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,"
        "home_elo_before,away_elo_before\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + "m1,2025-06-01T18:00:00Z,Alpha,Beta,2,,1900,1700\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "row 2" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_matches_rejects_odds_not_above_one():
    import tempfile

    from worldcup.backtest import load_matches

    header = (
        "match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,"
        "home_elo_before,away_elo_before,odds_home,odds_draw,odds_away\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + "m1,2025-06-01T18:00:00Z,Alpha,Beta,2,0,1900,1700,1.00,3.90,6.00\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "row 2" in str(exc) and "odds" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_replay_match_produces_model_and_market_probs():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    matches = load_matches(SAMPLE_CSV)
    result = replay_match(matches[0], cfg)
    assert math.isclose(sum(result["model_1x2"].values()), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(result["market_1x2"].values()), 1.0, abs_tol=1e-9)
    assert result["model_1x2"]["home"] > result["model_1x2"]["away"]
    assert 0.0 < result["model_ou"]["over"] < 1.0
    assert result["mu_used"] > 0


def test_replay_match_without_odds_keeps_model_only():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    last = load_matches(SAMPLE_CSV)[-1]
    result = replay_match(last, cfg)
    assert result["market_ou"] is None
    assert math.isclose(result["mu_used"], cfg["poisson"]["mu_total"], abs_tol=1e-9)


def test_replay_match_home_advantage_applied_when_not_neutral():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    matches = load_matches(SAMPLE_CSV)
    non_neutral = matches[2]
    assert non_neutral.neutral is False
    result = replay_match(non_neutral, cfg)
    base_dr = non_neutral.home_elo_before - non_neutral.away_elo_before
    assert math.isclose(result["dr"], base_dr + cfg["elo"]["home_adv"])


def test_run_backtest_report_structure_and_small_sample_flag():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    cfg = load_config()
    report = run_backtest(load_matches(SAMPLE_CSV), cfg, min_sample=200)
    sample = report["sample"]
    assert sample["n_matches"] == 7
    assert sample["n_1x2"] == 7
    assert sample["n_ou"] == 6
    assert sample["n_ah"] == 6
    assert sample["sample_too_small"] is True

    metrics = report["markets"]["1x2"]
    for source in ("model", "market", "uniform"):
        assert metrics[source]["n"] == 7
        assert 0.0 <= metrics[source]["brier"] <= 2.0
        assert metrics[source]["log_loss"] > 0.0
    assert report["markets"]["ou_2_5"]["model"]["n"] == 7
    assert report["markets"]["ou_2_5"]["market"]["n"] == 6

    assert report["calibration_1x2"]
    assert sum(b["n"] for b in report["ev_buckets_1x2"]) == 21
    assert sum(b["n"] for b in report["odds_buckets_1x2"]) == 21
    assert sum(b["n"] for b in report["ah_ev_buckets"]) == 6
    assert report["totals_by_abs_dr"]
    assert "no staking advice" in report["notes"]


def test_run_backtest_not_small_when_min_sample_met():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    report = run_backtest(load_matches(SAMPLE_CSV), load_config(), min_sample=5)
    assert report["sample"]["sample_too_small"] is False


def test_run_backtest_reports_model_matched_subset():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    report = run_backtest(load_matches(SAMPLE_CSV), load_config(), min_sample=5)
    assert report["markets"]["1x2"]["model_matched"]["n"] == 7
    assert report["markets"]["ou_2_5"]["model_matched"]["n"] == 6
    assert report["markets"]["ou_2_5"]["market"]["n"] == 6
    assert report["markets"]["ou_2_5"]["model"]["n"] == 7


def test_apply_overrides_returns_modified_copy():
    from worldcup.backtest import apply_overrides

    cfg = {"poisson": {"dc_rho": 0.0}, "ou_main_line": 2.5}
    out = apply_overrides(cfg, ["poisson.dc_rho=-0.1", "ou_main_line=3.5"])
    assert out["poisson"]["dc_rho"] == -0.1
    assert out["ou_main_line"] == 3.5
    assert cfg["poisson"]["dc_rho"] == 0.0
    assert cfg["ou_main_line"] == 2.5


def test_apply_overrides_parses_int_bool_string():
    from worldcup.backtest import apply_overrides

    cfg = {"odds": {"min_books": 3}}
    out = apply_overrides(cfg, ["odds.min_books=5", "odds.flag=true", "odds.name=abc"])
    assert out["odds"]["min_books"] == 5
    assert out["odds"]["flag"] is True
    assert out["odds"]["name"] == "abc"


def test_apply_overrides_invalid_format_raises():
    from worldcup.backtest import apply_overrides

    for bad in ("poisson.dc_rho", "=1", "unknown.key=1"):
        try:
            apply_overrides({"poisson": {}}, [bad])
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_cli_accepts_set_overrides():
    import json
    import tempfile

    from worldcup.backtest import main

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.json"
        code = main(
            [
                "--csv",
                str(SAMPLE_CSV),
                "--out",
                str(out_path),
                "--min-sample",
                "5",
                "--set",
                "poisson.mu_market_weight=0",
            ]
        )
        assert code == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        mus = [b["mean_mu_used"] for b in report["totals_by_abs_dr"] if b["n"]]
        for mu in mus:
            assert math.isclose(mu, 2.6, abs_tol=1e-9)


def test_cli_writes_report_json():
    import json
    import tempfile

    from worldcup.backtest import main

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.json"
        code = main(["--csv", str(SAMPLE_CSV), "--out", str(out_path), "--min-sample", "5"])
        assert code == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert report["sample"]["n_matches"] == 7
        assert report["sample"]["sample_too_small"] is False
