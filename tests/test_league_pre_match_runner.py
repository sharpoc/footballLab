from __future__ import annotations

import fcntl
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_lineup_notifications import LeagueLineupNotificationOutbox
from worldcup.league_post_lineup_refresh import (
    PostLineupRefreshStateStore,
    _ack_token,
    run_post_lineup_refresh,
)
from worldcup.league_team_identity import LeagueTeamIdentityRegistry
from worldcup.league_pre_match_runner import (
    DEFAULT_LOCK_RELATIVE_PATH,
    LeaguePreMatchStateStore,
    STATE_RELATIVE_PATH,
    run_league_pre_match,
)


NOW = "2026-08-24T12:00:00+00:00"
EPL = "epl_2026_27"
LALIGA = "laliga_2026_27"


def _fail(*_args, **_kwargs):
    raise AssertionError("dependency must not be called")


def _receipt(event_id: str, char: str = "a") -> dict:
    fingerprint = char * 64
    return {
        "event_id": event_id,
        "source_match_id": f"source-{event_id}",
        "kickoff_at_utc": "2026-08-24T12:40:00+00:00",
        "fetched_at": "2026-08-24T11:55:00+00:00",
        "lineup_fingerprint": fingerprint,
        "ack_key": {
            "competition_id": EPL,
            "event_id": event_id,
            "lineup_fingerprint": fingerprint,
        },
    }


def _lineup_result(*rows: dict, status: str = "refreshed") -> dict:
    grouped = {EPL: list(rows)} if rows else {}
    return {
        "status": status,
        "skipped": {},
        "rejection_reasons": {},
        "newly_confirmed": grouped,
        "next_due_at": None,
        "counts": {
            "fixture_count": len(rows),
            "request_count": len(rows),
            "calendar_fetch_count": len(rows),
            "details_fetch_count": len(rows),
            "accepted_count": len(rows),
            "newly_confirmed_count": len(rows),
            "rejection_count": 0,
            "source_failure_count": 0,
            "cache_commit_count": len(rows),
            "state_commit_count": 1 if rows else 0,
        },
    }


def _post_result(
    *,
    durable: tuple[dict, ...] = (),
    retryable: tuple[tuple[dict, str], ...] = (),
    blocked: tuple[tuple[dict, str], ...] = (),
    status: str = "published",
    publish_status: str | None = "stored",
    component_snapshot_id: str = "league-test",
    aggregate_snapshot_id: str = "league-aggregate-test",
    receipt_count: int = 1,
) -> dict:
    def ack(row: dict, reason: str | None = None) -> dict:
        value = {"ack_key": dict(row["ack_key"])}
        if reason is not None:
            value["reason"] = reason
        return value

    publication = None
    if publish_status is not None:
        publication = {
            "status": "published",
            "publish": {"status": publish_status},
            "aggregate": {
                "snapshot_id": aggregate_snapshot_id,
                "run_id": aggregate_snapshot_id,
                "components": [{
                    "competition_id": EPL,
                    "snapshot_id": component_snapshot_id,
                }],
            },
        }
    return {
        "status": status,
        "plan": {"competition_ids": [EPL], "receipt_count": receipt_count},
        "acks": {
            "durable": [ack(row) for row in durable],
            "retryable": [ack(row, reason) for row, reason in retryable],
            "blocked": [ack(row, reason) for row, reason in blocked],
        },
        "refresh": None,
        "publish": publication,
    }


def _decision(selection: str) -> dict:
    return {
        "schema_version": 2,
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": selection,
        "line": None,
        "p_hit_safe": 0.60,
        "odds": 1.90,
    }


def _context(
    event_id: str,
    selection: str = "home",
    *,
    competition_id: str = EPL,
    fixture_status: str = "SCHEDULED",
    acceptance_active: bool = True,
    kickoff_at_utc: str = "2026-08-24T12:40:00+00:00",
    snapshot_id: str = "league-test",
) -> dict:
    return {
        "competition_id": competition_id,
        "event_id": event_id,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_at_utc": kickoff_at_utc,
        "fixture_status": fixture_status,
        "acceptance_active": acceptance_active,
        "snapshot_id": snapshot_id,
        "match_decision": _decision(selection),
    }


def _full_flags(*, notify: bool = False) -> dict:
    return {
        "live_lineups": True,
        "write_lineups": True,
        "refresh_after_lineups": True,
        "live_refresh": True,
        "refresh_guard": True,
        "publish": True,
        "notify": notify,
    }


def _write_pending(root: str | Path, *rows: dict) -> None:
    path = Path(root) / "data/local/leagues/lineup_refresh_pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = {
        f"{EPL}:{row['event_id']}": {"competition_id": EPL, **row}
        for row in rows
    }
    path.write_text(
        json.dumps({"schema_version": 1, "events": events}, sort_keys=True),
        encoding="utf-8",
    )


def test_default_dry_run_does_not_lock_write_or_invoke_external_dependencies():
    with TemporaryDirectory() as tmp:
        calls = []

        def lineups(**kwargs):
            calls.append(kwargs)
            return _lineup_result(status="dry_run")

        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lineups,
            post_lineup_refresh_fn=_fail,
            env_loader=_fail,
            quota_loader=_fail,
            odds_fetcher=_fail,
            publish_fn=_fail,
            notifier=_fail,
            outbox_factory=_fail,
            state_store_factory=_fail,
        )

        assert result["status"] == "dry_run"
        assert calls == [{"root": tmp, "now": NOW, "live": False, "write": False}]
        assert list(Path(tmp).rglob("*")) == []


def test_unsafe_layered_flags_are_rejected_before_any_dependency():
    result = run_league_pre_match(
        root="/does/not/matter",
        now=NOW,
        live_refresh=True,
        lineup_refresh_fn=_fail,
        post_lineup_refresh_fn=_fail,
        env_loader=_fail,
    )

    assert result == {
        "status": "blocked",
        "reason": "unsafe_flag_combination",
        "lock": "not_acquired",
    }


def test_nonblocking_real_lock_contention_invokes_nothing():
    with TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / DEFAULT_LOCK_RELATIVE_PATH
        lock_path.parent.mkdir(parents=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                live_lineups=True,
                write_lineups=True,
                lineup_refresh_fn=_fail,
                post_lineup_refresh_fn=_fail,
                notifier=_fail,
            )

        assert result == {
            "status": "locked",
            "reason": "single_instance_lock_contended",
            "lock": "contended",
        }


