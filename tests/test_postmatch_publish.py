import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import worldcup.postmatch_publish as postmatch_module
from worldcup.ingest import build_ingest_request
from worldcup.ingest_server import verify_ingest_request
from worldcup.postmatch_publish import run_postmatch_publish
from worldcup.preview import build_preview_html


NOW = "2026-07-16T00:15:00+00:00"
ENDPOINT = "https://example.test/api/ingest/snapshot"


def _base_snapshot() -> dict:
    return {
        "snapshot_at": "2026-07-15T18:46:55+00:00",
        "competition": {"id": "fifa_world_cup_2026", "name": "2026 世界杯"},
        "run": {
            "schema_version": 1,
            "run_id": "20260715T184655Z-live",
            "mode": "live",
            "observed_at": "2026-07-15T18:46:55+00:00",
            "policy": {"policy_version": "free-tier-v3"},
            "quota": {"theoddsapi-primary": {"remaining": 17}},
        },
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-07-15T19:00:00+00:00",
                "competition": {"id": "fifa_world_cup_2026", "name": "2026 世界杯"},
                "stage": "Semi-final",
                "home_team": "England",
                "away_team": "Argentina",
                "home_canonical": "england",
                "away_canonical": "argentina",
                "match_decision": {
                    "schema_version": 2,
                    "policy_version": "match_pick_v3",
                    "label": "MATCH_PICK",
                    "market": "1X2",
                    "selection": "draw",
                    "odds": 3.2,
                    "p_hit_safe": 0.34,
                    "p_no_loss_safe": 0.34,
                    "valid_until": "2026-07-15T19:00:00+00:00",
                },
            }
        ],
        "finished": {
            "schema_version": 2,
            "matches": [],
            "decision_tally": {"hit": 0, "miss": 0, "push": 0, "no_pick": 0},
            "decision_sample": {"settled": 0, "sample_too_small": True},
            "decision_coverage": {"finished_result_count": 0},
            "skipped_no_closing": 0,
        },
    }


def _openfootball(*, ft=(1, 1), include_ft=True) -> dict:
    score = {"et": [2, 1], "p": [4, 3]}
    if include_ft:
        score["ft"] = list(ft)
    return {
        "name": "World Cup 2026",
        "matches": [
            {
                "num": 102,
                "round": "Semi-final",
                "date": "2026-07-15",
                "time": "15:00 UTC-4",
                "team1": "England",
                "team2": "Argentina",
                "score1": 2,
                "score2": 1,
                "score": score,
            }
        ],
    }


def _paths(root: Path) -> dict:
    return {
        "base_snapshot_path": root / "cache" / "analysis_snapshot.json",
        "postmatch_snapshot_path": root / "cache" / "wc2026_postmatch_snapshot.json",
        "state_path": root / "cache" / "wc2026_postmatch_state.json",
        "openfootball_cache_path": root / "cache" / "openfootball_2026.json",
        "history_dir": root / "history",
        "results_path": root / "results" / "wc2026_results.csv",
        "finished_store_path": root / "local" / "finished_record_store.json",
    }


