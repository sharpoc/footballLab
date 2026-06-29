from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_observation_report import (
    build_observation_report,
    default_report_path,
    format_observation_markdown,
    main as observation_main,
)


def _snapshot() -> dict:
    return {
        "snapshot_at": "2026-06-29T02:32:31.106142+00:00",
        "stake": "must-not-leak-stake",
        "competition": {
            "id": "csl_2026",
            "name": "中超 2026",
            "rating_policy": "club_rating_pending",
        },
        "counts": {"fixtures": 1, "odds_events": 1, "match_inputs": 1, "matches": 1},
        "data_quality": {
            "fixture_source": "odds_event_only",
            "warnings": [
                "club_rating_pending",
                "odds_event_only",
                "api_key=secret-like-value",
                "bookmaker_private_feed",
                "provider_payload",
            ],
            "club_alias_unmatched": [],
            "invalid_odds_count": 0,
            "invalid_odds_examples": [],
            "club_rating": {
                "mode": "sample_replay",
                "source": "data/cache/club_results_csl_2026.csv",
                "competition_id": "csl_2026",
                "matches_replayed": 840,
                "teams_rated": 22,
                "missing_teams": [
                    "shanghai_shenhua",
                    "http://must-not-leak-team",
                    "bookmaker_team",
                    "provider_feed",
                ],
                "skipped_rows": 0,
                "sample_too_small": False,
                "errors": [
                    "sample_replay_warning",
                    "secret-like-value",
                    "price_2.39",
                    "token:must-not-leak",
                    "provider_payload",
                ],
            },
        },
        "matches": [
            {
                "source_event_id": "csl-event-1",
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "elo": {"home": 1556, "away": 1556},
                "model": {
                    "combined_1x2": {"home": 0.4741499, "draw": 0.2445535, "away": 0.2812966},
                    "ou_2_5": {"over": 0.5701251, "under": 0.4298749},
                    "mu_total": 2.9703123,
                },
                "market": {
                    "1x2": {
                        "market_probs": {"home": 0.3837166, "draw": 0.2646045, "away": 0.3516789},
                        "odds": {"home": 2.39, "draw": 3.46, "away": 2.61},
                        "n_books_by_selection": {"home": 18, "draw": 18, "away": 18},
                        "bookmakers": [
                            {
                                "key": "must-not-leak-bookmaker",
                                "price": 9.99,
                                "api_key": "secret-like-value",
                            }
                        ],
                        "stake": "must-not-leak-stake",
                    },
                    "ou_2_5": {
                        "line": 2.5,
                        "market_probs": {"over": 0.6050856, "under": 0.3949144},
                        "odds": {"over": 1.52, "under": 2.33},
                        "n_books_by_selection": {"over": 5, "under": 5},
                        "bookmakers": [
                            {
                                "key": "must-not-leak-bookmaker-ou",
                                "price": 9.99,
                                "api_key": "secret-like-value",
                            }
                        ],
                        "stake": "must-not-leak-stake",
                    },
                },
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "B",
                        "raw_grade": "S",
                        "ev": 0.1334817,
                        "edge": 0.0904333,
                        "status": "OK",
                        "reasons": [
                            "ah_not_supporting_1x2",
                            "secret-like-value",
                            "http://must-not-leak-reason",
                            "price_2.39",
                            "provider_feed",
                        ],
                    },
                    {
                        "market_type": "OU_2_5_90min",
                        "selection": "under",
                        "grade": "C",
                        "raw_grade": "C",
                        "ev": -0.02,
                        "edge": -0.01,
                        "status": "OK",
                        "reasons": [],
                    },
                    {
                        "market_type": "OU_2_5_90min",
                        "selection": "over",
                        "grade": "C",
                        "raw_grade": "C",
                        "ev": 0.01,
                        "edge": 0.02,
                        "status": "OK",
                        "reasons": ["must-not-leak-positive-signal"],
                    },
                ],
            }
        ],
    }


def _assert_forbidden_terms_absent(text: str) -> None:
    lower_text = text.lower()
    for forbidden in (
        "must-not-leak",
        "secret-like-value",
        "bookmaker",
        "api_key",
        "token",
        "cookie",
        "hmac",
        "private",
        "provider",
        "provider_payload",
        "provider_payload_event",
        "provider_feed",
        "provider_payload_grade",
        "provider_payload_raw_grade",
        "stake",
        "price",
        "http://",
        "2.39",
        "3.46",
        "2.61",
        "1.52",
        "2.33",
        "9.99",
    ):
        assert forbidden not in lower_text


def _assert_markdown_forbidden_terms_absent(markdown: str) -> None:
    _assert_forbidden_terms_absent(markdown)
    for forbidden in ("下注金额", "重注", "追损", "串关"):
        assert forbidden not in markdown


def test_build_observation_report_sanitizes_snapshot_and_counts_caps():
    report = build_observation_report(
        _snapshot(),
        generated_at="2026-06-29T10:40:00Z",
    )

    assert report["schema_version"] == 1
    assert report["mode"] == "local_csl_observation"
    assert report["generated_at"] == "2026-06-29T10:40:00Z"
    assert report["status"] == "warn"
    assert report["competition"]["id"] == "csl_2026"
    assert report["counts"]["matches"] == 1
    assert report["counts"]["final_strong_grades"] == 0
    assert report["counts"]["raw_strong_candidates"] == 1
    assert report["warnings"] == ["club_rating_pending", "odds_event_only"]
    assert report["data_quality"]["club_rating"]["mode"] == "sample_replay"
    assert report["data_quality"]["club_rating"]["missing_teams"] == ["shanghai_shenhua"]
    assert report["data_quality"]["club_rating"]["errors"] == ["sample_replay_warning"]

    match = report["matches"][0]
    assert match["home_team"] == "Yunnan Yukun"
    assert match["away_team"] == "Henan FC"
    assert match["model_1x2"]["home"] == 0.4741
    assert match["market_1x2"]["away"] == 0.3517
    assert match["ou_2_5"] == {"line": 2.5, "model_over": 0.5701, "market_over": 0.6051}
    assert match["signals"] == [
        {
            "market_type": "1X2_90min",
            "selection": "home",
            "grade": "B",
            "raw_grade": "S",
            "ev": 0.1335,
            "edge": 0.0904,
            "status": "OK",
            "reasons": ["ah_not_supporting_1x2"],
        }
    ]

    text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "\"odds\"" not in text
    _assert_forbidden_terms_absent(text)


