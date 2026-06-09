from worldcup.ledger import (
    build_signal_explanation,
    build_summary_metrics,
    derive_quality_status,
    format_market_label,
    format_percent,
    format_probability,
    project_signal_rows,
)


def _snapshot():
    return {
        "snapshot_at": "2026-06-09T08:00:00+00:00",
        "run": {
            "run_id": "20260609T080000Z-live",
            "quota": {"theoddsapi": {"last": 3, "remaining": 494, "used": 6}},
            "stale_sources": ["theoddsapi"],
            "source_errors": [],
        },
        "counts": {"fixtures": 104, "matches": 2, "odds_events": 72},
        "data_quality": {
            "missing_odds": ["Canada vs Qatar"],
            "missing_elo": [],
            "time_mismatches": ["Brazil vs Haiti"],
        },
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "market": {"1x2": {"market_probs": {"home": 0.57, "draw": 0.25, "away": 0.18}}},
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "A",
                        "ev": 0.052,
                        "edge": 0.041,
                        "status": "OK",
                    }
                ],
            },
            {
                "kickoff_at_utc": "2026-06-12T01:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group B",
                "home_team": "Canada",
                "away_team": "Qatar",
                "signals": [],
            },
        ],
    }


def test_format_percent_handles_values_and_missing():
    assert format_percent(0.041) == "+4.1%"
    assert format_percent(-0.004) == "-0.4%"
    assert format_percent(None) == "—"


def test_format_probability_is_unsigned_and_handles_missing():
    assert format_probability(0.61) == "61.0%"
    assert format_probability(-0.004) == "-0.4%"
    assert format_probability(None) == "—"


def test_format_market_label_maps_known_market_types():
    assert format_market_label("1X2_90min", "home", None) == "胜平负 - 主队"
    assert format_market_label("OverUnder_90min", "Over", 2.5) == "大小球 2.5 - 大球"
    assert format_market_label("AsianHandicap_90min", "home", -0.25) == "亚洲让球 -0.25 - 主队"
    assert format_market_label("AsianHandicap_90min", "home_-1", -1.0) == "亚洲让球 -1 - 主队"
    assert format_market_label("AsianHandicap_90min", "away_+1", 1.0) == "亚洲让球 +1 - 客队"


def test_derive_quality_status_warns_on_stale_or_missing_data():
    status = derive_quality_status(_snapshot())

    assert status["label"] == "预警"
    assert status["tone"] == "warn"
    assert "stale_sources" in status["reasons"]
    assert "missing_odds" in status["reasons"]


def test_quality_status_and_metrics_do_not_double_count_duplicate_sources():
    snapshot = _snapshot()
    snapshot["data_quality"]["stale_sources"] = ["theoddsapi"]
    snapshot["run"]["stale_sources"] = ["theoddsapi"]

    status = derive_quality_status(snapshot)
    metrics = build_summary_metrics(snapshot)

    assert status["reasons"].count("stale_sources") == 1
    assert metrics["stale_sources"]["value"] == 1


def test_derive_quality_status_attention_on_source_errors():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["data_quality"] = {}
    snapshot["run"]["source_errors"] = [{"source": "theoddsapi", "error": "timeout"}]

    status = derive_quality_status(snapshot)

    assert status["label"] == "需关注"
    assert status["tone"] == "error"
    assert status["reasons"] == ["source_errors"]


def test_derive_quality_status_good_for_clean_snapshot():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["run"]["source_errors"] = []
    snapshot["data_quality"] = {
        "missing_odds": [],
        "missing_elo": [],
        "time_mismatches": [],
        "stale_sources": [],
        "source_errors": [],
    }

    status = derive_quality_status(snapshot)

    assert status["label"] == "正常"
    assert status["tone"] == "ok"
    assert status["reasons"] == []


def test_build_summary_metrics_counts_signal_grades():
    metrics = build_summary_metrics(_snapshot())

    assert metrics["upcoming_matches"]["value"] == 2
    assert metrics["strong_signals"]["value"] == 1
    assert metrics["watch_signals"]["value"] == 0
    assert metrics["weak_signals"]["value"] == 0
    assert metrics["stale_sources"]["value"] == 1
    assert metrics["overall_quality"]["value"] == "预警"
    assert metrics["grade_counts"]["value"] == {"A": 1}


