from __future__ import annotations

import csv
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.club_rating import load_club_results_csv, replay_club_ratings
from worldcup.csl_pending_gate import (
    build_pending_gate_report,
    build_walk_forward_matches,
    default_gate_path,
    format_pending_gate_markdown,
    main as pending_gate_main,
)


FIELDNAMES = [
    "competition_id",
    "season",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
]

FORBIDDEN_TERMS = (
    "下注金额",
    "重注",
    "追损",
    "串关",
    "stake",
    "api_key",
    "secret",
    "provider",
    "bookmaker",
)


def _write_results(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
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
            "date": "2026-03-04",
            "home_team": "Beijing Guoan",
            "away_team": "Henan FC",
            "home_score": "2",
            "away_score": "0",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-08",
            "home_team": "Shanghai Port",
            "away_team": "Henan FC",
            "home_score": "1",
            "away_score": "0",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-15",
            "home_team": "Shandong Taishan",
            "away_team": "Beijing Guoan",
            "home_score": "1",
            "away_score": "1",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-22",
            "home_team": "Henan FC",
            "away_team": "Shanghai Shenhua",
            "home_score": "0",
            "away_score": "2",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-29",
            "home_team": "Shanghai Shenhua",
            "away_team": "Shanghai Port",
            "home_score": "1",
            "away_score": "2",
            "neutral": "0",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _write_same_date_results(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
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
            "date": "2026-03-04",
            "home_team": "Beijing Guoan",
            "away_team": "Henan FC",
            "home_score": "2",
            "away_score": "0",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-08",
            "home_team": "Shandong Taishan",
            "away_team": "Shanghai Port",
            "home_score": "0",
            "away_score": "1",
            "neutral": "0",
        },
        {
            "competition_id": "csl_2026",
            "season": "2026",
            "date": "2026-03-08",
            "home_team": "Shanghai Port",
            "away_team": "Henan FC",
            "home_score": "1",
            "away_score": "0",
            "neutral": "0",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _load_fixture_results(tmp: str) -> list:
    path = Path(tmp) / "club_results_csl_2026.csv"
    _write_results(path)
    return load_club_results_csv(path, "csl_2026")


def _assert_forbidden_terms_absent(text: str) -> None:
    lower_text = text.lower()
    for forbidden in FORBIDDEN_TERMS:
        assert forbidden.lower() not in lower_text


def test_build_walk_forward_matches_uses_only_prior_results_for_ratings():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)
        warmup_pool = replay_club_ratings(
            results[:2],
            "csl_2026",
            home_adv=100.0,
        )
        expected_home = warmup_pool.rating_for("shanghai_port")
        expected_away = warmup_pool.rating_for("henan")

        rows = build_walk_forward_matches(results, warmup_matches=2, home_adv=100.0)

        assert len(rows) == 4
        assert expected_home is not None
        assert expected_away is not None
        first = rows[0]
        assert first.match_id == "csl_2026:2026-03-08:shanghai_port:henan"
        assert first.home_team == "Shanghai Port"
        assert first.away_team == "Henan FC"
        assert first.home_canonical == "shanghai_port"
        assert first.away_canonical == "henan"
        assert first.home_score == 1
        assert first.away_score == 0
        assert first.neutral is False
        assert first.odds_1x2 is None
        assert first.home_elo_before == expected_home.rating
        assert first.away_elo_before == expected_away.rating
        assert first.home_elo_before > 1500
        assert first.away_elo_before < 1500


def test_build_walk_forward_matches_batches_same_date_updates():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_same_date_results(path)
        results = load_club_results_csv(path, "csl_2026")
        warmup_pool = replay_club_ratings(
            results[:2],
            "csl_2026",
            home_adv=100.0,
        )
        expected_port = warmup_pool.rating_for("shanghai_port")

        rows = build_walk_forward_matches(results, warmup_matches=2, home_adv=100.0)

        assert expected_port is not None
        assert len(rows) == 2
        port_ratings_before = []
        for row in rows:
            if row.home_canonical == "shanghai_port":
                port_ratings_before.append(row.home_elo_before)
            if row.away_canonical == "shanghai_port":
                port_ratings_before.append(row.away_elo_before)
        assert port_ratings_before == [expected_port.rating, expected_port.rating]


def test_build_walk_forward_matches_keeps_same_date_warmup_boundary_clean():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_same_date_results(path)
        results = load_club_results_csv(path, "csl_2026")
        warmup_pool = replay_club_ratings(
            results[:2],
            "csl_2026",
            home_adv=100.0,
        )
        expected_port = warmup_pool.rating_for("shanghai_port")

        rows = build_walk_forward_matches(results, warmup_matches=3, home_adv=100.0)

        assert expected_port is not None
        assert len(rows) == 1
        row = rows[0]
        assert row.match_id == "csl_2026:2026-03-08:shanghai_port:henan"
        assert row.home_elo_before == expected_port.rating


def test_build_pending_gate_report_does_not_read_project_config():
    from worldcup import config as config_module

    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)
        config_module.load_config.cache_clear()

        with patch(
            "pathlib.Path.read_text",
            side_effect=AssertionError("unexpected config file read"),
        ):
            report = build_pending_gate_report(
                results,
                source="club_results_csl_2026.csv",
                warmup_matches=2,
                min_eval_matches=3,
                generated_at="2026-06-29T10:50:00Z",
            )

        assert report["sample"]["evaluated_matches"] == 4
        assert report["decision"]["can_lift_club_rating_pending"] is False


