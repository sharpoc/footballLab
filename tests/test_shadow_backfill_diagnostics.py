import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.shadow_backfill_diagnostics import build_shadow_backfill_diagnostics, main


def _snapshot(at: str, home_odds: float, ah_line: float) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "model": {
                    "lambdas": {"home": 2.6, "away": 1.0},
                },
                "market": {
                    "1x2": {"odds": {"home": home_odds, "draw": 3.6, "away": 4.8}},
                    "ah_main": {
                        "line_home": ah_line,
                        "odds": {"home": 1.9, "away": 1.9},
                        "n_books_by_selection": {"home": 3, "away": 3},
                    },
                },
                "signals": [
                    {
                        "market_type": "AsianHandicap_90min",
                        "selection": f"home_{ah_line:g}",
                        "line": ah_line,
                        "grade": "B",
                        "raw_grade": "S",
                        "ev": 0.09,
                        "edge": None,
                        "reasons": ["ah_market_edge_missing"],
                    }
                ],
            }
        ],
    }


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
                    "result": {"home_score": 2, "away_score": 0},
                    "closing_snapshot_at": "2026-06-11T18:00:00+00:00",
                    "closing_signals": [
                        {
                            "market_type": "AsianHandicap_90min",
                            "selection": "home_-1",
                            "line": -1.0,
                            "grade": "B",
                            "raw_grade": "S",
                            "prediction": {"status": "hit", "label": "命中"},
                        }
                    ],
                }
            ],
            "skipped_no_closing": 0,
        },
    }


def _write_history(root: Path) -> None:
    root.mkdir(parents=True)
    for name, payload in (
        ("snapshot_20260611T170000Z-live.json", _snapshot("2026-06-11T17:00:00+00:00", 1.9, -0.5)),
        ("snapshot_20260611T180000Z-live.json", _snapshot("2026-06-11T18:00:00+00:00", 1.8, -1.0)),
    ):
        (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_build_shadow_backfill_diagnostics_recomputes_missing_shadow_fields():
    with TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"
        _write_history(history)

        report = build_shadow_backfill_diagnostics(
            _finished_snapshot(),
            history,
            generated_at="2026-06-18T08:00:00+00:00",
            min_sample=5,
        )

        row = report["signals"][0]
        assert report["schema_version"] == 1
        assert report["summary"]["raw_strong_signal_count"] == 1
        assert report["summary"]["decided_raw_strong_signal_count"] == 1
        assert report["summary"]["sample_too_small"] is True
        assert row["outcome"] == "hit"
        assert row["grade"] == "B"
        assert row["raw_grade"] == "S"
        assert row["ah_validation_shadow"]["candidate_validated"] is True
        assert row["ah_validation_shadow"]["activation"] == "shadow_only"
        assert row["movement_shadow"]["supports_signal"] is True
        assert row["movement_shadow"]["activation"] == "shadow_only"
        assert report["buckets"]["by_ah_candidate_validated"]["true"]["hit"] == 1
        assert report["buckets"]["by_movement_supports_signal"]["true"]["hit"] == 1


def test_main_writes_shadow_backfill_report():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        _write_history(history)
        snapshot = root / "snapshot.json"
        snapshot.write_text(json.dumps(_finished_snapshot()), encoding="utf-8")
        out = root / "diagnostics" / "shadow.json"

        exit_code = main(
            [
                "--snapshot",
                str(snapshot),
                "--history",
                str(history),
                "--out",
                str(out),
                "--generated-at",
                "2026-06-18T08:00:00+00:00",
            ]
        )

        written = json.loads(out.read_text(encoding="utf-8"))
        assert exit_code == 0
        assert written["summary"]["raw_strong_signal_count"] == 1
        assert written["signals"][0]["movement_shadow"]["supports_signal"] is True
