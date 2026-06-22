import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.lineups_refresh import run_lineups_refresh


def _name(value):
    return [{"Locale": "en-GB", "Description": value}]


def _player(idx, status, position=1, name=None):
    return {
        "IdPlayer": f"p{idx}",
        "ShirtNumber": idx,
        "Status": status,
        "PlayerName": _name(name or f"Player {idx}"),
        "ShortName": _name(name or f"P{idx}"),
        "Position": position,
    }


def _team(name, tactic, players):
    return {
        "TeamName": _name(name),
        "Tactics": tactic,
        "Players": players,
    }


def _confirmed_live(match_id="400021504"):
    return {
        "IdMatch": match_id,
        "MatchNumber": 24,
        "Date": "2026-06-18T02:00:00Z",
        "HomeTeam": _team(
            "Uzbekistan",
            "3-4-3",
            [_player(1, 1, 0, "Utkir YUSUPOV")]
            + [_player(i, 1) for i in range(2, 12)]
            + [_player(i, 2) for i in range(12, 27)],
        ),
        "AwayTeam": _team(
            "Colombia",
            "4-1-2-3",
            [_player(101, 1, 0, "Camilo VARGAS")]
            + [_player(i, 1) for i in range(102, 112)]
            + [_player(i, 2) for i in range(112, 127)],
        ),
    }


def _unconfirmed_live(match_id="400021440"):
    return {
        "IdMatch": match_id,
        "MatchNumber": 25,
        "Date": "2026-06-18T16:00:00Z",
        "HomeTeam": _team("Czechia", None, []),
        "AwayTeam": _team("South Africa", None, []),
    }


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_lineups_refresh_live_write_stores_only_confirmed_fifa_lineups():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021504",
                "Date": "2026-06-18T02:00:00Z",
            },
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021440",
                "Date": "2026-06-18T16:00:00Z",
            },
        ]
    }

    def fake_transport(url):
        if "/calendar/matches" in url:
            return FakeResponse(calendar)
        if url.endswith("/400021504?language=en"):
            return FakeResponse(_confirmed_live())
        return FakeResponse(_unconfirmed_live())

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineups_wc2026.json"
        result = run_lineups_refresh(
            live=True,
            write=True,
            notify=False,
            now="2026-06-18T14:45:00+00:00",
            out_path=out,
            transport=fake_transport,
        )

        payload = json.loads(out.read_text())
        assert result["status"] == "captured"
        assert result["matches_checked"] == 2
        assert result["confirmed"] == 1
        assert result["newly_confirmed"] == 1
        assert result["missing"] == 1
        assert payload["provider"] == "fifa_public_api"
        assert len(payload["matches"]) == 1
        assert payload["matches"][0]["home"]["formation"] == "3-4-3"
        assert len(payload["matches"][0]["home"]["starting"]) == 11


def test_lineups_refresh_notifies_once_when_lineups_missing_inside_alert_window():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021440",
                "Date": "2026-06-18T16:00:00Z",
            }
        ]
    }

    def fake_transport(url):
        if "/calendar/matches" in url:
            return FakeResponse(calendar)
        return FakeResponse(_unconfirmed_live())

    notify_calls = []

    def notify_fn(content, *, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    with TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "lineups_missing_notifications.json"
        first = run_lineups_refresh(
            live=True,
            write=False,
            notify=True,
            now="2026-06-18T15:30:00+00:00",
            notification_state_path=state_path,
            transport=fake_transport,
            notify_fn=notify_fn,
        )
        second = run_lineups_refresh(
            live=True,
            write=False,
            notify=True,
            now="2026-06-18T15:35:00+00:00",
            notification_state_path=state_path,
            transport=fake_transport,
            notify_fn=notify_fn,
        )

        assert first["notification"]["status"] == "sent"
        assert second["notification"]["status"] == "skipped"
        assert second["notification"]["reason"] == "already_notified"
        assert len(notify_calls) == 1
        assert "官方首发未抓到" in notify_calls[0]["summary"]
        assert "Czechia vs South Africa" in notify_calls[0]["content"]
        state = json.loads(state_path.read_text())
        assert "fifa_public_api:400021440" in state["sent"]


def test_lineups_refresh_default_missing_notification_waits_until_35_minutes():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021440",
                "Date": "2026-06-18T16:00:00Z",
            }
        ]
    }

    def fake_transport(url):
        if "/calendar/matches" in url:
            return FakeResponse(calendar)
        return FakeResponse(_unconfirmed_live())

    notify_calls = []

    def notify_fn(content, *, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    with TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "lineups_missing_notifications.json"
        result = run_lineups_refresh(
            live=True,
            write=False,
            notify=True,
            now="2026-06-18T15:20:00+00:00",
            notification_state_path=state_path,
            transport=fake_transport,
            notify_fn=notify_fn,
        )

        assert result["missing"] == 1
        assert result["missing_alerts"] == 0
        assert result["notification"]["status"] == "skipped"
        assert result["notification"]["reason"] == "no_missing_lineups_in_window"
        assert notify_calls == []
        assert not state_path.exists()


def test_lineups_refresh_write_preserves_existing_confirmed_cache_when_current_poll_missing():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021440",
                "Date": "2026-06-18T16:00:00Z",
            }
        ]
    }

    def fake_transport(url):
        if "/calendar/matches" in url:
            return FakeResponse(calendar)
        return FakeResponse(_unconfirmed_live())

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineups_wc2026.json"
        out.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": "fifa_public_api",
                    "matches": [
                        {
                            "source_match_no": 24,
                            "kickoff_at_utc": "2026-06-18T02:00:00+00:00",
                            "home_team": "Uzbekistan",
                            "away_team": "Colombia",
                            "source": "fifa_live_football",
                            "provider": "fifa_public_api",
                            "confirmed_starting_xi": True,
                            "lineup_confirmed_at": "2026-06-18T00:45:00+00:00",
                            "home": {"starting": [{"name": "Utkir YUSUPOV"}]},
                            "away": {"starting": [{"name": "Camilo VARGAS"}]},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = run_lineups_refresh(
            live=True,
            write=True,
            notify=False,
            now="2026-06-18T14:45:00+00:00",
            out_path=out,
            transport=fake_transport,
        )

        payload = json.loads(out.read_text())
        assert result["confirmed"] == 0
        assert result["newly_confirmed"] == 0
        assert result["missing"] == 1
        assert len(payload["matches"]) == 1
        assert payload["matches"][0]["home_team"] == "Uzbekistan"


def test_lineups_refresh_dry_run_does_not_fetch_or_write():
    calls = []

    def fake_transport(url):
        calls.append(url)
        return FakeResponse({})

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineups_wc2026.json"
        result = run_lineups_refresh(
            live=False,
            write=True,
            notify=True,
            now="2026-06-18T14:45:00+00:00",
            out_path=out,
            transport=fake_transport,
        )

        assert result["status"] == "dry_run"
        assert calls == []
        assert not out.exists()
