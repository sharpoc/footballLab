from __future__ import annotations

import io
import json
import multiprocessing
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_closing_coverage import select_observed_closing_exact
from worldcup.csl_scheduled_publish import _load_archive_history_safe
from worldcup.csl_snapshot_archive import (
    archive_snapshot,
    load_snapshot,
    main as archive_main,
    target_snapshot_path,
    validate_snapshot,
)


def _snapshot(
    *,
    snapshot_at: str = "2026-07-03T11:30:00+00:00",
    competition_id: str = "csl_2026",
    matches: list[dict] | None = None,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "competition": {"id": competition_id, "name": "Chinese Super League"},
        "matches": matches
        if matches is not None
        else [
            {
                "competition": {"id": "csl_2026"},
                "kickoff_at_utc": "2026-07-03T12:00:00+00:00",
                "home_team": "Yunnan Yukun",
                "away_team": "Henan FC",
                "home_canonical": "yunnan_yukun",
                "away_canonical": "henan_fc",
                "market": {
                    "1x2": {
                        "bookmaker": "must-not-leak",
                        "odds": {"home": 2.4, "draw": 3.4, "away": 3.0},
                    }
                },
            }
        ],
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _canonical_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _archive_race_worker(
    source: Path,
    history: Path,
    start: object,
    results: object,
) -> None:
    start.wait()
    try:
        summary = archive_snapshot(source=source, history=history)
    except ValueError as exc:
        results.put(str(exc).split(":", 1)[0])
    else:
        results.put(summary["status"])


def test_target_path_uses_snapshot_at_utc_stamp():
    with TemporaryDirectory() as tmp:
        history = Path(tmp) / "history"

        path = target_snapshot_path(
            _snapshot(snapshot_at="2026-07-03T19:30:00+08:00"),
            history,
        )

        assert path == history / "snapshot_20260703T113000Z-live.json"


def test_archive_writes_csl_snapshot_to_history_with_stable_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        history = root / "data/local/diagnostics/csl_history"
        _write_json(source, _snapshot())

        summary = archive_snapshot(source=source, history=history)

        archived = history / "snapshot_20260703T113000Z-live.json"
        assert summary == {
            "status": "created",
            "created": True,
            "duplicate": False,
            "dry_run": False,
            "competition_id": "csl_2026",
            "snapshot_at": "2026-07-03T11:30:00Z",
            "matches": 1,
            "late_matches": 0,
            "source": str(source),
            "path": str(archived),
        }
        assert json.loads(archived.read_text(encoding="utf-8")) == _snapshot()


def test_archive_is_idempotent_for_same_snapshot_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "csl_live_league_snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        first = archive_snapshot(source=source, history=history)
        second = archive_snapshot(source=source, history=history)

        assert first["status"] == "created"
        assert second["status"] == "duplicate"
        assert second["created"] is False
        assert second["duplicate"] is True
        assert len(list(history.glob("snapshot_*.json"))) == 1


def test_archive_dry_run_validates_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "csl_live_league_snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        summary = archive_snapshot(source=source, history=history, dry_run=True)

        assert summary["status"] == "dry_run"
        assert summary["created"] is False
        assert not list(history.glob("snapshot_*.json"))


def test_archive_rejects_naive_snapshot_time_before_creating_history():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot(snapshot_at="2026-07-03T11:30:00"))

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "invalid_snapshot_at"
        else:
            raise AssertionError("expected naive snapshot time to fail closed")

        assert not history.exists()


def test_archive_rejects_naive_match_kickoff_before_creating_history():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        payload["matches"][0]["kickoff_at_utc"] = "2026-07-03T12:00:00"
        _write_json(source, payload)

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "invalid_match_kickoff:0"
        else:
            raise AssertionError("expected naive match kickoff to fail closed")

        assert not history.exists()


def test_task5_archive_is_accepted_by_task6_history_loader():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        archive_snapshot(source=source, history=history)
        loaded = _load_archive_history_safe(history)

        assert loaded["status"] == "ok"
        assert loaded["warning"] is None
        assert loaded["snapshots"] == [_snapshot()]


