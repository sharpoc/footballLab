from __future__ import annotations

import json
import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ops_check import run_ops_check, scan_text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_plist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(
            {
                "Label": "xin.celab.football.scheduled-publish",
                "ProgramArguments": [
                    "/opt/python/bin/python3",
                    "-m",
                    "worldcup.scheduled_publish",
                    "--live",
                ],
                "WorkingDirectory": "/Users/eagod/ai-dev/足彩",
                "StandardOutPath": str(path.parent / "scheduled-publish.out.log"),
                "StandardErrorPath": str(path.parent / "scheduled-publish.err.log"),
                "StartInterval": 900,
            },
            fh,
        )


def _write_pre_match_plist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(
            {
                "Label": "xin.celab.football.pre-match",
                "ProgramArguments": [
                    "/opt/python/bin/python3",
                    "-m",
                    "worldcup.pre_match_runner",
                    "--live-lineups",
                    "--write-lineups",
                    "--notify-missing",
                    "--notify-audit",
                ],
                "WorkingDirectory": "/Users/eagod/ai-dev/足彩",
                "StandardOutPath": str(path.parent / "pre-match.out.log"),
                "StandardErrorPath": str(path.parent / "pre-match.err.log"),
                "StartInterval": 300,
            },
            fh,
        )


def _fake_fetcher(url: str, timeout: int) -> dict:
    assert timeout == 20
    path = url.split("football.celab.xin", 1)[1]
    bodies = {
        "/healthz": '{"status":"ok"}',
        "/api/matches": '{"matches":[{"home_team":"Mexico","away_team":"South Africa","signal_count":7,"top_grade":"S"}]}',
        "/api/finished": json.dumps(
            {
                "finished": {
                    "schema_version": 1,
                    "summary": {
                        "match_count": 1,
                        "signal_count": 2,
                        "skipped_no_closing": 0,
                        "tally": {
                            "S": {"hit": 1, "miss": 0, "push": 0},
                            "A": {"hit": 0, "miss": 0, "push": 1},
                        },
                        "coverage": {
                            "finished_result_count": 1,
                            "closing_available_count": 1,
                            "missing_closing_count": 0,
                            "closing_coverage_rate": 1.0,
                        },
                        "sample": {
                            "min_sample": 20,
                            "decided_strong_signal_count": 1,
                            "sample_too_small": True,
                        },
                    },
                    "matches": [],
                }
            }
        ),
        "/api/snapshot/latest": '{"error":"not_found"}',
        "/": "<html><p>仅用于研究分析，不构成投注建议。</p><p>最后更新<br>2026 年 6 月 10 日 星期三 18:07</p></html>",
        "/preview": "<html><p>仅用于研究分析，不构成投注建议。</p><p>最后更新<br>2026 年 6 月 10 日 星期三 18:07</p></html>",
    }
    statuses = {"/api/snapshot/latest": 404}
    body = bodies[path]
    return {
        "status": statuses.get(path, 200),
        "body": body,
        "headers": {"content-type": "text/html" if path in {"/", "/preview"} else "application/json"},
    }


def _csl_live_odds_event(
    home_team: str = "Shanghai SIPG FC",
    away_team: str = "Beijing FC",
    event_id: str = "csl-event-1",
) -> dict:
    return {
        "id": event_id,
        "sport_key": "soccer_china_superleague",
        "commence_time": "2026-06-25T11:35:00Z",
        "home_team": home_team,
        "away_team": away_team,
        "bookmakers": [
            {
                "key": "safe_book",
                "last_update": "2026-06-24T01:51:18Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": home_team, "price": 2.05},
                            {"name": away_team, "price": 3.10},
                            {"name": "Draw", "price": 3.30},
                        ],
                    },
                    {
                        "key": "spreads",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": home_team, "price": 1.91, "point": -0.5},
                            {"name": away_team, "price": 1.95, "point": 0.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-06-24T01:51:18Z",
                        "outcomes": [
                            {"name": "Over", "price": 1.88, "point": 2.5},
                            {"name": "Under", "price": 1.98, "point": 2.5},
                        ],
                    },
                ],
            }
        ],
    }


