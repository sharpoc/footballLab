import json
from pathlib import Path
from tempfile import TemporaryDirectory
import urllib.error

from worldcup.csl_scheduled_publish import (
    _runner_diagnostic,
    build_csl_publish_decision,
    run_csl_scheduled_publish,
)


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
                "competition": {"id": "csl_2026", "name": "中超 2026"},
            }
            for index, kickoff in enumerate(kickoffs, start=1)
        ],
    }


def _write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "not_due"
    assert decision["next_due_at"] == "2026-07-11T11:35:00+00:00"


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
    )

    assert decision["should_refresh"] is False
    assert decision["reason"] == "global_throttle"
    assert decision["throttle_remaining_seconds"] == 900


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
            refresh_fn=forbidden,
            publish_fn=forbidden,
        )

    assert result["status"] == "dry_run"
    assert result["decision"]["should_refresh"] is True


def test_live_force_refreshes_builds_snapshot_and_publishes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        runner_diagnostics_path = root / "csl_live_league_runner_check.json"
        quota_path = root / "quota.json"
        calls = {"results": 0, "refresh": 0, "publish": 0}

        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def fake_load_env(path):
            return {
                "THE_ODDS_API_KEY_SECONDARY": "test-key",
                "INGEST_HMAC_SECRET": "test-secret",
            }

        def fake_refresh(**kwargs):
            calls["refresh"] += 1
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
            assert kwargs["live"] is True
            assert kwargs["write"] is True
            return {
                "status": "updated",
                "verified_current_season_matches": 136,
                "total_matches": 856,
                "latest_result_date": "2026-07-05",
            }

        def fake_builder(cache_dir, competition_id, snapshot_at):
            assert competition_id == "csl_2026"
            return _snapshot(["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at)

        def fake_publish(**kwargs):
            calls["publish"] += 1
            assert kwargs["secret"] == "test-secret"
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
            refresh_fn=fake_refresh,
            snapshot_builder=fake_builder,
            publish_fn=fake_publish,
        )

        written = json.loads(snapshot_path.read_text(encoding="utf-8"))
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        runner_diagnostics = json.loads(runner_diagnostics_path.read_text(encoding="utf-8"))
        archived = list((root / "csl_history").glob("snapshot_*-live.json"))

    assert result["status"] == "published"
    assert calls == {"results": 1, "refresh": 1, "publish": 1}
    assert result["results_refresh"]["status"] == "updated"
    assert result["archive"]["status"] == "created"
    assert len(archived) == 1
    assert written["run"]["run_id"] == "20260710T103000Z-csl-live"
    assert diagnostics["run"]["run_id"] == "20260710T103000Z-csl-live"
    assert runner_diagnostics["match_picks"] == 0
    assert runner_diagnostics["missing_decisions"] == 1


def test_archive_failure_warns_but_does_not_block_current_publish():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        def fake_builder(cache_dir, competition_id, snapshot_at):
            return _snapshot(["2026-07-10T12:00:00+00:00"], observed_at=snapshot_at)

        def broken_archive(**_kwargs):
            raise OSError("disk temporarily unavailable")

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {"INGEST_HMAC_SECRET": "test-secret"},
            results_refresh_fn=lambda **_kwargs: {"status": "updated"},
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
    assert "snapshot_archive_failed" in written["data_quality"]["warnings"]


def test_results_failure_marks_cached_fixture_status_stale_without_blocking_odds_publish():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        diagnostics_path = root / "csl_live_league_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:30:00+00:00",
            live=True,
            force=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=diagnostics_path,
            load_env=lambda _path: {"INGEST_HMAC_SECRET": "test-secret"},
            results_refresh_fn=lambda **_kwargs: {
                "status": "error",
                "reason": "results_refresh_failed_using_existing_cache",
            },
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
    assert "club_results_refresh_failed" in written["data_quality"]["warnings"]
    assert written["data_quality"]["stale_sources"] == [
        "csl_results",
        "csl_fixture_status",
    ]


def test_csl_publish_retries_pending_snapshot_without_consuming_refresh_again():
    calls = {"refresh": 0, "publish": 0}

    def fake_load_env(_path):
        return {
            "THE_ODDS_API_KEY_SECONDARY": "test-key",
            "INGEST_HMAC_SECRET": "test-secret",
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
        return {
            "status": "updated",
            "verified_current_season_matches": 136,
            "total_matches": 856,
            "latest_result_date": "2026-07-05",
        }

    def fake_builder(_cache_dir, competition_id, snapshot_at):
        return _snapshot(["2026-07-12T12:00:00+00:00"], observed_at=snapshot_at)

    def fake_publish(**_kwargs):
        calls["publish"] += 1
        if calls["publish"] == 1:
            raise urllib.error.URLError("temporary tls failure")
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

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
            refresh_fn=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("pending publish retry must not refresh")
            ),
            snapshot_builder=fake_builder,
            publish_fn=fake_publish,
        )

        pending_exists_after = pending_path.exists()

    assert first["status"] == "publish_pending"
    assert second["status"] == "republished"
    assert calls == {"refresh": 1, "publish": 2}
    assert pending_exists_after is False
