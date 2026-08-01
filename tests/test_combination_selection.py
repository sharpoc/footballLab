from __future__ import annotations



def _module():
    from worldcup import combination_selection

    return combination_selection


def _pick(match_id, home, away, probability=0.70, market="1X2"):
    return {
        "match_id": match_id,
        "home_team": home,
        "away_team": away,
        "competition_label": "中超",
        "market": market,
        "selection": "home",
        "prediction_probability": probability,
        "match_decision": {
            "label": "MATCH_PICK",
            "market": market,
            "selection": "home",
            "p_hit_safe": probability,
            "odds": 1.80,
        },
    }


def test_combinations_are_derived_only_from_the_same_global_top_four():
    module = _module()
    top4 = [
        _pick("m1", "A", "B", 0.80),
        _pick("m2", "C", "D", 0.75),
        _pick("m3", "E", "F", 0.70),
        _pick("m4", "G", "H", 0.65),
    ]
    result = module.build_combination_research(top4)
    assert all(item["match_ids"] <= {"m1", "m2", "m3", "m4"} for item in result.parlay_2 + result.parlay_3)
    assert result.parlay_2
    assert result.parlay_3


def test_same_match_multiple_markets_are_rejected():
    module = _module()
    top4 = [
        _pick("m1", "A", "B", 0.80, market="1X2"),
        _pick("m1", "A", "B", 0.76, market="OU"),
        _pick("m2", "C", "D", 0.70),
        _pick("m3", "E", "F", 0.65),
    ]
    result = module.build_combination_research(top4)
    for item in result.parlay_2 + result.parlay_3:
        assert len(item["match_ids"]) == len(set(item["match_ids"]))
    assert "same_match_conflict" in result.rejection_reasons


def test_same_team_repetition_is_rejected():
    module = _module()
    top4 = [
        _pick("m1", "A", "B", 0.80),
        _pick("m2", "A", "C", 0.75),
        _pick("m3", "D", "E", 0.70),
        _pick("m4", "F", "G", 0.65),
    ]
    result = module.build_combination_research(top4)
    for item in result.parlay_2 + result.parlay_3:
        teams = item["teams"]
        assert len(teams) == len(set(teams))
    assert "same_team_conflict" in result.rejection_reasons


def test_two_and_three_fold_shortages_are_transparent():
    module = _module()
    result = module.build_combination_research([_pick("m1", "A", "B", 0.70)])
    assert result.parlay_2 == []
    assert result.parlay_3 == []
    assert "fewer_than_2_matches" in result.degradation_reasons
    assert "fewer_than_3_matches" in result.degradation_reasons


def test_combination_score_is_explicitly_independence_approximation_not_calibrated_joint_probability():
    module = _module()
    result = module.build_combination_research(
        [_pick("m1", "A", "B", 0.80), _pick("m2", "C", "D", 0.75), _pick("m3", "E", "F", 0.70)]
    )
    for item in result.parlay_2 + result.parlay_3:
        assert item["score_label"] == "独立性近似组合分数"
        assert item["is_calibrated_joint_probability"] is False
        assert item["approximate_score"] > 0
