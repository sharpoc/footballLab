import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.openfootball import parse_openfootball_fixtures
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.config import load_config
from worldcup.ingest import build_ingest_payload
from worldcup.local_runner import (
    _analysis_to_dict,
    _signal_to_dict,
    build_snapshot_from_cache,
    build_snapshot_from_probe,
    write_snapshot,
)
from worldcup.models import Grade, MarketType, Signal
from worldcup.pipeline import analyze_match_input, build_match_inputs


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
                            "last_update": "2026-06-08T01:00:00Z",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "last_update": "2026-06-08T02:00:00Z",
                                    "outcomes": [
                                        {"name": "Mexico", "price": 1.8},
                                        {"name": "South Africa", "price": 4.8},
                                        {"name": "Draw", "price": 3.6},
                                    ],
                                },
                                {
                                    "key": "totals",
                                    "last_update": "2026-06-08T03:00:00Z",
                                    "outcomes": [
                                        {"name": "Over", "price": 1.9, "point": 2.5},
                                        {"name": "Under", "price": 2.0, "point": 2.5},
                                    ],
                                },
                                {
                                    "key": "spreads",
                                    "last_update": "2026-06-08T04:00:00Z",
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


def _append_totals_books(root: Path, line: float, books: tuple[str, ...]) -> None:
    odds_path = root / "theoddsapi_wc_odds.json"
    data = json.loads(odds_path.read_text())
    for book in books:
        data[0]["bookmakers"].append(
            {
                "key": book,
                "last_update": "2026-06-08T05:00:00Z",
                "markets": [
                    {
                        "key": "totals",
                        "last_update": "2026-06-08T05:00:00Z",
                        "outcomes": [
                            {"name": "Over", "price": 1.92, "point": line},
                            {"name": "Under", "price": 1.88, "point": line},
                        ],
                    }
                ],
            }
        )
    odds_path.write_text(json.dumps(data))


def test_build_snapshot_from_probe_serializes_match_analysis():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        assert snapshot["snapshot_at"] == "2026-06-08T00:00:00+00:00"
        assert snapshot["counts"]["fixtures"] == 1
        assert snapshot["counts"]["match_inputs"] == 1
        assert snapshot["matches"][0]["home_team"] == "Mexico"
        assert snapshot["matches"][0]["odds_updated_at"] == "2026-06-08T04:00:00+00:00"
        assert snapshot["matches"][0]["refresh_plan"]["next_update_at"] == "2026-06-09T00:00:00+00:00"
        assert snapshot["matches"][0]["refresh_plan"]["label"] == "常规"
        assert snapshot["run"]["policy"]["match_plans"][0]["match_id"] == "event-1"
        assert snapshot["matches"][0]["market"]["1x2"]["last_update_at"] == "2026-06-08T02:00:00+00:00"
        assert snapshot["matches"][0]["market"]["ou_2_5"]["last_update_at"] == "2026-06-08T03:00:00+00:00"
        assert snapshot["matches"][0]["model"]["combined_1x2"]["home"] > 0
        decision = snapshot["matches"][0]["match_decision"]
        assert decision["schema_version"] == 1
        assert decision["label"] in {
            "STRONG_VALUE",
            "VALUE_CANDIDATE",
            "HIGH_CONFIDENCE_LEAN",
            "LOW_CONFIDENCE_LEAN",
            "NO_CLEAN_MARKET",
        }
        assert "p_hit_safe" in decision
        assert "p_no_loss_safe" in decision
        assert "reasons" in decision
        assert "risks" in decision
        assert snapshot["matches"][0]["signals"]
        ah_signal = next(
            signal
            for signal in snapshot["matches"][0]["signals"]
            if signal["market_type"] == "AsianHandicap_90min" and signal["selection"].startswith("home_")
        )
        assert ah_signal["ah_validation_shadow"]["activation"] == "shadow_only"
        assert "candidate_validated" in ah_signal["ah_validation_shadow"]


def test_build_snapshot_serializes_probability_families_without_removing_legacy_model_fields():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        model = snapshot["matches"][0]["model"]
        assert "combined_1x2" in model
        assert "ou_2_5" in model
        assert "mu_total" in model
        families = model["probability_families"]
        assert families["schema_version"] == 1
        assert families["active_signal_family"] == "model_market_total"
        assert families["recommended_future_signal_family"] == "model_raw"
        assert set(families["families"]) == {"model_raw", "model_market_total", "market_only"}
        assert families["families"]["model_raw"]["provenance"]["activation"] == "shadow_only"
        assert families["families"]["market_only"]["provenance"]["allowed_for_value_signal"] is False


def test_analysis_to_dict_serializes_ou_total_shadow_when_present():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        fixtures = parse_openfootball_fixtures(json.loads((probe_dir / "openfootball_2026.json").read_text()))
        odds_events = parse_theoddsapi_events(json.loads((probe_dir / "theoddsapi_wc_odds.json").read_text()))
        ratings = parse_elo_ratings((probe_dir / "elo_world.tsv").read_text())
        aliases = parse_elo_team_aliases((probe_dir / "elo_teams.tsv").read_text())
        match_input = build_match_inputs(fixtures, odds_events, ratings, aliases).inputs[0]

        analysis = analyze_match_input(match_input, load_config())
        match = _analysis_to_dict(analysis, [])

        shadow = match["model"]["ou_total_shadow"]
        assert shadow["schema_version"] == 1
        assert shadow["activation"] == "shadow_only"
        assert shadow["shadow_family"] == "model_raw"
        assert shadow["same_market_total_anchor"] == analysis.same_market_total_anchor


def test_analysis_to_dict_serializes_lineup_shadow_when_present():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        fixtures = parse_openfootball_fixtures(json.loads((probe_dir / "openfootball_2026.json").read_text()))
        odds_events = parse_theoddsapi_events(json.loads((probe_dir / "theoddsapi_wc_odds.json").read_text()))
        ratings = parse_elo_ratings((probe_dir / "elo_world.tsv").read_text())
        aliases = parse_elo_team_aliases((probe_dir / "elo_teams.tsv").read_text())
        match_input = build_match_inputs(fixtures, odds_events, ratings, aliases).inputs[0]
        match_input = replace(
            match_input,
            lineup_context={
                "confirmed_starting_xi": True,
                "lineup_confirmed_at": "2026-06-08T03:30:00+00:00",
                "lineup_confidence": 0.8,
                "home_attack_delta": 0.08,
            },
        )

        analysis = analyze_match_input(match_input, load_config())
        match = _analysis_to_dict(analysis, [])

        shadow = match["model"]["lineup_shadow"]
        assert shadow["schema_version"] == 1
        assert shadow["activation"] == "shadow_only"
        assert shadow["lineup_confirmed_at"] == "2026-06-08T03:30:00+00:00"
        assert shadow["post_information_odds_available"] is True


def test_build_snapshot_from_cache_reads_manual_lineups_and_requests_post_information_odds():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_probe_files(cache_dir)
        (cache_dir / "lineups_wc2026.json").write_text(
            json.dumps(
                {
                    "provider": "manual_json",
                    "matches": [
                        {
                            "source_match_no": 1,
                            "kickoff_at_utc": "2026-06-11T19:00:00Z",
                            "home_team": "Mexico",
                            "away_team": "South Africa",
                            "source": "fifa_match_centre",
                            "confirmed_starting_xi": True,
                            "lineup_confirmed_at": "2026-06-08T04:30:00Z",
                            "lineup_confidence": 1.0,
                            "home": {
                                "starting": [{"name": "Mexico GK"}],
                                "impact": {"attack_delta": 0.05},
                            },
                            "away": {
                                "starting": [{"name": "South Africa GK"}],
                                "impact": {"goalkeeper_delta": -0.03},
                            },
                        }
                    ],
                }
            )
        )

        snapshot = build_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-08T05:00:00+00:00",
        )

        match = snapshot["matches"][0]
        shadow = match["model"]["lineup_shadow"]
        assert shadow["confirmed_starting_xi"] is True
        assert shadow["lineup_confirmed_at"] == "2026-06-08T04:30:00+00:00"
        assert shadow["odds_observed_at"] == "2026-06-08T04:00:00+00:00"
        assert shadow["post_information_odds_available"] is False
        assert shadow["lineups"]["home"]["starting_count"] == 1
        assert shadow["lineups"]["away"]["starting"][0]["name"] == "South Africa GK"
        assert match["refresh_plan"]["policy_reason"] == "post_information_odds_required"
        assert match["refresh_plan"]["next_update_at"] == "2026-06-08T05:00:00+00:00"
        assert snapshot["data_quality"]["lineups"]["provider"] == "manual_json"
        assert snapshot["data_quality"]["lineups"]["confirmed"] == 1


