from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from worldcup.csl_postmatch_sentinel import (
    SentinelValidationError,
    evaluate_postmatch_sentinel,
    main,
    run_csl_postmatch_sentinel,
    validate_postmatch_inputs,
)


def _reports(
    *,
    decision_count: int = 38,
    missing_closing: int = 128,
    missing_decision: int = 8,
    finished_result_count: int | None = None,
    closing_available_count: int | None = None,
    generated_at: str = "2026-08-15T10:36:37Z",
    min_sample: int = 50,
):
    tally = {"hit": decision_count, "miss": 0, "push": 0, "no_pick": 0}
    sample = {
        "actionable": decision_count,
        "decided": decision_count,
        "decision_count": decision_count,
        "hit_rate": 1.0 if decision_count else None,
        "min_sample": min_sample,
        "pick_rate": 1.0 if decision_count else 0.0,
        "sample_too_small": decision_count < min_sample,
    }
    closing_available = (
        decision_count + missing_decision
        if closing_available_count is None
        else closing_available_count
    )
    finished_results = (
        closing_available + missing_closing
        if finished_result_count is None
        else finished_result_count
    )
    coverage_block = {
        "finished_result_count": finished_results,
        "closing_available_count": closing_available,
        "decision_available_count": decision_count,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "legacy_decision_count": 0,
        "missing_closing_count": missing_closing,
        "missing_decision_count": missing_decision,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    matches = [
        {
            "match_id": f"csl_2026:missing:{index}",
            "settlement": {"status": "missing_closing"},
        }
        for index in range(missing_closing)
    ] + [
        {
            "match_id": f"csl_2026:no-decision:{index}",
            "settlement": {"status": "missing_decision"},
        }
        for index in range(missing_decision)
    ]
    shadow = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "a" * 64,
        "status": "ok",
        "decision_sample": sample,
        "decision_tally": tally,
        "decision_coverage": coverage_block,
        "matches": matches,
    }
    coverage = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "b" * 64,
        "summary": {
            "finished_result_count": coverage_block["finished_result_count"],
            "missing_count": missing_closing,
            "observed_closing_count": coverage_block["closing_available_count"],
            "observed_current_decision_count": decision_count,
            "observed_missing_current_decision_count": missing_decision,
        },
        "performance": {
            "observed": {
                "decision_sample": deepcopy(sample),
                "decision_tally": deepcopy(tally),
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            }
        },
        "matches": [],
    }
    return shadow, coverage


def _write_reports(root: Path, **kwargs: object) -> None:
    shadow, coverage = _reports(**kwargs)
    diagnostics = root / "data/local/diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    (diagnostics / "csl_postmatch_shadow.json").write_text(
        json.dumps(shadow), encoding="utf-8"
    )
    (diagnostics / "csl_closing_coverage.json").write_text(
        json.dumps(coverage), encoding="utf-8"
    )


def _concurrent_runner(root_text: str, worker_id: int) -> None:
    root = Path(root_text)
    calls: list[str] = []
    result = run_csl_postmatch_sentinel(
        root=root,
        write=True,
        notify=True,
        observed_at="2026-08-20T01:00:00Z",
        notify_fn=lambda content, **_kwargs: calls.append(content)
        or {"status": "sent"},
    )
    (root / f"worker-{worker_id}.json").write_text(
        json.dumps({"result": result, "notification_count": len(calls)}),
        encoding="utf-8",
    )


def test_runner_dry_run_creates_no_state_lock_or_notification():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        before = sorted(
            path.relative_to(root) for path in root.rglob("*") if path.is_file()
        )
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            observed_at="2026-08-20T00:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True),
        )
        after = sorted(
            path.relative_to(root) for path in root.rglob("*") if path.is_file()
        )
    assert result["status"] == "dry_run_ready"
    assert result["event_count"] == 0
    assert result["decision_count"] == 38
    assert result["sample_too_small"] is True
    assert before == after
    assert calls == []


