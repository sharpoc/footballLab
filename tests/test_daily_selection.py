from __future__ import annotations

from datetime import datetime, timezone


BEIJING = timezone.utc


def _module():
    from worldcup import daily_selection

    return daily_selection


def _row(
    match_id: str,
    kickoff: str,
    probability: float,
    *,
    competition_id: str = "csl_2026",
    competition_label: str = "中超",
    home: str | None = None,
    away: str | None = None,
    label: str = "MATCH_PICK",
    valid_until: str | None = None,
    fixture_status: str = "SCHEDULED",
):
    home = home or f"Home {match_id}"
    away = away or f"Away {match_id}"
    return {
        "match_id": match_id,
        "kickoff_at_utc": kickoff,
        "competition_id": competition_id,
        "competition_label": competition_label,
        "home_team": home,
        "away_team": away,
        "fixture_status": fixture_status,
        "match_decision": {
            "schema_version": 2,
            "policy_version": "match_pick_v3",
            "label": label,
            "market": "1X2",
            "selection": "home",
            "odds": 1.80,
            "p_hit_safe": probability,
            "p_no_loss_safe": probability,
            "valid_until": valid_until or "2026-08-01T16:00:00+00:00",
        },
    }


def test_window_is_beijing_18_to_next_day_18_and_uses_utc_internally():
    module = _module()
    result = module.compute_daily_selection_window("2026-07-31T10:00:00+00:00")
    assert result.timezone_name == "Asia/Shanghai"
    assert result.start_at_utc.isoformat() == "2026-07-31T10:00:00+00:00"
    assert result.end_at_utc.isoformat() == "2026-08-01T10:00:00+00:00"
    assert result.start_local.hour == 18
    assert result.end_local.hour == 18


def test_window_handles_utc_cross_day_boundaries():
    module = _module()
    window = module.compute_daily_selection_window("2026-08-01T09:59:59+00:00")
    assert module.kickoff_in_window("2026-08-01T09:59:59+00:00", window)
    assert not module.kickoff_in_window("2026-08-01T10:00:00+00:00", window)
    assert not module.kickoff_in_window("2026-07-31T09:59:59+00:00", window)


def test_top_four_is_global_and_has_deterministic_tie_break():
    module = _module()
    rows = [
        _row("z", "2026-07-31T12:00:00+00:00", 0.70, competition_id="c2", competition_label="英超"),
        _row("a", "2026-07-31T13:00:00+00:00", 0.80, competition_id="c1", competition_label="中超"),
        _row("c", "2026-07-31T14:00:00+00:00", 0.75, competition_id="c3", competition_label="德甲"),
        _row("b", "2026-07-31T15:00:00+00:00", 0.80, competition_id="c1", competition_label="中超"),
        _row("d", "2026-07-31T16:00:00+00:00", 0.65, competition_id="c4", competition_label="法甲"),
    ]
    result = module.select_daily_top4(
        rows,
        now="2026-07-31T12:00:00+00:00",
        enabled_competition_ids=("c1", "c2", "c3", "c4"),
    )
    assert [row["match_id"] for row in result.selected] == ["a", "b", "c", "z"]
    assert result.candidate_count == 5
    assert result.selected_count == 4


def test_insufficient_pool_is_transparent_and_never_pads_with_fake_rows():
    module = _module()
    rows = [_row("only", "2026-07-31T12:00:00+00:00", 0.61)]
    result = module.select_daily_top4(
        rows,
        now="2026-07-31T12:00:00+00:00",
        enabled_competition_ids=("csl_2026",),
    )
    assert result.selected_count == 1
    assert result.candidate_count == 1
    assert "fewer_than_4_candidates" in result.degradation_reasons
    assert all(row.get("synthetic") is not True for row in result.selected)


def test_started_postponed_expired_no_pick_and_disabled_matches_are_excluded():
    module = _module()
    rows = [
        _row("started", "2026-07-31T11:00:00+00:00", 0.90),
        _row("postponed", "2026-07-31T13:00:00+00:00", 0.89, fixture_status="POSTPONED"),
        _row("expired", "2026-07-31T14:00:00+00:00", 0.88, valid_until="2026-07-31T12:01:00+00:00"),
        _row("no-pick", "2026-07-31T15:00:00+00:00", 0.87, label="NO_CLEAN_MARKET"),
        _row("disabled", "2026-07-31T16:00:00+00:00", 0.86, competition_id="unknown"),
        _row("good", "2026-07-31T17:00:00+00:00", 0.60),
    ]
    result = module.select_daily_top4(
        rows,
        now="2026-07-31T12:30:00+00:00",
        enabled_competition_ids=("csl_2026",),
    )
    assert [row["match_id"] for row in result.selected] == ["good"]
    assert result.excluded_count == 5


def test_match_is_locked_after_kickoff_without_changing_the_public_decision():
    module = _module()
    row = _row("lock", "2026-07-31T12:00:00+00:00", 0.66)
    locked = module.lock_match_for_cycle(row, now="2026-07-31T12:00:01+00:00")
    assert locked.is_locked is True
    assert locked.match["match_decision"] == row["match_decision"]
    assert locked.reason == "match_started"


def test_naive_datetimes_are_rejected_for_window_and_selection():
    module = _module()
    try:
        module.compute_daily_selection_window(datetime(2026, 7, 31, 18, 0, 0))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive datetime must be rejected")
