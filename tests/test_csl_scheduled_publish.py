import json
from pathlib import Path
from tempfile import TemporaryDirectory
import urllib.error

from worldcup.csl_scheduled_publish import (
    _quota_remaining,
    _runner_diagnostic,
    _safe_archive_summary,
    build_csl_publish_decision,
    run_csl_scheduled_publish,
)

COVERAGE_FINGERPRINT = "a" * 64
RETRY_COVERAGE_FINGERPRINT = "b" * 64
BLOCKED_RETRY_COVERAGE_FINGERPRINT = "c" * 64


def test_quota_remaining_prefers_fresh_tertiary_over_low_secondary():
    with TemporaryDirectory() as tmp:
        quota_path = Path(tmp) / "quota.json"
        _write_json(
            quota_path,
            {
                "providers": {
                    "theoddsapi_primary": {"remaining": 0},
                    "theoddsapi_secondary": {"remaining": 26},
                    "theoddsapi_tertiary": {"remaining": 497},
                }
            },
        )

        assert _quota_remaining(quota_path) == 497


def _snapshot(kickoffs, *, observed_at="2026-07-09T10:00:00+00:00"):
    return {
        "snapshot_at": observed_at,
        "run": {"run_id": "previous-csl-live", "observed_at": observed_at},
        "competition": {"id": "csl_2026", "name": "中超 2026"},
        "counts": {"matches": len(kickoffs)},
        "matches": [
            {
                "source_event_id": f"event-{index}",
                "kickoff_at_utc": kickoff,
                "home_team": f"Home {index}",
                "away_team": f"Away {index}",
                "home_canonical": "yunnan_yukun",
                "away_canonical": "henan_fc",
                "competition": {"id": "csl_2026", "name": "中超 2026"},
            }
            for index, kickoff in enumerate(kickoffs, start=1)
        ],
    }


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _coverage_result(*, status="unchanged", fingerprint=COVERAGE_FINGERPRINT):
    return {
        "status": status,
        "competition_id": "csl_2026",
        "season": "2026",
        "input_fingerprint": fingerprint,
        "finished_result_count": 136,
        "observed_closing_count": 43,
        "observed_current_decision_count": 35,
        "missing_count": 93,
        "sample_too_small": True,
    }


def _dry_run_with_archive_history(payload):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        history_path = root / "csl_history"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-10T12:00:00+00:00"],
                observed_at="2026-07-10T09:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )
        _write_json(history_path / "snapshot_bad.json", payload)
        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=False,
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
        )
    return result


def _assert_archive_history_unknown(result):
    assert result["status"] == "dry_run"
    assert result["decision"]["closing_coverage_candidates"] == []
    assert result["decision"]["closing_coverage_quality"] == {
        "history_status": "unreadable",
        "warning": "coverage_history_unreadable",
    }
    assert "private-history-marker" not in json.dumps(result)


def _run_invalid_kickoff_archive_failure(*, archive_fn, publish_fn):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        coverage_calls = 0
        publish_calls = 0
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )

        def fake_coverage(**_kwargs):
            nonlocal coverage_calls
            coverage_calls += 1
            return _coverage_result()

        def fake_builder(_cache_dir, competition_id, snapshot_at):
            built = _snapshot(
                ["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at
            )
            built["matches"][0]["kickoff_at_utc"] = "not-a-time"
            return built

        def tracked_publish(**kwargs):
            nonlocal publish_calls
            publish_calls += 1
            return publish_fn(**kwargs)

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=lambda **_kwargs: {"status": "unchanged"},
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=fake_builder,
            archive_fn=archive_fn,
            publish_fn=tracked_publish,
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))
        pending_exists = (
            root / "csl_publish_snapshot.publish_pending.json"
        ).exists()
    return result, written, coverage_calls, publish_calls, pending_exists


class _ExplodingArchiveDict(dict):
    def get(self, *_args, **_kwargs):
        raise RuntimeError("private exploding get")


class _ExplodingArchiveValue:
    def __str__(self):
        raise RuntimeError("private exploding str")


def test_archive_projection_is_total_allowlisted_and_redacts_private_values():
    invalid = {
        "status": "error",
        "reason": "invalid_snapshot_archive_result",
        "error_type": "ValueError",
    }
    cases = (
        {"status": "created", "reason": "private arbitrary reason"},
        {
            "status": "error",
            "reason": "snapshot_archive_failed",
            "error_type": "OSError",
            "path": "/Users/private/secret/archive.json",
        },
        _ExplodingArchiveDict(status="created"),
        {"status": _ExplodingArchiveValue(), "path": _ExplodingArchiveValue()},
    )

    projected = [_safe_archive_summary(value) for value in cases]

    assert projected[0] == invalid
    assert projected[1] == {
        "status": "error",
        "reason": "snapshot_archive_failed",
        "error_type": "OSError",
    }
    assert projected[2:] == [invalid, invalid]
    serialized = json.dumps(projected)
    for private in (
        "private arbitrary reason",
        "/Users/private/secret/archive.json",
        "private exploding get",
        "private exploding str",
    ):
        assert private not in serialized