def test_archive_rejects_wrong_competition_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot(competition_id="fifa_world_cup_2026"))

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "unexpected_competition"
        else:
            raise AssertionError("expected wrong competition to be rejected")

        assert not history.exists()


def test_archive_rejects_nested_wrong_competition_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        payload["matches"][0]["competition"] = {"id": "fifa_world_cup_2026"}
        _write_json(source, payload)

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "unexpected_match_competition:0"
        else:
            raise AssertionError("expected nested wrong competition to be rejected")

        assert not history.exists()


def test_every_row_requires_own_competition_dict_including_postponed():
    private_marker = "private-competition-marker"
    for fixture_status in ("SCHEDULED", "POSTPONED"):
        for row_competition, expected_error in (
            (None, "missing_match_competition:0"),
            (private_marker, "invalid_match_competition:0"),
        ):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "snapshot.json"
                history = root / "history"
                payload = _snapshot()
                payload["matches"][0]["fixture_status"] = fixture_status
                if row_competition is None:
                    payload["matches"][0].pop("competition")
                else:
                    payload["matches"][0]["competition"] = row_competition
                _write_json(source, payload)

                try:
                    archive_snapshot(source=source, history=history)
                except ValueError as exc:
                    assert str(exc) == expected_error
                    assert private_marker not in str(exc)
                else:
                    raise AssertionError("expected invalid row competition to fail closed")

                assert not history.exists()


def test_every_row_requires_string_canonical_identity_including_postponed():
    for fixture_status in ("SCHEDULED", "POSTPONED"):
        for field in ("home_canonical", "away_canonical"):
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "snapshot.json"
                history = root / "history"
                payload = _snapshot()
                payload["matches"][0]["fixture_status"] = fixture_status
                payload["matches"][0][field] = 12345
                _write_json(source, payload)

                try:
                    archive_snapshot(source=source, history=history)
                except ValueError as exc:
                    assert str(exc) == "missing_match_identity:0"
                else:
                    raise AssertionError("expected numeric canonical identity to fail closed")

                assert not history.exists()


def test_archive_rejects_missing_canonical_identity_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        payload["matches"][0].pop("home_canonical")
        _write_json(source, payload)

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "missing_match_identity:0"
        else:
            raise AssertionError("expected missing canonical identity to be rejected")

        assert not history.exists()


def test_postponed_archive_row_still_requires_identity_and_kickoff():
    for missing_field, expected_error in (
        ("away_canonical", "missing_match_identity:0"),
        ("kickoff_at_utc", "missing_snapshot_at"),
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "snapshot.json"
            history = root / "history"
            payload = _snapshot()
            payload["matches"][0]["fixture_status"] = "POSTPONED"
            payload["matches"][0].pop(missing_field)
            _write_json(source, payload)

            try:
                archive_snapshot(source=source, history=history)
            except ValueError as exc:
                assert str(exc) == expected_error
            else:
                raise AssertionError(f"expected missing {missing_field} to be rejected")

            assert not history.exists()


def test_archive_rejects_empty_matches_without_writing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot(matches=[]))

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert "insufficient_matches" in str(exc)
        else:
            raise AssertionError("expected empty snapshot to be rejected")

        assert not history.exists()


def test_archive_commit_failure_never_exposes_final_target():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        def broken_commit(path: Path, _content: str, **_kwargs) -> None:
            path.with_name(f".{path.name}.partial").write_text("{", encoding="utf-8")
            raise OSError("interrupted")

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=broken_commit,
            )
        except OSError:
            pass
        else:
            raise AssertionError("expected interrupted archive write")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert not target.exists()


def test_created_archive_is_reopened_and_identity_validated():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())
        summary = archive_snapshot(source=source, history=history)
        stored = load_snapshot(summary["path"])
        metadata = validate_snapshot(stored)

    assert metadata == {
        "competition_id": summary["competition_id"],
        "snapshot_at": summary["snapshot_at"],
        "matches": summary["matches"],
    }


