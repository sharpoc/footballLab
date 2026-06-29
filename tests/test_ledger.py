from copy import deepcopy

from worldcup.ledger import (
    build_finished_view,
    build_snapshot_change_items,
    build_signal_explanation,
    build_summary_metrics,
    competition_id_for_match,
    competition_label_for_match,
    competition_options,
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
                "odds_updated_at": "2026-06-09T07:30:00+00:00",
                "refresh_plan": {
                    "next_update_at": "2026-06-11T17:30:00+00:00",
                    "policy_reason": "pre_90m_lineup_warmup",
                    "label": "T-1小时30分",
                    "description": "阵容/伤停预热",
                },
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
    assert rows[0]["competition_id"] == "fifa_world_cup_2026"
    assert rows[0]["competition_label"] == "2026 世界杯"
    assert rows[0]["kickoff_date"] == "2026 年 6 月 12 日 星期五"
    assert rows[0]["kickoff_time"] == "03:00"
    assert rows[0]["updated_time"] == "15:30"
    assert rows[0]["updated_label"] == "赔率源更新"
    assert rows[0]["next_update_time"] == "01:30"
    assert rows[0]["next_update_label"] == "T-1小时30分"
    assert rows[0]["next_update_description"] == "阵容/伤停预热"
    assert rows[0]["next_update_full"] == "2026 年 6 月 12 日 星期五 01:30"
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


def test_competition_helpers_use_snapshot_block_and_keep_legacy_default():
    snapshot = _snapshot()
    match = snapshot["matches"][0]

    assert competition_id_for_match(match) == "fifa_world_cup_2026"
    assert competition_label_for_match(match) == "2026 世界杯"
    assert competition_options(snapshot) == [
        {"id": "fifa_world_cup_2026", "label": "2026 世界杯"}
    ]

    match["competition"] = {
        "id": "csl_2026",
        "name": "中超 2026",
        "rating_policy": "club_rating_pending",
    }

    rows = project_signal_rows(snapshot)

    assert competition_id_for_match(match) == "csl_2026"
    assert competition_label_for_match(match) == "中超 2026"
    assert competition_options(snapshot) == [
        {"id": "csl_2026", "label": "中超 2026"},
        {"id": "fifa_world_cup_2026", "label": "2026 世界杯"},
    ]
    assert rows[0]["competition_id"] == "csl_2026"
    assert rows[0]["competition_label"] == "中超 2026"


def test_competition_options_include_finished_match_competitions():
    snapshot = {
        "matches": [],
        "finished": {
            "matches": [
                {
                    "competition": {"id": "csl_2026", "name": "中超 2026"},
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                }
            ]
        },
    }

    assert competition_options(snapshot) == [{"id": "csl_2026", "label": "中超 2026"}]


def test_project_signal_rows_includes_detail_items_for_expandable_analysis():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []

    rows = project_signal_rows(snapshot)
    details = {item["label"]: item["value"] for item in rows[0]["detail_items"]}

    assert details["核心判断"] == "模型概率高于去水后的市场概率。"
    assert details["盘口方向"] == "胜平负 - 主队"
    assert details["模型与市场"] == "模型 61.0%，市场 57.0%，Edge +4.1%"
    assert details["EV"] == "+5.2%"
    assert details["等级状态"] == "A / OK / 新鲜"
    assert details["更新时间"] == "赔率源更新：2026 年 6 月 9 日 星期二 15:30"
    assert details["下次更新"] == "T-1小时30分 阵容/伤停预热：2026 年 6 月 12 日 星期五 01:30"
    assert details["风险提示"] == "当前数据新鲜，未触发额外降级原因。"


def test_project_signal_rows_surfaces_candidate_grade_without_counting_as_strong():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    signal = snapshot["matches"][0]["signals"][0]
    signal.update(
        {
            "market_type": "AsianHandicap_90min",
            "selection": "home_-1",
            "line": -1.0,
            "grade": "B",
            "raw_grade": "S",
            "edge": None,
            "reasons": ["ah_market_edge_missing"],
            "candidate_grade": "S-candidate",
            "candidate_reasons": [
                "official_grade_capped_by_ah_market_edge_missing",
                "ah_validation_shadow_candidate_validated",
            ],
        }
    )

    rows = project_signal_rows(snapshot)
    details = {item["label"]: item["value"] for item in rows[0]["detail_items"]}
    metrics = build_summary_metrics(snapshot)

    assert rows[0]["grade"] == "B"
    assert rows[0]["candidate_grade"] == "S-candidate"
    assert rows[0]["candidate_reasons"] == [
        "official_grade_capped_by_ah_market_edge_missing",
        "ah_validation_shadow_candidate_validated",
    ]
    assert details["候选等级"] == "S-candidate（研究候选，不计入正式 S/A）"
    assert "AH shadow 验证通过" in details["候选依据"]
    assert metrics["strong_signals"]["value"] == 0
    assert metrics["watch_signals"]["value"] == 1


def test_project_signal_rows_marks_finished_1x2_prediction_hit():
    snapshot = _snapshot()
    snapshot["matches"][0]["result"] = {"status": "finished", "home_score": 2, "away_score": 0}

    rows = project_signal_rows(snapshot)
    details = {item["label"]: item["value"] for item in rows[0]["detail_items"]}

    assert rows[0]["prediction_result"] == {
        "status": "hit",
        "label": "命中",
        "detail": "赛果：墨西哥 2-0 南非；方向：主胜",
    }
    assert details["赛后验证"] == "命中；赛果：墨西哥 2-0 南非；方向：主胜"


def test_project_signal_rows_marks_finished_over_under_prediction_miss():
    snapshot = _snapshot()
    snapshot["matches"][0]["result"] = {"status": "finished", "home_score": 1, "away_score": 1}
    snapshot["matches"][0]["model"] = {"ou_2_5": {"over": 0.54, "under": 0.46}}
    snapshot["matches"][0]["market"] = {
        "ou_2_5": {"market_probs": {"over": 0.51, "under": 0.49}}
    }
    snapshot["matches"][0]["signals"] = [
        {
            "market_type": "OverUnder_90min",
            "selection": "Over",
            "line": 2.5,
            "grade": "A",
        }
    ]

    rows = project_signal_rows(snapshot)

    assert rows[0]["prediction_result"]["status"] == "miss"
    assert rows[0]["prediction_result"]["label"] == "未中"
    assert rows[0]["prediction_result"]["detail"] == "赛果：墨西哥 1-1 南非；方向：大 2.5"


def test_project_signal_rows_marks_finished_ah_prediction_push():
    snapshot = _snapshot()
    snapshot["matches"][0]["result"] = {"status": "finished", "home_score": 1, "away_score": 0}
    snapshot["matches"][0]["signals"] = [
        {
            "market_type": "AsianHandicap_90min",
            "selection": "home_-1",
            "line": -1.0,
            "grade": "B",
        }
    ]

    rows = project_signal_rows(snapshot)

    assert rows[0]["prediction_result"]["status"] == "push"
    assert rows[0]["prediction_result"]["label"] == "走水"
    assert rows[0]["prediction_result"]["detail"] == "赛果：墨西哥 1-0 南非；方向：主队 -1"


def test_project_signal_rows_attaches_recent_changes_to_matching_signal_row():
    previous = _snapshot()
    previous["run"]["stale_sources"] = []
    previous["matches"][0]["market"]["1x2"]["odds"] = {"home": 2.0}
    current = deepcopy(previous)
    current["matches"][0]["market"]["1x2"]["odds"]["home"] = 1.85
    current["matches"][0]["signals"][0]["grade"] = "S"
    current["matches"][0]["signals"][0]["ev"] = 0.092

    rows = project_signal_rows(current, previous_snapshot=previous)
    details = {item["label"]: item["value"] for item in rows[0]["detail_items"]}

    assert rows[0]["recent_change"]["tone"] == "strong"
    assert rows[0]["recent_change"]["detail"] == "等级 A → S；EV +5.2% → +9.2%；赔率 2.00 → 1.85"
    assert details["本轮变化"] == "等级 A → S；EV +5.2% → +9.2%；赔率 2.00 → 1.85"


def test_project_signal_rows_explains_quality_guard_reasons():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["data_quality"] = {
        "missing_odds": [],
        "missing_elo": [],
        "time_mismatches": [],
        "stale_sources": [],
        "source_errors": [],
    }
    snapshot["matches"][0]["signals"][0]["reasons"] = [
        "model_disagreement",
        "market_dispersion",
    ]

    rows = project_signal_rows(snapshot)
    risk_item = next(
        item for item in rows[0]["detail_items"] if item["label"] == "风险提示"
    )

    assert "Elo 与 Poisson 模型分歧" in risk_item["value"]
    assert "多家赔率报价分歧较大" in risk_item["value"]


def test_project_signal_rows_explains_x12_candidate_only_reasons():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["matches"][0]["signals"][0]["reasons"] = [
        "x12_draw_candidate_only",
        "x12_long_odds_candidate_only",
    ]

    rows = project_signal_rows(snapshot)
    risk_item = next(
        item for item in rows[0]["detail_items"] if item["label"] == "风险提示"
    )

    assert "平局强信号暂列研究候选" in risk_item["value"]
    assert "赔率高于正式强信号上限" in risk_item["value"]


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


def test_build_snapshot_change_items_compares_previous_signal_values():
    previous = _snapshot()
    previous["matches"][0]["market"]["1x2"]["odds"] = {"home": 2.0, "draw": 3.3, "away": 4.0}
    current = deepcopy(previous)
    current["snapshot_at"] = "2026-06-09T10:00:00+00:00"
    current["matches"][0]["market"]["1x2"]["odds"]["home"] = 1.85
    current["matches"][0]["market"]["1x2"]["market_probs"]["home"] = 0.54
    current["matches"][0]["signals"][0]["grade"] = "S"
    current["matches"][0]["signals"][0]["ev"] = 0.092
    current["matches"][0]["signals"][0]["edge"] = 0.071

    items = build_snapshot_change_items(
        previous,
        current,
        cfg={"ev_change": 0.03, "odds_move": 0.05},
    )

    assert len(items) == 1
    assert items[0]["title"] == "墨西哥 对 南非 | 胜平负 - 主队"
    assert "等级 A → S" in items[0]["detail"]
    assert "EV +5.2% → +9.2%" in items[0]["detail"]
    assert "Edge +4.1% → +7.1%" in items[0]["detail"]
    assert "市场概率 57.0% → 54.0%" in items[0]["detail"]
    assert "赔率 2.00 → 1.85" in items[0]["detail"]


def test_build_snapshot_change_items_handles_missing_previous_snapshot():
    items = build_snapshot_change_items(None, _snapshot())

    assert items == [
        {
            "tone": "neutral",
            "title": "暂无上一轮数据",
            "detail": "当前只有一轮快照，下一次更新后会展示等级、EV、概率和赔率变化。",
        }
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


def _snapshot_with_finished() -> dict:
    return {
        "snapshot_at": "2026-06-12T08:00:00+00:00",
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "S", "line": None}
                ],
                "odds_trend": {
                    "1x2": {
                        "home": [
                            ["2026-06-10T00:00:00+00:00", 1.85],
                            ["2026-06-11T18:00:00+00:00", 1.78],
                        ]
                    },
                },
            },
            {
                "kickoff_at_utc": "2026-06-13T19:00:00+00:00",
                "home_team": "Canada",
                "away_team": "Qatar",
                "home_canonical": "canada",
                "away_canonical": "qatar",
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "A", "line": None}
                ],
                "odds_trend": {
                    "1x2": {
                        "home": [
                            ["2026-06-12T00:00:00+00:00", 1.60],
                            ["2026-06-12T06:00:00+00:00", 1.55],
                        ]
                    },
                },
            },
        ],
        "finished": {
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "home_canonical": "mexico",
                    "away_canonical": "south_africa",
                    "stage": "Matchday 1",
                    "group": "Group A",
                    "result": {"home_score": 2, "away_score": 0},
                    "closing_snapshot_at": "2026-06-11T18:00:00+00:00",
                    "closing_signals": [
                        {
                            "market_type": "1X2_90min",
                            "selection": "home",
                            "line": None,
                            "grade": "S",
                            "odds": 1.78,
                            "prediction": {"label": "命中", "detail": "全场 2-0"},
                        },
                        {
                            "market_type": "1X2_90min",
                            "selection": "away",
                            "line": None,
                            "grade": "C",
                            "odds": 4.8,
                            "prediction": {"label": "未中", "detail": "全场 2-0"},
                        },
                    ],
                    "odds_trend": {
                        "1x2": {
                            "home": [
                                ["2026-06-10T00:00:00+00:00", 1.85],
                                ["2026-06-11T18:00:00+00:00", 1.78],
                            ]
                        }
                    },
                }
            ],
            "tally": {
                "S": {"hit": 1, "miss": 0, "push": 0},
                "A": {"hit": 0, "miss": 0, "push": 0},
            },
            "skipped_no_closing": 0,
        },
    }


