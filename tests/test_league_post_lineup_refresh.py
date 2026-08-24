import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ingest import build_ingest_request
from worldcup.league_post_lineup_refresh import run_post_lineup_refresh
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


NOW = "2026-08-24T12:00:00+00:00"
EPL = "epl_2026_27"
LALIGA = "laliga_2026_27"


def _fail(*_args, **_kwargs):
    raise AssertionError("dependency must not be called")


def _active_row(competition_id):
    return {
        "competition_id": competition_id,
        "state": "active",
        "reason": None,
        "fingerprints": {
            name: f"{competition_id}-{name}"
            for name in ("sport_catalog", "odds_sample", "team_identity", "result_contract")
        },
    }


def _acceptance(*competition_ids):
    return {
        "schema_version": 1,
        "competitions": {
            competition_id: _active_row(competition_id)
            for competition_id in competition_ids
        },
    }


def _write_acceptance(root, *competition_ids):
    path = Path(root) / "data/local/leagues/acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_acceptance(*competition_ids), sort_keys=True),
        encoding="utf-8",
    )


def _receipt(competition_id, event_id, fingerprint_char, kickoff="2026-08-24T13:00:00+00:00"):
    fingerprint = fingerprint_char * 64
    return {
        "event_id": event_id,
        "source_match_id": f"source-{event_id}",
        "kickoff_at_utc": kickoff,
        "fetched_at": "2026-08-24T11:55:00+00:00",
        "lineup_fingerprint": fingerprint,
        "ack_key": {
            "competition_id": competition_id,
            "event_id": event_id,
            "lineup_fingerprint": fingerprint,
        },
    }


def _write_task4_pending(root, grouped):
    events = {}
    for competition_id, rows in grouped.items():
        for row in rows:
            events[f"{competition_id}:{row['event_id']}"] = {
                "competition_id": competition_id,
                **row,
            }
    path = Path(root) / "data/local/leagues/lineup_refresh_pending.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "events": events}, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _registry(*competition_ids):
    groups = {}
    for competition_id in competition_ids:
        groups[competition_id] = {
            f"{competition_id}_home": (f"{competition_id} Home",),
            f"{competition_id}_away": (f"{competition_id} Away",),
        }
    return LeagueTeamIdentityRegistry(groups)


def _snapshot_builder(payload, competition_id, observed_at, **_kwargs):
    event_ids = [str(row["id"]) for row in payload]
    return {
        "snapshot_at": observed_at,
        "competition": {"id": competition_id},
        "matches": [
            {
                "source_event_id": event_id,
                "competition": {"id": competition_id},
                "match_decision": {"label": "MATCH_PICK"},
            }
            for event_id in event_ids
        ],
    }


def _quota(**remaining_by_provider):
    return {
        "providers": {
            provider: {"remaining": remaining}
            for provider, remaining in remaining_by_provider.items()
        }
    }


def _env(two=False):
    value = {"THE_ODDS_API_KEY_PRIMARY": "p" * 40}
    if two:
        value["THE_ODDS_API_KEY_SECONDARY"] = "s" * 40
    return value


def _ack_events(result, group):
    return [row["ack_key"]["event_id"] for row in result["acks"][group]]


def test_dry_run_does_not_read_quota_refresh_publish_or_write():
    receipt = _receipt(EPL, "epl-1", "a")
    with TemporaryDirectory() as tmp:
        result = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=False,
            env_loader=_fail,
            quota_loader=_fail,
            refresh_fn=_fail,
            publish_fn=_fail,
            state_store_factory=_fail,
        )

        assert result["status"] == "dry_run"
        assert result["plan"] == {
            "competition_ids": [EPL],
            "receipt_count": 1,
        }
        assert list(Path(tmp).rglob("*")) == []