def test_invalid_archive_result_does_not_block_publish_or_leak_locally_or_publicly():
    private = "/Users/private/secret/archive.json"

    result, written, coverage_calls, publish_calls, pending_exists = (
        _run_invalid_kickoff_archive_failure(
            archive_fn=lambda **_kwargs: {
                "status": "created",
                "reason": "private arbitrary reason",
                "error_type": _ExplodingArchiveValue(),
                "path": private,
            },
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
        )
    )

    assert result["status"] == "published", result
    assert result["archive"] == {
        "status": "error",
        "reason": "invalid_snapshot_archive_result",
        "error_type": "ValueError",
    }
    assert coverage_calls == 1
    assert publish_calls == 1
    assert pending_exists is False
    serialized = json.dumps({"result": result, "written": written})
    assert private not in serialized
    assert "private arbitrary reason" not in serialized


def test_t90_anchor_due_triggers_competition_refresh():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-10T10:30:00+00:00",
    )

    assert decision["should_refresh"] is True
    assert decision["reason"] == "match_anchor_due"
    assert decision["due_matches"][0]["anchor"] == "T-90"
    assert decision["due_matches"][0]["match_label"] == "Home 1 vs Away 1"


def test_closing_candidate_annotation_does_not_override_quota_authority():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=0,
        now="2026-07-10T10:30:00+00:00",
        archived_snapshots=[],
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "quota_exhausted"
    assert decision["closing_coverage_candidates"][0]["issue_code"] == (
        "closing_archive_missing"
    )


def test_archived_due_match_is_not_annotated_as_missing():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )
    archived = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T10:20:00+00:00",
    )

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-10T10:30:00+00:00",
        archived_snapshots=[archived],
    )

    assert decision["should_refresh"] is True
    assert decision["closing_coverage_candidates"] == []


def test_unreadable_archive_history_never_implies_missing_coverage():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-10T10:30:00+00:00",
        archived_snapshots=[],
        archive_history_status="unreadable",
    )

    assert decision["should_refresh"] is True
    assert decision["closing_coverage_candidates"] == []
    assert decision["closing_coverage_quality"] == {
        "history_status": "unreadable",
        "warning": "coverage_history_unreadable",
    }


def test_postponed_match_never_triggers_anchor_or_pick_expiry_refresh():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )
    snapshot["matches"][0]["fixture_status"] = "POSTPONED"
    snapshot["matches"][0]["match_decision"] = {
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "valid_until": "2026-07-10T12:00:00+00:00",
    }

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-10T10:30:00+00:00",
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "discovery_not_due"
    assert decision["due_matches"] == []


def test_runner_diagnostic_counts_postponed_separately_from_no_pick():
    snapshot = _snapshot(["2026-07-10T12:00:00+00:00"])
    snapshot["matches"][0]["fixture_status"] = "POSTPONED"
    snapshot["matches"][0]["match_decision"] = {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "NO_CLEAN_MARKET",
    }

    diagnostic = _runner_diagnostic(snapshot)

    assert diagnostic["postponed"] == 1
    assert diagnostic["no_pick"] == 0
    assert diagnostic["missing_decisions"] == 0


def test_pick_expiry_guard_refreshes_before_future_csl_picks_expire():
    snapshot = _snapshot(
        ["2026-07-11T12:00:00+00:00"],
        observed_at="2026-07-10T08:34:00+00:00",
    )
    snapshot["matches"][0]["match_decision"] = {
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "valid_until": "2026-07-10T12:04:00+00:00",
    }

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=176,
        now="2026-07-10T11:44:00+00:00",
    )

    assert decision["should_refresh"] is True
    assert decision["reason"] == "pick_expiry_due"
    assert decision["next_due_at"] == "2026-07-10T11:44:00+00:00"


def test_low_quota_does_not_refresh_only_for_csl_pick_expiry():
    snapshot = _snapshot(
        ["2026-07-11T12:00:00+00:00"],
        observed_at="2026-07-10T08:34:00+00:00",
    )
    snapshot["matches"][0]["match_decision"] = {
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "valid_until": "2026-07-10T12:04:00+00:00",
    }

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=30,
        now="2026-07-10T11:44:00+00:00",
        archived_snapshots=[],
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "not_due"
    assert decision["next_due_at"] == "2026-07-11T11:35:00+00:00"
    assert decision["closing_coverage_candidates"] == []


