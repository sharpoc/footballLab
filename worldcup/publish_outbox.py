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
) -> dict[str, Any]:
    snapshot = Path(snapshot_path)
    state = Path(state_path) if state_path is not None else pending_publish_path(snapshot)
    identity = _snapshot_identity(snapshot)
    payload = {
        "schema_version": 1,
        "status": "pending",
        "snapshot_path": str(snapshot),
        "run_id": identity["run_id"],
        "snapshot_sha256": identity["snapshot_sha256"],
        "staged_at": staged_at or _now_utc_iso(),
    }
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
    try:
        identity = _snapshot_identity(snapshot)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"status": "invalid", "reason": "pending_snapshot_unavailable", "path": str(state)}
    if identity["snapshot_sha256"] != payload.get("snapshot_sha256"):
        return {"status": "invalid", "reason": "pending_snapshot_changed", "path": str(state)}
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
    )


def attempt_publish(
    *,
    snapshot_path: str | Path,
    endpoint: str,
    secret: str,
    timestamp: str,
    publish_fn: PublishFn,
    state_path: str | Path | None = None,
    stage: bool,
) -> dict[str, Any]:
    if stage:
        pending = stage_pending_publish(
            snapshot_path,
            state_path=state_path,
            staged_at=timestamp,
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

    try:
        published = publish_fn(
            snapshot_path=snapshot_path,
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

    clear_pending_publish(snapshot_path, state_path=state_path)
    return {
        "status": "published",
        "publish": published,
        "pending": None,
    }
