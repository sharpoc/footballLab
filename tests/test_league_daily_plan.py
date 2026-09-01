import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_daily_plan import (
    load_daily_events,
    merge_market_requests,
    plan_daily_refresh,
)
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def test_expiry_does_not_drop_t25_markets():
    requests = [
        {"competition_id": "epl_2026_27", "event_ids": ["a"], "markets": ["h2h"]},
        {
            "competition_id": "epl_2026_27",
            "event_ids": ["b"],
            "markets": ["h2h", "spreads", "totals"],
        },
    ]

    merged = merge_market_requests(requests)

    assert len(merged) == 1
    assert merged[0]["event_ids"] == ["a", "b"]
    assert merged[0]["markets"] == ["h2h", "spreads", "totals"]
    assert merged[0]["estimated_credits"] == 3


def test_merge_preserves_shared_optional_transport_metadata():
    merged = merge_market_requests([
        {"competition_id": "epl_2026_27", "event_ids": ["a"], "markets": ["h2h"], "sport_key": "soccer_epl", "anchor_metadata": {"a": "expiry"}},
        {"competition_id": "epl_2026_27", "event_ids": ["b"], "markets": ["spreads"], "sport_key": "soccer_epl", "anchor_metadata": {"b": "t25"}},
    ])
    assert merged[0]["sport_key"] == "soccer_epl"
    assert merged[0]["anchor_metadata"] == {"a": ["expiry"], "b": ["t25"]}


EPL = "epl_2026_27"


def _registry():
    return LeagueTeamIdentityRegistry({EPL: {"arsenal": ("Arsenal",), "chelsea": ("Chelsea",)}})


def _active_acceptance():
    from worldcup.league_team_identity import league_team_identity_registry_fingerprint
    return {
        "schema_version": 1,
        "competitions": {EPL: {"competition_id": EPL, "state": "active", "fingerprints": {
            "sport_catalog": "sport", "odds_sample": "odds", "team_identity": league_team_identity_registry_fingerprint(_registry(), EPL), "result_contract": "result",
        }}},
    }


def _event(event_id: str, kickoff: str, *, valid_until: str | None = None):
    event = {"event_id": event_id, "kickoff_at_utc": kickoff, "home_canonical": "arsenal", "away_canonical": "chelsea"}
    if valid_until:
        event["match_decision"] = {"label": "MATCH_PICK", "valid_until": valid_until}
        event["source_snapshot_id"] = "snapshot-1"
    return event


def test_load_uses_production_events_and_strict_identity_without_probe_fallback():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "data/cache/leagues" / EPL / "events.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 1, "competition_id": EPL, "observed_at": "2026-08-31T00:00:00Z", "source_snapshot_id": "events-1", "events": [{"source_event_id": "e1", "home_team": "Arsenal", "away_team": "Chelsea", "kickoff": "2026-09-01T12:00:00Z"}]}))
        probe = root / "data/probe/leagues" / EPL / "events.json"
        probe.parent.mkdir(parents=True)
        probe.write_text("[]")

        result = load_daily_events(root, _active_acceptance(), _registry())

    assert result["competitions"] == [EPL]
    assert result["errors"] == []
    assert result["events"][EPL] == [{"event_id": "e1", "kickoff_at_utc": "2026-09-01T12:00:00+00:00", "home_canonical": "arsenal", "away_canonical": "chelsea", "source_snapshot_id": "events-1"}]


def test_load_falls_back_to_formal_snapshot_but_blocks_bad_cache_and_identity_or_fingerprint():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot = root / "data/cache/leagues" / EPL / "snapshot.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text(json.dumps({"snapshot_id": "s1", "competition": {"id": EPL}, "matches": [{"source_event_id": "e1", "kickoff_at_utc": "2026-09-01T12:00:00Z", "home_team": "Arsenal", "away_team": "Chelsea"}]}))
        acceptance = _active_acceptance()
        acceptance["competitions"][EPL]["fingerprints"]["team_identity"] = "bad"
        blocked = load_daily_events(root, acceptance, _registry())
        assert blocked["events"] == {}
        assert blocked["errors"] == [{"competition_id": EPL, "reason": "acceptance_identity_fingerprint_mismatch"}]

        good = _active_acceptance()
        from worldcup.league_team_identity import league_team_identity_registry_fingerprint
        good["competitions"][EPL]["fingerprints"]["team_identity"] = league_team_identity_registry_fingerprint(_registry(), EPL)
        loaded = load_daily_events(root, good, _registry())
        assert loaded["events"][EPL][0]["event_id"] == "e1"

        (root / "data/cache/leagues" / EPL / "events.json").write_text("{")
        bad = load_daily_events(root, good, _registry())
        assert bad["errors"] == [{"competition_id": EPL, "reason": "production_events_invalid"}]


