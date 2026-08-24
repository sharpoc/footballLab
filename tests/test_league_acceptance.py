import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_acceptance import LeagueAcceptanceStore, acceptance_row_is_active, evaluate_league_acceptance


def _evidence(**overrides):
    value = {
        "sport_catalog": {"verified": True, "fingerprint": "sport-fp"},
        "odds_sample": {"verified": True, "fingerprint": "odds-fp"},
        "team_identity": {"verified": True, "fingerprint": "team-fp", "unmatched_count": 0},
        "result_contract": {"verified": True, "fingerprint": "result-fp"},
    }
    value.update(overrides)
    return value


def test_acceptance_state_is_derived_from_ordered_evidence_not_requested_state():
    assert evaluate_league_acceptance("epl_2026_27", {})["state"] == "disabled_until_live_acceptance"
    assert evaluate_league_acceptance(
        "epl_2026_27",
        {"sport_catalog": {"verified": True, "fingerprint": "sport-fp"}},
    )["state"] == "probing"
    assert evaluate_league_acceptance(
        "epl_2026_27",
        {**_evidence(result_contract={}), "requested_state": "active"},
    )["state"] == "identity_verified"
    assert evaluate_league_acceptance("epl_2026_27", _evidence())["state"] == "active"


def test_acceptance_blocks_identity_gaps_and_evidence_competition_mismatch():
    unmatched = evaluate_league_acceptance(
        "epl_2026_27",
        _evidence(team_identity={"verified": True, "fingerprint": "team-fp", "unmatched_count": 1}),
    )
    mismatch = evaluate_league_acceptance(
        "epl_2026_27",
        {**_evidence(), "competition_id": "laliga_2026_27"},
    )

    assert unmatched["state"] == "blocked"
    assert unmatched["reason"] == "unmatched_team_identity"
    assert mismatch["state"] == "blocked"
    assert mismatch["reason"] == "acceptance_competition_mismatch"


def test_acceptance_store_is_partition_safe_atomic_and_idempotent():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "data/local/leagues/acceptance.json"
        store = LeagueAcceptanceStore(path)
        report = {
            "schema_version": 1,
            "competitions": {
                "epl_2026_27": evaluate_league_acceptance("epl_2026_27", _evidence()),
                "laliga_2026_27": evaluate_league_acceptance("laliga_2026_27", {}),
            },
        }

        assert store.write(report) == "stored"
        first = path.read_bytes()
        assert store.write(report) == "unchanged"
        assert path.read_bytes() == first
        assert store.read() == json.loads(first)
        assert not list(path.parent.glob("*.tmp"))

        try:
            LeagueAcceptanceStore(Path(tmp) / "acceptance.json")
        except ValueError as exc:
            assert str(exc) == "league_acceptance_path_isolation"
        else:
            raise AssertionError("wrong acceptance path must fail")


def test_active_row_requires_all_four_evidence_fingerprints():
    active = evaluate_league_acceptance("epl_2026_27", _evidence())
    forged = {"competition_id": "epl_2026_27", "state": "active", "fingerprints": {}}
    assert acceptance_row_is_active(active, "epl_2026_27") is True
    assert acceptance_row_is_active(forged, "epl_2026_27") is False

    with TemporaryDirectory() as tmp:
        store = LeagueAcceptanceStore(Path(tmp) / "data/local/leagues/acceptance.json")
        try:
            store.write({"schema_version": 1, "competitions": {"epl_2026_27": forged}})
        except ValueError as exc:
            assert str(exc) == "league_acceptance_invalid_active_evidence"
        else:
            raise AssertionError("forged active row must not persist")
