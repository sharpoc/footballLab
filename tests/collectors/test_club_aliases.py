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
