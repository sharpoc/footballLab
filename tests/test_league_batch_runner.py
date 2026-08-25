from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_batch_runner import run_league_batch, run_planned_league_refresh
from worldcup.league_acceptance import acceptance_fingerprint, evaluate_league_acceptance
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def _epl_registry():
    return LeagueTeamIdentityRegistry({"epl_2026_27": {"home": ("Home FC",), "away": ("Away FC",)}})


def _fail(*args, **kwargs):
    raise AssertionError("dependency must not be called")


def test_batch_dry_run_does_not_read_env_call_transport_or_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_league_batch(
            root=root,
            observed_at="2026-08-24T12:00:00Z",
            live=False,
            write=False,
            env_loader=_fail,
            odds_fetcher=_fail,
            score_fetcher=_fail,
        )
        assert result["status"] == "dry_run"
        assert set(result["competitions"]) == set(FORMAL_SINGLE_MATCH_IDS)
        assert list(root.rglob("*")) == []


def test_live_and_write_remain_blocked_before_acceptance():
    result = run_league_batch(root=".", observed_at="2026-08-24T12:00:00Z", live=True)
    assert result == {"status": "blocked", "reason": "live_acceptance_not_enabled"}


def test_batch_isolates_one_league_failure_and_reports_partial_status():
    payloads = {competition_id: [] for competition_id in FORMAL_SINGLE_MATCH_IDS}

    def build(payload, competition_id, observed_at):
        del payload, observed_at
        if competition_id == "epl_2026_27":
            raise ValueError("invalid fixture identity")
        return {"matches": [{"competition_id": competition_id}]}

    result = run_league_batch(
        root=".",
        observed_at="2026-08-24T12:00:00Z",
        odds_payloads=payloads,
        snapshot_builder=build,
    )

    assert result["status"] == "partial"
    assert result["competitions"]["epl_2026_27"] == {"status": "error", "reason": "ValueError"}
    assert sum(row["status"] == "built" for row in result["competitions"].values()) == 5


def test_live_batch_writes_only_evidence_active_league_and_keeps_others_blocked():
    with TemporaryDirectory() as tmp:
        def build(payload, competition_id, observed_at, **kwargs):
            del payload
            return {
                "snapshot_at": observed_at,
                "competition": {"id": competition_id},
                "matches": [{"competition_id": competition_id}],
            }

        result = run_league_batch(
            root=tmp,
            observed_at="2026-08-24T12:00:00Z",
            live=True,
            write=True,
            odds_payloads={"epl_2026_27": []},
            acceptance_report={
                "schema_version": 1,
                "competitions": {
                    "epl_2026_27": evaluate_league_acceptance("epl_2026_27", {
                        "sport_catalog": {"verified": True, "fingerprint": "sport-fp"},
                        "odds_sample": {"verified": True, "fingerprint": "odds-fp"},
                        "team_identity": {"verified": True, "fingerprint": "team-fp", "unmatched_count": 0},
                        "result_contract": {"verified": True, "fingerprint": "result-fp"},
                    }),
                    "laliga_2026_27": {"competition_id": "laliga_2026_27", "state": "probing"},
                },
            },
            snapshot_builder=build,
            identity_registry=_epl_registry(),
        )

        assert result["status"] == "refreshed"
        assert result["competitions"]["epl_2026_27"]["status"] == "built"
        assert result["competitions"]["laliga_2026_27"]["status"] == "blocked"
        assert (Path(tmp) / "data/cache/leagues/epl_2026_27/snapshot.json").exists()
        assert not (Path(tmp) / "data/cache/leagues/laliga_2026_27/snapshot.json").exists()


