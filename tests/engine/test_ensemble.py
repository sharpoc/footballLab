import math

from worldcup.engine.ensemble import combine_1x2


def test_combine_weighted_average_normalized():
    elo = {"home": 0.6, "draw": 0.25, "away": 0.15}
    poi = {"home": 0.4, "draw": 0.35, "away": 0.25}
    out = combine_1x2(elo, poi, w_elo=0.5, w_poisson=0.5)
    assert math.isclose(sum(out.values()), 1.0, abs_tol=1e-9)
    assert 0.4 <= out["home"] <= 0.6


def test_combine_all_weight_elo():
    elo = {"home": 0.6, "draw": 0.25, "away": 0.15}
    poi = {"home": 0.1, "draw": 0.1, "away": 0.8}
    out = combine_1x2(elo, poi, w_elo=1.0, w_poisson=0.0)
    assert math.isclose(out["home"], 0.6, abs_tol=1e-9)