def _seed_base(paths: dict, *, with_history=True) -> bytes:
    base = _base_snapshot()
    base_path = paths["base_snapshot_path"]
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_path.write_text(json.dumps(base, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    if with_history:
        history = paths["history_dir"]
        history.mkdir(parents=True, exist_ok=True)
        (history / "snapshot_20260715T184655Z-live.json").write_text(
            json.dumps(base, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    return base_path.read_bytes()


def _fetch(raw: dict):
    return lambda: SimpleNamespace(
        status=200,
        text=json.dumps(raw, ensure_ascii=False),
        headers={},
    )


def _forbidden(label):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"{label} must not be called")

    return fail


def _read_results(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_results(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "kickoff_at_utc",
                "home_team",
                "away_team",
                "home_canonical",
                "away_canonical",
                "home_score",
                "away_score",
                "captured_at",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_postmatch_default_dry_run_has_zero_side_effects():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        base_bytes = _seed_base(paths)
        quota = root / "cache" / "quota.json"
        quota.write_bytes(b"quota-sentinel")

        result = run_postmatch_publish(
            live=False,
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            load_env_fn=_forbidden("load_env"),
            **paths,
        )

        assert result["status"] == "dry_run"
        assert paths["base_snapshot_path"].read_bytes() == base_bytes
        assert quota.read_bytes() == b"quota-sentinel"
        for key in (
            "postmatch_snapshot_path",
            "state_path",
            "openfootball_cache_path",
            "results_path",
            "finished_store_path",
        ):
            assert not paths[key].exists()


def test_postmatch_live_uses_ft_builds_full_independent_snapshot_and_publishes():
    calls = []

    def publish_fn(**kwargs):
        calls.append(
            {
                **kwargs,
                "snapshot_bytes": Path(kwargs["snapshot_path"]).read_bytes(),
            }
        )
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        base_bytes = _seed_base(paths)
        quota = root / "cache" / "quota.json"
        quota.write_bytes(b"quota-sentinel")

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )

        output = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))
        result_rows = _read_results(paths["results_path"])

        assert result["status"] == "published"
        assert (result_rows[0]["home_score"], result_rows[0]["away_score"]) == ("1", "1")
        assert output["matches"] == _base_snapshot()["matches"]
        assert output["finished"]["matches"][0]["result"] == {
            "home_score": 1,
            "away_score": 1,
        }
        assert output["finished"]["matches"][0]["closing_match_decision_result"]["status"] == "hit"
        assert output["run"]["mode"] == "postmatch_results"
        assert output["run"]["parent_run_id"] == "20260715T184655Z-live"
        assert output["run"]["postmatch"]["score_field"] == "score.ft"
        assert output["run"]["postmatch"]["period"] == "90min"
        assert paths["base_snapshot_path"].read_bytes() == base_bytes
        assert quota.read_bytes() == b"quota-sentinel"
        assert len(calls) == 1
        assert Path(calls[0]["snapshot_path"]).name.endswith(".prepared.json")
        assert json.loads(calls[0]["snapshot_bytes"]) == output
        assert calls[0]["live"] is True
        assert not paths["postmatch_snapshot_path"].with_name(
            "wc2026_postmatch_snapshot.publish_pending.json"
        ).exists()

        request = build_ingest_request(
            output,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            timestamp=NOW,
        )
        verified = verify_ingest_request(
            method=request["method"],
            path=request["path"],
            headers=request["headers"],
            body=request["body"],
            secret="test-secret-long-enough-for-validation!!",
            now=NOW,
        )
        assert verified.ok is True

        html = build_preview_html(output)
        assert "英格兰 对 阿根廷" in html
        assert "1 - 1" in html
        assert "已开赛·赛果待确认" not in html
        assert "<span>赛果待确认</span><strong>0</strong>" in html


def test_postmatch_invalid_source_preserves_existing_files_and_never_publishes():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        sentinels = {
            paths["openfootball_cache_path"]: b"old-cache",
            paths["results_path"]: b"old-results",
            paths["finished_store_path"]: b"old-store",
            paths["postmatch_snapshot_path"]: b"old-output",
        }
        for path, content in sentinels.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=lambda: SimpleNamespace(status=200, text="not-json", headers={}),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "error", "reason": "invalid_openfootball_json"}
        for path, content in sentinels.items():
            assert path.read_bytes() == content


def test_postmatch_missing_closing_is_published_as_transparent_coverage_gap():
    calls = []

    def publish_fn(**kwargs):
        calls.append(kwargs)
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths, with_history=False)

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )

        output = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))

        assert result["status"] == "published"
        assert _read_results(paths["results_path"])[0]["home_score"] == "1"
        assert output["finished"]["matches"] == []
        assert output["finished"]["skipped_no_closing"] == 1
        assert output["finished"]["decision_coverage"]["missing_closing_count"] == 1
        assert output["run"]["postmatch"]["missing_closing_count"] == 1
        assert output["run"]["postmatch"]["partial_publish"] is True
        assert len(calls) == 1


