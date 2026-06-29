from __future__ import annotations

import csv
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _csl_event(event_id: str, home_team: str, away_team: str) -> dict:
    return {
        "id": event_id,
        "sport_key": "soccer_china_superleague",
        "commence_time": "2026-07-03T11:35:00Z",
        "home_team": home_team,
        "away_team": away_team,
        "bookmakers": [
            {
                "key": "must-not-leak",
                "last_update": "2026-07-03T09:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-07-03T09:00:00Z",
                        "outcomes": [
                            {"name": home_team, "price": 2.35},
                            {"name": "Draw", "price": 3.35},
                            {"name": away_team, "price": 3.2},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": "2026-07-03T09:00:00Z",
                        "outcomes": [
                            {"name": "Over", "price": 1.92, "point": 2.5},
                            {"name": "Under", "price": 1.91, "point": 2.5},
                        ],
                    },
                    {
                        "key": "spreads",
                        "last_update": "2026-07-03T09:00:00Z",
                        "outcomes": [
                            {"name": home_team, "price": 1.9, "point": -0.5},
                            {"name": away_team, "price": 1.93, "point": 0.5},
                        ],
                    },
                ],
            }
        ],
    }


def _write_csl_odds_cache(cache_dir: Path, *, event_count: int = 1) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    events = [
        _csl_event("csl-event-1", "Shanghai Port", "Shandong Taishan"),
        _csl_event("csl-event-2", "Beijing Guoan", "Chengdu Rongcheng"),
    ][:event_count]
    (cache_dir / "theoddsapi_csl_2026_odds.json").write_text(
        json.dumps(events),
        encoding="utf-8",
    )


def _write_results(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with (cache_dir / "club_results_csl_2026.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "competition_id",
                "season",
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "neutral",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-07-03",
                "home_team": "Shanghai Port",
                "away_team": "Shandong Taishan",
                "home_score": "2",
                "away_score": "1",
                "neutral": "0",
            }
        )


def test_dry_run_reads_local_state_without_writing_or_loading_env():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        before_files = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("dry-run must not load .env")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            load_env=fail_load_env,
        )

        assert summary["status"] in ("ok", "warn")
        assert summary["mode"] == "dry_run"
        assert summary["competition_id"] == "csl_2026"
        assert summary["safety"] == {
            "read_env": False,
            "called_theoddsapi": False,
            "published": False,
            "deployed": False,
            "changed_launch_agent": False,
        }
        local_state = summary["steps"]["local_state"]
        assert local_state["status"] in ("ok", "warn")
        assert local_state["cache_exists"] is True
        after_files = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
        assert after_files == before_files
        assert not (root / "data/local/diagnostics/csl_live_league_snapshot.json").exists()
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()


def test_dry_run_summary_does_not_expose_raw_market_or_secret_text():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("dry-run must not load .env")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            load_env=fail_load_env,
        )

        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True).lower()
        for forbidden in (
            "must-not-leak",
            "bookmaker",
            "api_key",
            "secret",
            "hmac",
            "下注金额",
            "stake amount",
        ):
            assert forbidden.lower() not in serialized


def test_cli_default_prints_dry_run_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        stdout = io.StringIO()

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("dry-run CLI must not load .env")

        original_load_env = runner._load_env
        runner._load_env = fail_load_env
        try:
            with redirect_stdout(stdout):
                exit_code = runner.main(
                    ["--root", str(root), "--generated-at", "2026-07-03T09:00:00Z"]
                )
        finally:
            runner._load_env = original_load_env

        assert exit_code == 0
        payload = json.loads(stdout.getvalue())
        assert payload["mode"] == "dry_run"
        assert payload["safety"]["read_env"] is False
        assert payload["safety"]["called_theoddsapi"] is False


def test_dry_run_uses_custom_cache_dir_and_history():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        default_cache = root / "data/cache"
        cache_dir = root / "custom/cache"
        history = root / "custom/history"
        _write_csl_odds_cache(default_cache, event_count=1)
        _write_csl_odds_cache(cache_dir, event_count=2)
        _write_results(cache_dir)
        history.mkdir(parents=True)
        (history / "snapshot_20260703T080000Z.json").write_text("{}", encoding="utf-8")
        (history / "snapshot_20260703T090000Z.json").write_text("{}", encoding="utf-8")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            cache_dir="custom/cache",
            history="custom/history",
        )

        local_state = summary["steps"]["local_state"]
        assert local_state["cache_exists"] is True
        assert local_state["results_exists"] is True
        assert local_state["history_snapshots"] == 2
        assert local_state["events"] == 2
        assert local_state["fixtures"] == 2
        assert local_state["odds_events"] == 2