def test_created_archive_requires_canonical_raw_bytes_and_preserves_final():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        _write_json(source, payload)
        minified = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        def noncanonical_commit(path: Path, _content: str, **_kwargs) -> None:
            path.write_text(minified, encoding="utf-8")

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=noncanonical_commit,
            )
        except ValueError as exc:
            assert str(exc) == "archive_validation_failed"
        else:
            raise AssertionError("expected non-canonical final bytes to be rejected")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert target.read_text(encoding="utf-8") == minified


def test_existing_duplicate_requires_canonical_raw_bytes_and_preserves_final():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        _write_json(source, payload)
        target = history / "snapshot_20260703T113000Z-live.json"
        minified = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        target.parent.mkdir(parents=True)
        target.write_text(minified, encoding="utf-8")

        try:
            archive_snapshot(source=source, history=history)
        except ValueError as exc:
            assert str(exc) == "archive_conflict"
        else:
            raise AssertionError("expected non-canonical existing bytes not to duplicate")

        assert target.read_text(encoding="utf-8") == minified


def test_commit_error_after_final_write_propagates_and_preserves_final():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())
        private_message = "private-commit-failure"

        def failed_after_write(path: Path, content: str, **_kwargs) -> None:
            path.write_text(content, encoding="utf-8")
            raise OSError(private_message)

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=failed_after_write,
            )
        except OSError as exc:
            assert str(exc) == private_message
        else:
            raise AssertionError("expected commit error to propagate without success summary")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert target.read_text(encoding="utf-8") == _canonical_text(_snapshot())


def test_file_exists_identical_canonical_winner_returns_duplicate_and_is_preserved():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        def identical_winner(path: Path, content: str, **_kwargs) -> None:
            path.write_text(content, encoding="utf-8")
            raise FileExistsError("private-identical-winner-message")

        summary = archive_snapshot(
            source=source,
            history=history,
            commit_new=identical_winner,
        )

        target = history / "snapshot_20260703T113000Z-live.json"
        assert summary["status"] == "duplicate"
        assert "private-identical-winner-message" not in json.dumps(summary)
        assert target.read_text(encoding="utf-8") == _canonical_text(_snapshot())


def test_file_exists_noncanonical_semantic_winner_is_not_duplicate():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        payload = _snapshot()
        _write_json(source, payload)
        minified = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        def noncanonical_winner(path: Path, _content: str, **_kwargs) -> None:
            path.write_text(minified, encoding="utf-8")
            raise FileExistsError("private-noncanonical-winner-message")

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=noncanonical_winner,
            )
        except ValueError as exc:
            assert str(exc) == "archive_conflict"
        else:
            raise AssertionError("expected non-canonical winner not to duplicate")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert target.read_text(encoding="utf-8") == minified


def test_file_exists_conflicting_canonical_winner_is_not_overwritten():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        expected = _snapshot()
        conflicting = _snapshot()
        conflicting["matches"][0]["home_team"] = "Conflicting Home"
        _write_json(source, expected)
        conflicting_content = _canonical_text(conflicting)

        def conflicting_winner(path: Path, _content: str, **_kwargs) -> None:
            path.write_text(conflicting_content, encoding="utf-8")
            raise FileExistsError("private-conflict-winner-message")

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=conflicting_winner,
            )
        except ValueError as exc:
            assert str(exc) == "archive_conflict"
        else:
            raise AssertionError("expected conflicting winner to fail closed")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert target.read_text(encoding="utf-8") == conflicting_content


