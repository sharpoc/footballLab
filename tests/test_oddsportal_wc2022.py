from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.oddsportal_wc2022 import (
    join_with_history,
    merge_markets,
    normalize_1x2,
    normalize_ah,
    normalize_ou,
    write_backtest_csv,
)


RAW_BASE = {
    "scraped_date": "2026-06-12 08:19:30 UTC",
    "match_date": "2022-12-13 19:00:00 UTC",
    "match_link": "https://www.oddsportal.com/football/h2h/argentina-f9OppQjp/croatia-K8aznggo/#QXChy9gE",
    "home_team": "Argentina",
    "away_team": "Croatia",
    "league_name": "World Cup 2022",
    "home_score": "3",
    "away_score": "0",
}

RAW_1X2 = {
    **RAW_BASE,
    "1x2_market": [
        {
            "1": "1.98",
            "X": "3.14",
            "2": "4.82",
            "bookmaker_name": "1xBet",
            "period": "FullTime",
            "odds_history_data": [
                {"opening_odds": {"odds": 1.88}},
                {"opening_odds": {"odds": 3.54}},
                {"opening_odds": {"odds": 4.96}},
            ],
        },
        {
            "1": "2.08",
            "X": "3.10",
            "2": "4.76",
            "bookmaker_name": "BetInAsia",
            "period": "FullTime",
            "odds_history_data": [
                {"opening_odds": {"odds": 1.90}},
                {"opening_odds": {"odds": 3.50}},
                {"opening_odds": {"odds": 4.90}},
            ],
        },
    ],
}

RAW_OU = {
    **RAW_BASE,
    "over_under_2_5_market": [
        {
            "odds_over": "2.38",
            "odds_under": "1.61",
            "bookmaker_name": "1xBet",
            "period": "FullTime",
            "odds_history_data": [
                {"opening_odds": {"odds": 2.50}},
                {"opening_odds": {"odds": 1.63}},
            ],
        }
    ],
}

RAW_AH = {
    **RAW_BASE,
    "asian_handicap_-0_5_market": [
        {
            "team1_handicap": "2.08",
            "team2_handicap": "1.92",
            "bookmaker_name": "BetInAsia",
            "period": "FullTime",
            "odds_history_data": [
                {"opening_odds": {"odds": 1.88}},
                {"opening_odds": {"odds": 2.09}},
            ],
        },
        {
            "team1_handicap": "2.09(172)",
            "team2_handicap": "1.90(31625)",
            "bookmaker_name": "Betfair Exchange",
            "period": "FullTime",
        },
    ],
    "asian_handicap_0_market": [
        {
            "submarket_name": "Asian Handicap 0",
            "period": "FullTime",
            "market_type": "Asian Handicap",
            "extraction_mode": "passive",
            "team1_handicap": "1.42",
            "team2_handicap": "3.27",
        }
    ],
}


def test_normalize_1x2_extracts_real_oddsharvester_open_and_close():
    row = normalize_1x2(RAW_1X2)

    assert row.date == "2022-12-13"
    assert row.home_canonical == "argentina"
    assert row.away_canonical == "croatia"
    assert row.home_score == 3
    assert row.away_score == 0
    assert row.close_1x2 == {"home": 2.03, "draw": 3.12, "away": 4.79}
    assert row.open_1x2 == {"home": 1.89, "draw": 3.52, "away": 4.93}


def test_normalize_ou_extracts_2_5_open_and_close():
    row = normalize_ou(RAW_OU)

    assert row.close_ou == {"over": 2.38, "under": 1.61}
    assert row.open_ou == {"over": 2.5, "under": 1.63}


def test_normalize_ah_picks_main_line_independently_for_open_and_close():
    row = normalize_ah(RAW_AH)

    assert row.close_ah_line == -0.5
    assert row.close_ah == {"home": 2.085, "away": 1.91}
    assert row.open_ah_line == -0.5
    assert row.open_ah == {"home": 1.88, "away": 2.09}


def test_merge_markets_joins_three_markets_by_match_key():
    merged = merge_markets([normalize_1x2(RAW_1X2)], [normalize_ah(RAW_AH)], [normalize_ou(RAW_OU)])

    assert len(merged) == 1
    assert merged[0].close_1x2
    assert merged[0].close_ah_line == -0.5
    assert merged[0].close_ou == {"over": 2.38, "under": 1.61}


def test_merge_markets_keeps_match_with_missing_market():
    merged = merge_markets([normalize_1x2(RAW_1X2)], [], [])

    assert len(merged) == 1
    assert merged[0].close_ah is None


def _intl_rows() -> list[dict]:
    return [
        {
            "match_id": "2022-12-13_argentina_croatia",
            "kickoff_at_utc": "2022-12-13T12:00:00Z",
            "home_team": "Argentina",
            "away_team": "Croatia",
            "home_score": "3",
            "away_score": "0",
            "home_elo_before": "2140.0",
            "away_elo_before": "1930.0",
            "neutral": "1",
        },
        {
            "match_id": "2022-12-14_france_morocco",
            "kickoff_at_utc": "2022-12-14T12:00:00Z",
            "home_team": "France",
            "away_team": "Morocco",
            "home_score": "2",
            "away_score": "0",
            "home_elo_before": "2050.0",
            "away_elo_before": "1840.0",
            "neutral": "1",
        },
    ]


def _normalized_argentina() -> "NormalizedMatch":
    return merge_markets([normalize_1x2(RAW_1X2)], [normalize_ah(RAW_AH)], [normalize_ou(RAW_OU)])[0]


def test_join_matches_by_date_and_canonical_names():
    joined, unmatched = join_with_history([_normalized_argentina()], _intl_rows())

    assert len(joined) == 1
    assert unmatched == []
    rec = joined[0]
    assert rec["match_id"] == "2022-12-13_argentina_croatia"
    assert rec["home_elo_before"] == "2140.0"
    assert rec["odds_home"] == "2.03"
    assert rec["odds_draw"] == "3.12"
    assert rec["ah_line"] == "-0.5"
    assert rec["open_ah_line"] == "-0.5"
    assert rec["open_odds_home"] == "1.89"


def test_join_tolerates_one_day_offset():
    moved = _normalized_argentina()
    moved.date = "2022-12-12"
    joined, unmatched = join_with_history([moved], _intl_rows())

    assert len(joined) == 1
    assert unmatched == []


def test_join_reports_unmatched():
    ghost = _normalized_argentina()
    ghost.away_canonical = "atlantis"
    joined, unmatched = join_with_history([ghost], _intl_rows())

    assert joined == []
    assert unmatched and unmatched[0]["away_canonical"] == "atlantis"


def test_write_backtest_csv_is_loadable_by_backtest_module():
    from worldcup.backtest import load_matches

    joined, _ = join_with_history([_normalized_argentina()], _intl_rows())
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "wc2022_history.csv"
        write_backtest_csv(joined, out)
        matches = load_matches(out)

    assert len(matches) == 1
    match = matches[0]
    assert match.odds_1x2 == {"home": 2.03, "draw": 3.12, "away": 4.79}
    assert match.ah_line == -0.5
    assert match.odds_ah == {"home": 2.085, "away": 1.91}
    assert match.odds_ou == {"over": 2.38, "under": 1.61}
