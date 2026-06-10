import tempfile
from pathlib import Path

from worldcup.eval_data import build_rows, closing_match_entry


def _snapshot(snapshot_at: str, odds_home: float) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "elo": {"home": 1875, "away": 1700},
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {
                        "line_home": -0.5,
                        "odds": {"home": 1.96, "away": 1.88},
                    },
                },
            }
        ],
    }


RESULT_ROW = {
    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
    "home_team": "Mexico",
    "away_team": "South Africa",
    "home_canonical": "mexico",
    "away_canonical": "south_africa",
    "home_score": "2",
    "away_score": "1",
}


def test_closing_picks_last_snapshot_before_kickoff():
    snapshots = [
        _snapshot("2026-06-11T06:00:00+00:00", 1.7),
        _snapshot("2026-06-11T18:00:00+00:00", 1.9),
        _snapshot("2026-06-11T20:00:00+00:00", 2.4),
    ]
    entry = closing_match_entry(
        snapshots, "2026-06-11T19:00:00+00:00", "mexico", "south_africa"
    )
    assert entry["market"]["1x2"]["odds"]["home"] == 1.9


def test_build_rows_joins_and_roundtrips_through_backtest_loader():
    from worldcup.backtest import load_matches

    snapshots = [_snapshot("2026-06-11T18:00:00+00:00", 1.8)]
    rows, skipped = build_rows(snapshots, [RESULT_ROW])
    assert skipped == 0
    assert rows[0]["odds_home"] == 1.8
    assert rows[0]["neutral"] == 1

    from worldcup.eval_data import write_csv

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "eval.csv"
        write_csv(rows, out)
        loaded = load_matches(out)
    assert len(loaded) == 1
    assert loaded[0].odds_1x2 == {"home": 1.8, "draw": 3.6, "away": 4.8}
    assert loaded[0].odds_ou == {"over": 1.9, "under": 2.0}
    assert loaded[0].ah_line == -0.5
    assert loaded[0].odds_ah == {"home": 1.96, "away": 1.88}
    assert loaded[0].home_elo_before == 1875.0
    assert loaded[0].home_score == 2


def test_build_rows_skips_result_without_pre_kickoff_snapshot():
    snapshots = [_snapshot("2026-06-11T20:00:00+00:00", 2.0)]
    rows, skipped = build_rows(snapshots, [RESULT_ROW])
    assert rows == [] and skipped == 1


def test_build_rows_blank_odds_when_market_incomplete():
    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    snap["matches"][0]["market"]["ou_2_5"] = {"odds": {"over": 1.9}}
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["odds_over"] == "" and rows[0]["odds_under"] == ""
    assert rows[0]["odds_home"] == 1.8


def test_build_rows_includes_main_ah_odds():
    snapshots = [_snapshot("2026-06-11T18:00:00+00:00", 1.8)]
    rows, _ = build_rows(snapshots, [RESULT_ROW])
    assert rows[0]["ah_line"] == -0.5
    assert rows[0]["odds_ah_home"] == 1.96
    assert rows[0]["odds_ah_away"] == 1.88


def test_build_rows_blank_ah_when_block_missing_or_incomplete():
    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    del snap["matches"][0]["market"]["ah_main"]
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["ah_line"] == ""
    assert rows[0]["odds_ah_home"] == "" and rows[0]["odds_ah_away"] == ""

    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    snap["matches"][0]["market"]["ah_main"]["odds"] = {"home": 1.96}
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["ah_line"] == ""
    assert rows[0]["odds_ah_home"] == "" and rows[0]["odds_ah_away"] == ""
