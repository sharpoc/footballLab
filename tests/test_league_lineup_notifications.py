from __future__ import annotations

import hashlib
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
    render_notification_event,
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
            "schema_version": 2,
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
            "schema_version": 2,
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": current_selection,
            "p_hit_safe": 0.62,
            "odds": 1.88,
            "edge": 0.50,
            "legacy_grade": "A",
        },
    )


def _build_with_current_decision(current_decision):
    return build_published_refresh_event(
        **MATCH,
        lineup_fingerprint=LINEUP_FINGERPRINT,
        confirmed_at="2026-08-24T18:05:00+00:00",
        publish_status="stored",
        previous_decision={
            "schema_version": 2,
            "label": "MATCH_PICK",
            "market": "1X2",
            "selection": "home",
            "line": None,
            "p_hit_safe": 0.55,
            "odds": 2.10,
        },
        current_decision=current_decision,
    )


def _state_path(root: Path) -> Path:
    return root / "data/local/leagues/lineup_notification_state.json"


def _render(event):
    return render_notification_event(event)


def test_published_refresh_changed_message_is_safe_and_uses_beijing_time():
    event = _published_event()
    rendered = _render(event)

    assert event is not None
    assert set(event) == {"schema_version", "event_type", "event_fingerprint", "payload"}
    assert event["event_type"] == "published_refresh_changed"
    assert rendered["summary"] == "意甲 2026/27首发后本场首选已更新"
    assert "Inter Milan vs AC Milan" in rendered["content"]
    assert "2026-08-25 02:45（北京时间）" in rendered["content"]
    assert "胜平负 - 主队 → 胜平负 - 客队" in rendered["content"]
    assert "新安全概率：62.0%" in rendered["content"]
    assert "新参考赔率：1.88" in rendered["content"]
    assert "仅供研究分析，不构成投注建议。" in rendered["content"]
    serialized = json.dumps([event, rendered], ensure_ascii=False)
    for forbidden in ("金额", "EV", "Edge", "edge", "grade", "legacy", "raw", "header", "secret", "UID"):
        assert forbidden not in serialized


def test_published_refresh_unchanged_has_explicit_wording_and_stable_fingerprint():
    first = _published_event(current_selection="home")
    second = _published_event(current_selection="home")

    assert first is not None
    assert first["event_type"] == "published_refresh_unchanged"
    assert "首发后复核：方向未变" in _render(first)["content"]
    assert first["event_fingerprint"] == second["event_fingerprint"]


def test_published_event_accepts_only_v2_market_pick_contracts():
    valid = (
        {"schema_version": 2, "label": "MATCH_PICK", "market": "1X2", "selection": "draw", "line": None, "p_hit_safe": 0.5, "odds": 2.0},
        {"schema_version": 2, "label": "MATCH_PICK", "market": "DNB", "selection": "home", "line": 0.0, "p_hit_safe": 0.0, "odds": 1.01},
        {"schema_version": 2, "label": "MATCH_PICK", "market": "AH", "selection": "away", "line": 0.25, "p_hit_safe": 1.0, "odds": 3.5},
        {"schema_version": 2, "label": "MATCH_PICK", "market": "OU", "selection": "over", "line": 2.5, "p_hit_safe": 0.61, "odds": 1.88},
        {"schema_version": 2, "label": "NO_CLEAN_MARKET"},
    )

    for decision in valid:
        event = _build_with_current_decision(decision)
        assert event is not None
        assert _render(event)["content"]


def test_published_event_rejects_legacy_unknown_and_malformed_pick_contracts():
    base = {
        "schema_version": 2,
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "away",
        "line": None,
        "p_hit_safe": 0.62,
        "odds": 1.88,
    }
    invalid = (
        {**base, "schema_version": 1},
        {**base, "label": "LEGACY_S"},
        {**base, "label": ""},
        {**base, "label": None},
        {**base, "label": "UNKNOWN"},
        {**base, "market": ["1X2"]},
        {**base, "market": "UNKNOWN"},
        {**base, "selection": ["away"]},
        {**base, "selection": "over"},
        {**base, "line": 0.0},
        {**base, "p_hit_safe": -0.01},
        {**base, "p_hit_safe": 1.01},
        {**base, "p_hit_safe": float("nan")},
        {**base, "p_hit_safe": float("inf")},
        {**base, "odds": -9},
        {**base, "odds": 0},
        {**base, "odds": 1.0},
        {**base, "odds": float("nan")},
        {**base, "odds": float("inf")},
        {**base, "market": "DNB", "selection": "draw", "line": 0.0},
        {**base, "market": "DNB", "selection": "home", "line": 0.25},
        {**base, "market": "AH", "selection": "home", "line": None},
        {**base, "market": "AH", "selection": "home", "line": 0.0},
        {**base, "market": "OU", "selection": "under", "line": None},
        {**base, "market": "OU", "selection": "under", "line": -2.5},
        {
            "schema_version": 2,
            "label": "NO_CLEAN_MARKET",
            "market": "1X2",
            "selection": "home",
            "p_hit_safe": 0.6,
            "odds": 2.0,
        },
    )

    for decision in invalid:
        try:
            _build_with_current_decision(decision)
        except ValueError as exc:
            assert str(exc) == "league_lineup_notification_event_invalid"
        else:
            raise AssertionError(f"invalid decision was accepted: {decision!r}")