def _write_minimal_ops_inputs(root: Path, launch_agent: Path) -> None:
    snapshot = {
        "snapshot_at": "2026-06-10T10:07:25+00:00",
        "counts": {"matches": 72},
        "matches": [],
        "run": {"run_id": "20260610T100725Z-live"},
        "data_quality": {"source_errors": [], "stale_sources": []},
    }
    _write(root / "data/cache/analysis_snapshot.json", json.dumps(snapshot))
    _write(
        root / "data/cache/quota.json",
        json.dumps(
            {
                "providers": {
                    "theoddsapi_secondary": {
                        "remaining": 248,
                        "used": 252,
                        "last": 3,
                        "api_key": "must-not-leak",
                    }
                }
            }
        ),
    )
    _write_plist(launch_agent)


def test_scan_text_counts_sensitive_terms_without_values():
    result = scan_text("GET /tokens.json\npassword=super-secret\nall good")

    assert result["sensitive_hits"] == 2
    assert result["error_hits"] == 0
    assert "super-secret" not in str(result)


def test_scan_text_does_not_count_safe_api_key_field_names_as_secret_leaks():
    result = scan_text('{"provider":"theoddsapi","api_key":null,"status":"configured"}')

    assert result["sensitive_hits"] == 0
    assert result["sensitive_field_name_hits"] == 1