def test_postmatch_publishes_finished_with_closing_while_reporting_later_missing_closing():
    calls = []

    def publish_fn(**kwargs):
        calls.append(kwargs)
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    raw = _openfootball(ft=(1, 2))
    raw["matches"].append(
        {
            "num": 103,
            "round": "Match for third place",
            "date": "2026-07-18",
            "time": "17:00 UTC-4",
            "team1": "France",
            "team2": "England",
            "score": {"ft": [4, 6], "ht": [0, 4]},
        }
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(raw),
            publish_fn=publish_fn,
            **paths,
        )

        output = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))

        assert result["status"] == "published"
        assert len(output["finished"]["matches"]) == 1
        assert output["finished"]["matches"][0]["home_team"] == "England"
        assert output["finished"]["matches"][0]["away_team"] == "Argentina"
        assert output["finished"]["decision_coverage"]["finished_result_count"] == 2
        assert output["finished"]["decision_coverage"]["closing_available_count"] == 1
        assert output["finished"]["decision_coverage"]["missing_closing_count"] == 1
        assert output["run"]["postmatch"]["missing_closing_count"] == 1
        assert output["run"]["postmatch"]["partial_publish"] is True
        assert len(calls) == 1


def test_postmatch_pending_retry_reuses_exact_snapshot_without_refetch():
    publish_calls = []

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        if len(publish_calls) == 1:
            return {"status": "sent", "http_status": 503, "ingest_status": None}
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )
        pending_path = paths["postmatch_snapshot_path"].with_name(
            "wc2026_postmatch_snapshot.publish_pending.json"
        )
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        prepared_path = Path(pending["snapshot_path"])
        output_bytes = prepared_path.read_bytes()
        assert not paths["postmatch_snapshot_path"].exists()
        second = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:20:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=publish_fn,
            **paths,
        )

        assert first["status"] == "publish_pending"
        assert second["status"] == "republished"
        assert len(publish_calls) == 2
        assert paths["postmatch_snapshot_path"].read_bytes() == output_bytes
        assert not pending_path.exists()
        assert not prepared_path.exists()


def test_postmatch_same_finished_fingerprint_does_not_publish_again():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
            **paths,
        )
        output_bytes = paths["postmatch_snapshot_path"].read_bytes()
        second = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:30:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "published"
        assert second["status"] == "unchanged"
        assert paths["postmatch_snapshot_path"].read_bytes() == output_bytes


def test_postmatch_score_revision_requires_manual_review_without_overwriting_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
            **paths,
        )
        before = {
            key: paths[key].read_bytes()
            for key in (
                "openfootball_cache_path",
                "results_path",
                "finished_store_path",
                "postmatch_snapshot_path",
                "state_path",
            )
        }
        revised = run_postmatch_publish(
            live=True,
            now="2026-07-16T01:00:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball(ft=(2, 1))),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "published"
        assert revised["status"] == "blocked"
        assert revised["reason"] == "score_revision_manual_review_required"
        for key, content in before.items():
            assert paths[key].read_bytes() == content


def test_postmatch_live_rejects_default_endpoint_before_fetch_or_write():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        base_bytes = _seed_base(paths)

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "invalid_ingest_endpoint"}
        assert paths["base_snapshot_path"].read_bytes() == base_bytes
        assert not paths["postmatch_snapshot_path"].exists()


def test_postmatch_rejects_write_path_collision_before_fetch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        base_bytes = _seed_base(paths)
        paths["postmatch_snapshot_path"] = paths["base_snapshot_path"]

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "postmatch_path_collision"}
        assert paths["base_snapshot_path"].read_bytes() == base_bytes