def test_quota_unknown_below_minimum_and_exhausted_block_without_side_effects():
    cases = (
        (_quota(theoddsapi_primary=None), "quota_unknown"),
        (_quota(theoddsapi_primary=30), "quota_below_minimum"),
        (_quota(theoddsapi_primary=0), "quota_exhausted"),
    )
    for ledger, reason in cases:
        receipt = _receipt(EPL, "epl-1", "a")
        with TemporaryDirectory() as tmp:
            _write_acceptance(tmp, EPL)
            pending_path = _write_task4_pending(tmp, {EPL: [receipt]})
            before = pending_path.read_text(encoding="utf-8")

            result = run_post_lineup_refresh(
                root=tmp,
                now=NOW,
                newly_confirmed={EPL: [receipt]},
                live=True,
                env=_env(),
                quota_ledger=ledger,
                acceptance_report=_acceptance(EPL),
                identity_registry=_registry(EPL),
                refresh_fn=_fail,
                publish_fn=_fail,
            )

            assert result["status"] == "blocked"
            assert result["acks"]["durable"] == []
            assert result["acks"]["retryable"] == []
            assert result["acks"]["blocked"] == [{
                "ack_key": receipt["ack_key"],
                "reason": reason,
            }]
            assert pending_path.read_text(encoding="utf-8") == before


def test_available_next_key_coalesces_same_competition_and_acks_after_hmac_publish():
    first = _receipt(EPL, "epl-1", "a")
    second = _receipt(EPL, "epl-2", "b", "2026-08-24T13:15:00+00:00")
    fetch_calls = []
    published = []

    def fetch(sport_key, selected_env):
        fetch_calls.append((sport_key, sorted(selected_env)))
        return [{"id": "epl-1"}, {"id": "epl-2"}]

    def publish(snapshot):
        request = build_ingest_request(
            snapshot,
            endpoint="https://example.invalid/api/ingest/snapshot",
            secret="h" * 32,
            timestamp=NOW,
        )
        published.append((snapshot, request))
        return {"status": "stored"}

    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL)
        pending_path = _write_task4_pending(tmp, {EPL: [first, second]})
        result = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [first, second]},
            live=True,
            env=_env(two=True),
            quota_ledger=_quota(theoddsapi_primary=10, theoddsapi_secondary=100),
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            odds_fetcher=fetch,
            snapshot_builder=_snapshot_builder,
            publish_fn=publish,
        )

        assert result["status"] == "published"
        assert fetch_calls == [("soccer_epl", ["THE_ODDS_API_KEY_SECONDARY"])]
        assert _ack_events(result, "durable") == ["epl-1", "epl-2"]
        assert result["acks"]["retryable"] == []
        assert result["acks"]["blocked"] == []
        assert len(published) == 1
        aggregate, request = published[0]
        assert aggregate["run"]["run_id"].startswith("league-aggregate-")
        assert request["headers"]["X-Worldcup-Run-Id"] == aggregate["run"]["run_id"]
        assert json.loads(pending_path.read_text(encoding="utf-8"))["events"] == {}
        state = json.loads(
            (Path(tmp) / "data/local/leagues/post_lineup_refresh_state.json").read_text(
                encoding="utf-8"
            )
        )
        assert all(row["phase"] == "published" for row in state["receipts"].values())


def test_started_receipt_is_blocked_and_never_reads_env_or_quota():
    receipt = _receipt(EPL, "epl-started", "c", "2026-08-24T11:59:00+00:00")
    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL)
        pending_path = _write_task4_pending(tmp, {EPL: [receipt]})
        result = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            env_loader=_fail,
            quota_loader=_fail,
            refresh_fn=_fail,
            publish_fn=_fail,
        )

        assert result["status"] == "blocked"
        assert result["acks"]["blocked"] == [{
            "ack_key": receipt["ack_key"],
            "reason": "match_started",
        }]
        assert json.loads(pending_path.read_text(encoding="utf-8"))["events"]