def test_low_quota_skips_t90_but_allows_t25():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T09:00:00+00:00",
    )

    t90 = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=29,
        now="2026-07-10T10:30:00+00:00",
    )
    t25 = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=29,
        now="2026-07-10T11:35:00+00:00",
    )

    assert t90["should_refresh"] is False
    assert t90["reason"] == "not_due"
    assert t25["should_refresh"] is True
    assert t25["due_matches"][0]["anchor"] == "T-25"


def test_global_throttle_skips_duplicate_refresh_inside_min_interval():
    snapshot = _snapshot(
        ["2026-07-10T12:00:00+00:00"],
        observed_at="2026-07-10T11:20:00+00:00",
    )

    decision = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-10T11:35:00+00:00",
        min_interval_seconds=30 * 60,
        archived_snapshots=[],
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "global_throttle"
    assert decision["throttle_remaining_seconds"] == 900
    assert decision["closing_coverage_candidates"][0]["issue_code"] == (
        "closing_archive_missing"
    )


def test_no_future_matches_uses_discovery_interval():
    snapshot = _snapshot(
        ["2026-07-08T12:00:00+00:00"],
        observed_at="2026-07-08T09:00:00+00:00",
    )

    stale = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-09T10:00:00+00:00",
    )
    fresh = build_csl_publish_decision(
        snapshot=snapshot,
        quota_remaining=200,
        now="2026-07-08T20:00:00+00:00",
    )

    assert stale["should_refresh"] is True
    assert stale["reason"] == "discovery_due"
    assert fresh["should_refresh"] is False
    assert fresh["reason"] == "discovery_not_due"


def test_dry_run_does_not_read_env_or_call_live_side_effects():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-10T12:00:00+00:00"],
                observed_at="2026-07-10T09:00:00+00:00",
            ),
        )
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def forbidden(*args, **kwargs):
            raise AssertionError("side effect should not be called")

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=False,
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            load_env=forbidden,
            closing_coverage_fn=forbidden,
            refresh_fn=forbidden,
            publish_fn=forbidden,
        )

    assert result["status"] == "dry_run"
    assert result["decision"]["should_refresh"] is True


def test_dry_run_reports_unreadable_history_without_leaking_corrupt_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        history_path = root / "csl_history"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-10T12:00:00+00:00"],
                observed_at="2026-07-10T09:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )
        history_path.mkdir()
        (history_path / "snapshot_corrupt.json").write_text(
            "private-corrupt-history{", encoding="utf-8"
        )

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=False,
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
        )

    assert result["decision"]["closing_coverage_candidates"] == []
    assert result["decision"]["closing_coverage_quality"] == {
        "history_status": "unreadable",
        "warning": "coverage_history_unreadable",
    }
    serialized = json.dumps(result)
    assert "private-corrupt-history" not in serialized
    assert str(history_path) not in serialized


def test_empty_object_archive_history_is_unknown_instead_of_missing():
    _assert_archive_history_unknown(_dry_run_with_archive_history({}))


def test_list_archive_history_is_unknown_instead_of_raising():
    _assert_archive_history_unknown(
        _dry_run_with_archive_history(["private-history-marker"])
    )


def test_invalid_snapshot_time_makes_entire_archive_history_unknown():
    archived = _snapshot(["2026-07-10T12:00:00+00:00"])
    archived["snapshot_at"] = "private-history-marker"
    _assert_archive_history_unknown(_dry_run_with_archive_history(archived))


def test_malformed_history_match_makes_entire_archive_history_unknown():
    archived = _snapshot(["2026-07-10T12:00:00+00:00"])
    archived["matches"] = ["private-history-marker"]
    _assert_archive_history_unknown(_dry_run_with_archive_history(archived))


def test_live_not_due_reconciles_local_pending_without_provider_calls():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-11T12:00:00+00:00"],
                observed_at="2026-07-10T10:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )
        calls = {"coverage": 0}

        def fake_coverage(**kwargs):
            calls["coverage"] += 1
            assert kwargs["write"] is True
            return _coverage_result(status="unchanged") | {
                "finished_result_count": 171,
                "missing_count": 128,
            }

        def forbidden(**_kwargs):
            raise AssertionError("not-due recovery must not call provider or publish")

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:10:00+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
            load_env=lambda _path: (_ for _ in ()).throw(
                AssertionError("non-due coverage recovery must not read .env")
            ),
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            results_refresh_fn=forbidden,
            postmatch_shadow_fn=forbidden,
            refresh_fn=forbidden,
            snapshot_builder=forbidden,
            archive_fn=forbidden,
            publish_fn=forbidden,
        )

    assert result["status"] == "skipped"
    assert result["closing_coverage"]["status"] == "unchanged"
    assert calls == {"coverage": 1}


