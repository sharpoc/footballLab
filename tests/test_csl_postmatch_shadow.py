from __future__ import annotations

import csv
from contextlib import redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import ClubResult
from worldcup.decision_settlement import settle_match_decision
import worldcup.csl_postmatch_shadow as shadow_module
from worldcup.csl_postmatch_shadow import (
    build_shadow_report,
    main as shadow_main,
    run_csl_postmatch_shadow,
)


def _decision(
    market: str,
    selection: str,
    line: float | None,
    *,
    probability: float,
    label: str = "MATCH_PICK",
    schema_version: int = 2,
    risks: list[str] | None = None,
) -> dict:
    return {
        "schema_version": schema_version,
        "policy_version": "match_pick_v3" if schema_version == 2 else "legacy_v1",
        "label": label,
        "market": market,
        "selection": selection,
        "line": line,
        "odds": 1.91,
        "p_hit": probability + 0.02,
        "p_hit_safe": probability,
        "p_no_loss_safe": probability,
        "evidence_score": 0.72,
        "uncertainty_penalty": 0.04,
        "selected_option_id": f"{market}|{selection}|{line}",
        "method": "coverage_evidence_ranked",
        "computed_at": "2026-08-09T10:30:00Z",
        "odds_latest_at": "2026-08-09T10:29:00Z",
        "valid_until": "2026-08-09T11:40:00Z",
        "reasons": ["best_available_main_market"],
        "risks": list(risks or []),
        "secret": "must-not-leak",
        "bookmakers": [{"title": "must-not-leak"}],
    }


def _snapshot(
    date: str,
    home: str,
    away: str,
    decision: dict | None,
    *,
    suffix: str,
    snapshot_hour: int = 10,
) -> dict:
    entry = {
        "kickoff_at_utc": f"{date}T12:00:00Z",
        "home_team": home.replace("_", " ").title(),
        "away_team": away.replace("_", " ").title(),
        "home_canonical": home,
        "away_canonical": away,
        "competition": {"id": "csl_2026"},
        "match_decision": decision,
        "elo": {"home": 1510, "away": 1490},
        "market": {
            "1x2": {"odds": {"home": 1.91, "draw": 3.3, "away": 4.0}},
            "ou_2_5": {"line": 2.5, "odds": {"over": 1.91, "under": 1.95}},
            "ah_main": {
                "line_home": -0.25,
                "odds": {"home": 1.91, "away": 1.97},
            },
            "raw_odds": {"provider_payload": "must-not-leak"},
            "bookmakers": [{"api_key": "must-not-leak"}],
        },
    }
    return {
        "snapshot_at": f"{date}T{snapshot_hour:02d}:30:00Z",
        "run": {"run_id": f"{date}-{suffix}"},
        "competition": {"id": "csl_2026"},
        "matches": [entry],
    }


def _result(
    date: str,
    home: str,
    away: str,
    score: tuple[int, int],
    *,
    season: str = "2026",
) -> ClubResult:
    return ClubResult(
        competition_id="csl_2026",
        season=season,
        date=date,
        home_team=home.replace("_", " ").title(),
        away_team=away.replace("_", " ").title(),
        home_canonical=home,
        away_canonical=away,
        home_score=score[0],
        away_score=score[1],
        neutral=False,
    )


