from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import socket
import urllib.request
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


CAPTURED_AT = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)
COMPETITIONS = {
    "epl_2026_27": ("47", "5795372", "Arsenal", "Chelsea"),
    "bundesliga_2026_27": ("54", "5795374", "Augsburg", "Bayern Munich"),
}
TOP_LEVEL_KEYS = {
    "schema_version",
    "provider",
    "competition_id",
    "calendar_date",
    "observed_league_id",
    "calendar",
    "details",
}
RESULT_KEYS = {
    "schema_version",
    "competition_id",
    "sample_path",
    "sample_sha256",
    "evidence_fingerprint",
    "status",
    "accepted_result_count",
    "accepted_event_ids",
    "pending",
    "reason",
}


def _probe_module():
    try:
        return importlib.import_module("worldcup.league_fotmob_result_probe")
    except ModuleNotFoundError as exc:
        raise AssertionError("saved-sample evaluator interface is missing") from exc


def _status(*, finished: bool, score: str = "2 - 1", kickoff: str = "2026-08-24T19:00:00Z") -> dict:
    return {
        "utcTime": kickoff,
        "started": finished,
        "cancelled": False,
        "finished": finished,
        "scoreStr": score,
        "reason": {"short": "FT" if finished else "NS", "long": "Full-Time" if finished else "Not started"},
    }


def _match(event_id: str, home: str, away: str, *, finished: bool = True) -> dict:
    return {
        "id": int(event_id),
        "home": {"name": home},
        "away": {"name": away},
        "status": _status(finished=finished),
    }


def _detail(league_id: str, event_id: str, home: str, away: str, *, score: str = "2 - 1") -> dict:
    return {
        "general": {
            "matchId": int(event_id),
            "leagueId": int(league_id),
            "matchTimeUTCDate": "2026-08-24T19:00:00Z",
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
        },
        "header": {
            "status": {
                **_status(finished=True, score=score),
                "halfs": {"firstExtraHalfStarted": "", "secondExtraHalfStarted": ""},
                "whoLostOnPenalties": None,
                "whoLostOnAggregated": "",
            }
        },
    }


def _bundle(competition_id: str, *, include_pending: bool = False) -> dict:
    league_id, event_id, home, away = COMPETITIONS[competition_id]
    matches = [_match(event_id, home, away)]
    if include_pending:
        matches.append(_match(str(int(event_id) + 1), away, home, finished=False))
    return {
        "schema_version": 1,
        "provider": "fotmob",
        "competition_id": competition_id,
        "calendar_date": "20260824",
        "observed_league_id": league_id,
        "calendar": {"leagues": [{"id": int(league_id), "matches": matches}]},
        "details": {event_id: _detail(league_id, event_id, home, away)},
    }


def _write_bundle(root: Path, relative: str, bundle: object) -> bytes:
    raw = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _evaluate(root: Path, relative: str, competition_id: str, *, identity_registry=None) -> dict:
    module = _probe_module()
    return module.evaluate_saved_fotmob_result_bundle(
        root=root,
        sample_path=relative,
        competition_id=competition_id,
        captured_at=CAPTURED_AT,
        identity_registry=identity_registry,
    )