def test_durable_same_fingerprint_never_repeats_quota_refresh_snapshot_or_publish():
    receipt = _receipt(EPL, "epl-1", "d")
    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL)
        _write_task4_pending(tmp, {EPL: [receipt]})
        first = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            env=_env(),
            quota_ledger=_quota(theoddsapi_primary=100),
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            odds_fetcher=lambda _sport, _env: [{"id": "epl-1"}],
            snapshot_builder=_snapshot_builder,
            publish_fn=lambda _snapshot: {"status": "duplicate"},
        )
        second = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            env_loader=_fail,
            quota_loader=_fail,
            refresh_fn=_fail,
            publish_fn=_fail,
        )

        assert first["status"] == "published"
        assert second["status"] == "already_acked"
        assert second["acks"]["durable"] == [{"ack_key": receipt["ack_key"]}]


def test_one_competition_failure_does_not_discard_other_committed_refresh():
    epl = _receipt(EPL, "epl-1", "e")
    laliga = _receipt(LALIGA, "laliga-1", "f")
    fetch_calls = []

    def fetch(sport_key, _selected_env):
        fetch_calls.append(sport_key)
        if sport_key == "soccer_spain_la_liga":
            raise RuntimeError("provider detail must remain private")
        return [{"id": "epl-1"}]

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_acceptance(root, EPL, LALIGA)
        _write_task4_pending(root, {EPL: [epl], LALIGA: [laliga]})
        old_laliga = root / f"data/cache/leagues/{LALIGA}/snapshot.json"
        old_laliga.parent.mkdir(parents=True, exist_ok=True)
        old_laliga.write_text(json.dumps({
            "snapshot_id": "laliga-old",
            "snapshot_at": "2026-08-24T11:00:00+00:00",
            "competition": {"id": LALIGA},
            "matches": [{
                "source_event_id": "laliga-old-event",
                "competition": {"id": LALIGA},
            }],
        }), encoding="utf-8")
        result = run_post_lineup_refresh(
            root=root,
            now=NOW,
            newly_confirmed={EPL: [epl], LALIGA: [laliga]},
            live=True,
            env=_env(),
            quota_ledger=_quota(theoddsapi_primary=100),
            acceptance_report=_acceptance(EPL, LALIGA),
            identity_registry=_registry(EPL, LALIGA),
            odds_fetcher=fetch,
            snapshot_builder=_snapshot_builder,
            publish_fn=lambda _snapshot: {"status": "stored"},
        )

        assert result["status"] == "partial"
        assert fetch_calls == ["soccer_epl", "soccer_spain_la_liga"]
        assert _ack_events(result, "durable") == ["epl-1"]
        assert result["acks"]["retryable"] == [{
            "ack_key": laliga["ack_key"],
            "reason": "refresh_failed",
        }]
        pending = json.loads(
            (root / "data/local/leagues/lineup_refresh_pending.json").read_text(
                encoding="utf-8"
            )
        )
        assert list(pending["events"]) == [f"{LALIGA}:laliga-1"]
        assert "private" not in json.dumps(result, ensure_ascii=False)


def test_commit_or_publish_failure_never_removes_task4_pending_or_returns_durable_ack():
    receipt = _receipt(EPL, "epl-1", "1")

    class FailingStore:
        def __init__(self, _root):
            pass

        def commit_snapshot(self, _competition_id, _snapshot):
            raise OSError("private commit path")

    for store_factory, publish_fn, expected_reason in (
        (FailingStore, _fail, "refresh_failed"),
        (None, lambda _snapshot: {"status": "error"}, "publish_failed"),
    ):
        with TemporaryDirectory() as tmp:
            _write_acceptance(tmp, EPL)
            pending_path = _write_task4_pending(tmp, {EPL: [receipt]})
            kwargs = {}
            if store_factory is not None:
                kwargs["live_store_factory"] = store_factory
            result = run_post_lineup_refresh(
                root=tmp,
                now=NOW,
                newly_confirmed={EPL: [receipt]},
                live=True,
                env=_env(),
                quota_ledger=_quota(theoddsapi_primary=100),
                acceptance_report=_acceptance(EPL),
                identity_registry=_registry(EPL),
                odds_fetcher=lambda _sport, _env: [{"id": "epl-1"}],
                snapshot_builder=_snapshot_builder,
                publish_fn=publish_fn,
                **kwargs,
            )

            assert result["acks"]["durable"] == []
            assert result["acks"]["retryable"] == [{
                "ack_key": receipt["ack_key"],
                "reason": expected_reason,
            }]
            assert json.loads(pending_path.read_text(encoding="utf-8"))["events"]
            assert "private" not in json.dumps(result, ensure_ascii=False)