def test_project_signal_rows_expands_signals_without_money_fields():
    rows = project_signal_rows(_snapshot())

    assert len(rows) == 1
    assert rows[0]["matchup"] == "墨西哥 对 南非"
    assert rows[0]["kickoff_date"] == "2026 年 6 月 12 日 星期五"
    assert rows[0]["kickoff_time"] == "03:00"
    assert rows[0]["stage_group"] == "小组赛第 1 轮 | A 组"
    assert rows[0]["market_label"] == "胜平负 - 主队"
    assert rows[0]["model_prob"] == "61.0%"
    assert rows[0]["market_prob"] == "57.0%"
    assert rows[0]["edge"] == "+4.1%"
    assert rows[0]["ev"] == "+5.2%"
    assert rows[0]["grade"] == "A"
    for row in rows:
        assert "stake" not in row
        assert "bet_amount" not in row
        assert "bankroll" not in row
        assert "payout" not in row
        assert "unit" not in row


def test_project_signal_rows_reads_realistic_over_under_probabilities():
    snapshot = {
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "model": {"ou_2_5": {"over": 0.54, "under": 0.46}},
                "market": {"ou_2_5": {"market_probs": {"over": 0.51, "under": 0.49}}},
                "signals": [
                    {
                        "market_type": "OverUnder_90min",
                        "selection": "Over",
                        "line": 2.5,
                        "grade": "B",
                    }
                ],
            }
        ]
    }

    rows = project_signal_rows(snapshot)

    assert rows[0]["matchup"] == "墨西哥 对 南非"
    assert rows[0]["market_label"] == "大小球 2.5 - 大球"
    assert rows[0]["model_prob"] == "54.0%"
    assert rows[0]["market_prob"] == "51.0%"


def test_project_signal_rows_labels_realistic_asian_handicap_selection():
    snapshot = {
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [
                    {
                        "market_type": "AsianHandicap_90min",
                        "selection": "away_+1",
                        "line": 1.0,
                        "grade": "C",
                    }
                ],
            }
        ]
    }

    rows = project_signal_rows(snapshot)

    assert rows[0]["market_label"] == "亚洲让球 +1 - 客队"


def test_project_signal_rows_sorts_by_kickoff_then_grade_descending():
    snapshot = {
        "matches": [
            {
                "kickoff_at_utc": "2026-06-12T01:00:00+00:00",
                "home_team": "Late",
                "away_team": "Match",
                "signals": [{"market_type": "1X2_90min", "selection": "home", "grade": "S"}],
            },
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Early Low",
                "away_team": "Match",
                "signals": [{"market_type": "1X2_90min", "selection": "home", "grade": "B"}],
            },
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Early High",
                "away_team": "Match",
                "signals": [{"market_type": "1X2_90min", "selection": "home", "grade": "A"}],
            },
        ]
    }

    rows = project_signal_rows(snapshot)

    assert [row["matchup"] for row in rows] == [
        "Early High 对 Match",
        "Early Low 对 Match",
        "Late 对 Match",
    ]


def test_build_signal_explanation_matches_plan_strings_and_is_safe():
    cases = [
        (
            {"market_type": "1X2_90min"},
            False,
            "模型概率高于去水后的市场概率。",
        ),
        (
            {"market_type": "OverUnder_90min"},
            False,
            "模型总进球分布与市场大小球预期存在差异。",
        ),
        (
            {"market_type": "AsianHandicap_90min"},
            False,
            "当前让球盘口下的结算 EV 为正。",
        ),
        (
            {"market_type": "Unknown"},
            False,
            "模型估计与市场估计差异足够大，值得复核。",
        ),
        (
            {"market_type": "1X2_90min"},
            True,
            "由于一个或多个输入过期或缺失，信号已被降级。",
        ),
    ]

    for signal, stale, expected in cases:
        text = build_signal_explanation(signal, stale=stale)

        assert text == expected
        assert "bet" not in text.lower()
        assert "stake" not in text.lower()