def test_run_ops_check_summarizes_local_and_public_state_without_secrets():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot = {
            "snapshot_at": "2026-06-10T10:07:25+00:00",
            "counts": {"matches": 72},
            "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
            "run": {"run_id": "20260610T100725Z-live"},
            "data_quality": {"source_errors": [], "stale_sources": []},
        }
        _write(root / "data/cache/analysis_snapshot.json", json.dumps(snapshot))
        _write(
            root / "data/cache/quota.json",
            '{"providers":{"theoddsapi":{"remaining":473,"used":27,"last":3}}}',
        )
        _write(root / "data/local/history/snapshot_20260610T100725Z-live.json", json.dumps(snapshot))
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_plist(launch_agent)
        pre_match_launch_agent = logs_dir / "xin.celab.football.pre-match.plist"
        _write_pre_match_plist(pre_match_launch_agent)
        _write(logs_dir / "scheduled-publish.out.log", '{"status":"skipped"}\n')
        _write(logs_dir / "scheduled-publish.err.log", "")
        _write(
            logs_dir / "pre-match.out.log",
            '{"lineup_audit":{"notification":{"status":"skipped"}}}\n',
        )
        _write(logs_dir / "pre-match.err.log", "")
        _write(
            root / "data/local/diagnostics/lineup_audit.json",
            json.dumps(
                {
                    "generated_at": "2026-06-22T01:41:00+00:00",
                    "summary": {
                        "confirmed_lineups": 10,
                        "captured_before_kickoff": 0,
                        "entered_snapshot": 5,
                        "post_information_odds_available": 5,
                        "captured_without_snapshot_input": 5,
                        "captured_without_post_information_odds": 5,
                    },
                    "notifications": {"sent_count": 3},
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url="https://football.celab.xin",
            fetcher=_fake_fetcher,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[
                logs_dir / "scheduled-publish.out.log",
                logs_dir / "scheduled-publish.err.log",
            ],
            pre_match_launch_agent_path=pre_match_launch_agent,
            pre_match_log_paths=[
                logs_dir / "pre-match.out.log",
                logs_dir / "pre-match.err.log",
            ],
        )

    assert result["ok"] is True
    assert result["local"]["snapshot"]["run_id"] == "20260610T100725Z-live"
    assert result["local"]["history"]["count"] == 1
    assert result["local"]["quota"]["providers"]["theoddsapi"]["remaining"] == 473
    assert result["public"]["healthz"]["http_status"] == 200
    assert result["public"]["matches"]["count"] == 1
    assert result["public"]["finished"]["http_status"] == 200
    assert result["public"]["finished"]["summary"]["sample"]["sample_too_small"] is True
    assert result["public"]["snapshot_latest"]["http_status"] == 404
    assert result["public"]["home"]["has_disclaimer"] is True
    assert result["public"]["home"]["last_update"] == "2026 年 6 月 10 日 星期三 18:07"
    assert result["remote"]["status"] == "skipped"
    pre_match = result["local"]["pre_match"]
    assert pre_match["launch_agent"]["label"] == "xin.celab.football.pre-match"
    assert pre_match["wiring"] == {
        "has_live_lineups": True,
        "has_write_lineups": True,
        "has_notify_missing": True,
        "has_notify_audit": True,
        "has_refresh_guard": False,
        "has_refresh_after_lineups": False,
        "has_live_refresh": False,
    }
    assert pre_match["lineup_audit"]["summary"]["confirmed_lineups"] == 10
    assert pre_match["lineup_audit"]["summary"]["captured_before_kickoff"] == 0
    assert pre_match["lineup_audit"]["notifications"]["sent_count"] == 3
    assert "super-secret" not in str(result)


def test_run_ops_check_summarizes_csl_live_odds_without_raw_prices_or_secrets():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(
                {
                    "status": "fetched",
                    "events": 1,
                    "observed_at": "2026-06-24T01:51:18.952055+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "theoddsapi_secondary",
                    "quota_remaining": 248,
                    "quota_last": 3,
                    "cache_path": "data/cache/theoddsapi_csl_2026_odds.json",
                    "raw_price_should_not_leak": 1.91,
                    "secret": "must-not-leak",
                }
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "fixture_source": "odds_event_only",
                    "warnings": ["club_rating_pending", "odds_event_only"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "rating_policy": "club_rating_pending",
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": [],
                    },
                    "signals": 7,
                    "strong_grades": [],
                    "raw_market_should_not_leak": [{"price": 1.91}],
                    "secret": "must-not-leak",
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    csl = result["local"]["csl_live_odds"]
    assert result["ok"] is True
    assert csl["status"] == "ok"
    assert csl["competition_id"] == "csl_2026"
    assert csl["events"] == 1
    assert csl["sport_keys"] == ["soccer_china_superleague"]
    assert csl["has_synthetic_marker"] is False
    assert csl["club_alias_unmatched"] == []
    assert csl["invalid_odds_count"] == 0
    assert csl["quota"]["providers"]["theoddsapi_secondary"] == {
        "remaining": 248,
        "used": 252,
        "last": 3,
    }
    assert csl["refresh_diagnostic"]["status"] == "fetched"
    assert csl["runner_check"]["counts"]["matches"] == 1
    assert csl["runner_check"]["warnings"] == ["club_rating_pending", "odds_event_only"]
    assert csl["runner_check"]["club_rating"]["mode"] == "sample_replay"
    assert csl["runner_check"]["strong_grades"] == []
    rendered = str(result)
    assert "must-not-leak" not in rendered
    assert "raw_market_should_not_leak" not in rendered
    assert "raw_price_should_not_leak" not in rendered
    assert "bookmakers" not in rendered
    assert "safe_book" not in rendered
    assert "2.05" not in rendered


def test_run_ops_check_sanitizes_csl_live_whitelisted_values():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/quota.json",
            json.dumps(
                {
                    "providers": {
                        "https://example.test/secret?token=must-not-leak": {
                            "remaining": 100,
                            "used": 200,
                            "last": 3,
                        },
                        "opaqueLiveKeyABC123": {
                            "remaining": 111,
                            "used": 222,
                            "last": 3,
                        },
                        "theoddsapi_secondary": {
                            "remaining": "must-not-leak",
                            "used": 252,
                            "last": 3,
                            "api_key": "must-not-leak",
                        }
                    }
                }
            ),
        )
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(
                {
                    "status": "fetched",
                    "events": 1,
                    "observed_at": "2026-06-24T01:51:18+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "secret:must-not-leak",
                    "quota_remaining": "must-not-leak",
                    "quota_last": 3,
                    "cache_path": "data/cache/../../opaqueLiveKeyABC123.json",
                }
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": [],
                    "errors": [
                        "club_rating_missing",
                        "opaqueLiveKeyABC123",
                        "https://example.test/secret?token=must-not-leak",
                        {"secret": "must-not-leak"},
                    ],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": ["missing", "opaqueLiveKeyABC123", "token=must-not-leak"],
                    },
                    "strong_grades": [],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    csl = result["local"]["csl_live_odds"]
    assert "must-not-leak" not in str(result)
    assert "https://" not in str(result)
    assert "secret" not in str(result)
    assert "opaqueLiveKeyABC123" not in str(result)
    assert "../../" not in str(result)
    assert "https://example.test/secret?token=must-not-leak" not in csl["quota"]["providers"]
    assert "https://example.test/secret?token=must-not-leak" not in result["local"]["quota"]["providers"]
    assert "opaqueLiveKeyABC123" not in csl["quota"]["providers"]
    assert "opaqueLiveKeyABC123" not in result["local"]["quota"]["providers"]
    assert csl["quota"]["providers"]["theoddsapi_secondary"] == {
        "used": 252,
        "last": 3,
    }
    assert result["local"]["quota"]["providers"]["theoddsapi_secondary"] == {
        "used": 252,
        "last": 3,
    }
    assert csl["refresh_diagnostic"]["status"] == "fetched"
    assert csl["refresh_diagnostic"]["observed_at"] == "2026-06-24T01:51:18+00:00"
    assert "theoddsapi_provider" not in csl["refresh_diagnostic"]
    assert "cache_path" not in csl["refresh_diagnostic"]
    assert "quota_remaining" not in csl["refresh_diagnostic"]
    assert csl["runner_check"]["errors"] == ["club_rating_missing"]
    assert csl["runner_check"]["errors_count"] == 4
    assert csl["runner_check"]["club_rating"]["errors"] == ["missing"]
    assert csl["runner_check"]["club_rating"]["errors_count"] == 3


def test_run_ops_check_flags_csl_live_alias_drift_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event(away_team="Unknown FC")]),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert result["local"]["csl_live_odds"]["club_alias_unmatched"] == ["Unknown FC"]


