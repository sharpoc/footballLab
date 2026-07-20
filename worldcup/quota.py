from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _lock_path(ledger_path: Path) -> Path:
    return ledger_path.with_suffix(ledger_path.suffix + ".lock")


def load_quota_ledger(path: str | Path) -> dict:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return {"providers": {}}
    return json.loads(ledger_path.read_text(encoding="utf-8"))


def save_quota_ledger(path: str | Path, ledger: dict) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(ledger_path.parent), suffix=".tmp")
    try:
        os.write(fd, content)
        os.fsync(fd)
        os.close(fd)
        os.replace(tmp, str(ledger_path))
    except BaseException:
        os.close(fd) if not _fd_closed(fd) else None
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _fd_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
        return False
    except OSError:
        return True


def update_quota_from_headers(
    path: str | Path,
    provider: str,
    headers: Mapping[str, str],
    estimated_last: int | None = None,
    observed_at: str | None = None,
) -> dict:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _lock_path(ledger_path)

    normalized = {key.lower(): value for key, value in headers.items()}
    entry = {
        "used": _to_int(normalized.get("x-requests-used")),
        "remaining": _to_int(normalized.get("x-requests-remaining")),
        "last": _to_int(normalized.get("x-requests-last")),
        "observed_at": observed_at or _now_utc_iso(),
    }
    if entry["last"] is None:
        entry["last"] = estimated_last

    lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ledger = load_quota_ledger(ledger_path)
        ledger.setdefault("providers", {})[provider] = entry
        save_quota_ledger(ledger_path, ledger)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    return entry