def _mixed_fixture() -> tuple[list[dict], list[ClubResult]]:
    cases = [
        ("2026-08-01", "ou_home", "ou_away", _decision("OU", "over", 2.5, probability=0.60), (2, 1)),
        ("2026-08-02", "one_home", "one_away", _decision("1X2", "home", None, probability=0.55), (0, 1)),
        (
            "2026-08-03",
            "ah_home",
            "ah_away",
            _decision("AH", "home", -0.25, probability=0.50, risks=["thin_market"]),
            (1, 1),
        ),
        (
            "2026-08-04",
            "dnb_home",
            "dnb_away",
            _decision("DNB", "away", None, probability=0.58, risks=["severe_dispersion"]),
            (1, 1),
        ),
        (
            "2026-08-05",
            "no_pick_home",
            "no_pick_away",
            _decision("", "", None, probability=0.40, label="NO_CLEAN_MARKET"),
            (1, 0),
        ),
        ("2026-08-06", "missing_home", "missing_away", None, (1, 0)),
        (
            "2026-08-07",
            "invalid_home",
            "invalid_away",
            _decision("OU", "over", 2.3, probability=0.57),
            (3, 0),
        ),
        (
            "2026-08-08",
            "legacy_home",
            "legacy_away",
            _decision(
                "1X2",
                "home",
                None,
                probability=0.62,
                label="STRONG_VALUE",
                schema_version=1,
            ),
            (2, 0),
        ),
    ]
    snapshots = [
        _snapshot(date, home, away, decision, suffix=str(index))
        for index, (date, home, away, decision, _score) in enumerate(cases, start=1)
    ]
    results = [
        _result(date, home, away, score)
        for date, home, away, _decision_payload, score in cases
    ]
    results.append(_result("2026-08-09", "no_close_home", "no_close_away", (1, 0)))
    results.append(
        _result(
            "2025-08-09",
            "old_home",
            "old_away",
            (4, 0),
            season="2025",
        )
    )
    return snapshots, results


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _write_local_inputs(root: Path, *, score: tuple[int, int] = (2, 1)) -> None:
    history = root / "data/local/diagnostics/csl_history"
    history.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot(
        "2026-08-09",
        "yunnan_yukun",
        "henan",
        _decision("OU", "over", 2.5, probability=0.60),
        suffix="local",
    )
    (history / "snapshot_20260809T103000Z-live.json").write_text(
        json.dumps(snapshot, ensure_ascii=False),
        encoding="utf-8",
    )

    results = root / "data/cache/club_results_csl_2026.csv"
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-08-09",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "home_score": str(score[0]),
                "away_score": str(score[1]),
                "neutral": "0",
            }
        )


def _output_paths(root: Path) -> list[Path]:
    return [
        root / "data/local/backtest/csl_2026_eval.csv",
        root / "data/local/backtest/csl_2026_report.json",
        root / "data/local/diagnostics/csl_pending_gate_latest.json",
        root / "data/local/diagnostics/csl_postmatch_shadow.json",
        root / "data/local/diagnostics/csl_postmatch_shadow_state.json",
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shadow_report_uses_canonical_settlement_and_separates_coverage():
    snapshots, results = _mixed_fixture()

    report = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
        min_sample=50,
    )

    assert report["decision_tally"] == {
        "hit": 1,
        "miss": 2,
        "push": 1,
        "no_pick": 1,
    }
    assert report["decision_sample"] == {
        "min_sample": 50,
        "decided": 3,
        "actionable": 4,
        "decision_count": 5,
        "sample_too_small": True,
        "hit_rate": 1 / 3,
        "pick_rate": 4 / 5,
    }
    assert report["decision_coverage"] == {
        "finished_result_count": 9,
        "closing_available_count": 8,
        "missing_closing_count": 1,
        "decision_available_count": 5,
        "missing_decision_count": 1,
        "invalid_decision_count": 1,
        "unresolved_count": 0,
        "legacy_decision_count": 1,
        "identity_mismatch_count": 0,
        "result_source_blocked_count": 0,
    }
    assert len(report["matches"]) == 9
    assert report["warnings"] == ["sample_too_small"]

    for row in report["matches"]:
        if row["closing_snapshot_at"] is None:
            assert row["settlement"]["status"] == "missing_closing"
            continue
        assert row["settlement"] == settle_match_decision(
            row["closing_match_decision"],
            row["result"],
        )