def test_all_live_task4_task5_calls_run_inside_the_real_lock():
    with TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / DEFAULT_LOCK_RELATIVE_PATH
        calls = []

        def assert_locked(name: str) -> None:
            with lock_path.open("a+", encoding="utf-8") as contender:
                try:
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    calls.append(name)
                else:
                    raise AssertionError("live dependency escaped the single-instance lock")

        def lineups(**_kwargs):
            assert_locked("lineups")
            return _lineup_result(_receipt("new", "b"))

        def post(**kwargs):
            assert_locked("post")
            row = kwargs["newly_confirmed"][EPL][0]
            return _post_result(durable=(row,))

        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lineups,
            post_lineup_refresh_fn=post,
            **_full_flags(),
        )

        assert result["status"] == "published"
        assert calls == ["lineups", "post"]


def test_full_live_preflight_runs_inside_lock_and_blocks_before_lineups_or_task5():
    with TemporaryDirectory() as tmp:
        lock_path = Path(tmp) / DEFAULT_LOCK_RELATIVE_PATH

        def preflight():
            with lock_path.open("a+", encoding="utf-8") as contender:
                try:
                    fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {"status": "blocked", "reason": "publish_secret_invalid"}
            raise AssertionError("preflight escaped the single-instance lock")

        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=_fail,
            post_lineup_refresh_fn=_fail,
            live_preflight=preflight,
            **_full_flags(),
        )

        assert result == {
            "status": "blocked",
            "reason": "publish_secret_invalid",
            "lock": "acquired",
            "notifications": [],
        }


def test_pending_is_retried_before_lineups_and_not_retried_twice_with_new_due_receipt():
    old = _receipt("old", "a")
    new = _receipt("new", "b")
    with TemporaryDirectory() as tmp:
        _write_pending(tmp, old)
        calls = []

        def post(**kwargs):
            ids = [row["event_id"] for rows in kwargs["newly_confirmed"].values() for row in rows]
            calls.append(("post", ids))
            rows = [row for values in kwargs["newly_confirmed"].values() for row in values]
            return _post_result(durable=tuple(rows))

        def lineups(**_kwargs):
            calls.append(("lineups", []))
            return _lineup_result(old, new)

        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lineups,
            post_lineup_refresh_fn=post,
            **_full_flags(),
        )

        assert result["status"] == "published"
        assert calls == [("post", ["old"]), ("lineups", []), ("post", ["new"])]


def test_lineup_write_failure_stops_before_post_lineup_refresh():
    with TemporaryDirectory() as tmp:
        failed = _lineup_result(status="error")
        failed["reason"] = "cache_commit_failed"
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: failed,
            post_lineup_refresh_fn=_fail,
            **_full_flags(),
        )

        assert result["status"] == "lineup_failed"
        assert result["reason"] == "cache_commit_failed"


def test_quota_block_builds_one_degraded_event_after_durable_context_stage():
    row = _receipt("quota", "c")
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {"status": "sent", "event_fingerprint": event["event_fingerprint"]}

    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(
                blocked=((row, "quota_below_minimum"),),
                status="blocked",
                publish_status=None,
            ),
            match_context_loader=lambda _root: {f"{EPL}:quota": _context("quota")},
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "blocked"
        assert [event["event_type"] for event in delivered] == ["quota_blocked"]
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))
        assert state["receipts"] == {}


def test_publish_failure_never_constructs_a_success_event():
    row = _receipt("publish-fail", "d")

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, _event, **_kwargs):
            raise AssertionError("publish failure must not construct a success event")

    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(
                retryable=((row, "publish_failed"),),
                status="publish_failed",
                publish_status=None,
            ),
            match_context_loader=lambda _root: {
                f"{EPL}:publish-fail": _context("publish-fail")
            },
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "publish_failed"
        assert result["notifications"] == []


def test_notification_failure_is_retained_by_the_real_outbox():
    row = _receipt("notify-fail", "e")
    context_calls = 0

    def contexts(_root):
        nonlocal context_calls
        context_calls += 1
        selection = "home" if context_calls == 1 else "away"
        return {f"{EPL}:notify-fail": _context("notify-fail", selection)}

    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(durable=(row,)),
            match_context_loader=contexts,
            notifier=lambda *_args, **_kwargs: {"status": "failed", "raw": "not returned"},
            **_full_flags(notify=True),
        )

        assert result["status"] == "published"
        assert result["notifications"][0]["status"] == "failed"
        outbox_state = json.loads(
            (Path(tmp) / "data/local/leagues/lineup_notification_state.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(outbox_state["pending"]) == 1
        serialized = json.dumps(result, ensure_ascii=False).lower()
        assert "raw" not in serialized


def test_orchestrator_state_commit_failure_prevents_task5_and_preserves_task4_receipt():
    row = _receipt("state-fail", "f")

    class FailingStateStore:
        def read(self):
            return {"schema_version": 1, "receipts": {}, "source_episodes": {}}

        def commit(self, _state):
            raise OSError("injected state failure")

    with TemporaryDirectory() as tmp:
        _write_pending(tmp, row)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=_fail,
            post_lineup_refresh_fn=_fail,
            match_context_loader=lambda _root: {
                f"{EPL}:state-fail": _context("state-fail")
            },
            state_store_factory=lambda _root: FailingStateStore(),
            **_full_flags(notify=True),
        )

        assert result["status"] == "state_failed"
        pending = json.loads(
            (Path(tmp) / "data/local/leagues/lineup_refresh_pending.json").read_text(
                encoding="utf-8"
            )
        )
        assert f"{EPL}:state-fail" in pending["events"]


def test_source_failure_episode_keeps_its_original_threshold_and_recovers_once():
    events = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            events.append(event["event_type"])
            return {"status": "sent", "event_fingerprint": event["event_fingerprint"]}

    failing = _lineup_result(status="error")
    failing["reason"] = "source_failed"
    failing["rejection_reasons"] = {EPL: {"parser_failed": 1}}
    failing["counts"]["fixture_count"] = 1
    failing["counts"]["request_count"] = 1
    failing["counts"]["source_failure_count"] = 1
    failing["counts"]["rejection_count"] = 1
    failing["source_events"] = [{
        "competition_id": EPL,
        "event_id": "episode",
        "outcome": "failed",
    }]
    recovered = _lineup_result(status="polled")
    recovered["counts"]["fixture_count"] = 1
    recovered["counts"]["request_count"] = 1
    recovered["source_events"] = [{
        "competition_id": EPL,
        "event_id": "episode",
        "outcome": "succeeded",
    }]

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": lambda _root: {
                f"{EPL}:episode": _context("episode")
            },
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: failing,
            source_failure_threshold=2,
            **common,
        )
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: failing,
            source_failure_threshold=99,
            **common,
        )
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: recovered,
            source_failure_threshold=99,
            **common,
        )
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: recovered,
            source_failure_threshold=99,
            **common,
        )

        assert events == ["sustained_source_failure", "source_recovery"]
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))
        episode = state["source_episodes"][f"{EPL}:episode"]
        assert episode["failure_threshold"] == 2
        assert episode["active"] is False


