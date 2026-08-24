import json
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.league_scheduled_publish as league_scheduled_publish
from worldcup.ingest import build_ingest_payload, build_ingest_request
from worldcup.league_scheduled_publish import (
    publish_committed_league_snapshots,
    run_league_scheduled_publish,
)


def _fail(*args, **kwargs):
    raise AssertionError("dependency must not be called")


def _active_row(competition_id):
    return {
        "competition_id": competition_id, "state": "active", "reason": None,
        "fingerprints": {name: f"{competition_id}-{name}" for name in (
            "sport_catalog", "odds_sample", "team_identity", "result_contract"
        )},
    }


def _write_acceptance(root, *competition_ids):
    path = Path(root) / "data/local/leagues/acceptance.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "competitions": {
        competition_id: _active_row(competition_id) for competition_id in competition_ids
    }}), encoding="utf-8")


def test_scheduler_default_dry_run_does_not_load_env_refresh_publish_or_write():
    result = run_league_scheduled_publish(
        root=".",
        now="2026-08-24T12:00:00Z",
        plan={"requests": [], "estimated_credits": 0},
        live=False,
        write=False,
        env_loader=_fail,
        refresh_fn=_fail,
        publish_fn=_fail,
    )

    assert result == {
        "status": "dry_run",
        "plan": {"requests": [], "estimated_credits": 0},
        "refresh": None,
        "publish": None,
    }


def test_scheduler_retries_pending_publish_before_refresh():
    calls = []

    def publish(payload):
        calls.append(("publish", payload["snapshot_id"]))
        return {"status": "stored"}

    result = run_league_scheduled_publish(
        root=".",
        now="2026-08-24T12:00:00Z",
        plan={"requests": [{"competition_id": "epl_2026_27"}], "estimated_credits": 1},
        live=True,
        write=True,
        pending_payload={"snapshot_id": "pending-1"},
        env_loader=lambda: {"INGEST_HMAC_SECRET": "x" * 32},
        refresh_fn=_fail,
        publish_fn=publish,
    )

    assert calls == [("publish", "pending-1")]
    assert result["status"] == "published_pending"


def test_scheduler_refreshes_then_publishes_only_committed_snapshots():
    calls = []

    def refresh(**kwargs):
        calls.append("refresh")
        snapshot = {
            "snapshot_id": "fresh-1", "snapshot_at": "2026-08-24T12:00:00Z",
            "competition": {"id": "epl_2026_27"},
            "matches": [{"source_event_id": "event-1", "competition": {"id": "epl_2026_27"}}],
        }
        path = Path(kwargs["root"]) / "data/cache/leagues/epl_2026_27/snapshot.json"
        path.parent.mkdir(parents=True); path.write_text(json.dumps(snapshot))
        return {"status": "refreshed", "snapshots": [snapshot]}

    def publish(payload):
        calls.append(("publish", payload["snapshot_id"]))
        return {"status": "stored"}

    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, "epl_2026_27")
        result = run_league_scheduled_publish(
            root=tmp, now="2026-08-24T12:00:00Z",
            plan={"requests": [{"competition_id": "epl_2026_27"}], "estimated_credits": 1},
            live=True, write=True,
            env_loader=lambda: {"INGEST_HMAC_SECRET": "x" * 32},
            refresh_fn=refresh, publish_fn=publish,
        )

    assert calls[0] == "refresh"
    assert calls[1][0] == "publish"
    assert calls[1][1].startswith("league-aggregate-")
    assert result["status"] == "published"


def test_scheduler_publishes_one_aggregate_snapshot_for_multiple_leagues():
    published = []

    def refresh(**kwargs):
        snapshots = [
                {
                    "snapshot_id": "epl-1", "snapshot_at": "2026-08-24T12:00:00+00:00",
                    "competition": {"id": "epl_2026_27", "name": "英超"},
                    "matches": [{"source_event_id": "epl-event", "competition": {"id": "epl_2026_27"}}],
                },
                {
                    "snapshot_id": "laliga-1", "snapshot_at": "2026-08-24T12:01:00+00:00",
                    "competition": {"id": "laliga_2026_27", "name": "西甲"},
                    "matches": [{"source_event_id": "es-event", "competition": {"id": "laliga_2026_27"}}],
                },
            ]
        for snapshot in snapshots:
            competition_id = snapshot["competition"]["id"]
            path = Path(kwargs["root"]) / f"data/cache/leagues/{competition_id}/snapshot.json"
            path.parent.mkdir(parents=True); path.write_text(json.dumps(snapshot))
        return {"status": "refreshed", "snapshots": snapshots}

    def publish(payload):
        published.append(payload)
        return {"status": "stored"}

    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, "epl_2026_27", "laliga_2026_27")
        result = run_league_scheduled_publish(
            root=tmp,
            now="2026-08-24T12:00:00Z",
            plan={"requests": [{"competition_id": "epl_2026_27"}], "estimated_credits": 1},
            live=True,
            write=True,
            env_loader=lambda: {"INGEST_HMAC_SECRET": "x" * 32},
            refresh_fn=refresh,
            publish_fn=publish,
        )

    assert result["status"] == "published"
    assert len(published) == 1
    aggregate = published[0]
    assert aggregate["snapshot_id"].startswith("league-aggregate-")
    assert [row["source_event_id"] for row in aggregate["matches"]] == ["epl-event", "es-event"]
    assert {row["competition"]["id"] for row in aggregate["matches"]} == {
        "epl_2026_27", "laliga_2026_27",
    }


