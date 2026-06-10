from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.scheduled_refresh import run_scheduled_refresh


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
            run_metadata={"run_id": "20260608T120000Z-live"},
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
            now="2026-06-08T12:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            api_key="fake-key",
            refresh_fn=refresh_fn,
        )

        assert result["status"] == "refreshed"
        assert result["refresh"]["matches"] == 72
        assert result["refresh"]["run_id"] == "20260608T120000Z-live"
        assert calls[0]["api_key"] == "fake-key"
        assert calls[0]["observed_at"] == "2026-06-08T12:00:00+00:00"
