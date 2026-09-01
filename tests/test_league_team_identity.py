import worldcup.league_team_identity as league_team_identity
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def test_league_identity_is_explicit_competition_scoped_and_never_slug_falls_back():
    registry = LeagueTeamIdentityRegistry({
        "epl_2026_27": {
            "arsenal": ("Arsenal", "Arsenal FC"),
            "chelsea": ("Chelsea", "Chelsea FC"),
        },
        "laliga_2026_27": {"arsenal_de_sarandi": ("Arsenal",)},
    })

    assert registry.resolve("epl_2026_27", "Arsenal FC").canonical == "arsenal"
    assert registry.resolve("laliga_2026_27", "Arsenal").canonical == "arsenal_de_sarandi"
    assert registry.resolve("epl_2026_27", "Unknown United").canonical is None
    assert registry.resolve("epl_2026_27", "Unknown United").reason == "unmatched_team"


def test_league_identity_rejects_ambiguous_aliases_and_same_team_fixture():
    try:
        LeagueTeamIdentityRegistry({
            "epl_2026_27": {"club_a": ("United",), "club_b": ("United",)},
        })
    except ValueError as exc:
        assert str(exc) == "league_team_alias_ambiguous:epl_2026_27:united"
    else:
        raise AssertionError("ambiguous alias must fail")

    registry = LeagueTeamIdentityRegistry({"epl_2026_27": {"arsenal": ("Arsenal", "Arsenal FC")}})
    result = registry.resolve_fixture("epl_2026_27", "Arsenal", "Arsenal FC")
    assert result["status"] == "blocked"
    assert result["reason"] == "same_team_identity"


def test_accepted_registry_resolves_every_verified_serie_a_provider_team_and_rejects_unknowns():
    assert hasattr(league_team_identity, "accepted_league_team_identity_registry"), (
        "accepted Serie A identity registry is missing"
    )
    registry = league_team_identity.accepted_league_team_identity_registry()
    expected = {
        "AC Milan": "ac_milan",
        "AS Roma": "as_roma",
        "Roma": "as_roma",
        "Atalanta BC": "atalanta",
        "Bologna": "bologna",
        "Cagliari": "cagliari",
        "Como": "como",
        "Fiorentina": "fiorentina",
        "Frosinone": "frosinone",
        "Genoa": "genoa",
        "Inter Milan": "inter_milan",
        "Juventus": "juventus",
        "Lazio": "lazio",
        "Lecce": "lecce",
        "Monza": "monza",
        "Napoli": "napoli",
        "Parma": "parma",
        "Sassuolo": "sassuolo",
        "Torino": "torino",
        "Udinese": "udinese",
        "Venezia": "venezia",
    }

    assert {
        provider_name: registry.resolve("serie_a_2026_27", provider_name).canonical
        for provider_name in expected
    } == expected
    assert registry.resolve("serie_a_2026_27", "Unknown Calcio").canonical is None
    assert registry.resolve("epl_2026_27", "AC Milan").canonical is None