def test_aggregate_keeps_cached_active_leagues_and_excludes_identity_only_league():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        acceptance_path = root / "data/local/leagues/acceptance.json"
        acceptance_path.parent.mkdir(parents=True)
        acceptance_path.write_text(json.dumps({
            "schema_version": 1,
            "competitions": {
                "epl_2026_27": _active_row("epl_2026_27"),
                "laliga_2026_27": _active_row("laliga_2026_27"),
                "bundesliga_2026_27": {
                    "competition_id": "bundesliga_2026_27", "state": "identity_verified",
                    "reason": None, "fingerprints": {},
                },
            },
        }), encoding="utf-8")
        cached_path = root / "data/cache/leagues/laliga_2026_27/snapshot.json"
        cached_path.parent.mkdir(parents=True)
        cached_path.write_text(json.dumps({
            "snapshot_id": "laliga-cached", "snapshot_at": "2026-08-24T11:00:00Z",
            "competition": {"id": "laliga_2026_27"},
            "matches": [{"source_event_id": "es-cached", "competition": {"id": "laliga_2026_27"}}],
        }), encoding="utf-8")

        aggregate = league_scheduled_publish.build_aggregate_league_snapshot(
            root=root,
            snapshots=[{
                "snapshot_id": "epl-new", "snapshot_at": "2026-08-24T12:00:00Z",
                "competition": {"id": "epl_2026_27"},
                "matches": [{"source_event_id": "epl-new", "competition": {"id": "epl_2026_27"}}],
            }],
        )

    assert [row["source_event_id"] for row in aggregate["matches"]] == ["epl-new", "es-cached"]
    assert aggregate["data_quality"]["missing_competition_snapshots"] == []
    assert build_ingest_payload(aggregate, generated_at="2026-08-24T12:01:00Z")["run_id"].startswith(
        "league-aggregate-"
    )


def test_aggregate_fails_closed_without_acceptance_or_complete_active_cache():
    snapshot = {
        "snapshot_id": "epl-new", "snapshot_at": "2026-08-24T12:00:00Z",
        "competition": {"id": "epl_2026_27"}, "matches": [],
    }
    with TemporaryDirectory() as tmp:
        try:
            league_scheduled_publish.build_aggregate_league_snapshot(root=tmp, snapshots=[snapshot])
        except ValueError as exc:
            assert str(exc) == "league_aggregate_acceptance_missing"
        else:
            raise AssertionError("missing acceptance must block aggregate")
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "data/local/leagues/acceptance.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 1, "competitions": {
            "epl_2026_27": _active_row("epl_2026_27"),
            "laliga_2026_27": _active_row("laliga_2026_27"),
        }}), encoding="utf-8")
        try:
            league_scheduled_publish.build_aggregate_league_snapshot(root=root, snapshots=[snapshot])
        except ValueError as exc:
            assert str(exc) == "league_aggregate_active_snapshot_missing"
        else:
            raise AssertionError("missing active cache must block aggregate")


def test_aggregate_rejects_duplicate_components_and_cross_league_event_ids():
    epl = {"snapshot_id": "e1", "snapshot_at": "2026-08-24T12:00:00Z", "competition": {"id": "epl_2026_27"},
           "matches": [{"source_event_id": "same", "competition": {"id": "epl_2026_27"}}]}
    laliga = {"snapshot_id": "l1", "snapshot_at": "2026-08-24T12:00:00Z", "competition": {"id": "laliga_2026_27"},
              "matches": [{"source_event_id": "same", "competition": {"id": "laliga_2026_27"}}]}
    with TemporaryDirectory() as tmp:
        root = Path(tmp); path = root / "data/local/leagues/acceptance.json"; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 1, "competitions": {
            "epl_2026_27": _active_row("epl_2026_27"), "laliga_2026_27": _active_row("laliga_2026_27")}}))
        for snapshots, reason in (([epl, epl], "league_aggregate_duplicate_component"), ([epl, laliga], "league_aggregate_match_identity_invalid")):
            try: league_scheduled_publish.build_aggregate_league_snapshot(root=root, snapshots=snapshots)
            except ValueError as exc: assert str(exc) == reason
            else: raise AssertionError(reason)


