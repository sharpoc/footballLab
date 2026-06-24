import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.odds_trend import (
    attach_trends,
    build_odds_movement,
    extract_match_trend,
    list_history_files,
)


def _hist_snapshot(at: str, odds_home: float, ah_line: float = -1.0, ou_line: float = 2.5) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-15T19:00:00+00:00",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"line": ou_line, "odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {
                        "line_home": ah_line,
                        "odds": {"home": 1.74, "away": 2.12},
                    },
                },
            }
        ],
    }


def test_extract_trend_keeps_only_changes_plus_first_and_last():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85),
        _hist_snapshot("2026-06-12T06:00:00+00:00", 1.85),  # 无变化，跳过
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.80),  # 变化，保留
        _hist_snapshot("2026-06-12T18:00:00+00:00", 1.80),  # 无变化但是最新点，保留
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    home_points = trend["1x2"]["home"]
    assert [p[1] for p in home_points] == [1.85, 1.8, 1.8]
    assert home_points[0][0] == "2026-06-12T00:00:00+00:00"
    assert home_points[-1][0] == "2026-06-12T18:00:00+00:00"
    # OU 全程无变化：只剩首点 + 最新点
    assert [p[1] for p in trend["ou_2_5"]["over"]] == [1.9, 1.9]


def test_extract_trend_records_ah_line_per_point():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85, ah_line=-1.0),
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.85, ah_line=-1.25),
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    ah_points = trend["ah_main"]["home"]
    assert ah_points[0][2] == -1.0
    assert ah_points[-1][2] == -1.25


def test_extract_trend_records_ou_line_per_point():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85, ou_line=2.5),
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.85, ou_line=3.5),
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    ou_points = trend["ou_2_5"]["over"]
    assert ou_points[0][2] == 2.5
    assert ou_points[-1][2] == 3.5


def test_extract_trend_caps_points_per_selection():
    snapshots = [
        _hist_snapshot(f"2026-06-12T{h:02d}:{m:02d}:00+00:00", 1.5 + h * 0.01 + m * 0.0001)
        for h in range(20)
        for m in (0, 30)
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa", max_points=30)

    assert len(trend["1x2"]["home"]) == 30
    # 上限裁剪保最新：末点必须是时间最大的那轮
    assert trend["1x2"]["home"][-1][0] == "2026-06-12T19:30:00+00:00"


def test_list_history_files_filters_by_filename_window():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "snapshot_20260601T000000Z-live.json"
        new = root / "snapshot_20260612T010000Z-live.json"
        raw = root / "odds_raw_20260612T010000Z-live.json.gz"
        for path in (old, new):
            path.write_text("{}")
        raw.write_text("x")

        files = list_history_files(root, since="2026-06-10T00:00:00+00:00")

        assert files == [new]


def test_attach_trends_writes_into_snapshot_matches():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds in (
            ("20260612T000000Z", 1.85),
            ("20260612T120000Z", 1.80),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds))
            )
        snapshot = _hist_snapshot("2026-06-12T13:00:00+00:00", 1.79)

        attach_trends(snapshot, root, now="2026-06-12T13:00:00+00:00")

        points = snapshot["matches"][0]["odds_trend"]["1x2"]["home"]
        assert [p[1] for p in points] == [1.85, 1.8]


def test_build_odds_movement_summarizes_1x2_ah_and_ou_diagnostics():
    trend = {
        "1x2": {
            "home": [
                ["2026-06-12T00:00:00+00:00", 2.0],
                ["2026-06-12T11:00:00+00:00", 1.9],
            ],
            "draw": [["2026-06-12T00:00:00+00:00", 3.4]],
            "away": [["2026-06-12T00:00:00+00:00", 4.5]],
        },
        "ah_main": {
            "home": [
                ["2026-06-12T00:00:00+00:00", 1.9, -0.5],
                ["2026-06-12T11:00:00+00:00", 1.85, -0.75],
            ],
            "away": [
                ["2026-06-12T00:00:00+00:00", 1.95, 0.5],
                ["2026-06-12T11:00:00+00:00", 1.98, 0.75],
            ],
        },
        "ou_2_5": {
            "over": [
                ["2026-06-12T00:00:00+00:00", 2.1, 2.5],
                ["2026-06-12T11:00:00+00:00", 1.8, 3.0],
            ],
            "under": [
                ["2026-06-12T00:00:00+00:00", 1.75, 2.5],
                ["2026-06-12T11:00:00+00:00", 2.05, 3.0],
            ],
        },
    }

    movement = build_odds_movement(trend)

    assert movement["schema_version"] == 1
    assert movement["window"] == "captured_history"
    assert movement["1x2"]["home"]["first_odds"] == 2.0
    assert movement["1x2"]["home"]["latest_odds"] == 1.9
    assert movement["1x2"]["home"]["relative_move"] == -0.05
    assert movement["1x2"]["home"]["direction"] == "shortened"
    assert movement["ah_main"]["first_line_home"] == -0.5
    assert movement["ah_main"]["latest_line_home"] == -0.75
    assert movement["ah_main"]["line_move_abs"] == 0.25
    assert movement["ah_main"]["favorite_line_direction"] == "home_strengthened"
    assert movement["ou"]["first_line"] == 2.5
    assert movement["ou"]["latest_line"] == 3.0
    assert movement["ou"]["total_line_move"] == 0.5
    assert movement["quality"]["enough_points"] is True
    assert movement["quality"]["line_changed"] is True
    assert movement["quality"]["stale"] is False
    assert movement["quality"]["noisy_or_sparse"] is False