def test_runner_write_persists_initial_baseline_without_old_gap_event():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        state = json.loads(
            (root / "data/local/diagnostics/csl_postmatch_sentinel_state.json")
            .read_text(encoding="utf-8")
        )
    assert result["status"] == "stored"
    assert result["event_count"] == 0
    assert state["baseline_quality"]["missing_closing_count"] == 128
    assert state["baseline_quality"]["missing_decision_count"] == 8


def test_runner_suppresses_new_event_when_notify_false_and_never_backfills_it():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        suppressed = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T01:00:00Z",
        )
        calls = []
        repeated = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
    assert suppressed["notification_status"] == "suppressed"
    assert repeated["status"] == "unchanged"
    assert calls == []


def test_failed_notification_remains_pending_and_retries_on_unchanged_input():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        failed = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: {
                "status": "failed",
                "exit_code": 1,
            },
        )
        calls = []
        retried = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
    assert failed["notification_status"] == "failed"
    assert retried["notification_status"] == "sent"
    assert calls == [True]


def test_notify_requires_write_before_creating_lock():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            run_csl_postmatch_sentinel(root=root, write=False, notify=True)
        except ValueError as exc:
            assert str(exc) == "notify_requires_write"
        else:
            raise AssertionError("notify without write must fail closed")
        assert not (root / "data/local/diagnostics/csl_postmatch_sentinel.lock").exists()


def test_valid_state_tracks_input_error_once_and_notifies_recovery_once():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        shadow_path = root / "data/local/diagnostics/csl_postmatch_shadow.json"
        shadow_path.write_text("{broken", encoding="utf-8")
        calls = []
        broken = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda content, **kwargs: calls.append((content, kwargs))
            or {"status": "sent"},
        )
        repeated = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda content, **kwargs: calls.append((content, kwargs))
            or {"status": "sent"},
        )
        _write_reports(root)
        recovered = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T03:00:00Z",
            notify_fn=lambda content, **kwargs: calls.append((content, kwargs))
            or {"status": "sent"},
        )
        recovered_again = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T04:00:00Z",
            notify_fn=lambda content, **kwargs: calls.append((content, kwargs))
            or {"status": "sent"},
        )
    assert broken["event_count"] == 1
    assert repeated["status"] == "unchanged"
    assert recovered["event_count"] == 1
    assert recovered_again["status"] == "unchanged"
    assert len(calls) == 2
    assert all(kwargs == {"summary": "中超赛后数据监控提醒"} for _, kwargs in calls)


def test_recovery_of_suppressed_anomaly_is_also_suppressed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T01:00:00Z",
        )
        _write_reports(root, finished_result_count=175)
        calls = []
        recovery = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
    assert recovery["notification_status"] == "suppressed"
    assert calls == []


def test_corrupt_state_is_preserved_and_notification_is_not_attempted():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        corrupt = b'{"secret":"keep exactly",broken'
        state_path.write_bytes(corrupt)
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        preserved = state_path.read_bytes()
    assert result == {
        "status": "error",
        "reason": "sentinel_state_unreadable",
        "error_type": "SentinelValidationError",
        "event_count": 0,
        "notification_status": "not_attempted",
    }
    assert preserved == corrupt
    assert calls == []


def test_atomic_state_failure_preserves_previous_bytes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        previous = state_path.read_bytes()
        _write_reports(root, missing_closing=129)
        with patch(
            "worldcup.csl_postmatch_sentinel.os.replace",
            side_effect=OSError("injected replace failure"),
        ):
            result = run_csl_postmatch_sentinel(
                root=root,
                write=True,
                observed_at="2026-08-20T01:00:00Z",
            )
        preserved = state_path.read_bytes()
        temp_files = list(state_path.parent.glob("*.tmp"))
    assert result["status"] == "error"
    assert result["reason"] == "sentinel_state_write_failed"
    assert preserved == previous
    assert temp_files == []


