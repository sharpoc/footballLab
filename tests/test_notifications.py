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
    current["matches"][0]["signals"][0]["grade"] = "S"
    current["matches"][0]["signals"][0]["ev"] = 0.092
    current["matches"][0]["signals"][0]["edge"] = 0.071

    notification = build_change_notification(previous, current, limit=5)

    assert notification["should_send"] is True
    assert notification["summary"] == "世界杯信号更新：1 条变化"
    assert "墨西哥 对 南非 | 胜平负 - 主队" in notification["content"]
    assert "等级 A → S" in notification["content"]
    assert "EV +5.2% → +9.2%" in notification["content"]
    assert "赔率 2.00 → 1.85" in notification["content"]
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
        "世界杯信号更新",
        summary="世界杯信号更新：1 条变化",
        runner=runner,
    )

    assert result == {"status": "sent", "exit_code": 0}
    assert calls[0][0][0].endswith("wxpusher-remind")
    assert calls[0][0][1:] == ["--summary", "世界杯信号更新：1 条变化", "世界杯信号更新"]
    assert "UID_secret" not in str(result)
    assert "example.invalid" not in str(result)


def test_send_wxpusher_notification_returns_failed_when_command_errors():
    def runner(*_args, **_kwargs):
        raise OSError("missing command with UID_secret in message")

    result = send_wxpusher_notification(
        "世界杯信号更新",
        summary="世界杯信号更新：1 条变化",
        runner=runner,
    )

    assert result == {"status": "failed", "exit_code": None}
    assert "UID_secret" not in str(result)
