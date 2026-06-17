import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.postmatch_diagnostics import build_postmatch_diagnostics, main


def _finished_snapshot() -> dict:
    return {
        "snapshot_at": "2026-06-12T04:00:00+00:00",
        "finished": {
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "home_canonical": "mexico",
                    "away_canonical": "south_africa",
                    "result": {"home_score": 0, "away_score": 1},
                    "closing_snapshot_at": "2026-06-11T18:00:00+00:00",
                    "closing_signals": [
                        {
                            "market_type": "1X2_90min",
                            "selection": "home",
                            "grade": "S",
                            "line": None,
                            "odds": 1.8,
                            "prediction": {"label": "未中"},
                        },
                        {
                            "market_type": "OverUnder_90min",
                            "selection": "under",
                            "grade": "B",
                            "line": 2.5,
                            "odds": 2.0,
                            "prediction": {"label": "命中"},
                        },
                    ],
                },
                {
                    "kickoff_at_utc": "2026-06-12T19:00:00+00:00",
                    "home_team": "Canada",
                    "away_team": "Qatar",
                    "home_canonical": "canada",
                    "away_canonical": "qatar",
                    "result": {"home_score": 3, "away_score": 1},
                    "closing_snapshot_at": "2026-06-12T18:00:00+00:00",
                    "closing_signals": [
                        {
                            "market_type": "OverUnder_90min",
                            "selection": "over",
                            "grade": "A",
                            "line": 2.5,
                            "odds": 1.9,
                            "prediction": {"label": "命中"},
                        }
                    ],
                },
            ],
            "skipped_no_closing": 0,
        },
    }


def _closing_history() -> list[tuple[str, dict]]:
    return [
        (
            "snapshot_20260611T180000Z-live.json",
            {
                "snapshot_at": "2026-06-11T18:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "home_canonical": "mexico",
                        "away_canonical": "south_africa",
                        "model": {
                            "probability_families": {
                                "schema_version": 1,
                                "active_signal_family": "model_market_total",
                                "families": {
                                    "model_raw": {
                                        "combined_1x2": {
                                            "home": 0.52,
                                            "draw": 0.26,
                                            "away": 0.22,
                                        }
                                    },
                                    "model_market_total": {
                                        "combined_1x2": {
                                            "home": 0.62,
                                            "draw": 0.22,
                                            "away": 0.16,
                                        }
                                    },
                                    "market_only": {
                                        "1x2": {
                                            "home": 0.49,
                                            "draw": 0.28,
                                            "away": 0.23,
                                        }
                                    },
                                },
                            }
                        },
                        "odds_movement": {
                            "schema_version": 1,
                            "quality": {
                                "enough_points": True,
                                "line_changed": True,
                                "sparse": False,
                            },
                        },
                        "signals": [
                            {
                                "market_type": "1X2_90min",
                                "selection": "home",
                                "grade": "S",
                                "raw_grade": "S",
                                "line": None,
                                "ev": 0.11,
                                "edge": 0.13,
                                "reasons": ["reverse_market", "market_dispersion"],
                            }
                        ],
                    }
                ],
            },
        ),
        (
            "snapshot_20260612T180000Z-live.json",
            {
                "snapshot_at": "2026-06-12T18:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-12T19:00:00+00:00",
                        "home_team": "Canada",
                        "away_team": "Qatar",
                        "home_canonical": "canada",
                        "away_canonical": "qatar",
                        "model": {
                            "probability_families": {
                                "schema_version": 1,
                                "active_signal_family": "model_market_total",
                                "families": {
                                    "model_raw": {
                                        "ou": {
                                            "line": 2.5,
                                            "probs": {"over": 0.55, "under": 0.45},
                                        }
                                    },
                                    "model_market_total": {
                                        "ou": {
                                            "line": 2.5,
                                            "probs": {"over": 0.56, "under": 0.44},
                                        }
                                    },
                                    "market_only": {
                                        "ou": {
                                            "line": 2.5,
                                            "probs": {"over": 0.53, "under": 0.47},
                                        }
                                    },
                                },
                            }
                        },
                        "odds_movement": {
                            "schema_version": 1,
                            "quality": {
                                "enough_points": True,
                                "line_changed": False,
                                "sparse": False,
                            },
                        },
                        "signals": [
                            {
                                "market_type": "OverUnder_90min",
                                "selection": "over",
                                "grade": "A",
                                "raw_grade": "A",
                                "line": 2.5,
                                "ev": 0.04,
                                "edge": 0.03,
                                "reasons": ["market_informed_total"],
                            }
                        ],
                    }
                ],
            },
        ),
    ]


