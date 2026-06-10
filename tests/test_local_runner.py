import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.config import load_config
from worldcup.ingest import build_ingest_payload
from worldcup.local_runner import build_snapshot_from_cache, build_snapshot_from_probe, write_snapshot


def _write_probe_files(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "openfootball_2026.json").write_text(
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
        )
    )
    (root / "theoddsapi_wc_odds.json").write_text(
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
        )
    )
    (root / "elo_world.tsv").write_text("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    (root / "elo_teams.tsv").write_text("MX\tMexico\nZA\tSouth Africa\n")


def test_build_snapshot_from_probe_serializes_match_analysis():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        assert snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert snapshot["counts"]["fixtures"] == 1
        assert snapshot["counts"]["match_inputs"] == 1
        assert snapshot["matches"][0]["home_team"] == "Mexico"
        assert snapshot["matches"][0]["model"]["combined_1x2"]["home"] > 0
        assert snapshot["matches"][0]["signals"]


def test_write_snapshot_creates_parent_directory_and_json_file():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "snapshot.json"
        write_snapshot({"ok": True}, out)
        assert json.loads(out.read_text()) == {"ok": True}


def test_build_snapshot_from_cache_reads_same_input_contract():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_probe_files(cache_dir)

        snapshot = build_snapshot_from_cache(cache_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        assert snapshot["counts"]["matches"] == 1
        assert snapshot["matches"][0]["away_team"] == "South Africa"


def test_build_snapshot_from_cache_includes_run_metadata_for_ingest():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_probe_files(cache_dir)

        snapshot = build_snapshot_from_cache(cache_dir, snapshot_at="2026-06-08T00:00:00+00:00")
        payload = build_ingest_payload(
            snapshot,
            generated_at="2026-06-08T00:01:00+00:00",
        )

        assert snapshot["run"]["run_id"] == "20260608T000000Z-local"
        assert payload["run_id"] == "20260608T000000Z-local"


def test_build_snapshot_caps_signals_when_odds_quotes_are_stale():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        odds_path = probe_dir / "theoddsapi_wc_odds.json"
        odds_events = json.loads(odds_path.read_text())
        for market in odds_events[0]["bookmakers"][0]["markets"]:
            market["last_update"] = "2026-06-08T00:00:00Z"
            if market["key"] == "h2h":
                market["outcomes"][0]["price"] = 2.2
        odds_path.write_text(json.dumps(odds_events))
        base_cfg = load_config()
        cfg = {**base_cfg, "odds": {**base_cfg["odds"], "min_books": 1}}

        snapshot = build_snapshot_from_probe(
            probe_dir,
            snapshot_at="2026-06-08T04:00:01+00:00",
            cfg=cfg,
        )

        home_signal = next(
            signal
            for signal in snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert home_signal["grade"] == "B"
        assert "stale_odds" in home_signal["reasons"]
