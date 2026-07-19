from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PublishFn = Callable[..., dict[str, Any]]
_TRANSIENT_PUBLISH_ERRORS = (
    urllib.error.URLError,
    ssl.SSLError,
    TimeoutError,
    ConnectionError,
)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pending_publish_path(snapshot_path: str | Path) -> Path:
    snapshot = Path(snapshot_path)
    return snapshot.with_name(f"{snapshot.stem}.publish_pending.json")


def _snapshot_identity(snapshot_path: str | Path) -> dict[str, str]:
    path = Path(snapshot_path)
    raw = path.read_bytes()
    snapshot = json.loads(raw.decode("utf-8"))
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    run_id = str(run.get("run_id") or snapshot.get("snapshot_at") or "").strip()
    if not run_id:
        raise ValueError("snapshot run_id is missing")
    return {
        "run_id": run_id,
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _is_owned_publish_path(
    owner_snapshot_path: str | Path,
    publish_snapshot_path: str | Path,
    snapshot_sha256: str,
) -> bool:
    owner = Path(owner_snapshot_path).resolve(strict=False)
    publish = Path(publish_snapshot_path).resolve(strict=False)
    if publish == owner:
        return True
    return (
        publish.parent == owner.parent
        and publish.name
        == f".{owner.stem}.{snapshot_sha256[:20]}.prepared.json"
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def stage_pending_publish(
    snapshot_path: str | Path,
    *,
    state_path: str | Path | None = None,
    staged_at: str | None = None,
    endpoint: str | None = None,
    owner_snapshot_path: str | Path | None = None,
) -> dict[str, Any]:
    snapshot = Path(snapshot_path)
    state = Path(state_path) if state_path is not None else pending_publish_path(snapshot)
    identity = _snapshot_identity(snapshot)
    publish_path = snapshot.resolve(strict=False)
    owner = (
        Path(owner_snapshot_path).resolve(strict=False)
        if owner_snapshot_path is not None
        else publish_path
    )
    if not _is_owned_publish_path(owner, publish_path, identity["snapshot_sha256"]):
        raise ValueError("publish snapshot is not owned by pending owner")
    payload = {
        "schema_version": 1,
        "status": "pending",
        "snapshot_path": str(publish_path),
        "owner_snapshot_path": str(owner),
        "run_id": identity["run_id"],
        "snapshot_sha256": identity["snapshot_sha256"],
        "staged_at": staged_at or _now_utc_iso(),
    }
    if endpoint is not None:
        payload["endpoint"] = endpoint
    _write_json_atomic(state, payload)
    return payload


def load_pending_publish(
    snapshot_path: str | Path,
    *,
    state_path: str | Path | None = None,
) -> dict[str, Any] | None:
    snapshot = Path(snapshot_path)
    state = Path(state_path) if state_path is not None else pending_publish_path(snapshot)
    if not state.exists():
        return None
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid", "reason": "invalid_pending_state", "path": str(state)}
    if not isinstance(payload, dict) or payload.get("status") != "pending":
        return {"status": "invalid", "reason": "invalid_pending_state", "path": str(state)}
    if "endpoint" in payload and not isinstance(payload.get("endpoint"), str):
        return {"status": "invalid", "reason": "invalid_pending_endpoint", "path": str(state)}
    payload_snapshot_path = payload.get("snapshot_path")
    if not isinstance(payload_snapshot_path, str) or not payload_snapshot_path.strip():
        return {"status": "invalid", "reason": "invalid_pending_snapshot_path", "path": str(state)}
    owner_snapshot_path = payload.get("owner_snapshot_path")
    requested_owner = snapshot.resolve(strict=False)
    if owner_snapshot_path is None:
        owner_snapshot_path = payload_snapshot_path
    if not isinstance(owner_snapshot_path, str) or (
        Path(owner_snapshot_path).resolve(strict=False) != requested_owner
    ):
        return {"status": "invalid", "reason": "pending_owner_changed", "path": str(state)}
    try:
        identity = _snapshot_identity(payload_snapshot_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"status": "invalid", "reason": "pending_snapshot_unavailable", "path": str(state)}
    if identity["snapshot_sha256"] != payload.get("snapshot_sha256"):
        return {"status": "invalid", "reason": "pending_snapshot_changed", "path": str(state)}
    if not _is_owned_publish_path(
        owner_snapshot_path,
        payload_snapshot_path,
        identity["snapshot_sha256"],
    ):
        return {"status": "invalid", "reason": "pending_snapshot_not_owned", "path": str(state)}
    return {**payload, "path": str(state)}


def clear_pending_publish(
    snapshot_path: str | Path,
    *,
    state_path: str | Path | None = None,
) -> None:
    state = (
        Path(state_path)
        if state_path is not None
        else pending_publish_path(snapshot_path)
    )
    state.unlink(missing_ok=True)


def _publish_succeeded(result: dict[str, Any]) -> bool:
    status = result.get("http_status")
    return (
        result.get("status") == "sent"
        and isinstance(status, int)
        and 200 <= status < 300
        and result.get("ingest_status") in {"stored", "duplicate"}
    )


def attempt_publish(
    *,
    snapshot_path: str | Path,
    endpoint: str,
    secret: str,
    timestamp: str,
    publish_fn: PublishFn,
    state_path: str | Path | None = None,
    owner_snapshot_path: str | Path | None = None,
    stage: bool,
    clear_on_success: bool = True,
) -> dict[str, Any]:
    publish_snapshot_path: str | Path = snapshot_path
    if stage:
        pending = stage_pending_publish(
            snapshot_path,
            state_path=state_path,
            staged_at=timestamp,
            endpoint=endpoint,
            owner_snapshot_path=owner_snapshot_path,
        )
    else:
        pending = load_pending_publish(snapshot_path, state_path=state_path)
        if pending is None:
            return {"status": "no_pending", "publish": None, "pending": None}
        if pending.get("status") != "pending":
            return {
                "status": "pending_invalid",
                "reason": pending.get("reason"),
                "publish": None,
                "pending": pending,
            }
        pending_endpoint = pending.get("endpoint")
        if pending_endpoint is not None and pending_endpoint != endpoint:
            return {
                "status": "pending_invalid",
                "reason": "pending_endpoint_changed",
                "publish": None,
                "pending": pending,
            }
        publish_snapshot_path = str(pending["snapshot_path"])

    try:
        published = publish_fn(
            snapshot_path=publish_snapshot_path,
            endpoint=endpoint,
            secret=secret,
            timestamp=timestamp,
            live=True,
        )
    except _TRANSIENT_PUBLISH_ERRORS as exc:
        return {
            "status": "publish_pending",
            "reason": "transient_transport_error",
            "error_type": type(exc).__name__,
            "publish": None,
            "pending": pending,
        }

    if not _publish_succeeded(published):
        return {
            "status": "publish_pending",
            "reason": "publish_response_not_successful",
            "http_status": published.get("http_status"),
            "publish": published,
            "pending": pending,
        }

    if clear_on_success:
        clear_pending_publish(snapshot_path, state_path=state_path)
    return {
        "status": "published",
        "publish": published,
        "pending": None if clear_on_success else pending,
    }
