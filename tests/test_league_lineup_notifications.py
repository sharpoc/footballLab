from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_lineup_notifications import (
    LeagueLineupNotificationOutbox,
    build_missing_lineup_event,
    build_published_refresh_event,
    build_quota_blocked_event,
    build_source_failure_event,
    build_source_recovery_event,
)


MATCH = {
    "competition_id": "serie_a_2026_27",
    "event_id": "serie-a-001",
    "home_team": "Inter Milan",
    "away_team": "AC Milan",
    "kickoff_at_utc": "2026-08-24T18:45:00+00:00",
}
LINEUP_FINGERPRINT = "a" * 64


def _published_event(*, publish_status="stored", current_selection="away"):
    return build_published_refresh_event(
        **MATCH,
        lineup_fingerprint=LINEUP_FINGERPRINT,
        confirmed_at="2026-08-24T18:05:00+00:00",
        publish_status=publish_status,
        previous_decision={
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": "home",
            "p_hit_safe": 0.55,
            "odds": 2.10,
            "ev": 0.99,
            "grade": "S",
            "raw_provider_response": "secret UID and header",
        },
        current_decision={
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": current_selection,
            "p_hit_safe": 0.62,
            "odds": 1.88,
            "edge": 0.50,
            "legacy_grade": "A",
        },
    )


def _state_path(root: Path) -> Path:
    return root / "data/local/leagues/lineup_notification_state.json"


def test_published_refresh_changed_message_is_safe_and_uses_beijing_time():
    event = _published_event()

    assert event is not None
    assert event["event_type"] == "published_refresh_changed"
    assert event["summary"] == "意甲 2026/27首发后本场首选已更新"
    assert "Inter Milan vs AC Milan" in event["content"]
    assert "2026-08-25 02:45（北京时间）" in event["content"]
    assert "胜平负 - 主队 → 胜平负 - 客队" in event["content"]
    assert "新安全概率：62.0%" in event["content"]
    assert "新参考赔率：1.88" in event["content"]
    assert "仅供研究分析，不构成投注建议。" in event["content"]
    serialized = json.dumps(event, ensure_ascii=False)
    for forbidden in ("金额", "EV", "Edge", "edge", "grade", "legacy", "raw", "header", "secret", "UID"):
        assert forbidden not in serialized


def test_published_refresh_unchanged_has_explicit_wording_and_stable_fingerprint():
    first = _published_event(current_selection="home")
    second = _published_event(current_selection="home")

    assert first is not None
    assert first["event_type"] == "published_refresh_unchanged"
    assert "首发后复核：方向未变" in first["content"]
    assert first["event_fingerprint"] == second["event_fingerprint"]


def test_publish_failure_never_creates_a_success_event():
    for publish_status in ("failed", "pending", None):
        assert _published_event(publish_status=publish_status) is None


def test_degraded_and_recovery_events_are_safe_and_deduplicated_by_episode():
    common = {
        **MATCH,
        "source_fingerprint": "episode-20260824T180000Z",
    }
    missing = build_missing_lineup_event(**common)
    quota = build_quota_blocked_event(**common)
    failed = build_source_failure_event(
        **common,
        failure_count=3,
        error_details="Authorization header leaked secret UID",
    )
    recovered = build_source_recovery_event(
        **common,
        error_details="provider response had raw Cookie",
    )

    assert "首发未确认，保留原推荐" in missing["content"]
    assert "首发已保存，赔率刷新被额度保护阻断，保留原推荐" in quota["content"]
    assert "首发数据源连续失败（3 次）" in failed["content"]
    assert "首发数据源已恢复" in recovered["content"]
    assert missing["event_fingerprint"] == build_missing_lineup_event(**common)["event_fingerprint"]
    assert len(
        {
            missing["event_fingerprint"],
            quota["event_fingerprint"],
            failed["event_fingerprint"],
            recovered["event_fingerprint"],
        }
    ) == 4
    serialized = json.dumps([failed, recovered], ensure_ascii=False)
    for forbidden in ("Authorization", "header", "secret", "UID", "provider response", "raw", "Cookie"):
        assert forbidden not in serialized