def test_dry_run_uses_custom_quota_path():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        quota_path = root / "custom/quota.json"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        quota_path.parent.mkdir(parents=True)
        quota_path.write_text(
            json.dumps({"providers": {"theoddsapi_secondary": {"remaining": 17}}}),
            encoding="utf-8",
        )

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            quota_path="custom/quota.json",
        )

        assert summary["steps"]["local_state"]["quota_remaining"] == 17
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "theoddsapi_secondary" not in serialized
        assert "\"providers\"" not in serialized


def test_dry_run_quota_uses_secondary_when_primary_is_exhausted():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        quota_path = root / "custom/quota.json"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        quota_path.parent.mkdir(parents=True)
        quota_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "theoddsapi_primary": {"remaining": 0},
                        "theoddsapi_secondary": {"remaining": 34},
                        "theoddsapi": {"remaining": 0},
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            quota_path="custom/quota.json",
        )

        assert summary["steps"]["local_state"]["quota_remaining"] == 34
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "theoddsapi_primary" not in serialized
        assert "theoddsapi_secondary" not in serialized
        assert "\"providers\"" not in serialized


def test_dry_run_quota_ignores_non_finite_remaining():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        quota_path = root / "custom/quota.json"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        quota_path.parent.mkdir(parents=True)
        quota_path.write_text(
            '{"providers":{"theoddsapi_primary":{"remaining":NaN}}}',
            encoding="utf-8",
        )

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            quota_path="custom/quota.json",
        )

        assert summary["steps"]["local_state"]["quota_remaining"] is None
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "NaN" not in serialized


def test_cli_live_odds_without_run_local_is_blocked_without_loading_env():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        stdout = io.StringIO()

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("blocked live-odds dry-run must not load .env")

        original_load_env = runner._load_env
        runner._load_env = fail_load_env
        try:
            with redirect_stdout(stdout):
                exit_code = runner.main(
                    [
                        "--root",
                        str(root),
                        "--generated-at",
                        "2026-07-03T09:00:00Z",
                        "--live-odds",
                    ]
                )
        finally:
            runner._load_env = original_load_env

        payload = json.loads(stdout.getvalue())
        assert exit_code == 2
        assert payload["status"] == "blocked"
        assert payload["mode"] == "dry_run"
        assert payload["steps"]["live_odds"] == {
            "status": "blocked",
            "reason": "live_odds_requires_run_local",
        }
        assert payload["safety"]["read_env"] is False
        assert payload["safety"]["called_theoddsapi"] is False


def test_cli_postmatch_without_run_local_is_blocked_without_loading_env():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        stdout = io.StringIO()

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("blocked postmatch dry-run must not load .env")

        original_load_env = runner._load_env
        runner._load_env = fail_load_env
        try:
            with redirect_stdout(stdout):
                exit_code = runner.main(
                    [
                        "--root",
                        str(root),
                        "--generated-at",
                        "2026-07-03T09:00:00Z",
                        "--postmatch",
                    ]
                )
        finally:
            runner._load_env = original_load_env

        payload = json.loads(stdout.getvalue())
        assert exit_code == 2
        assert payload["status"] == "blocked"
        assert payload["mode"] == "dry_run"
        assert payload["steps"]["postmatch"] == {
            "status": "blocked",
            "reason": "postmatch_requires_run_local",
        }
        assert payload["safety"]["read_env"] is False
        assert payload["safety"]["called_theoddsapi"] is False