def test_load_rejects_unregistered_canonical_cache_identity():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "data/cache/leagues" / EPL / "events.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 1, "competition_id": EPL, "observed_at": "2026-08-31T00:00:00Z", "source_snapshot_id": "events-1", "events": [{"source_event_id": "e1", "home_canonical": "invented", "away_canonical": "chelsea", "kickoff": "2026-09-01T12:00:00Z"}]}))
        result = load_daily_events(root, _active_acceptance(), _registry())
    assert result["events"] == {}
    assert result["errors"] == [{"competition_id": EPL, "reason": "production_event_identity_invalid"}]


def test_daily_plan_prioritizes_due_boundaries_and_merges_expiry_with_t25():
    result = plan_daily_refresh(
        now="2026-08-31T04:00:00Z",
        events={EPL: [
            _event("t25", "2026-08-31T04:25:00Z"),
            _event("expiry", "2026-08-31T10:00:00Z", valid_until="2026-08-31T04:19:00Z"),
            _event("t90", "2026-08-31T05:30:00Z"),
            _event("t6", "2026-08-31T10:00:00Z"),
        ]},
        acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=10,
    )
    assert result["requests"][0]["competition_id"] == EPL
    assert result["requests"][0]["event_ids"] == ["expiry", "t25", "t6", "t90"]
    assert result["requests"][0]["markets"] == ["h2h", "spreads", "totals"]
    assert result["requests"][0]["anchors"] == ["T-25m", "T-90m", "EXPIRY", "T-6h"]
    assert result["estimated_credits"] == 3
    assert result["requests"][0]["anchor_metadata"]["t25"] == [f"{EPL}|t25|2026-08-31T04:25:00+00:00|T-25m"]
    assert result["requests"][0]["anchor_metadata"]["expiry"] == [f"{EPL}|expiry|2026-08-31T10:00:00+00:00|EXPIRY|2026-08-31T04:19:00+00:00|snapshot-1", f"{EPL}|expiry|2026-08-31T10:00:00+00:00|T-6h"]


def test_expiry_at_t25_keeps_same_event_full_markets_and_signature_metadata():
    result = plan_daily_refresh(now="2026-08-31T04:00:00Z", events={EPL: [_event("both", "2026-08-31T04:25:00Z", valid_until="2026-08-31T04:19:00Z")]}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=3)
    request = result["requests"][0]
    assert request["markets"] == ["h2h", "spreads", "totals"]
    assert request["anchors"] == ["T-25m", "EXPIRY"]
    assert request["anchor_metadata"]["both"] == [f"{EPL}|both|2026-08-31T04:25:00+00:00|T-25m", f"{EPL}|both|2026-08-31T04:25:00+00:00|EXPIRY|2026-08-31T04:19:00+00:00|snapshot-1"]


def test_merged_budget_execution_preserves_anchor_priority_not_competition_order():
    result = plan_daily_refresh(now="2026-08-31T04:00:00Z", events={
        "serie_a_2026_27": [_event("late", "2026-08-31T10:00:00Z")],
        EPL: [_event("soon", "2026-08-31T04:25:00Z")],
    }, acceptance={"schema_version": 1, "competitions": {**_active_acceptance()["competitions"], "serie_a_2026_27": {"competition_id": "serie_a_2026_27", "state": "active", "fingerprints": {"sport_catalog": "s", "odds_sample": "o", "team_identity": "x", "result_contract": "r"}}}}, state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=3)
    assert [request["competition_id"] for request in result["requests"]] == [EPL]
    assert result["skipped"]["serie_a_2026_27"]["daily_budget_exhausted"] == 1


def test_loader_rejects_cache_without_aware_observed_at_or_nonempty_snapshot_id():
    with TemporaryDirectory() as tmp:
        root = Path(tmp); path = root / "data/cache/leagues" / EPL / "events.json"; path.parent.mkdir(parents=True)
        for observed_at, snapshot_id in (("2026-08-31T00:00:00", "events-1"), ("2026-08-31T00:00:00Z", "")):
            path.write_text(json.dumps({"schema_version": 1, "competition_id": EPL, "observed_at": observed_at, "source_snapshot_id": snapshot_id, "events": []}))
            result = load_daily_events(root, _active_acceptance(), _registry())
            assert result["errors"] == [{"competition_id": EPL, "reason": "production_events_invalid"}]


def test_loader_rejects_blank_or_nonstring_formal_snapshot_id():
    with TemporaryDirectory() as tmp:
        root = Path(tmp); path = root / "data/cache/leagues" / EPL / "snapshot.json"; path.parent.mkdir(parents=True)
        for snapshot_id in ("", "  ", 4, None):
            path.write_text(json.dumps({"snapshot_id": snapshot_id, "competition": {"id": EPL}, "matches": []}))
            result = load_daily_events(root, _active_acceptance(), _registry())
            assert result["errors"] == [{"competition_id": EPL, "reason": "production_snapshot_invalid"}]