def test_mixed_time_snapshot_archives_future_row_without_observing_started_row():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        started = deepcopy(_snapshot()["matches"][0])
        started["kickoff_at_utc"] = "2026-07-03T11:00:00+00:00"
        future = deepcopy(_snapshot()["matches"][0])
        future.update(
            {
                "kickoff_at_utc": "2026-07-03T12:30:00+00:00",
                "home_team": "Shanghai Port",
                "away_team": "Shandong Taishan",
                "home_canonical": "shanghai_port",
                "away_canonical": "shandong_taishan",
            }
        )
        _write_json(source, _snapshot(matches=[started, future]))

        summary = archive_snapshot(source=source, history=history)
        archived = load_snapshot(summary["path"])

        assert summary["status"] == "created"
        assert summary["late_matches"] == 1
        assert archived["matches"] == [started, future]
        assert select_observed_closing_exact(
            [archived],
            competition_id="csl_2026",
            kickoff_at_utc=started["kickoff_at_utc"],
            home_canonical=started["home_canonical"],
            away_canonical=started["away_canonical"],
        ) is None
        assert select_observed_closing_exact(
            [archived],
            competition_id="csl_2026",
            kickoff_at_utc=future["kickoff_at_utc"],
            home_canonical=future["home_canonical"],
            away_canonical=future["away_canonical"],
        ) is not None


def test_interprocess_archive_race_never_overwrites_or_mixes_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        first_source = root / "first.json"
        second_source = root / "second.json"
        first = _snapshot()
        second = _snapshot()
        second["matches"][0].update(
            {
                "home_team": "Shanghai Port",
                "away_team": "Shandong Taishan",
                "home_canonical": "shanghai_port",
                "away_canonical": "shandong_taishan",
            }
        )
        _write_json(first_source, first)
        _write_json(second_source, second)

        context = multiprocessing.get_context("fork")
        start = context.Event()
        results = context.Queue()
        processes = [
            context.Process(
                target=_archive_race_worker,
                args=(source, history, start, results),
            )
            for source in (first_source, second_source)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=10)

        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        assert sorted(results.get(timeout=1) for _ in processes) == [
            "archive_conflict",
            "created",
        ]
        target = history / "snapshot_20260703T113000Z-live.json"
        stored = load_snapshot(target)
        assert stored == first or stored == second


def test_cli_archives_from_root_defaults_and_prints_safe_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        _write_json(source, _snapshot())
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = archive_main(["--root", str(root)])

        assert exit_code == 0
        summary = json.loads(stdout.getvalue())
        assert summary["status"] == "created"
        assert summary["matches"] == 1
        assert summary["path"].endswith(
            "data/local/diagnostics/csl_history/snapshot_20260703T113000Z-live.json"
        )
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("bookmaker", "must-not-leak", "odds", "api_key", "secret"):
            assert forbidden not in serialized


def test_cli_validation_error_is_stable_and_redacts_untrusted_row_value():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        private_marker = "private-row-competition-marker"
        payload = _snapshot()
        payload["matches"][0]["competition"] = private_marker
        _write_json(source, payload)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = archive_main(["--root", str(root)])

        assert exit_code == 2
        assert json.loads(stdout.getvalue()) == {
            "status": "error",
            "reason": "snapshot_archive_failed",
            "error_type": "ValueError",
        }
        assert private_marker not in stdout.getvalue()


def test_cli_io_error_is_stable_without_bottom_exception_message_or_traceback():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        source.mkdir(parents=True)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = archive_main(["--root", str(root)])

        assert exit_code == 2
        assert json.loads(stdout.getvalue()) == {
            "status": "error",
            "reason": "snapshot_archive_failed",
            "error_type": "IsADirectoryError",
        }
        assert "Is a directory" not in stdout.getvalue()
        assert "Traceback" not in stdout.getvalue()


def test_cli_archive_path_error_is_stable_without_commit_message_or_traceback():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        history = root / "data/local/diagnostics/csl_history"
        _write_json(source, _snapshot())
        history.write_text("private-history-file-marker", encoding="utf-8")
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = archive_main(["--root", str(root)])

        assert exit_code == 2
        assert json.loads(stdout.getvalue()) == {
            "status": "error",
            "reason": "snapshot_archive_failed",
            "error_type": "FileExistsError",
        }
        assert "File exists" not in stdout.getvalue()
        assert "private-history-file-marker" not in stdout.getvalue()
        assert "Traceback" not in stdout.getvalue()
