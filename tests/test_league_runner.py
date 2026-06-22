import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import ClubRating, ClubRatingPool
from worldcup.collectors.models import Fixture
from worldcup.league_runner import _ratings_for_fixture, build_league_snapshot_from_cache, main
from worldcup.local_runner import cap_signals_for_pending_club_rating
from worldcup.models import Grade, MarketType, Signal


def _write_csl_odds_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True)
    bookmakers = []
    for idx, price_home in enumerate((2.35, 2.4, 2.45), start=1):
        bookmakers.append(
            {
                "key": f"bk{idx}",
                "last_update": "2026-06-22T08:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-22T08:01:00Z",
                        "outcomes": [
                            {"name": "Shanghai Port", "price": price_home},
                            {"name": "Shandong Taishan", "price": 3.2},
                            {"name": "Draw", "price": 3.4},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-06-22T08:02:00Z",
                        "outcomes": [
                            {"name": "Over", "price": 2.35, "point": 2.5},
                            {"name": "Under", "price": 1.62, "point": 2.5},
                        ],
                    },
                    {
                        "key": "spreads",
                        "last_update": "2026-06-22T08:03:00Z",
                        "outcomes": [
                            {"name": "Shanghai Port", "price": 2.2, "point": -0.5},
                            {"name": "Shandong Taishan", "price": 1.72, "point": 0.5},
                        ],
                    },
                ],
            }
        )
    (cache_dir / "theoddsapi_csl_2026_odds.json").write_text(
        json.dumps(
            [
                {
                    "id": "csl-event-1",
                    "sport_key": "soccer_china_superleague",
                    "commence_time": "2026-06-24T11:35:00Z",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "bookmakers": bookmakers,
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_club_results_cache(cache_dir: Path, rows: list[dict[str, str]]) -> None:
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
    with (cache_dir / "club_results_csl_2026.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_cap_signals_for_pending_club_rating_caps_strong_grades():
    signals = [
        Signal(MarketType.X12, "home", Grade.S, 0.12, 0.08, "OK", ["value_edge"]),
        Signal(
            MarketType.OU,
            "over",
            Grade.A,
            0.08,
            0.05,
            "OK",
            ["club_rating_pending"],
        ),
        Signal(MarketType.AH, "away_0.5", Grade.C, None, None, "NO_VALUE"),
    ]

    capped = cap_signals_for_pending_club_rating(signals)

    assert capped[0].grade == Grade.B
    assert capped[0].reasons == ["value_edge", "club_rating_pending"]
    assert capped[1].grade == Grade.B
    assert capped[1].reasons == ["club_rating_pending"]
    assert capped[2] is signals[2]
    assert signals[0].grade == Grade.S


def test_build_league_snapshot_from_cache_builds_local_csl_snapshot():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
        )

        assert snapshot["snapshot_at"] == "2026-06-22T09:00:00+00:00"
        assert snapshot["competition"]["id"] == "csl_2026"
        assert snapshot["counts"]["fixtures"] == 1
        assert snapshot["counts"]["odds_events"] == 1
        assert snapshot["counts"]["matches"] == 1
        assert snapshot["data_quality"]["fixture_source"] == "odds_event_only"
        assert "odds_event_only" in snapshot["data_quality"]["warnings"]
        assert "club_rating_pending" in snapshot["data_quality"]["warnings"]
        assert snapshot["data_quality"]["club_alias_unmatched"] == []

        match = snapshot["matches"][0]
        assert match["source_event_id"] == "csl-event-1"
        assert match["competition"]["id"] == "csl_2026"
        assert match["home_team"] == "Shanghai Port"
        assert match["away_team"] == "Shandong Taishan"
        assert match["elo"] == {"home": 1500, "away": 1500}
        assert match["signals"]
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])
        assert any("club_rating_pending" in signal["reasons"] for signal in match["signals"])


def test_build_league_snapshot_reports_missing_club_rating_quality():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "missing"
        assert club_quality["source"].endswith("club_results_csl_2026.csv")
        assert club_quality["competition_id"] == "csl_2026"
        assert club_quality["matches_replayed"] == 0
        assert club_quality["teams_rated"] == 0
        assert club_quality["sample_too_small"] is True
        assert club_quality["missing_teams"] == []
        assert "club_rating_missing" in snapshot["data_quality"]["warnings"]
        assert "club_rating_sample_too_small" not in snapshot["data_quality"]["warnings"]


def test_build_league_snapshot_reports_invalid_club_rating_without_sample_warning():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "not-a-date",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                }
            ],
        )

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "invalid"
        assert club_quality["errors"] == ["no_valid_rows"]
        assert "club_rating_invalid" in snapshot["data_quality"]["warnings"]
        assert "club_rating_sample_too_small" not in snapshot["data_quality"]["warnings"]


