import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup import league_scheduled_publish


def _component(hour=12, ident="a"):
    return {"competition": {"id": "epl_2026_27"}, "snapshot_id": ident,
            "snapshot_at": f"2026-09-01T{hour:02}:00:00+00:00",
            "matches": [{"source_event_id": "event", "competition": {"id": "epl_2026_27"},
                         "home_team": "Home", "away_team": "Away"}]}


def _aggregate(hour=12, ident="a"):
    from worldcup.league_publication import build_publication_vector
    component = _component(hour, ident)
    return {"snapshot_id": ident, "snapshot_at": component["snapshot_at"],
            "run": {"run_id": ident}, "competition": {"id": "multi_league"},
            "components": [{"competition_id": "epl_2026_27", "snapshot_id": ident}],
            "matches": component["matches"],
            "league_publication": {"schema_version": 1, "components": build_publication_vector([component])}}


def _raises(reason, fn):
    try:
        fn()
    except ValueError as exc:
        assert str(exc) == reason, str(exc)
    else:
        raise AssertionError(reason)


def test_component_vector_rejects_regression_conflict_missing_and_bad_time():
    from worldcup.league_publication import build_publication_vector, validate_component_vector
    current = build_publication_vector([_component()])
    validate_component_vector(current, current)
    _raises("league_component_regression", lambda: validate_component_vector(current, build_publication_vector([_component(11)])))
    _raises("league_component_conflict", lambda: validate_component_vector(current, build_publication_vector([_component(12, "b")])))
    _raises("league_component_regression", lambda: validate_component_vector(current, {}))
    bad = _component(); bad["snapshot_at"] = "2026-09-01T12:00:00"
    _raises("league_publication_invalid", lambda: build_publication_vector([bad]))


def test_component_hash_uses_exact_safe_four_fields_and_sorted_matches():
    from worldcup.league_publication import build_publication_vector, project_publication_match
    component = _component()
    component["matches"][0]["provider"] = {"secret": "NEVER"}
    component["matches"][0]["match_decision"] = {"label": "MATCH_PICK", "headers": {"secret": "NEVER"}}
    safe = project_publication_match(component["matches"][0])
    assert "NEVER" not in json.dumps(safe)
    raw = {"competition_id": "epl_2026_27", "snapshot_id": "a", "snapshot_at": component["snapshot_at"], "matches": [safe]}
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert build_publication_vector([component])["odds:epl_2026_27"]["content_sha256"] == digest


def _ingest(db, snapshot, timestamp="2026-09-01T14:00:00+00:00", store=None):
    from worldcup.ingest import build_ingest_request
    from worldcup.ingest_app import process_local_ingest
    request = build_ingest_request(snapshot, "https://example.invalid/api/ingest/snapshot", "x" * 32, timestamp)
    return process_local_ingest(db, request["method"], request["path"], request["headers"], request["body"], "x" * 32, now=timestamp, store=store)


def test_real_hmac_sqlite_delayed_requests_and_tampered_hash_rejected():
    from worldcup.store import SQLiteSnapshotStore
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"store.db"
        assert _ingest(db, _aggregate())["status"] == "stored"
        assert _ingest(db, _aggregate(13, "b"))["status"] == "stored"
        assert _ingest(db, _aggregate())["status"] == "duplicate"
        late = _aggregate(); late["run"]["run_id"] = "late"
        assert _ingest(db, late) == {"status": "rejected", "reason": "league_component_regression"}
        corrupt = _aggregate(14, "c"); corrupt["matches"][0]["home_team"] = "Tampered"
        assert _ingest(db, corrupt)["status"] == "rejected"
        store = SQLiteSnapshotStore(db)
        assert store.count_snapshots() == 2
        assert store.latest_snapshot()["snapshot"]["snapshot_id"] == "b"


def test_server_rejects_newer_odds_component_that_drops_existing_event_membership():
    from worldcup.league_publication import build_publication_vector
    from worldcup.store import SQLiteSnapshotStore
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "store.db"
        previous = _aggregate()
        retained = copy.deepcopy(previous["matches"][0])
        retained["source_event_id"] = "retained-event"
        retained["home_team"] = "Liverpool"
        retained["away_team"] = "Manchester City"
        previous["matches"].append(retained)
        previous_component = _component()
        previous_component["matches"] = previous["matches"]
        previous["league_publication"]["components"] = build_publication_vector(
            [previous_component]
        )
        assert _ingest(db, previous)["status"] == "stored"

        missing = _aggregate(13, "newer-but-partial")
        assert _ingest(db, missing) == {
            "status": "rejected",
            "reason": "league_component_regression",
        }
        latest = SQLiteSnapshotStore(db).latest_snapshot()["snapshot"]
        assert {row["source_event_id"] for row in latest["matches"]} == {
            "event", "retained-event"
        }