def test_build_pending_gate_report_requires_explicit_source():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)

        try:
            build_pending_gate_report(
                results,
                warmup_matches=2,
                min_eval_matches=3,
                generated_at="2026-06-29T10:50:00Z",
            )
        except TypeError as exc:
            assert "source" in str(exc)
        else:
            raise AssertionError("source must be explicit for pending gate reports")


def test_build_pending_gate_report_rejects_none_source():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)

        try:
            build_pending_gate_report(
                results,
                source=None,
                warmup_matches=2,
                min_eval_matches=3,
                generated_at="2026-06-29T10:50:00Z",
            )
        except ValueError as exc:
            assert "source" in str(exc)
        else:
            raise AssertionError("source=None should be rejected")


def test_pending_gate_home_prior_uses_only_dates_before_evaluated_match():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_same_date_results(path)
        results = load_club_results_csv(path, "csl_2026")

        report = build_pending_gate_report(
            results,
            source=path,
            warmup_matches=3,
            min_eval_matches=1,
            generated_at="2026-06-29T10:50:00Z",
            home_adv=100.0,
        )

        assert report["sample"]["evaluated_matches"] == 1
        assert report["metrics"]["home_prior_1x2"]["brier"] == 0.24


def test_build_pending_gate_report_keeps_no_lift_without_market_odds():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)

        report = build_pending_gate_report(
            results,
            source="club_results_csl_2026.csv",
            warmup_matches=2,
            min_eval_matches=3,
            generated_at="2026-06-29T10:50:00Z",
            home_adv=100.0,
        )

        assert report["schema_version"] == 1
        assert report["mode"] == "local_csl_pending_gate"
        assert report["generated_at"] == "2026-06-29T10:50:00Z"
        assert report["competition_id"] == "csl_2026"
        assert report["source"] == "club_results_csl_2026.csv"
        assert report["sample"] == {
            "total_results": 6,
            "warmup_matches": 2,
            "evaluated_matches": 4,
            "min_eval_matches": 3,
            "sample_too_small": False,
            "has_market_odds": False,
        }
        assert report["metrics"]["model_1x2"]["n"] == 4
        assert report["metrics"]["market_baseline"]["n"] == 0
        assert report["checks"]["market_baseline_available"] is False
        assert "can_lift" not in report["decision"]
        assert report["decision"]["can_lift_club_rating_pending"] is False
        assert report["decision"]["status"] in {"observe_only_no_lift", "keep_pending"}
        assert "historical_market_odds_missing" in report["decision"]["reasons"]