def test_postmatch_corrupt_state_blocks_before_fetch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        paths["state_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["state_path"].write_text("{not-json", encoding="utf-8")

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "invalid_postmatch_state"}


def test_postmatch_blocks_partial_source_result_regression_without_overwriting_cache():
    existing = {
        "kickoff_at_utc": "2026-07-15T19:00:00+00:00",
        "home_team": "England",
        "away_team": "Argentina",
        "home_canonical": "england",
        "away_canonical": "argentina",
        "home_score": "1",
        "away_score": "1",
        "captured_at": NOW,
    }
    partial = {
        "name": "World Cup 2026",
        "matches": [
            {
                "num": 103,
                "round": "Semi-final",
                "date": "2026-07-16",
                "time": "15:00 UTC-4",
                "team1": "Spain",
                "team2": "Brazil",
                "score": {"ft": [2, 0]},
            }
        ],
    }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        _write_results(paths["results_path"], [existing])
        results_bytes = paths["results_path"].read_bytes()

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(partial),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "openfootball_result_regression"
        assert paths["results_path"].read_bytes() == results_bytes
        assert not paths["openfootball_cache_path"].exists()


def test_postmatch_rejects_mixed_competition_base_before_fetch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        base = json.loads(paths["base_snapshot_path"].read_text(encoding="utf-8"))
        base["matches"][0]["competition"] = {"id": "csl_2026", "name": "中超 2026"}
        paths["base_snapshot_path"].write_text(json.dumps(base), encoding="utf-8")

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "base_snapshot_not_world_cup"}


def test_postmatch_rejects_base_without_explicit_world_cup_identity():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        base = json.loads(paths["base_snapshot_path"].read_text(encoding="utf-8"))
        base.pop("competition", None)
        base["matches"][0].pop("competition", None)
        paths["base_snapshot_path"].write_text(json.dumps(base), encoding="utf-8")

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "base_snapshot_not_world_cup"}


def test_postmatch_keeps_pending_when_publish_succeeds_but_state_write_fails():
    publish_calls = []

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        original_write = postmatch_module._write_json_atomic

        def fail_state(path, payload):
            if Path(path) == paths["state_path"]:
                raise OSError("simulated state write failure")
            return original_write(path, payload)

        try:
            postmatch_module._write_json_atomic = fail_state
            first = run_postmatch_publish(
                live=True,
                now=NOW,
                endpoint=ENDPOINT,
                secret="test-secret-long-enough-for-validation!!",
                fetch_fn=_fetch(_openfootball()),
                publish_fn=publish_fn,
                **paths,
            )
        finally:
            postmatch_module._write_json_atomic = original_write

        pending_path = paths["postmatch_snapshot_path"].with_name(
            "wc2026_postmatch_snapshot.publish_pending.json"
        )
        assert first["status"] == "publish_pending"
        assert first["reason"] == "state_write_failed"
        assert pending_path.exists()
        assert not paths["state_path"].exists()

        second = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:20:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=publish_fn,
            **paths,
        )

        assert second["status"] == "republished"
        assert len(publish_calls) == 2
        assert paths["state_path"].exists()
        assert not pending_path.exists()


def test_postmatch_republishes_same_finished_for_a_new_incomplete_base_parent():
    publish_calls = []

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )

        newer_base = _base_snapshot()
        newer_base["run"]["run_id"] = "20260716T003000Z-live"
        newer_base["snapshot_at"] = "2026-07-16T00:30:00+00:00"
        paths["base_snapshot_path"].write_text(
            json.dumps(newer_base, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        second = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:35:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )

        output = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))
        assert first["status"] == "published"
        assert second["status"] == "published"
        assert len(publish_calls) == 2
        assert output["run"]["parent_run_id"] == "20260716T003000Z-live"