def _source_result(event_id: str, outcome: str) -> dict:
    failed = outcome == "failed"
    result = _lineup_result(status="error" if failed else "polled")
    result["counts"].update({
        "fixture_count": 1,
        "request_count": 1,
        "source_failure_count": 1 if failed else 0,
        "rejection_count": 1 if failed else 0,
    })
    result["rejection_reasons"] = (
        {EPL: {"details_fetch_failed": 1}} if failed else {}
    )
    result["source_events"] = [{
        "competition_id": EPL,
        "event_id": event_id,
        "outcome": outcome,
    }]
    return result


def test_source_success_below_sustained_threshold_closes_silently():
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event["event_type"])
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": lambda _root: {
                f"{EPL}:transient": _context("transient")
            },
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            "source_failure_threshold": 3,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("transient", "failed"),
            **common,
        )
        recovered = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("transient", "succeeded"),
            **common,
        )
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))

        assert recovered["notifications"] == []
        assert delivered == []
        episode = state["source_episodes"][f"{EPL}:transient"]
        assert episode["failure_count"] == 1
        assert episode["active"] is False
        assert episode["recovery_pending"] is False


def test_sustained_failure_intent_retries_after_pre_pending_failure_and_no_due_restart():
    delivery_attempts = []
    fail_before_pending = True

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivery_attempts.append(event["event_type"])
            if fail_before_pending:
                raise OSError("atomic pending write failed")
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        first = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _source_result("failure-restart", "failed"),
            post_lineup_refresh_fn=_fail,
            match_context_loader=lambda _root: {
                f"{EPL}:failure-restart": _context("failure-restart")
            },
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            source_failure_threshold=1,
            **_full_flags(notify=True),
        )
        state_after_failure = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        fail_before_pending = False
        restarted = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
            post_lineup_refresh_fn=_fail,
            match_context_loader=lambda _root: {},
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            source_failure_threshold=1,
            **_full_flags(notify=True),
        )
        state_after_restart = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )

        assert first["notifications"][-1]["status"] == "failed"
        pending_episode = state_after_failure["source_episodes"][f"{EPL}:failure-restart"]
        assert pending_episode["failure_pending"] is True
        assert pending_episode["failure_notified"] is False
        assert restarted["notifications"][-1]["status"] == "sent"
        assert delivery_attempts == [
            "sustained_source_failure",
            "sustained_source_failure",
        ]
        completed_episode = state_after_restart["source_episodes"][
            f"{EPL}:failure-restart"
        ]
        assert completed_episode["active"] is True
        assert completed_episode["failure_pending"] is False
        assert completed_episode["failure_notified"] is True


def test_source_relapse_cancels_pre_pending_recovery_and_keeps_episode_active():
    delivery_attempts = []
    fail_recovery_before_pending = False

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivery_attempts.append(event["event_type"])
            if fail_recovery_before_pending and event["event_type"] == "source_recovery":
                raise OSError("atomic pending write failed")
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": lambda _root: {
                f"{EPL}:relapse": _context("relapse")
            },
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            "source_failure_threshold": 1,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("relapse", "failed"),
            **common,
        )
        fail_recovery_before_pending = True
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("relapse", "succeeded"),
            **common,
        )
        fail_recovery_before_pending = False
        relapsed = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("relapse", "failed"),
            **common,
        )
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))

        assert relapsed["notifications"] == []
        assert delivery_attempts == ["sustained_source_failure", "source_recovery"]
        episode = state["source_episodes"][f"{EPL}:relapse"]
        assert episode["active"] is True
        assert episode["failure_notified"] is True
        assert episode["recovery_pending"] is False


def test_same_source_episode_uses_stable_identity_when_current_kickoff_changes():
    delivered = []
    kickoff = "2026-08-24T12:40:00+00:00"

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    def contexts(_root):
        return {
            f"{EPL}:rescheduled": _context(
                "rescheduled", kickoff_at_utc=kickoff
            )
        }

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": contexts,
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            "source_failure_threshold": 1,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("rescheduled", "failed"),
            **common,
        )
        kickoff = "2026-08-24T12:44:00+00:00"
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("rescheduled", "failed"),
            **common,
        )
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))

        failures = [
            event for event in delivered
            if event["event_type"] == "sustained_source_failure"
        ]
        assert len(failures) == 1
        assert failures[0]["payload"]["kickoff_at_utc"] == "2026-08-24T12:40:00+00:00"
        assert state["source_episodes"][f"{EPL}:rescheduled"]["failure_count"] == 2


def test_malformed_lineup_dependency_fails_closed_without_leaking_or_calling_task5():
    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            live_lineups=True,
            write_lineups=True,
            lineup_refresh_fn=lambda **_kwargs: ["raw", {"Authorization": "secret"}],
            post_lineup_refresh_fn=_fail,
        )

        assert result == {
            "status": "lineup_failed",
            "reason": "lineup_result_invalid",
            "lock": "acquired",
            "notifications": [],
        }
        serialized = json.dumps(result).lower()
        assert "authorization" not in serialized
        assert "secret" not in serialized


def test_task5_pending_converges_before_corrupt_task6_retry_and_lineup_polling_continues():
    old = _receipt("old-pending", "1")
    calls = []

    class CorruptOutbox:
        def retry_pending(self, **_kwargs):
            calls.append("notification_retry")
            raise ValueError("corrupt notification state")

        def deliver(self, _event, **_kwargs):
            raise AssertionError("no event is buildable for the unchanged fixture")

    def post(**kwargs):
        calls.append("task5")
        rows = tuple(
            row
            for grouped in kwargs["newly_confirmed"].values()
            for row in grouped
        )
        return _post_result(durable=rows)

    def lineups(**_kwargs):
        calls.append("task4")
        return _lineup_result(status="no_due")

    with TemporaryDirectory() as tmp:
        _write_pending(tmp, old)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lineups,
            post_lineup_refresh_fn=post,
            match_context_loader=lambda _root: {
                f"{EPL}:old-pending": _context("old-pending")
            },
            outbox_factory=lambda _root: CorruptOutbox(),
            odds_fetcher=_fail,
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "published"
        assert result["notification_retry"] == {"status": "failed"}
        assert calls == ["task5", "task4", "notification_retry"]


