import json
import importlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_result_evidence import build_result_contract_evidence
from worldcup.league_closing import LeagueClosingStore, select_league_closings
from worldcup.league_postmatch import build_league_postmatch
from worldcup.league_scheduled_publish import run_local_league_scheduler


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


def _write_lifecycle_inputs(root: Path) -> None:
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


def test_league_lifecycle_adapts_verified_scores_without_writing_formal_fotmob_state():
    assert importlib.util.find_spec("worldcup.league_lifecycle") is not None, (
        "league lifecycle orchestrator is missing"
    )
    league_lifecycle = importlib.import_module("worldcup.league_lifecycle")
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_lifecycle_inputs(root)

        dry_run = league_lifecycle.run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=False
        )
        assert dry_run["status"] == "dry_run"
        assert dry_run["competitions"]["epl_2026_27"]["status"] == "ready"
        assert dry_run["competitions"]["epl_2026_27"]["decision_tally"]["hit"] == 1
        assert not (root / "data/local/leagues/epl_2026_27/closing.json").exists()

        written = league_lifecycle.run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True
        )
        assert written["status"] == "stored"
        assert not (root / "data/local/leagues/epl_2026_27/postmatch.json").exists()
        legacy_root = root / "data/local/leagues/legacy_theoddsapi"
        assert (legacy_root / "epl_2026_27/postmatch.json").exists()
        assert (legacy_root / "statistics.json").exists()
        assert not (root / "data/local/leagues/postmatch_statistics.json").exists()
        assert not (root / "data/local/leagues/postmatch_state.json").exists()
        assert not (root / "data/local/leagues/postmatch_notification_state.json").exists()


def test_real_scheduler_lifecycle_composition_preserves_legacy_public_statistics():
    """The scheduled publisher must keep the legacy lifecycle wired until FotMob Gates A-D."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_lifecycle_inputs(root)
        acceptance = root / "data/local/leagues/acceptance.json"
        acceptance.write_text(json.dumps({
            "schema_version": 1,
            "competitions": {"epl_2026_27": {
                "competition_id": "epl_2026_27", "state": "active", "reason": None,
                "fingerprints": {
                    "sport_catalog": "sport", "odds_sample": "odds",
                    "team_identity": "teams", "result_contract": "results",
                },
            }},
        }), encoding="utf-8")
        snapshot_path = root / "data/cache/leagues/epl_2026_27/snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
        other_snapshot = _snapshot()
        other_snapshot["matches"][0].update({
            "source_event_id": "fotmob-owned-event",
            "home_team": "Liverpool", "away_team": "Everton",
            "home_canonical": "liverpool", "away_canonical": "everton",
        })
        shared_closing = root / "data/local/leagues/epl_2026_27/closing.json"
        LeagueClosingStore(shared_closing).commit(
            select_league_closings([other_snapshot], "epl_2026_27")
        )

        result = run_local_league_scheduler(
            root=root,
            now="2026-08-25T12:00:00Z",
            live=True,
            write=True,
            env_loader=lambda: {"INGEST_HMAC_SECRET": "x" * 32},
            publish_fn=lambda payload: (_ for _ in ()).throw(
                AssertionError("not-due scheduler must not publish")
            ),
        )

        assert result["status"] == "not_due"
        assert result["lifecycle"]["status"] == "stored"
        aggregate = __import__(
            "worldcup.league_scheduled_publish", fromlist=["build_aggregate_league_snapshot"]
        ).build_aggregate_league_snapshot(root=root, snapshots=[_snapshot()])
        assert aggregate["league_statistics"]["statistics_origin"] == (
            "legacy_theoddsapi_scores_compatibility"
        )
        assert aggregate["league_statistics"]["aggregate"]["decision_tally"]["hit"] == 1
        assert set(json.loads(shared_closing.read_text())["closings"]) == {
            "epl-event-1", "fotmob-owned-event",
        }
        assert not (root / "data/local/leagues/postmatch_statistics.json").exists()


def test_existing_shared_legacy_postmatch_is_archived_during_isolation_upgrade():
    """Upgrade must preserve old bytes for audit while clearing the future FotMob formal path."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_lifecycle_inputs(root)
        old_path = root / "data/local/leagues/epl_2026_27/postmatch.json"
        old_payload = {"schema_version": 2, "competition_id": "epl_2026_27", "matches": []}
        old_bytes = (json.dumps(old_payload, sort_keys=True) + "\n").encode()
        old_path.write_bytes(old_bytes)

        result = importlib.import_module("worldcup.league_lifecycle").run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True
        )

        archive = root / (
            "data/local/leagues/legacy_theoddsapi/epl_2026_27/"
            "postmatch.pre_isolation.json"
        )
        assert result["status"] == "stored"
        assert not old_path.exists()
        assert archive.read_bytes() == old_bytes
        assert (archive.parent / "postmatch.json").exists()


