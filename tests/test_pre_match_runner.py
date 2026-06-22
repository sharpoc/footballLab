from pathlib import Path

from worldcup.pre_match_runner import run_pre_match_cycle


def test_pre_match_cycle_default_dry_run_does_not_refresh_odds():
    calls = []

    def lineups_refresh_fn(**kwargs):
        calls.append(("lineups", kwargs))
        return {
            "status": "dry_run",
            "newly_confirmed": 0,
            "notification": None,
        }

    def scheduled_refresh_fn(**_kwargs):
        raise AssertionError("odds refresh should not run in default dry-run mode")

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "dry_run"
    assert calls[0][1]["live"] is False
    assert calls[0][1]["write"] is False
    assert calls[0][1]["notify"] is False
    assert result["post_information_refresh"] == {
        "status": "skipped",
        "reason": "no_new_confirmed_lineups",
    }


def test_pre_match_cycle_missing_lineups_notifies_without_odds_refresh():
    def lineups_refresh_fn(**kwargs):
        assert kwargs["live"] is True
        assert kwargs["write"] is True
        assert kwargs["notify"] is True
        return {
            "status": "captured",
            "matches_checked": 1,
            "confirmed": 0,
            "newly_confirmed": 0,
            "missing": 1,
            "notification": {"status": "sent", "summary": "世界杯官方首发未抓到：1 场"},
        }

    def scheduled_refresh_fn(**_kwargs):
        raise AssertionError("missing lineups should not consume The Odds API quota")

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        live_lineups=True,
        write_lineups=True,
        notify_missing=True,
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "lineups_checked"
    assert result["lineups"]["notification"]["status"] == "sent"
    assert result["post_information_refresh"] == {
        "status": "skipped",
        "reason": "no_new_confirmed_lineups",
    }


def test_pre_match_cycle_can_notify_lineup_audit_without_odds_refresh():
    audit_calls = []

    def lineups_refresh_fn(**_kwargs):
        return {
            "status": "captured",
            "confirmed": 1,
            "newly_confirmed": 0,
            "missing": 0,
        }

    def scheduled_refresh_fn(**_kwargs):
        raise AssertionError("lineup audit notification should not refresh odds")

    def lineup_audit_fn(**kwargs):
        audit_calls.append(kwargs)
        return {
            "summary": {"confirmed_lineups": 1},
            "matches": [
                {
                    "match_label": "Spain vs Saudi Arabia",
                    "minutes_before_kickoff": 30,
                    "issue_flags": ["captured_without_post_information_odds"],
                }
            ],
        }

    def lineup_audit_notification_fn(report, **_kwargs):
        return {
            "status": "sent",
            "summary": "世界杯首发链路待处理：1 场",
            "match_count": len(report["matches"]),
        }

    result = run_pre_match_cycle(
        now="2026-06-22T10:30:00+00:00",
        live_lineups=True,
        write_lineups=True,
        notify_audit=True,
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
        lineup_audit_fn=lineup_audit_fn,
        lineup_audit_notification_fn=lineup_audit_notification_fn,
    )

    assert result["status"] == "lineups_checked"
    assert audit_calls[0]["lineups_path"] == "data/cache/lineups_wc2026.json"
    assert result["lineup_audit"]["notification"]["status"] == "sent"
    assert result["post_information_refresh"] == {
        "status": "skipped",
        "reason": "no_new_confirmed_lineups",
    }


def test_pre_match_cycle_new_lineups_marks_post_information_refresh_required():
    def lineups_refresh_fn(**_kwargs):
        return {
            "status": "captured",
            "confirmed": 1,
            "newly_confirmed": 1,
            "missing": 0,
        }

    def scheduled_refresh_fn(**_kwargs):
        raise AssertionError("refresh should require an explicit refresh_after_lineups flag")

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        live_lineups=True,
        write_lineups=True,
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "post_information_refresh_required"
    assert result["post_information_refresh"] == {
        "status": "required",
        "reason": "new_confirmed_lineups",
        "newly_confirmed": 1,
    }


