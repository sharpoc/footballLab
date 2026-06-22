from worldcup.collectors.fifa_lineups import parse_fifa_live_match


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


def test_parse_fifa_live_match_builds_confirmed_lineup_context():
    raw = {
        "IdMatch": "400021504",
        "MatchNumber": 24,
        "Date": "2026-06-18T02:00:00Z",
        "HomeTeam": _team(
            "Uzbekistan",
            "3-4-3",
            [_player(1, 1, 0, "Utkir YUSUPOV")]
            + [_player(i, 1) for i in range(2, 12)]
            + [_player(i, 2, 2) for i in range(12, 27)],
        ),
        "AwayTeam": _team(
            "Colombia",
            "4-1-2-3",
            [_player(101, 1, 0, "Camilo VARGAS")]
            + [_player(i, 1) for i in range(102, 112)]
            + [_player(i, 2, 3) for i in range(112, 127)],
        ),
    }

    context = parse_fifa_live_match(raw, fetched_at="2026-06-18T00:45:00+00:00")
    pipeline = context.to_pipeline_context()

    assert context.provider == "fifa_public_api"
    assert context.source == "fifa_live_football"
    assert context.source_match_no == 24
    assert context.confirmed_starting_xi is True
    assert context.lineup_confirmed_at.isoformat() == "2026-06-18T00:45:00+00:00"
    assert context.home_canonical == "uzbekistan"
    assert context.away_canonical == "colombia"
    assert len(context.home_starting) == 11
    assert len(context.home_bench) == 15
    assert context.home_starting[0].name == "Utkir YUSUPOV"
    assert context.home_starting[0].position == "GK"
    assert pipeline["lineups"]["home"]["formation"] == "3-4-3"
    assert pipeline["lineups"]["away"]["formation"] == "4-1-2-3"


def test_parse_fifa_live_match_keeps_unconfirmed_match_unconfirmed():
    raw = {
        "IdMatch": "400021440",
        "MatchNumber": 25,
        "Date": "2026-06-18T16:00:00Z",
        "HomeTeam": _team("Czechia", None, []),
        "AwayTeam": _team("South Africa", None, []),
    }

    context = parse_fifa_live_match(raw, fetched_at="2026-06-18T14:45:00+00:00")

    assert context.confirmed_starting_xi is False
    assert context.lineup_confirmed_at is None
    assert context.home_canonical == "czech_republic"
    assert context.away_canonical == "south_africa"
    assert context.home_starting == []
    assert context.away_starting == []