def test_build_pending_gate_report_reflects_market_baseline_without_lifting():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)
        market_report = {
            "sample": {"n_matches": 12, "n_1x2": 12},
            "markets": {
                "1x2": {
                    "market": {"n": 12, "brier": 0.51, "log_loss": 0.91},
                }
            },
        }

        report = build_pending_gate_report(
            results,
            source="club_results_csl_2026.csv",
            warmup_matches=2,
            min_eval_matches=3,
            generated_at="2026-06-29T10:50:00Z",
            home_adv=100.0,
            market_report=market_report,
        )

        assert report["sample"]["has_market_odds"] is True
        assert report["checks"]["market_baseline_available"] is True
        assert report["metrics"]["market_baseline"] == {
            "n": 12,
            "brier": 0.51,
            "log_loss": 0.91,
        }
        assert report["decision"]["can_lift_club_rating_pending"] is False
        assert report["can_lift_club_rating_pending"] is False
        assert "historical_market_odds_missing" not in report["decision"]["reasons"]


def test_pending_gate_marks_small_sample_as_keep_pending():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)

        report = build_pending_gate_report(
            results,
            source="club_results_csl_2026.csv",
            warmup_matches=4,
            min_eval_matches=10,
            generated_at="2026-06-29T10:50:00Z",
            home_adv=100.0,
        )

        assert report["sample"]["evaluated_matches"] == 2
        assert report["sample"]["sample_too_small"] is True
        assert report["decision"] == {
            "status": "keep_pending",
            "can_lift_club_rating_pending": False,
            "reasons": ["sample_too_small", "historical_market_odds_missing"],
        }


def test_format_pending_gate_markdown_is_clear_and_safe():
    with TemporaryDirectory() as tmp:
        results = _load_fixture_results(tmp)
        report = build_pending_gate_report(
            results,
            source="club_results_csl_2026.csv",
            warmup_matches=2,
            min_eval_matches=3,
            generated_at="2026-06-29T10:50:00Z",
            home_adv=100.0,
        )

        markdown = format_pending_gate_markdown(report)

        assert markdown.startswith("# CSL Pending Gate\n")
        assert "仅用于研究分析，不构成投注建议。" in markdown
        assert "can_lift_club_rating_pending: false" in markdown
        assert "\ncan_lift: false\n" not in markdown
        assert "historical_market_odds_missing" in markdown
        assert "model_1x2" in markdown
        _assert_forbidden_terms_absent(markdown)


def test_default_gate_path_uses_diagnostics_timestamp():
    path = default_gate_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-29T10:50:00Z",
    )

    assert path == Path("/tmp/worldcup/data/local/diagnostics/csl_pending_gate_20260629T105000Z.json")