def test_publish_retry_reuses_committed_snapshot_without_second_provider_or_quota_call():
    receipt = _receipt(EPL, "epl-1", "2")
    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL)
        _write_task4_pending(tmp, {EPL: [receipt]})
        first = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            env=_env(),
            quota_ledger=_quota(theoddsapi_primary=100),
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            odds_fetcher=lambda _sport, _env: [{"id": "epl-1"}],
            snapshot_builder=_snapshot_builder,
            publish_fn=lambda _snapshot: {"status": "error"},
        )
        second = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            env_loader=_fail,
            quota_loader=_fail,
            refresh_fn=_fail,
            publish_fn=lambda _snapshot: {"status": "stored"},
        )

        assert first["acks"]["retryable"] == [{
            "ack_key": receipt["ack_key"],
            "reason": "publish_failed",
        }]
        assert second["status"] == "published"
        assert second["acks"]["durable"] == [{"ack_key": receipt["ack_key"]}]


def test_missing_active_partition_preserves_pending_and_never_calls_publisher():
    receipt = _receipt(EPL, "epl-1", "3")
    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL, LALIGA)
        pending_path = _write_task4_pending(tmp, {EPL: [receipt]})
        result = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            env=_env(),
            quota_ledger=_quota(theoddsapi_primary=100),
            acceptance_report=_acceptance(EPL, LALIGA),
            identity_registry=_registry(EPL, LALIGA),
            odds_fetcher=lambda _sport, _env: [{"id": "epl-1"}],
            snapshot_builder=_snapshot_builder,
            publish_fn=_fail,
        )

        assert result["status"] == "publish_failed"
        assert result["acks"]["durable"] == []
        assert result["acks"]["retryable"] == [{
            "ack_key": receipt["ack_key"],
            "reason": "publish_failed",
        }]
        assert json.loads(pending_path.read_text(encoding="utf-8"))["events"]


def test_published_ingest_without_durable_ack_state_keeps_task4_pending():
    receipt = _receipt(EPL, "epl-1", "4")

    class AckFailingStateStore:
        def __init__(self, root):
            from worldcup.league_post_lineup_refresh import PostLineupRefreshStateStore

            self.delegate = PostLineupRefreshStateStore(root)
            self.commits = 0

        def read(self):
            return self.delegate.read()

        def commit(self, state):
            self.commits += 1
            if self.commits == 2:
                raise OSError("private ack state path")
            return self.delegate.commit(state)

    stores = []

    def store_factory(root):
        store = AckFailingStateStore(root)
        stores.append(store)
        return store

    with TemporaryDirectory() as tmp:
        _write_acceptance(tmp, EPL)
        pending_path = _write_task4_pending(tmp, {EPL: [receipt]})
        result = run_post_lineup_refresh(
            root=tmp,
            now=NOW,
            newly_confirmed={EPL: [receipt]},
            live=True,
            env=_env(),
            quota_ledger=_quota(theoddsapi_primary=100),
            acceptance_report=_acceptance(EPL),
            identity_registry=_registry(EPL),
            odds_fetcher=lambda _sport, _env: [{"id": "epl-1"}],
            snapshot_builder=_snapshot_builder,
            publish_fn=lambda _snapshot: {"status": "stored"},
            state_store_factory=store_factory,
        )

        assert stores[0].commits == 2
        assert result["status"] == "publish_failed"
        assert result["acks"]["durable"] == []
        assert result["acks"]["retryable"] == [{
            "ack_key": receipt["ack_key"],
            "reason": "ack_state_commit_failed",
        }]
        assert json.loads(pending_path.read_text(encoding="utf-8"))["events"]
        assert "private" not in json.dumps(result, ensure_ascii=False)