def test_state_result_and_notification_redact_sensitive_input():
    sensitive_values = (
        "secret",
        "api_key",
        "bookmakers",
        "/Users/private/person/project/.env",
        "Traceback (most recent call last): private stack",
    )
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        shadow, _coverage = _reports()
        shadow["status"] = "broken"
        shadow["secret"] = "hidden"
        shadow["api_key"] = "private-key"
        shadow["bookmakers"] = [{"payload": "raw"}]
        shadow["private_path"] = sensitive_values[3]
        shadow["traceback"] = sensitive_values[4]
        shadow_path = root / "data/local/diagnostics/csl_postmatch_shadow.json"
        shadow_path.write_text(json.dumps(shadow), encoding="utf-8")
        notifications = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda content, **kwargs: notifications.append(
                {"content": content, "kwargs": kwargs}
            )
            or {"status": "sent"},
        )
        state = json.loads(
            (root / "data/local/diagnostics/csl_postmatch_sentinel_state.json")
            .read_text(encoding="utf-8")
        )
    scanned = json.dumps(
        {"result": result, "state": state, "notifications": notifications},
        ensure_ascii=False,
        sort_keys=True,
    )
    for sensitive in sensitive_values:
        assert sensitive not in scanned
    assert notifications[0]["content"].endswith("仅用于研究分析，不构成投注建议。")


def test_concurrent_runners_create_and_send_one_event():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        context = multiprocessing.get_context("fork")
        workers = [
            context.Process(target=_concurrent_runner, args=(str(root), worker_id))
            for worker_id in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            assert worker.exitcode == 0
        results = [
            json.loads((root / f"worker-{worker_id}.json").read_text(encoding="utf-8"))
            for worker_id in range(2)
        ]
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        temp_files = list(state_path.parent.glob("*.tmp"))
    assert sum(item["notification_count"] for item in results) == 1
    assert sum(item["result"]["event_count"] for item in results) == 1
    assert len(state["outbox"]) == 1
    assert len({item["event_id"] for item in state["outbox"]}) == 1
    assert temp_files == []


def test_cli_defaults_to_zero_write_dry_run():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        previous_cwd = Path.cwd()
        output = StringIO()
        try:
            os.chdir(root)
            with redirect_stdout(output):
                exit_code = main([])
        finally:
            os.chdir(previous_cwd)
        result = json.loads(output.getvalue())
    assert exit_code == 0
    assert result == {
        "status": "dry_run_ready",
        "event_count": 0,
        "notification_status": "not_attempted",
        "decision_count": 38,
        "sample_too_small": True,
    }
    assert not (root / "data/local/diagnostics/csl_postmatch_sentinel_state.json").exists()
    assert not (root / "data/local/diagnostics/csl_postmatch_sentinel.lock").exists()


def test_cli_notify_uses_injected_runner():
    calls = []

    def fake_runner(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        assert kwargs["write"] is True
        assert kwargs["notify"] is True
        return {
            "status": "stored",
            "event_count": 1,
            "notification_status": "sent",
        }

    output = StringIO()
    with redirect_stdout(output):
        exit_code = main(["--write", "--notify"], runner=fake_runner)
    assert exit_code == 0
    assert len(calls) == 1
    assert json.loads(output.getvalue()) == {
        "status": "stored",
        "event_count": 1,
        "notification_status": "sent",
    }


def test_state_with_unknown_sensitive_field_is_rejected_without_rewrite():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["secret"] = "must-not-be-normalized-away"
        corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
        state_path.write_bytes(corrupt)
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        preserved = state_path.read_bytes()
    assert result["reason"] == "sentinel_state_unreadable"
    assert preserved == corrupt
    assert calls == []


def test_notification_batch_has_at_most_five_detail_lines():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        shadow, coverage = _reports(missing_closing=129, missing_decision=9)
        for field in (
            "identity_mismatch_count",
            "invalid_decision_count",
            "result_source_blocked_count",
            "unresolved_count",
        ):
            shadow["decision_coverage"][field] = 1
        diagnostics = root / "data/local/diagnostics"
        (diagnostics / "csl_postmatch_shadow.json").write_text(
            json.dumps(shadow), encoding="utf-8"
        )
        (diagnostics / "csl_closing_coverage.json").write_text(
            json.dumps(coverage), encoding="utf-8"
        )
        notifications = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda content, **_kwargs: notifications.append(content)
            or {"status": "sent"},
        )
    lines = notifications[0].splitlines()
    assert result["event_count"] == 6
    assert lines[0] == "中超赛后数据监控"
    assert lines[-1] == "仅用于研究分析，不构成投注建议。"
    assert len(lines[1:-1]) <= 5


def test_repeated_anomaly_after_recovery_gets_new_lifecycle_event_id():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        calls = []
        _write_reports(root, missing_closing=129)
        first = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        first_state = json.loads(state_path.read_text(encoding="utf-8"))
        first_anomaly_id = first_state["outbox"][-1]["event_id"]

        _write_reports(root, finished_result_count=175)
        recovered = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )

        _write_reports(root, missing_closing=129, finished_result_count=176)
        repeated = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        repeated_anomaly_id = final_state["outbox"][-1]["event_id"]
    assert first["status"] == "stored"
    assert recovered["status"] == "stored"
    assert repeated["status"] == "stored"
    assert first_anomaly_id != repeated_anomaly_id
    assert len(final_state["outbox"]) == 3
    assert calls == [True, True, True]