def test_run_ops_check_sanitizes_csl_live_raw_cache_identifiers_but_counts_alias_drift():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        event = _csl_live_odds_event(
            away_team="secret:must-not-leak",
        )
        event["sport_key"] = "https://example.test?token=must-not-leak"
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([event]))

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    csl = result["local"]["csl_live_odds"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert csl["sport_keys"] == []
    assert csl["club_alias_unmatched"] == []
    assert csl["club_alias_unmatched_count"] == 1
    rendered = str(result)
    assert "must-not-leak" not in rendered
    assert "https://" not in rendered
    assert "secret:" not in rendered


def test_run_ops_check_drops_opaque_team_like_alias_without_hiding_count():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        event = _csl_live_odds_event(
            away_team="Opaque Live Key ABC123",
        )
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([event]))

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    csl = result["local"]["csl_live_odds"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert csl["club_alias_unmatched"] == []
    assert csl["club_alias_unmatched_count"] == 1
    assert "Opaque Live Key ABC123" not in str(result)


def test_run_ops_check_flags_csl_live_synthetic_marker_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        event = _csl_live_odds_event()
        event["_synthetic_smoke"] = True
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([event]))

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert result["local"]["csl_live_odds"]["has_synthetic_marker"] is True


def test_run_ops_check_reports_malformed_csl_live_payload_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        event = _csl_live_odds_event()
        event["bookmakers"] = ["not-a-dict"]
        _write(root / "data/cache/theoddsapi_csl_2026_odds.json", json.dumps([event]))

        try:
            result = run_ops_check(
                root=root,
                public_base_url=None,
                remote_host=None,
                launch_agent_path=launch_agent,
                local_log_paths=[],
                pre_match_launch_agent_path=None,
                pre_match_log_paths=[],
            )
        except AttributeError as exc:
            raise AssertionError("run_ops_check raised AttributeError") from exc

    csl = result["local"]["csl_live_odds"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert csl["status"] == "error"
    assert csl["message"] == "invalid_odds_cache_payload"
    assert csl["error_type"] == "AttributeError"


def test_run_ops_check_treats_missing_csl_live_cache_as_warning_only():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is True
    assert result["summary"]["warnings"] == 1
    assert result["local"]["csl_live_odds"]["status"] == "missing"


def test_run_ops_check_flags_csl_live_runner_blockers_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": ["club_rating_missing"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "fallback",
                        "matches_replayed": 0,
                        "teams_rated": 0,
                        "sample_too_small": True,
                        "errors": ["missing"],
                    },
                    "strong_grades": [],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    runner = result["local"]["csl_live_odds"]["runner_check"]
    assert runner["warnings"] == ["club_rating_missing"]
    assert runner["strong_grades"] == []


