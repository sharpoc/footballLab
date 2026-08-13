"""Archive local CSL snapshots for later postmatch evaluation.

This module only reads an already-created local snapshot and writes a copy into
ignored local history. It never fetches sources, reads secrets, or publishes.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkstemp
from typing import Any, Callable

DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SNAPSHOT = "data/local/diagnostics/csl_live_league_snapshot.json"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"


def _parse_utc(value: Any, *, reason: str = "invalid_snapshot_at") -> datetime:
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
        raise ValueError(reason) from exc
    if parsed.tzinfo is None:
        raise ValueError(reason)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def _utc_iso(value: Any) -> str:
    return _parse_utc(value).isoformat().replace("+00:00", "Z")


def _stamp(value: Any) -> str:
    return _parse_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _canonical_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def target_snapshot_path(snapshot: dict[str, Any], history: str | Path) -> Path:
    return Path(history) / f"snapshot_{_stamp(snapshot.get('snapshot_at'))}-live.json"


def _load_snapshot_with_bytes(source: str | Path) -> tuple[dict[str, Any], bytes]:
    path = Path(source)
    raw = path.read_bytes()
    try:
        snapshot = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_snapshot: expected object")
    return snapshot, raw


def load_snapshot(source: str | Path) -> dict[str, Any]:
    snapshot, _raw = _load_snapshot_with_bytes(source)
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
        raise ValueError("unexpected_competition")

    matches = snapshot.get("matches")
    if not isinstance(matches, list):
        raise ValueError("invalid_matches")
    if len(matches) < min_matches:
        raise ValueError("insufficient_matches")

    snapshot_at = _utc_iso(snapshot.get("snapshot_at"))
    return {
        "competition_id": competition_id,
        "snapshot_at": snapshot_at,
        "matches": len(matches),
    }


def validate_archive_fixture_coverage(
    snapshot: dict[str, Any],
    *,
    competition_id: str = DEFAULT_COMPETITION_ID,
) -> dict[str, int]:
    snapshot_at = _parse_utc(snapshot.get("snapshot_at"))
    late_matches = 0
    for index, match in enumerate(snapshot.get("matches") or []):
        if not isinstance(match, dict):
            raise ValueError(f"invalid_match:{index}")
        match_competition = match.get("competition")
        if match_competition is None:
            raise ValueError(f"missing_match_competition:{index}")
        if not isinstance(match_competition, dict):
            raise ValueError(f"invalid_match_competition:{index}")
        actual_competition = match_competition.get("id")
        if not isinstance(actual_competition, str) or not actual_competition.strip():
            raise ValueError(f"missing_match_competition:{index}")
        if actual_competition != competition_id:
            raise ValueError(f"unexpected_match_competition:{index}")
        home = match.get("home_canonical")
        away = match.get("away_canonical")
        if (
            not isinstance(home, str)
            or not home.strip()
            or not isinstance(away, str)
            or not away.strip()
        ):
            raise ValueError(f"missing_match_identity:{index}")
        kickoff = _parse_utc(
            match.get("kickoff_at_utc"),
            reason=f"invalid_match_kickoff:{index}",
        )
        if (
            str(match.get("fixture_status") or "").upper() != "POSTPONED"
            and snapshot_at >= kickoff
        ):
            late_matches += 1
    return {"late_matches": late_matches}


@contextmanager
def _archive_lock(history: Path):
    history.mkdir(parents=True, exist_ok=True)
    lock_path = history / ".csl_snapshot_archive.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _commit_new_archive(
    path: Path,
    content: str,
    *,
    competition_id: str,
    min_matches: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staged, staged_metadata, staged_bytes = _validated_archive(
            temp_path,
            competition_id=competition_id,
            min_matches=min_matches,
        )
        if staged_bytes != content.encode("utf-8"):
            raise ValueError("archive_staging_content_mismatch")
        os.link(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _validated_archive(
    path: Path,
    *,
    competition_id: str,
    min_matches: int,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    stored, raw = _load_snapshot_with_bytes(path)
    metadata = validate_snapshot(
        stored,
        competition_id=competition_id,
        min_matches=min_matches,
    )
    validate_archive_fixture_coverage(
        stored,
        competition_id=competition_id,
    )
    return stored, metadata, raw


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
    commit_new: Callable[..., None] = _commit_new_archive,
) -> dict[str, Any]:
    source_path = Path(source)
    history_path = Path(history)
    snapshot = load_snapshot(source_path)
    metadata = validate_snapshot(
        snapshot,
        competition_id=competition_id,
        min_matches=min_matches,
    )
    coverage = validate_archive_fixture_coverage(
        snapshot,
        competition_id=competition_id,
    )
    summary_metadata = {**metadata, **coverage}
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
            metadata=summary_metadata,
        )

    with _archive_lock(history_path):
        if target.exists():
            _existing, existing_metadata, existing_bytes = _validated_archive(
                target,
                competition_id=competition_id,
                min_matches=min_matches,
            )
            if (
                existing_bytes == content.encode("utf-8")
                and existing_metadata == metadata
            ):
                return _summary(
                    status="duplicate",
                    created=False,
                    duplicate=True,
                    dry_run=False,
                    source=source_path,
                    target=target,
                    metadata=summary_metadata,
                )
            raise ValueError("archive_conflict")

        try:
            commit_new(
                target,
                content,
                competition_id=competition_id,
                min_matches=min_matches,
            )
        except FileExistsError:
            _existing, existing_metadata, existing_bytes = _validated_archive(
                target,
                competition_id=competition_id,
                min_matches=min_matches,
            )
            if (
                existing_bytes == content.encode("utf-8")
                and existing_metadata == metadata
            ):
                return _summary(
                    status="duplicate",
                    created=False,
                    duplicate=True,
                    dry_run=False,
                    source=source_path,
                    target=target,
                    metadata=summary_metadata,
                )
            raise ValueError("archive_conflict")

        _stored, stored_metadata, stored_bytes = _validated_archive(
            target,
            competition_id=competition_id,
            min_matches=min_matches,
        )
        if stored_bytes != content.encode("utf-8") or stored_metadata != metadata:
            raise ValueError("archive_validation_failed")

        return _summary(
            status="created",
            created=True,
            duplicate=False,
            dry_run=False,
            source=source_path,
            target=target,
            metadata=summary_metadata,
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
    except (OSError, ValueError) as exc:
        summary = {
            "status": "error",
            "reason": "snapshot_archive_failed",
            "error_type": type(exc).__name__,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