def _write_history(path: Path) -> None:
    path.mkdir(parents=True)
    for name, payload in _closing_history():
        (path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_build_postmatch_diagnostics_enriches_finished_signals_from_closing_history():
    with TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"
        _write_history(history)

        report = build_postmatch_diagnostics(
            _finished_snapshot(),
            history,
            generated_at="2026-06-17T08:00:00+00:00",
            min_sample=20,
        )

        assert report["schema_version"] == 1
        assert report["summary"]["match_count"] == 2
        assert report["summary"]["strong_signal_count"] == 2
        assert report["summary"]["decided_strong_signal_count"] == 2
        assert report["summary"]["sample_too_small"] is True
        assert report["summary"]["source_coverage"] == {
            "closing_entry": 2,
            "full_signal": 2,
            "reason": 2,
            "probability_family": 2,
            "odds_movement": 2,
        }
        assert report["buckets"]["by_outcome"] == {"hit": 1, "miss": 1, "push": 0}
        assert report["buckets"]["by_market"]["1X2_90min"]["miss"] == 1
        assert report["buckets"]["by_market"]["OverUnder_90min"]["hit"] == 1
        assert report["buckets"]["by_reason"]["reverse_market"]["miss"] == 1
        assert report["buckets"]["by_reason"]["market_informed_total"]["hit"] == 1

        miss_row = report["signals"][0]
        assert miss_row["outcome"] == "miss"
        assert miss_row["match_label"] == "Mexico vs South Africa"
        assert miss_row["reasons"] == ["reverse_market", "market_dispersion"]
        assert miss_row["odds_movement_quality"]["line_changed"] is True
        assert miss_row["probability_family_probs"]["model_raw"] == 0.52
        assert miss_row["probability_family_probs"]["model_market_total"] == 0.62
        assert miss_row["probability_family_probs"]["market_only"] == 0.49
        assert miss_row["probability_family_deltas"]["model_raw_minus_active"] == -0.1
        assert miss_row["source_coverage"] == {
            "closing_entry": True,
            "full_signal": True,
            "reason": True,
            "probability_family": True,
            "odds_movement": True,
        }
        assert miss_row["diagnostic_flags"] == [
            "miss",
            "line_changed",
            "raw_active_gap_ge_5pp",
        ]


def test_main_writes_postmatch_diagnostics_report():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        _write_history(history)
        snapshot = root / "analysis_snapshot.json"
        snapshot.write_text(json.dumps(_finished_snapshot()), encoding="utf-8")
        out = root / "diagnostics" / "postmatch_diagnostics.json"

        code = main(
            [
                "--snapshot",
                str(snapshot),
                "--history",
                str(history),
                "--out",
                str(out),
                "--generated-at",
                "2026-06-17T08:00:00+00:00",
            ]
        )

        assert code == 0
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["summary"]["strong_signal_count"] == 2
        assert written["research_boundary"] == "仅用于研究分析，不构成投注建议"


def test_postmatch_diagnostics_prefers_frozen_v2_signal_fields_without_history():
    snapshot = {
        "snapshot_at": "2026-06-13T04:00:00+00:00",
        "finished": {
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-12T19:00:00+00:00",
                    "home_team": "Canada",
                    "away_team": "Qatar",
                    "home_canonical": "canada",
                    "away_canonical": "qatar",
                    "result": {"home_score": 0, "away_score": 1},
                    "closing_snapshot_at": "2026-06-12T18:00:00+00:00",
                    "closing_signals": [
                        {
                            "diagnostic_schema_version": 2,
                            "market_type": "1X2_90min",
                            "selection": "home",
                            "grade": "S",
                            "raw_grade": "S",
                            "line": None,
                            "odds": 1.8,
                            "ev": 0.12,
                            "edge": 0.10,
                            "reasons": ["reverse_market"],
                            "prediction": {"label": "未中"},
                            "probability_family_probs": {
                                "model_raw": 0.58,
                                "model_market_total": 0.64,
                                "market_only": 0.52,
                            },
                            "probability_family_deltas": {
                                "active_family": "model_market_total",
                                "model_raw_minus_active": -0.06,
                                "active_minus_market": 0.12,
                            },
                            "odds_movement_quality": {
                                "enough_points": True,
                                "line_changed": True,
                                "sparse": False,
                            },
                            "diagnostic_flags": [
                                "miss",
                                "line_changed",
                                "raw_active_gap_ge_5pp",
                            ],
                        }
                    ],
                }
            ],
            "skipped_no_closing": 0,
        },
    }
    with TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"
        history.mkdir()

        report = build_postmatch_diagnostics(snapshot, history)

        assert report["summary"]["source_coverage"] == {
            "closing_entry": 0,
            "full_signal": 1,
            "reason": 1,
            "probability_family": 1,
            "odds_movement": 1,
        }
        row = report["signals"][0]
        assert row["reasons"] == ["reverse_market"]
        assert row["probability_family_probs"]["model_market_total"] == 0.64
        assert row["odds_movement_quality"]["line_changed"] is True
        assert row["diagnostic_flags"] == [
            "miss",
            "line_changed",
            "raw_active_gap_ge_5pp",
        ]