def _manifest(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_saved_bundle_evaluator_returns_exact_verified_schema_from_one_hardened_read():
    """Re-reading the path or emitting parser internals would break reproducible sample binding."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative = "data/probe/leagues/results/epl/sample.json"
        raw = _write_bundle(root, relative, _bundle("epl_2026_27", include_pending=True))
        original_reader = module.read_fotmob_sample_bytes
        calls: list[tuple[object, object]] = []

        def counted_reader(reader_root, reader_path):
            calls.append((reader_root, reader_path))
            return original_reader(reader_root, reader_path)

        with patch.object(module, "read_fotmob_sample_bytes", side_effect=counted_reader), patch.object(
            Path, "read_bytes", side_effect=AssertionError("unsafe preliminary read")
        ):
            result = _evaluate(root, relative, "epl_2026_27")

        assert calls == [(root, relative)]
        assert set(result) == RESULT_KEYS
        assert result == {
            "schema_version": 1,
            "competition_id": "epl_2026_27",
            "sample_path": relative,
            "sample_sha256": hashlib.sha256(raw).hexdigest(),
            "evidence_fingerprint": result["evidence_fingerprint"],
            "status": "verified",
            "accepted_result_count": 1,
            "accepted_event_ids": ["5795372"],
            "pending": [{"source_event_id": "5795373", "reason": "details_missing"}],
            "reason": None,
        }
        assert re.fullmatch(r"[0-9a-f]{64}", result["sample_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", result["evidence_fingerprint"])


def test_saved_bundle_evaluator_normalizes_root_symlink_loop_to_sample_path_invalid():
    """The evaluator must not expose Path.resolve's loop-specific RuntimeError."""
    with TemporaryDirectory() as tmp:
        loop = Path(tmp) / "loop"
        loop.symlink_to("loop", target_is_directory=True)

        result = _evaluate(
            loop,
            "data/probe/leagues/results/epl/sample.json",
            "epl_2026_27",
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "sample_path_invalid"
        assert result["sample_path"] == "data/probe/leagues/results/epl/sample.json"


def test_saved_bundle_evaluator_has_complete_deterministic_semantic_reason_taxonomy():
    """Choosing an arbitrary parser pending reason would make top-level audit status payload-order dependent."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = _bundle("epl_2026_27")
        cases: list[tuple[str, dict, str]] = []

        missing_target = deepcopy(base)
        missing_target["calendar"]["leagues"][0]["id"] = 87
        missing_target["details"] = {}
        cases.append(("missing-target", missing_target, "target_competition_missing"))

        no_finished = deepcopy(base)
        no_finished["calendar"]["leagues"][0]["matches"][0]["status"] = _status(finished=False)
        cases.append(("no-finished", no_finished, "no_current_season_finished_match"))

        missing_detail = deepcopy(base)
        missing_detail["details"] = {}
        cases.append(("missing-detail", missing_detail, "sample_detail_missing"))

        parser_rejected = deepcopy(base)
        parser_rejected["details"]["5795372"]["general"]["homeTeam"]["name"] = "Private Unknown Club"
        cases.append(("parser-rejected", parser_rejected, "strict_parser_rejected"))

        multiple = deepcopy(base)
        multiple["calendar"]["leagues"][0]["matches"].append(
            _match("5795373", "Chelsea", "Arsenal")
        )
        multiple["details"]["5795373"] = _detail("47", "5795373", "Chelsea", "Arsenal")
        cases.append(("multiple", multiple, "multiple_results_not_allowed"))

        for name, bundle, expected_reason in cases:
            relative = f"data/probe/leagues/results/epl/{name}.json"
            _write_bundle(root, relative, bundle)
            result = _evaluate(root, relative, "epl_2026_27")
            assert set(result) == RESULT_KEYS
            assert result["status"] == "blocked"
            assert result["reason"] == expected_reason
            assert result["accepted_result_count"] == (2 if name == "multiple" else 0)


def test_saved_bundle_evaluator_blocks_every_structural_failure_with_exact_reason():
    """Loose top-level or provider identity validation could relabel an unrelated saved response."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = _bundle("epl_2026_27")
        cases: list[tuple[str, dict, str]] = []

        wrong_competition = deepcopy(base)
        wrong_competition["competition_id"] = "laliga_2026_27"
        cases.append(("competition", wrong_competition, "bundle_competition_mismatch"))

        wrong_provider = deepcopy(base)
        wrong_provider["provider"] = "theoddsapi"
        cases.append(("provider", wrong_provider, "bundle_provider_invalid"))

        wrong_schema = deepcopy(base)
        wrong_schema["schema_version"] = 2
        cases.append(("schema", wrong_schema, "bundle_schema_invalid"))

        boolean_schema = deepcopy(base)
        boolean_schema["schema_version"] = True
        cases.append(("boolean-schema", boolean_schema, "bundle_schema_invalid"))

        extra_key = deepcopy(base)
        extra_key["private_metadata"] = "must-not-escape"
        cases.append(("extra", extra_key, "bundle_schema_invalid"))

        wrong_observed_id = deepcopy(base)
        wrong_observed_id["observed_league_id"] = "87"
        cases.append(("observed", wrong_observed_id, "bundle_schema_invalid"))

        for name, bundle, expected_reason in cases:
            relative = f"data/probe/leagues/results/epl/{name}.json"
            _write_bundle(root, relative, bundle)
            result = _evaluate(root, relative, "epl_2026_27")
            assert set(result) == RESULT_KEYS
            assert result["status"] == "blocked"
            assert result["reason"] == expected_reason
            assert "must-not-escape" not in json.dumps(result, sort_keys=True)

        invalid_path = _evaluate(root, "data/probe/../private.json", "epl_2026_27")
        assert invalid_path["status"] == "blocked"
        assert invalid_path["reason"] == "sample_path_invalid"


def test_saved_bundle_evaluator_accepts_exact_ascii_yyyymmdd_real_dates():
    """Provider-native ordinary and leap dates must pass the saved-wrapper contract."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, calendar_date in (
            ("ordinary", "20260824"),
            ("leap-day", "20240229"),
        ):
            bundle = _bundle("epl_2026_27")
            bundle["calendar_date"] = calendar_date
            relative = f"data/probe/leagues/results/epl/{name}.json"
            _write_bundle(root, relative, bundle)

            result = _evaluate(root, relative, "epl_2026_27")

            assert result["status"] == "verified"
            assert result["reason"] is None


def test_saved_bundle_evaluator_rejects_non_native_or_impossible_calendar_dates():
    """Only eight ASCII digits forming a real Gregorian date are valid."""
    invalid_dates = (
        ("iso", "2026-08-24"),
        ("seven-digits", "2026082"),
        ("nine-digits", "202608240"),
        ("unicode-digits", "２０２６０８２４"),
        ("ascii-nondigit", "20260A24"),
        ("boolean", True),
        ("integer", 20260824),
        ("none", None),
        ("non-leap-day", "20260229"),
        ("impossible-day", "20260230"),
        ("zero-month", "20260024"),
        ("month-thirteen", "20261324"),
    )
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, calendar_date in invalid_dates:
            bundle = _bundle("epl_2026_27")
            bundle["calendar_date"] = calendar_date
            relative = f"data/probe/leagues/results/epl/{name}.json"
            _write_bundle(root, relative, bundle)

            result = _evaluate(root, relative, "epl_2026_27")

            assert result["status"] == "blocked"
            assert result["reason"] == "bundle_schema_invalid"


def test_aggregate_rejects_empty_or_duplicate_entries_and_has_all_three_statuses():
    """Duplicate keys or empty input could make the aggregate silently incomplete."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        epl = "data/probe/leagues/results/epl/sample.json"
        bundesliga = "data/probe/leagues/results/bundesliga/sample.json"
        _write_bundle(root, epl, _bundle("epl_2026_27"))
        _write_bundle(root, bundesliga, _bundle("bundesliga_2026_27"))

        verified = module.evaluate_saved_fotmob_result_bundles(
            root=root,
            entries=(("epl_2026_27", epl), ("bundesliga_2026_27", bundesliga)),
            captured_at=CAPTURED_AT,
        )
        assert set(verified) == {
            "schema_version", "captured_at", "status", "verified_count", "blocked_count", "competitions"
        }
        assert verified["status"] == "verified"
        assert verified["verified_count"] == 2
        assert verified["blocked_count"] == 0
        assert list(verified["competitions"]) == ["bundesliga_2026_27", "epl_2026_27"]

        broken = deepcopy(_bundle("bundesliga_2026_27"))
        broken["details"] = {}
        _write_bundle(root, bundesliga, broken)
        partial = module.evaluate_saved_fotmob_result_bundles(
            root=root,
            entries=(("epl_2026_27", epl), ("bundesliga_2026_27", bundesliga)),
            captured_at=CAPTURED_AT,
        )
        blocked = module.evaluate_saved_fotmob_result_bundles(
            root=root,
            entries=(("bundesliga_2026_27", bundesliga),),
            captured_at=CAPTURED_AT,
        )
        assert partial["status"] == "partial"
        assert (partial["verified_count"], partial["blocked_count"]) == (1, 1)
        assert blocked["status"] == "blocked"

        for entries, reason in (((), "fotmob_probe_entries_required"), ((("epl_2026_27", epl), ("epl_2026_27", epl)), "fotmob_probe_competition_duplicate")):
            try:
                module.evaluate_saved_fotmob_result_bundles(
                    root=root,
                    entries=entries,
                    captured_at=CAPTURED_AT,
                )
            except ValueError as exc:
                assert str(exc) == reason
            else:
                raise AssertionError("invalid aggregate entries must fail closed")


def test_cli_is_reproducible_sorted_offline_and_has_zero_side_effects_without_out():
    """An audit-only CLI must never call a provider or create hidden runtime state."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        epl = "data/probe/leagues/results/epl/sample.json"
        bundesliga = "data/probe/leagues/results/bundesliga/calendar.json"
        _write_bundle(root, epl, _bundle("epl_2026_27"))
        _write_bundle(root, bundesliga, _bundle("bundesliga_2026_27"))
        before = _manifest(root)
        stdout = io.StringIO()

        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), patch.object(
            urllib.request, "urlopen", side_effect=AssertionError("URL open forbidden")
        ), redirect_stdout(stdout):
            code = module.main([
                "--root", str(root),
                "--entry", f"epl_2026_27={epl}",
                "--entry", f"bundesliga_2026_27={bundesliga}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])

        result = json.loads(stdout.getvalue())
        assert code == 0
        assert result["status"] == "verified"
        assert result["captured_at"] == "2026-08-26T00:00:00+00:00"
        assert list(result["competitions"]) == ["bundesliga_2026_27", "epl_2026_27"]
        assert _manifest(root) == before
        assert not list(root.rglob("*state*"))
        assert not list(root.rglob("*outbox*"))
        assert not list(root.rglob("*lock*"))


def test_cli_out_is_same_atomic_bytes_and_preserves_old_file_on_replace_failure():
    """A failed atomic replacement must not truncate a previously committed audit."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative = "data/probe/leagues/results/epl/sample.json"
        _write_bundle(root, relative, _bundle("epl_2026_27"))
        out = root / "data/probe/audit.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root), "--entry", f"epl_2026_27={relative}",
                "--captured-at", "2026-08-26T00:00:00+00:00", "--out", "data/probe/audit.json",
            ])
        assert code == 0
        assert out.read_bytes() == stdout.getvalue().encode("utf-8")

        old = b"previous-committed-audit\n"
        out.write_bytes(old)
        failed_stdout = io.StringIO()
        with patch.object(module.os, "replace", side_effect=OSError("private replace path")), redirect_stdout(failed_stdout):
            code = module.main([
                "--root", str(root), "--entry", f"epl_2026_27={relative}",
                "--captured-at", "2026-08-26T00:00:00+00:00", "--out", "data/probe/audit.json",
            ])
        assert code == 2
        assert out.read_bytes() == old
        assert json.loads(failed_stdout.getvalue()) == {"status": "error", "reason": "fotmob_probe_output_failed"}
        assert "private replace path" not in failed_stdout.getvalue()
        assert list(out.parent.glob(".audit.json.*.tmp")) == []