def test_non_due_exploding_coverage_text_fails_closed_before_env():
    class ExplodingText(str):
        def __str__(self):
            raise RuntimeError("private coverage marker")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-11T12:00:00+00:00"],
                observed_at="2026-07-10T10:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )
        result = run_csl_scheduled_publish(
            now="2026-07-10T10:10:00+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
            load_env=lambda _path: (_ for _ in ()).throw(
                AssertionError("invalid local coverage must still return before env")
            ),
            closing_coverage_fn=lambda **_kwargs: {
                "status": ExplodingText("stored")
            },
            closing_coverage_root=root,
        )

    assert result["status"] == "skipped"
    assert result["closing_coverage"] == {
        "status": "error",
        "reason": "invalid_closing_coverage_result",
        "error_type": "ValueError",
    }
    assert "private coverage marker" not in json.dumps(result)


def test_non_due_invalid_coverage_fields_fail_closed_without_coercion():
    invalid_results = [
        {"status": "unexpected_status"},
        {"status": "stored", "reason": "private arbitrary string"},
        {"status": "stored", "input_fingerprint": "not-a-sha256"},
        {"status": "stored", "missing_count": -1},
        {"status": "stored", "sample_too_small": 1},
        {"status": "stored", "competition_id": 2026},
        {
            "status": "error",
            "reason": "coverage_inputs_unavailable",
            "error_type": "私密标记",
        },
    ]
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-11T12:00:00+00:00"],
                observed_at="2026-07-10T10:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )
        for invalid in invalid_results:
            result = run_csl_scheduled_publish(
                now="2026-07-10T10:10:00+00:00",
                live=True,
                cache_dir=root,
                quota_path=quota_path,
                snapshot_path=snapshot_path,
                diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
                load_env=lambda _path: (_ for _ in ()).throw(
                    AssertionError("invalid local coverage must not read env")
                ),
                closing_coverage_fn=lambda **_kwargs: invalid,
                closing_coverage_root=root,
            )
            assert result["status"] == "skipped"
            assert result["closing_coverage"] == {
                "status": "error",
                "reason": "invalid_closing_coverage_result",
                "error_type": "ValueError",
            }
            assert "private arbitrary string" not in json.dumps(result)


def test_due_exploding_coverage_mapping_does_not_block_publication():
    class ExplodingCoverage(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("private mapping marker")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        publish_calls = 0
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )

        def fake_publish(**_kwargs):
            nonlocal publish_calls
            publish_calls += 1
            return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=lambda **_kwargs: ExplodingCoverage(
                status="stored"
            ),
            closing_coverage_root=root,
            postmatch_shadow_fn=lambda **_kwargs: {"status": "unchanged"},
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=lambda _cache_dir, competition_id, snapshot_at: _snapshot(
                ["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at
            ),
            archive_fn=lambda **_kwargs: {"status": "duplicate"},
            publish_fn=fake_publish,
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert result["status"] == "published"
    assert result["closing_coverage"] == {
        "status": "error",
        "reason": "invalid_closing_coverage_result",
        "error_type": "ValueError",
    }
    assert publish_calls == 1
    assert "private mapping marker" not in json.dumps(result)
    assert "private mapping marker" not in json.dumps(written)


def test_live_quota_exhausted_deduplicates_due_candidate_quota_events():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        audit_events = []
        _write_json(
            snapshot_path,
            _snapshot(
                [
                    "2026-07-10T12:00:00+00:00",
                    "2026-07-10T12:00:00+00:00",
                ],
                observed_at="2026-07-10T09:00:00+00:00",
            ),
        )
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 0}}},
        )

        def fake_coverage(**kwargs):
            audit_events.extend(kwargs["audit_events"])
            return _coverage_result()

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
            load_env=lambda _path: (_ for _ in ()).throw(
                AssertionError("quota-blocked recovery must not read .env")
            ),
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
        )

    assert result["status"] == "skipped"
    assert audit_events == [
        {
            "observed_at": "2026-07-10T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-07-10T12:00:00+00:00",
            "home_canonical": "yunnan_yukun",
            "away_canonical": "henan_fc",
            "issue_code": "quota_blocked",
        }
    ]


