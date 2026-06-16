import math

from worldcup.engine.value import edge, ev, grade_signal
from worldcup.models import Grade, MarketType

CFG = {
    "s_ev": 0.08,
    "s_edge": 0.04,
    "a_ev": 0.05,
    "a_edge": 0.02,
    "b_ev": 0.03,
    "b_edge": 0.01,
    "longshot_market_prob_max": 0.12,
    "odds_dispersion_ratio_max": 1.18,
    "odds_max_age_seconds": 12600,
    "min_books": 3,
}


def ok_ctx(**kw):
    base = {
        "status": "OK",
        "odds_age_seconds": 60,
        "n_books": 5,
        "depends_on_backup": False,
        "line_changed_unknown": False,
    }
    base.update(kw)
    return base


def test_ev_and_edge():
    assert math.isclose(ev(0.6, 1.75), 0.05)
    assert math.isclose(edge(0.55, 0.45), 0.10)


def test_1x2_grade_s_requires_ev_and_edge():
    signal = grade_signal(MarketType.X12, "home", 0.60, 0.45, 1.85, ok_ctx(), CFG)
    assert signal.grade == Grade.S


def test_1x2_high_ev_but_low_edge_not_s():
    signal = grade_signal(MarketType.X12, "home", 0.50, 0.495, 2.3, ok_ctx(), CFG)
    assert signal.grade in (Grade.B, Grade.C)


def test_ah_keeps_ev_raw_grade_but_caps_without_market_validation():
    signal = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.6,
        None,
        1.85,
        ok_ctx(),
        CFG,
        ah_ev=0.09,
    )
    assert signal.grade == Grade.B
    assert signal.raw_grade == Grade.S
    assert signal.edge is None
    assert "ah_market_edge_missing" in signal.reasons


def test_ah_requires_precomputed_settlement_ev():
    try:
        grade_signal(MarketType.AH, "home_-0.5", 0.6, None, 1.85, ok_ctx(), CFG)
    except ValueError as exc:
        assert "AH requires ah_ev" in str(exc)
    else:
        raise AssertionError("expected AH without ah_ev to fail")


def test_stale_odds_caps_at_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_age_seconds=99999),
        CFG,
    )
    assert signal.grade == Grade.B


def test_few_books_caps_at_b():
    signal = grade_signal(MarketType.X12, "home", 0.60, 0.45, 1.85, ok_ctx(n_books=1), CFG)
    assert signal.grade == Grade.B


def test_model_disagreement_caps_1x2_at_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(model_disagreement=True),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "model_disagreement" in signal.reasons


def test_model_disagreement_caps_a_grade_to_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.55,
        0.52,
        1.92,
        ok_ctx(model_disagreement=True),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "model_disagreement" in signal.reasons


def test_model_disagreement_reason_does_not_apply_to_ah():
    signal = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.0,
        None,
        1.85,
        ok_ctx(model_disagreement=True),
        CFG,
        ah_ev=0.09,
    )

    assert signal.grade == Grade.B
    assert signal.raw_grade == Grade.S
    assert "model_disagreement" not in signal.reasons
    assert "ah_market_edge_missing" in signal.reasons


