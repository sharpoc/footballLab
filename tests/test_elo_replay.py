import math

from worldcup.elo_replay import goal_index, k_factor, update_pair


def test_k_factor_by_tournament_class():
    assert k_factor("FIFA World Cup") == 60.0
    assert k_factor("Copa América") == 50.0
    assert k_factor("UEFA Euro") == 50.0
    assert k_factor("FIFA World Cup qualification") == 40.0
    assert k_factor("UEFA Euro qualification") == 40.0
    assert k_factor("UEFA Nations League") == 40.0
    assert k_factor("CONCACAF Nations League") == 40.0
    assert k_factor("Friendly") == 20.0
    assert k_factor("King's Cup") == 30.0


def test_goal_index_margins():
    assert goal_index(0) == 1.0
    assert goal_index(1) == 1.0
    assert goal_index(-1) == 1.0
    assert goal_index(2) == 1.5
    assert goal_index(-2) == 1.5
    assert math.isclose(goal_index(3), (11 + 3) / 8)
    assert math.isclose(goal_index(-5), (11 + 5) / 8)


def test_update_pair_friendly_home_win_uses_home_advantage():
    from worldcup.engine.elo import expected_score

    rh, ra = update_pair(1500.0, 1500.0, 1, 0, k=20.0, neutral=False)
    delta = 20.0 * 1.0 * (1.0 - expected_score(100.0))
    assert math.isclose(rh, 1500.0 + delta)
    assert math.isclose(ra, 1500.0 - delta)


def test_update_pair_neutral_draw_between_equals_is_noop():
    rh, ra = update_pair(1600.0, 1600.0, 1, 1, k=40.0, neutral=True)
    assert math.isclose(rh, 1600.0)
    assert math.isclose(ra, 1600.0)


def test_update_pair_is_zero_sum():
    rh, ra = update_pair(1700.0, 1450.0, 0, 3, k=50.0, neutral=True)
    assert math.isclose((rh + ra), 1700.0 + 1450.0)
    assert rh < 1700.0 and ra > 1450.0