def test_old_pending_and_new_receipt_bind_to_their_exact_successive_decisions():
    old = _receipt("old", "2")
    new = _receipt("new", "3")
    current_selection = "home"
    current_snapshot_id = "initial-snapshot"
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    def contexts(_root):
        return {
            f"{EPL}:old": _context(
                "old", current_selection, snapshot_id=current_snapshot_id
            ),
            f"{EPL}:new": _context(
                "new", current_selection, snapshot_id=current_snapshot_id
            ),
        }

    def post(**kwargs):
        nonlocal current_selection, current_snapshot_id
        rows = tuple(
            row
            for grouped in kwargs["newly_confirmed"].values()
            for row in grouped
        )
        event_ids = {row["event_id"] for row in rows}
        if event_ids == {"old"}:
            current_selection = "away"
            current_snapshot_id = "old-snapshot"
        else:
            current_selection = "draw"
            current_snapshot_id = "new-snapshot"
        return _post_result(
            durable=rows,
            component_snapshot_id=current_snapshot_id,
            aggregate_snapshot_id=f"aggregate-{current_snapshot_id}",
        )

    with TemporaryDirectory() as tmp:
        _write_pending(tmp, old)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(old, new),
            post_lineup_refresh_fn=post,
            match_context_loader=contexts,
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "published"
        decisions = {
            event["payload"]["event_id"]: (
                event["payload"]["previous_decision"]["selection"],
                event["payload"]["current_decision"]["selection"],
            )
            for event in delivered
            if event["event_type"].startswith("published_refresh_")
        }
        assert decisions == {"old": ("home", "away"), "new": ("away", "draw")}


def test_bound_receipt_rebuilds_notification_after_pre_pending_failure_and_restart():
    row = _receipt("bound-restart", "9")
    current_selection = "home"
    current_snapshot_id = "before-binding"
    delivered = []

    def contexts(_root):
        return {
            f"{EPL}:bound-restart": _context(
                "bound-restart",
                current_selection,
                snapshot_id=current_snapshot_id,
            )
        }

    def post(**_kwargs):
        nonlocal current_selection, current_snapshot_id
        current_selection = "away"
        current_snapshot_id = "after-binding"
        return _post_result(
            durable=(row,),
            component_snapshot_id=current_snapshot_id,
            aggregate_snapshot_id="aggregate-after-binding",
        )

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        first = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=post,
            match_context_loader=contexts,
            outbox_factory=_fail,
            notifier=_fail,
            **_full_flags(notify=True),
        )
        state_after_failure = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        restarted = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
            post_lineup_refresh_fn=_fail,
            match_context_loader=lambda _root: {},
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )
        state_after_restart = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )

        assert first["notifications"][0]["status"] == "failed"
        bound = next(iter(state_after_failure["receipts"].values()))
        assert bound["previous_decision"]["selection"] == "home"
        assert bound["current_decision"]["selection"] == "away"
        assert restarted["status"] == "lineups_checked"
        assert len(delivered) == 1
        assert delivered[0]["event_type"] == "published_refresh_changed"
        assert delivered[0]["payload"]["previous_decision"]["selection"] == "home"
        assert delivered[0]["payload"]["current_decision"]["selection"] == "away"
        assert state_after_restart["receipts"] == {}


def test_source_evidence_is_event_scoped_and_other_events_cannot_recover_it():
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append((event["event_type"], event["payload"]["event_id"]))
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    contexts = {
        f"{EPL}:failed": _context("failed"),
        f"{EPL}:healthy": _context("healthy"),
        f"{LALIGA}:other": _context("other", competition_id=LALIGA),
    }
    failed = _lineup_result(status="error")
    failed["rejection_reasons"] = {EPL: {"details_fetch_failed": 1}}
    failed["counts"].update({
        "fixture_count": 3,
        "request_count": 1,
        "source_failure_count": 1,
        "rejection_count": 1,
    })
    failed["source_events"] = [{
        "competition_id": EPL,
        "event_id": "failed",
        "outcome": "failed",
    }]
    unrelated_success = _lineup_result(status="polled")
    unrelated_success["counts"].update({"fixture_count": 3, "request_count": 1})
    unrelated_success["source_events"] = [{
        "competition_id": LALIGA,
        "event_id": "other",
        "outcome": "succeeded",
    }]

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": lambda _root: contexts,
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            "source_failure_threshold": 1,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: failed,
            **common,
        )
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: unrelated_success,
            **common,
        )

        assert delivered == [("sustained_source_failure", "failed")]
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))
        assert set(state["source_episodes"]) == {f"{EPL}:failed"}
        assert state["source_episodes"][f"{EPL}:failed"]["active"] is True


def test_recovery_remains_active_until_outbox_is_durable_and_retries_after_restart():
    delivery_attempts = []
    fail_recovery_write = False
    context_available = True

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivery_attempts.append(event["event_type"])
            if fail_recovery_write and event["event_type"] == "source_recovery":
                raise OSError("atomic pending write failed")
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    failed = _lineup_result(status="error")
    failed["rejection_reasons"] = {EPL: {"parser_failed": 1}}
    failed["counts"].update({
        "fixture_count": 1,
        "request_count": 1,
        "source_failure_count": 1,
        "rejection_count": 1,
    })
    failed["source_events"] = [{
        "competition_id": EPL,
        "event_id": "recovery",
        "outcome": "failed",
    }]
    recovered = _lineup_result(status="polled")
    recovered["counts"].update({"fixture_count": 1, "request_count": 1})
    recovered["source_events"] = [{
        "competition_id": EPL,
        "event_id": "recovery",
        "outcome": "succeeded",
    }]

    def contexts(_root):
        if not context_available:
            return {}
        return {f"{EPL}:recovery": _context("recovery")}

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": contexts,
            "outbox_factory": lambda _root: Outbox(),
            "notifier": _fail,
            "source_failure_threshold": 1,
            **_full_flags(notify=True),
        }
        run_league_pre_match(lineup_refresh_fn=lambda **_kwargs: failed, **common)
        fail_recovery_write = True
        failed_delivery = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: recovered,
            **common,
        )
        state_after_failure = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        fail_recovery_write = False
        context_available = False
        restarted = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
            **common,
        )

        assert failed_delivery["notifications"][-1]["status"] == "failed"
        assert state_after_failure["source_episodes"][f"{EPL}:recovery"]["active"] is True
        assert (
            state_after_failure["source_episodes"][f"{EPL}:recovery"]["recovery_pending"]
            is True
        )
        assert restarted["notifications"][-1]["status"] == "sent"
        assert delivery_attempts == [
            "sustained_source_failure",
            "source_recovery",
            "source_recovery",
        ]


def _acceptance_row(competition_id: str, state: str) -> dict:
    return {
        "competition_id": competition_id,
        "state": state,
        "reason": None,
        "fingerprints": {
            name: f"{competition_id}-{name}"
            for name in (
                "sport_catalog",
                "odds_sample",
                "team_identity",
                "result_contract",
            )
        },
    }