def test_expiry_without_signed_snapshot_identity_skips_expiry_but_keeps_regular_anchor():
    event = _event("both", "2026-08-31T04:25:00Z", valid_until="2026-08-31T04:19:00Z")
    event.pop("source_snapshot_id")
    result = plan_daily_refresh(now="2026-08-31T04:00:00Z", events={EPL: [event]}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=3)
    assert result["requests"][0]["anchors"] == ["T-25m"]
    assert result["requests"][0]["anchor_metadata"] == {"both": [f"{EPL}|both|2026-08-31T04:25:00+00:00|T-25m"]}
    assert result["skipped"][EPL]["expiry_source_snapshot_id_invalid"] == 1


def test_daily_plan_blocks_malformed_state_and_skips_started_unknown_and_completed_signatures():
    acceptance = _active_acceptance()
    bad = plan_daily_refresh(now="2026-08-31T04:00:00Z", events={EPL: []}, acceptance=acceptance, state={"schema_version": 2}, quota_mode="normal", daily_credit_limit=1)
    assert bad["requests"] == []
    assert bad["live_blockers"] == ["daily_refresh_state_invalid"]

    state = {"schema_version": 1, "competitions": {EPL: {"successful_anchors": [f"{EPL}|done|2026-08-31T04:25:00+00:00|T-25m"]}}}
    result = plan_daily_refresh(now="2026-08-31T04:00:00Z", events={EPL: [_event("done", "2026-08-31T04:25:00Z"), _event("started", "2026-08-31T03:59:59Z"), {"event_id": "unknown", "kickoff_at_utc": "2026-08-31T04:25:00Z", "home_canonical": None, "away_canonical": "chelsea"}]}, acceptance=acceptance, state=state, quota_mode="normal", daily_credit_limit=10)
    assert result["requests"] == []
    assert result["skipped"][EPL] == {"anchor_already_completed": 1, "post_kickoff": 1, "identity_unverified": 1}


def test_low_quota_budget_and_beijing_day_are_explicit():
    events = {EPL: [_event("t25", "2026-08-31T16:25:00Z"), _event("t90", "2026-08-31T17:30:00Z")]}
    low = plan_daily_refresh(now="2026-08-31T16:00:00Z", events=events, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="low", daily_credit_limit=1)
    assert low["requests"][0]["markets"] == ["h2h"]
    assert low["estimated_credits"] == 1
    assert low["next_due_at"] == "2026-08-31T16:00:00+00:00"

    unconfigured = plan_daily_refresh(now="2026-08-31T15:59:00Z", events=events, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=None)
    assert "daily_budget_unconfigured" in unconfigured["live_blockers"]

    exhausted = plan_daily_refresh(now="2026-08-31T16:00:00Z", events=events, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=2)
    assert exhausted["requests"] == []
    assert exhausted["skipped"][EPL]["daily_budget_exhausted"] == 2


def test_state_budget_is_beijing_scoped_and_malformed_existing_day_fails_closed():
    events = {EPL: [_event("t25", "2026-08-31T16:25:00Z")]}
    state = {"schema_version": 1, "competitions": {}, "budgets": {
        "2026-08-31": {"reserved_credits": 0},
        "2026-09-01": {"reserved_credits": 3},
    }}
    result = plan_daily_refresh(now="2026-08-31T16:00:00Z", events=events, acceptance=_active_acceptance(), state=state, quota_mode="normal", daily_credit_limit=3)
    assert result["requests"] == []
    assert result["skipped"][EPL]["daily_budget_exhausted"] == 1

    state["budgets"]["2026-09-01"] = {}
    blocked = plan_daily_refresh(now="2026-08-31T16:00:00Z", events=events, acceptance=_active_acceptance(), state=state, quota_mode="normal", daily_credit_limit=3)
    assert blocked["live_blockers"] == ["daily_refresh_state_invalid"]