def test_server_rejects_newer_odds_component_that_changes_event_identity():
    from worldcup.league_publication import build_publication_vector
    from worldcup.store import SQLiteSnapshotStore
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "store.db"
        previous = _aggregate()
        previous["matches"][0].update({
            "kickoff_at_utc": "2026-09-01T18:00:00+00:00",
            "home_canonical": "arsenal",
            "away_canonical": "chelsea",
        })
        previous_component = _component()
        previous_component["matches"] = previous["matches"]
        previous["league_publication"]["components"] = build_publication_vector(
            [previous_component]
        )
        assert _ingest(db, previous)["status"] == "stored"

        changed = copy.deepcopy(previous)
        changed["snapshot_id"] = "identity-changed"
        changed["snapshot_at"] = "2026-09-01T13:00:00+00:00"
        changed["run"]["run_id"] = "identity-changed"
        changed["matches"][0]["away_canonical"] = "liverpool"
        changed_component = _component(13, "identity-changed")
        changed_component["matches"] = changed["matches"]
        changed["league_publication"]["components"] = build_publication_vector(
            [changed_component]
        )

        assert _ingest(db, changed) == {
            "status": "rejected",
            "reason": "league_component_conflict",
        }
        latest = SQLiteSnapshotStore(db).latest_snapshot()["snapshot"]
        assert latest["matches"][0]["away_canonical"] == "chelsea"


def test_outbox_freezes_body_retries_and_binds_endpoint():
    from worldcup.league_publication import deliver_league_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp); sent = []
        def send(*, payload, endpoint, timestamp):
            state = json.loads((root/"data/local/leagues/publication_state.json").read_text())
            assert state["pending"]["body"] == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            sent.append((copy.deepcopy(payload), timestamp))
            return {"status": "error"} if len(sent) == 1 else {"status": "stored"}
        kwargs = dict(root=root, endpoint="https://example.invalid/ingest", snapshot=_aggregate(), publish_fn=send)
        assert deliver_league_publication(**kwargs, now="2026-09-01T14:00:00Z")["status"] == "pending"
        mismatch = dict(kwargs, endpoint="https://other.invalid/ingest")
        assert deliver_league_publication(**mismatch, now="2026-09-01T14:01:00Z")["status"] == "rejected"
        assert deliver_league_publication(**kwargs, now="2026-09-01T14:02:00Z")["status"] == "stored"
        assert sent[0][0] == sent[1][0]
        assert sent[0][1] != sent[1][1]
        state = json.loads((root/"data/local/leagues/publication_state.json").read_text())
        assert state["pending"] is None and state["sent"]["status"] == "stored"


def test_projection_preserves_actual_query_contract_without_nested_diagnostics():
    from worldcup.league_publication import project_publication_match
    from worldcup.query import project_match_rows
    from datetime import datetime, timezone
    match = _component()["matches"][0]
    match["competition"].update({"label": "英超", "rating_policy": "club_rating_pending", "headers": {"token": "NEVER"}})
    match["refresh_plan"] = {"next_update_at": "2026-09-01T15:00:00Z", "label": "更新", "description": "稍后刷新", "provider": {"token": "NEVER"}}
    match["match_decision"] = {"schema_version": 2, "policy_version": "match_pick_v3", "label": "MATCH_PICK", "market": "1X2", "selection": "home", "valid_until": "2026-09-01T16:00:00Z", "odds": 1.8, "p_hit_safe": .6}
    safe = project_publication_match(match)
    now = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    assert project_match_rows({"matches": [match]}, as_of=now) == project_match_rows({"matches": [safe]}, as_of=now)
    assert "NEVER" not in json.dumps(safe)


def test_sqlite_concurrent_components_cannot_overwrite_newer_or_lose_membership():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from worldcup.store import SQLiteSnapshotStore
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"store.db"; store = SQLiteSnapshotStore(db); store.initialize()
        assert _ingest(db, _aggregate())["status"] == "stored"
        barrier = Barrier(2)
        def send(item):
            barrier.wait()
            return _ingest(db, item)
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(send, [_aggregate(13, "b"), _aggregate(14, "c")]))
        assert all(row["status"] in {"stored", "rejected"} for row in results)
        assert store.latest_snapshot()["snapshot"]["snapshot_id"] == "c"
        missing = _aggregate(15, "missing")
        missing["league_publication"]["components"] = {}; missing["matches"] = []
        assert _ingest(db, missing)["status"] == "rejected"


