from worldcup.league_scheduled_publish import run_league_scheduled_publish


def _fail(*args, **kwargs):
    raise AssertionError("dependency must not be called")


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
        return {"status": "refreshed", "snapshots": [{"snapshot_id": "fresh-1"}]}

    def publish(payload):
        calls.append(("publish", payload["snapshot_id"]))
        return {"status": "stored"}

    result = run_league_scheduled_publish(
        root=".",
        now="2026-08-24T12:00:00Z",
        plan={"requests": [{"competition_id": "epl_2026_27"}], "estimated_credits": 1},
        live=True,
        write=True,
        env_loader=lambda: {"INGEST_HMAC_SECRET": "x" * 32},
        refresh_fn=refresh,
        publish_fn=publish,
    )

    assert calls == ["refresh", ("publish", "fresh-1")]
    assert result["status"] == "published"