def test_live_force_refreshes_builds_snapshot_and_publishes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        runner_diagnostics_path = root / "csl_live_league_runner_check.json"
        quota_path = root / "quota.json"
        calls = {
            "results": 0,
            "coverage": 0,
            "shadow": 0,
            "refresh": 0,
            "publish": 0,
        }
        events = []

        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def fake_load_env(path):
            return {
                "THE_ODDS_API_KEY_SECONDARY": "test-key",
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!",
            }

        def fake_refresh(**kwargs):
            calls["refresh"] += 1
            events.append("odds")
            assert kwargs["live"] is True
            assert kwargs["competition_id"] == "csl_2026"
            return {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197, "used": 303, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            }

        def fake_results_refresh(**kwargs):
            calls["results"] += 1
            events.append("results")
            assert kwargs["live"] is True
            assert kwargs["write"] is True
            return {
                "status": "updated",
                "verified_current_season_matches": 136,
                "total_matches": 856,
                "latest_result_date": "2026-07-05",
            }

        def fake_coverage(**kwargs):
            calls["coverage"] += 1
            events.append("coverage")
            assert kwargs["write"] is True
            assert Path(kwargs["root"]) == root
            assert Path(kwargs["history"]) == root / "csl_history"
            assert Path(kwargs["results_path"]) == root / "club_results_csl_2026.csv"
            assert kwargs["generated_at"] == "2026-07-10T10:30:00+00:00"
            return _coverage_result(status="stored")

        def fake_shadow(**kwargs):
            calls["shadow"] += 1
            events.append("shadow")
            assert kwargs["write"] is True
            assert kwargs["competition_id"] == "csl_2026"
            assert kwargs["season"] == "2026"
            assert kwargs["source_status"] == "updated"
            assert Path(kwargs["history"]) == root / "csl_history"
            assert Path(kwargs["results"]) == root / "club_results_csl_2026.csv"
            return {
                "status": "stored",
                "competition_id": "csl_2026",
                "results": 136,
                "closing_available": 35,
                "decided": 35,
                "sample_too_small": True,
                "input_fingerprint_prefix": "123456789abc",
            }

        def fake_builder(cache_dir, competition_id, snapshot_at):
            assert competition_id == "csl_2026"
            return _snapshot(["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at)

        def fake_publish(**kwargs):
            calls["publish"] += 1
            assert kwargs["secret"] == "test-secret-long-enough-for-validation!!"
            assert kwargs["live"] is True
            assert Path(kwargs["snapshot_path"]) == snapshot_path
            return {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
                "request": {
                    "run_id": "20260710T103000Z-csl-live",
                    "snapshot_id": "snapshot-id",
                },
            }

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            runner_diagnostics_path=runner_diagnostics_path,
            load_env=fake_load_env,
            results_refresh_fn=fake_results_refresh,
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=fake_shadow,
            postmatch_shadow_root=root,
            refresh_fn=fake_refresh,
            snapshot_builder=fake_builder,
            publish_fn=fake_publish,
        )

        written = json.loads(snapshot_path.read_text(encoding="utf-8"))
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        runner_diagnostics = json.loads(runner_diagnostics_path.read_text(encoding="utf-8"))
        archived = list((root / "csl_history").glob("snapshot_*-live.json"))

    assert result["status"] == "published"
    assert calls == {
        "results": 1,
        "coverage": 1,
        "shadow": 1,
        "refresh": 1,
        "publish": 1,
    }
    assert events[:4] == ["results", "coverage", "shadow", "odds"]
    assert result["results_refresh"]["status"] == "updated"
    assert result["closing_coverage"]["input_fingerprint"] == COVERAGE_FINGERPRINT
    assert result["postmatch_shadow"]["status"] == "stored"
    assert result["archive"]["status"] == "created"
    assert len(archived) == 1
    assert written["run"]["run_id"] == "20260710T103000Z-csl-live"
    assert "closing_coverage_candidates" not in written["run"]["policy"]
    assert "closing_coverage_quality" not in written["run"]["policy"]
    assert "closing_coverage_candidates" not in json.dumps(written)
    assert "closing_coverage_quality" not in json.dumps(written)
    assert "csl_postmatch_shadow" not in written["data_quality"]
    assert diagnostics["run"]["run_id"] == "20260710T103000Z-csl-live"
    assert runner_diagnostics["match_picks"] == 0
    assert runner_diagnostics["missing_decisions"] == 1


def test_coverage_failure_is_local_and_does_not_block_or_leak_to_public_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        published = []
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 200}}},
        )

        def broken_coverage(**_kwargs):
            raise RuntimeError("private odds secret")

        def fake_publish(**kwargs):
            published.append(
                json.loads(Path(kwargs["snapshot_path"]).read_text(encoding="utf-8"))
            )
            return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=broken_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=lambda **_kwargs: {"status": "unchanged"},
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197, "used": 303, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=lambda _cache_dir, competition_id, snapshot_at: _snapshot(
                ["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at
            ),
            archive_fn=lambda **_kwargs: {"status": "duplicate"},
            publish_fn=fake_publish,
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))

    expected = {
        "status": "error",
        "reason": "csl_closing_coverage_failed",
        "error_type": "RuntimeError",
    }
    assert result["status"] == "published"
    assert result["closing_coverage"] == expected
    assert published == [written]
    assert "csl_closing_coverage_failed" not in json.dumps(written)
    assert "private odds secret" not in json.dumps(result)
    assert "private odds secret" not in json.dumps(written)


