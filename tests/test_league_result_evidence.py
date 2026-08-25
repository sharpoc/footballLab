from worldcup.league_result_evidence import build_result_contract_evidence, verify_result_contract_evidence


FOTMOB_SAMPLE_SHA256 = "a861a1aa1c83b7193ea68a6705abc44647fb49194ee80a557356b55fe5bf1e00"


def test_result_contract_evidence_binds_competition_sport_key_schema_and_scope():
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )

    assert verify_result_contract_evidence(evidence, "epl_2026_27") is True
    assert verify_result_contract_evidence(evidence, "laliga_2026_27") is False
    assert evidence["fingerprint"]


def test_result_contract_evidence_rejects_unproven_or_wrong_scope():
    unproven = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="completed_score_unspecified",
        source_reference="saved-sample-sha256",
    )
    assert unproven["verified"] is False
    assert verify_result_contract_evidence({**unproven, "verified": True}, "epl_2026_27") is False


def test_result_contract_evidence_accepts_verified_fotmob_90_minute_schema():
    """Restricting evidence to scores API would block the quota-free FotMob result source."""
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
    )

    assert evidence["verified"] is True
    assert verify_result_contract_evidence(evidence, "epl_2026_27") is True
    assert verify_result_contract_evidence(
        {**evidence, "provider_schema": "fotmob_league_results_v2"},
        "epl_2026_27",
    ) is False


def test_fotmob_evidence_requires_its_provider_schema_and_saved_sample_sha256():
    """Accepting a legacy provider or a label instead of sample content would bypass FotMob semantics proof."""
    fotmob = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="fotmob",
    )
    legacy = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )
    short_reference = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="saved-fotmob-sample",
        provider="fotmob",
    )
    wrong_provider = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=FOTMOB_SAMPLE_SHA256,
        provider="theoddsapi",
    )

    assert verify_result_contract_evidence(
        fotmob, "epl_2026_27", provider_schema="fotmob_league_results_v1"
    ) is True
    assert verify_result_contract_evidence(
        legacy, "epl_2026_27", provider_schema="fotmob_league_results_v1"
    ) is False
    assert short_reference["verified"] is False
    assert wrong_provider["verified"] is False
    assert verify_result_contract_evidence(
        {**short_reference, "verified": True},
        "epl_2026_27",
        provider_schema="fotmob_league_results_v1",
    ) is False
    assert verify_result_contract_evidence(
        {**wrong_provider, "verified": True},
        "epl_2026_27",
        provider_schema="fotmob_league_results_v1",
    ) is False