def test_attach_trends_writes_odds_movement_diagnostic():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshots = [
            ("20260612T000000Z", 2.0, -0.5, 2.5),
            ("20260612T110000Z", 1.9, -0.75, 3.0),
        ]
        for at, odds, ah_line, ou_line in snapshots:
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds, ah_line=ah_line, ou_line=ou_line))
            )
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.88, ah_line=-0.75, ou_line=3.0)

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        movement = snapshot["matches"][0]["odds_movement"]
        assert movement["schema_version"] == 1
        assert movement["1x2"]["home"]["relative_move"] == -0.05
        assert movement["ah_main"]["line_move_abs"] == 0.25
        assert movement["ou"]["total_line_move"] == 0.5


def test_attach_trends_adds_signal_level_movement_shadow_without_changing_grade():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds, ah_line, ou_line in (
            ("20260612T000000Z", 2.0, -0.5, 2.5),
            ("20260612T110000Z", 1.9, -0.75, 3.0),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds, ah_line=ah_line, ou_line=ou_line))
            )
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.88, ah_line=-0.75, ou_line=3.0)
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "1X2_90min",
                "selection": "home",
                "grade": "B",
                "raw_grade": "S",
            },
            {
                "market_type": "AsianHandicap_90min",
                "selection": "home_-0.75",
                "grade": "B",
                "raw_grade": "S",
                "line": -0.75,
            },
            {
                "market_type": "OverUnder_90min",
                "selection": "over",
                "grade": "C",
                "raw_grade": "S",
                "line": 3.0,
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        x12, ah, ou = snapshot["matches"][0]["signals"]
        assert x12["grade"] == "B"
        assert x12["movement_shadow"]["activation"] == "shadow_only"
        assert x12["movement_shadow"]["market_odds_direction"] == "shortened"
        assert x12["movement_shadow"]["supports_signal"] is True
        assert ah["movement_shadow"]["line_direction_supports_signal"] is True
        assert ah["movement_shadow"]["supports_signal"] is True
        assert ou["movement_shadow"]["line_direction_supports_signal"] is True
        assert ou["movement_shadow"]["supports_signal"] is True


def test_attach_trends_promotes_supported_ah_candidate_to_official_grade():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds, ah_line, ou_line in (
            ("20260612T000000Z", 2.0, -0.5, 2.5),
            ("20260612T110000Z", 1.9, -0.75, 3.0),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds, ah_line=ah_line, ou_line=ou_line))
            )
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.88, ah_line=-0.75, ou_line=3.0)
        snapshot["matches"][0]["model"] = {
            "lineup_shadow": {
                "confirmed_starting_xi": True,
                "post_information_odds_available": True,
            }
        }
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "AsianHandicap_90min",
                "selection": "home_-0.75",
                "grade": "B",
                "raw_grade": "S",
                "line": -0.75,
                "reasons": ["ah_market_edge_missing"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": [
                    "official_grade_capped_by_ah_market_edge_missing",
                    "ah_validation_shadow_candidate_validated",
                ],
                "ah_validation_shadow": {"candidate_validated": True},
            },
            {
                "market_type": "OverUnder_90min",
                "selection": "over",
                "grade": "C",
                "raw_grade": "S",
                "line": 3.0,
                "reasons": ["market_informed_total"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": ["ou_shadow_candidate"],
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        ah, ou = snapshot["matches"][0]["signals"]
        assert ah["grade"] == "S"
        assert ah["promotion"]["activation"] == "official"
        assert ah["promotion"]["from_grade"] == "B"
        assert ah["promotion"]["to_grade"] == "S"
        assert "ah_candidate_promoted" in ah["reasons"]
        assert "ah_market_edge_missing" not in ah["reasons"]
        assert "candidate_grade" not in ah
        assert "candidate_reasons" not in ah
        assert ou["grade"] == "C"
        assert "promotion" not in ou


def test_attach_trends_does_not_promote_ah_candidate_when_line_moves_against_side():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, ah_line, away_odds in (
            ("20260612T000000Z", -2.0, 1.95),
            ("20260612T110000Z", -2.5, 1.75),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            payload = _hist_snapshot(iso, 1.2, ah_line=ah_line, ou_line=3.5)
            payload["matches"][0]["market"]["ah_main"]["odds"]["away"] = away_odds
            (root / f"snapshot_{at}-live.json").write_text(json.dumps(payload))
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.18, ah_line=-2.5, ou_line=3.5)
        snapshot["matches"][0]["model"] = {
            "lineup_shadow": {
                "confirmed_starting_xi": True,
                "post_information_odds_available": True,
            }
        }
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "AsianHandicap_90min",
                "selection": "away_+2.5",
                "grade": "B",
                "raw_grade": "S",
                "line": 2.5,
                "reasons": ["ah_market_edge_missing"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": [
                    "official_grade_capped_by_ah_market_edge_missing",
                    "ah_validation_shadow_candidate_validated",
                ],
                "ah_validation_shadow": {"candidate_validated": True},
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        signal = snapshot["matches"][0]["signals"][0]
        assert signal["movement_shadow"]["market_odds_direction"] == "shortened"
        assert signal["movement_shadow"]["line_direction"] == "home_strengthened"
        assert signal["movement_shadow"]["line_direction_supports_signal"] is False
        assert signal["movement_shadow"]["supports_signal"] is False
        assert signal["grade"] == "B"
        assert signal["candidate_grade"] == "S-candidate"
        assert "promotion" not in signal


def test_attach_trends_does_not_promote_extreme_favorite_ah_candidate():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, ah_line, away_odds in (
            ("20260612T000000Z", -2.5, 2.05),
            ("20260612T110000Z", -2.0, 1.85),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            payload = _hist_snapshot(iso, 1.2, ah_line=ah_line, ou_line=3.5)
            payload["matches"][0]["market"]["ah_main"]["odds"]["away"] = away_odds
            (root / f"snapshot_{at}-live.json").write_text(json.dumps(payload))
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.18, ah_line=-2.0, ou_line=3.5)
        snapshot["matches"][0]["model"] = {
            "lineup_shadow": {
                "confirmed_starting_xi": True,
                "post_information_odds_available": True,
            }
        }
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "AsianHandicap_90min",
                "selection": "away_+2",
                "grade": "B",
                "raw_grade": "S",
                "line": 2.0,
                "reasons": ["ah_market_edge_missing", "extreme_favorite_handicap"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": [
                    "official_grade_capped_by_ah_market_edge_missing",
                    "ah_validation_shadow_candidate_validated",
                ],
                "ah_validation_shadow": {"candidate_validated": True},
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        signal = snapshot["matches"][0]["signals"][0]
        assert signal["movement_shadow"]["line_direction"] == "away_strengthened"
        assert signal["movement_shadow"]["supports_signal"] is True
        assert signal["grade"] == "B"
        assert signal["candidate_grade"] == "S-candidate"
        assert "promotion" not in signal


def test_attach_trends_does_not_promote_ah_candidate_without_confirmed_lineup():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds, ah_line in (
            ("20260612T000000Z", 2.0, -0.5),
            ("20260612T110000Z", 1.9, -0.75),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds, ah_line=ah_line))
            )
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.88, ah_line=-0.75)
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "AsianHandicap_90min",
                "selection": "home_-0.75",
                "grade": "B",
                "raw_grade": "S",
                "line": -0.75,
                "reasons": ["ah_market_edge_missing"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": [
                    "official_grade_capped_by_ah_market_edge_missing",
                    "ah_validation_shadow_candidate_validated",
                ],
                "ah_validation_shadow": {"candidate_validated": True},
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        signal = snapshot["matches"][0]["signals"][0]
        assert signal["movement_shadow"]["supports_signal"] is True
        assert signal["grade"] == "B"
        assert signal["candidate_grade"] == "S-candidate"
        assert "promotion" not in signal