def test_project_signal_rows_skips_matches_present_in_finished():
    rows = project_signal_rows(_snapshot_with_finished())

    assert all(row["kickoff_at_utc"] != "2026-06-11T19:00:00+00:00" for row in rows)
    assert any(row.get("source_matchup") == "Canada vs Qatar" for row in rows)


def test_signal_rows_carry_trend_points_for_their_selection():
    rows = project_signal_rows(_snapshot_with_finished())

    canada = next(row for row in rows if row.get("source_matchup") == "Canada vs Qatar")
    assert [p[1] for p in canada["odds_trend_points"]] == [1.6, 1.55]


def test_summary_metrics_include_sa_record_when_finished_present():
    metrics = build_summary_metrics(_snapshot_with_finished())

    assert metrics["record_s"]["value"] == "命中 1 · 未中 0 · 走水 0 · 命中率 100%"
    assert metrics["record_a"]["value"] == "命中 0 · 未中 0 · 走水 0 · 命中率 —"
    assert metrics["upcoming_matches"]["value"] == 1


def test_build_finished_view_groups_by_beijing_day():
    view = build_finished_view(_snapshot_with_finished())

    assert len(view["days"]) == 1
    assert view["summary"]["match_count"] == 1
    assert view["summary"]["signal_count"] == 2
    assert view["summary"]["skipped_no_closing"] == 0
    assert view["summary"]["sample"]["sample_too_small"] is True
    day = view["days"][0]
    assert day["date_label"].startswith("2026 年 6 月 12 日")
    match = day["matches"][0]
    assert match["score_label"] == "2 - 0"
    assert match["sa_badges"][0]["grade"] == "S"
    assert match["sa_badges"][0]["outcome"] == "命中"
    assert all(badge["grade"] in ("S", "A") for badge in match["sa_badges"])
    assert any(item["grade"] == "C" for item in match["detail_signals"])
