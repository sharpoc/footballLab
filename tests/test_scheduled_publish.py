from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import worldcup.scheduled_publish as scheduled_publish
from worldcup.scheduled_publish import run_scheduled_publish
from worldcup.theoddsapi_keys import PRIMARY_PROVIDER, SECONDARY_PROVIDER


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


def test_scheduled_publish_dry_run_does_not_load_env_or_publish():
    def fail_load_env(_path):
        raise AssertionError("dry-run must not load env")

    def refresh_fn(**_kwargs):
        raise AssertionError("dry-run must not refresh")

    def publish_fn(**_kwargs):
        raise AssertionError("dry-run must not publish")

    old_load_env = scheduled_publish._load_env
    scheduled_publish._load_env = fail_load_env
    try:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_scheduled_publish(
                now="2026-06-08T00:00:00+00:00",
                live=False,
                notify=False,
                env_path=root / ".env",
                cache_dir=root / "cache",
                snapshot_path=root / "cache" / "analysis_snapshot.json",
                quota_path=root / "cache" / "quota.json",
                refresh_fn=refresh_fn,
                publish_fn=publish_fn,
            )

            assert result["status"] == "dry_run"
            assert result["publish"] is None
            assert result["notification"] is None
    finally:
        scheduled_publish._load_env = old_load_env


def test_scheduled_publish_refreshes_then_publishes_when_due():
    refresh_calls = []
    publish_calls = []

    def refresh_fn(**kwargs):
        refresh_calls.append(kwargs)
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {
            "status": "sent",
            "http_status": 200,
            "ingest_status": "stored",
            "request": {"run_id": "20260609T000000Z-live"},
        }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path, quota_path = _write_not_due_snapshot(root)
        env_path = root / ".env"
        quota_path.write_text(
            json.dumps(
                {
                    "providers": {
                        PRIMARY_PROVIDER: {"remaining": 0, "last": 3},
                        SECONDARY_PROVIDER: {"remaining": 497, "last": 3},
                        "theoddsapi": {"remaining": 0, "last": 3},
                    }
                }
            ),
            encoding="utf-8",
        )
        env_path.write_text(
            "THE_ODDS_API_KEY_PRIMARY=primary-key\n"
            "THE_ODDS_API_KEY_SECONDARY=secondary-key\n"
            "INGEST_HMAC_SECRET=fake-secret\n",
            encoding="utf-8",
        )

        result = run_scheduled_publish(
            now="2026-06-09T00:00:00+00:00",
            live=True,
            env_path=env_path,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            refresh_fn=refresh_fn,
            publish_fn=publish_fn,
        )

    assert result["status"] == "published"
    assert result["refresh"]["status"] == "refreshed"
    assert result["publish"]["ingest_status"] == "stored"
    assert refresh_calls[0]["api_key"] == "secondary-key"
    assert refresh_calls[0]["theoddsapi_provider"] == SECONDARY_PROVIDER
    assert publish_calls[0]["secret"] == "fake-secret"
    assert publish_calls[0]["live"] is True
    assert publish_calls[0]["endpoint"] == "https://football.celab.xin/api/ingest/snapshot"


