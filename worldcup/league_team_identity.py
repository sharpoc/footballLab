from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


_ACCEPTED_TEAM_GROUPS: dict[str, dict[str, tuple[str, ...]]] = {
    "epl_2026_27": {
        "arsenal": ("Arsenal",), "aston_villa": ("Aston Villa",), "bournemouth": ("Bournemouth",),
        "brentford": ("Brentford",), "brighton_and_hove_albion": ("Brighton and Hove Albion",),
        "chelsea": ("Chelsea",), "coventry_city": ("Coventry City",),
        "crystal_palace": ("Crystal Palace",), "everton": ("Everton",), "fulham": ("Fulham",),
        "hull_city": ("Hull City",), "ipswich_town": ("Ipswich Town",),
        "leeds_united": ("Leeds United",), "liverpool": ("Liverpool",),
        "manchester_city": ("Manchester City",), "manchester_united": ("Manchester United",),
        "newcastle_united": ("Newcastle United",), "nottingham_forest": ("Nottingham Forest",),
        "sunderland": ("Sunderland",), "tottenham_hotspur": ("Tottenham Hotspur",),
    },
    "laliga_2026_27": {
        "alaves": ("Alavés",), "athletic_bilbao": ("Athletic Bilbao",),
        "atletico_madrid": ("Atlético Madrid",), "barcelona": ("Barcelona",),
        "ca_osasuna": ("CA Osasuna",), "celta_vigo": ("Celta Vigo",),
        "deportivo_la_coruna": ("Deportivo La Coruña",), "elche": ("Elche CF",),
        "espanyol": ("Espanyol",), "getafe": ("Getafe",), "levante": ("Levante",),
        "malaga": ("Málaga",), "rayo_vallecano": ("Rayo Vallecano",),
        "real_betis": ("Real Betis",), "real_madrid": ("Real Madrid",),
        "racing_santander": ("Real Racing Club de Santander",), "real_sociedad": ("Real Sociedad",),
        "sevilla": ("Sevilla",), "valencia": ("Valencia",), "villarreal": ("Villarreal",),
    },
    "bundesliga_2026_27": {
        "fc_koln": ("1. FC Köln",), "augsburg": ("Augsburg",),
        "bayer_leverkusen": ("Bayer Leverkusen",), "bayern_munich": ("Bayern Munich",),
        "borussia_dortmund": ("Borussia Dortmund",),
        "borussia_monchengladbach": ("Borussia Monchengladbach",),
        "eintracht_frankfurt": ("Eintracht Frankfurt",), "elversberg": ("Elversberg",),
        "schalke_04": ("FC Schalke 04",), "mainz_05": ("FSV Mainz 05",),
        "hamburger_sv": ("Hamburger SV",), "rb_leipzig": ("RB Leipzig",),
        "freiburg": ("SC Freiburg",), "paderborn": ("SC Paderborn",),
        "hoffenheim": ("TSG Hoffenheim",), "union_berlin": ("Union Berlin",),
        "stuttgart": ("VfB Stuttgart",), "werder_bremen": ("Werder Bremen",),
    },
    "ligue_1_2026_27": {
        "as_monaco": ("AS Monaco",), "angers": ("Angers",), "auxerre": ("Auxerre",),
        "brest": ("Brest",), "le_havre": ("Le Havre",), "le_mans": ("Le Mans FC",),
        "lille": ("Lille",), "lorient": ("Lorient",), "lyon": ("Lyon",),
        "marseille": ("Marseille",), "nice": ("Nice",), "paris_fc": ("Paris FC",),
        "paris_saint_germain": ("Paris Saint Germain",), "rc_lens": ("RC Lens",),
        "rennes": ("Rennes",), "strasbourg": ("Strasbourg",),
        "toulouse": ("Toulouse",), "troyes": ("Troyes",),
    },
    "serie_a_brazil_2026": {
        "atletico_mineiro": ("Atletico Mineiro",),
        "atletico_paranaense": ("Atletico Paranaense", "Athletico Paranaense"),
        "bahia": ("Bahia",), "botafogo": ("Botafogo", "Botafogo RJ"), "bragantino": ("Bragantino-SP",),
        "chapecoense": ("Chapecoense",), "corinthians": ("Corinthians",),
        "coritiba": ("Coritiba",), "cruzeiro": ("Cruzeiro",), "flamengo": ("Flamengo",),
        "fluminense": ("Fluminense",), "gremio": ("Grêmio",),
        "internacional": ("Internacional",), "mirassol": ("Mirassol",),
        "palmeiras": ("Palmeiras",), "remo": ("Remo",), "santos": ("Santos",),
        "sao_paulo": ("Sao Paulo",), "vasco_da_gama": ("Vasco da Gama",),
        "vitoria": ("Vitoria",),
    },
    "serie_a_2026_27": {
        "ac_milan": ("AC Milan",),
        "as_roma": ("AS Roma",),
        "atalanta": ("Atalanta BC",),
        "bologna": ("Bologna",),
        "cagliari": ("Cagliari",),
        "como": ("Como",),
        "fiorentina": ("Fiorentina",),
        "frosinone": ("Frosinone",),
        "genoa": ("Genoa",),
        "inter_milan": ("Inter Milan",),
        "juventus": ("Juventus",),
        "lazio": ("Lazio",),
        "lecce": ("Lecce",),
        "monza": ("Monza",),
        "napoli": ("Napoli",),
        "parma": ("Parma",),
        "sassuolo": ("Sassuolo",),
        "torino": ("Torino",),
        "udinese": ("Udinese",),
        "venezia": ("Venezia",),
    },
}

