from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from worldcup.competitions import get_competition


_THEODDSAPI_SCHEMA = "theoddsapi_scores_v1"
_FOTMOB_SCHEMA = "fotmob_league_results_v1"
_SCHEMAS = frozenset({_THEODDSAPI_SCHEMA, _FOTMOB_SCHEMA})
_SCOPE = "football_90min"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAMPLE_READ_ERROR = "fotmob_sample_read_invalid"


def fotmob_result_contract_evidence_path(
    root: str | Path, competition_id: str
) -> Path:
    return (
        Path(root)
        / "data/local/leagues"
        / competition_id
        / "providers/fotmob/result_contract_evidence.json"
    )


def legacy_theoddsapi_result_contract_evidence_path(
    root: str | Path, competition_id: str
) -> Path:
    return (
        Path(root)
        / "data/local/leagues/legacy_theoddsapi"
        / competition_id
        / "result_contract_evidence.json"
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_result_contract_evidence(
    *,
    competition_id: str,
    sport_key: str,
    provider_schema: str,
    score_scope: str,
    source_reference: str,
    provider: str | None = None,
    sample_path: str | None = None,
) -> dict[str, Any]:
    profile = get_competition(competition_id)
    core = {
        "competition_id": competition_id,
        "sport_key": str(sport_key),
        "provider_schema": str(provider_schema),
        "score_scope": str(score_scope),
        "source_reference": str(source_reference),
    }
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        core["provider"] = str(provider or "")
        if sample_path is not None:
            core["sample_path"] = str(sample_path)
    verified = (
        profile.theoddsapi_sport_key == core["sport_key"]
        and core["provider_schema"] in _SCHEMAS
        and core["score_scope"] == _SCOPE
        and _source_reference_is_valid(core)
        and (
            "sample_path" not in core
            or fotmob_sample_path_is_sanitized(core["sample_path"])
        )
    )
    return {**core, "verified": verified, "fingerprint": _fingerprint(core)}


def _source_reference_is_valid(core: Mapping[str, str]) -> bool:
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        return core.get("provider") == "fotmob" and _SHA256.fullmatch(core["source_reference"]) is not None
    return bool(core["source_reference"].strip())


def fotmob_sample_path_is_sanitized(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.parts[:2] == ("data", "probe")
        and len(path.parts) > 2
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _same_inode(before: os.stat_result, after: os.stat_result) -> bool:
    return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


@contextmanager
def _hardened_probe_parent(
    root: str | Path,
    sample_path: str,
) -> Iterator[tuple[int, str]]:
    if not fotmob_sample_path_is_sanitized(sample_path):
        raise ValueError(_SAMPLE_READ_ERROR)
    relative = Path(sample_path)
    opened: list[int] = []
    try:
        root_resolved = Path(root).resolve(strict=True)
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        before_root = root_resolved.lstat()
        if not stat.S_ISDIR(before_root.st_mode):
            raise ValueError(_SAMPLE_READ_ERROR)
        current = os.open(root_resolved, directory_flags)
        opened.append(current)
        after_root = os.fstat(current)
        if not stat.S_ISDIR(after_root.st_mode) or not _same_inode(before_root, after_root):
            raise ValueError(_SAMPLE_READ_ERROR)
        for component in relative.parts[:-1]:
            before = os.lstat(component, dir_fd=current)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError(_SAMPLE_READ_ERROR)
            next_descriptor = os.open(component, directory_flags, dir_fd=current)
            opened.append(next_descriptor)
            after = os.fstat(next_descriptor)
            if not stat.S_ISDIR(after.st_mode) or not _same_inode(before, after):
                raise ValueError(_SAMPLE_READ_ERROR)
            current = next_descriptor
        yield current, relative.parts[-1]
    finally:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass


def read_fotmob_sample_bytes(
    root: str | Path,
    sample_path: str,
) -> tuple[bytes, str]:
    """Read one path-bound data/probe sample and hash the same fd byte stream."""
    file_descriptor: int | None = None
    try:
        with _hardened_probe_parent(root, sample_path) as (parent_descriptor, filename):
            before = os.lstat(filename, dir_fd=parent_descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(_SAMPLE_READ_ERROR)
            file_descriptor = os.open(
                filename,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_descriptor,
            )
            after = os.fstat(file_descriptor)
            if not stat.S_ISREG(after.st_mode) or not _same_inode(before, after):
                raise ValueError(_SAMPLE_READ_ERROR)
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            while chunk := os.read(file_descriptor, 1024 * 1024):
                chunks.append(chunk)
                digest.update(chunk)
            return b"".join(chunks), digest.hexdigest()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(_SAMPLE_READ_ERROR) from None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass


def verify_result_contract_evidence(
    evidence: Mapping[str, Any] | None,
    competition_id: str,
    *,
    provider_schema: str | None = None,
) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    try:
        profile = get_competition(competition_id)
    except KeyError:
        return False
    core = {
        "competition_id": str(evidence.get("competition_id") or ""),
        "sport_key": str(evidence.get("sport_key") or ""),
        "provider_schema": str(evidence.get("provider_schema") or ""),
        "score_scope": str(evidence.get("score_scope") or ""),
        "source_reference": str(evidence.get("source_reference") or ""),
    }
    if core["provider_schema"] == _FOTMOB_SCHEMA:
        core["provider"] = str(evidence.get("provider") or "")
        if "sample_path" in evidence:
            core["sample_path"] = str(evidence.get("sample_path") or "")
    return (
        evidence.get("verified") is True
        and core["competition_id"] == competition_id
        and core["sport_key"] == profile.theoddsapi_sport_key
        and core["provider_schema"] in _SCHEMAS
        and (provider_schema is None or core["provider_schema"] == provider_schema)
        and core["score_scope"] == _SCOPE
        and _source_reference_is_valid(core)
        and (
            "sample_path" not in core
            or fotmob_sample_path_is_sanitized(core["sample_path"])
        )
        and str(evidence.get("fingerprint") or "") == _fingerprint(core)
    )