def test_dry_run_and_sent_duplicate_never_call_notifier_or_write():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        outbox = LeagueLineupNotificationOutbox(root)
        event = _published_event()
        calls = []

        def notifier(*args, **kwargs):
            calls.append((args, kwargs))
            return {"status": "sent", "exit_code": 0}

        dry_run = outbox.deliver(event, notify=False, notifier=notifier)

        assert dry_run == {"status": "dry_run", "event_fingerprint": event["event_fingerprint"]}
        assert calls == []
        assert not _state_path(root).exists()

        sent = outbox.deliver(event, notify=True, notifier=notifier)
        duplicate = outbox.deliver(event, notify=True, notifier=notifier)

        assert sent["status"] == "sent"
        assert duplicate == {"status": "already_sent", "event_fingerprint": event["event_fingerprint"]}
        assert len(calls) == 1
        assert calls[0][0] == (event["content"],)
        assert calls[0][1] == {"summary": event["summary"]}
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
        assert state["pending"] == {}
        assert list(state["sent"]) == [event["event_fingerprint"]]
        assert "exit_code" not in json.dumps(state)


def test_failed_or_exception_send_is_retained_and_retried_after_restart():
    for raises in (False, True):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            event = _published_event()
            outbox = LeagueLineupNotificationOutbox(root)

            def failing_notifier(*_args, **_kwargs):
                if raises:
                    raise RuntimeError("provider response contains UID_secret")
                return {
                    "status": "failed",
                    "exit_code": 9,
                    "provider_response": "raw UID_secret",
                }

            failed = outbox.deliver(event, notify=True, notifier=failing_notifier)
            persisted = json.loads(_state_path(root).read_text(encoding="utf-8"))

            assert failed == {"status": "failed", "event_fingerprint": event["event_fingerprint"]}
            assert list(persisted["pending"]) == [event["event_fingerprint"]]
            assert persisted["sent"] == {}
            assert "UID_secret" not in json.dumps(persisted)
            assert "provider_response" not in json.dumps(persisted)

            restarted = LeagueLineupNotificationOutbox(root)
            delivered = restarted.deliver(
                event,
                notify=True,
                notifier=lambda *_args, **_kwargs: {"status": "sent", "exit_code": 0},
            )
            final_state = json.loads(_state_path(root).read_text(encoding="utf-8"))

            assert delivered["status"] == "sent"
            assert final_state["pending"] == {}
            assert list(final_state["sent"]) == [event["event_fingerprint"]]


def test_restart_can_retry_pending_without_rebuilding_the_source_event():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        event = _published_event()
        LeagueLineupNotificationOutbox(root).deliver(
            event,
            notify=True,
            notifier=lambda *_args, **_kwargs: {"status": "failed", "exit_code": 7},
        )
        calls = []
        restarted = LeagueLineupNotificationOutbox(root)

        dry_run = restarted.retry_pending(
            notify=False,
            notifier=lambda *_args, **_kwargs: calls.append(True),
        )
        result = restarted.retry_pending(
            notify=True,
            notifier=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or {"status": "sent", "exit_code": 0}
            ),
        )

        assert dry_run == {"status": "dry_run", "pending": 1}
        assert result == {"status": "complete", "sent": 1, "failed": 0}
        assert calls == [((event["content"],), {"summary": event["summary"]})]
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
        assert state["pending"] == {}
        assert list(state["sent"]) == [event["event_fingerprint"]]


def test_malformed_state_fails_closed_without_calling_notifier():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = _state_path(root)
        path.parent.mkdir(parents=True)
        path.write_text('{"schema_version":1,"pending":[],"sent":{}}\n', encoding="utf-8")
        calls = []

        try:
            LeagueLineupNotificationOutbox(root).deliver(
                _published_event(),
                notify=True,
                notifier=lambda *_args, **_kwargs: calls.append(True),
            )
        except ValueError as exc:
            assert str(exc) == "league_lineup_notification_state_invalid"
        else:
            raise AssertionError("malformed state was accepted")

        assert calls == []


def test_skipped_publish_path_does_not_call_notifier_or_write():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        event = _published_event(publish_status="failed")
        calls = []

        assert event is None
        assert LeagueLineupNotificationOutbox(root).deliver(
            event,
            notify=True,
            notifier=lambda *_args, **_kwargs: calls.append(True),
        ) == {"status": "skipped", "event_fingerprint": None}
        assert calls == []
        assert not _state_path(root).exists()