def test_pending_gate_cli_writes_json():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data" / "cache"
        results_path = cache_dir / "club_results_csl_2026.csv"
        out = root / "data" / "local" / "diagnostics" / "custom_gate.json"
        _write_results(results_path)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = pending_gate_main(
                [
                    "--root",
                    str(root),
                    "--cache-dir",
                    str(cache_dir),
                    "--generated-at",
                    "2026-06-29T10:50:00Z",
                    "--warmup-matches",
                    "2",
                    "--min-eval-matches",
                    "3",
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        assert out.exists()
        summary = json.loads(stdout.getvalue())
        report = json.loads(out.read_text(encoding="utf-8"))
        assert summary == {
            "research_notice": "仅用于研究分析，不构成投注建议。",
            "competition_id": "csl_2026",
            "evaluated_matches": 4,
            "decision_status": report["decision"]["status"],
            "can_lift_club_rating_pending": False,
            "path": str(out),
        }
        assert summary["decision_status"] in {"observe_only_no_lift", "keep_pending"}
        assert report["competition_id"] == "csl_2026"
        assert report["sample"]["evaluated_matches"] == 4
        assert report["decision"]["can_lift_club_rating_pending"] is False
        _assert_forbidden_terms_absent(json.dumps(report, ensure_ascii=False, sort_keys=True))


def test_pending_gate_cli_reads_market_report_json():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data" / "cache"
        results_path = cache_dir / "club_results_csl_2026.csv"
        market_report_path = root / "data" / "local" / "backtest" / "csl_2026_report.json"
        out = root / "data" / "local" / "diagnostics" / "custom_gate.json"
        _write_results(results_path)
        market_report_path.parent.mkdir(parents=True, exist_ok=True)
        market_report_path.write_text(
            json.dumps(
                {
                    "sample": {"n_matches": 12, "n_1x2": 12},
                    "markets": {
                        "1x2": {
                            "market": {"n": 12, "brier": 0.51, "log_loss": 0.91},
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = pending_gate_main(
                [
                    "--root",
                    str(root),
                    "--cache-dir",
                    str(cache_dir),
                    "--generated-at",
                    "2026-06-29T10:50:00Z",
                    "--warmup-matches",
                    "2",
                    "--min-eval-matches",
                    "3",
                    "--market-report",
                    str(market_report_path),
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        summary = json.loads(stdout.getvalue())
        report = json.loads(out.read_text(encoding="utf-8"))
        assert summary["can_lift_club_rating_pending"] is False
        assert report["sample"]["has_market_odds"] is True
        assert report["checks"]["market_baseline_available"] is True
        assert report["metrics"]["market_baseline"]["n"] == 12
        assert report["can_lift_club_rating_pending"] is False


def test_pending_gate_cli_writes_markdown_without_project_config_read():
    from worldcup import config as config_module

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data" / "cache"
        results_path = cache_dir / "club_results_csl_2026.csv"
        out = root / "data" / "local" / "diagnostics" / "custom_gate.md"
        _write_results(results_path)
        stdout = io.StringIO()
        config_module.load_config.cache_clear()

        with patch(
            "pathlib.Path.read_text",
            side_effect=AssertionError("unexpected config file read"),
        ):
            with redirect_stdout(stdout):
                exit_code = pending_gate_main(
                    [
                        "--root",
                        str(root),
                        "--cache-dir",
                        str(cache_dir),
                        "--generated-at",
                        "2026-06-29T10:50:00Z",
                        "--warmup-matches",
                        "2",
                        "--min-eval-matches",
                        "3",
                        "--format",
                        "markdown",
                        "--out",
                        str(out),
                    ]
                )

        assert exit_code == 0
        assert out.exists()
        summary = json.loads(stdout.getvalue())
        assert summary["path"] == str(out)
        assert summary["research_notice"] == "仅用于研究分析，不构成投注建议。"
        assert summary["can_lift_club_rating_pending"] is False
        markdown = out.read_text(encoding="utf-8")
        assert markdown.startswith("# CSL Pending Gate\n")
        assert "can_lift_club_rating_pending: false" in markdown
        assert "historical_market_odds_missing" in markdown
        _assert_forbidden_terms_absent(markdown)


def test_pending_gate_cli_rejects_legacy_short_aliases():
    stderr = io.StringIO()

    with redirect_stderr(stderr):
        try:
            pending_gate_main(["--warmup", "2", "--min", "3"])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("legacy short aliases should be rejected")

    assert "unrecognized arguments" in stderr.getvalue()
    assert "--warmup" in stderr.getvalue()


def load_tests(loader, tests, pattern):
    del loader, tests, pattern
    return unittest.TestSuite(
        unittest.FunctionTestCase(test_func)
        for test_func in (
            test_build_walk_forward_matches_uses_only_prior_results_for_ratings,
            test_build_walk_forward_matches_batches_same_date_updates,
            test_build_walk_forward_matches_keeps_same_date_warmup_boundary_clean,
            test_build_pending_gate_report_does_not_read_project_config,
            test_build_pending_gate_report_requires_explicit_source,
            test_build_pending_gate_report_rejects_none_source,
            test_pending_gate_home_prior_uses_only_dates_before_evaluated_match,
            test_build_pending_gate_report_keeps_no_lift_without_market_odds,
            test_build_pending_gate_report_reflects_market_baseline_without_lifting,
            test_pending_gate_marks_small_sample_as_keep_pending,
            test_format_pending_gate_markdown_is_clear_and_safe,
            test_default_gate_path_uses_diagnostics_timestamp,
            test_pending_gate_cli_writes_json,
            test_pending_gate_cli_reads_market_report_json,
            test_pending_gate_cli_writes_markdown_without_project_config_read,
            test_pending_gate_cli_rejects_legacy_short_aliases,
        )
    )
