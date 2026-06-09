from worldcup.ingest import build_ingest_request
from worldcup.ingest_server import InMemoryIngestStore, process_ingest_request, verify_ingest_request


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "20260608T000000Z-live",
            "observed_at": "2026-06-08T00:00:00+00:00",
        },
        "counts": {"matches": 1},
        "matches": [{"home_team": "Mexico", "away_team": "South Africa"}],
    }


def _request(secret="test-hmac-secret"):
    return build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret=secret,
        timestamp="2026-06-08T00:02:00+00:00",
    )


def test_verify_ingest_request_accepts_signed_request():
    request = _request()

    result = verify_ingest_request(
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
    )

    assert result.ok is True
    assert result.reason == "ok"
    assert result.run_id == "20260608T000000Z-live"
    assert result.idempotency_key == request["headers"]["X-Worldcup-Idempotency-Key"]
    assert result.payload["snapshot"]["counts"]["matches"] == 1


def test_verify_ingest_request_rejects_tampered_body_hash():
    request = _request()

    result = verify_ingest_request(
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"].replace("Mexico", "Canada"),
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
    )

    assert result.ok is False
    assert result.reason == "body_hash_mismatch"


def test_verify_ingest_request_rejects_bad_signature():
    request = _request()
    headers = dict(request["headers"])
    headers["X-Worldcup-Signature"] = "sha256=bad"

    result = verify_ingest_request(
        method=request["method"],
        path=request["path"],
        headers=headers,
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
    )

    assert result.ok is False
    assert result.reason == "signature_mismatch"


def test_verify_ingest_request_rejects_timestamp_outside_replay_window():
    request = _request()

    result = verify_ingest_request(
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:20:00+00:00",
        replay_window_seconds=300,
    )

    assert result.ok is False
    assert result.reason == "timestamp_out_of_window"


def test_process_ingest_request_is_idempotent_by_header_key():
    store = InMemoryIngestStore()
    request = _request()

    first = process_ingest_request(
        store=store,
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:03:00+00:00",
    )
    second = process_ingest_request(
        store=store,
        method=request["method"],
        path=request["path"],
        headers=request["headers"],
        body=request["body"],
        secret="test-hmac-secret",
        now="2026-06-08T00:04:00+00:00",
    )

    assert first["status"] == "stored"
    assert second["status"] == "duplicate"
    assert store.count == 1