def test_postmatch_blocks_finished_store_score_drift_without_publishing():
    publish_calls = []

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=publish_fn,
            **paths,
        )
        output_bytes = paths["postmatch_snapshot_path"].read_bytes()
        state_bytes = paths["state_path"].read_bytes()
        store = json.loads(paths["finished_store_path"].read_text(encoding="utf-8"))
        next(iter(store.values()))["result"] = {"home_score": 2, "away_score": 1}
        paths["finished_store_path"].write_text(json.dumps(store), encoding="utf-8")

        drifted = run_postmatch_publish(
            live=True,
            now="2026-07-16T01:00:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "published"
        assert drifted["status"] == "blocked"
        assert drifted["reason"] == "finished_score_mismatch_manual_review_required"
        assert paths["postmatch_snapshot_path"].read_bytes() == output_bytes
        assert paths["state_path"].read_bytes() == state_bytes
        assert len(publish_calls) == 1


def test_postmatch_blocks_unverified_finished_store_record():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        fake_record = {
            "competition_id": "fifa_world_cup_2026",
            "competition": {"id": "fifa_world_cup_2026"},
            "kickoff_at_utc": "2026-07-14T19:00:00+00:00",
            "home_team": "Spain",
            "away_team": "Brazil",
            "home_canonical": "spain",
            "away_canonical": "brazil",
            "result": {"home_score": 3, "away_score": 0},
        }
        paths["finished_store_path"].parent.mkdir(parents=True, exist_ok=True)
        paths["finished_store_path"].write_text(
            json.dumps({"fake": fake_record}),
            encoding="utf-8",
        )

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "unverified_finished_score"
        assert not paths["postmatch_snapshot_path"].exists()


def test_postmatch_blocks_conflicting_base_and_previous_finished_scores():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
            **paths,
        )
        previous = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))
        base = _base_snapshot()
        base["finished"] = previous["finished"]
        base["finished"]["matches"][0]["result"] = {
            "home_score": 2,
            "away_score": 1,
        }
        paths["base_snapshot_path"].write_text(json.dumps(base), encoding="utf-8")

        conflicted = run_postmatch_publish(
            live=True,
            now="2026-07-16T01:15:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "published"
        assert conflicted["status"] == "blocked"
        assert conflicted["reason"] == "finished_reference_conflict"


def test_postmatch_keeps_pending_for_http_200_with_unsuccessful_ingest_status():
    for ingest_status in ("rejected", None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _paths(root)
            _seed_base(paths)

            result = run_postmatch_publish(
                live=True,
                now=NOW,
                endpoint=ENDPOINT,
                secret="test-secret-long-enough-for-validation!!",
                fetch_fn=_fetch(_openfootball()),
                publish_fn=lambda **_kwargs: {
                    "status": "sent",
                    "http_status": 200,
                    "ingest_status": ingest_status,
                },
                **paths,
            )

            pending_path = paths["postmatch_snapshot_path"].with_name(
                "wc2026_postmatch_snapshot.publish_pending.json"
            )
            assert result["status"] == "publish_pending"
            assert pending_path.exists()
            assert not paths["state_path"].exists()


def test_postmatch_live_locks_all_shared_write_resources_before_secret_or_fetch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        with postmatch_module._exclusive_postmatch_lock(paths["results_path"]):
            result = run_postmatch_publish(
                live=True,
                now=NOW,
                endpoint=ENDPOINT,
                fetch_fn=_forbidden("fetch"),
                publish_fn=_forbidden("publish"),
                load_env_fn=_forbidden("load_env"),
                **paths,
            )

        assert result == {"status": "blocked", "reason": "postmatch_already_running"}


def test_postmatch_pending_cannot_be_retried_to_a_different_endpoint():
    publish_calls = []

    def first_publish(**kwargs):
        publish_calls.append(kwargs)
        return {"status": "sent", "http_status": 503, "ingest_status": None}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=first_publish,
            **paths,
        )
        second = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:20:00+00:00",
            endpoint="https://other.example.test/api/ingest/snapshot",
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "publish_pending"
        assert second["status"] == "publish_pending_invalid"
        assert second["reason"] == "pending_endpoint_changed"
        assert len(publish_calls) == 1
        assert paths["postmatch_snapshot_path"].with_name(
            "wc2026_postmatch_snapshot.publish_pending.json"
        ).exists()


def test_postmatch_invalid_observed_at_has_no_live_side_effects():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        base_bytes = _seed_base(paths)

        result = run_postmatch_publish(
            live=True,
            now="not-a-datetime",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "error", "reason": "invalid_observed_at"}
        assert paths["base_snapshot_path"].read_bytes() == base_bytes
        assert not postmatch_module._postmatch_lock_path(
            paths["postmatch_snapshot_path"]
        ).exists()


def test_postmatch_blocks_when_published_snapshot_no_longer_matches_state():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
            **paths,
        )
        output = json.loads(paths["postmatch_snapshot_path"].read_text(encoding="utf-8"))
        output["tampered"] = True
        paths["postmatch_snapshot_path"].write_text(json.dumps(output), encoding="utf-8")

        tampered = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:30:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "published"
        assert tampered == {
            "status": "blocked",
            "reason": "postmatch_state_snapshot_mismatch",
        }


