import json
import gzip
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.refresh_runner import refresh_cache_and_build_snapshot
from worldcup.theoddsapi_keys import SECONDARY_PROVIDER


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "3",
        "x-requests-remaining": "497",
        "x-requests-last": "3",
    }

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_refresh_cache_and_build_snapshot_with_injected_transports():
    def openfootball_transport(_url):
        return FakeResponse(
            json.dumps(
                {
                    "matches": [
                        {
                            "round": "Matchday 1",
                            "date": "2026-06-11",
                            "time": "13:00 UTC-6",
                            "team1": "Mexico",
                            "team2": "South Africa",
                            "group": "Group A",
                            "ground": "Mexico City",
                        }
                    ]
                }
            ).encode()
        )

    def theoddsapi_transport(_url):
        return FakeResponse(
            json.dumps(
                [
                    {
                        "id": "event-1",
                        "sport_key": "soccer_fifa_world_cup",
                        "commence_time": "2026-06-11T19:00:00Z",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "bookmakers": [
                            {
                                "key": "bk1",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "outcomes": [
                                            {"name": "Mexico", "price": 1.8},
                                            {"name": "South Africa", "price": 4.8},
                                            {"name": "Draw", "price": 3.6},
                                        ],
                                    },
                                    {
                                        "key": "totals",
                                        "outcomes": [
                                            {"name": "Over", "price": 1.9, "point": 2.5},
                                            {"name": "Under", "price": 2.0, "point": 2.5},
                                        ],
                                    },
                                    {
                                        "key": "spreads",
                                        "outcomes": [
                                            {"name": "Mexico", "price": 1.9, "point": -0.5},
                                            {"name": "South Africa", "price": 1.9, "point": 0.5},
                                        ],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            ).encode()
        )

    def elo_transport(url):
        if url.endswith("World.tsv"):
            return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
        if url.endswith("en.teams.tsv"):
            return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
        raise AssertionError(url)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=root / "cache",
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=root / "cache" / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=elo_transport,
            history_dir=root / "history",
            observed_at="2026-06-08T00:00:00+00:00",
            theoddsapi_provider=SECONDARY_PROVIDER,
        )

        assert result.snapshot["counts"]["matches"] == 1
        assert result.snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert result.snapshot_path.exists()
        assert result.run_metadata["run_id"] == "20260608T000000Z-live"
        assert result.snapshot["run"]["quota"][SECONDARY_PROVIDER]["remaining"] == 497
        assert result.snapshot["run"]["quota"]["theoddsapi"]["remaining"] == 497
        assert result.snapshot["run"]["stale_sources"] == []
        assert result.snapshot["matches"][0]["refresh_plan"]["next_update_at"] == "2026-06-09T00:00:00+00:00"
        assert result.snapshot["run"]["policy"]["match_plans"][0]["quota_remaining"] == 497
        assert result.cache_dir.joinpath("openfootball_2026.json").exists()
        assert result.cache_dir.joinpath("theoddsapi_wc_odds.json").exists()
        assert json.loads(result.quota_path.read_text())["providers"]["theoddsapi"]["remaining"] == 497
        archive = root / "history" / "snapshot_20260608T000000Z-live.json"
        assert result.archive_path == archive
        assert archive.exists()
        assert json.loads(archive.read_text())["run"]["run_id"] == "20260608T000000Z-live"
        odds_raw = root / "history" / "odds_raw_20260608T000000Z-live.json.gz"
        assert result.odds_raw_archive_path == odds_raw
        assert odds_raw.exists()

        archived_events = json.loads(gzip.open(odds_raw, "rb").read().decode("utf-8"))
        assert archived_events[0]["id"] == "event-1"
        assert archived_events[0]["bookmakers"][0]["key"] == "bk1"


def test_refresh_uses_stale_odds_cache_when_theoddsapi_times_out():
    openfootball_body = json.dumps(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    odds_body = json.dumps(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-11T19:00:00Z",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "bookmakers": [
                    {
                        "key": "bk1",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Mexico", "price": 1.8},
                                    {"name": "South Africa", "price": 4.8},
                                    {"name": "Draw", "price": 3.6},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = root / "cache"
        cache.mkdir()
        (cache / "openfootball_2026.json").write_text(openfootball_body)
        (cache / "theoddsapi_wc_odds.json").write_text(odds_body)
        (cache / "elo_world.tsv").write_text("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
        (cache / "elo_teams.tsv").write_text("MX\tMexico\nZA\tSouth Africa\n")

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        def fail_theoddsapi(_url):
            raise TimeoutError("handshake timed out")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            elo_transport=elo_transport,
            theoddsapi_transport=fail_theoddsapi,
            history_dir=root / "history",
        )

        assert result.snapshot["counts"]["matches"] == 1
        assert result.snapshot["run"]["stale_sources"] == ["theoddsapi"]
        assert result.snapshot["data_quality"]["stale_sources"] == ["theoddsapi"]
        assert result.snapshot["data_quality"]["source_errors"][0]["source"] == "theoddsapi"
        assert "handshake timed out" in result.snapshot["data_quality"]["source_errors"][0]["error"]
        home_signal = next(
            signal
            for signal in result.snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert "unconfirmed_backup" in home_signal["reasons"]
        assert result.odds_raw_archive_path is None
        assert list((root / "history").glob("odds_raw_*.json.gz")) == []


def _elo_cache_fixture(root: Path) -> Path:
    cache = root / "cache"
    cache.mkdir()
    openfootball_body = json.dumps(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    odds_body = json.dumps(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-11T19:00:00Z",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "bookmakers": [
                    {
                        "key": "bk1",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Mexico", "price": 1.8},
                                    {"name": "South Africa", "price": 4.8},
                                    {"name": "Draw", "price": 3.6},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    (cache / "openfootball_2026.json").write_text(openfootball_body)
    (cache / "theoddsapi_wc_odds.json").write_text(odds_body)
    (cache / "elo_world.tsv").write_text("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    (cache / "elo_teams.tsv").write_text("MX\tMexico\nZA\tSouth Africa\n")
    return cache


def test_refresh_elo_fetch_failure_records_error_without_stale():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("invalid Elo ratings TSV: parsed 0 rows")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        assert result.snapshot["data_quality"]["source_errors"][0]["source"] == "eloratings"
        assert "parsed 0 rows" in result.snapshot["data_quality"]["source_errors"][0]["error"]
        assert result.snapshot["data_quality"]["stale_sources"] == []
        assert result.snapshot["run"]["stale_sources"] == []
        home_signal = next(
            signal
            for signal in result.snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert "unconfirmed_backup" not in home_signal["reasons"]


def test_refresh_applies_finished_results_to_local_elo():
    from worldcup.elo_local import freeze_baseline

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        fixture_data = json.loads((cache / "openfootball_2026.json").read_text())
        fixture_data["matches"][0]["score1"] = 2
        fixture_data["matches"][0]["score2"] = 0
        openfootball_body = json.dumps(fixture_data)
        (cache / "openfootball_2026.json").write_text(openfootball_body)
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        freeze_baseline(cache, baseline_at="2026-06-01T00:00:00+00:00")

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("blocked")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        assert result.snapshot["matches"][0]["elo"]["home"] > 1875
        assert result.snapshot["matches"][0]["elo"]["away"] < 1700
        elo_quality = result.snapshot["data_quality"]["elo"]
        assert elo_quality["mode"] == "local_replay"
        assert elo_quality["results_applied"] == 1


def test_refresh_elo_fetch_success_reanchors_baseline():
    from worldcup.elo_local import load_baseline

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1880\n2\t2\tZA\t1695\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=elo_transport,
            observed_at="2026-06-14T00:00:00+00:00",
            history_dir=root / "history",
        )

        ratings, _aliases, baseline_at = load_baseline(cache)
        assert ratings["MX"].rating == 1880
        assert baseline_at == "2026-06-14T00:00:00+00:00"
        assert result.snapshot["data_quality"]["source_errors"] == []


def test_refresh_attaches_trend_and_finished_block():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        history = root / "history"
        history.mkdir()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        for observed in ("2026-06-08T00:00:00+00:00", "2026-06-08T01:00:00+00:00"):
            result = refresh_cache_and_build_snapshot(
                api_key="fake-key",
                cache_dir=cache,
                snapshot_path=root / "out" / "snapshot.json",
                quota_path=cache / "quota.json",
                openfootball_transport=openfootball_transport,
                theoddsapi_transport=theoddsapi_transport,
                elo_transport=elo_transport,
                history_dir=history,
                observed_at=observed,
            )

        match = result.snapshot["matches"][0]
        assert "odds_trend" in match
        assert match["odds_trend"]["1x2"]["home"], "trend points should exist from first archive"
        assert "finished" in result.snapshot
        assert result.snapshot["finished"]["tally"]["S"] == {"hit": 0, "miss": 0, "push": 0}


def test_refresh_survives_enrichment_failure(monkeypatch=None):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        bad_results = root / "results_dir"
        bad_results.mkdir()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=elo_transport,
            history_dir=root / "history",
            results_csv=bad_results,
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert result.snapshot["counts"]["matches"] == 1