def test_pending_retry_keeps_original_event_id_without_duplicate_record():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        failed = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: {"status": "failed"},
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        failed_state = json.loads(state_path.read_text(encoding="utf-8"))
        original_id = failed_state["outbox"][0]["event_id"]
        calls = []
        retried = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        retried_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert failed["notification_status"] == "failed"
    assert retried["event_count"] == 0
    assert retried["notification_status"] == "sent"
    assert len(retried_state["outbox"]) == 1
    assert retried_state["outbox"][0]["event_id"] == original_id
    assert calls == [True]


def test_directory_fsync_failure_cannot_report_failure_after_replace_commit():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        previous = state_path.read_bytes()
        _write_reports(root, missing_closing=129)
        calls = []

        def fail_second_fsync(_descriptor: int) -> None:
            calls.append(True)
            if len(calls) == 2:
                raise OSError("injected directory fsync failure")

        with patch("worldcup.csl_postmatch_sentinel.os.fsync", fail_second_fsync):
            result = run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=False,
                observed_at="2026-08-20T01:00:00Z",
            )
        committed = state_path.read_bytes()
    assert result["status"] == "stored"
    assert calls == [True]
    assert committed != previous
    assert json.loads(committed)["outbox"][0]["delivery_status"] == "suppressed"


def test_state_rejects_unknown_nested_fields_without_rewrite_or_notification():
    for block_name in ("baseline_quality", "high_water", "active"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_reports(root)
            run_csl_postmatch_sentinel(
                root=root,
                write=True,
                observed_at="2026-08-20T00:00:00Z",
            )
            if block_name == "active":
                _write_reports(root, missing_closing=129)
                run_csl_postmatch_sentinel(
                    root=root,
                    write=True,
                    notify=False,
                    observed_at="2026-08-20T01:00:00Z",
                )
            state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if block_name == "active":
                active = next(iter(state["active_conditions"].values()))
                active["unknown"] = 1
            else:
                state[block_name]["unknown"] = 1
            corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
            state_path.write_bytes(corrupt)
            calls = []
            result = run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=True,
                observed_at="2026-08-20T02:00:00Z",
                notify_fn=lambda *_args, **_kwargs: calls.append(True)
                or {"status": "sent"},
            )
            preserved = state_path.read_bytes()
        assert result["reason"] == "sentinel_state_unreadable"
        assert preserved == corrupt
        assert calls == []


def test_state_rejects_active_outbox_count_or_digest_mismatch():
    for field, replacement in (("current_count", 130), ("match_ids_digest", "f" * 64)):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_reports(root)
            run_csl_postmatch_sentinel(
                root=root,
                write=True,
                observed_at="2026-08-20T00:00:00Z",
            )
            _write_reports(root, missing_closing=129)
            run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=False,
                observed_at="2026-08-20T01:00:00Z",
            )
            state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            active = next(iter(state["active_conditions"].values()))
            active[field] = replacement
            corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
            state_path.write_bytes(corrupt)
            calls = []
            result = run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=True,
                observed_at="2026-08-20T02:00:00Z",
                notify_fn=lambda *_args, **_kwargs: calls.append(True)
                or {"status": "sent"},
            )
            preserved = state_path.read_bytes()
        assert result["reason"] == "sentinel_state_unreadable"
        assert preserved == corrupt
        assert calls == []


