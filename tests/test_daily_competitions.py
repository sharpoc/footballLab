from __future__ import annotations


EXPECTED_NAMES = (
    "中超",
    "英超",
    "英冠",
    "德甲",
    "德乙",
    "法甲",
    "意甲",
    "西甲",
    "瑞典超",
    "挪超",
    "丹超",
    "芬超",
    "墨西哥超",
    "墨西哥甲",
    "澳超",
    "J联赛",
    "K联赛",
    "巴西甲",
    "阿根廷超",
    "美职联",
)


def _catalog_module():
    from worldcup import daily_competitions

    return daily_competitions


def test_daily_catalog_contains_exactly_the_20_confirmed_leagues():
    module = _catalog_module()
    catalog = module.daily_competition_catalog()
    assert tuple(item.name for item in catalog) == EXPECTED_NAMES
    assert len(catalog) == 20


def test_daily_catalog_has_explicit_status_and_reason_for_every_league():
    module = _catalog_module()
    catalog = module.daily_competition_catalog()
    by_name = {item.name: item for item in catalog}
    assert len(catalog) == 20
    assert sum(item.status == "enabled" for item in catalog) == 17
    assert {by_name[name].status for name in ("墨西哥甲", "澳超", "阿根廷超")} == {"code_reserved"}
    for item in catalog:
        assert item.reason
        if item.name not in {"墨西哥甲", "澳超", "阿根廷超"}:
            assert item.competition_id
            assert item.sport_key
        else:
            assert item.competition_id is None
            assert item.sport_key is None


def test_verified_catalog_uses_the_formal_competition_profiles_and_exact_sport_keys():
    module = _catalog_module()
    from worldcup.competitions import get_competition

    expected = {
        "中超": ("csl_2026", "soccer_china_superleague"),
        "英超": ("epl_2026_27", "soccer_epl"),
        "英冠": ("efl_championship_2026_27", "soccer_efl_champ"),
        "德甲": ("bundesliga_2026_27", "soccer_germany_bundesliga"),
        "德乙": ("bundesliga2_2026_27", "soccer_germany_bundesliga2"),
        "法甲": ("ligue_1_2026_27", "soccer_france_ligue_one"),
        "意甲": ("serie_a_2026_27", "soccer_italy_serie_a"),
        "西甲": ("laliga_2026_27", "soccer_spain_la_liga"),
        "瑞典超": ("allsvenskan_2026", "soccer_sweden_allsvenskan"),
        "挪超": ("eliteserien_2026", "soccer_norway_eliteserien"),
        "丹超": ("superliga_2026_27", "soccer_denmark_superliga"),
        "芬超": ("veikkausliiga_2026", "soccer_finland_veikkausliiga"),
        "墨西哥超": ("liga_mx_2026", "soccer_mexico_ligamx"),
        "J联赛": ("j1_league_2026", "soccer_japan_j_league"),
        "K联赛": ("k_league_1_2026", "soccer_korea_kleague1"),
        "巴西甲": ("serie_a_brazil_2026", "soccer_brazil_campeonato"),
        "美职联": ("mls_2026", "soccer_usa_mls"),
    }
    by_name = {item.name: item for item in module.daily_competition_catalog()}
    assert {name: (by_name[name].competition_id, by_name[name].sport_key) for name in expected} == expected
    profile_expectations = {
        name: (sport_key, (sport_key,))
        for name, (_competition_id, sport_key) in expected.items()
    }
    profile_expectations["中超"] = (
        None,
        ("soccer_china_superleague", "soccer_china_super_league"),
    )
    assert {
        name: (get_competition(competition_id).theoddsapi_sport_key, get_competition(competition_id).theoddsapi_candidate_keys)
        for name, (competition_id, _sport_key) in expected.items()
    } == profile_expectations


def test_disabled_leagues_remain_fail_closed_without_profile_or_provider_key():
    module = _catalog_module()
    by_name = {item.name: item for item in module.daily_competition_catalog()}
    for name in ("墨西哥甲", "澳超", "阿根廷超"):
        item = by_name[name]
        assert item.status == "code_reserved"
        assert item.competition_id is None
        assert item.sport_key is None
        assert "未提供" in item.reason or "未验证" in item.reason


def test_enabled_competition_ids_contains_only_the_17_verified_profiles():
    module = _catalog_module()
    assert len(module.enabled_competition_ids()) == 17
    assert "csl_2026" in module.enabled_competition_ids()
    assert "epl_2026_27" in module.enabled_competition_ids()
    assert "bundesliga_2026_27" in module.enabled_competition_ids()
    assert all(item not in module.enabled_competition_ids() for item in (None, "liga_mx_2_2026", "a_league_2026"))


def test_only_verified_csl_is_enabled_and_big_five_are_not_enabled():
    module = _catalog_module()
    by_name = {item.name: item for item in module.daily_competition_catalog()}
    assert by_name["中超"].status == "enabled"
    assert by_name["英超"].status == "enabled"
    assert by_name["德甲"].status == "enabled"
    assert by_name["法甲"].status == "enabled"
    assert by_name["意甲"].status == "enabled"
    assert by_name["西甲"].status == "enabled"


def test_unknown_leagues_remain_disabled_without_guessed_sport_keys():
    module = _catalog_module()
    by_name = {item.name: item for item in module.daily_competition_catalog()}
    for name in ("墨西哥甲", "澳超", "阿根廷超"):
        item = by_name[name]
        assert item.status == "code_reserved"
        assert item.competition_id is None
        assert item.sport_key is None
