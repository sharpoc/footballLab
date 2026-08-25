from worldcup.league_statistics import build_league_statistics, crossed_evaluation_thresholds


def _block(competition_id: str, hit: int, miss: int, *, scope: str = "observed_schema_v2_match_pick_only") -> dict:
    return {
        "competition_id": competition_id,
        "statistics_scope": scope,
        "decision_tally": {"hit": hit, "miss": miss, "push": 0, "no_pick": 0},
        "decision_coverage": {"finished_result_count": hit + miss, "closing_available_count": hit + miss,
            "missing_closing_count": 0, "decision_available_count": hit + miss,
            "missing_decision_count": 0, "invalid_decision_count": 0,
            "unresolved_count": 0, "legacy_decision_count": 0},
    }


def test_statistics_exclude_csl_and_legacy_from_six_league_aggregate():
    report = build_league_statistics([
        _block("epl_2026_27", 2, 1),
        _block("serie_a_2026_27", 1, 1),
        _block("csl_2026", 100, 0),
        _block("laliga_2026_27", 50, 0, scope="legacy"),
    ])
    assert report["aggregate"]["decision_tally"] == {"hit": 3, "miss": 2, "push": 0, "no_pick": 0}
    assert report["aggregate"]["decision_sample"]["hit_rate"] == 0.6
    assert "csl_2026" not in report["competitions"]


def test_thresholds_report_every_crossed_unsent_boundary_once():
    """Dropping a crossed unsent boundary would suppress the required offline review signal."""
    assert crossed_evaluation_thresholds(19, 101, {50}) == [20, 100]
    assert crossed_evaluation_thresholds(101, 101, set()) == []
