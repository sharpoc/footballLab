from copy import deepcopy

from worldcup.competitions import formal_single_match_competitions
from worldcup.config import load_config
from worldcup.league_competition_pipeline import build_league_competition_snapshot
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


OBSERVED_AT = "2026-08-24T12:00:00+00:00"


def _odds_event(sport_key: str) -> dict:
    bookmakers = []
    for index in range(3):
        bookmakers.append(
            {
                "key": f"book-{index}",
                "last_update": "2026-08-24T11:55:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home FC", "price": 1.8},
                            {"name": "Draw", "price": 3.7},
                            {"name": "Away FC", "price": 4.8},
                        ],
                    }
                ],
            }
        )
    return {
        "id": "event-1",
        "sport_key": sport_key,
        "commence_time": "2026-08-25T18:00:00Z",
        "home_team": "Home FC",
        "away_team": "Away FC",
        "bookmakers": bookmakers,
    }


def test_all_six_profiles_build_schema_v2_market_fallback_snapshot():
    for profile in formal_single_match_competitions():
        snapshot = build_league_competition_snapshot(
            [_odds_event(profile.theoddsapi_sport_key)],
            profile.id,
            OBSERVED_AT,
        )

        assert snapshot["competition"]["id"] == profile.id
        assert snapshot["counts"]["matches"] == 1
        decision = snapshot["matches"][0]["match_decision"]
        assert decision["schema_version"] == 2
        assert decision["label"] == "MATCH_PICK"
        assert "market_only_rating_fallback" in decision["risks"]
        assert "model_settlement" not in decision
        assert "market_consensus_fallback" in snapshot["data_quality"]["warnings"]


def test_placeholder_elo_and_home_advantage_cannot_change_pending_direction():
    raw = [_odds_event("soccer_epl")]
    first_cfg = deepcopy(load_config())
    second_cfg = deepcopy(load_config())
    first_cfg["elo"]["home_adv"] = -500.0
    second_cfg["elo"]["home_adv"] = 500.0

    first = build_league_competition_snapshot(
        raw, "epl_2026_27", OBSERVED_AT, cfg=first_cfg
    )
    second = build_league_competition_snapshot(
        raw, "epl_2026_27", OBSERVED_AT, cfg=second_cfg
    )

    assert first["matches"][0]["match_decision"]["selection"] == second["matches"][0]["match_decision"]["selection"]


def test_pipeline_rejects_non_formal_competition_and_wrong_sport_key():
    try:
        build_league_competition_snapshot(
            [_odds_event("soccer_china_superleague")],
            "csl_2026",
            OBSERVED_AT,
        )
    except ValueError as exc:
        assert str(exc) == "unsupported_league_pipeline: csl_2026"
    else:
        raise AssertionError("expected unsupported league rejection")

    try:
        build_league_competition_snapshot(
            [_odds_event("soccer_italy_serie_a")],
            "epl_2026_27",
            OBSERVED_AT,
        )
    except ValueError as exc:
        assert str(exc) == "sport_key_mismatch: event-1"
    else:
        raise AssertionError("expected sport key mismatch")


def test_pipeline_uses_explicit_identity_registry_and_blocks_unknown_fixture_only():
    registry = LeagueTeamIdentityRegistry({
        "epl_2026_27": {"home": ("Home FC",), "away": ("Away FC",)},
    })
    accepted = build_league_competition_snapshot(
        [_odds_event("soccer_epl")],
        "epl_2026_27",
        OBSERVED_AT,
        identity_registry=registry,
    )
    blocked = build_league_competition_snapshot(
        [{**_odds_event("soccer_epl"), "away_team": "Unknown FC"}],
        "epl_2026_27",
        OBSERVED_AT,
        identity_registry=registry,
    )

    assert accepted["matches"][0]["home_canonical"] == "home"
    assert blocked["matches"] == []
    assert blocked["data_quality"]["club_alias_unmatched"] == ["Unknown FC"]