def _write_context_fixture(
    root: str | Path,
    *,
    acceptance_state: str,
    fixture_status: str,
    event_id: str,
) -> None:
    acceptance_path = Path(root) / "data/local/leagues/acceptance.json"
    acceptance_path.parent.mkdir(parents=True, exist_ok=True)
    acceptance_path.write_text(
        json.dumps({
            "schema_version": 1,
            "competitions": {EPL: _acceptance_row(EPL, acceptance_state)},
        }),
        encoding="utf-8",
    )
    snapshot_path = Path(root) / f"data/cache/leagues/{EPL}/snapshot.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps({
            "snapshot_id": f"snapshot-{event_id}",
            "competition": {"id": EPL},
            "matches": [{
                "source_event_id": event_id,
                "home_team": "Home FC",
                "away_team": "Away FC",
                "kickoff_at_utc": "2026-08-24T12:15:00+00:00",
                "fixture_status": fixture_status,
                "match_decision": _decision("home"),
            }],
        }),
        encoding="utf-8",
    )


def test_missing_t20_requires_active_acceptance_and_nonterminal_current_fixture():
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    cases = (
        ("identity_verified", "SCHEDULED", 0),
        ("active", "POSTPONED", 0),
        ("active", "CANCELLED", 0),
        ("active", "FINISHED", 0),
        ("active", "", 0),
        ("active", "NOT_A_REAL_STATUS", 0),
        ("active", "SCHEDULED", 1),
    )
    for index, (acceptance_state, fixture_status, expected_count) in enumerate(cases):
        with TemporaryDirectory() as tmp:
            delivered.clear()
            _write_context_fixture(
                tmp,
                acceptance_state=acceptance_state,
                fixture_status=fixture_status,
                event_id=f"fixture-{index}",
            )
            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
                post_lineup_refresh_fn=_fail,
                outbox_factory=lambda _root: Outbox(),
                notifier=_fail,
                **_full_flags(notify=True),
            )

            assert result["status"] == "lineups_checked"
            assert len(delivered) == expected_count
            if delivered:
                assert delivered[0]["event_type"] == "missing_confirmed"


def test_malformed_lineup_state_suppresses_missing_notification_for_whole_cycle():
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    malformed_payloads = (
        "{broken-json",
        json.dumps({"schema_version": 1, "events": []}),
    )
    for index, payload in enumerate(malformed_payloads):
        with TemporaryDirectory() as tmp:
            delivered.clear()
            _write_context_fixture(
                tmp,
                acceptance_state="active",
                fixture_status="SCHEDULED",
                event_id=f"malformed-lineup-state-{index}",
            )
            state_path = Path(tmp) / "data/local/leagues/lineup_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(payload, encoding="utf-8")

            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
                post_lineup_refresh_fn=_fail,
                outbox_factory=lambda _root: Outbox(),
                notifier=_fail,
                **_full_flags(notify=True),
            )

            assert result["status"] == "lineups_checked"
            assert delivered == []


def test_task5_ack_membership_and_publish_semantics_fail_closed():
    row = _receipt("membership", "4")
    unknown = _receipt("unknown", "5")
    cases = (
        _post_result(
            durable=(row,),
            blocked=((row, "quota_below_minimum"),),
            status="partial",
        ),
        _post_result(durable=(unknown,)),
        _post_result(durable=(row, row)),
        _post_result(durable=(), status="published"),
        _post_result(durable=(row,), status="published", publish_status=None),
        _post_result(durable=(row,), status="publish_failed"),
        _post_result(durable=(row,), status="partial", publish_status=None),
        _post_result(
            retryable=((row, "refresh_failed"),),
            status="partial",
            publish_status=None,
        ),
    )
    malformed_reason = _post_result(
        blocked=((row, "quota_below_minimum"),),
        status="blocked",
        publish_status=None,
    )
    malformed_reason["acks"]["blocked"][0]["reason"] = {"raw": "secret"}
    blocked_with_publish = _post_result(
        blocked=((row, "quota_below_minimum"),),
        status="blocked",
    )
    malformed_failure_publish = _post_result(
        retryable=((row, "publish_failed"),),
        status="publish_failed",
        publish_status=None,
    )
    malformed_failure_publish["publish"] = {
        "status": "publish_failed",
        "reason": "league_aggregate_ingest_not_confirmed",
        "publish": {"status": "failed", "raw_response": "secret"},
        "aggregate": None,
    }
    cases = (
        *cases,
        malformed_reason,
        blocked_with_publish,
        malformed_failure_publish,
    )
    for malformed in cases:
        with TemporaryDirectory() as tmp:
            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
                post_lineup_refresh_fn=lambda **_kwargs: malformed,
                match_context_loader=lambda _root: {
                    f"{EPL}:membership": _context("membership")
                },
                outbox_factory=_fail,
                notifier=_fail,
                **_full_flags(notify=True),
            )

            assert result["status"] == "post_refresh_failed"
            assert result["reason"] == "post_lineup_result_invalid"
            assert result["notifications"] == []


def _commit_published_task5_receipt(root: str | Path, row: dict) -> None:
    PostLineupRefreshStateStore(root).commit({
        "schema_version": 1,
        "receipts": {
            _ack_token(row["ack_key"]): {
                "ack_key": dict(row["ack_key"]),
                "phase": "published",
                "snapshot_id": "persisted-snapshot",
                "aggregate_snapshot_id": "persisted-aggregate",
                "publish_status": "stored",
            }
        },
    })


def _commit_task5_contract_rows(
    root: str | Path,
    *,
    committed: tuple[dict, ...] = (),
    published: tuple[dict, ...] = (),
    snapshot_id: str = "league-test",
    published_aggregate_snapshot_id: str = "persisted-aggregate",
) -> None:
    receipts = {
        _ack_token(row["ack_key"]): {
            "ack_key": dict(row["ack_key"]),
            "phase": "committed",
            "snapshot_id": snapshot_id,
        }
        for row in committed
    }
    receipts.update({
        _ack_token(row["ack_key"]): {
            "ack_key": dict(row["ack_key"]),
            "phase": "published",
            "snapshot_id": snapshot_id,
            "aggregate_snapshot_id": published_aggregate_snapshot_id,
            "publish_status": "stored",
        }
        for row in published
    })
    if receipts:
        PostLineupRefreshStateStore(root).commit({
            "schema_version": 1,
            "receipts": receipts,
        })


