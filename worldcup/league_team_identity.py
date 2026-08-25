from __future__ import annotations

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
        "atletico_mineiro": ("Atletico Mineiro",), "atletico_paranaense": ("Atletico Paranaense",),
        "bahia": ("Bahia",), "botafogo": ("Botafogo",), "bragantino": ("Bragantino-SP",),
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


def accepted_league_team_identity_registry() -> LeagueTeamIdentityRegistry:
    return LeagueTeamIdentityRegistry(_ACCEPTED_TEAM_GROUPS)