def test_sqlite_conservative_legacy_upgrade_and_old_writer_cutoff():
    from worldcup.store import SQLiteSnapshotStore
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"store.db"
        legacy = _aggregate(); legacy.pop("league_publication")
        assert _ingest(db, legacy)["status"] == "stored"
        conflict = _aggregate(12, "changed")
        assert _ingest(db, conflict) == {"status": "rejected", "reason": "league_publication_migration_required"}
        assert _ingest(db, _aggregate())["status"] == "stored"
        legacy["run"]["run_id"] = "old-writer"
        assert _ingest(db, legacy) == {"status": "rejected", "reason": "league_publication_contract_required"}
        for cid in ("fifa_world_cup_2026", "csl_2026"):
            other = {"run": {"run_id": cid}, "competition": {"id": cid}, "matches": []}
            assert _ingest(db, other)["status"] == "stored"


def test_unsupported_store_never_accepts_new_publication():
    class Unsupported:
        def put_snapshot(self, **kwargs):
            raise AssertionError("unsupported store cannot write")
    assert _ingest("unused", _aggregate(), store=Unsupported()) == {"status": "rejected", "reason": "league_publication_unsupported"}


def test_frozen_signer_only_changes_timestamp_and_signature():
    from worldcup.ingest import build_ingest_payload, build_frozen_ingest_request
    payload = build_ingest_payload(_aggregate(), "2026-09-01T14:00:00Z")
    first = build_frozen_ingest_request(payload, "https://example.invalid/ingest", "x"*32, "2026-09-01T14:00:00Z")
    second = build_frozen_ingest_request(payload, "https://example.invalid/ingest", "x"*32, "2026-09-01T14:01:00Z")
    assert first["body"] == second["body"]
    assert first["headers"]["X-Worldcup-Idempotency-Key"] == second["headers"]["X-Worldcup-Idempotency-Key"]
    assert first["headers"]["X-Worldcup-Signature"] != second["headers"]["X-Worldcup-Signature"]


def test_receipt_write_failure_retains_pending_then_server_returns_duplicate():
    from unittest.mock import patch
    from worldcup import league_publication as publication
    from worldcup.ingest import build_frozen_ingest_request
    from worldcup.ingest_app import process_local_ingest
    with TemporaryDirectory() as tmp:
        root = Path(tmp); writes = []; replies = []
        real_write = publication._write_state
        def write(path, state):
            writes.append(state["sent"])
            if len(writes) == 2:
                raise OSError("disk full")
            real_write(path, state)
        def send(*, payload, endpoint, timestamp):
            request = build_frozen_ingest_request(payload, endpoint, "x"*32, timestamp)
            reply = process_local_ingest(root/"db.sqlite", request["method"], request["path"], request["headers"], request["body"], "x"*32, now=timestamp)
            replies.append(reply["status"]); return reply
        kwargs = dict(root=root, endpoint="https://example.invalid/ingest", snapshot=_aggregate(), publish_fn=send, now="2026-09-01T14:00:00Z")
        with patch.object(publication, "_write_state", write):
            try: publication.deliver_league_publication(**kwargs)
            except OSError: pass
            else: raise AssertionError("receipt write must fail")
        assert json.loads((root/"data/local/leagues/publication_state.json").read_text())["pending"]
        assert publication.deliver_league_publication(**dict(kwargs, snapshot=None, now="2026-09-01T14:02:00Z"))["status"] == "duplicate"
        assert replies == ["stored", "duplicate"]


def test_server_stale_pending_is_audited_before_retirement():
    from worldcup.league_publication import deliver_league_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        reply = deliver_league_publication(root=root, endpoint="https://example.invalid/ingest", snapshot=_aggregate(), now="2026-09-01T14:00:00Z", publish_fn=lambda **kw: {"status": "rejected", "reason": "league_component_regression"})
        assert reply == {"status": "rejected", "reason": "league_component_regression"}
        state = json.loads((root/"data/local/leagues/publication_state.json").read_text())
        assert state["pending"] is None
        assert state["superseded"][0]["pending"]["payload"]["snapshot"]["snapshot_id"] == "a"
        assert state["components"] == {}


def test_pending_fingerprint_corruption_blocks_send():
    from worldcup.league_publication import deliver_league_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        kwargs = dict(root=root, endpoint="https://example.invalid/ingest", snapshot=_aggregate(), now="2026-09-01T14:00:00Z")
        deliver_league_publication(**kwargs, publish_fn=lambda **kw: {"status": "error"})
        path = root/"data/local/leagues/publication_state.json"
        state = json.loads(path.read_text()); state["pending"]["accepted_fingerprint"] = "corrupt"
        path.write_text(json.dumps(state))
        def forbidden(**kw): raise AssertionError("corrupt pending must not send")
        assert deliver_league_publication(**kwargs, publish_fn=forbidden)["status"] == "rejected"


