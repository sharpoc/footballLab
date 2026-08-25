import json
import importlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_result_evidence import build_result_contract_evidence


def _snapshot() -> dict:
    competition = {"id": "epl_2026_27", "name": "英超 2026/27"}
    return {
        "snapshot_at": "2026-08-24T17:50:00+00:00",
        "snapshot_id": "epl-snapshot-1",
        "competition": competition,
        "matches": [{
            "source_event_id": "epl-event-1",
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Arsenal", "away_team": "Chelsea",
            "home_canonical": "arsenal", "away_canonical": "chelsea",
            "competition": competition,
            "match_decision": {
                "schema_version": 2, "label": "MATCH_PICK", "policy_version": "match_pick_v3",
                "market": "1X2", "selection": "home", "odds": 1.8,
            },
        }],
    }


def test_league_lifecycle_refuses_uncommitted_raw_scores_without_postmatch_write():
    assert importlib.util.find_spec("worldcup.league_lifecycle") is not None, (
        "league lifecycle orchestrator is missing"
    )
    league_lifecycle = importlib.import_module("worldcup.league_lifecycle")
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "data/local/leagues/epl_2026_27/history/epl-snapshot-1.json"
        history.parent.mkdir(parents=True)
        history.write_text(json.dumps(_snapshot()), encoding="utf-8")
        scores = root / "data/cache/leagues/epl_2026_27/scores.json"
        scores.parent.mkdir(parents=True)
        scores.write_text(json.dumps([{
            "id": "epl-event-1", "sport_key": "soccer_epl", "completed": True,
            "commence_time": "2026-08-24T18:00:00Z", "last_update": "2026-08-24T20:00:00Z",
            "home_team": "Arsenal", "away_team": "Chelsea",
            "scores": [{"name": "Arsenal", "score": "2"}, {"name": "Chelsea", "score": "0"}],
        }]), encoding="utf-8")
        evidence = build_result_contract_evidence(
            competition_id="epl_2026_27", sport_key="soccer_epl",
            provider_schema="theoddsapi_scores_v1", score_scope="football_90min",
            source_reference="official-result-crosscheck+saved-sample-sha256",
        )
        evidence_path = root / "data/local/leagues/epl_2026_27/result_contract_evidence.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

        dry_run = league_lifecycle.run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=False
        )
        assert dry_run["status"] == "dry_run"
        assert dry_run["competitions"]["epl_2026_27"] == {"status": "error", "reason": "ValueError"}
        assert not (root / "data/local/leagues/epl_2026_27/closing.json").exists()

        written = league_lifecycle.run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True
        )
        assert written["status"] == "blocked"
        assert not (root / "data/local/leagues/epl_2026_27/postmatch.json").exists()

        scores.unlink()
        degraded = league_lifecycle.run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True
        )
        assert degraded["status"] == "blocked"
