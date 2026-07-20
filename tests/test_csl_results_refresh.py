import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_results_refresh import run_csl_results_refresh


def _write_existing(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=(
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2025",
                "date": "2025-11-01",
                "home_team": "山东泰山",
                "away_team": "浙江队",
                "home_score": "2",
                "away_score": "0",
                "neutral": "0",
            }
        )
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-07-05",
                "home_team": "辽宁铁人楠波湾",
                "away_team": "浙江队",
                "home_score": "1",
                "away_score": "2",
                "neutral": "0",
            }
        )


def _official_payload():
    return {
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
                    "match_status": "Played",
                    "local_date": "2026-07-06",
                    "local_time": "19:35:00",
                    "week": 17,
                    "home_contestant_name": "山东泰山",
                    "away_contestant_name": "云南玉昆",
                    "ft_home_score": 3,
                    "ft_away_score": 1,
                },
                {
                    "id": "official-3",
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
                    "id": "official-4",
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


def _sevenm_fixture():
    return """
    var Tmp_bh_Arr = [ 7001, 7002, 7003, 7004 ];
    var Run_Arr = [ 17, 17, 18, 18 ];
    var Time_Arr = [ "2026,07,05,19,35,00", "2026,07,06,19,35,00", "2026,07,11,19,35,00", "2026,07,14,19,35,00" ];
    var Scores_Arr = [ "1-2(0-1)", "3-1(2-0)", "VS", "VS" ];
    var TeamA_Arr = [ "辽宁铁人楠波湾", "山东泰山", "上海申花", "浙江队" ];
    var TeamB_Arr = [ "浙江队", "云南玉昆", "北京国安", "青岛海牛" ];
    var Stat_Arr = [ 4, 4, 13, 17 ];
    var Memo_Arr = [ "", "", "因天气恶劣而延期", "" ];
    """


def test_verified_dual_source_refresh_preserves_history_and_writes_atomically():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        replay_path = root / "club_results_csl_2026.csv"
        _write_existing(replay_path)

        result = run_csl_results_refresh(
            live=True,
            write=True,
            replay_path=replay_path,
            raw_dir=root / "raw",
            official_fetch=lambda _url: _official_payload(),
            sevenm_fetch=lambda _url: _sevenm_fixture(),
        )

        with replay_path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        fixture_status = json.loads(
            (root / "csl_fixture_status_csl_2026.json").read_text(encoding="utf-8")
        )

    assert result["status"] == "updated"
    assert result["verified_current_season_matches"] == 2
    assert result["total_matches"] == 3
    assert result["latest_result_date"] == "2026-07-06"
    assert result["fixture_status"]["status"] == "updated"
    assert result["fixture_status"]["postponed_matches"] == 1
    assert [row["status"] for row in fixture_status["fixtures"]] == [
        "POSTPONED",
        "SCHEDULED",
    ]
    assert [row["season"] for row in rows] == ["2025", "2026", "2026"]


def test_dual_source_score_mismatch_blocks_replay_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        replay_path = root / "club_results_csl_2026.csv"
        _write_existing(replay_path)
        sevenm = _sevenm_fixture().replace('"3-1(2-0)"', '"2-1(1-0)"')

        result = run_csl_results_refresh(
            live=True,
            write=True,
            replay_path=replay_path,
            raw_dir=root / "raw",
            official_fetch=lambda _url: _official_payload(),
            sevenm_fetch=lambda _url: sevenm,
        )

        with replay_path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

    assert result["status"] == "blocked"
    assert result["reason"] == "dual_source_verification_failed"
    assert len(rows) == 2


def test_fixture_status_mismatch_does_not_overwrite_status_cache():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        replay_path = root / "club_results_csl_2026.csv"
        _write_existing(replay_path)
        sevenm = _sevenm_fixture().replace("因天气恶劣而延期", "")

        result = run_csl_results_refresh(
            live=True,
            write=True,
            replay_path=replay_path,
            raw_dir=root / "raw",
            official_fetch=lambda _url: _official_payload(),
            sevenm_fetch=lambda _url: sevenm,
        )

    assert result["status"] == "updated"
    assert result["fixture_status"]["status"] == "blocked"
    assert result["fixture_status"]["reason"] == (
        "fixture_status_dual_source_verification_failed"
    )
    assert not (root / "csl_fixture_status_csl_2026.json").exists()


def test_results_refresh_dry_run_never_calls_network_or_writes():
    with TemporaryDirectory() as tmp:
        replay_path = Path(tmp) / "club_results_csl_2026.csv"
        _write_existing(replay_path)

        def forbidden(_url):
            raise AssertionError("dry-run must not call network")

        result = run_csl_results_refresh(
            live=False,
            write=False,
            replay_path=replay_path,
            official_fetch=forbidden,
            sevenm_fetch=forbidden,
        )

    assert result["status"] == "dry_run"
    assert result["existing_matches"] == 2