def test_wrong_receipt_identity_never_clears_pending_or_records_success():
    from worldcup.league_publication import deliver_league_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = deliver_league_publication(root=root, endpoint="https://example.invalid/ingest", snapshot=_aggregate(), now="2026-09-01T14:00:00Z", publish_fn=lambda **kw: {"status": "stored", "snapshot_id": "other"})
        assert result == {"status": "rejected", "reason": "league_publication_receipt_mismatch"}
        state = json.loads((root/"data/local/leagues/publication_state.json").read_text())
        assert state["pending"] and state["sent"] is None


def test_legacy_incomplete_membership_cannot_be_silently_migrated():
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"db.sqlite"
        old = _aggregate(); old.pop("league_publication")
        old["matches"].append({"competition": {"id": "laliga_2026_27"}, "source_event_id": "es"})
        assert _ingest(db, old)["status"] == "stored"
        assert _ingest(db, _aggregate(13, "new")) == {"status": "rejected", "reason": "league_publication_migration_required"}


def test_transaction_order_survives_earlier_request_clock_in_public_query():
    from worldcup.store import SQLiteSnapshotStore
    from worldcup.league_publication import build_publication_vector
    from worldcup.query import load_latest_snapshot_view, project_match_rows
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"db.sqlite"
        old = _aggregate()
        new = _aggregate(13, "b")
        new["matches"][0]["home_team"] = "New Home"
        part = _component(13, "b"); part["matches"] = new["matches"]
        new["league_publication"]["components"] = build_publication_vector([part])
        assert _ingest(db, old, timestamp="2026-09-01T14:00:01Z")["status"] == "stored"
        assert _ingest(db, new, timestamp="2026-09-01T14:00:00Z")["status"] == "stored"
        view = load_latest_snapshot_view(db)
        assert project_match_rows(view)[0]["home_team"] == "New Home"
        store = SQLiteSnapshotStore(db)
        assert store.latest_snapshot()["snapshot"]["snapshot_id"] == "b"
        assert store.list_recent_snapshots()[0]["snapshot"]["snapshot_id"] == "b"
        assert store.list_latest_snapshots_by_competition(["multi_league"])[0]["snapshot"]["snapshot_id"] == "b"


def test_signed_acceptance_invalid_containers_return_safe_rejection():
    from worldcup.store import SQLiteSnapshotStore
    invalid = [[], "invalid", 1, {"competitions": []}, {"competitions": None}, {"competitions": "invalid"}]
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"db.sqlite"
        for acceptance in invalid:
            snapshot = _aggregate(); snapshot["league_acceptance"] = acceptance
            assert _ingest(db, snapshot) == {"status": "rejected", "reason": "league_publication_invalid"}
        assert SQLiteSnapshotStore(db).count_snapshots() == 0


def test_upgrade_clamps_against_all_historical_league_metadata_only():
    from worldcup.store import SQLiteSnapshotStore
    from worldcup.query import load_latest_snapshot_view
    with TemporaryDirectory() as tmp:
        db = Path(tmp)/"db.sqlite"
        first = _aggregate(); first.pop("league_publication")
        second = _aggregate(13, "b"); second.pop("league_publication")
        assert _ingest(db, first, timestamp="2026-09-01T15:00:00Z")["status"] == "stored"
        assert _ingest(db, second, timestamp="2026-09-01T14:00:00Z")["status"] == "stored"
        assert _ingest(db, _aggregate(14, "c"), timestamp="2026-09-01T14:01:00Z")["status"] == "stored"
        store = SQLiteSnapshotStore(db)
        latest = store.latest_snapshot()
        assert latest["snapshot"]["snapshot_id"] == "c"
        assert latest["stored_at"] == "2026-09-01T15:00:00Z"
        assert load_latest_snapshot_view(db)["snapshot_id"] == "c"
        for cid in ("fifa_world_cup_2026", "csl_2026"):
            other = {"run": {"run_id": cid}, "competition": {"id": cid}, "matches": []}
            assert _ingest(db, other, timestamp="2026-09-01T14:02:00Z")["status"] == "stored"
            assert store.list_latest_snapshots_by_competition([cid])[0]["stored_at"] == "2026-09-01T14:02:00Z"
def test_vector_malformed_containers_raise_safe_value_error():
    from worldcup.league_publication import build_publication_vector
    values = [None, [], {'competition': []}, {'competition': {'id': 'epl_2026_27'},
        'matches': [{'competition': []}]}]
    for value in values:
        try:
            build_publication_vector([value])
        except ValueError as exc:
            assert str(exc) == 'league_publication_invalid'
        else:
            raise AssertionError('invalid container accepted')
