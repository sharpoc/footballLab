from worldcup.collectors.csl_result_sources import (
    parse_cfl_official_fixture_rows,
    parse_cfl_official_result_rows,
    parse_sevenm_fixture_rows,
    parse_sevenm_fixture_result_rows,
)


def test_parse_cfl_official_result_rows_keeps_only_played_matches():
    payload = {
        "data": {
            "dataList": [
                {
                    "id": "official-1",
                    "match_status": "Played",
                    "local_date": "2026-07-05",
                    "local_time": "19:35:00",
                    "week": 17,
                    "home_contestant_name": "辽宁铁人楠波湾",
                    "away_contestant_name": "浙江队",
                    "ft_home_score": 1,
                    "ft_away_score": 2,
                },
                {
                    "id": "official-2",
                    "match_status": "Fixture",
                    "local_date": "2026-07-10",
                    "home_contestant_name": "山东泰山",
                    "away_contestant_name": "云南玉昆",
                },
            ]
        }
    }

    rows = parse_cfl_official_result_rows(
        payload,
        season="2026",
        source_url="https://example.invalid/cfl",
    )

    assert rows == [
        {
            "season": "2026",
            "round": "17",
            "date": "2026-07-05",
            "kickoff_time_local": "19:35:00",
            "home_team": "辽宁铁人楠波湾",
            "away_team": "浙江队",
            "home_score": "1",
            "away_score": "2",
            "neutral": "0",
            "status": "finished",
            "source_match_id": "official-1",
            "source_url": "https://example.invalid/cfl",
        }
    ]


def test_parse_sevenm_fixture_result_rows_parses_finished_arrays_only():
    fixture_js = """
    var Tmp_bh_Arr = [ 7001, 7002 ];
    var Run_Arr = [ 17, 18 ];
    var Time_Arr = [ "2026,07,05,19,35,00", "2026,07,10,19,35,00" ];
    var Scores_Arr = [ "1-2(0-1)", "VS" ];
    var TeamA_Arr = [ "辽宁铁人楠波湾", "山东泰山" ];
    var TeamB_Arr = [ "浙江队", "云南玉昆" ];
    var Stat_Arr = [ 4, 17 ];
    """

    rows = parse_sevenm_fixture_result_rows(
        fixture_js,
        season="2026",
        source_url="https://example.invalid/sevenm",
    )

    assert rows == [
        {
            "season": "2026",
            "round": "17",
            "date": "2026-07-05",
            "kickoff_time_local": "19:35:00",
            "home_team": "辽宁铁人楠波湾",
            "away_team": "浙江队",
            "home_score": "1",
            "away_score": "2",
            "neutral": "0",
            "status": "finished",
            "source_match_id": "7001",
            "source_url": "https://example.invalid/sevenm",
        }
    ]


def test_fixture_row_parsers_keep_scheduled_and_postponed_statuses():
    official = {
        "data": {
            "dataList": [
                {
                    "id": "official-postponed",
                    "match_status": "Postponed",
                    "local_date": "2026-07-11",
                    "local_time": "19:35:00",
                    "week": 18,
                    "home_contestant_name": "上海申花",
                    "away_contestant_name": "北京国安",
                    "ft_home_score": 0,
                    "ft_away_score": 0,
                },
                {
                    "id": "official-rescheduled",
                    "match_status": "Fixture",
                    "local_date": "2026-07-14",
                    "local_time": "19:35:00",
                    "week": 18,
                    "home_contestant_name": "浙江队",
                    "away_contestant_name": "青岛海牛",
                    "ft_home_score": 0,
                    "ft_away_score": 0,
                },
            ]
        }
    }
    sevenm = """
    var Tmp_bh_Arr = [ 7001, 7002 ];
    var Run_Arr = [ 18, 18 ];
    var Time_Arr = [ "2026,07,11,19,35,00", "2026,07,14,19,35,00" ];
    var Scores_Arr = [ "VS", "VS" ];
    var TeamA_Arr = [ "上海申花", "浙江队" ];
    var TeamB_Arr = [ "北京国安", "青岛海牛" ];
    var Stat_Arr = [ 13, 17 ];
    var Memo_Arr = [ "因天气恶劣而延期", "" ];
    """

    official_rows = parse_cfl_official_fixture_rows(
        official,
        season="2026",
        source_url="https://example.invalid/cfl",
    )
    sevenm_rows = parse_sevenm_fixture_rows(
        sevenm,
        season="2026",
        source_url="https://example.invalid/sevenm",
    )

    assert [row["status"] for row in official_rows] == ["POSTPONED", "SCHEDULED"]
    assert [row["status"] for row in sevenm_rows] == ["POSTPONED", "SCHEDULED"]
    assert official_rows[0]["kickoff_at_utc"] == "2026-07-11T11:35:00+00:00"
    assert official_rows[0]["home_canonical"] == "shanghai_shenhua"
    assert sevenm_rows[1]["away_canonical"] == "qingdao_hainiu"
