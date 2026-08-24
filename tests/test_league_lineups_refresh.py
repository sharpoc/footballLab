import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_lineups_refresh import run_league_lineups_refresh
from worldcup.league_team_identity import accepted_league_team_identity_registry


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
COMPETITION = "epl_2026_27"
PROVIDER_COMPETITION = "47"


def _active_report():
    return {
        "schema_version": 1,
        "competitions": {
            COMPETITION: {
                "competition_id": COMPETITION,
                "state": "active",
                "fingerprints": {
                    "sport_catalog": "sport",
                    "odds_sample": "odds",
                    "team_identity": "identity",
                    "result_contract": "result",
                },
            },
        },
    }


def _fixture(event_id, kickoff, home="Arsenal", away="Chelsea"):
    return {
        "competition_id": COMPETITION,
        "event_id": event_id,
        "kickoff_at_utc": kickoff,
        "home_team": home,
        "away_team": away,
    }


def _calendar(*matches):
    return {
        "leagues": [{"id": PROVIDER_COMPETITION, "matches": list(matches)}],
    }


def _calendar_match(match_id, kickoff, home, away):
    return {
        "id": match_id,
        "home": {"name": home},
        "away": {"name": away},
        "status": {"utcTime": kickoff},
    }


def _details(match_id, kickoff, home, away, player_offset=0):
    return {
        "general": {
            "matchId": match_id,
            "leagueId": PROVIDER_COMPETITION,
            "matchTimeUTC": kickoff,
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
        },
        "content": {
            "lineup": {
                "lineupStatus": "confirmed",
                "homeTeam": {
                    "formation": "4-3-3",
                    "starters": [
                        {"id": player_offset + number, "name": {"fullName": f"Home {number}"}}
                        for number in range(1, 12)
                    ],
                },
                "awayTeam": {
                    "formation": "4-2-3-1",
                    "starters": [
                        {"id": player_offset + number, "name": {"fullName": f"Away {number}"}}
                        for number in range(21, 32)
                    ],
                },
            },
        },
    }


def _empty_state():
    return {"schema_version": 1, "events": {}}


def _explode(*_args, **_kwargs):
    raise AssertionError("forbidden dependency was called")


def _root_hash(root):
    digest = hashlib.sha256()
    base = Path(root)
    if not base.exists():
        return digest.hexdigest()
    for path in sorted(path for path in base.rglob("*") if path.is_file()):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_committed_inputs(root):
    root = Path(root)
    acceptance = root / "data/local/leagues/acceptance.json"
    acceptance.parent.mkdir(parents=True)
    acceptance.write_text(json.dumps(_active_report()), encoding="utf-8")
    snapshot = root / f"data/cache/leagues/{COMPETITION}/snapshot.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({
        "competition": {"id": COMPETITION},
        "matches": [_fixture("epl-1", "2026-08-24T13:00:00+00:00")],
    }), encoding="utf-8")
    state = root / "data/local/leagues/lineup_state.json"
    state.write_text(json.dumps(_empty_state()), encoding="utf-8")


def test_default_dry_run_reads_committed_inputs_without_any_side_effect():
    """Calling a live-only dependency or touching disk would break dry-run safety."""
    with TemporaryDirectory() as tmp:
        _write_committed_inputs(tmp)
        before = _root_hash(tmp)

        result = run_league_lineups_refresh(
            root=tmp,
            now=NOW,
            calendar_fetcher=_explode,
            details_fetcher=_explode,
            store_factory=_explode,
            env_loader=_explode,
            notifier=_explode,
        )

        assert result["status"] == "dry_run"
        assert result["counts"]["request_count"] == 1
        assert result["counts"]["calendar_fetch_count"] == 0
        assert result["counts"]["details_fetch_count"] == 0
        assert result["newly_confirmed"] == {}
        assert _root_hash(tmp) == before