def test_attach_trends_does_not_promote_ah_candidate_before_post_information_odds():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds, ah_line in (
            ("20260612T000000Z", 2.0, -0.5),
            ("20260612T110000Z", 1.9, -0.75),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds, ah_line=ah_line))
            )
        snapshot = _hist_snapshot("2026-06-12T12:00:00+00:00", 1.88, ah_line=-0.75)
        snapshot["matches"][0]["model"] = {
            "lineup_shadow": {
                "confirmed_starting_xi": True,
                "post_information_odds_available": False,
            }
        }
        snapshot["matches"][0]["signals"] = [
            {
                "market_type": "AsianHandicap_90min",
                "selection": "home_-0.75",
                "grade": "B",
                "raw_grade": "S",
                "line": -0.75,
                "reasons": ["ah_market_edge_missing"],
                "candidate_grade": "S-candidate",
                "candidate_reasons": [
                    "official_grade_capped_by_ah_market_edge_missing",
                    "ah_validation_shadow_candidate_validated",
                ],
                "ah_validation_shadow": {"candidate_validated": True},
            },
        ]

        attach_trends(snapshot, root, now="2026-06-12T12:00:00+00:00")

        signal = snapshot["matches"][0]["signals"][0]
        assert signal["movement_shadow"]["supports_signal"] is True
        assert signal["grade"] == "B"
        assert signal["candidate_grade"] == "S-candidate"
        assert "promotion" not in signal
