from __future__ import annotations

import json
import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.refresh_audit import build_audit, inspect_launch_agent, summarize_history


def _write_snapshot(path: Path, run_id: str, snapshot_at: str, matches: int) -> None:
    path.write_text(
        json.dumps(
            {
                "snapshot_at": snapshot_at,
                "counts": {"matches": matches},
                "run": {"run_id": run_id},
                "data_quality": {
                    "source_errors": [{"source": "theoddsapi", "error": "timeout"}],
                    "stale_sources": ["theoddsapi"],
                },
            }
        ),
        encoding="utf-8",
    )


def test_summarize_history_reads_latest_snapshots():
    with TemporaryDirectory() as tmp:
        history = Path(tmp)
        _write_snapshot(
            history / "snapshot_20260610T010000Z-live.json",
            "20260610T010000Z-live",
            "2026-06-10T01:00:00+00:00",
            70,
        )
        _write_snapshot(
            history / "snapshot_20260610T070000Z-live.json",
            "20260610T070000Z-live",
            "2026-06-10T07:00:00+00:00",
            71,
        )
        _write_snapshot(
            history / "snapshot_20260610T090000Z-live.json",
            "20260610T090000Z-live",
            "2026-06-10T09:00:00+00:00",
            72,
        )

        summary = summarize_history(history, limit=2)

    assert summary["count"] == 3
    assert [item["run_id"] for item in summary["latest"]] == [
        "20260610T090000Z-live",
        "20260610T070000Z-live",
    ]
    assert summary["latest"][0]["matches"] == 72
    assert summary["latest"][0]["source_errors_count"] == 1
    assert summary["latest"][0]["stale_sources"] == ["theoddsapi"]


def test_inspect_launch_agent_extracts_command_target():
    with TemporaryDirectory() as tmp:
        plist_path = Path(tmp) / "xin.celab.football.scheduled-publish.plist"
        with open(plist_path, "wb") as fh:
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
                    "StandardOutPath": "/tmp/football.out.log",
                    "StandardErrorPath": "/tmp/football.err.log",
                    "StartInterval": 900,
                    "RunAtLoad": True,
                },
                fh,
            )

        result = inspect_launch_agent(plist_path)

    assert result["status"] == "present"
    assert result["label"] == "xin.celab.football.scheduled-publish"
    assert result["python"] == "/opt/python/bin/python3"
    assert result["module"] == "worldcup.scheduled_publish"
    assert result["working_directory"] == "/Users/eagod/ai-dev/足彩"
    assert result["start_interval"] == 900
    assert result["run_at_load"] is True


def test_build_audit_handles_missing_launch_agent():
    with TemporaryDirectory() as tmp:
        result = build_audit(
            history_dir=Path(tmp) / "history",
            launch_agent_path=Path(tmp) / "missing.plist",
        )

    assert result["history"]["count"] == 0
    assert result["launch_agent"]["status"] == "missing"