def test_negative_zero_dnb_line_is_canonical_positive_zero():
    positive = _build_with_current_decision(
        {"schema_version": 2, "label": "MATCH_PICK", "market": "DNB", "selection": "home", "line": 0.0, "p_hit_safe": 0.55, "odds": 1.88}
    )
    negative = _build_with_current_decision(
        {"schema_version": 2, "label": "MATCH_PICK", "market": "DNB", "selection": "home", "line": -0.0, "p_hit_safe": 0.55, "odds": 1.88}
    )

    assert positive["event_fingerprint"] == negative["event_fingerprint"]
    assert negative["payload"]["current_decision"]["line"] == 0.0
    assert "-0.0" not in json.dumps(negative)


def test_missing_retry_does_not_suppress_later_unchanged_confirmed_update():
    with TemporaryDirectory() as tmp:
        event = build_missing_lineup_event(**MATCH, source_fingerprint="missing")
        assert LeagueLineupNotificationOutbox(tmp).deliver(
            event, notify=True, notifier=lambda *_a, **_k: {"status": "failed"},
        )["status"] == "failed"
        sent = []

        def notifier(content, **_kwargs):
            sent.append(content)
            return {"status": "sent"}

        outbox = LeagueLineupNotificationOutbox(tmp)
        assert outbox.retry_pending(notify=True, notifier=notifier)["sent"] == 1
        assert outbox.deliver(event, notify=True, notifier=notifier)["status"] == "already_sent"
        updated = _published_event(current_selection="home")
        assert outbox.deliver(updated, notify=True, notifier=notifier)["status"] == "sent"
        assert LeagueLineupNotificationOutbox(tmp).deliver(updated, notify=True, notifier=notifier)["status"] == "already_sent"
        assert len(sent) == 2
        assert "尚未获取正式首发" in sent[0]
        assert "已获取正式首发" in sent[1]
        assert "方向未变" in sent[1]


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

    assert "尚未获取正式首发" in _render(missing)["content"]
    assert "未完成首发后复核" in _render(missing)["content"]
    assert "旧推荐及赔率有效性未验证" in _render(missing)["content"]
    assert "官方尚未公布" not in _render(missing)["content"]
    assert "保留原推荐" not in _render(missing)["content"]
    assert "首发已保存，赔率刷新被额度保护阻断，保留原推荐" in _render(quota)["content"]
    assert "首发数据源连续失败（3 次）" in _render(failed)["content"]
    assert "首发数据源已恢复" in _render(recovered)["content"]
    assert missing["event_fingerprint"] == build_missing_lineup_event(**common)["event_fingerprint"]
    assert len(
        {
            missing["event_fingerprint"],
            quota["event_fingerprint"],
            failed["event_fingerprint"],
            recovered["event_fingerprint"],
        }
    ) == 4
    serialized = json.dumps([failed, recovered, _render(failed), _render(recovered)], ensure_ascii=False)
    for forbidden in ("Authorization", "header", "secret", "UID", "provider response", "raw", "Cookie"):
        assert forbidden not in serialized


def test_sustained_failure_count_growth_is_one_business_event_and_one_send():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        common = {
            **MATCH,
            "source_fingerprint": "failure-episode-1",
        }
        events = [
            build_source_failure_event(**common, failure_count=count)
            for count in (3, 4, 5)
        ]
        calls = []
        outbox = LeagueLineupNotificationOutbox(root)
        statuses = [
            outbox.deliver(
                event,
                notify=True,
                notifier=lambda *_args, **_kwargs: (
                    calls.append(True) or {"status": "sent", "exit_code": 0}
                ),
            )["status"]
            for event in events
        ]

        assert len({event["event_fingerprint"] for event in events}) == 1
        assert statuses == ["sent", "already_sent", "already_sent"]
        assert calls == [True]
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
        assert len(state["sent"]) == 1
        assert state["pending"] == {}


