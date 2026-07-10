from copy import deepcopy

from worldcup.notifications import build_change_notification, send_wxpusher_notification


def _snapshot():
    return {
        "snapshot_at": "2026-06-09T08:00:00+00:00",
        "run": {"run_id": "20260609T080000Z-live"},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "market": {
                    "1x2": {
                        "market_probs": {"home": 0.57, "draw": 0.25, "away": 0.18},
                        "odds": {"home": 2.0, "draw": 3.3, "away": 4.0},
                    }
                },
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "A",
                        "ev": 0.052,
                        "edge": 0.041,
                        "status": "OK",
                    }
                ],
                "match_decision": {
                    "schema_version": 2,
                    "label": "MATCH_PICK",
                    "market": "1X2",
                    "selection": "home",
                    "odds": 2.0,
                    "p_hit_safe": 0.59,
                    "p_no_loss_safe": 0.59,
                },
            }
        ],
    }


def test_build_change_notification_formats_significant_match_updates():
    previous = _snapshot()
    current = deepcopy(previous)
    current["snapshot_at"] = "2026-06-09T10:00:00+00:00"
    current["run"]["run_id"] = "20260609T100000Z-live"
    current["matches"][0]["market"]["1x2"]["odds"]["home"] = 1.85
    current["matches"][0]["market"]["1x2"]["market_probs"]["home"] = 0.54
    current["matches"][0]["signals"][0]["grade"] = "S"  # ignored legacy payload
    current["matches"][0]["match_decision"]["odds"] = 1.85
    current["matches"][0]["match_decision"]["p_hit_safe"] = 0.63

    notification = build_change_notification(previous, current, limit=5)

    assert notification["should_send"] is True
    assert notification["summary"] == "世界杯本场首选更新：1 场变化"
    assert "墨西哥 对 南非" in notification["content"]
    assert "安全命中率 59.0% → 63.0%" in notification["content"]
    assert "参考赔率 2.00 → 1.85" in notification["content"]
    assert "等级" not in notification["content"]
    assert "EV" not in notification["content"]
    assert "20260609T100000Z-live" in notification["content"]


def test_build_change_notification_skips_no_significant_changes():
    previous = _snapshot()
    current = deepcopy(previous)
    current["snapshot_at"] = "2026-06-09T10:00:00+00:00"

    notification = build_change_notification(previous, current)

    assert notification["should_send"] is False
    assert notification["content"] == ""


def test_send_wxpusher_notification_redacts_command_output():
    calls = []

    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))

        class Result:
            returncode = 0
            stdout = '{"uid":"UID_secret","url":"https://example.invalid/message"}'
            stderr = ""

        return Result()

    result = send_wxpusher_notification(
        "世界杯本场首选更新",
        summary="世界杯本场首选更新：1 场变化",
        runner=runner,
    )

    assert result == {"status": "sent", "exit_code": 0}
    assert calls[0][0][0].endswith("wxpusher-remind")
    assert calls[0][0][1:] == [
        "--summary",
        "世界杯本场首选更新：1 场变化",
        "世界杯本场首选更新",
    ]
    assert "UID_secret" not in str(result)
    assert "example.invalid" not in str(result)


def test_send_wxpusher_notification_returns_failed_when_command_errors():
    def runner(*_args, **_kwargs):
        raise OSError("missing command with UID_secret in message")

    result = send_wxpusher_notification(
        "世界杯本场首选更新",
        summary="世界杯本场首选更新：1 场变化",
        runner=runner,
    )

    assert result == {"status": "failed", "exit_code": None}
    assert "UID_secret" not in str(result)