def test_run_local_writes_snapshot_archive_observation_and_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("local run without live-odds must not load .env")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            load_env=fail_load_env,
        )

        snapshot = root / "data/local/diagnostics/csl_live_league_snapshot.json"
        archive = root / "data/local/diagnostics/csl_history/snapshot_20260703T090000Z-live.json"
        observation = root / "data/cache/csl_observation_report_20260703T090000Z.md"
        summary_path = root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json"

        assert summary["status"] in {"ok", "warn"}
        assert summary["mode"] == "local"
        assert snapshot.exists()
        assert archive.exists()
        assert observation.exists()
        assert summary_path.exists()
        assert json.loads(summary_path.read_text(encoding="utf-8")) == summary
        assert summary["steps"]["snapshot"]["matches"] == 1
        assert summary["steps"]["archive"]["status"] in {"created", "duplicate"}
        assert summary["steps"]["observation"]["matches"] == 1
        assert summary["paths"]["snapshot"] == str(snapshot)
        assert summary["paths"]["archive"] == str(archive)
        assert summary["paths"]["observation"] == str(observation)
        assert summary["paths"]["summary"] == str(summary_path)
        assert summary["safety"]["read_env"] is False
        assert summary["safety"]["called_theoddsapi"] is False

        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("must-not-leak", "bookmaker", "api_key", "secret", "下注金额"):
            assert forbidden.lower() not in serialized.lower()


def test_cli_run_local_writes_artifacts_and_prints_safe_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)
        stdout = io.StringIO()

        def fail_load_env(*_args, **_kwargs):
            raise AssertionError("CLI --run-local without --live-odds must not load .env")

        original_load_env = runner._load_env
        runner._load_env = fail_load_env
        try:
            with redirect_stdout(stdout):
                exit_code = runner.main(
                    [
                        "--root",
                        str(root),
                        "--generated-at",
                        "2026-07-03T09:00:00Z",
                        "--run-local",
                    ]
                )
        finally:
            runner._load_env = original_load_env

        assert exit_code == 0
        payload = json.loads(stdout.getvalue())
        assert payload["mode"] == "local"
        assert payload["paths"]["snapshot"].endswith("csl_live_league_snapshot.json")
        assert Path(payload["paths"]["snapshot"]).exists()
        assert Path(payload["paths"]["archive"]).exists()
        assert Path(payload["paths"]["observation"]).exists()
        assert Path(payload["paths"]["summary"]).exists()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for forbidden in ("must-not-leak", "bookmaker", "api_key", "secret"):
            assert forbidden.lower() not in serialized.lower()


def test_live_odds_uses_env_only_when_explicit_and_keeps_summary_safe():
    import worldcup.csl_ops_runner as runner

    calls = {"load_env": 0, "transport": 0, "url": ""}

    def load_env(path: str) -> dict[str, str]:
        calls["load_env"] += 1
        assert path == ".env"
        return {"THE_ODDS_API_KEY_PRIMARY": "secret-key-value"}

    class Response:
        status = 200
        headers = {
            "x-requests-last": "3",
            "x-requests-remaining": "99",
            "x-requests-used": "3",
        }

        def read(self) -> bytes:
            return json.dumps(
                [
                    _csl_event("csl-event-1", "Shanghai Port", "Shandong Taishan"),
                ]
            ).encode("utf-8")

    def transport(url: str) -> Response:
        calls["transport"] += 1
        calls["url"] = url
        return Response()

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_results(root / "data/cache")

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            live_odds=True,
            load_env=load_env,
            live_transport=transport,
        )

        assert calls["load_env"] == 1
        assert calls["transport"] == 1
        assert "apiKey=secret-key-value" in calls["url"]
        assert summary["status"] in {"ok", "warn"}
        assert summary["mode"] == "live_odds_local"
        assert summary["steps"]["live_odds"]["status"] == "fetched"
        assert summary["steps"]["snapshot"]["matches"] == 1
        assert summary["safety"]["read_env"] is True
        assert summary["safety"]["called_theoddsapi"] is True

        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            "secret-key-value",
            "must-not-leak",
            "bookmaker",
            "api_key",
            "quota_entry",
            "theoddsapi_provider",
            "下注金额",
        ):
            assert forbidden.lower() not in serialized.lower()


def test_run_local_postmatch_writes_eval_report_and_keeps_pending_gate_false():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            postmatch=True,
            postmatch_min_sample=1,
            postmatch_warmup_matches=0,
        )

        assert summary["status"] in {"ok", "warn"}
        assert summary["steps"]["postmatch"]["status"] == "ok"
        assert summary["postmatch"]["joined"] == 1
        assert summary["postmatch"]["pending_gate"]["can_lift_club_rating_pending"] is False
        assert (root / "data/local/backtest/csl_2026_eval.csv").exists()
        assert (root / "data/local/backtest/csl_2026_report.json").exists()
        assert (root / "data/local/diagnostics/csl_pending_gate_20260703T090000Z.json").exists()

        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in ("must-not-leak", "bookmaker", "api_key", "secret", "下注金额"):
            assert forbidden.lower() not in serialized.lower()


