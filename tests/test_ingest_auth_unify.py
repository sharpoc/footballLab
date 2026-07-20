"""Tests for ingest authentication error unification and timestamp window.

Red-light: under old implementation, two signature failures produce DIFFERENT
response bodies (leaking the verification stage). After fix, they must be
identical and contain no internal detail.

Also locks the existing timestamp window semantics without changing them.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from worldcup.ingest import build_ingest_request, canonical_json
from worldcup.http_app import handle_request


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


def _valid_request(secret="test-hmac-secret", timestamp="2026-06-08T00:02:00+00:00"):
    return build_ingest_request(
        snapshot=_snapshot(),
        endpoint="https://example.com/api/ingest/snapshot",
        secret=secret,
        timestamp=timestamp,
    )


def _ingest(headers, body, secret="test-hmac-secret", now="2026-06-08T00:03:00+00:00"):
    return handle_request(
        method="POST",
        path="/api/ingest/snapshot",
        headers={"Content-Type": "application/json", **headers},
        body=body,
        db_path="/tmp/test_auth_unify.db",
        secret=secret,
        now=now,
    )


# --- Red-light: two auth failures must produce IDENTICAL response body ---

def test_signature_format_and_mismatch_produce_identical_401_body():
    """Both signature failure modes must return the same body (no stage leak)."""
    request = _valid_request()
    body = request["body"]

    # Case 1: bad format (no sha256= prefix)
    headers_bad_format = dict(request["headers"])
    headers_bad_format["X-Worldcup-Signature"] = "bad-no-prefix"
    resp_format = _ingest(headers_bad_format, body)

    # Case 2: valid format but wrong signature
    headers_bad_sig = dict(request["headers"])
    headers_bad_sig["X-Worldcup-Signature"] = "sha256=0000000000000000000000000000000000000000000000000000000000000000"
    resp_mismatch = _ingest(headers_bad_sig, body)

    assert resp_format["status"] == 401
    assert resp_mismatch["status"] == 401
    # Error code must be identical (no stage differentiation);
    # request_id is per-request random so compare structure without it.
    parsed_format = json.loads(resp_format["body"])
    parsed_mismatch = json.loads(resp_mismatch["body"])
    assert parsed_format["error"]["code"] == parsed_mismatch["error"]["code"]
    assert parsed_format["error"]["code"] == "authentication_failed"


def test_401_body_contains_authentication_failed_code():
    """The unified error code must be 'authentication_failed'."""
    request = _valid_request()
    headers = dict(request["headers"])
    headers["X-Worldcup-Signature"] = "sha256=wrong"
    resp = _ingest(headers, request["body"])

    assert resp["status"] == 401
    parsed = json.loads(resp["body"])
    assert parsed["error"]["code"] == "authentication_failed"


def test_401_body_does_not_leak_internal_details():
    """401 response must not contain: reason names, secret, paths, SQL, tracebacks."""
    request = _valid_request()
    headers = dict(request["headers"])
    headers["X-Worldcup-Signature"] = "sha256=wrong"
    resp = _ingest(headers, request["body"])

    body_text = resp["body"]
    for forbidden in [
        "signature_mismatch",
        "signature_format_invalid",
        "hmac",
        "secret",
        "sqlite",
        "Traceback",
        "File ",
        "/Users/",
        "/opt/",
        "/tmp/",
    ]:
        assert forbidden.lower() not in body_text.lower(), (
            f"Leaked '{forbidden}' in 401 body"
        )


# --- Timestamp window semantics (lock existing behavior, don't change) ---

def test_future_timestamp_within_window_accepted():
    """A timestamp 200s in the FUTURE is within 300s window → accepted."""
    # now = T+0, timestamp = T+200 (future but within window)
    now = "2026-06-08T00:00:00+00:00"
    future_ts = "2026-06-08T00:03:20+00:00"  # +200s
    request = _valid_request(timestamp=future_ts)
    resp = _ingest(request["headers"], request["body"], now=now)
    # Should succeed (200) because |200s| < 300s
    assert resp["status"] == 200, f"Expected 200, got {resp['status']}: {resp['body']}"


def test_future_timestamp_beyond_window_rejected():
    """A timestamp 400s in the FUTURE exceeds 300s window → rejected."""
    now = "2026-06-08T00:00:00+00:00"
    future_ts = "2026-06-08T00:06:40+00:00"  # +400s
    request = _valid_request(timestamp=future_ts)
    resp = _ingest(request["headers"], request["body"], now=now)
    assert resp["status"] == 400
    parsed = json.loads(resp["body"])
    assert parsed["error"]["code"] == "timestamp_out_of_window"


def test_past_timestamp_beyond_window_rejected():
    """A timestamp 400s in the PAST exceeds 300s window → rejected."""
    now = "2026-06-08T00:10:00+00:00"
    past_ts = "2026-06-08T00:03:20+00:00"  # -400s
    request = _valid_request(timestamp=past_ts)
    resp = _ingest(request["headers"], request["body"], now=now)
    assert resp["status"] == 400
    parsed = json.loads(resp["body"])
    assert parsed["error"]["code"] == "timestamp_out_of_window"


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import inspect

    failures = 0
    count = 0
    for name, fn in sorted(inspect.getmembers(sys.modules[__name__], inspect.isfunction)):
        if not name.startswith("test_"):
            continue
        count += 1
        try:
            fn()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"\n{count - failures}/{count} passed")
    raise SystemExit(1 if failures else 0)
