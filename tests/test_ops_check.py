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


def _fake_fetcher(url: str, timeout: int) -> dict:
    assert timeout == 20
    path = url.split("football.celab.xin", 1)[1]
    bodies = {
        "/healthz": '{"status":"ok"}',
        "/api/matches": '{"matches":[{"home_team":"Mexico","away_team":"South Africa","signal_count":7,"top_grade":"S"}]}',
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
        _write(logs_dir / "scheduled-publish.out.log", '{"status":"skipped"}\n')
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
    assert result["local"]["snapshot"]["run_id"] == "20260610T100725Z-live"
    assert result["local"]["history"]["count"] == 1
    assert result["local"]["quota"]["providers"]["theoddsapi"]["remaining"] == 473
    assert result["public"]["healthz"]["http_status"] == 200
    assert result["public"]["matches"]["count"] == 1
    assert result["public"]["snapshot_latest"]["http_status"] == 404
    assert result["public"]["home"]["has_disclaimer"] is True
    assert result["public"]["home"]["last_update"] == "2026 年 6 月 10 日 星期三 18:07"
    assert result["remote"]["status"] == "skipped"
    assert "super-secret" not in str(result)


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
