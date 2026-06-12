from worldcup.line_move_report import build_report, move_bucket, odds_move_bucket


def _row(open_line, close_line, **extra):
    base = {
        "match_id": "m1",
        "kickoff_at_utc": "2022-11-22T12:00:00Z",
        "home_team": "Argentina",
        "away_team": "Saudi Arabia",
        "home_score": "1",
        "away_score": "2",
        "home_elo_before": "2143.0",
        "away_elo_before": "1640.0",
        "neutral": "1",
        "odds_home": "1.17",
        "odds_draw": "7.0",
        "odds_away": "19.0",
        "odds_over": "1.66",
        "odds_under": "2.26",
        "ah_line": str(close_line),
        "odds_ah_home": "1.95",
        "odds_ah_away": "1.87",
        "open_odds_home": "1.20",
        "open_odds_draw": "6.5",
        "open_odds_away": "17.0",
        "open_odds_over": "1.72",
        "open_odds_under": "2.15",
        "open_ah_line": str(open_line),
        "open_odds_ah_home": "1.85",
        "open_odds_ah_away": "1.97",
    }
    base.update(extra)
    return base


def test_move_bucket_boundaries():
    assert move_bucket(0.0) == "0.00"
    assert move_bucket(0.25) == "0.25"
    assert move_bucket(0.5) == "0.50"
    assert move_bucket(0.75) == ">=0.75"
    assert move_bucket(1.5) == ">=0.75"


def test_odds_move_bucket_boundaries():
    assert odds_move_bucket(0.0) == "<2%"
    assert odds_move_bucket(0.02) == "2-5%"
    assert odds_move_bucket(0.05) == "5-10%"
    assert odds_move_bucket(0.10) == ">=10%"
    assert odds_move_bucket(0.30) == ">=10%"


def test_build_report_groups_by_1x2_odds_move():
    rows = [
        _row(-1.75, -1.75, match_id="m_a"),
        _row(-1.75, -1.75, match_id="m_b", open_odds_home="1.17"),
    ]
    report = build_report(rows)

    assert report["sample"]["n_with_1x2_open_close"] == 2
    buckets = {b["bucket"]: b for b in report["by_1x2_move"]}
    assert buckets["2-5%"]["n_matches"] == 1
    assert buckets["<2%"]["n_matches"] == 1


def test_build_report_emits_1x2_move_even_without_ah_columns():
    rows = [
        _row(
            "",
            "",
            match_id="m_no_ah",
            ah_line="",
            odds_ah_home="",
            odds_ah_away="",
            open_ah_line="",
            open_odds_ah_home="",
            open_odds_ah_away="",
        )
    ]
    report = build_report(rows)

    assert report["sample"]["n_with_both_ah_lines"] == 0
    assert report["by_abs_move"] == []
    assert len(report["by_1x2_move"]) == 1


def test_build_report_groups_by_abs_line_move():
    rows = [
        _row(-1.75, -1.75, match_id="m_still"),
        _row(-2.25, -1.75, match_id="m_moved"),
    ]
    report = build_report(rows)

    assert report["sample"]["n_rows"] == 2
    assert report["sample"]["n_with_both_ah_lines"] == 2
    buckets = {b["bucket"]: b for b in report["by_abs_move"]}
    assert buckets["0.00"]["n_matches"] == 1
    assert buckets["0.50"]["n_matches"] == 1


def test_build_report_settles_ah_return_per_unit():
    rows = [_row(-1.75, -1.75)]
    report = build_report(rows)
    bucket = next(b for b in report["by_abs_move"] if b["bucket"] == "0.00")

    assert bucket["n_matches"] == 1
    assert bucket["ah"]["n_signals"] == 1
    assert abs(bucket["ah"]["mean_return"] - (-1.0)) < 1e-9


def test_build_report_skips_rows_without_open_line():
    rows = [_row(-1.75, -1.75, open_ah_line="")]
    report = build_report(rows)

    assert report["sample"]["n_with_both_ah_lines"] == 0
    assert report["by_abs_move"] == []