def test_accepted_registry_covers_verified_provider_teams_for_the_other_five_leagues():
    registry = league_team_identity.accepted_league_team_identity_registry()
    expected = {
        "epl_2026_27": {
            "Arsenal": "arsenal", "Aston Villa": "aston_villa", "Bournemouth": "bournemouth",
            "Brentford": "brentford", "Brighton and Hove Albion": "brighton_and_hove_albion",
            "Chelsea": "chelsea", "Coventry City": "coventry_city", "Crystal Palace": "crystal_palace",
            "Everton": "everton", "Fulham": "fulham", "Hull City": "hull_city",
            "Ipswich Town": "ipswich_town", "Leeds United": "leeds_united", "Liverpool": "liverpool",
            "Manchester City": "manchester_city", "Manchester United": "manchester_united",
            "Newcastle United": "newcastle_united", "Nottingham Forest": "nottingham_forest",
            "Sunderland": "sunderland", "Tottenham Hotspur": "tottenham_hotspur",
        },
        "laliga_2026_27": {
            "Alavés": "alaves", "Athletic Bilbao": "athletic_bilbao", "Atlético Madrid": "atletico_madrid",
            "Barcelona": "barcelona", "CA Osasuna": "ca_osasuna", "Celta Vigo": "celta_vigo",
            "Deportivo La Coruña": "deportivo_la_coruna", "Elche CF": "elche", "Espanyol": "espanyol",
            "Getafe": "getafe", "Levante": "levante", "Málaga": "malaga", "Rayo Vallecano": "rayo_vallecano",
            "Real Betis": "real_betis", "Real Madrid": "real_madrid",
            "Real Racing Club de Santander": "racing_santander", "Real Sociedad": "real_sociedad",
            "Sevilla": "sevilla", "Valencia": "valencia", "Villarreal": "villarreal",
        },
        "bundesliga_2026_27": {
            "1. FC Köln": "fc_koln", "Augsburg": "augsburg", "Bayer Leverkusen": "bayer_leverkusen",
            "Bayern Munich": "bayern_munich", "Bayern München": "bayern_munich",
            "Borussia Dortmund": "borussia_dortmund",
            "Borussia Monchengladbach": "borussia_monchengladbach", "Eintracht Frankfurt": "eintracht_frankfurt",
            "Elversberg": "elversberg", "FC Schalke 04": "schalke_04", "FSV Mainz 05": "mainz_05",
            "Hamburger SV": "hamburger_sv", "RB Leipzig": "rb_leipzig", "SC Freiburg": "freiburg",
            "SC Paderborn": "paderborn", "TSG Hoffenheim": "hoffenheim", "Union Berlin": "union_berlin",
            "VfB Stuttgart": "stuttgart", "Werder Bremen": "werder_bremen",
        },
        "ligue_1_2026_27": {
            "AS Monaco": "as_monaco", "Angers": "angers", "Auxerre": "auxerre", "Brest": "brest",
            "Le Havre": "le_havre", "Le Mans FC": "le_mans", "Lille": "lille", "Lorient": "lorient",
            "Lyon": "lyon", "Marseille": "marseille", "Nice": "nice", "Paris FC": "paris_fc",
            "Paris Saint Germain": "paris_saint_germain", "RC Lens": "rc_lens", "Rennes": "rennes",
            "Strasbourg": "strasbourg", "Toulouse": "toulouse", "Troyes": "troyes",
        },
        "serie_a_brazil_2026": {
            "Atletico Mineiro": "atletico_mineiro", "Atletico Paranaense": "atletico_paranaense",
            "Athletico Paranaense": "atletico_paranaense", "Bahia": "bahia",
            "Botafogo": "botafogo", "Botafogo RJ": "botafogo", "Bragantino-SP": "bragantino",
            "Chapecoense": "chapecoense", "Corinthians": "corinthians", "Coritiba": "coritiba",
            "Cruzeiro": "cruzeiro", "Flamengo": "flamengo", "Fluminense": "fluminense",
            "Grêmio": "gremio", "Internacional": "internacional", "Mirassol": "mirassol",
            "Palmeiras": "palmeiras", "Remo": "remo", "Santos": "santos", "Sao Paulo": "sao_paulo",
            "Vasco da Gama": "vasco_da_gama", "Vitoria": "vitoria",
        },
    }

    for competition_id, teams in expected.items():
        assert {
            provider_name: registry.resolve(competition_id, provider_name).canonical
            for provider_name in teams
        } == teams
    assert registry.resolve("bundesliga_2026_27", "Arsenal").canonical is None


def test_accepted_league_teams_have_competition_scoped_chinese_display_names():
    assert hasattr(league_team_identity, "league_team_display_name_zh")
    display_name = league_team_identity.league_team_display_name_zh
    expected = {
        ("epl_2026_27", "Arsenal"): "阿森纳",
        ("laliga_2026_27", "Real Madrid"): "皇家马德里",
        ("bundesliga_2026_27", "Bayern Munich"): "拜仁慕尼黑",
        ("ligue_1_2026_27", "Paris Saint Germain"): "巴黎圣日耳曼",
        ("serie_a_2026_27", "Inter Milan"): "国际米兰",
        ("serie_a_brazil_2026", "Flamengo"): "弗拉门戈",
    }
    assert {
        key: display_name(*key)
        for key in expected
    } == expected

    for competition_id, groups in league_team_identity._ACCEPTED_TEAM_GROUPS.items():
        for aliases in groups.values():
            for provider_name in aliases:
                translated = display_name(competition_id, provider_name)
                assert translated
                assert translated != provider_name

    assert display_name("epl_2026_27", "Unknown United") is None
    assert display_name("laliga_2026_27", "Arsenal") is None
