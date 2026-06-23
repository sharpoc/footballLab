from worldcup.collectors.club_aliases import canonicalize_club, match_club_alias, match_known_club_alias
from worldcup.collectors.team_aliases import canonicalize_team


def test_csl_club_aliases_are_scoped_to_competition():
    assert canonicalize_club("csl_2026", "Shanghai Port") == "shanghai_port"
    assert canonicalize_club("csl_2026", "Shanghai SIPG") == "shanghai_port"
    assert canonicalize_club("csl_2026", "Beijing Guoan") == "beijing_guoan"
    assert canonicalize_club("csl_2026", "Shandong Taishan") == "shandong_taishan"


def test_club_aliases_do_not_change_national_team_aliases():
    assert canonicalize_team("Shanghai Port") == "shanghai_port"
    assert canonicalize_team("USA") == "united_states"
    assert canonicalize_club("csl_2026", "USA") == "usa"


def test_match_club_alias_reports_unmatched_unknown_clubs():
    result = match_club_alias("csl_2026", "Unknown FC")

    assert result.raw_name == "Unknown FC"
    assert result.canonical_key is None
    assert result.unmatched_name == "Unknown FC"


def test_match_known_club_alias_accepts_configured_csl_alias_only():
    result = match_known_club_alias("csl_2026", "Shanghai SIPG")

    assert result.raw_name == "Shanghai SIPG"
    assert result.canonical_key == "shanghai_port"
    assert result.unmatched_name is None


def test_match_known_club_alias_blocks_slug_fallback_for_unknown_csl_team():
    result = match_known_club_alias("csl_2026", "Unknown FC")

    assert result.raw_name == "Unknown FC"
    assert result.canonical_key is None
    assert result.unmatched_name == "Unknown FC"


def test_permissive_canonicalize_club_remains_available_for_existing_callers():
    assert canonicalize_club("csl_2026", "Unknown FC") == "unknown_fc"


def test_csl_2023_2026_source_aliases_are_known():
    cases = [
        ("Shanghai Port", "shanghai_port"),
        ("Shanghai SIPG", "shanghai_port"),
        ("上海海港", "shanghai_port"),
        ("Shanghai Shenhua", "shanghai_shenhua"),
        ("上海申花", "shanghai_shenhua"),
        ("Shandong Taishan", "shandong_taishan"),
        ("山东泰山", "shandong_taishan"),
        ("Beijing Guoan", "beijing_guoan"),
        ("北京国安", "beijing_guoan"),
        ("Chengdu Rongcheng", "chengdu_rongcheng"),
        ("成都蓉城", "chengdu_rongcheng"),
        ("Zhejiang Professional", "zhejiang_professional"),
        ("Zhejiang", "zhejiang_professional"),
        ("浙江队", "zhejiang_professional"),
        ("浙江俱乐部绿城", "zhejiang_professional"),
        ("Henan FC", "henan"),
        ("Henan", "henan"),
        ("河南队", "henan"),
        ("河南俱乐部彩陶坊", "henan"),
        ("Tianjin Jinmen Tiger", "tianjin_jinmen_tiger"),
        ("天津津门虎", "tianjin_jinmen_tiger"),
        ("Wuhan Three Towns", "wuhan_three_towns"),
        ("武汉三镇", "wuhan_three_towns"),
        ("Meizhou Hakka", "meizhou_hakka"),
        ("梅州客家", "meizhou_hakka"),
        ("Qingdao West Coast", "qingdao_west_coast"),
        ("青岛西海岸", "qingdao_west_coast"),
        ("Qingdao Hainiu", "qingdao_hainiu"),
        ("青岛海牛", "qingdao_hainiu"),
        ("Changchun Yatai", "changchun_yatai"),
        ("长春亚泰", "changchun_yatai"),
        ("Shenzhen Peng City", "shenzhen_peng_city"),
        ("深圳新鹏城", "shenzhen_peng_city"),
        ("Yunnan Yukun", "yunnan_yukun"),
        ("云南玉昆", "yunnan_yukun"),
        ("Dalian Yingbo", "dalian_yingbo"),
        ("大连英博", "dalian_yingbo"),
        ("大连英博海发", "dalian_yingbo"),
        ("Cangzhou Mighty Lions", "cangzhou_mighty_lions"),
        ("沧州雄狮", "cangzhou_mighty_lions"),
        ("Dalian Pro", "dalian_pro"),
        ("大连人", "dalian_pro"),
        ("Nantong Zhiyun", "nantong_zhiyun"),
        ("南通支云", "nantong_zhiyun"),
        ("Shenzhen", "shenzhen"),
        ("Shenzhen FC", "shenzhen"),
        ("深圳队", "shenzhen"),
        ("Chongqing Tonglianglong", "chongqing_tonglianglong"),
        ("重庆铜梁龙", "chongqing_tonglianglong"),
        ("Liaoning Tieren", "liaoning_tieren"),
        ("辽宁铁人", "liaoning_tieren"),
        ("辽宁铁人楠波湾", "liaoning_tieren"),
    ]

    for raw_name, canonical_key in cases:
        result = match_known_club_alias("csl_2026", raw_name)

        assert result.raw_name == raw_name
        assert result.canonical_key == canonical_key
        assert result.unmatched_name is None