def test_shadow_report_builds_literal_breakdowns_and_calibration():
    snapshots, results = _mixed_fixture()

    report = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
        min_sample=50,
    )

    markets = {item["bucket"]: item for item in report["breakdowns"]["market"]}
    assert markets["OU"]["sample"] == 2
    assert markets["OU"]["hit"] == 1
    assert markets["OU"]["miss"] == 0
    assert markets["OU"]["invalid"] == 1
    assert markets["OU"]["sample_too_small"] is True

    coverage_risk = {
        item["bucket"]: item
        for item in report["breakdowns"]["bookmaker_coverage_risk"]
    }
    assert coverage_risk["thin_market"]["sample"] == 1
    dispersion_risk = {
        item["bucket"]: item for item in report["breakdowns"]["dispersion_risk"]
    }
    assert dispersion_risk["severe_dispersion"]["sample"] == 1

    calibration = report["calibration"]
    assert calibration["sample"] == 3
    assert math.isclose(calibration["brier_score"], 0.2375)
    assert calibration["sample_too_small"] is True
    bins = {item["bucket"]: item for item in calibration["p_hit_safe"]}
    assert bins["0.50-0.54"] == {
        "bucket": "0.50-0.54",
        "sample": 1,
        "hit": 0,
        "miss": 1,
        "mean_predicted": 0.5,
        "actual_hit_rate": 0.0,
    }
    assert bins["0.60-0.64"]["actual_hit_rate"] == 1.0


def test_shadow_report_excludes_old_season_and_sensitive_raw_fields():
    snapshots, results = _mixed_fixture()

    report = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )

    assert report["season"] == "2026"
    assert all(row["season"] == "2026" for row in report["matches"])
    forbidden = {
        "api_key",
        "secret",
        "bankroll",
        "stake",
        "bookmakers",
        "provider_payload",
        "raw_odds",
    }
    assert forbidden.isdisjoint(set(_walk_keys(report)))


def test_shadow_fingerprint_ignores_time_old_season_and_unselected_snapshot():
    snapshots, results = _mixed_fixture()
    unrelated = _snapshot(
        "2026-08-20",
        "future_home",
        "future_away",
        _decision("OU", "under", 2.5, probability=0.54),
        suffix="future",
    )

    first = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )
    second = build_shadow_report(
        [*snapshots, unrelated],
        [*results, _result("2024-01-01", "older_home", "older_away", (7, 0), season="2024")],
        generated_at="2026-08-12T01:00:00Z",
    )

    assert first["input_fingerprint"] == second["input_fingerprint"]


def test_shadow_fingerprint_changes_for_result_or_selected_decision():
    snapshots, results = _mixed_fixture()
    baseline = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )["input_fingerprint"]

    changed_results = list(results)
    changed_results[0] = replace(changed_results[0], home_score=0, away_score=0)
    changed_score = build_shadow_report(
        snapshots,
        changed_results,
        generated_at="2026-08-12T00:00:00Z",
    )["input_fingerprint"]

    changed_snapshots = list(snapshots)
    changed_snapshots[0] = _snapshot(
        "2026-08-01",
        "ou_home",
        "ou_away",
        _decision("OU", "under", 2.5, probability=0.52),
        suffix="changed",
    )
    changed_decision = build_shadow_report(
        changed_snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )["input_fingerprint"]

    changed_kickoff_snapshots = list(snapshots)
    changed_kickoff_snapshots[0] = json.loads(json.dumps(snapshots[0]))
    changed_kickoff_snapshots[0]["matches"][0][
        "kickoff_at_utc"
    ] = "2026-08-01T13:00:00Z"
    changed_kickoff = build_shadow_report(
        changed_kickoff_snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )["input_fingerprint"]

    assert changed_score != baseline
    assert changed_decision != baseline
    assert changed_kickoff != baseline


def test_august_ninth_three_over_2_5_settle_two_hit_one_miss():
    cases = [
        ("first_home", "first_away", (3, 0)),
        ("second_home", "second_away", (1, 1)),
        ("third_home", "third_away", (2, 1)),
    ]
    snapshots = [
        _snapshot(
            "2026-08-09",
            home,
            away,
            _decision("OU", "over", 2.5, probability=0.56),
            suffix=str(index),
            snapshot_hour=8 + index,
        )
        for index, (home, away, _score) in enumerate(cases)
    ]
    results = [
        _result("2026-08-09", home, away, score)
        for home, away, score in cases
    ]

    report = build_shadow_report(
        snapshots,
        results,
        generated_at="2026-08-12T00:00:00Z",
    )

    assert report["decision_tally"] == {
        "hit": 2,
        "miss": 1,
        "push": 0,
        "no_pick": 0,
    }
    assert {
        (row["closing_match_decision"]["market"], row["closing_match_decision"]["selection"], row["closing_match_decision"]["line"])
        for row in report["matches"]
    } == {("OU", "over", 2.5)}