_TEAM_DISPLAY_NAMES_ZH: dict[str, dict[str, str]] = {
    "epl_2026_27": {
        "arsenal": "阿森纳", "aston_villa": "阿斯顿维拉", "bournemouth": "伯恩茅斯",
        "brentford": "布伦特福德", "brighton_and_hove_albion": "布莱顿",
        "chelsea": "切尔西", "coventry_city": "考文垂", "crystal_palace": "水晶宫",
        "everton": "埃弗顿", "fulham": "富勒姆", "hull_city": "赫尔城",
        "ipswich_town": "伊普斯维奇", "leeds_united": "利兹联", "liverpool": "利物浦",
        "manchester_city": "曼城", "manchester_united": "曼联", "newcastle_united": "纽卡斯尔联",
        "nottingham_forest": "诺丁汉森林", "sunderland": "桑德兰", "tottenham_hotspur": "托特纳姆热刺",
    },
    "laliga_2026_27": {
        "alaves": "阿拉维斯", "athletic_bilbao": "毕尔巴鄂竞技", "atletico_madrid": "马德里竞技",
        "barcelona": "巴塞罗那", "ca_osasuna": "奥萨苏纳", "celta_vigo": "塞尔塔",
        "deportivo_la_coruna": "拉科鲁尼亚", "elche": "埃尔切", "espanyol": "西班牙人",
        "getafe": "赫塔费", "levante": "莱万特", "malaga": "马拉加",
        "rayo_vallecano": "巴列卡诺", "real_betis": "皇家贝蒂斯", "real_madrid": "皇家马德里",
        "racing_santander": "桑坦德竞技", "real_sociedad": "皇家社会", "sevilla": "塞维利亚",
        "valencia": "瓦伦西亚", "villarreal": "比利亚雷亚尔",
    },
    "bundesliga_2026_27": {
        "fc_koln": "科隆", "augsburg": "奥格斯堡", "bayer_leverkusen": "勒沃库森",
        "bayern_munich": "拜仁慕尼黑", "borussia_dortmund": "多特蒙德",
        "borussia_monchengladbach": "门兴格拉德巴赫", "eintracht_frankfurt": "法兰克福",
        "elversberg": "埃尔弗斯堡", "schalke_04": "沙尔克04", "mainz_05": "美因茨05",
        "hamburger_sv": "汉堡", "rb_leipzig": "RB莱比锡", "freiburg": "弗赖堡",
        "paderborn": "帕德博恩", "hoffenheim": "霍芬海姆", "union_berlin": "柏林联合",
        "stuttgart": "斯图加特", "werder_bremen": "云达不莱梅",
    },
    "ligue_1_2026_27": {
        "as_monaco": "摩纳哥", "angers": "昂热", "auxerre": "欧塞尔", "brest": "布雷斯特",
        "le_havre": "勒阿弗尔", "le_mans": "勒芒", "lille": "里尔", "lorient": "洛里昂",
        "lyon": "里昂", "marseille": "马赛", "nice": "尼斯", "paris_fc": "巴黎FC",
        "paris_saint_germain": "巴黎圣日耳曼", "rc_lens": "朗斯", "rennes": "雷恩",
        "strasbourg": "斯特拉斯堡", "toulouse": "图卢兹", "troyes": "特鲁瓦",
    },
    "serie_a_brazil_2026": {
        "atletico_mineiro": "米内罗竞技", "atletico_paranaense": "巴拉纳竞技",
        "bahia": "巴伊亚", "botafogo": "博塔弗戈", "bragantino": "布拉干蒂诺",
        "chapecoense": "沙佩科恩斯", "corinthians": "科林蒂安", "coritiba": "科里蒂巴",
        "cruzeiro": "克鲁塞罗", "flamengo": "弗拉门戈", "fluminense": "弗鲁米嫩塞",
        "gremio": "格雷米奥", "internacional": "巴西国际", "mirassol": "米拉索尔",
        "palmeiras": "帕尔梅拉斯", "remo": "瑞模贝雷", "santos": "桑托斯",
        "sao_paulo": "圣保罗", "vasco_da_gama": "瓦斯科达伽马", "vitoria": "维多利亚",
    },
    "serie_a_2026_27": {
        "ac_milan": "AC米兰", "as_roma": "罗马", "atalanta": "亚特兰大", "bologna": "博洛尼亚",
        "cagliari": "卡利亚里", "como": "科莫", "fiorentina": "佛罗伦萨", "frosinone": "弗罗西诺内",
        "genoa": "热那亚", "inter_milan": "国际米兰", "juventus": "尤文图斯", "lazio": "拉齐奥",
        "lecce": "莱切", "monza": "蒙扎", "napoli": "那不勒斯", "parma": "帕尔马",
        "sassuolo": "萨索洛", "torino": "都灵", "udinese": "乌迪内斯", "venezia": "威尼斯",
    },
}


