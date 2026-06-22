import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.lineup_audit import build_lineup_audit, main, send_lineup_audit_notification


def test_build_lineup_audit_tracks_capture_snapshot_and_post_information_odds():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        lineups_path = root / "lineups_wc2026.json"
        history_dir = root / "history"
        history_dir.mkdir()
        notification_state_path = root / "lineups_missing_notifications.json"

        lineups_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-06-21T08:30:00+00:00",
                    "provider": "fifa_public_api",
                    "matches": [
                        {
                            "source_match_no": 36,
                            "kickoff_at_utc": "2026-06-21T04:00:00+00:00",
                            "home_team": "Tunisia",
                            "away_team": "Japan",
                            "confirmed_starting_xi": True,
                            "lineup_confirmed_at": "2026-06-21T03:32:00+00:00",
                            "home": {"starting": [{"name": f"Tunisia {idx}"} for idx in range(11)]},
                            "away": {"starting": [{"name": f"Japan {idx}"} for idx in range(11)]},
                        },
                        {
                            "source_match_no": 39,
                            "kickoff_at_utc": "2026-06-21T19:00:00+00:00",
                            "home_team": "Belgium",
                            "away_team": "Iran",
                            "confirmed_starting_xi": True,
                            "lineup_confirmed_at": "2026-06-21T18:40:00+00:00",
                            "home": {"starting": [{"name": f"Belgium {idx}"} for idx in range(11)]},
                            "away": {"starting": [{"name": f"Iran {idx}"} for idx in range(11)]},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (history_dir / "snapshot_20260621T034000Z-live.json").write_text(
            json.dumps(
                {
                    "snapshot_at": "2026-06-21T03:40:00+00:00",
                    "matches": [
                        {
                            "source_match_no": 36,
                            "kickoff_at_utc": "2026-06-21T04:00:00+00:00",
                            "home_team": "Tunisia",
                            "away_team": "Japan",
                            "home_canonical": "tunisia",
                            "away_canonical": "japan",
                            "odds_updated_at": "2026-06-21T03:39:00+00:00",
                            "model": {
                                "lineup_shadow": {
                                    "confirmed_starting_xi": True,
                                    "lineup_confirmed_at": "2026-06-21T03:32:00+00:00",
                                    "odds_observed_at": "2026-06-21T03:39:00+00:00",
                                    "post_information_odds_available": True,
                                }
                            },
                            "signals": [{"grade": "A"}, {"grade": "B"}],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        notification_state_path.write_text(
            json.dumps({"sent": {"fifa_public_api:400021440": "2026-06-21T18:30:00+00:00"}}),
            encoding="utf-8",
        )

        report = build_lineup_audit(
            lineups_path=lineups_path,
            history_dir=history_dir,
            notification_state_path=notification_state_path,
            generated_at="2026-06-21T09:00:00+00:00",
        )

        assert report["schema_version"] == 1
        assert report["summary"] == {
            "confirmed_lineups": 2,
            "captured_before_kickoff": 2,
            "entered_snapshot": 1,
            "post_information_odds_available": 1,
            "captured_without_snapshot_input": 1,
            "captured_without_post_information_odds": 1,
        }
        assert report["notifications"]["sent_count"] == 1
        tunisia = report["matches"][0]
        assert tunisia["match_label"] == "Tunisia vs Japan"
        assert tunisia["minutes_before_kickoff"] == 28
        assert tunisia["entered_snapshot"] is True
        assert tunisia["post_information_odds_available"] is True
        assert tunisia["strong_signal_count"] == 1
        belgium = report["matches"][1]
        assert belgium["match_label"] == "Belgium vs Iran"
        assert belgium["entered_snapshot"] is False
        assert belgium["post_information_odds_available"] is False


def test_lineup_audit_main_writes_report():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        lineups_path = root / "lineups_wc2026.json"
        out_path = root / "diagnostics" / "lineup_audit.json"
        lineups_path.write_text(
            json.dumps({"provider": "fifa_public_api", "matches": []}),
            encoding="utf-8",
        )

        exit_code = main(
            [
                "--lineups",
                str(lineups_path),
                "--out",
                str(out_path),
                "--generated-at",
                "2026-06-21T09:00:00+00:00",
            ]
        )

        assert exit_code == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["summary"]["confirmed_lineups"] == 0


def test_lineup_audit_notification_sends_once_for_pre_kickoff_chain_gaps():
    with TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "lineups_missing_notifications.json"
        notify_calls = []

        def notify_fn(content, *, summary):
            notify_calls.append({"content": content, "summary": summary})
            return {"status": "sent", "exit_code": 0}

        report = {
            "generated_at": "2026-06-22T10:25:00+00:00",
            "matches": [
                {
                    "source_match_no": 44,
                    "match_label": "Spain vs Saudi Arabia",
                    "kickoff_at_utc": "2026-06-22T11:00:00+00:00",
                    "minutes_before_kickoff": 35,
                    "issue_flags": ["captured_without_post_information_odds"],
                    "entered_snapshot": True,
                    "post_information_odds_available": False,
                }
            ],
        }

        first = send_lineup_audit_notification(report, state_path=state_path, notify_fn=notify_fn)
        second = send_lineup_audit_notification(report, state_path=state_path, notify_fn=notify_fn)

        assert first["status"] == "sent"
        assert first["match_count"] == 1
        assert second == {"status": "skipped", "reason": "already_notified"}
        assert len(notify_calls) == 1
        assert notify_calls[0]["summary"] == "世界杯首发链路待处理：1 场"
        assert "Spain vs Saudi Arabia" in notify_calls[0]["content"]
        assert "captured_without_post_information_odds" in notify_calls[0]["content"]


def test_lineup_audit_notification_ignores_after_kickoff_issues():
    with TemporaryDirectory() as tmp:
        notify_calls = []
        report = {
            "generated_at": "2026-06-22T12:00:00+00:00",
            "matches": [
                {
                    "source_match_no": 44,
                    "match_label": "Spain vs Saudi Arabia",
                    "kickoff_at_utc": "2026-06-22T11:00:00+00:00",
                    "minutes_before_kickoff": -60,
                    "issue_flags": ["captured_without_post_information_odds"],
                }
            ],
        }

        result = send_lineup_audit_notification(
            report,
            state_path=Path(tmp) / "lineups_missing_notifications.json",
            notify_fn=lambda content, *, summary: notify_calls.append((content, summary)),
        )

        assert result == {"status": "skipped", "reason": "no_actionable_lineup_audit_issues"}
        assert notify_calls == []
