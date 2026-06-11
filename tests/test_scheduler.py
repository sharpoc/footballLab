import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup import scheduler
from worldcup.scheduler import build_refresh_decision, build_run_metadata, build_scheduler_report


def _match(
    kickoff_at_utc: str,
    home_team: str = "Mexico",
    away_team: str = "South Africa",
) -> dict:
    return {
        "source_event_id": f"{home_team}-{away_team}",
        "kickoff_at_utc": kickoff_at_utc,
        "home_team": home_team,
        "away_team": away_team,
    }


def test_scheduler_refreshes_when_no_previous_run():
    decision = build_refresh_decision(
        now="2026-06-08T00:00:00+00:00",
        last_refresh_at=None,
        next_kickoff_at="2026-06-11T19:00:00+00:00",
        quota_remaining=494,
    )

    assert decision.should_refresh is True
    assert decision.reason == "no_previous_refresh"
    assert decision.policy_reason == "default"
    assert decision.interval_seconds == 86400
    assert decision.next_due_at is None


def test_scheduler_uses_daily_interval_regardless_of_kickoff_window():
    decision = build_refresh_decision(
        now="2026-06-08T11:00:00+00:00",
        last_refresh_at="2026-06-08T00:00:00+00:00",
        next_kickoff_at="2026-06-11T19:00:00+00:00",
        quota_remaining=494,
    )

    assert decision.should_refresh is False
    assert decision.reason == "not_due"
    assert decision.policy_reason == "default"
    assert decision.interval_seconds == 86400
    assert decision.next_due_at == "2026-06-09T00:00:00+00:00"


def test_match_plan_uses_lineup_warmup_anchor_before_kickoff():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T17:25:00+00:00",
        last_refresh_at="2026-06-11T16:45:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=494,
    )

    assert plan["match_label"] == "Mexico vs South Africa"
    assert plan["next_update_at"] == "2026-06-11T17:30:00+00:00"
    assert plan["policy_reason"] == "pre_90m_lineup_warmup"
    assert plan["label"] == "T-1小时30分"
    assert plan["description"] == "阵容/伤停预热"
    assert plan["should_refresh"] is False


def test_match_plan_aligns_cadence_to_kickoff_clock_after_manual_refresh():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T02:58:55+00:00",
        last_refresh_at="2026-06-11T02:58:55+00:00",
        match=_match("2026-06-13T19:00:00+00:00"),
        quota_remaining=461,
    )

    assert plan["next_update_at"] == "2026-06-12T03:00:00+00:00"
    assert plan["policy_reason"] == "default"
    assert plan["label"] == "常规"
    assert plan["should_refresh"] is False


def test_match_plan_holds_for_pre_12h_anchor_on_matchday():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T05:13:46+00:00",
        last_refresh_at="2026-06-11T03:08:26+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=455,
    )

    assert plan["next_update_at"] == "2026-06-11T07:00:00+00:00"
    assert plan["policy_reason"] == "pre_12h_checkpoint"
    assert plan["label"] == "T-12小时"
    assert plan["should_refresh"] is False


def test_match_refresh_decision_uses_aligned_cadence_due_time():
    decision = scheduler.build_match_refresh_decision(
        now="2026-06-11T05:13:46+00:00",
        last_refresh_at="2026-06-11T03:08:26+00:00",
        matches=[_match("2026-06-11T19:00:00+00:00")],
        quota_remaining=455,
    )

    assert decision.should_refresh is False
    assert decision.reason == "not_due"
    assert decision.next_due_at == "2026-06-11T07:00:00+00:00"


def test_match_plan_low_quota_keeps_critical_lineup_anchor():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T17:50:00+00:00",
        last_refresh_at="2026-06-11T17:35:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=24,
    )

    assert plan["next_update_at"] == "2026-06-11T18:05:00+00:00"
    assert plan["policy_reason"] == "pre_55m_lineup_main"
    assert plan["label"] == "T-55分钟"
    assert plan["interval_seconds"] == 86400
    assert plan["should_refresh"] is False


def test_match_plan_blocks_when_quota_is_exhausted():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T17:50:00+00:00",
        last_refresh_at="2026-06-11T17:20:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=0,
    )

    assert plan["next_update_at"] is None
    assert plan["policy_reason"] == "quota_exhausted"
    assert plan["label"] == "额度耗尽"
    assert plan["should_refresh"] is False