def test_shadow_runner_default_dry_run_does_not_write_outputs():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        before = sorted(
            (path.relative_to(root).as_posix(), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )

        result = run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T00:00:00Z",
        )

        after = sorted(
            (path.relative_to(root).as_posix(), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )

    assert result["status"] == "dry_run_ready"
    assert result["results"] == 1
    assert result["closing_available"] == 1
    assert result["decided"] == 1
    assert before == after


def test_shadow_runner_write_commits_hash_bound_bundle_and_repeats_unchanged():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)

        stored = run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T00:00:00Z",
            write=True,
        )
        paths = _output_paths(root)
        before_hashes = {path: _sha256(path) for path in paths[:-1]}
        before_mtimes = {path: path.stat().st_mtime_ns for path in paths[:-1]}
        state_before = json.loads(paths[-1].read_text(encoding="utf-8"))
        report = json.loads(paths[-2].read_text(encoding="utf-8"))

        def forbidden_postmatch(**_kwargs):
            raise AssertionError("unchanged input must not regenerate staged artifacts")

        unchanged = run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T01:00:00Z",
            write=True,
            postmatch_fn=forbidden_postmatch,
        )
        state_after = json.loads(paths[-1].read_text(encoding="utf-8"))

        assert all(path.exists() for path in paths)
        assert stored["status"] == "stored"
        assert report["decision_tally"] == {
            "hit": 1,
            "miss": 0,
            "push": 0,
            "no_pick": 0,
        }
        assert state_before["last_success"]["canonical_report_sha256"] == _sha256(
            paths[-2]
        )
        assert state_before["last_success"]["input_fingerprint"] == report[
            "input_fingerprint"
        ]
        assert unchanged["status"] == "unchanged"
        assert {path: _sha256(path) for path in paths[:-1]} == before_hashes
        assert {path: path.stat().st_mtime_ns for path in paths[:-1]} == before_mtimes
        assert state_after["last_success"] == state_before["last_success"]
        assert state_after["last_attempt"]["status"] == "unchanged"


def test_shadow_runner_blocked_source_preserves_successful_outputs():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T00:00:00Z",
            write=True,
        )
        paths = _output_paths(root)
        output_hashes = {path: _sha256(path) for path in paths[:-1]}
        success = json.loads(paths[-1].read_text(encoding="utf-8"))["last_success"]

        result = run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T01:00:00Z",
            source_status="blocked",
            write=True,
        )
        state = json.loads(paths[-1].read_text(encoding="utf-8"))

        assert result == {
            "status": "blocked",
            "reason": "result_source_not_accepted",
            "competition_id": "csl_2026",
        }
        assert {path: _sha256(path) for path in paths[:-1]} == output_hashes
        assert state["last_success"] == success
        assert state["last_attempt"]["status"] == "blocked"


def test_shadow_runner_repairs_corrupt_state_hash_instead_of_skipping():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T00:00:00Z",
            write=True,
        )
        state_path = _output_paths(root)[-1]
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_success"]["canonical_report_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")

        repaired = run_csl_postmatch_shadow(
            root=root,
            generated_at="2026-08-12T01:00:00Z",
            write=True,
        )
        repaired_state = json.loads(state_path.read_text(encoding="utf-8"))

        assert repaired["status"] == "stored"
        assert repaired_state["last_success"]["canonical_report_sha256"] == _sha256(
            _output_paths(root)[-2]
        )