def test_ack_state_commit_failure_reason_has_exact_bidirectional_task5_matrix():
    current = _receipt("ack-matrix-current", "5")
    waiting = _receipt("ack-matrix-waiting", "6")
    durable = _receipt("ack-matrix-durable", "7")
    second_current = _receipt("ack-matrix-second-current", "8")

    failed_publication = _post_result(
        retryable=((current, "ack_state_commit_failed"),),
        status="publish_failed",
        publish_status=None,
    )
    failed_publication["publish"] = {
        "status": "publish_failed",
        "reason": "league_aggregate_ingest_not_confirmed",
        "publish": {"status": "failed"},
        "aggregate": None,
    }

    cases = (
        {
            "name": "stored_exact_current",
            "valid": True,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="stored",
            ),
        },
        {
            "name": "duplicate_exact_current",
            "valid": True,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="duplicate",
            ),
        },
        {
            "name": "current_failure_with_noncurrent_waiting_retry",
            "valid": True,
            "rows": (current, waiting),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=(
                    (current, "ack_state_commit_failed"),
                    (waiting, "waiting_for_committed_publish"),
                ),
                status="publish_failed",
                publish_status="stored",
                receipt_count=2,
            ),
        },
        {
            "name": "ack_write_visible_before_commit_failure",
            "valid": True,
            "rows": (current,),
            "committed": (),
            "published": (current,),
            "snapshot_id": "league-test",
            "published_aggregate_snapshot_id": "league-aggregate-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="stored",
            ),
        },
        {
            "name": "partial_with_success_publication",
            "valid": False,
            "rows": (durable, current),
            "committed": (current,),
            "published": (durable,),
            "snapshot_id": "league-test",
            "result": _post_result(
                durable=(durable,),
                retryable=((current, "ack_state_commit_failed"),),
                status="partial",
                publish_status="stored",
                receipt_count=2,
            ),
        },
        {
            "name": "partial_without_publication",
            "valid": False,
            "rows": (durable, current),
            "committed": (current,),
            "published": (durable,),
            "snapshot_id": "league-test",
            "result": _post_result(
                durable=(durable,),
                retryable=((current, "ack_state_commit_failed"),),
                status="partial",
                publish_status=None,
                receipt_count=2,
            ),
        },
        {
            "name": "published",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="published",
            ),
        },
        {
            "name": "already_acked",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="already_acked",
                publish_status=None,
            ),
        },
        {
            "name": "blocked_retryable",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="blocked",
                publish_status=None,
            ),
        },
        {
            "name": "blocked_group_reason",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                blocked=((current, "ack_state_commit_failed"),),
                status="blocked",
                publish_status=None,
            ),
        },
        {
            "name": "refresh_failed",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="refresh_failed",
                publish_status=None,
            ),
        },
        {
            "name": "publish_failed_without_publication",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status=None,
            ),
        },
        {
            "name": "publish_failed_with_failed_publication",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": failed_publication,
        },
        {
            "name": "published_receipt_claimed_as_current",
            "valid": False,
            "rows": (current,),
            "committed": (),
            "published": (current,),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="stored",
            ),
        },
        {
            "name": "missing_current_state",
            "valid": False,
            "rows": (current,),
            "committed": (),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="stored",
            ),
        },
        {
            "name": "current_snapshot_mismatch",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "different-snapshot",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="publish_failed",
                publish_status="stored",
            ),
        },
        {
            "name": "incomplete_current_retryable_membership",
            "valid": False,
            "rows": (current, second_current),
            "committed": (current, second_current),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=(
                    (current, "ack_state_commit_failed"),
                    (second_current, "waiting_for_committed_publish"),
                ),
                status="publish_failed",
                publish_status="stored",
                receipt_count=2,
            ),
        },
        {
            "name": "unknown_status",
            "valid": False,
            "rows": (current,),
            "committed": (current,),
            "published": (),
            "snapshot_id": "league-test",
            "result": _post_result(
                retryable=((current, "ack_state_commit_failed"),),
                status="error",
                publish_status="stored",
            ),
        },
    )

    for case in cases:
        with TemporaryDirectory() as tmp:
            _commit_task5_contract_rows(
                tmp,
                committed=case["committed"],
                published=case["published"],
                snapshot_id=case["snapshot_id"],
                published_aggregate_snapshot_id=case.get(
                    "published_aggregate_snapshot_id", "persisted-aggregate"
                ),
            )
            _write_pending(tmp, *case["rows"])
            lineup_calls = []

            def lineups(**_kwargs):
                lineup_calls.append("called")
                return _lineup_result(status="no_due")

            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                lineup_refresh_fn=lineups,
                post_lineup_refresh_fn=lambda **_kwargs: case["result"],
                match_context_loader=lambda _root: {
                    f"{EPL}:{row['event_id']}": _context(row["event_id"])
                    for row in case["rows"]
                },
                outbox_factory=_fail,
                notifier=_fail,
                **_full_flags(notify=True),
            )
            task7_state = json.loads(
                (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
            )
            pending = json.loads(
                (
                    Path(tmp)
                    / "data/local/leagues/lineup_refresh_pending.json"
                ).read_text(encoding="utf-8")
            )

            if case["valid"]:
                assert result["status"] == "publish_failed", case["name"]
                assert lineup_calls == ["called"], case["name"]
            else:
                assert result["status"] == "post_refresh_failed", case["name"]
                assert result["reason"] == "post_lineup_result_invalid", case["name"]
                assert lineup_calls == [], case["name"]
            assert result["notifications"] == [], case["name"]
            assert set(task7_state["receipts"]) == {
                _ack_token(row["ack_key"]) for row in case["rows"]
            }, case["name"]
            assert all(
                row["current_decision"] is None
                for row in task7_state["receipts"].values()
            ), case["name"]
            assert set(pending["events"]) == {
                f"{EPL}:{row['event_id']}" for row in case["rows"]
            }, case["name"]


def test_task5_failure_status_matrix_cannot_use_persisted_durable_ack_as_success():
    row = _receipt("failure-matrix", "a")
    for status in ("blocked", "refresh_failed", "publish_failed", "error"):
        with TemporaryDirectory() as tmp:
            _commit_published_task5_receipt(tmp, row)
            contradictory = _post_result(
                durable=(row,),
                status=status,
                publish_status=None,
            )
            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
                post_lineup_refresh_fn=lambda **_kwargs: contradictory,
                match_context_loader=lambda _root: {
                    f"{EPL}:failure-matrix": _context(
                        "failure-matrix", snapshot_id="persisted-snapshot"
                    )
                },
                outbox_factory=_fail,
                notifier=_fail,
                **_full_flags(notify=True),
            )
            state = json.loads(
                (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
            )

            assert result["status"] == "post_refresh_failed"
            assert result["reason"] == "post_lineup_result_invalid"
            assert result["notifications"] == []
            staged = next(iter(state["receipts"].values()))
            assert staged["current_decision"] is None


def test_task5_success_status_matrix_allows_already_acked_and_mixed_partial():
    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    already = _receipt("already-matrix", "b")
    with TemporaryDirectory() as tmp:
        _commit_published_task5_receipt(tmp, already)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(already),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(
                durable=(already,),
                status="already_acked",
                publish_status=None,
            ),
            match_context_loader=lambda _root: {
                f"{EPL}:already-matrix": _context(
                    "already-matrix", snapshot_id="persisted-snapshot"
                )
            },
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "already_acked"
        assert delivered[-1]["event_type"].startswith("published_refresh_")

    delivered.clear()
    durable = _receipt("partial-durable", "c")
    blocked = _receipt("partial-blocked", "d")
    with TemporaryDirectory() as tmp:
        _commit_published_task5_receipt(tmp, durable)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(durable, blocked),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(
                durable=(durable,),
                blocked=((blocked, "quota_below_minimum"),),
                status="partial",
                publish_status=None,
                receipt_count=2,
            ),
            match_context_loader=lambda _root: {
                f"{EPL}:partial-durable": _context(
                    "partial-durable", snapshot_id="persisted-snapshot"
                ),
                f"{EPL}:partial-blocked": _context("partial-blocked"),
            },
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "partial"
        assert {event["event_type"] for event in delivered} == {
            "published_refresh_unchanged",
            "quota_blocked",
        }


def test_real_task5_ack_state_commit_failure_remains_retryable_and_does_not_block_new_due():
    old = _receipt("ack-state-old", "1")
    new = _receipt("ack-state-new", "2")
    acceptance = {
        "schema_version": 1,
        "competitions": {
            EPL: {
                "competition_id": EPL,
                "state": "active",
                "reason": None,
                "fingerprints": {
                    name: f"{EPL}-{name}"
                    for name in (
                        "sport_catalog",
                        "odds_sample",
                        "team_identity",
                        "result_contract",
                    )
                },
            }
        },
    }
    registry = LeagueTeamIdentityRegistry({
        EPL: {
            "home": ("Home FC",),
            "away": ("Away FC",),
        }
    })
    calls = {"lineups": 0, "provider": 0, "publish": 0, "post": []}
    delivered = []

    class AckFailingStateStore:
        def __init__(self, root):
            self.delegate = PostLineupRefreshStateStore(root)
            self.commits = 0

        def read(self):
            return self.delegate.read()

        def claim_refresh(self, state):
            return self.delegate.claim_refresh(state)

        def commit(self, state):
            self.commits += 1
            if self.commits == 2:
                raise OSError("injected ack state commit failure")
            return self.delegate.commit(state)

    def snapshot_builder(payload, competition_id, observed_at, **_kwargs):
        return {
            "snapshot_at": observed_at,
            "competition": {"id": competition_id},
            "matches": [
                {
                    "source_event_id": str(row["id"]),
                    "competition": {"id": competition_id},
                    "match_decision": {"label": "MATCH_PICK"},
                }
                for row in payload
            ],
        }

    def provider(_sport_key, _selected_env):
        calls["provider"] += 1
        return [{"id": old["event_id"]}]

    def publisher(_snapshot):
        calls["publish"] += 1
        return {"status": "stored"}

    def post(**kwargs):
        rows = [
            row
            for competition_rows in kwargs["newly_confirmed"].values()
            for row in competition_rows
        ]
        calls["post"].append([row["event_id"] for row in rows])
        if rows[0]["event_id"] == new["event_id"]:
            return _post_result(
                blocked=((new, "acceptance_not_active"),),
                status="blocked",
                publish_status=None,
            )
        return run_post_lineup_refresh(
            **kwargs,
            env={"THE_ODDS_API_KEY_PRIMARY": "p" * 40},
            quota_ledger={
                "providers": {
                    "theoddsapi_primary": {
                        "remaining": 100,
                        "observed_at": NOW,
                    }
                }
            },
            acceptance_report=acceptance,
            identity_registry=registry,
            snapshot_builder=snapshot_builder,
            state_store_factory=AckFailingStateStore,
        )

    def lineups(**_kwargs):
        calls["lineups"] += 1
        return _lineup_result(new)

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        acceptance_path = Path(tmp) / "data/local/leagues/acceptance.json"
        acceptance_path.parent.mkdir(parents=True, exist_ok=True)
        acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
        _write_pending(tmp, old)
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lineups,
            post_lineup_refresh_fn=post,
            match_context_loader=lambda _root: {
                f"{EPL}:{old['event_id']}": _context(old["event_id"]),
                f"{EPL}:{new['event_id']}": _context(new["event_id"]),
            },
            odds_fetcher=provider,
            publish_fn=publisher,
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )
        pending = json.loads(
            (Path(tmp) / "data/local/leagues/lineup_refresh_pending.json").read_text(
                encoding="utf-8"
            )
        )
        task7_state = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )

        assert result["pending_retry"]["status"] == "publish_failed"
        assert result["pending_retry"]["acks"]["retryable"] == [{
            "ack_key": old["ack_key"],
            "reason": "ack_state_commit_failed",
        }]
        assert calls == {
            "lineups": 1,
            "provider": 1,
            "publish": 1,
            "post": [[old["event_id"]], [new["event_id"]]],
        }
        assert list(pending["events"]) == [f"{EPL}:{old['event_id']}"]
        old_job = task7_state["receipts"][_ack_token(old["ack_key"])]
        assert old_job["current_decision"] is None
        assert delivered == []