def test_build_snapshot_from_probe_serializes_dynamic_ou_line():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        _append_totals_books(probe_dir, 1.5, ("bk2", "bk3", "bk4", "bk5"))

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")
        match = snapshot["matches"][0]
        ou_signal_lines = {
            signal["line"]
            for signal in match["signals"]
            if signal["market_type"] == "OverUnder_90min"
        }

        assert match["market"]["ou_2_5"]["line"] == 1.5
        assert match["model"]["ou_line"] == 1.5
        assert ou_signal_lines == {1.5}


def test_build_snapshot_from_probe_attaches_finished_result_when_available():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        fixture_path = probe_dir / "openfootball_2026.json"
        fixture_data = json.loads(fixture_path.read_text())
        fixture_data["matches"][0]["score1"] = 2
        fixture_data["matches"][0]["score2"] = 0
        fixture_path.write_text(json.dumps(fixture_data))

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-12T00:00:00+00:00")

        assert snapshot["matches"][0]["result"] == {
            "status": "finished",
            "home_score": 2,
            "away_score": 0,
        }


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


def test_invalid_odds_do_not_enter_market_aggregation_and_are_reported():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        odds_path = probe_dir / "theoddsapi_wc_odds.json"
        events = json.loads(odds_path.read_text())
        events[0]["bookmakers"].append(
            {
                "key": "badbook",
                "last_update": "2026-06-08T05:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-08T05:01:00Z",
                        "outcomes": [
                            {"name": "Mexico", "price": 1.0},
                            {"name": "South Africa", "price": 4.7},
                            {"name": "Draw", "price": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-06-08T05:02:00Z",
                        "outcomes": [
                            {"name": "Over", "price": 1.91, "point": 2.5},
                            {"name": "Under", "price": 0.99, "point": 2.5},
                        ],
                    },
                ],
            }
        )
        odds_path.write_text(json.dumps(events))
        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        data_quality = snapshot["data_quality"]
        assert data_quality["invalid_odds_count"] == 2
        assert len(data_quality["invalid_odds_examples"]) == 2
        first = data_quality["invalid_odds_examples"][0]
        assert first["reason"] == "odds_decimal_lte_one"
        assert first["odds"] == 1.0
        assert first["bookmaker"] == "badbook"
        assert first["market"] == "h2h"
        assert first["api_market_key"] == "h2h"
        assert first["selection"] == "home"
        assert first["outcome"] == "Mexico"
        assert first["match_id"] == "event-1"
        assert first["home_team"] == "Mexico"
        assert first["away_team"] == "South Africa"
        assert first["commence_time"] == "2026-06-11T19:00:00Z"
        assert first["last_update"] == "2026-06-08T05:01:00Z"
        assert first["raw_payload_path"] == str(odds_path)

        market_1x2 = snapshot["matches"][0]["market"]["1x2"]
        assert market_1x2["n_books_by_selection"]["home"] == 1
        assert market_1x2["n_books_by_selection"]["draw"] == 2
        assert market_1x2["n_books_by_selection"]["away"] == 2
        assert market_1x2["odds"]["home"] == 1.8
        assert market_1x2["market_probs"]


