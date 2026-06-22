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