def test_market_dispersion_caps_at_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_market_dispersion_caps_ou_at_b():
    signal = grade_signal(
        MarketType.OU,
        "over",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_ou_market_informed_total_caps_s_to_c():
    signal = grade_signal(
        MarketType.OU,
        "over",
        0.60,
        0.45,
        1.85,
        ok_ctx(same_market_total_anchor=True, total_mu_source="market_informed"),
        CFG,
    )

    assert signal.grade == Grade.C
    assert signal.raw_grade == Grade.S
    assert "market_informed_total" in signal.reasons
    assert signal.ev is not None
    assert signal.edge is not None
    assert signal.same_market_total_anchor is True
    assert signal.total_mu_source == "market_informed"


def test_ou_market_informed_total_caps_a_to_c():
    signal = grade_signal(
        MarketType.OU,
        "under",
        0.55,
        0.52,
        1.92,
        ok_ctx(same_market_total_anchor=True, total_mu_source="market_informed"),
        CFG,
    )

    assert signal.grade == Grade.C
    assert signal.raw_grade == Grade.A
    assert "market_informed_total" in signal.reasons


def test_ou_prior_total_not_capped_by_market_reason():
    signal = grade_signal(
        MarketType.OU,
        "over",
        0.60,
        0.45,
        1.85,
        ok_ctx(same_market_total_anchor=False, total_mu_source="prior"),
        CFG,
    )

    assert signal.grade == Grade.S
    assert signal.raw_grade == Grade.S
    assert "market_informed_total" not in signal.reasons
    assert signal.same_market_total_anchor is False
    assert signal.total_mu_source == "prior"


def test_market_dispersion_caps_a_grade_to_b():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.55,
        0.52,
        1.92,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_few_books_suppresses_market_dispersion_reason():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(n_books=1, odds_dispersion_ratio=1.25),
        CFG,
    )

    assert signal.grade == Grade.B
    assert "few_books" in signal.reasons
    assert "market_dispersion" not in signal.reasons


def test_market_dispersion_caps_ah_at_b():
    signal = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.0,
        None,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.25),
        CFG,
        ah_ev=0.09,
    )

    assert signal.grade == Grade.B
    assert "market_dispersion" in signal.reasons


def test_ah_missing_market_edge_caps_s_and_a_to_b():
    strong = grade_signal(
        MarketType.AH,
        "home_-0.5",
        0.0,
        None,
        1.85,
        ok_ctx(ah_market_validated=False),
        CFG,
        ah_ev=0.09,
    )
    medium = grade_signal(
        MarketType.AH,
        "away_+0.5",
        0.0,
        None,
        1.85,
        ok_ctx(ah_market_validated=False),
        CFG,
        ah_ev=0.055,
    )

    assert strong.grade == Grade.B
    assert strong.raw_grade == Grade.S
    assert "ah_market_edge_missing" in strong.reasons
    assert strong.ah_market_validated is False
    assert medium.grade == Grade.B
    assert medium.raw_grade == Grade.A
    assert "ah_market_edge_missing" in medium.reasons
    assert medium.ah_market_validated is False


def test_existing_reasons_are_preserved_when_capped():
    signal = grade_signal(
        MarketType.OU,
        "over",
        0.60,
        0.45,
        1.85,
        ok_ctx(
            odds_dispersion_ratio=1.25,
            same_market_total_anchor=True,
            total_mu_source="market_informed",
        ),
        CFG,
    )

    assert signal.grade == Grade.C
    assert signal.raw_grade == Grade.S
    assert "market_dispersion" in signal.reasons
    assert "market_informed_total" in signal.reasons


def test_market_dispersion_threshold_is_not_triggered_at_limit():
    signal = grade_signal(
        MarketType.X12,
        "home",
        0.60,
        0.45,
        1.85,
        ok_ctx(odds_dispersion_ratio=1.18),
        CFG,
    )

    assert signal.grade == Grade.S
    assert "market_dispersion" not in signal.reasons


def test_longshot_market_probability_caps_at_b():
    signal = grade_signal(MarketType.X12, "draw", 0.16, 0.05, 18.5, ok_ctx(), CFG)
    assert signal.grade == Grade.B
    assert "longshot_uncertainty" in signal.reasons


def test_no_market_yet_status():
    signal = grade_signal(
        MarketType.X12, "home", 0.60, None, None, ok_ctx(status="NO_MARKET_YET"), CFG
    )
    assert signal.grade == Grade.NO_MARKET_YET
    assert signal.status == "NO_MARKET_YET"


def test_odds_pending_status():
    signal = grade_signal(
        MarketType.X12, "home", 0.60, None, None, ok_ctx(status="ODDS_PENDING"), CFG
    )
    assert signal.grade == Grade.ODDS_PENDING
    assert signal.status == "ODDS_PENDING"


def test_d_status():
    signal = grade_signal(MarketType.X12, "home", 0.60, None, None, ok_ctx(status="D"), CFG)
    assert signal.grade == Grade.D
    assert signal.status == "D"