def test_discovery_is_due_once_then_uses_success_or_retry_cooldown_and_next_due():
    initial = plan_daily_refresh(now="2026-08-31T00:00:00Z", events={EPL: []}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="normal", daily_credit_limit=3)
    assert initial["requests"] == [{"competition_id": EPL, "event_ids": [], "markets": ["h2h"], "estimated_credits": 1, "anchors": ["DISCOVERY"]}]
    assert initial["next_due_at"] == "2026-08-31T00:00:00+00:00"

    retry = plan_daily_refresh(now="2026-08-31T00:10:00Z", events={EPL: []}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {EPL: {"last_attempt_at": "2026-08-31T00:00:00Z"}}}, quota_mode="normal", daily_credit_limit=3)
    assert retry["requests"] == []
    assert retry["next_due_at"] == "2026-08-31T00:30:00+00:00"

    successful = plan_daily_refresh(now="2026-08-31T01:00:00Z", events={EPL: []}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {EPL: {"last_attempt_at": "2026-08-31T00:00:00Z", "last_success_at": "2026-08-31T00:00:00Z", "next_discovery_at": "2026-09-01T00:00:00Z"}}}, quota_mode="normal", daily_credit_limit=3)
    assert successful["requests"] == []
    assert successful["next_due_at"] == "2026-09-01T00:00:00+00:00"


def test_low_mode_suppresses_discovery_and_budget_denial_has_diagnostics_and_next_day():
    low = plan_daily_refresh(now="2026-08-31T00:00:00Z", events={EPL: []}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}}, quota_mode="low", daily_credit_limit=3)
    assert low["requests"] == []
    assert low["skipped"][EPL]["discovery_low_quota_suppressed"] == 1

    denied = plan_daily_refresh(now="2026-08-31T16:00:00Z", events={EPL: []}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {}, "budgets": {"2026-09-01": {"reserved_credits": 3}}}, quota_mode="normal", daily_credit_limit=3)
    assert denied["requests"] == []
    assert denied["skipped"][EPL]["daily_budget_exhausted"] >= 1
    assert denied["next_due_at"] == "2026-09-01T16:00:00+00:00"


def test_completed_current_anchor_reports_next_later_anchor_and_signature_retry_cooldown():
    signature = f"{EPL}|e1|2026-08-31T06:00:00+00:00|T-6h"
    event = _event("e1", "2026-08-31T06:00:00Z")
    future = plan_daily_refresh(now="2026-08-31T00:00:00Z", events={EPL: [event]}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {EPL: {"successful_anchors": [signature]}}}, quota_mode="normal", daily_credit_limit=3)
    assert future["requests"] == []
    assert future["next_due_at"] == "2026-08-31T04:30:00+00:00"

    current = _event("current", "2026-08-31T00:25:00Z")
    retry_signature = f"{EPL}|current|2026-08-31T00:25:00+00:00|T-25m"
    cooled = plan_daily_refresh(now="2026-08-31T00:00:00Z", events={EPL: [current]}, acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {EPL: {"last_attempt_signatures": {retry_signature: "2026-08-30T23:45:00Z"}}}}, quota_mode="normal", daily_credit_limit=3)
    assert cooled["requests"] == []
    assert cooled["skipped"][EPL]["anchor_retry_cooldown"] == 1
    assert cooled["next_due_at"] == "2026-08-31T00:15:00+00:00"


def test_attempt_signature_state_rejects_all_malformed_entries_without_due_dependency():
    signature = f"{EPL}|old|2026-08-30T00:25:00+00:00|T-25m"
    malformed = [None, [], "invalid", {1: "2026-08-30T00:00:00Z"},
                 {"": "2026-08-30T00:00:00Z"}, {"  ": "2026-08-30T00:00:00Z"},
                 {signature: None}, {signature: 123}, {signature: "invalid"},
                 {signature: "2026-08-30T00:00:00"}]
    for events in ({EPL: [_event("due", "2026-08-31T00:25:00Z")]},
                   {EPL: [_event("future", "2026-09-01T00:25:00Z")]},
                   {EPL: []}, {}):
        for attempts in malformed:
            result = plan_daily_refresh(
                now="2026-08-31T00:00:00Z", events=events,
                acceptance=_active_acceptance(),
                state={"schema_version": 1, "competitions": {EPL: {"last_attempt_signatures": attempts}}},
                quota_mode="normal", daily_credit_limit=3,
            )
            assert result["live_blockers"] == ["daily_refresh_state_invalid"], (events, attempts, result)
            assert result["requests"] == []
            assert result["estimated_credits"] == 0


def test_attempt_signature_state_preserves_absence_empty_and_valid_unrelated_entries():
    signature = f"{EPL}|old|2026-08-30T00:25:00+00:00|T-25m"
    for row in ({}, {"last_attempt_signatures": {}},
                {"last_attempt_signatures": {signature: "2026-08-30T08:00:00+08:00"}}):
        result = plan_daily_refresh(
            now="2026-08-31T00:00:00Z", events={EPL: [_event("due", "2026-08-31T00:25:00Z")]},
            acceptance=_active_acceptance(), state={"schema_version": 1, "competitions": {EPL: row}},
            quota_mode="normal", daily_credit_limit=3,
        )
        assert result["live_blockers"] == []
        assert result["requests"][0]["event_ids"] == ["due"]
        assert result["estimated_credits"] == 3