def test_lifecycle_statistics_recomputes_stored_postmatch_instead_of_trusting_totals():
    """A retained block with stale totals must be recomputed from verified event evidence."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        closing = {
            "schema_version": 1, "competition_id": "epl_2026_27", "closings": {"epl-1": {
                "competition_id": "epl_2026_27", "source_event_id": "epl-1",
                "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
                "home_team": "Home FC", "away_team": "Away FC",
                "home_canonical": "home_fc", "away_canonical": "away_fc",
                "closing_snapshot_at": "2026-08-24T17:59:00+00:00",
                "closing_match_decision": {"schema_version": 2, "label": "MATCH_PICK", "market": "1X2", "selection": "home"},
            }},
        }
        row = {
            "competition_id": "epl_2026_27", "source_event_id": "epl-1",
            "kickoff_at_utc": "2026-08-24T18:00:00+00:00",
            "home_team": "Home FC", "away_team": "Away FC",
            "home_canonical": "home_fc", "away_canonical": "away_fc",
            "home_score": 2, "away_score": 0, "captured_at": "2026-08-24T20:00:00+00:00",
            "result_scope": "football_90min", "source_fingerprint": "a" * 64,
        }
        core = {"schema_version": 1, "competition_id": "epl_2026_27", "results": [row]}
        encoded = json.dumps(core, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        receipt = {**core, "fingerprint": __import__("hashlib").sha256(encoded.encode("utf-8")).hexdigest()}
        stored = build_league_postmatch(closing, receipt, "epl_2026_27")
        stored["decision_tally"]["hit"] = 999
        path = root / "data/local/leagues/legacy_theoddsapi/epl_2026_27/postmatch.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(stored), encoding="utf-8")

        result = __import__("worldcup.league_lifecycle", fromlist=["run_league_lifecycle"]).run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True,
        )

        statistics = json.loads((
            root / "data/local/leagues/legacy_theoddsapi/statistics.json"
        ).read_text())
        assert result["status"] == "blocked"
        assert statistics["aggregate"]["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}


def test_lifecycle_binds_stored_postmatch_to_its_outer_partition():
    """A valid Serie A block under the EPL directory must not be reclassified during aggregation."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        core = {"schema_version": 1, "competition_id": "serie_a_2026_27", "results": []}
        encoded = json.dumps(core, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        receipt = {**core, "fingerprint": __import__("hashlib").sha256(encoded.encode("utf-8")).hexdigest()}
        closing = {"schema_version": 1, "competition_id": "serie_a_2026_27", "closings": {}}
        stored = build_league_postmatch(closing, receipt, "serie_a_2026_27")
        path = root / "data/local/leagues/legacy_theoddsapi/epl_2026_27/postmatch.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(stored), encoding="utf-8")

        result = __import__("worldcup.league_lifecycle", fromlist=["run_league_lifecycle"]).run_league_lifecycle(
            root=root, competition_ids=["epl_2026_27"], write=True,
        )

        statistics = json.loads((
            root / "data/local/leagues/legacy_theoddsapi/statistics.json"
        ).read_text())
        assert result["status"] == "blocked"
        assert statistics["competitions"] == {}
        assert statistics["excluded_competitions"] == {"epl_2026_27": "postmatch_partition_mismatch"}