def test_run_ops_check_flags_csl_live_runner_errors_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": [],
                    "errors": ["runner_failed"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": [],
                    },
                    "strong_grades": [],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    runner = result["local"]["csl_live_odds"]["runner_check"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert runner["errors_count"] == 1
    assert runner["errors"] == ["runner_failed"]


def test_run_ops_check_counts_csl_live_runner_sensitive_alias_without_leaking():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": [],
                    "errors": [],
                    "club_alias_unmatched": ["secret:must-not-leak"],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": [],
                    },
                    "strong_grades": [],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    runner = result["local"]["csl_live_odds"]["runner_check"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert runner["club_alias_unmatched"] == []
    assert runner["club_alias_unmatched_count"] == 1
    assert "must-not-leak" not in str(result)
    assert "secret:" not in str(result)


def test_run_ops_check_counts_csl_live_club_rating_sensitive_errors_without_leaking():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": [],
                    "errors": [],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": ["token=must-not-leak"],
                    },
                    "strong_grades": [],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    club_rating = result["local"]["csl_live_odds"]["runner_check"]["club_rating"]
    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert "must-not-leak" not in str(result)
    assert club_rating["errors"] == []
    assert club_rating["errors_count"] == 1


def test_run_ops_check_flags_csl_live_strong_grades_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_minimal_ops_inputs(root, launch_agent)
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps([_csl_live_odds_event()]),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": ["club_rating_pending", "odds_event_only"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "sample_too_small": False,
                        "errors": [],
                    },
                    "strong_grades": ["S"],
                }
            ),
        )

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=None,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    runner = result["local"]["csl_live_odds"]["runner_check"]
    assert runner["warnings"] == ["club_rating_pending", "odds_event_only"]
    assert runner["strong_grades"] == ["S"]


def test_run_ops_check_flags_pre_match_live_refresh_without_guard_as_error():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        pre_match_launch_agent = logs_dir / "xin.celab.football.pre-match.plist"
        _write_plist(launch_agent)
        _write_pre_match_plist(pre_match_launch_agent)
        with open(pre_match_launch_agent, "rb") as fh:
            plist = plistlib.load(fh)
        plist["ProgramArguments"].append("--live-refresh")
        with open(pre_match_launch_agent, "wb") as fh:
            plistlib.dump(plist, fh)
        _write(root / "data/cache/analysis_snapshot.json", json.dumps({"matches": [], "data_quality": {}}))
        _write(root / "data/cache/quota.json", '{"providers":{}}')

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=pre_match_launch_agent,
            pre_match_log_paths=[],
        )

    assert result["ok"] is False
    assert result["summary"]["errors"] == 1
    assert result["local"]["pre_match"]["wiring"]["has_refresh_guard"] is False
    assert result["local"]["pre_match"]["wiring"]["has_live_refresh"] is True


def test_run_ops_check_allows_pre_match_live_refresh_with_guard():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        pre_match_launch_agent = logs_dir / "xin.celab.football.pre-match.plist"
        _write_plist(launch_agent)
        _write_pre_match_plist(pre_match_launch_agent)
        with open(pre_match_launch_agent, "rb") as fh:
            plist = plistlib.load(fh)
        plist["ProgramArguments"] += [
            "--refresh-guard",
            "--refresh-after-lineups",
            "--live-refresh",
        ]
        with open(pre_match_launch_agent, "wb") as fh:
            plistlib.dump(plist, fh)
        _write(root / "data/cache/analysis_snapshot.json", json.dumps({"matches": [], "data_quality": {}}))
        _write(root / "data/cache/quota.json", '{"providers":{}}')

        result = run_ops_check(
            root=root,
            public_base_url=None,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[],
            pre_match_launch_agent_path=pre_match_launch_agent,
            pre_match_log_paths=[],
        )

    assert result["ok"] is True
    assert result["summary"]["errors"] == 0
    wiring = result["local"]["pre_match"]["wiring"]
    assert wiring["has_refresh_guard"] is True
    assert wiring["has_live_refresh"] is True


