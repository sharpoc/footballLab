from __future__ import annotations

import json
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


def load_quota_ledger(path: str | Path) -> dict:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return {"providers": {}}
    return json.loads(ledger_path.read_text(encoding="utf-8"))


def save_quota_ledger(path: str | Path, ledger: dict) -> None:
    ledger_path = Path(path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def update_quota_from_headers(
    path: str | Path,
    provider: str,
    headers: Mapping[str, str],
    estimated_last: int | None = None,
    observed_at: str | None = None,
) -> dict:
    normalized = {key.lower(): value for key, value in headers.items()}
    entry = {
        "used": _to_int(normalized.get("x-requests-used")),
        "remaining": _to_int(normalized.get("x-requests-remaining")),
        "last": _to_int(normalized.get("x-requests-last")),
        "observed_at": observed_at or _now_utc_iso(),
    }
    if entry["last"] is None:
        entry["last"] = estimated_last

    ledger = load_quota_ledger(path)
    ledger.setdefault("providers", {})[provider] = entry
    save_quota_ledger(path, ledger)
    return entry
