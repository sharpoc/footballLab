from worldcup.league_live_planner import plan_league_live_refresh


def _event(event_id, kickoff, *, valid_until=None):
    row = {
        "id": event_id,
        "commence_time": kickoff,
        "home_team": f"home-{event_id}",
        "away_team": f"away-{event_id}",
    }
    if valid_until:
        row["match_decision"] = {"label": "MATCH_PICK", "valid_until": valid_until}
    return row


def test_planner_prioritizes_nearest_due_kickoff_instead_of_registry_order():
    result = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition={
            "serie_a_2026_27": [_event("it-1", "2026-08-24T17:00:00Z")],
            "epl_2026_27": [_event("epl-1", "2026-08-24T13:20:00Z")],
            "laliga_2026_27": [_event("es-1", "2026-08-24T12:20:00Z")],
        },
        acceptance_by_competition={
            "serie_a_2026_27": "active",
            "epl_2026_27": "active",
            "laliga_2026_27": "active",
        },
        quota_remaining=100,
    )

    assert [row["competition_id"] for row in result["requests"]] == [
        "laliga_2026_27",
        "epl_2026_27",
        "serie_a_2026_27",
    ]
    assert [row["anchor"] for row in result["requests"]] == ["T-25m", "T-90m", "T-6h"]
    assert result["requests"][0]["markets"] == ["h2h", "spreads", "totals"]
    assert result["requests"][1]["markets"] == ["h2h"]


def test_planner_coalesces_same_sport_key_anchor_and_excludes_started_matches():
    result = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition={
            "epl_2026_27": [
                _event("started", "2026-08-24T11:59:59Z"),
                _event("epl-1", "2026-08-24T13:00:00Z"),
                _event("epl-2", "2026-08-24T13:10:00Z"),
            ]
        },
        acceptance_by_competition={"epl_2026_27": "active"},
        quota_remaining=100,
    )

    assert len(result["requests"]) == 1
    assert result["requests"][0]["event_ids"] == ["epl-1", "epl-2"]
    assert result["skipped"]["epl_2026_27"]["post_kickoff"] == 1


def test_planner_puts_active_expiry_guard_ahead_of_probe_and_stops_without_quota():
    inputs = {
        "epl_2026_27": [
            _event(
                "epl-1",
                "2026-08-25T12:00:00Z",
                valid_until="2026-08-24T12:15:00Z",
            )
        ],
        "ligue_1_2026_27": [_event("fr-1", "2026-08-25T12:00:00Z")],
    }
    active = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition=inputs,
        acceptance_by_competition={"epl_2026_27": "active", "ligue_1_2026_27": "probing"},
        quota_remaining=100,
    )
    exhausted = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition=inputs,
        acceptance_by_competition={"epl_2026_27": "active", "ligue_1_2026_27": "probing"},
        quota_remaining=0,
    )

    assert active["requests"][0]["competition_id"] == "epl_2026_27"
    assert active["requests"][0]["reason"] == "pick_expiry_guard"
    assert exhausted["requests"] == []
    assert exhausted["stop_reason"] == "quota_exhausted"


def test_planner_keeps_acceptance_probe_behind_active_due_refresh():
    result = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition={
            "epl_2026_27": [_event("epl-1", "2026-08-24T17:00:00Z")],
            "ligue_1_2026_27": [_event("fr-1", "2026-08-24T12:20:00Z")],
        },
        acceptance_by_competition={"epl_2026_27": "active", "ligue_1_2026_27": "probing"},
        quota_remaining=100,
    )
    assert [row["competition_id"] for row in result["requests"]] == ["epl_2026_27", "ligue_1_2026_27"]
    assert result["requests"][1]["reason"] == "acceptance_probe"


def test_planner_rejects_naive_now_and_conflicting_event_identity():
    try:
        plan_league_live_refresh(
            now="2026-08-24T12:00:00",
            events_by_competition={},
            acceptance_by_competition={},
            quota_remaining=100,
        )
    except ValueError as exc:
        assert str(exc) == "league_live_now_must_be_timezone_aware"
    else:
        raise AssertionError("naive now must fail")

    result = plan_league_live_refresh(
        now="2026-08-24T12:00:00Z",
        events_by_competition={
            "epl_2026_27": [
                _event("epl-1", "2026-08-24T13:00:00Z"),
                _event("epl-1", "2026-08-24T14:00:00Z"),
            ]
        },
        acceptance_by_competition={"epl_2026_27": "active"},
        quota_remaining=100,
    )

    assert result["requests"] == []
    assert result["skipped"]["epl_2026_27"]["event_identity_conflict"] == 2