def test_live_batch_fetches_missing_payload_only_for_evidence_active_league():
    evidence = {
        "sport_catalog": {"verified": True, "fingerprint": "sport-fp"},
        "odds_sample": {"verified": True, "fingerprint": "odds-fp"},
        "team_identity": {"verified": True, "fingerprint": "team-fp", "unmatched_count": 0},
        "result_contract": {"verified": True, "fingerprint": "result-fp"},
    }
    calls = []

    def fetch(sport_key, env):
        calls.append((sport_key, sorted(env)))
        return []

    with TemporaryDirectory() as tmp:
        result = run_league_batch(
            root=tmp,
            observed_at="2026-08-24T12:00:00Z",
            live=True,
            write=True,
            acceptance_report={
                "schema_version": 1,
                "competitions": {
                    "epl_2026_27": evaluate_league_acceptance("epl_2026_27", evidence),
                },
            },
            env_loader=lambda: {"THE_ODDS_API_KEY_PRIMARY": "secret"},
            odds_fetcher=fetch,
            identity_registry=_epl_registry(),
        )

    assert result["competitions"]["epl_2026_27"]["status"] == "empty"
    assert calls == [("soccer_epl", ["THE_ODDS_API_KEY_PRIMARY"])]


def test_live_batch_blocks_without_strict_identity_registry_before_env_or_write():
    evidence = {
        "sport_catalog": {"verified": True, "fingerprint": "sport-fp"},
        "odds_sample": {"verified": True, "fingerprint": "odds-fp"},
        "team_identity": {"verified": True, "fingerprint": "team-fp", "unmatched_count": 0},
        "result_contract": {"verified": True, "fingerprint": "result-fp"},
    }
    result = run_league_batch(
        root=".",
        observed_at="2026-08-24T12:00:00Z",
        live=True,
        write=True,
        acceptance_report={"schema_version": 1, "competitions": {
            "epl_2026_27": evaluate_league_acceptance("epl_2026_27", evidence),
        }},
        env_loader=_fail,
    )
    assert result == {"status": "blocked", "reason": "strict_identity_registry_required"}


def test_planned_refresh_fetches_only_requested_competition_and_returns_commit_receipt():
    evidence = {
        "sport_catalog": {"verified": True, "fingerprint": "sport-fp"},
        "odds_sample": {"verified": True, "fingerprint": "odds-fp"},
        "team_identity": {"verified": True, "fingerprint": "team-fp", "unmatched_count": 0},
        "result_contract": {"verified": True, "fingerprint": "result-fp"},
    }
    calls = []

    def fetch(sport_key, env):
        calls.append((sport_key, sorted(env)))
        return [{"id": "event-1"}]

    def build(payload, competition_id, observed_at, **_kwargs):
        assert payload == [{"id": "event-1"}]
        return {
            "snapshot_at": observed_at,
            "competition": {"id": competition_id},
            "matches": [{
                "source_event_id": "event-1",
                "competition": {"id": competition_id},
                "match_decision": {"label": "MATCH_PICK"},
            }],
        }

    with TemporaryDirectory() as tmp:
        acceptance = {
            "schema_version": 1,
            "competitions": {
                "epl_2026_27": evaluate_league_acceptance("epl_2026_27", evidence),
            },
        }
        result = run_planned_league_refresh(
            root=tmp,
            observed_at="2026-08-24T12:00:00Z",
            competition_ids=["epl_2026_27"],
            env={"THE_ODDS_API_KEY_SECONDARY": "s" * 40},
            odds_fetcher=fetch,
            acceptance_report=acceptance,
            identity_registry=_epl_registry(),
            expected_event_ids_by_competition={"epl_2026_27": ["event-1"]},
            expected_snapshot_ids_by_competition={
                "epl_2026_27": "league-attempt-test"
            },
            guarded_acceptance_fingerprint=acceptance_fingerprint(acceptance),
            snapshot_builder=build,
        )

        assert result["status"] == "refreshed"
        assert calls == [("soccer_epl", ["THE_ODDS_API_KEY_SECONDARY"])]
        assert result["snapshots"] == [{
            "competition": {"id": "epl_2026_27"},
            "snapshot_id": result["competitions"]["epl_2026_27"]["snapshot_id"],
            "commit_status": "stored",
        }]
        assert (Path(tmp) / "data/cache/leagues/epl_2026_27/snapshot.json").exists()
        assert not (Path(tmp) / "data/cache/leagues/laliga_2026_27/snapshot.json").exists()
