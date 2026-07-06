from worldcup.team_identity import resolve_team_identity, team_identity_records


def test_csl_identity_registry_is_season_scoped_and_has_provider_ids():
    records = team_identity_records("csl_2026")
    shanghai = next(record for record in records if record.team_id == "csl_2026:shanghai_port")

    assert shanghai.competition_id == "csl_2026"
    assert shanghai.season_id == "2026"
    assert shanghai.canonical_key == "shanghai_port"
    assert "Shanghai SIPG FC" in shanghai.aliases
    assert shanghai.active_from == "2026-01-01"
    assert shanghai.active_to == "2026-12-31"
    assert shanghai.provider_team_ids["theoddsapi"] == ("Shanghai Port", "Shanghai SIPG FC")


def test_resolve_team_identity_does_not_fallback_unknown_clubs_as_known():
    known = resolve_team_identity("csl_2026", "Shanghai SIPG FC", provider="theoddsapi")
    unknown = resolve_team_identity("csl_2026", "Unknown FC", provider="theoddsapi")

    assert known.matched is True
    assert known.record is not None
    assert known.record.team_id == "csl_2026:shanghai_port"
    assert known.provider == "theoddsapi"
    assert unknown.matched is False
    assert unknown.record is None
    assert unknown.unmatched_name == "Unknown FC"
