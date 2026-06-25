from __future__ import annotations

import io
import json
import plistlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.ops_daily_report import (
    build_daily_ops_report,
    default_report_path,
    format_daily_ops_markdown,
    main as ops_daily_main,
)


def _write_plist(path: Path, *, working_directory: str) -> None:
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
                "WorkingDirectory": working_directory,
                "StandardOutPath": str(path.parent / "scheduled-publish.out.log"),
                "StandardErrorPath": str(path.parent / "scheduled-publish.err.log"),
                "StartInterval": 900,
            },
            fh,
        )


def _minimal_ops_result() -> dict:
    return {
        "summary": {"errors": 0, "warnings": 0},
        "report": {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {
                "status": "ok",
                "competition_id": "csl_2026",
                "events": 8,
                "fixtures": 8,
                "odds_events": 8,
                "sport_keys": ["soccer_china_superleague"],
                "observed_at": "2026-06-24T01:51:18+00:00",
                "provider": "theoddsapi_secondary",
                "quota_remaining": 248,
                "quota_last": 3,
                "has_synthetic_marker": False,
                "club_alias_unmatched_count": 0,
                "invalid_odds_count": 0,
                "runner_status": "ok",
                "runner_matches": 8,
                "runner_warnings": ["club_rating_pending", "odds_event_only"],
                "runner_errors_count": 0,
                "runner_strong_grades": [],
                "rating_policy": "club_rating_pending",
                "club_rating_mode": "sample_replay",
                "club_rating_matches_replayed": 840,
                "club_rating_teams_rated": 22,
                "issues": [],
            },
        },
        "local": {
            "csl_live_odds": {
                "raw_should_not_leak": {
                    "bookmaker": "must-not-leak",
                    "price": 2.05,
                    "api_key": "secret-like-value",
                }
            }
        },
    }


def test_build_daily_ops_report_uses_only_sanitized_ops_report():
    daily = build_daily_ops_report(
        _minimal_ops_result(),
        generated_at="2026-06-24T08:00:00Z",
    )

    assert daily["schema_version"] == 1
    assert daily["generated_at"] == "2026-06-24T08:00:00Z"
    assert daily["mode"] == "local_dry_run"
    assert daily["status"] == "ok"
    assert daily["errors"] == 0
    assert daily["warnings"] == 0
    assert daily["scope"] == {
        "public": False,
        "remote": False,
        "live_refresh": False,
        "notify": False,
        "deploy": False,
    }
    assert daily["delivery"] == {
        "status": "skipped",
        "reason": "dry_run_no_notification",
    }
    assert daily["csl_live_odds"]["events"] == 8
    assert daily["csl_live_odds"]["runner_warnings"] == [
        "club_rating_pending",
        "odds_event_only",
    ]
    text = json.dumps(daily, ensure_ascii=False, sort_keys=True)
    assert "must-not-leak" not in text
    assert "2.05" not in text
    assert "secret-like-value" not in text
    assert "bookmaker" not in text
    assert "api_key" not in text


def test_format_daily_ops_markdown_is_reviewable_and_safe():
    daily = build_daily_ops_report(
        _minimal_ops_result(),
        generated_at="2026-06-24T08:00:00Z",
    )

    markdown = format_daily_ops_markdown(daily)

    assert markdown.startswith("# Ops Daily Report\n")
    assert "generated_at: 2026-06-24T08:00:00Z" in markdown
    assert "mode: local_dry_run" in markdown
    assert "delivery: skipped (dry_run_no_notification)" in markdown
    assert "仅用于研究分析，不构成投注建议。" in markdown
    assert "CSL live odds: ok events=8 fixtures=8 odds_events=8" in markdown
    assert "warnings=club_rating_pending,odds_event_only strong_grades=none" in markdown
    assert "must-not-leak" not in markdown
    assert "2.05" not in markdown
    assert "api_key" not in markdown


def test_default_report_path_uses_cache_and_timestamp_extension():
    path = default_report_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-24T08:00:00Z",
        output_format="markdown",
    )

    assert path == Path("/tmp/worldcup/data/cache/ops_daily_report_20260624T080000Z.md")