def test_real_task6_pending_recovery_is_superseded_before_relapse_delivery():
    sent = []
    recovery_available = False

    def notifier(content, **_kwargs):
        if "数据源已恢复" in content:
            if not recovery_available:
                return {"status": "failed", "exit_code": 1}
            sent.append("recovery")
            return {"status": "sent", "exit_code": 0}
        if "数据源连续失败" in content:
            sent.append("failure")
            return {"status": "sent", "exit_code": 0}
        raise AssertionError("unexpected notification type")

    with TemporaryDirectory() as tmp:
        common = {
            "root": tmp,
            "now": NOW,
            "post_lineup_refresh_fn": _fail,
            "match_context_loader": lambda _root: {
                f"{EPL}:causal": _context("causal")
            },
            "notifier": notifier,
            "source_failure_threshold": 1,
            **_full_flags(notify=True),
        }
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("causal", "failed"),
            **common,
        )
        recovery = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("causal", "succeeded"),
            **common,
        )
        pending_after_recovery = json.loads(
            (
                Path(tmp)
                / "data/local/leagues/lineup_notification_state.json"
            ).read_text(encoding="utf-8")
        )
        recovery_available = True
        relapsed = run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _source_result("causal", "failed"),
            **common,
        )
        run_league_pre_match(
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
            **common,
        )
        notification_state = json.loads(
            (
                Path(tmp)
                / "data/local/leagues/lineup_notification_state.json"
            ).read_text(encoding="utf-8")
        )
        task7_state = json.loads(
            (Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8")
        )
        episode = task7_state["source_episodes"][f"{EPL}:causal"]

        assert recovery["notifications"][-1]["status"] == "failed"
        assert len(pending_after_recovery["pending"]) == 1
        assert relapsed["notifications"] == []
        assert sent == ["failure"]
        assert notification_state["pending"] == {}
        assert {
            receipt["event_type"]
            for receipt in notification_state["sent"].values()
        } == {"sustained_source_failure"}
        assert episode["generation"] == 1
        assert episode["active"] is True
        assert episode["failure_notified"] is True
        assert episode["recovery_pending"] is False
        assert len(episode["failure_notification_fingerprint"]) == 64
        assert len(episode["recovery_notification_fingerprint"]) == 64


def test_malformed_task6_delivery_never_clears_the_receipt_context():
    row = _receipt("outbox-malformed", "6")

    class MalformedOutbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, _event, **_kwargs):
            return {"unexpected": {"raw_response": "not durable"}}

    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(durable=(row,)),
            match_context_loader=lambda _root: {
                f"{EPL}:outbox-malformed": _context("outbox-malformed")
            },
            outbox_factory=lambda _root: MalformedOutbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert result["status"] == "published"
        assert result["notifications"][0]["status"] == "failed"
        state = json.loads((Path(tmp) / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))
        assert len(state["receipts"]) == 1


