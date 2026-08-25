from __future__ import annotations

import json
import io
import fcntl
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_bootstrap_publish import (
    build_league_bootstrap_plan,
    main,
    run_league_bootstrap_publish,
)
import worldcup.league_bootstrap_publish as league_bootstrap_publish


NOW = "2026-08-25T02:00:00Z"


def _fail(*args, **kwargs):
    raise AssertionError("dependency must not be called")


def _active_row(competition_id: str) -> dict:
    return {
        "competition_id": competition_id,
        "state": "active",
        "reason": None,
        "fingerprints": {
            name: f"{competition_id}-{name}"
            for name in ("sport_catalog", "odds_sample", "team_identity", "result_contract")
        },
    }


def _write_inputs(root: Path) -> None:
    acceptance = root / "data/local/leagues/acceptance.json"
    acceptance.parent.mkdir(parents=True)
    acceptance.write_text(json.dumps({
        "schema_version": 1,
        "competitions": {
            "epl_2026_27": _active_row("epl_2026_27"),
            "laliga_2026_27": _active_row("laliga_2026_27"),
            "bundesliga_2026_27": {
                "competition_id": "bundesliga_2026_27",
                "state": "identity_verified",
                "reason": None,
                "fingerprints": {
                    "sport_catalog": "de-sport",
                    "odds_sample": "de-odds",
                    "team_identity": "de-team",
                },
            },
        },
    }), encoding="utf-8")
    rows = {
        "epl_2026_27": [
            {"id": "epl-past", "commence_time": "2026-08-25T01:00:00Z"},
            {"id": "epl-future", "commence_time": "2026-08-26T12:00:00Z"},
        ],
        "laliga_2026_27": [
            {"id": "laliga-future", "commence_time": "2026-08-27T12:00:00Z"},
        ],
        "bundesliga_2026_27": [
            {"id": "bundesliga-future", "commence_time": "2026-08-27T12:00:00Z"},
        ],
    }
    for competition_id, events in rows.items():
        path = root / f"data/probe/leagues/{competition_id}/events.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(events), encoding="utf-8")


def test_bootstrap_dry_run_selects_only_active_future_events_without_dependencies():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        result = run_league_bootstrap_publish(
            root=root,
            now=NOW,
            env_loader=_fail,
            odds_fetcher=_fail,
            publish_fn=_fail,
        )

    assert result["status"] == "dry_run_ready"
    assert result["plan"]["competition_ids"] == ["epl_2026_27", "laliga_2026_27"]
    assert result["plan"]["expected_event_ids_by_competition"] == {
        "epl_2026_27": ["epl-future"],
        "laliga_2026_27": ["laliga-future"],
    }
    assert result["safety"] == {
        "read_env": False,
        "called_theoddsapi": False,
        "wrote_partitions": False,
        "published": False,
    }


def test_bootstrap_requires_exact_live_write_force_flags_and_https_endpoint():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        for flags in (
            {"live": True},
            {"live": True, "write": True},
            {"live": True, "force_initial": True},
        ):
            result = run_league_bootstrap_publish(
                root=root, now=NOW, endpoint="https://football.celab.xin/api/ingest/snapshot",
                env_loader=_fail, odds_fetcher=_fail, publish_fn=_fail, **flags,
            )
            assert result == {"status": "blocked", "reason": "bootstrap_live_flags_invalid"}

        result = run_league_bootstrap_publish(
            root=root, now=NOW, live=True, write=True, force_initial=True,
            endpoint="https://example.invalid/api/ingest/snapshot",
            env_loader=_fail, odds_fetcher=_fail, publish_fn=_fail,
        )
        assert result == {"status": "blocked", "reason": "bootstrap_endpoint_invalid"}


def test_bootstrap_plan_blocks_missing_future_events_and_duplicate_ids():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        (root / "data/probe/leagues/epl_2026_27/events.json").write_text(
            json.dumps([{"id": "past", "commence_time": "2026-08-24T00:00:00Z"}]),
            encoding="utf-8",
        )
        assert build_league_bootstrap_plan(root=root, now=NOW) == {
            "status": "blocked", "reason": "bootstrap_future_events_missing",
            "competition_id": "epl_2026_27",
        }

        (root / "data/probe/leagues/epl_2026_27/events.json").write_text(
            json.dumps([
                {"id": "same", "commence_time": "2026-08-26T00:00:00Z"},
                {"id": "same", "commence_time": "2026-08-27T00:00:00Z"},
            ]), encoding="utf-8",
        )
        assert build_league_bootstrap_plan(root=root, now=NOW) == {
            "status": "blocked", "reason": "bootstrap_event_ids_invalid",
            "competition_id": "epl_2026_27",
        }