def test_format_observation_markdown_is_reviewable_and_research_only():
    report = build_observation_report(
        _snapshot(),
        generated_at="2026-06-29T10:40:00Z",
    )

    markdown = format_observation_markdown(report)

    assert markdown.startswith("# CSL Observation Report\n")
    assert "仅用于研究分析，不构成投注建议。" in markdown
    assert "status: warn" in markdown
    assert "matches: 1" in markdown
    assert "raw strong candidates: 1" in markdown
    assert "final strong grades: 0" in markdown
    assert "Yunnan Yukun vs Henan FC" in markdown
    assert "1X2_90min home grade=B raw=S EV=0.1335 Edge=0.0904" in markdown
    assert "club_rating_pending" in markdown
    _assert_markdown_forbidden_terms_absent(markdown)


def test_default_observation_report_path_uses_cache_timestamp():
    path = default_report_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-29T10:40:00Z",
        output_format="markdown",
    )

    assert path == Path("/tmp/worldcup/data/cache/csl_observation_report_20260629T104000Z.md")


def test_observation_report_cli_writes_default_markdown_path():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(_snapshot(), ensure_ascii=False), encoding="utf-8")
        out = root / "data/cache/csl_observation_report_20260629T104000Z.md"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = observation_main(
                [
                    "--root",
                    str(root),
                    "--snapshot",
                    str(snapshot_path),
                    "--generated-at",
                    "2026-06-29T10:40:00Z",
                ]
            )

        assert exit_code == 0
        assert out.exists()
        summary = json.loads(stdout.getvalue())
        assert summary == {
            "research_notice": "仅用于研究分析，不构成投注建议。",
            "status": "warn",
            "matches": 1,
            "raw_strong_candidates": 1,
            "final_strong_grades": 0,
            "format": "markdown",
            "path": str(out),
        }
        content = out.read_text(encoding="utf-8")
        assert "CSL Observation Report" in content
        _assert_markdown_forbidden_terms_absent(content)


def test_observation_report_sanitizes_scalar_event_and_grade_fields():
    snapshot = _snapshot()
    leaking_match = json.loads(json.dumps(snapshot["matches"][0], ensure_ascii=False))
    leaking_match["source_event_id"] = "provider_payload_event"
    leaking_match["signals"] = [
        {
            "market_type": "1X2_90min",
            "selection": "home",
            "grade": "provider_payload_grade",
            "raw_grade": "S",
            "ev": 0.12,
            "edge": 0.08,
            "status": "OK",
            "reasons": ["ah_not_supporting_1x2"],
        },
        {
            "market_type": "1X2_90min",
            "selection": "away",
            "grade": "S",
            "raw_grade": "provider_payload_raw_grade",
            "ev": 0.09,
            "edge": 0.04,
            "status": "OK",
            "reasons": [],
        },
    ]
    snapshot["matches"] = [leaking_match]

    report = build_observation_report(snapshot, generated_at="2026-06-29T10:40:00Z")

    match = report["matches"][0]
    assert match["source_event_id"] in (None, "")
    assert match["signals"] == [
        {
            "market_type": "1X2_90min",
            "selection": "home",
            "grade": "",
            "raw_grade": "S",
            "ev": 0.12,
            "edge": 0.08,
            "status": "OK",
            "reasons": ["ah_not_supporting_1x2"],
        },
        {
            "market_type": "1X2_90min",
            "selection": "away",
            "grade": "S",
            "raw_grade": "",
            "ev": 0.09,
            "edge": 0.04,
            "status": "OK",
            "reasons": [],
        },
    ]

    report_text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    _assert_forbidden_terms_absent(report_text)
    markdown = format_observation_markdown(report)
    _assert_markdown_forbidden_terms_absent(markdown)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        snapshot_path.parent.mkdir(parents=True)
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        out = root / "data/cache/csl_observation_report_20260629T104000Z.md"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = observation_main(
                [
                    "--root",
                    str(root),
                    "--snapshot",
                    str(snapshot_path),
                    "--generated-at",
                    "2026-06-29T10:40:00Z",
                ]
            )

        assert exit_code == 0
        _assert_forbidden_terms_absent(stdout.getvalue())
        _assert_markdown_forbidden_terms_absent(out.read_text(encoding="utf-8"))


def load_tests(loader, tests, pattern):
    del loader, tests, pattern
    return unittest.TestSuite(
        unittest.FunctionTestCase(test_func)
        for test_func in (
            test_build_observation_report_sanitizes_snapshot_and_counts_caps,
            test_format_observation_markdown_is_reviewable_and_research_only,
            test_default_observation_report_path_uses_cache_timestamp,
            test_observation_report_cli_writes_default_markdown_path,
            test_observation_report_sanitizes_scalar_event_and_grade_fields,
        )
    )