def test_build_league_snapshot_uses_sample_club_ratings_but_keeps_signal_cap():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-01",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                },
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-08",
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                    "home_score": "1",
                    "away_score": "0",
                    "neutral": "0",
                },
            ],
        )

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
            club_rating_min_matches=2,
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "sample_replay"
        assert club_quality["matches_replayed"] == 2
        assert club_quality["teams_rated"] == 3
        assert club_quality["missing_teams"] == []
        assert "club_rating_pending" in snapshot["data_quality"]["warnings"]

        match = snapshot["matches"][0]
        assert match["elo"]["home"] > 1500
        assert match["elo"]["away"] < 1500
        assert match["signals"]
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])
        assert any("club_rating_pending" in signal["reasons"] for signal in match["signals"])


def test_build_league_snapshot_falls_back_when_fixture_team_missing_rating():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-01",
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                }
            ],
        )

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
            club_rating_min_matches=1,
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "sample_replay"
        assert club_quality["missing_teams"] == ["shandong_taishan"]
        assert "club_rating_missing" in snapshot["data_quality"]["warnings"]

        match = snapshot["matches"][0]
        assert match["elo"] == {"home": 1500, "away": 1500}
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])


def test_ratings_for_fixture_falls_back_when_canonical_is_missing_without_recording_none():
    fixture = Fixture(
        source_match_no=None,
        kickoff_at_utc=datetime(2026, 6, 24, 11, 35, tzinfo=timezone.utc),
        kickoff_time_raw="2026-06-24T11:35:00Z",
        home_team_name="Unknown Club",
        away_team_name="Shandong Taishan",
        home_canonical=None,
        away_canonical="shandong_taishan",
    )
    rating_pool = ClubRatingPool(
        competition_id="csl_2026",
        ratings={"shandong_taishan": ClubRating("shandong_taishan", 1520, 1)},
        matches_replayed=1,
    )

    home_rating, away_rating, missing = _ratings_for_fixture(fixture, rating_pool)

    assert home_rating.rating == 1500
    assert away_rating.rating == 1500
    assert missing == []


def test_build_league_snapshot_reports_invalid_league_odds_quality():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        odds_path = cache_dir / "theoddsapi_csl_2026_odds.json"
        events = json.loads(odds_path.read_text(encoding="utf-8"))
        events[0]["bookmakers"][0]["markets"][1]["outcomes"][0]["price"] = 1.0
        odds_path.write_text(json.dumps(events), encoding="utf-8")

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
        )

        data_quality = snapshot["data_quality"]
        assert data_quality["invalid_odds_count"] == 1
        example = data_quality["invalid_odds_examples"][0]
        assert example["reason"] == "odds_decimal_lte_one"
        assert example["raw_payload_path"] == str(odds_path)
        assert example["match_id"] == "csl-event-1"
        assert example["bookmaker"] == "bk1"
        assert example["market"] == "totals"
        assert example["selection"] == "over"
        assert example["odds"] == 1.0

        match = snapshot["matches"][0]
        assert match["signals"]
        assert match["market"]["ou_2_5"]["n_books_by_selection"]["over"] == 2
        assert match["market"]["ou_2_5"]["n_books_by_selection"]["under"] == 3


def test_main_accepts_competition_alias_without_network():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        out = Path(tmp) / "league_snapshot.json"
        _write_csl_odds_cache(cache_dir)

        result = main(
            [
                "--competition",
                "csl_2026",
                "--cache-dir",
                str(cache_dir),
                "--out",
                str(out),
            ]
        )

        assert result == 0
        assert out.exists()


def test_main_passes_club_rating_min_matches_to_snapshot_builder():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        out = Path(tmp) / "league_snapshot.json"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-01",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                }
            ],
        )

        result = main(
            [
                "--competition",
                "csl_2026",
                "--cache-dir",
                str(cache_dir),
                "--out",
                str(out),
                "--club-rating-min-matches",
                "1",
            ]
        )

        snapshot = json.loads(out.read_text(encoding="utf-8"))
        assert result == 0
        assert snapshot["data_quality"]["club_rating"]["mode"] == "sample_replay"