def test_cli_output_private_staging_preserves_old_output_on_parent_source_substitution():
    """A parent-only attacker must not reach the protected source before a failed publish."""
    module = _probe_module()
    real_replace = os.replace
    for mutation in ("external_symlink", "replacement_inode"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = "data/probe/leagues/results/epl/sample.json"
            _write_bundle(root, relative, _bundle("epl_2026_27"))
            out = root / "data/probe/audit.json"
            outside = root / "private-outside.json"
            outside.write_bytes(b"private-attacker-output")
            old = b"previous-committed-audit\n"
            out.write_bytes(old)
            parent_decoys: list[Path] = []

            def substituting_replace(
                source,
                destination,
                *,
                src_dir_fd=None,
                dst_dir_fd=None,
            ):
                # The in-scope attacker can mutate the destination parent, but
                # cannot traverse a writer-owned 0700 staging directory.
                try:
                    os.unlink(source, dir_fd=dst_dir_fd)
                except FileNotFoundError:
                    pass
                if mutation == "external_symlink":
                    os.symlink(str(outside), source, dir_fd=dst_dir_fd)
                else:
                    os.link(
                        outside,
                        source,
                        dst_dir_fd=dst_dir_fd,
                        follow_symlinks=False,
                    )
                parent_decoys.append(out.parent / str(source))
                if src_dir_fd != dst_dir_fd:
                    raise OSError("simulated publication interruption")
                return real_replace(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            stdout = io.StringIO()
            with patch.object(
                module.os,
                "replace",
                side_effect=substituting_replace,
            ), redirect_stdout(stdout):
                code = module.main([
                    "--root", str(root),
                    "--entry", f"epl_2026_27={relative}",
                    "--captured-at", "2026-08-26T00:00:00+00:00",
                    "--out", "data/probe/audit.json",
                ])

            assert code == 2
            assert json.loads(stdout.getvalue()) == {
                "status": "error",
                "reason": "fotmob_probe_output_failed",
            }
            assert outside.read_bytes() == b"private-attacker-output"
            assert out.read_bytes() == old
            assert not out.is_symlink()
            assert list(out.parent.glob(".audit.json.*.tmp")) == []
            assert list(out.parent.glob(".audit.json.*.stage")) == []
            for decoy in parent_decoys:
                if decoy.exists() or decoy.is_symlink():
                    decoy.unlink()


def test_cli_output_does_not_unlink_a_concurrent_legitimate_replacement():
    """A writer must never clean the final pathname after another publisher replaces it."""
    module = _probe_module()
    real_replace = os.replace
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative = "data/probe/leagues/results/epl/sample.json"
        _write_bundle(root, relative, _bundle("epl_2026_27"))
        out = root / "data/probe/audit.json"
        out.write_bytes(b"previous-committed-audit\n")
        concurrent = out.parent / "concurrent-legitimate.tmp"
        concurrent_bytes = b"concurrent-legitimate-audit\n"
        concurrent.write_bytes(concurrent_bytes)

        def publish_then_concurrent_replace(
            source,
            destination,
            *,
            src_dir_fd=None,
            dst_dir_fd=None,
        ):
            real_replace(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )
            real_replace(
                concurrent.name,
                destination,
                src_dir_fd=dst_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        stdout = io.StringIO()
        with patch.object(
            module.os,
            "replace",
            side_effect=publish_then_concurrent_replace,
        ), redirect_stdout(stdout):
            code = module.main([
                "--root", str(root),
                "--entry", f"epl_2026_27={relative}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
                "--out", "data/probe/audit.json",
            ])

        assert code == 0
        assert json.loads(stdout.getvalue())["status"] == "verified"
        assert out.read_bytes() == concurrent_bytes
        assert list(out.parent.glob(".audit.json.*.tmp")) == []
        assert list(out.parent.glob(".audit.json.*.stage")) == []


def test_cli_rejects_outside_traversal_and_symlink_output_without_touching_targets():
    """Output validation must bind the final rename to the real non-symlink data/probe directory fd."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative = "data/probe/leagues/results/epl/sample.json"
        _write_bundle(root, relative, _bundle("epl_2026_27"))
        outside = root / "private-output.json"
        outside.write_bytes(b"private-old-output")
        link = root / "data/probe/audit-link.json"
        link.symlink_to(outside)
        outside_dir = root / "private-output-dir"
        outside_dir.mkdir()
        (root / "data/probe/link").symlink_to(outside_dir, target_is_directory=True)

        for out_value in (str(outside), "data/probe/../private-output.json", "data/probe/audit-link.json", "data/probe/link/audit.json"):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = module.main([
                    "--root", str(root), "--entry", f"epl_2026_27={relative}",
                    "--captured-at", "2026-08-26T00:00:00+00:00", "--out", out_value,
                ])
            assert code == 2
            assert json.loads(stdout.getvalue()) == {"status": "error", "reason": "fotmob_probe_output_invalid"}
            assert str(root) not in stdout.getvalue()
            assert outside.read_bytes() == b"private-old-output"
            assert not (outside_dir / "audit.json").exists()


def test_cli_rejects_no_entries_duplicates_and_malformed_bundle_with_safe_json():
    """CLI failures must not expose raw bytes, absolute paths, or tracebacks."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        malformed = "data/probe/leagues/results/epl/private.json"
        marker = "private-provider-payload-must-not-leak"
        path = root / malformed
        path.parent.mkdir(parents=True)
        path.write_text("{" + marker, encoding="utf-8")
        cases = (
            (["--root", str(root), "--captured-at", "2026-08-26T00:00:00+00:00"], "fotmob_probe_entries_required"),
            (["--root", str(root), "--entry", f"epl_2026_27={malformed}", "--entry", f"epl_2026_27={malformed}", "--captured-at", "2026-08-26T00:00:00+00:00"], "fotmob_probe_competition_duplicate"),
        )
        for argv, reason in cases:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = module.main(argv)
            assert code == 2
            assert json.loads(stdout.getvalue()) == {"status": "error", "reason": reason}

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root), "--entry", f"epl_2026_27={malformed}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])
        payload = json.loads(stdout.getvalue())
        assert code == 2
        assert payload["status"] == "blocked"
        assert payload["competitions"]["epl_2026_27"]["reason"] == "bundle_schema_invalid"
        for forbidden in (str(root), marker, "Traceback", "JSONDecodeError"):
            assert forbidden not in stdout.getvalue()

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root), "--entry", f"epl_2026_27={root / 'private.json'}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])
        payload = json.loads(stdout.getvalue())
        assert code == 2
        assert payload["competitions"]["epl_2026_27"]["reason"] == "sample_path_invalid"
        assert payload["competitions"]["epl_2026_27"]["sample_path"] is None
        assert str(root) not in stdout.getvalue()

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root), "--entry", f"{root}={malformed}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])
        assert code == 2
        assert json.loads(stdout.getvalue()) == {
            "status": "error",
            "reason": "fotmob_probe_entries_invalid",
        }
        assert str(root) not in stdout.getvalue()

        nested_malformed = _bundle("epl_2026_27")
        nested_malformed["calendar"]["leagues"][0]["matches"] = 1
        nested_relative = "data/probe/leagues/results/epl/nested-malformed.json"
        _write_bundle(root, nested_relative, nested_malformed)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root), "--entry", f"epl_2026_27={nested_relative}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])
        payload = json.loads(stdout.getvalue())
        assert code == 2
        assert payload["status"] == "blocked"
        assert payload["competitions"]["epl_2026_27"]["reason"] == (
            "no_current_season_finished_match"
        )
        assert "Traceback" not in stdout.getvalue()


def test_cli_deep_json_is_bundle_schema_invalid_without_traceback_or_path_leakage():
    """json.loads RecursionError is untrusted bundle structure, not a process traceback."""
    module = _probe_module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relative = "data/probe/leagues/results/epl/deep.json"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"[" * 10000 + b"0" + b"]" * 10000)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            code = module.main([
                "--root", str(root),
                "--entry", f"epl_2026_27={relative}",
                "--captured-at", "2026-08-26T00:00:00+00:00",
            ])

        payload = json.loads(stdout.getvalue())
        assert code == 2
        assert payload["status"] == "blocked"
        assert payload["competitions"]["epl_2026_27"]["reason"] == (
            "bundle_schema_invalid"
        )
        for forbidden in (str(root), "Traceback", "RecursionError", "maximum recursion"):
            assert forbidden not in stdout.getvalue()