def test_state_rejects_missing_outbox_before_suppressed_anomaly_recovery():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            observed_at="2026-08-20T00:00:00Z",
        )
        _write_reports(root, missing_closing=129)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T01:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["active_conditions"]
        assert state["outbox"][0]["delivery_status"] == "suppressed"
        del state["outbox"]
        corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
        state_path.write_bytes(corrupt)

        _write_reports(root, finished_result_count=175)
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        preserved = state_path.read_bytes()
    assert result == {
        "status": "error",
        "reason": "sentinel_state_unreadable",
        "error_type": "SentinelValidationError",
        "event_count": 0,
        "notification_status": "not_attempted",
    }
    assert preserved == corrupt
    assert calls == []


def test_state_rejects_false_threshold_flag_after_threshold_was_recorded():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root, decision_count=50)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["threshold_notified"] is True
        assert len(state["outbox"]) == 1
        state["threshold_notified"] = False
        corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
        state_path.write_bytes(corrupt)

        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        preserved = state_path.read_bytes()
    assert result == {
        "status": "error",
        "reason": "sentinel_state_unreadable",
        "error_type": "SentinelValidationError",
        "event_count": 0,
        "notification_status": "not_attempted",
    }
    assert preserved == corrupt
    assert len(json.loads(preserved)["outbox"]) == 1
    assert calls == []


def test_state_rejects_true_threshold_flag_before_threshold_was_reached():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root, decision_count=38)
        run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["threshold_notified"] is False
        assert state["outbox"] == []
        state["threshold_notified"] = True
        corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
        state_path.write_bytes(corrupt)

        _write_reports(root, decision_count=50)
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            write=True,
            notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True)
            or {"status": "sent"},
        )
        preserved = state_path.read_bytes()
    assert result == {
        "status": "error",
        "reason": "sentinel_state_unreadable",
        "error_type": "SentinelValidationError",
        "event_count": 0,
        "notification_status": "not_attempted",
    }
    assert preserved == corrupt
    assert json.loads(preserved)["outbox"] == []
    assert calls == []


def test_state_rejects_threshold_outbox_history_mismatch():
    with TemporaryDirectory() as donor_tmp:
        donor_root = Path(donor_tmp)
        _write_reports(donor_root, decision_count=50)
        run_csl_postmatch_sentinel(
            root=donor_root,
            write=True,
            notify=False,
            observed_at="2026-08-20T00:00:00Z",
        )
        donor_state_path = (
            donor_root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
        )
        threshold_record = json.loads(
            donor_state_path.read_text(encoding="utf-8")
        )["outbox"][0]

    for decision_count, mutation in ((50, "remove"), (38, "add")):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_reports(root, decision_count=decision_count)
            run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=False,
                observed_at="2026-08-20T00:00:00Z",
            )
            state_path = root / "data/local/diagnostics/csl_postmatch_sentinel_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if mutation == "remove":
                assert state["threshold_notified"] is True
                state["outbox"] = []
            else:
                assert state["threshold_notified"] is False
                state["outbox"].append(threshold_record)
            corrupt = json.dumps(state, sort_keys=True).encode("utf-8")
            state_path.write_bytes(corrupt)
            calls = []
            result = run_csl_postmatch_sentinel(
                root=root,
                write=True,
                notify=True,
                observed_at="2026-08-20T01:00:00Z",
                notify_fn=lambda *_args, **_kwargs: calls.append(True)
                or {"status": "sent"},
            )
            preserved = state_path.read_bytes()
        assert result["reason"] == "sentinel_state_unreadable"
        assert preserved == corrupt
        assert calls == []


def test_validate_inputs_rejects_cross_report_mismatch():
    shadow, coverage = _reports()
    coverage["summary"]["observed_current_decision_count"] = 37
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "coverage_shadow_mismatch"
    else:
        raise AssertionError("mismatched reports must fail closed")


