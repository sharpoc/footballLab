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
                "market": {"1x2": {"probs": {"home": 0.57, "draw": 0.25, "away": 0.18}}},
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
    assert format_market_label("1X2_90min", "home", None) == "1X2 - Home"
    assert format_market_label("OverUnder_90min", "Over", 2.5) == "O/U 2.5 - Over"
    assert format_market_label("AsianHandicap_90min", "home", -0.25) == "AH -0.25 - Home"


def test_derive_quality_status_warns_on_stale_or_missing_data():
    status = derive_quality_status(_snapshot())

    assert status["label"] == "WARN"
    assert status["tone"] == "warn"
    assert "stale_sources" in status["reasons"]
    assert "missing_odds" in status["reasons"]


def test_derive_quality_status_attention_on_source_errors():
    snapshot = _snapshot()
    snapshot["run"]["stale_sources"] = []
    snapshot["data_quality"] = {}
    snapshot["run"]["source_errors"] = [{"source": "theoddsapi", "error": "timeout"}]

    status = derive_quality_status(snapshot)

    assert status["label"] == "ATTENTION"
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

    assert status["label"] == "GOOD"
    assert status["tone"] == "ok"
    assert status["reasons"] == []


def test_build_summary_metrics_counts_signal_grades():
    metrics = build_summary_metrics(_snapshot())

    assert metrics["upcoming_matches"]["value"] == 2
    assert metrics["strong_signals"]["value"] == 1
    assert metrics["watch_signals"]["value"] == 0
    assert metrics["weak_signals"]["value"] == 0
    assert metrics["stale_sources"]["value"] == 1
    assert metrics["overall_quality"]["value"] == "WARN"
    assert metrics["grade_counts"]["value"] == {"A": 1}


def test_project_signal_rows_expands_signals_without_money_fields():
    rows = project_signal_rows(_snapshot())

    assert len(rows) == 1
    assert rows[0]["matchup"] == "Mexico vs South Africa"
    assert rows[0]["kickoff_date"] == "Thursday, Jun 11, 2026"
    assert rows[0]["market_label"] == "1X2 - Home"
    assert rows[0]["model_prob"] == "61.0%"
    assert rows[0]["market_prob"] == "57.0%"
    assert rows[0]["edge"] == "+4.1%"
    assert rows[0]["ev"] == "+5.2%"
    assert rows[0]["grade"] == "A"
    assert "stake" not in rows[0]
    assert "bet_amount" not in rows[0]
    assert "bankroll" not in rows[0]
    assert "payout" not in rows[0]
    assert "unit" not in rows[0]


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
        "Early High vs Match",
        "Early Low vs Match",
        "Late vs Match",
    ]


def test_build_signal_explanation_matches_plan_strings_and_is_safe():
    cases = [
        (
            {"market_type": "1X2_90min"},
            False,
            "Model probability is above the devigged market probability.",
        ),
        (
            {"market_type": "OverUnder_90min"},
            False,
            "Model total-goals distribution differs from the market total.",
        ),
        (
            {"market_type": "AsianHandicap_90min"},
            False,
            "Settlement EV is positive at the current handicap line.",
        ),
        (
            {"market_type": "Unknown"},
            False,
            "Model and market estimates differ enough to flag for review.",
        ),
        (
            {"market_type": "1X2_90min"},
            True,
            "Signal is capped because one or more inputs are stale or missing.",
        ),
    ]

    for signal, stale, expected in cases:
        text = build_signal_explanation(signal, stale=stale)

        assert text == expected
        assert "bet" not in text.lower()
        assert "stake" not in text.lower()
