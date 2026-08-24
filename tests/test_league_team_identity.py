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