def test_task4_pseudo_success_with_inconsistent_receipt_count_fails_closed():
    row = _receipt("count-mismatch", "7")
    malformed = _lineup_result(row)
    malformed["counts"]["newly_confirmed_count"] = 0

    with TemporaryDirectory() as tmp:
        result = run_league_pre_match(
            root=tmp,
            now=NOW,
            live_lineups=True,
            write_lineups=True,
            lineup_refresh_fn=lambda **_kwargs: malformed,
            post_lineup_refresh_fn=_fail,
        )

        assert result == {
            "status": "lineup_failed",
            "reason": "lineup_result_invalid",
            "lock": "acquired",
            "notifications": [],
        }


def test_task4_event_source_evidence_must_match_failure_and_request_counts():
    failed_without_failure = _lineup_result(status="polled")
    failed_without_failure["source_events"] = [{
        "competition_id": EPL,
        "event_id": "failed-without-count",
        "outcome": "failed",
    }]
    succeeded_without_request = _lineup_result(status="no_due")
    succeeded_without_request["source_events"] = [{
        "competition_id": EPL,
        "event_id": "success-without-request",
        "outcome": "succeeded",
    }]

    for malformed in (failed_without_failure, succeeded_without_request):
        with TemporaryDirectory() as tmp:
            result = run_league_pre_match(
                root=tmp,
                now=NOW,
                live_lineups=True,
                write_lineups=True,
                lineup_refresh_fn=lambda **_kwargs: malformed,
                post_lineup_refresh_fn=_fail,
            )

            assert result["status"] == "lineup_failed"
            assert result["reason"] == "lineup_result_invalid"


def test_restart_binds_unfinished_receipt_from_exact_history_not_newer_current_snapshot():
    row = _receipt("history-binding", "8")
    current_selection = "home"
    current_snapshot_id = "initial-snapshot"

    class FailSecondCommit:
        def __init__(self, root):
            self.real = LeaguePreMatchStateStore(root)
            self.commits = 0

        def read(self):
            return self.real.read()

        def commit(self, state):
            self.commits += 1
            if self.commits == 2:
                raise OSError("crash before receipt binding")
            return self.real.commit(state)

    def contexts(_root):
        return {
            f"{EPL}:history-binding": _context(
                "history-binding",
                current_selection,
                snapshot_id=current_snapshot_id,
            )
        }

    def first_post(**_kwargs):
        nonlocal current_selection, current_snapshot_id
        current_selection = "away"
        current_snapshot_id = "old-snapshot"
        return _post_result(
            durable=(row,),
            component_snapshot_id="old-snapshot",
            aggregate_snapshot_id="old-aggregate",
        )

    delivered = []

    class Outbox:
        def retry_pending(self, **_kwargs):
            return {"status": "complete", "sent": 0, "failed": 0}

        def deliver(self, event, **_kwargs):
            delivered.append(event)
            return {
                "status": "sent",
                "event_fingerprint": event["event_fingerprint"],
            }

    with TemporaryDirectory() as tmp:
        first = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(row),
            post_lineup_refresh_fn=first_post,
            match_context_loader=contexts,
            state_store_factory=lambda root: FailSecondCommit(root),
            outbox_factory=_fail,
            notifier=_fail,
            **_full_flags(notify=True),
        )
        assert first["status"] == "state_failed"

        PostLineupRefreshStateStore(tmp).commit({
            "schema_version": 1,
            "receipts": {
                _ack_token(row["ack_key"]): {
                    "ack_key": row["ack_key"],
                    "phase": "published",
                    "snapshot_id": "old-snapshot",
                    "aggregate_snapshot_id": "old-aggregate",
                    "publish_status": "stored",
                }
            },
        })
        history = Path(tmp) / f"data/local/leagues/{EPL}/history/old-snapshot.json"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(
            json.dumps({
                "snapshot_id": "old-snapshot",
                "competition": {"id": EPL},
                "matches": [{
                    "source_event_id": "history-binding",
                    "home_team": "Home FC",
                    "away_team": "Away FC",
                    "kickoff_at_utc": "2026-08-24T12:40:00+00:00",
                    "fixture_status": "SCHEDULED",
                    "match_decision": _decision("away"),
                }],
            }),
            encoding="utf-8",
        )
        current_selection = "draw"
        current_snapshot_id = "newer-snapshot"

        restarted = run_league_pre_match(
            root=tmp,
            now=NOW,
            lineup_refresh_fn=lambda **_kwargs: _lineup_result(status="no_due"),
            post_lineup_refresh_fn=lambda **_kwargs: _post_result(
                durable=(row,),
                status="already_acked",
                publish_status=None,
            ),
            match_context_loader=contexts,
            outbox_factory=lambda _root: Outbox(),
            notifier=_fail,
            **_full_flags(notify=True),
        )

        assert restarted["status"] == "already_acked"
        assert delivered[0]["payload"]["previous_decision"]["selection"] == "home"
        assert delivered[0]["payload"]["current_decision"]["selection"] == "away"
