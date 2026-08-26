import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.lineup_source_probe import (
    build_fotmob_match_details_url,
    build_fotmob_matches_url,
    run_lineup_source_probe,
)


def _name(value):
    return [{"Locale": "en-GB", "Description": value}]


def _player(idx, status, position=1, name=None):
    return {
        "IdPlayer": f"p{idx}",
        "Status": status,
        "PlayerName": _name(name or f"Player {idx}"),
        "ShortName": _name(name or f"P{idx}"),
        "Position": position,
    }


def _team(name, players):
    return {"TeamName": _name(name), "Tactics": "4-3-3", "Players": players}


def _fifa_confirmed_live():
    return {
        "IdMatch": "400021504",
        "MatchNumber": 24,
        "Date": "2026-06-18T16:00:00Z",
        "HomeTeam": _team("Czechia", [_player(i, 1) for i in range(1, 12)]),
        "AwayTeam": _team("South Africa", [_player(i, 1) for i in range(21, 32)]),
    }


def _fotmob_matches():
    return {
        "leagues": [
            {
                "id": 77,
                "name": "FIFA World Cup",
                "matches": [
                    {
                        "id": 5315746,
                        "home": {"name": "Czechia"},
                        "away": {"name": "South Africa"},
                        "status": {"utcTime": "2026-06-18T16:00:00.000Z"},
                    }
                ],
            }
        ]
    }


def _fotmob_predicted_details():
    starters_home = [{"name": {"fullName": f"Home {i}"}, "position": "DF"} for i in range(1, 12)]
    starters_away = [{"name": {"fullName": f"Away {i}"}, "position": "MF"} for i in range(1, 12)]
    return {
        "general": {
            "matchId": 5315746,
            "matchTimeUTC": "2026-06-18T16:00:00.000Z",
            "homeTeam": {"name": "Czechia"},
            "awayTeam": {"name": "South Africa"},
        },
        "content": {
            "lineup": {
                "lineupStatus": "predicted",
                "homeTeam": {"formation": "4-3-3", "starters": starters_home},
                "awayTeam": {"formation": "4-2-3-1", "starters": starters_away},
            }
        },
    }


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_lineup_source_probe_dry_run_does_not_fetch_or_write():
    calls = []

    def fake_transport(url):
        calls.append(url)
        return FakeResponse({})

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineup_source_probe.json"
        result = run_lineup_source_probe(
            live=False,
            write=True,
            out_path=out,
            transport=fake_transport,
        )

        assert result["status"] == "dry_run"
        assert calls == []
        assert not out.exists()


def test_lineup_source_probe_records_fifa_confirmed_and_fotmob_predicted():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021504",
                "Date": "2026-06-18T16:00:00Z",
            }
        ]
    }

    def fake_transport(url):
        if "api.fifa.com/api/v3/calendar/matches" in url:
            return FakeResponse(calendar)
        if "api.fifa.com/api/v3/live/football" in url:
            return FakeResponse(_fifa_confirmed_live())
        if "www.fotmob.com/api/data/matches" in url:
            return FakeResponse(_fotmob_matches())
        if "www.fotmob.com/api/data/matchDetails" in url:
            return FakeResponse(_fotmob_predicted_details())
        raise AssertionError(f"unexpected url: {url}")

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineup_source_probe.json"
        result = run_lineup_source_probe(
            live=True,
            write=True,
            sources=("fifa", "fotmob"),
            now="2026-06-18T14:45:00+00:00",
            out_path=out,
            transport=fake_transport,
        )

        payload = json.loads(out.read_text())
        observations = payload["observations"]
        by_source = {item["source"]: item for item in observations}

        assert result["status"] == "captured"
        assert result["summary"]["observations"] == 2
        assert result["summary"]["confirmed"] == 1
        assert result["summary"]["predicted"] == 1
        assert by_source["fifa_public_api"]["lineup_status"] == "confirmed"
        assert by_source["fifa_public_api"]["captured_before_kickoff"] is True
        assert by_source["fifa_public_api"]["home_starting_count"] == 11
        assert by_source["fotmob"]["lineup_status"] == "predicted"
        assert by_source["fotmob"]["home_starting_count"] == 11
        assert by_source["fotmob"]["away_formation"] == "4-2-3-1"


def test_lineup_source_probe_appends_history_and_writes_report():
    calendar = {
        "Results": [
            {
                "IdCompetition": "17",
                "IdSeason": "285023",
                "IdStage": "289273",
                "IdMatch": "400021504",
                "Date": "2026-06-18T16:00:00Z",
            }
        ]
    }

    def fake_transport(url):
        if "api.fifa.com/api/v3/calendar/matches" in url:
            return FakeResponse(calendar)
        if "api.fifa.com/api/v3/live/football" in url:
            return FakeResponse(_fifa_confirmed_live())
        if "www.fotmob.com/api/data/matches" in url:
            return FakeResponse(_fotmob_matches())
        if "www.fotmob.com/api/data/matchDetails" in url:
            return FakeResponse(_fotmob_predicted_details())
        raise AssertionError(f"unexpected url: {url}")

    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "lineup_source_probe.json"
        history = Path(tmp) / "lineup_source_probe_history.jsonl"
        report = Path(tmp) / "lineup_source_probe_report.md"

        result = run_lineup_source_probe(
            live=True,
            write=True,
            append_history=True,
            write_report=True,
            sources=("fifa", "fotmob"),
            now="2026-06-18T14:45:00+00:00",
            out_path=out,
            history_path=history,
            report_path=report,
            transport=fake_transport,
        )

        history_rows = [json.loads(line) for line in history.read_text().splitlines()]
        markdown = report.read_text()

        assert result["history"]["appended"] == 2
        assert result["history"]["out"] == str(history)
        assert result["report"]["out"] == str(report)
        assert len(history_rows) == 2
        assert {row["source"] for row in history_rows} == {"fifa_public_api", "fotmob"}
        assert all(row["run_observed_at"] == "2026-06-18T14:45:00+00:00" for row in history_rows)
        assert "# 首发源观测报告" in markdown
        assert "FIFA public API" in markdown
        assert "FotMob" in markdown
        assert "confirmed: 1" in markdown
        assert "predicted: 1" in markdown
        assert "Czechia vs South Africa" in markdown
        assert "T-75.0 分钟" in markdown


def test_fotmob_probe_builders_use_current_data_routes():
    """Pre-match source probing must not retain the obsolete provider route family."""
    assert build_fotmob_matches_url("20260825") == "https://www.fotmob.com/api/data/matches?date=20260825"
    assert build_fotmob_match_details_url("5868020") == "https://www.fotmob.com/api/data/matchDetails?matchId=5868020"
