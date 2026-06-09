from worldcup.collectors.team_aliases import canonicalize_team, match_team_alias


def test_canonicalize_team_handles_required_aliases():
    assert canonicalize_team("Czech Republic") == "czech_republic"
    assert canonicalize_team("Czechia") == "czech_republic"
    assert canonicalize_team("USA") == "united_states"
    assert canonicalize_team("United States") == "united_states"
    assert canonicalize_team("Bosnia & Herzegovina") == "bosnia_herzegovina"


def test_match_team_alias_reports_unmatched_name():
    result = match_team_alias("Imaginary FC")
    assert result.canonical_key is None
    assert result.unmatched_name == "Imaginary FC"
