from worldcup.competitions import (
    CompetitionConfig,
    competition_block,
    get_competition,
    list_competitions,
)


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
    }
