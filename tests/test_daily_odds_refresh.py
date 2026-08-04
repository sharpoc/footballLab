from datetime import datetime, timezone

from worldcup.daily_competitions import (
    daily_competition_catalog,
    resolve_provider_catalog,
)
from worldcup.daily_odds_refresh import refresh_daily_odds

UTC = timezone.utc


def _sports(*items):
    return [
        {"key": key, "active": active, "title": title or key}
        for key, active, title in items
    ]


def _event(event_id, kickoff, home="A", away="B"):
    return {
        "id": event_id,
        "commence_time": kickoff,
        "home_team": home,
        "away_team": away,
    }


def test_provider_status_is_dynamic_and_keeps_all_twenty_rows_visible():
    catalog = daily_competition_catalog()
    resolved = resolve_provider_catalog(
        catalog,
        _sports(
            ("soccer_china_superleague", True, "Chinese Super League"),
            ("soccer_epl", False, "EPL"),
            ("soccer_unknown", True, "Unknown"),
        ),
    )

    assert len(resolved) == 20
    by_name = {item.name: item for item in resolved}
    assert by_name["中超"].status == "enabled"
    assert by_name["英超"].status == "provider_unavailable"
    assert by_name["英冠"].status == "provider_unavailable"
    assert by_name["英冠"].sport_key == "soccer_efl_champ"


def test_verified_seventeen_leagues_resolve_when_all_exact_provider_keys_are_active():
    catalog = daily_competition_catalog()
    sports = _sports(
        *(
            (item.sport_key, True, item.name)
            for item in catalog
            if item.sport_key
        )
    )

    resolved = resolve_provider_catalog(catalog, sports)
    by_name = {item.name: item for item in resolved}

    assert sum(item.status == "enabled" for item in resolved) == 17
    assert all(by_name[name].status == "enabled" for name in (item.name for item in catalog if item.sport_key))
    assert all(
        by_name[name].status == "provider_unavailable"
        for name in ("墨西哥甲", "澳超", "阿根廷超")
    )


def test_same_sport_key_two_events_are_one_request_and_one_snapshot_wave():
    calls = []
    writes = []
    now = "2026-08-01T06:00:00+00:00"

    result = refresh_daily_odds(
        now=now,
        sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
        events_fetcher=lambda sport_key: [
            _event("a", "2026-08-01T11:00:00Z"),
            _event("b", "2026-08-01T11:30:00Z"),
        ],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets)) or {"events": []},
        snapshot_writer=lambda payload: writes.append(payload),
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    assert len(calls) == 1
    assert calls[0] == ("soccer_china_superleague", ("h2h",))
    assert result.request_count == 1
    assert len(writes) == 1


def test_refresh_rejects_naive_now_before_fetching_dependencies():
    def fail(*_args, **_kwargs):
        raise AssertionError("naive now must fail closed before provider calls")

    try:
        refresh_daily_odds(
            now="2026-08-01T06:00:00",
            sports_fetcher=fail,
            events_fetcher=fail,
            odds_fetcher=fail,
        )
    except ValueError as exc:
        assert str(exc) == "now must be timezone-aware"
    else:
        raise AssertionError("expected timezone-aware validation")


def test_writer_failure_does_not_commit_idempotency_state():
    calls = []
    state = set()

    def writer(_payload):
        raise OSError("disk full")

    kwargs = dict(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
        events_fetcher=lambda sport_key: [_event("same", "2026-08-01T11:00:00Z")],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets)) or {"events": []},
        snapshot_writer=writer,
        quota_remaining_by_key={"soccer_china_superleague": 10},
        state=state,
    )

    try:
        refresh_daily_odds(**kwargs)
    except OSError as exc:
        assert str(exc) == "disk full"
    else:
        raise AssertionError("expected writer failure")

    assert state == set()
    assert calls == [("soccer_china_superleague", ("h2h",))]