def test_postmatch_blocks_duplicate_openfootball_result_identity():
    raw = _openfootball()
    duplicate = dict(raw["matches"][0])
    duplicate["num"] = 103
    raw["matches"].append(duplicate)
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(raw),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "duplicate_openfootball_result"}


def test_postmatch_blocks_duplicate_results_csv_identity():
    row = {
        "kickoff_at_utc": "2026-07-15T19:00:00+00:00",
        "home_team": "England",
        "away_team": "Argentina",
        "home_canonical": "england",
        "away_canonical": "argentina",
        "home_score": "1",
        "away_score": "1",
        "captured_at": NOW,
    }
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        _write_results(paths["results_path"], [row, dict(row)])

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "duplicate_results_identity"}


def test_postmatch_blocks_duplicate_finished_identity():
    record = {
        "kickoff_at_utc": "2026-07-15T19:00:00+00:00",
        "home_team": "England",
        "away_team": "Argentina",
        "home_canonical": "england",
        "away_canonical": "argentina",
        "result": {"home_score": 1, "away_score": 1},
    }
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        base = json.loads(paths["base_snapshot_path"].read_text(encoding="utf-8"))
        base["finished"]["matches"] = [record, dict(record)]
        paths["base_snapshot_path"].write_text(json.dumps(base), encoding="utf-8")

        result = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert result == {"status": "blocked", "reason": "duplicate_finished_identity"}


def test_postmatch_pending_stage_failure_never_replaces_canonical_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        original_attempt = postmatch_module.attempt_publish

        def fail_stage(**_kwargs):
            raise OSError("simulated pending stage failure")

        try:
            postmatch_module.attempt_publish = fail_stage
            failed = run_postmatch_publish(
                live=True,
                now=NOW,
                endpoint=ENDPOINT,
                secret="test-secret-long-enough-for-validation!!",
                fetch_fn=_fetch(_openfootball()),
                publish_fn=_forbidden("publish"),
                **paths,
            )
        finally:
            postmatch_module.attempt_publish = original_attempt

        prepared = list(paths["postmatch_snapshot_path"].parent.glob("*.prepared.json"))
        assert failed["status"] == "error"
        assert failed["reason"] == "pending_stage_failed"
        assert len(prepared) == 1
        assert not paths["postmatch_snapshot_path"].exists()
        assert not paths["state_path"].exists()

        recovered = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 200,
                "ingest_status": "stored",
            },
            **paths,
        )

        assert recovered["status"] == "published"
        assert paths["postmatch_snapshot_path"].exists()
        assert not list(paths["postmatch_snapshot_path"].parent.glob("*.prepared.json"))


def test_postmatch_rejects_pending_that_points_to_an_unowned_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = _paths(root)
        _seed_base(paths)
        first = run_postmatch_publish(
            live=True,
            now=NOW,
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_fetch(_openfootball()),
            publish_fn=lambda **_kwargs: {
                "status": "sent",
                "http_status": 503,
                "ingest_status": None,
            },
            **paths,
        )
        pending_path = paths["postmatch_snapshot_path"].with_name(
            "wc2026_postmatch_snapshot.publish_pending.json"
        )
        foreign_path = root / "cache" / "foreign.json"
        foreign_path.write_text(
            json.dumps(_base_snapshot(), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        pending["snapshot_path"] = str(foreign_path)
        pending["snapshot_sha256"] = hashlib.sha256(foreign_path.read_bytes()).hexdigest()
        pending_path.write_text(json.dumps(pending), encoding="utf-8")

        rejected = run_postmatch_publish(
            live=True,
            now="2026-07-16T00:20:00+00:00",
            endpoint=ENDPOINT,
            secret="test-secret-long-enough-for-validation!!",
            fetch_fn=_forbidden("fetch"),
            publish_fn=_forbidden("publish"),
            **paths,
        )

        assert first["status"] == "publish_pending"
        assert rejected["status"] == "publish_pending_invalid"
        assert rejected["reason"] == "pending_snapshot_not_owned"
