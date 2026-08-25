import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_postmatch_notifications import (
    LeaguePostmatchNotificationOutbox,
    build_daily_settlement_event,
    build_threshold_events,
    render_postmatch_notification,
)


AGGREGATE_FINGERPRINT = "a" * 64


def _competitions():
    return {
        "epl_2026_27": {
            "newly_settled": 2,
            "hit": 2,
            "miss": 1,
            "push": 0,
            "no_pick": 0,
            "missing_closing": 1,
            "decided": 3,
        }
    }


def _event():
    event = build_daily_settlement_event(
        settlement_date="2026-08-29",
        newly_settled=3,
        competitions=_competitions(),
        aggregate_fingerprint=AGGREGATE_FINGERPRINT,
    )
    assert event is not None
    return event


def _fails_once():
    attempts = []

    def sender(*_args, **_kwargs):
        attempts.append(True)
        return {"status": "failed", "raw_provider_response": "secret UID"} if len(attempts) == 1 else {
            "status": "sent", "provider_response": "raw header token"
        }

    return sender


def test_daily_digest_exists_only_for_new_settlements_and_renders_safe_league_counts():
    """A zero-settlement run must not create a digest that looks like new evidence."""
    kwargs = {
        "settlement_date": "2026-08-29",
        "competitions": _competitions(),
        "aggregate_fingerprint": AGGREGATE_FINGERPRINT,
    }

    assert build_daily_settlement_event(newly_settled=0, **kwargs) is None
    event = build_daily_settlement_event(newly_settled=3, **kwargs)
    assert event is not None
    rendered = render_postmatch_notification(event)

    assert "英超 2026/27" in rendered["content"]
    assert "命中 2｜未命中 1｜走水 0｜无首选 0" in rendered["content"]
    assert "hit/miss/push/no_pick" not in rendered["content"]
    assert "不构成投注建议" in rendered["content"]
    serialized = json.dumps([event, rendered], ensure_ascii=False)
    for forbidden in ("金额", "stake", "EV", "Edge", "edge", "raw", "secret", "token", "执行"):
        assert forbidden not in serialized


def test_threshold_events_cross_each_unsent_boundary_once_with_deterministic_identity():
    """Dropping a crossed threshold or changing its identity would duplicate milestone notices."""
    first = build_threshold_events(
        previous_decided=19,
        current_decided=101,
        sent_thresholds={50},
        aggregate_fingerprint=AGGREGATE_FINGERPRINT,
    )
    repeated = build_threshold_events(
        previous_decided=19,
        current_decided=101,
        sent_thresholds={50},
        aggregate_fingerprint=AGGREGATE_FINGERPRINT,
    )

    assert [event["payload"]["threshold"] for event in first] == [20, 100]
    assert [event["event_fingerprint"] for event in first] == [event["event_fingerprint"] for event in repeated]
    rendered = render_postmatch_notification(first[0])
    assert "20 场" in rendered["content"]
    assert "不构成投注建议" in rendered["content"]

    with TemporaryDirectory() as tmp:
        outbox = LeaguePostmatchNotificationOutbox(
            Path(tmp) / "outbox.json",
            notifier=lambda *_args, **_kwargs: {"status": "sent"},
        )
        assert outbox.deliver(first[0])["status"] == "sent"
        assert outbox.sent_thresholds() == {20}


def test_failed_delivery_remains_pending_without_duplicate_sent_receipt():
    """Marking sent before a successful sender response would lose recoverable notification intent."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.json"
        outbox = LeaguePostmatchNotificationOutbox(path, notifier=_fails_once())

        assert outbox.deliver(_event())["status"] == "failed"
        assert outbox.retry_pending()["sent"] == 1
        assert outbox.deliver(_event())["status"] == "already_sent"

        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["pending"] == {}
        assert list(state["sent"]) == [_event()["event_fingerprint"]]
        assert "secret" not in json.dumps(state)
        assert "raw_provider_response" not in json.dumps(state)


def test_pending_event_survives_restart_and_notifier_output_is_never_persisted():
    """A process crash after intent persistence must be recoverable without storing provider output."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.json"
        event = _event()
        LeaguePostmatchNotificationOutbox(
            path,
            notifier=lambda *_args, **_kwargs: {"status": "failed", "raw_log": "Cookie=secret"},
        ).deliver(event)

        calls = []
        restarted = LeaguePostmatchNotificationOutbox(
            path,
            notifier=lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "sent", "uid": "secret"},
        )
        assert restarted.retry_pending() == {"status": "complete", "sent": 1, "failed": 0}
        persisted = path.read_text(encoding="utf-8")
        assert calls == [((render_postmatch_notification(event)["content"],), {"summary": render_postmatch_notification(event)["summary"]})]
        for forbidden in ("Cookie", "secret", "raw_log", "uid"):
            assert forbidden not in persisted


def test_malformed_state_and_sensitive_competition_fields_fail_closed_before_sender():
    """A malformed durable receipt or unapproved metric must never reach the sender."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.json"
        path.write_text('{"schema_version":1,"pending":[],"sent":{}}\n', encoding="utf-8")
        calls = []
        outbox = LeaguePostmatchNotificationOutbox(
            path,
            notifier=lambda *_args, **_kwargs: calls.append(True) or {"status": "sent"},
        )
        try:
            outbox.deliver(_event())
        except ValueError as exc:
            assert str(exc) == "league_postmatch_notification_state_invalid"
        else:
            raise AssertionError("malformed outbox state was accepted")
        assert calls == []

    invalid_competitions = _competitions()
    invalid_competitions["epl_2026_27"]["EV"] = 0.5
    try:
        build_daily_settlement_event(
            settlement_date="2026-08-29",
            newly_settled=3,
            competitions=invalid_competitions,
            aggregate_fingerprint=AGGREGATE_FINGERPRINT,
        )
    except ValueError as exc:
        assert str(exc) == "league_postmatch_notification_event_invalid"
    else:
        raise AssertionError("sensitive metric was accepted")


def test_dry_run_validates_without_persisting_or_calling_sender():
    """Dry runs must prove safe notification shape without creating operational state."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.json"
        calls = []
        result = LeaguePostmatchNotificationOutbox(
            path,
            notifier=lambda *_args, **_kwargs: calls.append(True) or {"status": "sent"},
        ).deliver(_event(), dry_run=True)

        assert result == {"status": "dry_run", "event_fingerprint": _event()["event_fingerprint"]}
        assert calls == []
        assert not path.exists()


def test_atomic_intent_write_failure_never_calls_sender_or_leaves_partial_state():
    """Sending before an atomic pending write would make a crash unrecoverable."""
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "outbox.json"
        calls = []
        outbox = LeaguePostmatchNotificationOutbox(
            path,
            notifier=lambda *_args, **_kwargs: calls.append(True) or {"status": "sent"},
        )
        import worldcup.league_postmatch_notifications as notifications

        original_replace = notifications.os.replace
        notifications.os.replace = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace failed"))
        try:
            try:
                outbox.deliver(_event())
            except OSError as exc:
                assert str(exc) == "replace failed"
            else:
                raise AssertionError("atomic write failure was accepted")
        finally:
            notifications.os.replace = original_replace

        assert calls == []
        assert not path.exists()
        assert list(path.parent.glob(".outbox.json.*.tmp")) == []