def test_scheduled_publish_blocks_empty_refreshed_snapshot():
    publish_calls = []

    def refresh_fn(**kwargs):
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={
                "counts": {"fixtures": 104, "odds_events": 72, "match_inputs": 0, "matches": 0},
                "data_quality": {"missing_elo": ["Mexico", "South Africa"]},
            },
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        raise AssertionError("empty snapshots must not be published")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path, quota_path = _write_not_due_snapshot(root)

        result = run_scheduled_publish(
            now="2026-06-09T00:00:00+00:00",
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

    assert result["status"] == "blocked"
    assert result["reason"] == "empty_refreshed_snapshot"
    assert result["refresh"]["status"] == "refreshed"
    assert result["publish"] is None
    assert publish_calls == []


def _write_change_snapshot(path: Path, *, grade: str, ev: float, odds: float, run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "snapshot_at": "2026-06-09T08:00:00+00:00",
                "run": {"run_id": run_id, "observed_at": "2026-06-09T08:00:00+00:00"},
                "counts": {"matches": 1},
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "market": {
                            "1x2": {
                                "market_probs": {"home": 0.57, "draw": 0.25, "away": 0.18},
                                "odds": {"home": odds, "draw": 3.3, "away": 4.0},
                            }
                        },
                        "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                        "signals": [
                            {
                                "market_type": "1X2_90min",
                                "selection": "home",
                                "grade": grade,
                                "ev": ev,
                                "edge": 0.041,
                                "status": "OK",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_scheduled_publish_notifies_significant_changes_after_publish():
    publish_calls = []
    notify_calls = []

    def refresh_fn(**kwargs):
        _write_change_snapshot(
            Path(kwargs["snapshot_path"]),
            grade="S",
            ev=0.092,
            odds=1.85,
            run_id="20260609T100000Z-live",
        )
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 1}},
            run_metadata={"run_id": "20260609T100000Z-live"},
        )

    def publish_fn(**kwargs):
        publish_calls.append(kwargs)
        return {
            "status": "sent",
            "http_status": 200,
            "ingest_status": "stored",
            "request": {"run_id": "20260609T100000Z-live"},
        }

    def notify_fn(content, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "cache" / "analysis_snapshot.json"
        quota_path = root / "cache" / "quota.json"
        _write_change_snapshot(
            snapshot_path,
            grade="A",
            ev=0.052,
            odds=2.0,
            run_id="20260609T080000Z-live",
        )
        quota_path.write_text(
            json.dumps({"providers": {"theoddsapi": {"remaining": 494, "last": 3}}}),
            encoding="utf-8",
        )

        result = run_scheduled_publish(
            now="2026-06-09T10:00:00+00:00",
            live=True,
            force=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            api_key="fake-key",
            secret="fake-secret",
            refresh_fn=refresh_fn,
            publish_fn=publish_fn,
            notify_fn=notify_fn,
        )

    assert result["status"] == "published"
    assert result["notification"]["status"] == "sent"
    assert notify_calls[0]["summary"] == "世界杯信号更新：1 条变化"
    assert "墨西哥 对 南非 | 胜平负 - 主队" in notify_calls[0]["content"]
    assert "等级 A → S" in notify_calls[0]["content"]
    assert publish_calls


def test_scheduled_publish_skips_notification_without_changes():
    notify_calls = []

    def refresh_fn(**kwargs):
        _write_change_snapshot(
            Path(kwargs["snapshot_path"]),
            grade="A",
            ev=0.052,
            odds=2.0,
            run_id="20260609T100000Z-live",
        )
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 1}},
            run_metadata={"run_id": "20260609T100000Z-live"},
        )

    def publish_fn(**_kwargs):
        return {"status": "sent", "http_status": 200, "ingest_status": "stored"}

    def notify_fn(content, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "cache" / "analysis_snapshot.json"
        quota_path = root / "cache" / "quota.json"
        _write_change_snapshot(
            snapshot_path,
            grade="A",
            ev=0.052,
            odds=2.0,
            run_id="20260609T080000Z-live",
        )
        quota_path.write_text(
            json.dumps({"providers": {"theoddsapi": {"remaining": 494, "last": 3}}}),
            encoding="utf-8",
        )

        result = run_scheduled_publish(
            now="2026-06-09T10:00:00+00:00",
            live=True,
            force=True,
            cache_dir=root / "cache",
            snapshot_path=snapshot_path,
            quota_path=quota_path,
            endpoint="https://football.celab.xin/api/ingest/snapshot",
            api_key="fake-key",
            secret="fake-secret",
            refresh_fn=refresh_fn,
            publish_fn=publish_fn,
            notify_fn=notify_fn,
        )

    assert result["notification"] == {"status": "skipped", "reason": "no_significant_changes"}
    assert notify_calls == []


def _run_publish_with_quota(root, before, after, notify=True):
    """before/after are {provider: remaining}, simulating quota around one refresh."""
    snapshot_path, quota_path = _write_not_due_snapshot(root)
    env_path = root / ".env"
    env_path.write_text(
        "THE_ODDS_API_KEY_PRIMARY=fake-primary\nTHE_ODDS_API_KEY_SECONDARY=fake-secondary\n",
        encoding="utf-8",
    )
    quota_path.write_text(
        json.dumps({"providers": {p: {"remaining": r, "last": 3} for p, r in before.items()}}),
        encoding="utf-8",
    )
    notify_calls = []

    def refresh_fn(**kwargs):
        Path(kwargs["quota_path"]).write_text(
            json.dumps({"providers": {p: {"remaining": r, "last": 3} for p, r in after.items()}}),
            encoding="utf-8",
        )
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    def publish_fn(**kwargs):
        return {
            "status": "sent",
            "http_status": 200,
            "ingest_status": "stored",
            "request": {"run_id": "20260609T000000Z-live"},
        }

    def notify_fn(content, *, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    result = run_scheduled_publish(
        now="2026-06-09T00:00:00+00:00",
        live=True,
        env_path=env_path,
        cache_dir=root / "cache",
        snapshot_path=snapshot_path,
        quota_path=quota_path,
        endpoint="https://football.celab.xin/api/ingest/snapshot",
        api_key="fake-key",
        secret="fake-secret",
        notify=notify,
        refresh_fn=refresh_fn,
        publish_fn=publish_fn,
        notify_fn=notify_fn,
    )
    return result, notify_calls


def test_quota_alert_sent_when_primary_crosses_threshold():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 102, "theoddsapi": 102},
            after={"theoddsapi_primary": 99, "theoddsapi": 99},
        )

    assert result["status"] == "published"
    assert result["quota_alert"]["status"] == "sent"
    assert result["quota_alert"]["slots"] == [
        {"slot": "PRIMARY", "remaining": 99, "thresholds_crossed": [100]}
    ]
    alert_calls = [c for c in notify_calls if "额度告警" in c["summary"]]
    assert len(alert_calls) == 1
    assert "PRIMARY" in alert_calls[0]["content"]
    assert "99" in alert_calls[0]["content"]


def test_quota_alert_reports_primary_exhaustion_as_switchover():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 2, "theoddsapi": 2},
            after={"theoddsapi_primary": 0, "theoddsapi": 0},
        )

    assert result["quota_alert"]["slots"] == [
        {"slot": "PRIMARY", "remaining": 0, "thresholds_crossed": [0]}
    ]
    alert_calls = [c for c in notify_calls if "额度告警" in c["summary"]]
    assert len(alert_calls) == 1
    assert "耗尽" in alert_calls[0]["content"]
    assert "THE_ODDS_API_KEY" in alert_calls[0]["content"]


def test_quota_alert_not_sent_without_crossing():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 200, "theoddsapi": 200},
            after={"theoddsapi_primary": 197, "theoddsapi": 197},
        )

    assert result["status"] == "published"
    assert result["quota_alert"] is None
    assert [c for c in notify_calls if "额度告警" in c["summary"]] == []


def test_quota_alert_respects_no_notify():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 102, "theoddsapi": 102},
            after={"theoddsapi_primary": 99, "theoddsapi": 99},
            notify=False,
        )

    assert result["status"] == "published"
    assert result["quota_alert"] is None
    assert notify_calls == []