def test_refresh_result_and_snapshot_include_standardized_daily_fields():
    writes = []
    result = refresh_daily_odds(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
        events_fetcher=lambda sport_key: [_event("same", "2026-08-01T11:00:00Z")],
        odds_fetcher=lambda sport_key, markets: {
            "events": [
                {
                    "id": "same",
                    "commence_time": "2026-08-01T11:00:00Z",
                    "home_team": "A",
                    "away_team": "B",
                    "bookmakers": [
                        {
                            "key": "book-a",
                            "last_update": "2026-08-01T05:59:00Z",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "A", "price": 1.8},
                                        {"name": "Draw", "price": 3.4},
                                        {"name": "B", "price": 4.5},
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        snapshot_writer=lambda payload: writes.append(payload),
        quota_remaining_by_key={"soccer_china_superleague": 10},
    )

    assert result.to_dict()["schema_version"] == 2
    assert writes[0]["schema_version"] >= 2
    assert writes[0]["timezone"] == "Asia/Shanghai"
    assert writes[0]["events"][0]["event_id"] == "same"
    assert "raw_bookmaker_payload" not in str(writes[0])
    assert "bookmakers" not in str(writes[0])


def test_different_sport_keys_are_separate_requests_in_one_wave():
    calls = []
    events = {
        "soccer_china_superleague": [_event("csl", "2026-08-01T11:00:00Z")],
        "soccer_epl": [_event("epl", "2026-08-01T11:00:00Z")],
    }

    result = refresh_daily_odds(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: _sports(
            ("soccer_china_superleague", True, "CSL"),
            ("soccer_epl", True, "EPL"),
        ),
        events_fetcher=lambda sport_key: events[sport_key],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets)) or [],
        quota_remaining_by_key={
            "soccer_china_superleague": 10,
            "soccer_epl": 10,
        },
    )

    assert result.request_count == 2
    assert {item[0] for item in calls} == {
        "soccer_china_superleague",
        "soccer_epl",
    }


def test_anchor_markets_are_h2h_then_full_markets_at_t25():
    calls = []
    event = _event("same", "2026-08-01T12:00:00Z")
    state = set()
    base = dict(
        sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
        events_fetcher=lambda sport_key: [event],
        odds_fetcher=lambda sport_key, markets: calls.append(markets) or [],
        quota_remaining_by_key={"soccer_china_superleague": 10},
        state=state,
    )

    refresh_daily_odds(now="2026-08-01T06:00:00+00:00", **base)
    refresh_daily_odds(now="2026-08-01T10:30:00+00:00", **base)
    refresh_daily_odds(now="2026-08-01T11:35:00+00:00", **base)

    assert calls == [
        ("h2h",),
        ("h2h",),
        ("h2h", "spreads", "totals"),
    ]


def test_started_empty_duplicate_rescheduled_and_quota_blocked_events_are_safe():
    calls = []
    events = {
        "soccer_china_superleague": [
            _event("started", "2026-08-01T05:00:00Z"),
            _event("duplicate", "2026-08-01T12:00:00Z"),
            _event("duplicate", "2026-08-01T12:30:00Z"),
            _event("quota", "2026-08-01T11:00:00Z"),
        ],
        "soccer_epl": [],
    }

    result = refresh_daily_odds(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: _sports(
            ("soccer_china_superleague", True, "CSL"),
            ("soccer_epl", True, "EPL"),
        ),
        events_fetcher=lambda sport_key: events[sport_key],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets)) or [],
        quota_remaining_by_key={"soccer_china_superleague": 0, "soccer_epl": 10},
    )

    assert calls == []
    assert result.request_count == 0
    assert result.skipped["soccer_china_superleague"]["quota_exhausted"] == 1
    assert result.skipped["soccer_epl"]["no_future_events"] == 1
    assert result.excluded_rescheduled_events == ("duplicate",)


def test_each_sport_anchor_is_idempotent_with_state():
    calls = []
    state = set()
    kwargs = dict(
        now="2026-08-01T06:00:00+00:00",
        sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
        events_fetcher=lambda sport_key: [_event("same", "2026-08-01T11:00:00Z")],
        odds_fetcher=lambda sport_key, markets: calls.append((sport_key, markets)) or [],
        quota_remaining_by_key={"soccer_china_superleague": 10},
        state=state,
    )

    refresh_daily_odds(**kwargs)
    refresh_daily_odds(**kwargs)

    assert calls == [("soccer_china_superleague", ("h2h",))]


def test_source_events_url_preserves_existing_odds_url_contract():
    from urllib.parse import parse_qs, urlparse

    from worldcup.sources.theoddsapi import build_events_url, build_worldcup_odds_url

    events = urlparse(build_events_url("soccer_epl", "key"))
    assert events.path.endswith("/sports/soccer_epl/events/")
    assert parse_qs(events.query) == {"apiKey": ["key"]}
    odds = urlparse(build_worldcup_odds_url("key"))
    assert parse_qs(odds.query)["markets"] == ["h2h,spreads,totals"]


def test_zero_due_wave_atomically_persists_empty_daily_odds_state():
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from worldcup.daily_odds_state import DailyOddsState

    with TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "daily_odds_state.json"
        state = DailyOddsState(state_path)
        result = refresh_daily_odds(
            now="2026-08-04T03:00:00+00:00",
            sports_fetcher=lambda: _sports(("soccer_china_superleague", True, "CSL")),
            events_fetcher=lambda _sport_key: [],
            odds_fetcher=lambda *_args: (_ for _ in ()).throw(
                AssertionError("zero due wave must not call odds")
            ),
            snapshot_writer=lambda _payload: None,
            state=state,
            quota_remaining_by_key={"soccer_china_superleague": 85},
        )
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert result.request_count == 0
        assert payload == {
            "committed_keys": [],
            "namespace": "daily_odds_state",
            "schema_version": 1,
        }
        assert not list(state_path.parent.glob("*.tmp"))
