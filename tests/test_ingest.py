import hashlib
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ingest import (
    build_ingest_dry_run,
    build_ingest_payload,
    build_ingest_request,
    canonical_json,
)


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


def test_build_ingest_payload_derives_stable_snapshot_id_from_snapshot_body():
    snapshot = _snapshot()

    payload = build_ingest_payload(snapshot, generated_at="2026-06-08T00:01:00+00:00")

    assert payload["schema_version"] == 1
    assert payload["run_id"] == "20260608T000000Z-live"
    assert payload["snapshot_at"] == "2026-06-08T00:00:00+00:00"
    assert payload["generated_at"] == "2026-06-08T00:01:00+00:00"
    assert payload["snapshot"] == snapshot
    assert payload["snapshot_id"] == hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()


def test_build_ingest_request_signs_timestamp_run_id_snapshot_id_and_body_hash():
    snapshot = _snapshot()
    secret = "test-hmac-secret"

    request = build_ingest_request(
        snapshot=snapshot,
        endpoint="https://example.com/api/ingest/snapshot",
        secret=secret,
        timestamp="2026-06-08T00:02:00+00:00",
    )

    body_hash = hashlib.sha256(request["body"].encode()).hexdigest()
    payload = json.loads(request["body"])
    message = "\n".join(
        [
            "2026-06-08T00:02:00+00:00",
            "POST",
            "/api/ingest/snapshot",
            payload["run_id"],
            payload["snapshot_id"],
            body_hash,
        ]
    )
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    assert request["method"] == "POST"
    assert request["path"] == "/api/ingest/snapshot"
    assert request["headers"]["X-Worldcup-Timestamp"] == "2026-06-08T00:02:00+00:00"
    assert request["headers"]["X-Worldcup-Run-Id"] == payload["run_id"]
    assert request["headers"]["X-Worldcup-Snapshot-Id"] == payload["snapshot_id"]
    assert request["headers"]["X-Worldcup-Body-SHA256"] == body_hash
    assert request["headers"]["X-Worldcup-Signature"] == f"sha256={expected}"
    assert request["headers"]["X-Worldcup-Idempotency-Key"] == (
        f"{payload['run_id']}:{payload['snapshot_id']}"
    )


def test_ingest_dry_run_omits_body_by_default_and_never_exposes_secret():
    with TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "snapshot.json"
        snapshot_path.write_text(json.dumps(_snapshot()), encoding="utf-8")

        result = build_ingest_dry_run(
            snapshot_path=snapshot_path,
            endpoint="https://example.com/api/ingest/snapshot",
            secret="very-secret-value",
            timestamp="2026-06-08T00:02:00+00:00",
        )

        serialized = json.dumps(result)
        assert result["status"] == "dry_run"
        assert result["request"]["body"] is None
        assert result["request"]["body_bytes"] > 0
        assert result["request"]["body_sha256"]
        assert "very-secret-value" not in serialized