def test_run_ops_check_reports_finished_tally_and_results_consistency():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot = {
            "snapshot_at": "2026-06-10T10:07:25+00:00",
            "counts": {"matches": 72},
            "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
            "run": {"run_id": "20260610T100725Z-live"},
            "data_quality": {"source_errors": [], "stale_sources": []},
            "finished": {
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-10T00:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "result": {"status": "finished", "home_score": 2, "away_score": 0},
                        "closing_signals": [
                            {
                                "grade": "S",
                                "prediction": {"status": "hit", "label": "命中"},
                            },
                            {
                                "grade": "A",
                                "prediction": {"status": "push", "label": "走水"},
                            },
                        ],
                    }
                ],
                "tally": {
                    "S": {"hit": 1, "miss": 0, "push": 0},
                    "A": {"hit": 0, "miss": 0, "push": 1},
                },
                "skipped_no_closing": 0,
            },
        }
        _write(root / "data/cache/analysis_snapshot.json", json.dumps(snapshot))
        _write(root / "data/cache/quota.json", '{"providers":{}}')
        _write(root / "data/local/history/snapshot_20260610T100725Z-live.json", json.dumps(snapshot))
        _write(
            root / "data/local/results/wc2026_results.csv",
            "\n".join(
                [
                    "kickoff_at_utc,home_team,away_team,home_canonical,away_canonical,home_score,away_score,captured_at",
                    "2026-06-10T00:00:00+00:00,Mexico,South Africa,Mexico,South Africa,2,0,2026-06-10T02:00:00+00:00",
                ]
            ),
        )
        logs_dir = root / "logs"
        launch_agent = logs_dir / "xin.celab.football.scheduled-publish.plist"
        _write_plist(launch_agent)
        _write(logs_dir / "scheduled-publish.out.log", "")
        _write(logs_dir / "scheduled-publish.err.log", "")

        result = run_ops_check(
            root=root,
            public_base_url="https://football.celab.xin",
            fetcher=_fake_fetcher,
            remote_host=None,
            launch_agent_path=launch_agent,
            local_log_paths=[
                logs_dir / "scheduled-publish.out.log",
                logs_dir / "scheduled-publish.err.log",
            ],
        )

    assert result["ok"] is True
    assert result["local"]["finished"]["status"] == "ok"
    assert result["local"]["finished"]["summary"]["match_count"] == 1
    assert result["local"]["finished"]["tally_matches"] is True
    assert result["local"]["finished"]["results"]["count"] == 1
    assert result["local"]["finished"]["results"]["matches_finished_result_count"] is True


def test_run_ops_check_summarizes_remote_metadata_without_payload_json():
    calls: list[tuple[str, int]] = []

    def fake_remote_runner(host: str, timeout: int) -> dict:
        calls.append((host, timeout))
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "services": {
                        "worldcup": {"active": True},
                        "nginx": {"active": True},
                    },
                    "sqlite": {
                        "snapshot_count": 7,
                        "latest_meta": {
                            "run_id": "20260610T100725Z-live",
                            "snapshot_at": "2026-06-10T10:07:25+00:00",
                            "payload_json": "must-not-leak",
                        },
                    },
                    "logs": {
                        "journal": {"sensitive_hits": 0, "error_hits": 0},
                        "nginx": {"sensitive_project_hits": 0, "errors_5xx_or_upstream": 0},
                    },
                }
            ),
            "stderr": "",
        }

    result = run_ops_check(
        root=Path("."),
        public_base_url=None,
        remote_host="strategy-lab-ecs",
        remote_runner=fake_remote_runner,
    )

    assert calls == [("strategy-lab-ecs", 20)]
    assert result["remote"]["status"] == "ok"
    assert result["remote"]["sqlite"]["snapshot_count"] == 7
    assert result["remote"]["sqlite"]["latest_meta"]["run_id"] == "20260610T100725Z-live"
    assert "payload_json" not in result["remote"]["sqlite"]["latest_meta"]
    assert "must-not-leak" not in str(result)
