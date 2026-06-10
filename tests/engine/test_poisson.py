import math

from worldcup.engine.poisson import lambdas, prob_over, probs_1x2, score_matrix

CFG = {
    "mu_total": 2.6,
    "gd_div": 250,
    "gd_clamp": 2.5,
    "lambda_min": 0.15,
    "lambda_max": 4.5,
    "max_goals": 10,
    "tail_mass_max": 0.01,
}


def test_lambdas_equal_strength_split_mu():
    lh, la = lambdas(0, CFG)
    assert math.isclose(lh, 1.3)
    assert math.isclose(la, 1.3)


def test_lambdas_clamped():
    lh, la = lambdas(100000, CFG)
    assert lh == CFG["lambda_max"] or la == CFG["lambda_min"]


def test_matrix_normalized_sums_to_one():
    matrix, tail = score_matrix(1.3, 1.3, CFG)
    total = sum(sum(row) for row in matrix)
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert tail < CFG["tail_mass_max"]


def test_1x2_sums_to_one_and_symmetric():
    matrix, _ = score_matrix(1.3, 1.3, CFG)
    probs = probs_1x2(matrix)
    assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-9)
    assert math.isclose(probs["home"], probs["away"], abs_tol=1e-9)


def test_over_matches_total_poisson_manual():
    matrix, _ = score_matrix(1.3, 1.3, CFG)
    over = prob_over(matrix, 2.5)
    total_lambda = 2.6
    under_manual = sum(
        math.exp(-total_lambda) * (total_lambda**k) / math.factorial(k)
        for k in range(3)
    )
    assert math.isclose(over, 1 - under_manual, rel_tol=0.005)


def test_prob_total_over_matches_matrix():
    from worldcup.engine.poisson import prob_total_over

    matrix, _ = score_matrix(1.3, 1.3, CFG)
    p_matrix = prob_over(matrix, 2.5)
    assert math.isclose(prob_total_over(2.6, 2.5), p_matrix, abs_tol=1e-3)


def test_implied_total_mu_roundtrip():
    from worldcup.engine.poisson import implied_total_mu, prob_total_over

    for mu in (1.8, 2.6, 3.4):
        p = prob_total_over(mu, 2.5)
        assert math.isclose(implied_total_mu(p, 2.5), mu, abs_tol=1e-6)


def test_implied_total_mu_clamps_extreme_probs():
    from worldcup.engine.poisson import implied_total_mu

    assert 0.1 <= implied_total_mu(0.0, 2.5) <= 8.0
    assert 0.1 <= implied_total_mu(1.0, 2.5) <= 8.0


def test_prior_mu_zero_slope_returns_base():
    from worldcup.engine.poisson import prior_mu

    assert prior_mu(0, CFG) == CFG["mu_total"]
    assert prior_mu(400, CFG) == CFG["mu_total"]


def test_prior_mu_rises_with_abs_dr_and_is_symmetric():
    from worldcup.engine.poisson import prior_mu

    cfg = dict(CFG)
    cfg["mu_total"] = 2.3
    cfg["mu_dr_slope"] = 0.002
    assert math.isclose(prior_mu(0, cfg), 2.3)
    assert math.isclose(prior_mu(300, cfg), 2.3 + 0.002 * 300)
    assert math.isclose(prior_mu(-300, cfg), prior_mu(300, cfg))


def test_prior_mu_clamped():
    from worldcup.engine.poisson import prior_mu

    cfg = dict(CFG)
    cfg["mu_dr_slope"] = 0.01
    assert prior_mu(10000, cfg) == 4.0
    cfg["mu_prior_max"] = 3.5
    assert prior_mu(10000, cfg) == 3.5


def test_lambdas_mu_override():
    lh, la = lambdas(0, CFG, mu_total=3.0)
    assert math.isclose(lh + la, 3.0)


def test_blended_mu_without_market_falls_back_to_prior():
    from worldcup.engine.poisson import blended_mu

    cfg = dict(CFG)
    cfg["mu_market_weight"] = 0.7
    assert math.isclose(blended_mu(None, 2.5, cfg), CFG["mu_total"])


def test_blended_mu_weight_zero_keeps_prior():
    from worldcup.engine.poisson import blended_mu

    assert math.isclose(blended_mu(0.9, 2.5, dict(CFG)), CFG["mu_total"])


def test_blended_mu_full_weight_tracks_market():
    from worldcup.engine.poisson import blended_mu, prob_total_over

    cfg = dict(CFG)
    cfg["mu_market_weight"] = 1.0
    p_high = prob_total_over(3.2, 2.5)
    assert math.isclose(blended_mu(p_high, 2.5, cfg), 3.2, abs_tol=1e-6)


def test_score_matrix_dc_rho_zero_is_noop():
    base, tail_base = score_matrix(1.5, 1.1, CFG)
    cfg = dict(CFG)
    cfg["dc_rho"] = 0.0
    same, tail_same = score_matrix(1.5, 1.1, cfg)
    assert same == base
    assert tail_same == tail_base


def test_score_matrix_dc_negative_rho_boosts_low_score_draws():
    cfg = dict(CFG)
    cfg["dc_rho"] = -0.1
    adjusted, _ = score_matrix(1.5, 1.1, cfg)
    base, _ = score_matrix(1.5, 1.1, CFG)
    assert adjusted[0][0] > base[0][0]
    assert adjusted[1][1] > base[1][1]
    assert adjusted[0][1] < base[0][1]
    assert adjusted[1][0] < base[1][0]
    assert math.isclose(sum(sum(row) for row in adjusted), 1.0, abs_tol=1e-9)
    assert probs_1x2(adjusted)["draw"] > probs_1x2(base)["draw"]


def test_score_matrix_dc_extreme_rho_clamped_no_negative_cells():
    cfg = dict(CFG)
    cfg["dc_rho"] = -5.0
    adjusted, _ = score_matrix(1.5, 1.1, cfg)
    base, _ = score_matrix(1.5, 1.1, CFG)
    assert adjusted != base
    assert min(min(row) for row in adjusted) >= 0.0
    assert math.isclose(sum(sum(row) for row in adjusted), 1.0, abs_tol=1e-9)