def test_live_write_mismatch_is_blocked_before_read_or_external_dependencies():
    """One live flag must never cause a partial read/network/write execution."""
    for live, write in ((True, False), (False, True)):
        result = run_league_lineups_refresh(
            root="unused",
            now=NOW,
            live=live,
            write=write,
            acceptance_loader=_explode,
            fixtures_loader=_explode,
            state_loader=_explode,
            calendar_fetcher=_explode,
            details_fetcher=_explode,
            store_factory=_explode,
            env_loader=_explode,
            notifier=_explode,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "live_write_must_be_explicit"


def test_no_due_live_plan_makes_zero_transport_and_store_calls():
    """Ignoring the local due gate would turn a five-minute wakeup into provider traffic."""
    result = run_league_lineups_refresh(
        root="unused",
        now=NOW,
        live=True,
        write=True,
        acceptance_report=_active_report(),
        fixtures_by_competition={
            COMPETITION: [_fixture("epl-later", "2026-08-24T14:00:00+00:00")],
        },
        state=_empty_state(),
        provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
        identity_registry=accepted_league_team_identity_registry(),
        calendar_fetcher=_explode,
        details_fetcher=_explode,
        store_factory=_explode,
    )

    assert result["status"] == "no_due"
    assert result["counts"]["request_count"] == 0
    assert result["counts"]["calendar_fetch_count"] == 0
    assert result["counts"]["details_fetch_count"] == 0


def test_live_refresh_coalesces_calendar_by_date_and_fetches_only_due_details():
    """Fetching one calendar per event or details for non-due events would waste provider calls."""
    due = [
        _fixture("epl-1", "2026-08-24T13:00:00+00:00", "Arsenal", "Chelsea"),
        _fixture("epl-2", "2026-08-24T13:15:00+00:00", "Liverpool", "Everton"),
        _fixture("epl-later", "2026-08-24T14:00:00+00:00", "Fulham", "Brentford"),
    ]
    calendar = _calendar(
        _calendar_match("1001", due[0]["kickoff_at_utc"], "Arsenal", "Chelsea"),
        _calendar_match("1002", due[1]["kickoff_at_utc"], "Liverpool", "Everton"),
        _calendar_match("1003", due[2]["kickoff_at_utc"], "Fulham", "Brentford"),
    )
    details = {
        "1001": _details("1001", due[0]["kickoff_at_utc"], "Arsenal", "Chelsea"),
        "1002": _details("1002", due[1]["kickoff_at_utc"], "Liverpool", "Everton", 100),
        "1003": _details("1003", due[2]["kickoff_at_utc"], "Fulham", "Brentford", 200),
    }
    calendar_dates = []
    detail_ids = []

    def fetch_calendar(*, date, transport=None):
        del transport
        calendar_dates.append(date)
        return calendar

    def fetch_details(*, match_id, transport=None):
        del transport
        detail_ids.append(str(match_id))
        return details[str(match_id)]

    with TemporaryDirectory() as tmp:
        result = run_league_lineups_refresh(
            root=tmp,
            now=NOW,
            live=True,
            write=True,
            acceptance_report=_active_report(),
            fixtures_by_competition={COMPETITION: due},
            state=_empty_state(),
            provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
            identity_registry=accepted_league_team_identity_registry(),
            calendar_fetcher=fetch_calendar,
            details_fetcher=fetch_details,
        )

        assert result["status"] == "refreshed"
        assert calendar_dates == ["20260824"]
        assert detail_ids == ["1001", "1002"]
        assert [row["event_id"] for row in result["newly_confirmed"][COMPETITION]] == ["epl-1", "epl-2"]
        assert result["counts"]["calendar_fetch_count"] == 1
        assert result["counts"]["details_fetch_count"] == 2


def test_one_details_failure_is_isolated_and_never_exposes_exception_text():
    """One failed match must not discard another confirmation or leak provider error text."""
    fixtures = [
        _fixture("epl-1", "2026-08-24T13:00:00+00:00", "Arsenal", "Chelsea"),
        _fixture("epl-2", "2026-08-24T13:15:00+00:00", "Liverpool", "Everton"),
    ]
    calendar = _calendar(*[
        _calendar_match(str(1001 + index), row["kickoff_at_utc"], row["home_team"], row["away_team"])
        for index, row in enumerate(fixtures)
    ])

    def fetch_details(*, match_id, transport=None):
        del transport
        if str(match_id) == "1002":
            raise RuntimeError("SECRET signed URL and Cookie")
        return _details("1001", fixtures[0]["kickoff_at_utc"], "Arsenal", "Chelsea")

    with TemporaryDirectory() as tmp:
        result = run_league_lineups_refresh(
            root=tmp,
            now=NOW,
            live=True,
            write=True,
            acceptance_report=_active_report(),
            fixtures_by_competition={COMPETITION: fixtures},
            state=_empty_state(),
            provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
            identity_registry=accepted_league_team_identity_registry(),
            calendar_fetcher=lambda **_kwargs: calendar,
            details_fetcher=fetch_details,
        )

        assert [row["event_id"] for row in result["newly_confirmed"][COMPETITION]] == ["epl-1"]
        assert result["rejection_reasons"][COMPETITION]["details_fetch_failed"] == 1
        assert result["counts"]["source_failure_count"] == 1
        assert "SECRET" not in json.dumps(result, ensure_ascii=False)
        assert "Cookie" not in json.dumps(result, ensure_ascii=False)


def test_cache_failure_prevents_state_commit_and_new_confirmation_output():
    """Publishing in-memory evidence after cache failure would violate cache-before-state ordering."""
    fixture = _fixture("epl-1", "2026-08-24T13:00:00+00:00")
    calendar = _calendar(_calendar_match("1001", fixture["kickoff_at_utc"], "Arsenal", "Chelsea"))
    details = _details("1001", fixture["kickoff_at_utc"], "Arsenal", "Chelsea")
    calls = {"state": 0}

    class FailingStore:
        def read_competition(self, _competition_id):
            return None

        def commit_confirmed(self, _competition_id, _report):
            raise OSError("SECRET cache path")

        def commit_state(self, _state):
            calls["state"] += 1
            raise AssertionError("state commit must not run")

    result = run_league_lineups_refresh(
        root="unused",
        now=NOW,
        live=True,
        write=True,
        acceptance_report=_active_report(),
        fixtures_by_competition={COMPETITION: [fixture]},
        state=_empty_state(),
        provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
        identity_registry=accepted_league_team_identity_registry(),
        calendar_fetcher=lambda **_kwargs: calendar,
        details_fetcher=lambda **_kwargs: details,
        store_factory=lambda _root: FailingStore(),
    )

    assert result["status"] == "error"
    assert result["reason"] == "cache_commit_failed"
    assert result["newly_confirmed"] == {}
    assert calls["state"] == 0
    assert "SECRET" not in json.dumps(result, ensure_ascii=False)


def test_an_already_cached_fingerprint_is_not_newly_confirmed():
    """Treating an unchanged cache receipt as new would duplicate downstream refreshes."""
    fixture = _fixture("epl-1", "2026-08-24T13:00:00+00:00")
    calendar = _calendar(_calendar_match("1001", fixture["kickoff_at_utc"], "Arsenal", "Chelsea"))
    details = _details("1001", fixture["kickoff_at_utc"], "Arsenal", "Chelsea")

    with TemporaryDirectory() as tmp:
        first = run_league_lineups_refresh(
            root=tmp,
            now=NOW,
            live=True,
            write=True,
            acceptance_report=_active_report(),
            fixtures_by_competition={COMPETITION: [fixture]},
            state=_empty_state(),
            provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
            identity_registry=accepted_league_team_identity_registry(),
            calendar_fetcher=lambda **_kwargs: calendar,
            details_fetcher=lambda **_kwargs: details,
        )
        stale_state = _empty_state()
        second = run_league_lineups_refresh(
            root=tmp,
            now=NOW,
            live=True,
            write=True,
            acceptance_report=_active_report(),
            fixtures_by_competition={COMPETITION: [fixture]},
            state=stale_state,
            provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
            identity_registry=accepted_league_team_identity_registry(),
            calendar_fetcher=lambda **_kwargs: calendar,
            details_fetcher=lambda **_kwargs: details,
        )

        assert first["counts"]["newly_confirmed_count"] == 1
        assert second["newly_confirmed"] == {}
        assert second["counts"]["newly_confirmed_count"] == 0


def test_malformed_acceptance_fixtures_and_state_fail_closed_without_dependencies():
    """Treating corrupt committed inputs as empty would bypass acceptance or restart throttling evidence."""
    cases = [
        ({"schema_version": 1, "competitions": {"fifa_world_cup_2026": {}}}, {}, _empty_state(), "invalid_acceptance_report"),
        (_active_report(), {COMPETITION: "not-a-list"}, _empty_state(), "invalid_fixture_snapshot"),
        (_active_report(), {}, {"schema_version": 1, "events": {f"{COMPETITION}:epl-1": {
            "last_polled_at": "2026-08-24T12:00:00", "confirmed": False,
        }}}, "invalid_lineup_state"),
    ]
    for acceptance, fixtures, state, reason in cases:
        result = run_league_lineups_refresh(
            root="unused",
            now=NOW,
            live=True,
            write=True,
            acceptance_report=acceptance,
            fixtures_by_competition=fixtures,
            state=state,
            provider_competition_ids={COMPETITION: PROVIDER_COMPETITION},
            identity_registry=accepted_league_team_identity_registry(),
            calendar_fetcher=_explode,
            details_fetcher=_explode,
            store_factory=_explode,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == reason