def test_match_refresh_decision_uses_earliest_match_plan():
    decision = scheduler.build_match_refresh_decision(
        now="2026-06-11T17:25:00+00:00",
        last_refresh_at="2026-06-11T16:45:00+00:00",
        matches=[
            _match("2026-06-11T22:00:00+00:00", home_team="Canada", away_team="Qatar"),
            _match("2026-06-11T19:00:00+00:00"),
        ],
        quota_remaining=494,
    )

    assert decision.should_refresh is False
    assert decision.reason == "not_due"
    assert decision.next_due_at == "2026-06-11T17:30:00+00:00"
    assert decision.policy_reason == "pre_90m_lineup_warmup"
    assert len(decision.match_plans) == 2
    assert decision.match_plans[0]["next_update_at"] == "2026-06-11T17:30:00+00:00"


def test_scheduler_slows_down_when_free_tier_quota_is_low():
    decision = build_refresh_decision(
        now="2026-06-08T12:00:00+00:00",
        last_refresh_at="2026-06-08T00:00:00+00:00",
        next_kickoff_at="2026-06-11T19:00:00+00:00",
        quota_remaining=24,
    )

    assert decision.should_refresh is False
    assert decision.policy_reason == "quota_low"
    assert decision.interval_seconds == 86400
    assert decision.next_due_at == "2026-06-09T00:00:00+00:00"


def test_run_metadata_records_policy_quota_and_source_quality():
    decision = build_refresh_decision(
        now="2026-06-08T00:00:00+00:00",
        last_refresh_at=None,
        next_kickoff_at="2026-06-11T19:00:00+00:00",
        quota_remaining=494,
    )

    metadata = build_run_metadata(
        run_id="20260608T000000Z-live",
        mode="live",
        observed_at="2026-06-08T00:00:00+00:00",
        decision=decision,
        quota={"theoddsapi": {"remaining": 494, "last": 3}},
        source_errors=[{"source": "theoddsapi", "error": "TimeoutError: handshake timed out"}],
        stale_sources=["theoddsapi"],
    )

    assert metadata["schema_version"] == 1
    assert metadata["run_id"] == "20260608T000000Z-live"
    assert metadata["policy"]["reason"] == "no_previous_refresh"
    assert metadata["quota"]["theoddsapi"]["remaining"] == 494
    assert metadata["source_errors"][0]["source"] == "theoddsapi"
    assert metadata["stale_sources"] == ["theoddsapi"]


def test_scheduler_report_reads_snapshot_and_quota_without_refreshing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "analysis_snapshot.json"
        quota_path = root / "quota.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "snapshot_at": "2026-06-08T00:00:00+00:00",
                    "run": {"observed_at": "2026-06-08T00:00:00+00:00"},
                    "matches": [
                        {
                            "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                            "home_team": "Mexico",
                            "away_team": "South Africa",
                        }
                    ],
                }
            )
        )
        quota_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "theoddsapi": {
                            "remaining": 494,
                            "last": 3,
                            "observed_at": "2026-06-08T00:00:00+00:00",
                        }
                    }
                }
            )
        )

        report = build_scheduler_report(
            now="2026-06-08T11:00:00+00:00",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
        )

        assert report["mode"] == "dry-run"
        assert report["decision"]["should_refresh"] is False
        assert report["decision"]["next_due_at"] == "2026-06-09T00:00:00+00:00"
        assert report["quota"]["theoddsapi"]["remaining"] == 494
        assert report["last_refresh_at"] == "2026-06-08T00:00:00+00:00"


def test_match_plan_uses_pre_6h_checkpoint_anchor():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T08:00:00+00:00",
        last_refresh_at="2026-06-11T07:30:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=494,
    )

    assert plan["next_update_at"] == "2026-06-11T13:00:00+00:00"
    assert plan["policy_reason"] == "pre_6h_checkpoint"
    assert plan["label"] == "T-6小时"
    assert plan["should_refresh"] is False


def test_match_plan_skips_removed_t70_anchor_between_t90_and_t55():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T17:40:00+00:00",
        last_refresh_at="2026-06-11T17:35:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=494,
    )

    assert plan["next_update_at"] == "2026-06-11T18:05:00+00:00"
    assert plan["policy_reason"] == "pre_55m_lineup_main"
