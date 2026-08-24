from worldcup.competitions import (
    CompetitionConfig,
    competition_block,
    formal_single_match_competitions,
    get_competition,
    list_competitions,
)


FORMAL_SINGLE_MATCH_TARGETS = {
    "serie_a_2026_27": "soccer_italy_serie_a",
    "serie_a_brazil_2026": "soccer_brazil_campeonato",
    "laliga_2026_27": "soccer_spain_la_liga",
    "epl_2026_27": "soccer_epl",
    "bundesliga_2026_27": "soccer_germany_bundesliga",
    "ligue_1_2026_27": "soccer_france_ligue_one",
}


def test_six_leagues_declare_formal_disabled_pipeline_capability():
    assert {item.id for item in formal_single_match_competitions()} == set(
        FORMAL_SINGLE_MATCH_TARGETS
    )
    for competition_id, sport_key in FORMAL_SINGLE_MATCH_TARGETS.items():
        cfg = get_competition(competition_id)
        assert cfg.theoddsapi_sport_key == sport_key
        assert cfg.pipeline_family == "league_v1"
        assert cfg.prediction_policy == "market_consensus_until_club_rating_verified"
        assert cfg.result_policy == "verified_football_90min"
        assert cfg.statistics_scope == "observed_schema_v2_match_pick_only"
        assert cfg.runtime_status == "disabled_until_live_acceptance"


def test_internal_pipeline_capability_is_not_added_to_public_snapshot_block():
    block = competition_block("epl_2026_27")

    for private_key in (
        "pipeline_family",
        "prediction_policy",
        "result_policy",
        "statistics_scope",
        "runtime_status",
    ):
        assert private_key not in block


def test_registry_contains_worldcup_csl_and_big_five_constraints():
    ids = [item.id for item in list_competitions()]

    assert "fifa_world_cup_2026" in ids
    assert "csl_2026" in ids
    assert "epl_2026_27" in ids
    assert "laliga_2026_27" in ids
    assert "bundesliga_2026_27" in ids
    assert "serie_a_2026_27" in ids
    assert "ligue_1_2026_27" in ids


def test_csl_config_is_domestic_league_and_local_first():
    cfg = get_competition("csl_2026")

    assert isinstance(cfg, CompetitionConfig)
    assert cfg.name == "中超 2026"
    assert cfg.kind == "domestic_league"
    assert cfg.country == "CN"
    assert cfg.season == "2026"
    assert cfg.fixture_policy == "odds_event_window"
    assert cfg.rating_policy == "club_rating_pending"
    assert cfg.window_days == 14
    assert "Chinese Super League" in cfg.theoddsapi_search_terms


def test_competition_block_is_snapshot_safe_and_serializable():
    block = competition_block("fifa_world_cup_2026")

    assert block == {
        "id": "fifa_world_cup_2026",
        "name": "2026 世界杯",
        "kind": "tournament",
        "country": "international",
        "season": "2026",
        "source": "openfootball + theoddsapi",
        "fixture_source": "openfootball",
        "rating_policy": "national_team_elo",
        "settlement_rule": "football_90min",
        "identity_policy": "national_team_alias",
        "model_family": "worldcup_elo_poisson_v1",
        "refresh_priority": 100,
        "quota_budget": "worldcup_free_tier",
        "market_quality_profile": "worldcup_main",
    }


def test_competition_profile_declares_league_specific_boundaries():
    csl = get_competition("csl_2026")
    epl = get_competition("epl_2026_27")

    assert csl.settlement_rule == "football_90min"
    assert csl.identity_policy == "club_identity_registry"
    assert csl.model_family == "club_elo_poisson_pending_v1"
    assert csl.refresh_priority < get_competition("fifa_world_cup_2026").refresh_priority
    assert csl.quota_budget == "csl_free_tier"
    assert csl.market_quality_profile == "domestic_league_pending"

    assert epl.identity_policy == "club_identity_registry"
    assert epl.model_family == "club_elo_poisson_probe_v1"
    assert epl.refresh_priority < csl.refresh_priority