def test_local_scheduler_plan_reads_active_evidence_and_excludes_identity_only_league():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        acceptance_path = root / "data/local/leagues/acceptance.json"
        acceptance_path.parent.mkdir(parents=True)
        acceptance_path.write_text(json.dumps({
            "schema_version": 1,
            "competitions": {
                "epl_2026_27": {
                    "competition_id": "epl_2026_27", "state": "active", "reason": None,
                    "fingerprints": {name: f"{name}-fp" for name in (
                        "sport_catalog", "odds_sample", "team_identity", "result_contract"
                    )},
                },
                "bundesliga_2026_27": {
                    "competition_id": "bundesliga_2026_27", "state": "identity_verified", "reason": None,
                    "fingerprints": {name: f"{name}-fp" for name in (
                        "sport_catalog", "odds_sample", "team_identity"
                    )},
                },
            },
        }), encoding="utf-8")
        for competition_id, event_id, kickoff in (
            ("epl_2026_27", "epl-1", "2026-08-24T17:00:00Z"),
            ("bundesliga_2026_27", "de-1", "2026-08-24T12:20:00Z"),
        ):
            event_path = root / f"data/probe/leagues/{competition_id}/events.json"
            event_path.parent.mkdir(parents=True)
            event_path.write_text(json.dumps([{
                "id": event_id, "commence_time": kickoff,
                "home_team": "Home", "away_team": "Away",
            }]), encoding="utf-8")
        quota_path = root / "data/cache/quota.json"
        quota_path.parent.mkdir(parents=True)
        quota_path.write_text(json.dumps({
            "providers": {"theoddsapi_tertiary": {"remaining": 480}}
        }), encoding="utf-8")

        assert hasattr(league_scheduled_publish, "build_local_league_plan"), (
            "local scheduler plan builder is missing"
        )
        plan = league_scheduled_publish.build_local_league_plan(
            root=root, now="2026-08-24T12:00:00Z"
        )

        assert [row["competition_id"] for row in plan["requests"]] == ["epl_2026_27"]
        assert plan["requests"][0]["anchor"] == "T-6h"
        assert plan["estimated_credits"] == 1
        assert all(row["competition_id"] != "bundesliga_2026_27" for row in plan["requests"])

        assert hasattr(league_scheduled_publish, "run_local_league_scheduler"), (
            "local lifecycle scheduler is missing"
        )
        result = league_scheduled_publish.run_local_league_scheduler(
            root=root, now="2026-08-24T12:00:00Z"
        )
        assert result["status"] == "dry_run"
        assert result["scheduler"]["plan"]["estimated_credits"] == 1
        assert result["lifecycle"]["competitions"]["epl_2026_27"] == {
            "status": "blocked",
            "reason": "lifecycle_inputs_missing",
            "missing": ["history", "scores", "result_contract_evidence"],
        }
        assert "bundesliga_2026_27" not in result["lifecycle"]["competitions"]


def test_committed_receipt_is_reread_and_aggregate_is_real_ingest_compatible():
    published = []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_acceptance(root, "epl_2026_27")
        path = root / "data/cache/leagues/epl_2026_27/snapshot.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "snapshot_id": "committed-1",
            "snapshot_at": "2026-08-24T12:00:00+00:00",
            "competition": {"id": "epl_2026_27"},
            "matches": [{
                "source_event_id": "event-1",
                "competition": {"id": "epl_2026_27"},
            }],
        }), encoding="utf-8")

        def publish(snapshot):
            published.append(snapshot)
            request = build_ingest_request(
                snapshot,
                endpoint="https://example.invalid/api/ingest/snapshot",
                secret="h" * 32,
                timestamp="2026-08-24T12:01:00+00:00",
            )
            assert request["headers"]["X-Worldcup-Run-Id"] == snapshot["run"]["run_id"]
            return {"status": "duplicate"}

        result = publish_committed_league_snapshots(
            root=root,
            snapshot_receipts=[{
                "competition": {"id": "epl_2026_27"},
                "snapshot_id": "committed-1",
                "commit_status": "stored",
            }],
            publish_fn=publish,
        )

        assert result["status"] == "published"
        assert result["publish"] == {"status": "duplicate"}
        assert result["aggregate"]["run_id"].startswith("league-aggregate-")
        assert result["aggregate"]["components"] == [{
            "competition_id": "epl_2026_27",
            "snapshot_id": "committed-1",
        }]
        assert len(published) == 1


def test_committed_receipt_snapshot_id_mismatch_blocks_publisher():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_acceptance(root, "epl_2026_27")
        path = root / "data/cache/leagues/epl_2026_27/snapshot.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "snapshot_id": "actual-1",
            "snapshot_at": "2026-08-24T12:00:00+00:00",
            "competition": {"id": "epl_2026_27"},
            "matches": [],
        }), encoding="utf-8")

        result = publish_committed_league_snapshots(
            root=root,
            snapshot_receipts=[{
                "competition": {"id": "epl_2026_27"},
                "snapshot_id": "claimed-1",
                "commit_status": "stored",
            }],
            publish_fn=_fail,
        )

        assert result == {
            "status": "publish_failed",
            "reason": "league_refresh_snapshot_commit_mismatch",
            "publish": None,
            "aggregate": None,
        }