def test_failed_failure_event_retries_same_pending_when_count_grows():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        common = {
            **MATCH,
            "source_fingerprint": "failure-episode-retry",
        }
        count_three = build_source_failure_event(**common, failure_count=3)
        count_four = build_source_failure_event(**common, failure_count=4)
        outbox = LeagueLineupNotificationOutbox(root)
        calls = []

        first = outbox.deliver(
            count_three,
            notify=True,
            notifier=lambda *_args, **_kwargs: (
                calls.append("failed") or {"status": "failed", "exit_code": 9}
            ),
        )
        pending = json.loads(_state_path(root).read_text(encoding="utf-8"))
        second = outbox.deliver(
            count_four,
            notify=True,
            notifier=lambda *_args, **_kwargs: (
                calls.append("sent") or {"status": "sent", "exit_code": 0}
            ),
        )
        final_state = json.loads(_state_path(root).read_text(encoding="utf-8"))

        assert count_three["event_fingerprint"] == count_four["event_fingerprint"]
        assert first["status"] == "failed"
        assert len(pending["pending"]) == 1
        assert second["status"] == "sent"
        assert calls == ["failed", "sent"]
        assert final_state["pending"] == {}
        assert len(final_state["sent"]) == 1


def test_new_failure_episode_and_recovery_each_have_independent_dedupe_identity():
    common = {**MATCH, "failure_count": 3}
    first = build_source_failure_event(**common, source_fingerprint="failure-episode-1")
    second = build_source_failure_event(**common, source_fingerprint="failure-episode-2")
    recovery_one = build_source_recovery_event(
        **MATCH,
        source_fingerprint="failure-episode-1",
    )
    recovery_repeat = build_source_recovery_event(
        **MATCH,
        source_fingerprint="failure-episode-1",
    )

    assert first["event_fingerprint"] != second["event_fingerprint"]
    assert first["event_fingerprint"] != recovery_one["event_fingerprint"]
    assert recovery_one["event_fingerprint"] == recovery_repeat["event_fingerprint"]


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
        rendered = _render(event)
        assert calls[0][0] == (rendered["content"],)
        assert calls[0][1] == {"summary": rendered["summary"]}
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
        rendered = _render(event)
        assert calls == [((rendered["content"],), {"summary": rendered["summary"]})]
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


def test_forged_message_or_payload_mismatch_fails_before_notifier():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        event = _published_event()
        calls = []
        forged_message = {**event, "summary": "下注金额 100", "content": "EV provider response raw header"}
        forged_payload = {
            **event,
            "payload": {**event["payload"], "home_team": "Forged Team"},
        }

        for forged in (forged_message, forged_payload):
            try:
                LeagueLineupNotificationOutbox(root).deliver(
                    forged,
                    notify=True,
                    notifier=lambda *_args, **_kwargs: calls.append(True),
                )
            except ValueError as exc:
                assert str(exc) == "league_lineup_notification_event_invalid"
            else:
                raise AssertionError("forged event was accepted")

        assert calls == []
        assert not _state_path(root).exists()


def test_forged_shape_valid_pending_state_fails_before_retry_notifier():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        event = _published_event()
        fingerprint = event["event_fingerprint"]
        forged = {**event, "content": "下注金额 100；EV；provider response；raw header"}
        path = _state_path(root)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pending": {fingerprint: forged},
                    "sent": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        calls = []

        try:
            LeagueLineupNotificationOutbox(root).retry_pending(
                notify=True,
                notifier=lambda *_args, **_kwargs: calls.append(True),
            )
        except ValueError as exc:
            assert str(exc) == "league_lineup_notification_state_invalid"
        else:
            raise AssertionError("forged pending state was accepted")

        assert calls == []


def test_sensitive_display_payload_fails_even_with_recomputed_fingerprint():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        event = build_missing_lineup_event(
            **MATCH,
            source_fingerprint="episode-sensitive",
        )
        payload = {
            **event["payload"],
            "home_team": "下注金额 EV provider_response raw header",
        }
        encoded = json.dumps(
            {"event_type": event["event_type"], "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        forged = {**event, "event_fingerprint": fingerprint, "payload": payload}
        path = _state_path(root)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {"schema_version": 1, "pending": {fingerprint: forged}, "sent": {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        calls = []

        try:
            LeagueLineupNotificationOutbox(root).retry_pending(
                notify=True,
                notifier=lambda *_args, **_kwargs: calls.append(True),
            )
        except ValueError as exc:
            assert str(exc) == "league_lineup_notification_state_invalid"
        else:
            raise AssertionError("sensitive display payload was accepted")

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
