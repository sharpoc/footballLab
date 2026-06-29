from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.scheduled_refresh as scheduled_refresh
from worldcup.scheduled_refresh import run_scheduled_refresh
from worldcup.theoddsapi_keys import PRIMARY_PROVIDER, SECONDARY_PROVIDER


@dataclass(frozen=True)
class FakeRefreshResult:
    snapshot_path: Path
    snapshot: dict
    run_metadata: dict


def test_scheduled_refresh_dry_run_does_not_call_refresh_even_when_due():
    def refresh_fn(**_kwargs):
        raise AssertionError("refresh should not run in dry-run mode")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_scheduled_refresh(
            now="2026-06-08T00:00:00+00:00",
            live=False,
            cache_dir=root / "cache",
            snapshot_path=root / "cache" / "analysis_snapshot.json",
            quota_path=root / "cache" / "quota.json",
            api_key="fake-key",
            refresh_fn=refresh_fn,
        )

        assert result["status"] == "dry_run"
        assert result["report"]["decision"]["should_refresh"] is True
        assert result["refresh"] is None


def test_scheduled_refresh_dry_run_does_not_load_env():
    def fail_load_env(_path):
        raise AssertionError("dry-run must not load env")

    old_load_env = scheduled_refresh._load_env
    scheduled_refresh._load_env = fail_load_env
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_scheduled_refresh(
                now="2026-06-08T00:00:00+00:00",
                live=False,
                env_path=root / ".env",
                cache_dir=root / "cache",
                snapshot_path=root / "cache" / "analysis_snapshot.json",
                quota_path=root / "cache" / "quota.json",
            )

            assert result["status"] == "dry_run"
            assert result["refresh"] is None
    finally:
        scheduled_refresh._load_env = old_load_env


def test_scheduled_refresh_skips_live_when_not_due():
    def refresh_fn(**_kwargs):
        raise AssertionError("refresh should not run before next_due_at")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "cache" / "analysis_snapshot.json"
        quota_path = root / "cache" / "quota.json"
        snapshot_path.parent.mkdir()
        snapshot_path.write_text(
            """
            {
              "snapshot_at": "2026-06-08T00:00:00+00:00",
              "run": {"observed_at": "2026-06-08T00:00:00+00:00"},
              "matches": [{"kickoff_at_utc": "2026-06-11T19:00:00+00:00"}]
            }
            """.strip()
        )
        quota_path.write_text(
            """
            {
              "providers": {
                "theoddsapi": {"remaining": 494, "last": 3}
              }
            }
            """.strip()
        )

        result = run_scheduled_refresh(
            now="2026-06-08T03:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            api_key="fake-key",
            refresh_fn=refresh_fn,
        )

        assert result["status"] == "skipped"
        assert result["report"]["decision"]["reason"] == "not_due"
        assert result["refresh"] is None


def test_scheduled_refresh_runs_live_when_due():
    calls = []

    def refresh_fn(**kwargs):
        calls.append(kwargs)
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "cache" / "analysis_snapshot.json"
        quota_path = root / "cache" / "quota.json"
        snapshot_path.parent.mkdir()
        snapshot_path.write_text(
            """
            {
              "snapshot_at": "2026-06-08T00:00:00+00:00",
              "run": {"observed_at": "2026-06-08T00:00:00+00:00"},
              "matches": [{"kickoff_at_utc": "2026-06-11T19:00:00+00:00"}]
            }
            """.strip()
        )
        quota_path.write_text(
            """
            {
              "providers": {
                "theoddsapi": {"remaining": 494, "last": 3}
              }
            }
            """.strip()
        )

        result = run_scheduled_refresh(
            now="2026-06-09T00:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            api_key="fake-key",
            refresh_fn=refresh_fn,
        )

        assert result["status"] == "refreshed"
        assert result["refresh"]["matches"] == 72
        assert result["refresh"]["run_id"] == "20260609T000000Z-live"
        assert calls[0]["api_key"] == "fake-key"
        assert calls[0]["observed_at"] == "2026-06-09T00:00:00+00:00"


def test_scheduled_refresh_rotates_to_secondary_key_when_primary_quota_is_exhausted():
    calls = []

    def refresh_fn(**kwargs):
        calls.append(kwargs)
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "cache" / "analysis_snapshot.json"
        quota_path = root / "cache" / "quota.json"
        env_path = root / ".env"
        snapshot_path.parent.mkdir()
        snapshot_path.write_text(
            """
            {
              "snapshot_at": "2026-06-08T00:00:00+00:00",
              "run": {"observed_at": "2026-06-08T00:00:00+00:00"},
              "matches": [{"kickoff_at_utc": "2026-06-11T19:00:00+00:00"}]
            }
            """.strip()
        )
        quota_path.write_text(
            f"""
            {{
              "providers": {{
                "{PRIMARY_PROVIDER}": {{"remaining": 0, "last": 3}},
                "{SECONDARY_PROVIDER}": {{"remaining": 497, "last": 3}},
                "theoddsapi": {{"remaining": 0, "last": 3}}
              }}
            }}
            """.strip()
        )
        env_path.write_text(
            "THE_ODDS_API_KEY_PRIMARY=primary-key\nTHE_ODDS_API_KEY_SECONDARY=secondary-key\n"
        )

        result = run_scheduled_refresh(
            now="2026-06-09T00:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            env_path=env_path,
            refresh_fn=refresh_fn,
        )

        assert result["status"] == "refreshed"
        assert calls[0]["api_key"] == "secondary-key"
        assert calls[0]["theoddsapi_provider"] == SECONDARY_PROVIDER
        assert result["refresh"]["odds_api_key_slot"] == "secondary"
