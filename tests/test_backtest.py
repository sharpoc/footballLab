import math

from worldcup.backtest import brier_multiclass, calibration_bins, log_loss


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