def test_ops_daily_report_cli_writes_local_markdown_without_public_or_remote_checks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        launch_agent = root / "logs" / "xin.celab.football.scheduled-publish.plist"
        _write_plist(launch_agent, working_directory=str(root))
        out = root / "data/cache/custom_report.md"
        stdout = io.StringIO()

        def fake_run_ops_check(**kwargs: object) -> dict:
            assert kwargs["public_base_url"] is None
            assert kwargs["remote_host"] is None
            assert Path(kwargs["root"]) == root
            assert kwargs["pre_match_launch_agent_path"] is None
            return _minimal_ops_result()

        with patch(
            "worldcup.ops_daily_report.run_ops_check",
            side_effect=fake_run_ops_check,
        ) as run_ops_check:
            with redirect_stdout(stdout):
                exit_code = ops_daily_main(
                    [
                        "--root",
                        str(root),
                        "--launch-agent",
                        str(launch_agent),
                        "--local-log",
                        str(root / "logs" / "missing.out.log"),
                        "--pre-match-launch-agent",
                        "none",
                        "--generated-at",
                        "2026-06-24T08:00:00Z",
                        "--out",
                        str(out),
                    ]
                )

        run_ops_check.assert_called_once()

        assert exit_code == 0
        assert out.exists()
        report_text = out.read_text(encoding="utf-8")
        write_summary = json.loads(stdout.getvalue())
        assert write_summary == {
            "status": "ok",
            "errors": 0,
            "warnings": 0,
            "mode": "local_dry_run",
            "format": "markdown",
            "path": str(out),
        }
        assert "mode: local_dry_run" in report_text
        assert "delivery: skipped (dry_run_no_notification)" in report_text
        assert "CSL live odds: ok events=8 fixtures=8 odds_events=8" in report_text
        assert "must-not-leak" not in report_text
        assert "2.05" not in report_text
        assert "api_key" not in report_text


def test_build_daily_ops_report_marks_missing_or_malformed_report_as_error():
    for ops_result in (
        {"summary": {"errors": 0, "warnings": 0}},
        {"summary": {"errors": 0, "warnings": 0}, "report": ["not", "a", "dict"]},
    ):
        daily = build_daily_ops_report(
            ops_result,
            generated_at="2026-06-24T08:00:00Z",
        )

        assert daily["status"] == "error"
        assert daily["errors"] >= 1
        assert daily["csl_live_odds"] == {}
        assert daily["delivery"] == {
            "status": "skipped",
            "reason": "dry_run_no_notification",
        }


def test_generated_at_is_normalized_to_utc_for_offset_and_naive_values():
    assert (
        build_daily_ops_report(
            _minimal_ops_result(),
            generated_at="2026-06-24T08:00:00Z",
        )["generated_at"]
        == "2026-06-24T08:00:00Z"
    )
    assert (
        build_daily_ops_report(
            _minimal_ops_result(),
            generated_at="2026-06-24T16:00:00+08:00",
        )["generated_at"]
        == "2026-06-24T08:00:00Z"
    )
    assert (
        build_daily_ops_report(
            _minimal_ops_result(),
            generated_at="2026-06-24T08:00:00",
        )["generated_at"]
        == "2026-06-24T08:00:00Z"
    )

    path = default_report_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-24T16:00:00+08:00",
        output_format="json",
    )

    assert path == Path("/tmp/worldcup/data/cache/ops_daily_report_20260624T080000Z.json")


def test_ops_daily_report_cli_rejects_invalid_generated_at_before_running_checks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "data/cache/invalid.json"
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("worldcup.ops_daily_report.run_ops_check") as run_ops_check:
            try:
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    ops_daily_main(
                        [
                            "--root",
                            str(root),
                            "--generated-at",
                            "not-a-date",
                            "--format",
                            "json",
                            "--out",
                            str(out),
                        ]
                    )
            except SystemExit as exc:
                assert exc.code == 2
            else:
                raise AssertionError("expected SystemExit(2)")

        run_ops_check.assert_not_called()
        assert not out.exists()
        assert "invalid --generated-at" in stderr.getvalue()
        assert stdout.getvalue() == ""


def test_ops_daily_report_cli_returns_nonzero_for_malformed_report():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "data/cache/malformed.md"
        stdout = io.StringIO()

        with patch(
            "worldcup.ops_daily_report.run_ops_check",
            return_value={"summary": {"errors": 0, "warnings": 0}},
        ):
            with redirect_stdout(stdout):
                exit_code = ops_daily_main(
                    [
                        "--root",
                        str(root),
                        "--generated-at",
                        "2026-06-24T08:00:00Z",
                        "--out",
                        str(out),
                    ]
                )

        write_summary = json.loads(stdout.getvalue())
        report_text = out.read_text(encoding="utf-8")
        assert exit_code == 1
        assert write_summary["status"] == "error"
        assert write_summary["errors"] >= 1
        assert "status: error" in report_text
        assert "delivery: skipped (dry_run_no_notification)" in report_text