def test_pre_match_cycle_new_lineups_can_dry_run_refresh_guard_without_live_refresh():
    refresh_calls = []

    def lineups_refresh_fn(**_kwargs):
        return {
            "status": "captured",
            "confirmed": 1,
            "newly_confirmed": 1,
            "missing": 0,
        }

    def scheduled_refresh_fn(**kwargs):
        refresh_calls.append(kwargs)
        return {
            "status": "dry_run",
            "force": True,
            "report": {
                "decision": {
                    "should_refresh": True,
                    "reason": "due",
                    "policy_reason": "post_information_odds_required",
                    "next_due_at": "2026-06-18T14:45:00+00:00",
                    "quota_remaining": 376,
                }
            },
            "refresh": None,
        }

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        live_lineups=True,
        write_lineups=True,
        refresh_guard=True,
        env_path=Path("custom.env"),
        cache_dir=Path("cache"),
        snapshot_path=Path("cache/analysis_snapshot.json"),
        quota_path=Path("cache/quota.json"),
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "post_information_refresh_required"
    assert refresh_calls == [
        {
            "now": "2026-06-18T14:45:00+00:00",
            "live": False,
            "force": True,
            "env_path": Path("custom.env"),
            "cache_dir": Path("cache"),
            "snapshot_path": Path("cache/analysis_snapshot.json"),
            "quota_path": Path("cache/quota.json"),
        }
    ]
    guard = result["post_information_refresh"]["guard"]
    assert guard == {
        "status": "allowed",
        "reason": "quota_available",
        "mode": "dry_run",
        "would_force": True,
        "would_live_refresh": False,
        "quota_remaining": 376,
        "min_quota_remaining": 1,
        "decision": {
            "should_refresh": True,
            "reason": "due",
            "policy_reason": "post_information_odds_required",
            "next_due_at": "2026-06-18T14:45:00+00:00",
        },
    }


def test_pre_match_cycle_refresh_guard_blocks_live_refresh_when_quota_exhausted():
    refresh_calls = []

    def lineups_refresh_fn(**_kwargs):
        return {
            "status": "captured",
            "confirmed": 1,
            "newly_confirmed": 1,
            "missing": 0,
        }

    def scheduled_refresh_fn(**kwargs):
        refresh_calls.append(kwargs)
        assert kwargs["live"] is False
        return {
            "status": "dry_run",
            "force": True,
            "report": {
                "decision": {
                    "should_refresh": False,
                    "reason": "not_due",
                    "policy_reason": "quota_exhausted",
                    "next_due_at": None,
                    "quota_remaining": 0,
                }
            },
            "refresh": None,
        }

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        live_lineups=True,
        write_lineups=True,
        refresh_guard=True,
        refresh_after_lineups=True,
        live_refresh=True,
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "post_information_refresh_blocked"
    assert len(refresh_calls) == 1
    assert result["post_information_refresh"]["status"] == "blocked"
    assert result["post_information_refresh"]["reason"] == "quota_below_min"
    assert result["post_information_refresh"]["newly_confirmed"] == 1


def test_pre_match_cycle_new_lineups_can_force_scheduled_refresh():
    refresh_calls = []

    def lineups_refresh_fn(**_kwargs):
        return {
            "status": "captured",
            "confirmed": 1,
            "newly_confirmed": 1,
            "missing": 0,
        }

    def scheduled_refresh_fn(**kwargs):
        refresh_calls.append(kwargs)
        return {
            "status": "refreshed",
            "force": True,
            "refresh": {"matches": 72, "run_id": "20260618T144500Z-live"},
        }

    result = run_pre_match_cycle(
        now="2026-06-18T14:45:00+00:00",
        live_lineups=True,
        write_lineups=True,
        refresh_after_lineups=True,
        live_refresh=True,
        env_path=Path("custom.env"),
        cache_dir=Path("cache"),
        snapshot_path=Path("cache/analysis_snapshot.json"),
        quota_path=Path("cache/quota.json"),
        lineups_refresh_fn=lineups_refresh_fn,
        scheduled_refresh_fn=scheduled_refresh_fn,
    )

    assert result["status"] == "post_information_refreshed"
    assert refresh_calls == [
        {
            "now": "2026-06-18T14:45:00+00:00",
            "live": True,
            "force": True,
            "env_path": Path("custom.env"),
            "cache_dir": Path("cache"),
            "snapshot_path": Path("cache/analysis_snapshot.json"),
            "quota_path": Path("cache/quota.json"),
        }
    ]
    assert result["post_information_refresh"]["status"] == "refreshed"
