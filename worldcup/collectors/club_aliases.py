from __future__ import annotations

import re
import unicodedata

from worldcup.collectors.models import TeamAliasResult


_CSL_ALIAS_GROUPS = {
    "shanghai_port": (
        "Shanghai Port",
        "Shanghai SIPG",
        "Shanghai SIPG FC",
        "Shanghai Port FC",
        "上海海港",
        "上海上港",
    ),
    "shanghai_shenhua": ("Shanghai Shenhua", "Shanghai Shenhua FC", "上海申花"),
    "shandong_taishan": (
        "Shandong Taishan",
        "Shandong Luneng Taishan",
        "Shandong Luneng Taishan FC",
        "山东泰山",
    ),
    "beijing_guoan": ("Beijing Guoan", "Beijing Guoan FC", "Beijing FC", "北京国安"),
    "chengdu_rongcheng": ("Chengdu Rongcheng", "Chengdu Rongcheng FC", "成都蓉城"),
    "zhejiang_professional": (
        "Zhejiang Professional",
        "Zhejiang",
        "Zhejiang FC",
        "Zhejiang Greentown",
        "浙江队",
        "浙江",
        "浙江俱乐部绿城",
    ),
    "henan": (
        "Henan FC",
        "Henan",
        "Henan Songshan Longmen",
        "Henan Jiuzu Dukang",
        "Henan Club Jiuzu Dukang",
        "Henan Club Caitao Fang",
        "河南队",
        "河南",
        "河南俱乐部",
        "河南酒祖杜康",
        "河南俱乐部酒祖杜康",
        "河南俱乐部彩陶坊",
    ),
    "tianjin_jinmen_tiger": ("Tianjin Jinmen Tiger", "Tianjin Jinmen Tiger FC", "天津津门虎"),
    "wuhan_three_towns": ("Wuhan Three Towns", "武汉三镇"),
    "meizhou_hakka": ("Meizhou Hakka", "梅州客家"),
    "qingdao_west_coast": ("Qingdao West Coast", "Qingdao West Coast FC", "青岛西海岸"),
    "qingdao_hainiu": ("Qingdao Hainiu", "Qingdao Hainiu FC", "青岛海牛"),
    "changchun_yatai": ("Changchun Yatai", "长春亚泰"),
    "shenzhen_peng_city": ("Shenzhen Peng City", "Shenzhen Peng City FC", "深圳新鹏城"),
    "yunnan_yukun": ("Yunnan Yukun", "云南玉昆"),
    "dalian_yingbo": ("Dalian Yingbo", "大连英博", "大连英博海发"),
    "cangzhou_mighty_lions": (
        "Cangzhou Mighty Lions",
        "Cangzhou Mighty Lions FC",
        "Cangzhou Mighty Lions F.C.",
        "沧州雄狮",
    ),
    "dalian_pro": (
        "Dalian Pro",
        "Dalian Professional",
        "Dalian Professional FC",
        "大连人",
    ),
    "nantong_zhiyun": ("Nantong Zhiyun", "南通支云"),
    "shenzhen": ("Shenzhen", "Shenzhen FC", "深圳队"),
    "chongqing_tonglianglong": (
        "Chongqing Tonglianglong",
        "Chongqing Tonglianglong FC",
        "重庆铜梁龙",
    ),
    "liaoning_tieren": (
        "Liaoning Tieren",
        "Liaoning Tieren FC",
        "辽宁铁人",
        "辽宁铁人楠波湾",
    ),
}

_CSL_ALIASES = {
    alias.lower(): canonical_key
    for canonical_key, aliases in _CSL_ALIAS_GROUPS.items()
    for alias in aliases
}

_KNOWN_BY_COMPETITION = {
    "csl_2026": _CSL_ALIASES,
}


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().replace("&", " and ")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", ascii_value)).strip("_")


def canonicalize_club(competition_id: str, name: str) -> str:
    stripped = name.strip()
    key = stripped.lower()
    aliases = _KNOWN_BY_COMPETITION.get(competition_id, {})
    return aliases.get(key, _slugify(stripped))


def match_club_alias(competition_id: str, name: str) -> TeamAliasResult:
    canonical = canonicalize_club(competition_id, name)
    known = set(_KNOWN_BY_COMPETITION.get(competition_id, {}).values())
    if canonical in known:
        return TeamAliasResult(name, canonical)
    return TeamAliasResult(name, None, name)


def match_known_club_alias(competition_id: str, name: str) -> TeamAliasResult:
    stripped = name.strip()
    aliases = _KNOWN_BY_COMPETITION.get(competition_id, {})
    canonical = aliases.get(stripped.lower())
    if canonical is None:
        return TeamAliasResult(stripped, None, stripped)
    return TeamAliasResult(stripped, canonical)
