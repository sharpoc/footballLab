import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.refresh_runner import refresh_cache_and_build_snapshot


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
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert result.snapshot["counts"]["matches"] == 1
        assert result.snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert result.snapshot_path.exists()
        assert result.run_metadata["run_id"] == "20260608T000000Z-live"
        assert result.snapshot["run"]["quota"]["theoddsapi"]["remaining"] == 497
        assert result.snapshot["run"]["stale_sources"] == []
        assert result.cache_dir.joinpath("openfootball_2026.json").exists()
        assert result.cache_dir.joinpath("theoddsapi_wc_odds.json").exists()
        assert json.loads(result.quota_path.read_text())["providers"]["theoddsapi"]["remaining"] == 497


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
        )

        assert result.snapshot["counts"]["matches"] == 1
        assert result.snapshot["run"]["stale_sources"] == ["theoddsapi"]
        assert result.snapshot["data_quality"]["stale_sources"] == ["theoddsapi"]
        assert result.snapshot["data_quality"]["source_errors"][0]["source"] == "theoddsapi"
        assert "handshake timed out" in result.snapshot["data_quality"]["source_errors"][0]["error"]
