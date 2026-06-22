from __future__ import annotations

import re
import unicodedata

from worldcup.collectors.models import TeamAliasResult


_CSL_ALIASES = {
    "shanghai port": "shanghai_port",
    "shanghai sipg": "shanghai_port",
    "shanghai port fc": "shanghai_port",
    "shanghai shenhua": "shanghai_shenhua",
    "shandong taishan": "shandong_taishan",
    "beijing guoan": "beijing_guoan",
    "chengdu rongcheng": "chengdu_rongcheng",
    "zhejiang professional": "zhejiang_professional",
    "henan fc": "henan",
    "tianjin jinmen tiger": "tianjin_jinmen_tiger",
    "wuhan three towns": "wuhan_three_towns",
    "meizhou hakka": "meizhou_hakka",
    "qingdao west coast": "qingdao_west_coast",
    "qingdao hainiu": "qingdao_hainiu",
    "changchun yatai": "changchun_yatai",
    "shenzhen peng city": "shenzhen_peng_city",
    "yunnan yukun": "yunnan_yukun",
    "dalian yingbo": "dalian_yingbo",
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