def test_archive_validation_result_with_invalid_kickoff_still_publishes():
    result, written, coverage_calls, publish_calls, pending_exists = (
        _run_invalid_kickoff_archive_failure(
            archive_fn=lambda **_kwargs: {
                "status": "error",
                "reason": "archive_validation_failed",
                "error_type": "ValueError",
            },
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
        )
    )

    assert result["status"] == "published"
    assert result["archive"] == {
        "status": "error",
        "reason": "archive_validation_failed",
        "error_type": "ValueError",
    }
    assert result["closing_coverage"] == {
        "status": "error",
        "reason": "csl_closing_coverage_failed",
        "error_type": "ValueError",
    }
    assert coverage_calls == 1
    assert publish_calls == 1
    assert pending_exists is False
    assert "csl_closing_coverage_failed" not in json.dumps(written)


def test_thrown_archive_validation_with_invalid_kickoff_keeps_publish_pending():
    def broken_archive(**_kwargs):
        raise ValueError("private archive validation marker")

    def unavailable_publish(**_kwargs):
        raise urllib.error.URLError("temporary publish failure")

    result, written, coverage_calls, publish_calls, pending_exists = (
        _run_invalid_kickoff_archive_failure(
            archive_fn=broken_archive,
            publish_fn=unavailable_publish,
        )
    )

    assert result["status"] == "publish_pending"
    assert result["archive"] == {
        "status": "error",
        "reason": "archive_validation_failed",
        "error_type": "ValueError",
    }
    assert result["closing_coverage"] == {
        "status": "error",
        "reason": "csl_closing_coverage_failed",
        "error_type": "ValueError",
    }
    assert result["pending"]["status"] == "pending"
    assert coverage_calls == 1
    assert publish_calls == 1
    assert pending_exists is True
    assert "private archive validation marker" not in json.dumps(result)
    assert "private archive validation marker" not in json.dumps(written)
    assert "csl_closing_coverage_failed" not in json.dumps(written)