def test_run_local_missing_cache_blocks_without_writing_snapshot_or_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
        )

        assert summary["status"] == "blocked"
        assert summary["steps"]["local_state"]["reason"] == "missing_odds_cache"
        assert not (root / "data/local/diagnostics/csl_live_league_snapshot.json").exists()
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "api_key" not in serialized
        assert "secret" not in serialized


def test_run_local_invalid_cache_blocks_without_writing_snapshot_or_summary():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_path = root / "data/cache/theoddsapi_csl_2026_odds.json"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(
            json.dumps({"api_key": "secret-like-value", "bad": "shape"}),
            encoding="utf-8",
        )

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
        )

        assert summary["status"] == "blocked"
        assert summary["steps"]["local_state"]["reason"] == "invalid_odds_cache_shape"
        assert not (root / "data/local/diagnostics/csl_live_league_snapshot.json").exists()
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()
        serialized = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        assert "secret-like-value" not in serialized
        assert "api_key" not in serialized


def test_run_local_blocks_write_paths_outside_ignored_artifacts():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        _write_results(cache_dir)

        summary = runner.run_csl_ops(
            root=root,
            generated_at="2026-07-03T09:00:00Z",
            run_local=True,
            snapshot_out="unsafe/snapshot.json",
        )

        assert summary["status"] == "blocked"
        assert summary["steps"]["write_paths"]["status"] == "blocked"
        assert summary["steps"]["write_paths"]["guards"]["snapshot"]["reason"] == (
            "write_path_not_ignored"
        )
        assert not (root / "unsafe/snapshot.json").exists()
        assert not (root / "data/local/diagnostics/csl_ops_runner_20260703T090000Z.json").exists()


def test_unsupported_competition_is_blocked_without_local_state():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        summary = runner.run_csl_ops(
            root=Path(tmp),
            competition_id="jleague_2026",
            generated_at="2026-07-03T09:00:00Z",
        )

        assert summary["status"] == "blocked"
        assert summary["steps"]["competition"] == {
            "status": "blocked",
            "reason": "unsupported_competition",
        }
        assert "local_state" not in summary["steps"]


def test_cli_invalid_generated_at_returns_safe_json():
    import worldcup.csl_ops_runner as runner

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = runner.main(["--generated-at", "not-a-date"])

    assert exit_code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "blocked"
    assert payload["steps"]["error"]["status"] == "blocked"
    assert payload["steps"]["error"]["error_type"] == "ValueError"


def test_cli_run_local_missing_cache_returns_nonzero_safe_json():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = runner.main(
                [
                    "--root",
                    str(root),
                    "--generated-at",
                    "2026-07-03T09:00:00Z",
                    "--run-local",
                ]
            )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 2
        assert payload["status"] == "blocked"
        assert payload["steps"]["local_state"]["reason"] == "missing_odds_cache"
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assert "api_key" not in serialized
        assert "secret" not in serialized
        assert not (root / "data/local/diagnostics/csl_live_league_snapshot.json").exists()


def test_cli_postmatch_missing_results_returns_nonzero_safe_json():
    import worldcup.csl_ops_runner as runner

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_dir = root / "data/cache"
        _write_csl_odds_cache(cache_dir)
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = runner.main(
                [
                    "--root",
                    str(root),
                    "--generated-at",
                    "2026-07-03T09:00:00Z",
                    "--run-local",
                    "--postmatch",
                    "--postmatch-min-sample",
                    "1",
                    "--postmatch-warmup-matches",
                    "0",
                ]
            )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 2
        assert payload["status"] == "blocked"
        assert payload["steps"]["error"]["status"] == "blocked"
        assert payload["steps"]["error"]["error_type"] == "FileNotFoundError"
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assert "api_key" not in serialized
        assert "secret" not in serialized
        assert "must-not-leak" not in serialized


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            suite.addTest(unittest.FunctionTestCase(value))
    return suite