def test_ops_daily_report_cli_writes_json_report_without_raw_payload_leaks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "data/cache/custom_report.json"
        stdout = io.StringIO()

        with patch(
            "worldcup.ops_daily_report.run_ops_check",
            return_value=_minimal_ops_result(),
        ):
            with redirect_stdout(stdout):
                exit_code = ops_daily_main(
                    [
                        "--root",
                        str(root),
                        "--generated-at",
                        "2026-06-24T16:00:00+08:00",
                        "--format",
                        "json",
                        "--out",
                        str(out),
                    ]
                )

        write_summary = json.loads(stdout.getvalue())
        written = json.loads(out.read_text(encoding="utf-8"))
        written_text = json.dumps(written, ensure_ascii=False, sort_keys=True)
        assert exit_code == 0
        assert write_summary["format"] == "json"
        assert written["generated_at"] == "2026-06-24T08:00:00Z"
        assert written["mode"] == "local_dry_run"
        assert "must-not-leak" not in written_text
        assert "2.05" not in written_text
        assert "secret-like-value" not in written_text
        assert "bookmaker" not in written_text
        assert "api_key" not in written_text


def test_build_daily_ops_report_deepcopies_sanitized_csl_report():
    ops_result = _minimal_ops_result()
    daily = build_daily_ops_report(
        ops_result,
        generated_at="2026-06-24T08:00:00Z",
    )

    ops_result["report"]["csl_live_odds"]["events"] = 99

    assert daily["csl_live_odds"]["events"] == 8


def test_build_daily_ops_report_rejects_inconsistent_sanitized_report():
    inconsistent_reports = (
        {
            "schema_version": 1,
            "status": "error",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {},
        },
        {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 0,
        },
        {
            "schema_version": 1,
            "status": "green",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {},
        },
    )

    for report in inconsistent_reports:
        daily = build_daily_ops_report(
            {"summary": {"errors": 0, "warnings": 0}, "report": report},
            generated_at="2026-06-24T08:00:00Z",
        )

        assert daily["status"] == "error"
        assert daily["errors"] >= 1
        assert daily["csl_live_odds"] == {}


def test_ops_daily_report_cli_returns_nonzero_for_inconsistent_sanitized_report():
    inconsistent_reports = (
        {
            "schema_version": 1,
            "status": "error",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {},
        },
        {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 0,
        },
        {
            "schema_version": 1,
            "status": "green",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {},
        },
    )

    for index, report in enumerate(inconsistent_reports):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / f"data/cache/inconsistent_{index}.json"
            stdout = io.StringIO()

            with patch(
                "worldcup.ops_daily_report.run_ops_check",
                return_value={"summary": {"errors": 0, "warnings": 0}, "report": report},
            ):
                with redirect_stdout(stdout):
                    exit_code = ops_daily_main(
                        [
                            "--root",
                            str(root),
                            "--generated-at",
                            "2026-06-24T08:00:00Z",
                            "--format",
                            "json",
                            "--out",
                            str(out),
                        ]
                    )

            write_summary = json.loads(stdout.getvalue())
            written = json.loads(out.read_text(encoding="utf-8"))
            assert exit_code == 1
            assert write_summary["status"] == "error"
            assert write_summary["errors"] >= 1
            assert written["status"] == "error"
            assert written["errors"] >= 1


def _status_count_inconsistent_reports() -> tuple[dict, ...]:
    csl_report = _minimal_ops_result()["report"]["csl_live_odds"]
    return (
        {
            "schema_version": 1,
            "status": "ok",
            "errors": 1,
            "warnings": 0,
            "csl_live_odds": csl_report,
        },
        {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 1,
            "csl_live_odds": csl_report,
        },
        {
            "schema_version": 1,
            "status": "warn",
            "errors": 1,
            "warnings": 1,
            "csl_live_odds": csl_report,
        },
        {
            "schema_version": 1,
            "status": "warn",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": csl_report,
        },
        {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {},
        },
    )


def test_build_daily_ops_report_rejects_status_count_inconsistency():
    for report in _status_count_inconsistent_reports():
        daily = build_daily_ops_report(
            {"summary": {"errors": 0, "warnings": 0}, "report": report},
            generated_at="2026-06-24T08:00:00Z",
        )

        assert daily["status"] == "error"
        assert daily["errors"] >= 1


def test_ops_daily_report_cli_returns_nonzero_for_status_count_inconsistency():
    for index, report in enumerate(_status_count_inconsistent_reports()):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / f"data/cache/status_count_{index}.json"
            stdout = io.StringIO()

            with patch(
                "worldcup.ops_daily_report.run_ops_check",
                return_value={"summary": {"errors": 0, "warnings": 0}, "report": report},
            ):
                with redirect_stdout(stdout):
                    exit_code = ops_daily_main(
                        [
                            "--root",
                            str(root),
                            "--generated-at",
                            "2026-06-24T08:00:00Z",
                            "--format",
                            "json",
                            "--out",
                            str(out),
                        ]
                    )

            write_summary = json.loads(stdout.getvalue())
            written = json.loads(out.read_text(encoding="utf-8"))
            assert exit_code == 1
            assert write_summary["status"] == "error"
            assert write_summary["errors"] >= 1
            assert written["status"] == "error"
            assert written["errors"] >= 1
