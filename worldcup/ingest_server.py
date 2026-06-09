from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from worldcup.ingest import canonical_json

DEFAULT_REPLAY_WINDOW_SECONDS = 300


@dataclass(frozen=True)
class IngestVerification:
    ok: bool
    reason: str
    run_id: str | None = None
    snapshot_id: str | None = None
    idempotency_key: str | None = None
    body_sha256: str | None = None
    payload: dict[str, Any] | None = None


class InMemoryIngestStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    @property
    def count(self) -> int:
        return len(self._items)

    def has(self, key: str) -> bool:
        return key in self._items

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self._items[key] = payload


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Expected timezone-aware datetime: {value}")
    return parsed.astimezone(timezone.utc)


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _reject(reason: str) -> IngestVerification:
    return IngestVerification(ok=False, reason=reason)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signature_message(
    timestamp: str,
    method: str,
    path: str,
    run_id: str,
    snapshot_id: str,
    body_sha256: str,
) -> str:
    return "\n".join([timestamp, method.upper(), path, run_id, snapshot_id, body_sha256])


def verify_ingest_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    secret: str,
    now: str | None = None,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> IngestVerification:
    if not secret:
        return _reject("missing_secret")

    normalized = _normalize_headers(headers)
    required = {
        "x-worldcup-timestamp",
        "x-worldcup-run-id",
        "x-worldcup-snapshot-id",
        "x-worldcup-body-sha256",
        "x-worldcup-signature",
        "x-worldcup-idempotency-key",
    }
    missing = sorted(required - set(normalized))
    if missing:
        return _reject(f"missing_header:{missing[0]}")

    timestamp = normalized["x-worldcup-timestamp"]
    try:
        observed_at = _parse_utc(timestamp)
        current = _parse_utc(now) if now else _now_utc()
    except ValueError:
        return _reject("invalid_timestamp")

    if abs((current - observed_at).total_seconds()) > replay_window_seconds:
        return _reject("timestamp_out_of_window")

    body_sha256 = _sha256_text(body)
    if not hmac.compare_digest(body_sha256, normalized["x-worldcup-body-sha256"]):
        return _reject("body_hash_mismatch")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return _reject("invalid_json")

    run_id = payload.get("run_id")
    snapshot_id = payload.get("snapshot_id")
    if not run_id or not snapshot_id:
        return _reject("missing_payload_identity")

    if run_id != normalized["x-worldcup-run-id"]:
        return _reject("run_id_mismatch")
    if snapshot_id != normalized["x-worldcup-snapshot-id"]:
        return _reject("snapshot_id_mismatch")
    if normalized["x-worldcup-idempotency-key"] != f"{run_id}:{snapshot_id}":
        return _reject("idempotency_key_mismatch")

    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        return _reject("missing_snapshot")
    expected_snapshot_id = _sha256_text(canonical_json(snapshot))
    if not hmac.compare_digest(snapshot_id, expected_snapshot_id):
        return _reject("snapshot_id_invalid")

    message = _signature_message(
        timestamp=timestamp,
        method=method,
        path=path,
        run_id=run_id,
        snapshot_id=snapshot_id,
        body_sha256=body_sha256,
    )
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    signature = normalized["x-worldcup-signature"]
    if not signature.startswith("sha256="):
        return _reject("signature_format_invalid")
    if not hmac.compare_digest(signature, f"sha256={expected}"):
        return _reject("signature_mismatch")

    return IngestVerification(
        ok=True,
        reason="ok",
        run_id=run_id,
        snapshot_id=snapshot_id,
        idempotency_key=normalized["x-worldcup-idempotency-key"],
        body_sha256=body_sha256,
        payload=payload,
    )


def process_ingest_request(
    store: InMemoryIngestStore,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    secret: str,
    now: str | None = None,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
) -> dict[str, Any]:
    verification = verify_ingest_request(
        method=method,
        path=path,
        headers=headers,
        body=body,
        secret=secret,
        now=now,
        replay_window_seconds=replay_window_seconds,
    )
    if not verification.ok:
        return {
            "status": "rejected",
            "reason": verification.reason,
        }

    assert verification.idempotency_key is not None
    assert verification.payload is not None
    if store.has(verification.idempotency_key):
        return {
            "status": "duplicate",
            "run_id": verification.run_id,
            "snapshot_id": verification.snapshot_id,
            "idempotency_key": verification.idempotency_key,
        }

    store.put(verification.idempotency_key, verification.payload)
    return {
        "status": "stored",
        "run_id": verification.run_id,
        "snapshot_id": verification.snapshot_id,
        "idempotency_key": verification.idempotency_key,
    }
