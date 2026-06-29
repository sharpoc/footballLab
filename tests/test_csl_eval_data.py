from __future__ import annotations

import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import ClubResult
from worldcup.csl_eval_data import (
    build_rows,
    closing_match_entry,
    main as csl_eval_main,
    write_csv,
)


def _snapshot(snapshot_at: str, odds_home: float) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "competition": {"id": "csl_2026"},
        "matches": [
            {
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "home_canonical": "yunnan_yukun",
                "away_canonical": "henan",
                "elo": {"home": 1556, "away": 1540},
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.4, "away": 3.0}},
                    "ou_2_5": {
                        "line": 2.5,
                        "odds": {"over": 1.9, "under": 1.95},
                    },
                    "ah_main": {
                        "line_home": -0.25,
                        "odds": {"home": 1.88, "away": 2.02},
                    },
                },
            }
        ],
    }


def _result(
    date: str = "2026-07-03",
    home_canonical: str = "yunnan_yukun",
    away_canonical: str = "henan",
) -> ClubResult:
    return ClubResult(
        competition_id="csl_2026",
        season="2026",
        date=date,
        home_team="Yunnan Yukun",
        away_team="Henan FC",
        home_canonical=home_canonical,
        away_canonical=away_canonical,
        home_score=2,
        away_score=1,
        neutral=False,
    )


def test_closing_picks_last_csl_snapshot_before_kickoff():
    snapshots = [
        _snapshot("2026-07-03T08:00:00+00:00", 2.2),
        _snapshot("2026-07-03T11:30:00+00:00", 2.4),
        _snapshot("2026-07-03T12:30:00+00:00", 2.8),
    ]

    entry = closing_match_entry(snapshots, "2026-07-03", "yunnan_yukun", "henan")

    assert entry is not None
    assert entry["market"]["1x2"]["odds"]["home"] == 2.4


def test_build_rows_joins_csl_results_and_roundtrips_through_backtest_loader():
    from worldcup.backtest import load_matches

    snapshots = [_snapshot("2026-07-03T11:30:00+00:00", 2.4)]
    rows, skipped = build_rows(
        snapshots,
        [
            _result(),
            _result(
                date="2026-07-04",
                home_canonical="shanghai_port",
                away_canonical="shandong_taishan",
            ),
        ],
    )

    assert skipped == 1
    assert rows[0]["match_id"] == "csl_2026:2026-07-03:yunnan_yukun:henan"
    assert rows[0]["kickoff_at_utc"] == "2026-07-03T12:00:00+00:00"
    assert rows[0]["neutral"] == 0
    assert rows[0]["odds_home"] == 2.4
    assert rows[0]["odds_draw"] == 3.4
    assert rows[0]["odds_away"] == 3.0
    assert rows[0]["ou_line"] == 2.5
    assert rows[0]["ah_line"] == -0.25

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "csl_eval.csv"
        write_csv(rows, out)
        loaded = load_matches(out)

    assert len(loaded) == 1
    assert loaded[0].neutral is False
    assert loaded[0].home_elo_before == 1556
    assert loaded[0].away_elo_before == 1540
    assert loaded[0].odds_1x2 == {"home": 2.4, "draw": 3.4, "away": 3.0}
    assert loaded[0].odds_ou == {"over": 1.9, "under": 1.95}
    assert loaded[0].ah_line == -0.25
    assert loaded[0].odds_ah == {"home": 1.88, "away": 2.02}


def test_cli_writes_csl_eval_csv_from_local_files_only():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        (history / "snapshot_20260703T113000Z-live.json").write_text(
            json.dumps(_snapshot("2026-07-03T11:30:00+00:00", 2.4), ensure_ascii=False),
            encoding="utf-8",
        )
        results = root / "club_results_csl_2026.csv"
        with results.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "competition_id",
                    "season",
                    "date",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "neutral",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-07-03",
                    "home_team": "Yunnan Yukun",
                    "away_team": "Henan FC",
                    "home_score": "2",
                    "away_score": "1",
                    "neutral": "0",
                }
            )
        out = root / "backtest" / "csl_2026_eval.csv"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = csl_eval_main(
                [
                    "--history",
                    str(history),
                    "--results",
                    str(results),
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        summary = json.loads(stdout.getvalue())
        assert summary == {
            "competition_id": "csl_2026",
            "snapshots": 1,
            "results": 1,
            "joined": 1,
            "skipped_no_closing": 0,
            "out": str(out),
        }
        rows = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
        assert rows[0]["match_id"] == "csl_2026:2026-07-03:yunnan_yukun:henan"
        assert rows[0]["odds_home"] == "2.4"
