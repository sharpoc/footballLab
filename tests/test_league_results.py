from worldcup.league_results import (
    adapt_theoddsapi_results_to_committed_receipt,
    parse_verified_league_results,
)
from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def _score_event() -> dict:
    return {
        "id": "epl-event-1",
        "sport_key": "soccer_epl",
        "commence_time": "2026-08-24T18:00:00Z",
        "completed": True,
        "home_team": "Home FC",
        "away_team": "Away FC",
        "scores": [
            {"name": "Home FC", "score": "2"},
            {"name": "Away FC", "score": "0"},
        ],
        "last_update": "2026-08-24T20:00:00Z",
    }


def test_unverified_score_semantics_cannot_create_formal_result():
    parsed = parse_verified_league_results(
        [_score_event()], "epl_2026_27", result_contract_evidence=None
    )
    assert parsed["results"] == []
    assert parsed["pending"][0]["reason"] == "result_90min_semantics_unverified"


def test_verified_completed_integer_score_is_accepted():
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )
    parsed = parse_verified_league_results(
        [_score_event()],
        "epl_2026_27",
        result_contract_evidence=evidence,
        identity_registry=LeagueTeamIdentityRegistry({
            "epl_2026_27": {"home": ("Home FC",), "away": ("Away FC",)},
        }),
    )
    assert parsed["results"][0]["home_score"] == 2
    assert parsed["results"][0]["away_score"] == 0
    assert parsed["results"][0]["result_scope"] == "football_90min"
    assert parsed["results"][0]["home_canonical"] == "home"


def test_verified_result_without_strict_identity_registry_stays_pending():
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )
    parsed = parse_verified_league_results(
        [_score_event()], "epl_2026_27", result_contract_evidence=evidence
    )
    assert parsed["results"] == []
    assert parsed["pending"] == [{
        "source_event_id": "epl-event-1",
        "reason": "strict_team_identity_unavailable",
    }]


def test_fotmob_evidence_cannot_authorize_theoddsapi_scores_parser():
    """A verified score contract for another provider must not cross-authorize this parser."""
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="a" * 64,
        provider="fotmob",
    )

    parsed = parse_verified_league_results(
        [_score_event()],
        "epl_2026_27",
        result_contract_evidence=evidence,
        identity_registry=LeagueTeamIdentityRegistry({
            "epl_2026_27": {"home": ("Home FC",), "away": ("Away FC",)},
        }),
    )

    assert parsed["results"] == []
    assert parsed["pending"] == [{
        "source_event_id": "epl-event-1",
        "reason": "result_90min_semantics_unverified",
    }]


def test_theoddsapi_adapter_builds_a_task2_committed_receipt():
    """The wired legacy lifecycle must cross the Task 2 receipt boundary explicitly."""
    evidence = build_result_contract_evidence(
        competition_id="epl_2026_27",
        sport_key="soccer_epl",
        provider_schema="theoddsapi_scores_v1",
        score_scope="football_90min",
        source_reference="saved-sample-sha256",
    )
    adapted = adapt_theoddsapi_results_to_committed_receipt(
        [_score_event()],
        "epl_2026_27",
        result_contract_evidence=evidence,
        identity_registry=LeagueTeamIdentityRegistry({
            "epl_2026_27": {"home": ("Home FC",), "away": ("Away FC",)},
        }),
    )

    assert adapted["provider_schema"] == "theoddsapi_scores_v1"
    assert adapted["pending"] == []
    assert adapted["receipt"]["schema_version"] == 1
    assert adapted["receipt"]["competition_id"] == "epl_2026_27"
    assert len(adapted["receipt"]["fingerprint"]) == 64
    assert len(adapted["receipt"]["results"][0]["source_fingerprint"]) == 64