def test_validate_inputs_rejects_min_sample_below_fixed_threshold():
    shadow, coverage = _reports(decision_count=38, min_sample=1)
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "shadow_report_invalid"
    else:
        raise AssertionError("min_sample below 50 must fail closed")


def test_validate_inputs_rejects_min_sample_above_fixed_threshold():
    shadow, coverage = _reports(decision_count=50, min_sample=51)
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "shadow_report_invalid"
    else:
        raise AssertionError("min_sample above 50 must fail closed")


def test_first_evaluation_baselines_existing_128_and_8_without_alerting():
    shadow, coverage = _reports()
    result = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )
    assert result["events"] == []
    assert result["state"]["baseline_quality"] == {
        "missing_closing_count": 128,
        "missing_decision_count": 8,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    assert result["state"]["high_water"]["decision_count"] == 38


def test_new_gap_is_alerted_once_then_expansion_and_recovery_are_distinct():
    shadow, coverage = _reports()
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]

    expanded_shadow, expanded_coverage = _reports(
        missing_closing=129,
        finished_result_count=175,
        closing_available_count=46,
    )
    first = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow,
        coverage_report=expanded_coverage,
        previous_state=baseline,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in first["events"]] == [
        "missing_closing_increased"
    ]

    unchanged = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow,
        coverage_report=expanded_coverage,
        previous_state=first["state"],
        observed_at="2026-08-20T02:00:00Z",
    )
    assert unchanged["events"] == []

    wider_shadow, wider_coverage = _reports(
        missing_closing=130,
        finished_result_count=176,
        closing_available_count=46,
    )
    wider = evaluate_postmatch_sentinel(
        shadow_report=wider_shadow,
        coverage_report=wider_coverage,
        previous_state=unchanged["state"],
        observed_at="2026-08-20T03:00:00Z",
    )
    assert [event["kind"] for event in wider["events"]] == ["anomaly"]

    recovered_shadow, recovered_coverage = _reports(
        decision_count=40,
        missing_closing=128,
        missing_decision=8,
        finished_result_count=176,
        closing_available_count=48,
    )
    recovered = evaluate_postmatch_sentinel(
        shadow_report=recovered_shadow,
        coverage_report=recovered_coverage,
        previous_state=wider["state"],
        observed_at="2026-08-20T04:00:00Z",
    )
    assert [event["kind"] for event in recovered["events"]] == ["recovery"]


def test_count_regression_keeps_high_water_until_recovery():
    shadow, coverage = _reports(decision_count=38)
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]
    lower_shadow, lower_coverage = _reports(decision_count=37)
    lower = evaluate_postmatch_sentinel(
        shadow_report=lower_shadow,
        coverage_report=lower_coverage,
        previous_state=baseline,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert lower["state"]["high_water"]["decision_count"] == 38
    assert "decision_count_regressed" in {event["code"] for event in lower["events"]}


def test_threshold_crosses_once_and_hit_rate_change_does_not_alert():
    before_shadow, before_coverage = _reports(decision_count=49)
    state = evaluate_postmatch_sentinel(
        shadow_report=before_shadow,
        coverage_report=before_coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]
    at_shadow, at_coverage = _reports(decision_count=50)
    crossed = evaluate_postmatch_sentinel(
        shadow_report=at_shadow,
        coverage_report=at_coverage,
        previous_state=state,
        observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in crossed["events"]] == [
        "decision_sample_reached_minimum"
    ]
    changed = deepcopy(at_shadow)
    changed["decision_tally"] = {"hit": 25, "miss": 25, "push": 0, "no_pick": 0}
    changed["decision_sample"].update({"hit_rate": 0.5, "decided": 50})
    changed_coverage = deepcopy(at_coverage)
    changed_coverage["performance"]["observed"]["decision_tally"] = deepcopy(
        changed["decision_tally"]
    )
    changed_coverage["performance"]["observed"]["decision_sample"] = deepcopy(
        changed["decision_sample"]
    )
    second = evaluate_postmatch_sentinel(
        shadow_report=changed,
        coverage_report=changed_coverage,
        previous_state=crossed["state"],
        observed_at="2026-08-20T02:00:00Z",
    )
    assert second["events"] == []
