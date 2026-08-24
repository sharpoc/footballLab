from __future__ import annotations

import fcntl
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_lineup_notifications import LeagueLineupNotificationOutbox
from worldcup.league_pre_match_runner import (
    DEFAULT_LOCK_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    run_league_pre_match,
)


NOW = "2026-08-24T12:00:00+00:00"
EPL = "epl_2026_27"


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
                "snapshot_id": "league-aggregate-test",
                "run_id": "league-aggregate-test",
                "components": [{"competition_id": EPL, "snapshot_id": "league-test"}],
            },
        }
    return {
        "status": status,
        "plan": {"competition_ids": [EPL], "receipt_count": 1},
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


def _context(event_id: str, selection: str = "home") -> dict:
    return {
        "competition_id": EPL,
        "event_id": event_id,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_at_utc": "2026-08-24T12:40:00+00:00",
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
    recovered = _lineup_result(status="polled")
    recovered["counts"]["fixture_count"] = 1
    recovered["counts"]["request_count"] = 1

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
