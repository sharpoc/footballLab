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
