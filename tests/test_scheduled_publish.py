from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.scheduled_publish import run_scheduled_publish


@dataclass(frozen=True)
class FakeRefreshResult:
    snapshot_path: Path
    snapshot: dict
    run_metadata: dict


def _write_not_due_snapshot(root: Path) -> tuple[Path, Path]:
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
        """.strip(),
        encoding="utf-8",
    )
    quota_path.write_text(
        """
        {
          "providers": {
            "theoddsapi": {"remaining": 494, "last": 3}
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    return snapshot_path, quota_path


def test_scheduled_publish_skips_publish_when_refresh_is_not_due():
    def refresh_fn(**_kwargs):
        raise AssertionError("refresh should not run before next_due_at")

    def publish_fn(**_kwargs):
        raise AssertionError("publish should not run when refresh is skipped")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path, quota_path = _write_not_due_snapshot(root)

        result = run_scheduled_publish(
            now="2026-06-08T03:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            api_key="fake-key",
            secret="fake-secret",
            refresh_fn=refresh_fn,
            publish_fn=publish_fn,
        )

    assert result["status"] == "skipped"
    assert result["refresh"]["status"] == "skipped"
    assert result["publish"] is None


def test_scheduled_publish_refreshes_then_publishes_when_due():
    refresh_calls = []
    publish_calls = []

    def refresh_fn(**kwargs):
        refresh_calls.append(kwargs)
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260608T120000Z-live"},
        )

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {
            "status": "sent",
            "http_status": 200,
            "ingest_status": "stored",
            "request": {"run_id": "20260608T120000Z-live"},
        }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path, quota_path = _write_not_due_snapshot(root)

        result = run_scheduled_publish(
            now="2026-06-08T12:00:00+00:00",
            live=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            api_key="fake-key",
            secret="fake-secret",
            refresh_fn=refresh_fn,
            publish_fn=publish_fn,
        )

    assert result["status"] == "published"
    assert result["refresh"]["status"] == "refreshed"
    assert result["publish"]["ingest_status"] == "stored"
    assert refresh_calls[0]["api_key"] == "fake-key"
    assert publish_calls[0]["secret"] == "fake-secret"
    assert publish_calls[0]["live"] is True
    assert publish_calls[0]["endpoint"] == "https://football.celab.xin/api/ingest/snapshot"
