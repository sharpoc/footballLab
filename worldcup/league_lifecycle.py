from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_closing import LeagueClosingStore, select_league_closings
from worldcup.league_postmatch import merge_league_postmatch
from worldcup.league_result_evidence import (
    legacy_theoddsapi_result_contract_evidence_path,
    verify_result_contract_evidence,
)
from worldcup.league_results import (
    THEODDSAPI_RESULT_SCHEMA,
    adapt_theoddsapi_results_to_committed_receipt,
)
from worldcup.league_statistics import build_league_statistics
from worldcup.league_team_identity import accepted_league_team_identity_registry


LEGACY_ARTIFACT_SCOPE = "legacy_theoddsapi_scores_compatibility"
LEGACY_ROOT_RELATIVE_PATH = Path("data/local/leagues/legacy_theoddsapi")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _copy_legacy_evidence_atomic(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ValueError("legacy_result_contract_evidence_conflict")
            return
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _legacy_result_contract_evidence(
    root: Path, competition_id: str
) -> tuple[dict[str, Any], bytes | None]:
    isolated = legacy_theoddsapi_result_contract_evidence_path(root, competition_id)
    source = isolated
    migration_bytes: bytes | None = None
    if not source.exists():
        source = (
            root
            / "data/local/leagues"
            / competition_id
            / "result_contract_evidence.json"
        )
        if not source.exists():
            raise ValueError("legacy_result_contract_evidence_missing")
        try:
            migration_bytes = source.read_bytes()
        except OSError:
            raise ValueError("legacy_result_contract_evidence_unreadable") from None
        encoded = migration_bytes
    else:
        try:
            encoded = source.read_bytes()
        except OSError:
            raise ValueError("legacy_result_contract_evidence_unreadable") from None
    try:
        value = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("legacy_result_contract_evidence_unreadable") from None
    if (
        not isinstance(value, dict)
        or not verify_result_contract_evidence(
            value,
            competition_id,
            provider_schema=THEODDSAPI_RESULT_SCHEMA,
        )
    ):
        raise ValueError("legacy_result_contract_evidence_invalid")
    return value, migration_bytes


def _legacy_postmatch_path(root: Path, competition_id: str) -> Path:
    return root / LEGACY_ROOT_RELATIVE_PATH / competition_id / "postmatch.json"


def _shared_postmatch_path(root: Path, competition_id: str) -> Path:
    return root / "data/local/leagues" / competition_id / "postmatch.json"


def _is_fotmob_formal_postmatch(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("artifact_scope") == "fotmob_formal_postmatch"
        and payload.get("result_provider_schema") == "fotmob_league_results_v1"
    )


def _tag_legacy_postmatch(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "artifact_scope": LEGACY_ARTIFACT_SCOPE,
        "result_provider_schema": THEODDSAPI_RESULT_SCHEMA,
    }


def _legacy_statistics(blocks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {
        **build_league_statistics(blocks),
        "statistics_origin": LEGACY_ARTIFACT_SCOPE,
    }


def _archive_shared_legacy_postmatch(root: Path, competition_id: str) -> None:
    source = _shared_postmatch_path(root, competition_id)
    if not source.exists():
        return
    try:
        payload = _read_json(source)
    except (OSError, json.JSONDecodeError):
        raise ValueError("legacy_postmatch_upgrade_unreadable") from None
    if _is_fotmob_formal_postmatch(payload) or (
        isinstance(payload, dict)
        and isinstance(payload.get("accepted_result_receipts"), dict)
        and isinstance(payload.get("missing_closing_results"), dict)
        and isinstance(payload.get("missing_closing_event_ids"), list)
    ):
        return
    archive = (
        root
        / LEGACY_ROOT_RELATIVE_PATH
        / competition_id
        / "postmatch.pre_isolation.json"
    )
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if archive.read_bytes() != source.read_bytes():
            raise ValueError("legacy_postmatch_upgrade_conflict")
        source.unlink()
    else:
        os.replace(source, archive)
    for directory in {source.parent, archive.parent}:
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def run_league_lifecycle(
    *,
    root: str | Path,
    competition_ids: Iterable[str],
    write: bool = False,
) -> dict[str, Any]:
    root_path = Path(root)
    competition_ids = list(competition_ids)
    registry = accepted_league_team_identity_registry()
    competitions: dict[str, dict[str, Any]] = {}
    postmatch_blocks: list[dict[str, Any]] = []
    pending_writes: list[
        tuple[str, dict[str, Any], dict[str, Any], bytes | None]
    ] = []
    for competition_id in competition_ids:
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            competitions[str(competition_id)] = {"status": "blocked", "reason": "competition_not_allowed"}
            continue
        partition = root_path / "data/local/leagues" / competition_id
        history_dir = partition / "history"
        scores_path = root_path / "data/cache/leagues" / competition_id / "scores.json"
        try:
            evidence, evidence_migration = _legacy_result_contract_evidence(
                root_path,
                competition_id,
            )
        except ValueError as exc:
            competitions[competition_id] = {
                "status": "blocked",
                "reason": str(exc),
            }
            continue
        if not history_dir.exists() or not scores_path.exists():
            missing = []
            if not history_dir.exists():
                missing.append("history")
            if not scores_path.exists():
                missing.append("scores")
            competitions[competition_id] = {
                "status": "blocked", "reason": "lifecycle_inputs_missing", "missing": missing,
            }
            continue
        try:
            snapshots = [
                _read_json(path) for path in sorted(history_dir.glob("*.json")) if path.is_file()
            ]
            if not snapshots:
                competitions[competition_id] = {
                    "status": "blocked", "reason": "lifecycle_inputs_missing", "missing": ["history"],
                }
                continue
            closing = select_league_closings(snapshots, competition_id)
            adapted = adapt_theoddsapi_results_to_committed_receipt(
                _read_json(scores_path),
                competition_id,
                result_contract_evidence=evidence,
                identity_registry=registry,
            )
            existing_path = _legacy_postmatch_path(root_path, competition_id)
            existing = _read_json(existing_path) if existing_path.exists() else None
            postmatch = _tag_legacy_postmatch(
                merge_league_postmatch(
                    existing,
                    closing,
                    adapted["receipt"],
                    competition_id,
                )
            )
        except ValueError as exc:
            reason = str(exc)
            if reason.startswith("legacy_result_contract_evidence_"):
                competitions[competition_id] = {
                    "status": "blocked",
                    "reason": reason,
                }
            else:
                competitions[competition_id] = {
                    "status": "error",
                    "reason": type(exc).__name__,
                }
            continue
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            competitions[competition_id] = {"status": "error", "reason": type(exc).__name__}
            continue
        postmatch_blocks.append(postmatch)
        pending_writes.append((competition_id, closing, postmatch, evidence_migration))
        competitions[competition_id] = {
            "status": "ready",
            "closing_count": len(closing["closings"]),
            "result_count": len(adapted["receipt"]["results"]),
            "pending_result_count": len(adapted["pending"]),
            "result_provider_schema": adapted["provider_schema"],
            "decision_tally": postmatch["decision_tally"],
            "decision_coverage": postmatch["decision_coverage"],
        }
    ready_blocks = {block["competition_id"]: block for block in postmatch_blocks}
    stored_blocks: dict[str, dict[str, Any]] = {}
    for competition_id in competition_ids:
        postmatch_path = _legacy_postmatch_path(root_path, competition_id)
        if postmatch_path.exists():
            try:
                existing = _read_json(postmatch_path)
                if isinstance(existing, dict):
                    stored_blocks[competition_id] = {
                        **existing,
                        "_expected_partition_competition_id": competition_id,
                    }
            except (OSError, json.JSONDecodeError):
                pass
    statistics = _legacy_statistics(ready_blocks.values())
    stored_count = 0
    if write:
        for competition_id, closing, postmatch, evidence_migration in pending_writes:
            partition = root_path / "data/local/leagues" / competition_id
            closing_path = partition / "closing.json"
            postmatch_path = _legacy_postmatch_path(root_path, competition_id)
            try:
                if evidence_migration is not None:
                    _copy_legacy_evidence_atomic(
                        legacy_theoddsapi_result_contract_evidence_path(
                            root_path,
                            competition_id,
                        ),
                        evidence_migration,
                    )
                LeagueClosingStore(closing_path).merge(closing)
                _write_json_atomic(postmatch_path, postmatch)
                _archive_shared_legacy_postmatch(root_path, competition_id)
            except (OSError, ValueError) as exc:
                reason = str(exc)
                competitions[competition_id] = {
                    "status": "error",
                    "reason": (
                        reason
                        if reason.startswith("legacy_result_contract_evidence_")
                        else type(exc).__name__
                    ),
                }
            else:
                competitions[competition_id]["status"] = "stored"
                stored_blocks[competition_id] = {
                    **postmatch,
                    "_expected_partition_competition_id": competition_id,
                }
                stored_count += 1
        statistics = _legacy_statistics(stored_blocks.values())
        if stored_blocks:
            _write_json_atomic(root_path / LEGACY_ROOT_RELATIVE_PATH / "statistics.json", statistics)
    status = "stored" if write and stored_count else "dry_run" if not write else "blocked"
    return {"status": status, "competitions": competitions, "statistics": statistics}
