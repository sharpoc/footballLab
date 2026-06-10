import tempfile
from pathlib import Path

from worldcup.backtest_data import convert, known_canonicals, write_csv
from worldcup.elo_replay import ReplayMatch

ALIASES_TSV = "AA\tAlpha\nBB\tBeta\nCC\tGamma\n"


def _sample_matches() -> list[ReplayMatch]:
    return [
        ReplayMatch("2009-05-01", "Alpha", "Beta", 1, 0, "Friendly", False),
        ReplayMatch("2024-01-01", "Alpha", "Beta", 2, 1, "Friendly", False),
        ReplayMatch("2024-02-01", "Alpha", "Unknownia", 5, 0, "Friendly", True),
        ReplayMatch("2024-03-01", "Beta", "Gamma", 0, 0, "FIFA World Cup qualification", True),
    ]


def test_convert_filters_by_date_and_known_teams():
    known = known_canonicals(ALIASES_TSV)
    rows = convert(_sample_matches(), known, since="2010-01-01")
    assert [r["match_id"] for r in rows] == [
        "2024-01-01_alpha_beta",
        "2024-03-01_beta_gamma",
    ]
    first = rows[0]
    assert first["kickoff_at_utc"] == "2024-01-01T12:00:00Z"
    assert first["neutral"] == 0
    assert rows[1]["neutral"] == 1
    assert first["home_elo_before"] != 1500.0


def test_write_csv_roundtrips_through_backtest_loader():
    from worldcup.backtest import load_matches

    known = known_canonicals(ALIASES_TSV)
    rows = convert(_sample_matches(), known, since="2010-01-01")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "history.csv"
        write_csv(rows, out)
        loaded = load_matches(out)
    assert len(loaded) == 2
    assert loaded[0].home_team == "Alpha"
    assert loaded[0].odds_1x2 is None and loaded[0].odds_ou is None
    assert loaded[0].neutral is False
    assert loaded[1].neutral is True
    assert loaded[0].home_elo_before > loaded[0].away_elo_before