def test_bootstrap_blocks_when_all_active_partitions_already_exist():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        for competition_id in ("epl_2026_27", "laliga_2026_27"):
            path = root / f"data/cache/leagues/{competition_id}/snapshot.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        state = root / "data/local/leagues/bootstrap_publish_state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({
            "schema_version": 1,
            "acceptance_fingerprint": build_league_bootstrap_plan(root=root, now=NOW)[
                "acceptance_fingerprint"
            ],
            "competition_ids": ["epl_2026_27", "laliga_2026_27"],
            "aggregate_snapshot_id": "league-aggregate-complete",
            "publish_status": "stored",
        }), encoding="utf-8")
        result = run_league_bootstrap_publish(root=root, now=NOW)

    assert result == {"status": "blocked", "reason": "bootstrap_already_complete"}


def test_bootstrap_allows_retry_when_partitions_exist_without_confirmed_publish_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        for competition_id in ("epl_2026_27", "laliga_2026_27"):
            path = root / f"data/cache/leagues/{competition_id}/snapshot.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")

        result = run_league_bootstrap_publish(root=root, now=NOW)

    assert result["status"] == "dry_run_ready"
    assert result["plan"]["existing_partition_ids"] == [
        "epl_2026_27", "laliga_2026_27",
    ]


def test_bootstrap_live_refreshes_exact_active_set_then_publishes_complete_receipts():
    calls = []

    def refresh(**kwargs):
        calls.append(("refresh", kwargs))
        receipts = []
        for competition_id in kwargs["competition_ids"]:
            snapshot_id = kwargs["expected_snapshot_ids_by_competition"][competition_id]
            path = Path(kwargs["root"]) / f"data/cache/leagues/{competition_id}/snapshot.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "snapshot_id": snapshot_id,
                "snapshot_at": NOW,
                "competition": {"id": competition_id},
                "matches": [{
                    "source_event_id": kwargs["expected_event_ids_by_competition"][competition_id][0],
                    "competition": {"id": competition_id},
                }],
            }), encoding="utf-8")
            receipts.append({
                "competition": {"id": competition_id},
                "snapshot_id": snapshot_id,
                "commit_status": "stored",
            })
        return {"status": "refreshed", "competitions": {}, "snapshots": receipts}

    def publish(snapshot):
        calls.append(("publish", snapshot))
        return {"status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        result = run_league_bootstrap_publish(
            root=root, now=NOW, live=True, write=True, force_initial=True,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            env_loader=lambda: {"THE_ODDS_API_KEY_TERTIARY": "k" * 40},
            odds_fetcher=lambda *_args, **_kwargs: [],
            refresh_fn=refresh,
            publish_fn=publish,
            identity_registry=object(),
        )
        state = json.loads((
            root / "data/local/leagues/bootstrap_publish_state.json"
        ).read_text(encoding="utf-8"))

    assert result["status"] == "published"
    assert result["refresh"] == {"status": "refreshed", "snapshot_count": 2}
    assert result["publish"]["status"] == "stored"
    assert calls[0][0] == "refresh"
    assert calls[0][1]["competition_ids"] == ["epl_2026_27", "laliga_2026_27"]
    assert calls[1][0] == "publish"
    assert len(calls[1][1]["components"]) == 2
    assert state["publish_status"] == "stored"
    assert state["aggregate_snapshot_id"] == result["aggregate"]["snapshot_id"]


def test_bootstrap_never_publishes_partial_refresh():
    published = []
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        result = run_league_bootstrap_publish(
            root=root, now=NOW, live=True, write=True, force_initial=True,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            env_loader=lambda: {"THE_ODDS_API_KEY_TERTIARY": "k" * 40},
            odds_fetcher=lambda *_args, **_kwargs: [],
            refresh_fn=lambda **_kwargs: {
                "status": "partial", "competitions": {}, "snapshots": [],
            },
            publish_fn=lambda snapshot: published.append(snapshot),
            identity_registry=object(),
        )

    assert result == {
        "status": "refresh_failed",
        "refresh": {"status": "partial", "snapshot_count": 0},
        "publish": None,
    }
    assert published == []


def test_bootstrap_cli_rejects_partial_live_flags_before_reading_env():
    original = league_bootstrap_publish._load_env
    league_bootstrap_publish._load_env = _fail
    try:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--live"])
    finally:
        league_bootstrap_publish._load_env = original

    assert exit_code == 2
    assert json.loads(output.getvalue()) == {
        "status": "blocked", "reason": "bootstrap_live_flags_invalid",
    }


def test_bootstrap_live_lock_contention_blocks_before_env_or_provider_calls():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_inputs(root)
        lock_path = root / "data/local/leagues/bootstrap_publish.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_league_bootstrap_publish(
                root=root, now=NOW, live=True, write=True, force_initial=True,
                endpoint="https://football.celab.xin/api/ingest/snapshot",
                env_loader=_fail, odds_fetcher=_fail, publish_fn=_fail,
            )

    assert result == {"status": "blocked", "reason": "bootstrap_lock_contended"}
