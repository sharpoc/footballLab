from worldcup.collectors.lineups import parse_lineup_contexts


def test_parse_lineup_contexts_normalizes_manual_json_to_pipeline_context():
    contexts = parse_lineup_contexts(
        {
            "schema_version": 1,
            "provider": "manual_json",
            "matches": [
                {
                    "source_match_no": 1,
                    "kickoff_at_utc": "2026-06-11T19:00:00Z",
                    "home_team": "Mexico",
                    "away_team": "South Africa",
                    "source": "fifa_match_centre",
                    "confirmed_starting_xi": True,
                    "lineup_confirmed_at": "2026-06-11T18:02:00Z",
                    "lineup_confidence": 1.0,
                    "home": {
                        "starting": [
                            {"name": "Mexico GK", "position": "GK"},
                            {"name": "Mexico FW", "position": "FW"},
                        ],
                        "bench": [{"name": "Mexico Sub"}],
                        "absent": [{"name": "Mexico Injured", "reason": "injury"}],
                        "impact": {
                            "attack_delta": 0.04,
                            "defense_delta": -0.01,
                            "goalkeeper_delta": 0.02,
                        },
                    },
                    "away": {
                        "starting": [{"name": "South Africa GK", "position": "GK"}],
                        "impact": {
                            "attack_delta": -0.03,
                            "defense_delta": 0.01,
                            "goalkeeper_delta": -0.02,
                        },
                    },
                }
            ],
        }
    )

    assert len(contexts) == 1
    context = contexts[0]
    assert context.provider == "manual_json"
    assert context.source == "fifa_match_centre"
    assert context.home_canonical == "mexico"
    assert context.away_canonical == "south_africa"
    assert context.confirmed_starting_xi is True
    assert context.lineup_confirmed_at.isoformat() == "2026-06-11T18:02:00+00:00"

    pipeline_context = context.to_pipeline_context()
    assert pipeline_context["confirmed_starting_xi"] is True
    assert pipeline_context["lineup_confirmed_at"] == "2026-06-11T18:02:00+00:00"
    assert pipeline_context["home_attack_delta"] == 0.04
    assert pipeline_context["away_goalkeeper_delta"] == -0.02
    assert pipeline_context["provider"] == "manual_json"
    assert pipeline_context["lineups"]["home"]["starting"][0]["name"] == "Mexico GK"
    assert pipeline_context["lineups"]["home"]["starting_count"] == 2
    assert pipeline_context["lineups"]["away"]["starting_count"] == 1


def test_parse_lineup_contexts_skips_entries_without_usable_team_keys():
    contexts = parse_lineup_contexts(
        {
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-11T19:00:00Z",
                    "home_team": "",
                    "away_team": "South Africa",
                    "confirmed_starting_xi": True,
                }
            ]
        }
    )

    assert contexts == []
