import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import (
    load_club_rating_pool,
    load_club_results_csv,
    replay_club_ratings,
)


def _write_results(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "competition_id",
        "season",
        "date",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "neutral",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(
    home: str,
    away: str,
    home_score: str,
    away_score: str,
    *,
    date: str = "2026-03-01",
    competition_id: str = "csl_2026",
    neutral: str = "0",
) -> dict[str, str]:
    return {
        "competition_id": competition_id,
        "season": "2026",
        "date": date,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "neutral": neutral,
    }


def test_load_club_results_csv_filters_competition_and_canonicalizes_clubs():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0"),
                _row(
                    "Arsenal",
                    "Chelsea",
                    "1",
                    "1",
                    competition_id="epl_2026_27",
                ),
                _row("Shanghai Shenhua", "Beijing Guoan", "x", "1"),
            ],
        )

        results = load_club_results_csv(path, "csl_2026")

        assert len(results) == 1
        result = results[0]
        assert result.competition_id == "csl_2026"
        assert result.home_team == "Shanghai Port"
        assert result.away_team == "Shandong Taishan"
        assert result.home_canonical == "shanghai_port"
        assert result.away_canonical == "shandong_taishan"
        assert result.home_score == 2
        assert result.away_score == 0
        assert result.neutral is False


def test_replay_club_ratings_moves_winner_up_and_returns_pipeline_elo():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shandong Taishan", "Beijing Guoan", "1", "1", date="2026-03-08"),
            ],
        )

        pool = replay_club_ratings(load_club_results_csv(path, "csl_2026"), "csl_2026")

        shanghai = pool.rating_for("shanghai_port")
        shandong = pool.rating_for("shandong_taishan")
        assert shanghai is not None
        assert shandong is not None
        assert shanghai.rating > 1500
        assert shandong.rating < 1500
        assert pool.matches_replayed == 2
        assert pool.to_elo_rating("shanghai_port").code == "shanghai_port"
        assert pool.to_elo_rating("shanghai_port").rating == shanghai.rating
        assert pool.to_elo_rating("unknown_club") is None


def test_load_club_rating_pool_reports_missing_file_without_exception():
    with TemporaryDirectory() as tmp:
        result = load_club_rating_pool(Path(tmp), "csl_2026")

        assert result.pool is None
        assert result.quality.mode == "missing"
        assert result.quality.source.endswith("club_results_csl_2026.csv")
        assert result.quality.competition_id == "csl_2026"
        assert result.quality.matches_replayed == 0
        assert result.quality.teams_rated == 0
        assert result.quality.sample_too_small is True
        assert result.quality.to_dict()["missing_teams"] == []


def test_load_club_rating_pool_reports_sample_too_small_and_invalid_rows():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        path = cache_dir / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shanghai Shenhua", "Beijing Guoan", "bad", "1", date="2026-03-02"),
            ],
        )

        result = load_club_rating_pool(cache_dir, "csl_2026", min_matches=2)

        assert result.pool is None
        assert result.quality.mode == "sample_too_small"
        assert result.quality.matches_replayed == 1
        assert result.quality.teams_rated == 2
        assert result.quality.skipped_rows == 1
        assert result.quality.sample_too_small is True


def test_load_club_rating_pool_replays_when_sample_threshold_is_met():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        path = cache_dir / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shanghai Port", "Beijing Guoan", "1", "0", date="2026-03-08"),
                _row("Shanghai Shenhua", "Beijing Guoan", "0", "0", date="2026-03-15"),
            ],
        )

        result = load_club_rating_pool(cache_dir, "csl_2026", min_matches=3)

        assert result.pool is not None
        assert result.quality.mode == "sample_replay"
        assert result.quality.matches_replayed == 3
        assert result.quality.teams_rated == 4
        assert result.quality.skipped_rows == 0
        assert result.quality.sample_too_small is False
        assert result.quality.to_dict()["source"].endswith("club_results_csl_2026.csv")
