"""Archive local CSL snapshots for later postmatch evaluation.

This module only reads an already-created local snapshot and writes a copy into
ignored local history. It never fetches sources, reads secrets, or publishes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SNAPSHOT = "data/local/diagnostics/csl_live_league_snapshot.json"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"


def _parse_utc(value: Any) -> datetime:
    if value in (None, ""):
        raise ValueError("missing_snapshot_at")
    text = str(value).strip()
    if not text:
        raise ValueError("missing_snapshot_at")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid_snapshot_at: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _utc_iso(value: Any) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _stamp(value: Any) -> str:
    return _parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _canonical_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def target_snapshot_path(snapshot: dict[str, Any], history: str | Path) -> Path:
    return Path(history) / f"snapshot_{_stamp(snapshot.get('snapshot_at'))}-live.json"


def load_snapshot(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json: {path}") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_snapshot: expected object")
    return snapshot


def validate_snapshot(
    snapshot: dict[str, Any],
    *,
    competition_id: str = DEFAULT_COMPETITION_ID,
    min_matches: int = 1,
) -> dict[str, Any]:
    competition = snapshot.get("competition")
    if not isinstance(competition, dict):
        raise ValueError("missing_competition")
    actual_competition = competition.get("id")
    if actual_competition != competition_id:
        raise ValueError(f"unexpected_competition: {actual_competition}")

    matches = snapshot.get("matches")
    if not isinstance(matches, list):
        raise ValueError("invalid_matches")
    if len(matches) < min_matches:
        raise ValueError(f"insufficient_matches: {len(matches)} < {min_matches}")

    snapshot_at = _utc_iso(snapshot.get("snapshot_at"))
    return {
        "competition_id": competition_id,
        "snapshot_at": snapshot_at,
        "matches": len(matches),
    }


def _summary(
    *,
    status: str,
    created: bool,
    duplicate: bool,
    dry_run: bool,
    source: Path,
    target: Path,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "created": created,
        "duplicate": duplicate,
        "dry_run": dry_run,
        **metadata,
        "source": str(source),
        "path": str(target),
    }


def archive_snapshot(
    *,
    source: str | Path = DEFAULT_SNAPSHOT,
    history: str | Path = DEFAULT_HISTORY,
    competition_id: str = DEFAULT_COMPETITION_ID,
    min_matches: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    source_path = Path(source)
    history_path = Path(history)
    snapshot = load_snapshot(source_path)
    metadata = validate_snapshot(
        snapshot,
        competition_id=competition_id,
        min_matches=min_matches,
    )
    target = target_snapshot_path(snapshot, history_path)
    content = _canonical_json(snapshot)

    if dry_run:
        return _summary(
            status="dry_run",
            created=False,
            duplicate=False,
            dry_run=True,
            source=source_path,
            target=target,
            metadata=metadata,
        )

    if target.exists():
        existing = load_snapshot(target)
        if _canonical_json(existing) == content:
            return _summary(
                status="duplicate",
                created=False,
                duplicate=True,
                dry_run=False,
                source=source_path,
                target=target,
                metadata=metadata,
            )
        raise ValueError(f"archive_conflict: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return _summary(
        status="created",
        created=True,
        duplicate=False,
        dry_run=False,
        source=source_path,
        target=target,
        metadata=metadata,
    )


def _resolve_under_root(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive a local CSL snapshot into ignored history.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--competition-id", "--competition", default=DEFAULT_COMPETITION_ID)
    parser.add_argument("--min-matches", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root)
    try:
        summary = archive_snapshot(
            source=_resolve_under_root(root, args.snapshot),
            history=_resolve_under_root(root, args.history),
            competition_id=args.competition_id,
            min_matches=args.min_matches,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, ValueError) as exc:
        summary = {"status": "error", "error": str(exc)}
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