@dataclass(frozen=True)
class LeagueTeamIdentityResult:
    source_name: str
    canonical: str | None
    reason: str | None = None


class LeagueTeamIdentityRegistry:
    def __init__(self, groups: Mapping[str, Mapping[str, Sequence[str]]]) -> None:
        self._aliases: dict[str, dict[str, str]] = {}
        for competition_id, competition_groups in groups.items():
            if competition_id not in FORMAL_SINGLE_MATCH_IDS:
                raise ValueError("league_team_competition_not_allowed")
            aliases: dict[str, str] = {}
            for canonical, names in competition_groups.items():
                canonical_key = str(canonical).strip()
                if not canonical_key:
                    raise ValueError("league_team_canonical_invalid")
                for name in names:
                    key = str(name).strip().casefold()
                    if not key:
                        raise ValueError("league_team_alias_invalid")
                    existing = aliases.get(key)
                    if existing is not None and existing != canonical_key:
                        raise ValueError(f"league_team_alias_ambiguous:{competition_id}:{key}")
                    aliases[key] = canonical_key
            self._aliases[competition_id] = aliases

    def resolve(self, competition_id: str, provider_name: str) -> LeagueTeamIdentityResult:
        source = str(provider_name).strip()
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            return LeagueTeamIdentityResult(source, None, "competition_not_allowed")
        canonical = self._aliases.get(competition_id, {}).get(source.casefold())
        return LeagueTeamIdentityResult(source, canonical, None if canonical else "unmatched_team")

    def resolve_fixture(self, competition_id: str, home: str, away: str) -> dict[str, str | None]:
        home_result = self.resolve(competition_id, home)
        away_result = self.resolve(competition_id, away)
        if home_result.canonical is None or away_result.canonical is None:
            return {
                "status": "blocked",
                "reason": "unmatched_team",
                "home_canonical": home_result.canonical,
                "away_canonical": away_result.canonical,
            }
        if home_result.canonical == away_result.canonical:
            return {
                "status": "blocked",
                "reason": "same_team_identity",
                "home_canonical": home_result.canonical,
                "away_canonical": away_result.canonical,
            }
        return {
            "status": "verified",
            "reason": None,
            "home_canonical": home_result.canonical,
            "away_canonical": away_result.canonical,
        }


def league_team_identity_registry_fingerprint(
    registry: LeagueTeamIdentityRegistry,
    competition_id: str,
) -> str:
    """Bind the complete normalized strict alias registry for one competition."""
    if competition_id not in FORMAL_SINGLE_MATCH_IDS:
        raise ValueError("league_team_competition_not_allowed")
    aliases = registry._aliases.get(competition_id)
    if not isinstance(aliases, dict) or not aliases:
        raise ValueError("league_team_registry_missing")
    payload = {
        "competition_id": competition_id,
        "aliases": [
            {"provider_name": provider_name, "canonical": aliases[provider_name]}
            for provider_name in sorted(aliases)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def accepted_league_team_identity_registry() -> LeagueTeamIdentityRegistry:
    return LeagueTeamIdentityRegistry(_ACCEPTED_TEAM_GROUPS)


def league_team_display_name_zh(competition_id: str, provider_name: str) -> str | None:
    identity = accepted_league_team_identity_registry().resolve(competition_id, provider_name)
    if identity.canonical is None:
        return None
    return _TEAM_DISPLAY_NAMES_ZH.get(competition_id, {}).get(identity.canonical)