def test_shadow_runner_regenerates_when_state_missing_or_report_malformed():
    for corruption in ("missing_state", "malformed_report"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_local_inputs(root)
            run_csl_postmatch_shadow(
                root=root,
                generated_at="2026-08-12T00:00:00Z",
                write=True,
            )
            paths = _output_paths(root)
            if corruption == "missing_state":
                paths[-1].unlink()
            else:
                paths[-2].write_text("{not-json", encoding="utf-8")

            repaired = run_csl_postmatch_shadow(
                root=root,
                generated_at="2026-08-12T01:00:00Z",
                write=True,
            )
            state = json.loads(paths[-1].read_text(encoding="utf-8"))
            report = json.loads(paths[-2].read_text(encoding="utf-8"))

            assert repaired["status"] == "stored"
            assert state["last_success"]["canonical_report_sha256"] == _sha256(
                paths[-2]
            )
            assert state["last_success"]["input_fingerprint"] == report[
                "input_fingerprint"
            ]


def test_shadow_runner_rejects_output_path_collision_before_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        shared = "data/local/diagnostics/shared.json"

        try:
            run_csl_postmatch_shadow(
                root=root,
                shadow_report=shared,
                state_path=shared,
                generated_at="2026-08-12T00:00:00Z",
                write=True,
            )
        except ValueError as exc:
            assert str(exc) == "shadow_output_path_collision"
        else:
            raise AssertionError("colliding output paths must fail closed")

        assert not (root / shared).exists()


def test_shadow_runner_rejects_non_2026_canonical_scope_before_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)

        for kwargs in (
            {"competition_id": "epl_2026_27"},
            {"season": "2025"},
        ):
            try:
                run_csl_postmatch_shadow(
                    root=root,
                    generated_at="2026-08-12T00:00:00Z",
                    write=True,
                    **kwargs,
                )
            except ValueError as exc:
                assert str(exc) == "unsupported_csl_shadow_scope"
            else:
                raise AssertionError("canonical CSL shadow scope must fail closed")

        assert not any(path.exists() for path in _output_paths(root))


def test_shadow_runner_promotion_failure_restores_previous_bundle():
    original_atomic_replace = shadow_module._atomic_replace_bytes
    for failure_position in range(1, 6):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_local_inputs(root)
            run_csl_postmatch_shadow(
                root=root,
                generated_at="2026-08-12T00:00:00Z",
                write=True,
            )
            paths = _output_paths(root)
            old_outputs = {path: path.read_bytes() for path in paths[:-1]}
            old_success = json.loads(paths[-1].read_text(encoding="utf-8"))[
                "last_success"
            ]
            _write_local_inputs(root, score=(0, 0))
            calls = 0

            def fail_selected_replace(path: Path, data: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == failure_position:
                    raise OSError("injected promotion failure")
                original_atomic_replace(path, data)

            shadow_module._atomic_replace_bytes = fail_selected_replace
            try:
                try:
                    run_csl_postmatch_shadow(
                        root=root,
                        generated_at="2026-08-12T01:00:00Z",
                        write=True,
                    )
                except OSError:
                    pass
                else:
                    raise AssertionError(
                        f"promotion {failure_position} failure must propagate"
                    )
            finally:
                shadow_module._atomic_replace_bytes = original_atomic_replace

            state = json.loads(paths[-1].read_text(encoding="utf-8"))
            assert {path: path.read_bytes() for path in paths[:-1]} == old_outputs
            assert state["last_success"] == old_success
            assert state["last_attempt"]["status"] == "error"
            assert state["last_attempt"]["reason"] == "shadow_commit_failed"
            assert "injected promotion failure" not in paths[-1].read_text(
                encoding="utf-8"
            )


def test_shadow_cli_defaults_to_dry_run_and_prints_safe_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = shadow_main(
                [
                    "--root",
                    str(root),
                    "--generated-at",
                    "2026-08-12T00:00:00Z",
                ]
            )

        summary = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert summary["status"] == "dry_run_ready"
        assert not any(path.exists() for path in _output_paths(root))
        serialized = json.dumps(summary, ensure_ascii=False).lower()
        for forbidden in (
            "api_key",
            "secret",
            "bookmakers",
            "provider_payload",
            "raw_odds",
            "stake",
            "bankroll",
        ):
            assert forbidden not in serialized


def test_shadow_cli_write_commits_local_artifacts_only_when_explicit():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_local_inputs(root)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = shadow_main(
                [
                    "--root",
                    str(root),
                    "--generated-at",
                    "2026-08-12T00:00:00Z",
                    "--write",
                ]
            )

        summary = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert summary["status"] == "stored"
        assert all(path.exists() for path in _output_paths(root))