def test_signal_to_dict_serializes_candidate_grade_when_present():
    signal = Signal(
        MarketType.AH,
        "home_-1",
        Grade.B,
        0.09,
        None,
        "OK",
        ["ah_market_edge_missing"],
        -1.0,
        Grade.S,
        candidate_grade="S-candidate",
        candidate_reasons=[
            "official_grade_capped_by_ah_market_edge_missing",
            "ah_validation_shadow_candidate_validated",
        ],
    )

    out = _signal_to_dict(signal)

    assert out["grade"] == "B"
    assert out["raw_grade"] == "S"
    assert out["candidate_grade"] == "S-candidate"
    assert out["candidate_reasons"] == [
        "official_grade_capped_by_ah_market_edge_missing",
        "ah_validation_shadow_candidate_validated",
    ]


def test_signal_to_dict_omits_candidate_fields_when_absent():
    signal = Signal(MarketType.X12, "home", Grade.S, 0.1, 0.05, "OK")

    out = _signal_to_dict(signal)

    assert "candidate_grade" not in out
    assert "candidate_reasons" not in out


def test_build_snapshot_from_probe_includes_main_ah_market():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        ah_main = snapshot["matches"][0]["market"]["ah_main"]
        assert ah_main["line_home"] == -0.5
        assert ah_main["odds"] == {"home": 1.9, "away": 1.9}


def test_build_snapshot_from_probe_omits_ah_market_without_spreads():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        odds_path = probe_dir / "theoddsapi_wc_odds.json"
        events = json.loads(odds_path.read_text())
        events[0]["bookmakers"][0]["markets"] = [
            m for m in events[0]["bookmakers"][0]["markets"] if m["key"] != "spreads"
        ]
        odds_path.write_text(json.dumps(events))

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        assert "ah_main" not in snapshot["matches"][0]["market"]


def test_worldcup_snapshot_matches_include_competition_block():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        competition = snapshot["matches"][0]["competition"]
        assert competition["id"] == "fifa_world_cup_2026"
        assert competition["name"] == "2026 世界杯"
        assert competition["kind"] == "tournament"
        assert competition["fixture_source"] == "openfootball"
        assert competition["rating_policy"] == "national_team_elo"
        assert snapshot["matches"][0]["stage"] == "Matchday 1"
        assert "signals" in snapshot["matches"][0]
