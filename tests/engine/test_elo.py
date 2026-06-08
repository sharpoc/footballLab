import math

from worldcup.engine.elo import expected_score, win_draw_loss

CFG = {
    "home_adv": 100,
    "base_draw": 0.28,
    "draw_k": 0.0003,
    "draw_min": 0.18,
    "draw_max": 0.32,
}


def test_expected_score_equal_is_half():
    assert math.isclose(expected_score(0), 0.5)


def test_expected_score_monotonic():
    assert expected_score(200) > expected_score(0) > expected_score(-200)


def test_win_draw_loss_sums_to_one():
    probs = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-9)


def test_equal_neutral_home_equals_away():
    probs = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    assert math.isclose(probs["home"], probs["away"], abs_tol=1e-9)


def test_draw_prob_clamped():
    probs = win_draw_loss(2400, 1400, neutral=True, cfg=CFG)
    assert math.isclose(probs["draw"], CFG["draw_min"], abs_tol=1e-9)
    assert probs["home"] > probs["away"]


def test_home_advantage_applied_when_not_neutral():
    eq_neutral = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    with_home = win_draw_loss(1800, 1800, neutral=False, cfg=CFG)
    assert with_home["home"] > eq_neutral["home"]