def test_archive_failure_warns_but_does_not_block_current_publish():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        coverage_calls = []
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def fake_builder(cache_dir, competition_id, snapshot_at):
            return _snapshot(["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at)

        def broken_archive(**_kwargs):
            raise OSError("disk temporarily unavailable")

        def fake_coverage(**kwargs):
            coverage_calls.append(kwargs.get("audit_events", []))
            return _coverage_result()

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {"INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"},
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=lambda **_kwargs: {
                "status": "unchanged",
                "competition_id": "csl_2026",
            },
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197, "used": 303, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=fake_builder,
            archive_fn=broken_archive,
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert result["status"] == "published"
    assert result["archive"] == {
        "status": "error",
        "reason": "snapshot_archive_failed",
        "error_type": "OSError",
    }
    assert coverage_calls[0] == []
    assert coverage_calls[1] == [
        {
            "observed_at": "2026-07-10T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-07-10T12:00:00+00:00",
            "home_canonical": "yunnan_yukun",
            "away_canonical": "henan_fc",
            "issue_code": "snapshot_archive_failed",
        }
    ]
    assert "snapshot_archive_failed" in written["data_quality"]["warnings"]


def test_results_failure_marks_cached_fixture_status_stale_without_blocking_odds_publish():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        coverage_calls = 0
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def forbidden_shadow(**_kwargs):
            raise AssertionError("blocked result source must not run postmatch shadow")

        def fake_coverage(**_kwargs):
            nonlocal coverage_calls
            coverage_calls += 1
            return _coverage_result()

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {"INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"},
            results_refresh_fn=lambda **_kwargs: {
                "status": "error",
                "reason": "results_refresh_failed_using_existing_cache",
            },
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=forbidden_shadow,
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197, "used": 303, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=lambda _cache_dir, competition_id, snapshot_at: _snapshot(
                ["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at
            ),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert result["status"] == "published"
    assert result["postmatch_shadow"] == {
        "status": "blocked",
        "reason": "result_source_not_accepted",
    }
    assert coverage_calls == 1
    assert "club_results_refresh_failed" in written["data_quality"]["warnings"]
    assert written["data_quality"]["stale_sources"] == [
        "csl_results",
        "csl_fixture_status",
    ]


def test_shadow_failure_warns_without_blocking_odds_publish_or_leaking_message():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        calls = {"refresh": 0, "publish": 0}
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def broken_shadow(**_kwargs):
            raise RuntimeError("private shadow path and payload must not leak")

        def fake_refresh(**_kwargs):
            calls["refresh"] += 1
            return {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 197, "used": 303, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            }

        def fake_publish(**_kwargs):
            calls["publish"] += 1
            return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=lambda **_kwargs: _coverage_result(),
            closing_coverage_root=root,
            postmatch_shadow_fn=broken_shadow,
            refresh_fn=fake_refresh,
            snapshot_builder=lambda _cache_dir, competition_id, snapshot_at: _snapshot(
                ["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at
            ),
            publish_fn=fake_publish,
        )
        written = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert result["status"] == "published"
    assert calls == {"refresh": 1, "publish": 1}
    assert result["postmatch_shadow"] == {
        "status": "error",
        "reason": "csl_postmatch_shadow_failed",
        "error_type": "RuntimeError",
    }
    assert written["data_quality"]["warnings"].count(
        "csl_postmatch_shadow_failed"
    ) == 1
    assert "private shadow path" not in json.dumps(result)
    assert "private shadow path" not in json.dumps(written)


def test_shadow_runs_before_odds_refresh_failure_and_summary_is_preserved():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        quota_path = root / "quota.json"
        events = []
        coverage_events = []
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})
        _write_json(
            root / "csl_publish_snapshot.json",
            _snapshot(
                ["2026-07-10T12:00:00+00:00"],
                observed_at="2026-07-10T09:00:00+00:00",
            ),
        )

        def fake_results(**_kwargs):
            events.append("results")
            return {"status": "updated"}

        def fake_shadow(**_kwargs):
            events.append("shadow")
            return {
                "status": "stored",
                "competition_id": "csl_2026",
                "decided": 35,
            }

        def fake_coverage(**kwargs):
            events.append("coverage")
            coverage_events.append(kwargs.get("audit_events", []))
            return _coverage_result()

        def blocked_refresh(**_kwargs):
            events.append("odds")
            return {"status": "blocked", "reason": "provider_unavailable"}

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=root / "csl_publish_snapshot.json",
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=fake_results,
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=fake_shadow,
            refresh_fn=blocked_refresh,
            snapshot_builder=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("blocked odds refresh must not build snapshot")
            ),
            publish_fn=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("blocked odds refresh must not publish")
            ),
        )

    assert result["status"] == "blocked"
    assert events == ["results", "coverage", "shadow", "odds", "coverage"]
    assert coverage_events[0] == []
    assert coverage_events[1] == [
        {
            "observed_at": "2026-07-10T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-07-10T12:00:00+00:00",
            "home_canonical": "yunnan_yukun",
            "away_canonical": "henan_fc",
            "issue_code": "provider_refresh_failed",
        }
    ]
    assert result["postmatch_shadow"] == {
        "status": "stored",
        "competition_id": "csl_2026",
        "decided": 35,
    }


