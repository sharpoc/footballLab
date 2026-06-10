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