def test_csl_publish_retries_pending_snapshot_without_consuming_refresh_again():
    calls = {
        "results": 0,
        "coverage": 0,
        "shadow": 0,
        "refresh": 0,
        "builder": 0,
        "publish": 0,
    }
    events = []

    def fake_load_env(_path):
        return {
            "THE_ODDS_API_KEY_SECONDARY": "test-key",
            "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!",
        }

    def fake_refresh(**_kwargs):
        calls["refresh"] += 1
        return {
            "status": "fetched",
            "events": 1,
            "quota_entry": {"remaining": 173, "used": 327, "last": 3},
            "theoddsapi_provider": "theoddsapi_secondary",
        }

    def fake_results_refresh(**_kwargs):
        calls["results"] += 1
        return {
            "status": "updated",
            "verified_current_season_matches": 136,
            "total_matches": 856,
            "latest_result_date": "2026-07-05",
        }

    def fake_shadow(**_kwargs):
        calls["shadow"] += 1
        if calls["shadow"] > 1:
            raise AssertionError("pending publish retry must not rerun shadow")
        return {"status": "stored", "competition_id": "csl_2026"}

    def fake_builder(_cache_dir, competition_id, snapshot_at):
        calls["builder"] += 1
        return _snapshot(["2026-07-12T12:00:00+00:00"], observed_at=snapshot_at)

    def fake_publish(**_kwargs):
        calls["publish"] += 1
        events.append(f"publish-{calls['publish']}")
        if calls["publish"] == 1:
            raise urllib.error.URLError("temporary tls failure")
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    def first_coverage(**_kwargs):
        return _coverage_result()

    def retry_coverage(**_kwargs):
        calls["coverage"] += 1
        events.append("coverage-retry")
        return _coverage_result(fingerprint=RETRY_COVERAGE_FINGERPRINT)

    def forbidden(**_kwargs):
        raise AssertionError("pending publish retry must not rerun live pipeline")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        pending_path = root / "csl_publish_snapshot.publish_pending.json"
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 176}}})

        first = run_csl_scheduled_publish(
            now="2026-07-10T08:34:54+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=fake_load_env,
            results_refresh_fn=fake_results_refresh,
            closing_coverage_fn=first_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=fake_shadow,
            refresh_fn=fake_refresh,
            snapshot_builder=fake_builder,
            publish_fn=fake_publish,
        )
        second = run_csl_scheduled_publish(
            now="2026-07-10T08:39:54+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=fake_load_env,
            results_refresh_fn=forbidden,
            closing_coverage_fn=retry_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=forbidden,
            refresh_fn=forbidden,
            snapshot_builder=forbidden,
            archive_fn=forbidden,
            publish_fn=fake_publish,
        )

        pending_exists_after = pending_path.exists()

    assert first["status"] == "publish_pending"
    assert second["status"] == "republished"
    assert second["closing_coverage"]["input_fingerprint"] == (
        RETRY_COVERAGE_FINGERPRINT
    )
    assert calls == {
        "results": 1,
        "coverage": 1,
        "shadow": 1,
        "refresh": 1,
        "builder": 1,
        "publish": 2,
    }
    assert events[-2:] == ["coverage-retry", "publish-2"]
    assert pending_exists_after is False


def test_pending_publish_runs_coverage_before_missing_secret_blocks_retry():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        coverage_calls = 0
        publish_calls = 0
        _write_json(
            quota_path,
            {"providers": {"theoddsapi_secondary": {"remaining": 176}}},
        )

        def failing_publish(**_kwargs):
            nonlocal publish_calls
            publish_calls += 1
            raise urllib.error.URLError("temporary tls failure")

        first = run_csl_scheduled_publish(
            now="2026-07-10T08:34:54+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {
                "INGEST_HMAC_SECRET": "test-secret-long-enough-for-validation!!"
            },
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
            closing_coverage_fn=lambda **_kwargs: _coverage_result(),
            closing_coverage_root=root,
            postmatch_shadow_fn=lambda **_kwargs: {"status": "unchanged"},
            refresh_fn=lambda **_kwargs: {
                "status": "fetched",
                "events": 1,
                "quota_entry": {"remaining": 173, "used": 327, "last": 3},
                "theoddsapi_provider": "theoddsapi_secondary",
            },
            snapshot_builder=lambda _cache_dir, competition_id, snapshot_at: _snapshot(
                ["2026-07-12T12:00:00+00:00"], observed_at=snapshot_at
            ),
            archive_fn=lambda **_kwargs: {"status": "duplicate"},
            publish_fn=failing_publish,
        )

        def retry_coverage(**_kwargs):
            nonlocal coverage_calls
            coverage_calls += 1
            return _coverage_result(fingerprint=BLOCKED_RETRY_COVERAGE_FINGERPRINT)

        def forbidden(**_kwargs):
            raise AssertionError("blocked pending retry must not rerun live pipeline")

        second = run_csl_scheduled_publish(
            now="2026-07-10T08:39:54+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {},
            results_refresh_fn=forbidden,
            closing_coverage_fn=retry_coverage,
            closing_coverage_root=root,
            postmatch_shadow_fn=forbidden,
            refresh_fn=forbidden,
            snapshot_builder=forbidden,
            archive_fn=forbidden,
            publish_fn=forbidden,
        )

    assert first["status"] == "publish_pending"
    assert second["status"] == "blocked"
    assert second["reason"] == "missing_ingest_hmac_secret"
    assert second["closing_coverage"]["input_fingerprint"] == (
        BLOCKED_RETRY_COVERAGE_FINGERPRINT
    )
    assert coverage_calls == 1
    assert publish_calls == 1
