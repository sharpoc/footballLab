"""Reproducible, fully offline evaluation of saved FotMob result bundles."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import stat
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from worldcup.collectors import league_fotmob_results as fotmob_parser
from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, get_competition
from worldcup import league_result_evidence as result_evidence
from worldcup.league_result_evidence import (
    build_result_contract_evidence,
    fotmob_sample_path_is_sanitized,
    read_fotmob_sample_bytes,
)
from worldcup.league_team_identity import (
    LeagueTeamIdentityRegistry,
    accepted_league_team_identity_registry,
)


_BUNDLE_KEYS = {
    "schema_version",
    "provider",
    "competition_id",
    "calendar_date",
    "observed_league_id",
    "calendar",
    "details",
}
_CLI_ERRORS = {
    "fotmob_probe_arguments_invalid",
    "fotmob_probe_captured_at_invalid",
    "fotmob_probe_competition_duplicate",
    "fotmob_probe_entries_invalid",
    "fotmob_probe_entries_required",
    "fotmob_probe_output_failed",
    "fotmob_probe_output_invalid",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("fotmob_probe_arguments_invalid")


class _OutputPathInvalid(Exception):
    pass


class _OutputCommitFailed(Exception):
    pass


def _captured_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fotmob_probe_captured_at_invalid")
    return value.astimezone(timezone.utc)


def _blocked_result(
    *,
    competition_id: str,
    sample_path: str | None,
    reason: str,
    sample_sha256: str | None = None,
    evidence_fingerprint: str | None = None,
    accepted_result_count: int = 0,
    accepted_event_ids: list[str] | None = None,
    pending: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "sample_path": sample_path,
        "sample_sha256": sample_sha256,
        "evidence_fingerprint": evidence_fingerprint,
        "status": "blocked",
        "accepted_result_count": accepted_result_count,
        "accepted_event_ids": accepted_event_ids or [],
        "pending": pending or [],
        "reason": reason,
    }


def _bundle_structure_reason(
    bundle: Any,
    competition_id: str,
) -> str | None:
    if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_KEYS:
        return "bundle_schema_invalid"
    if type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1:
        return "bundle_schema_invalid"
    if bundle.get("provider") != "fotmob":
        return "bundle_provider_invalid"
    if bundle.get("competition_id") != competition_id:
        return "bundle_competition_mismatch"
    expected_league_id = fotmob_parser._FOTMOB_COMPETITION_IDS.get(competition_id)
    observed_league_id = bundle.get("observed_league_id")
    if (
        expected_league_id is None
        or isinstance(observed_league_id, bool)
        or not isinstance(observed_league_id, (str, int))
        or str(observed_league_id).strip() != expected_league_id
    ):
        return "bundle_schema_invalid"
    calendar_date = bundle.get("calendar_date")
    if (
        type(calendar_date) is not str
        or len(calendar_date) != 8
        or not calendar_date.isascii()
        or not calendar_date.isdigit()
    ):
        return "bundle_schema_invalid"
    try:
        date(
            int(calendar_date[0:4]),
            int(calendar_date[4:6]),
            int(calendar_date[6:8]),
        )
    except ValueError:
        return "bundle_schema_invalid"
    calendar = bundle.get("calendar")
    if (
        not isinstance(calendar, dict)
        or not isinstance(calendar.get("leagues"), list)
        or not isinstance(bundle.get("details"), dict)
    ):
        return "bundle_schema_invalid"
    return None


def _target_finished_event_ids(
    calendar: Mapping[str, Any],
    competition_id: str,
) -> tuple[bool, list[str]]:
    expected_league_id = fotmob_parser._FOTMOB_COMPETITION_IDS[competition_id]
    target_containers = [
        league
        for league in calendar.get("leagues") or []
        if isinstance(league, Mapping)
        and fotmob_parser._provider_id(league.get("id")) == expected_league_id
    ]
    finished: list[str] = []
    for league in target_containers:
        matches = league.get("matches")
        if not isinstance(matches, list):
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            event_id = fotmob_parser._event_id(match)
            status = fotmob_parser._mapping(match.get("status"))
            if event_id is not None and fotmob_parser._has_terminal_ft(status):
                finished.append(event_id)
    return bool(target_containers), sorted(set(finished))


def evaluate_saved_fotmob_result_bundle(
    *,
    root: str | Path,
    sample_path: str,
    competition_id: str,
    captured_at: datetime,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
) -> dict[str, Any]:
    projected_sample_path = (
        sample_path if fotmob_sample_path_is_sanitized(sample_path) else None
    )
    try:
        raw, sample_sha256 = read_fotmob_sample_bytes(root, sample_path)
    except ValueError:
        return _blocked_result(
            competition_id=competition_id,
            sample_path=projected_sample_path,
            reason="sample_path_invalid",
        )
    try:
        bundle = json.loads(raw)
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError):
        bundle = None
    structural_reason = _bundle_structure_reason(bundle, competition_id)
    if structural_reason is not None:
        return _blocked_result(
            competition_id=competition_id,
            sample_path=sample_path,
            sample_sha256=sample_sha256,
            reason=structural_reason,
        )

    captured = _captured_at(captured_at)
    profile = get_competition(competition_id)
    evidence = build_result_contract_evidence(
        competition_id=competition_id,
        sport_key=profile.theoddsapi_sport_key,
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference=sample_sha256,
        provider="fotmob",
        sample_path=sample_path,
    )
    registry = identity_registry or accepted_league_team_identity_registry()
    parsed: dict[str, Any] | None = None
    try:
        parsed = fotmob_parser.parse_fotmob_league_results(
            bundle["calendar"],
            bundle["details"],
            competition_id,
            result_contract_evidence=evidence,
            identity_registry=registry,
            captured_at=captured,
        )
    except (KeyError, TypeError, ValueError):
        parsed = None

    target_exists, finished_event_ids = _target_finished_event_ids(
        bundle["calendar"], competition_id
    )
    if not target_exists:
        reason = "target_competition_missing"
    elif not finished_event_ids:
        reason = "no_current_season_finished_match"
    elif not any(event_id in bundle["details"] for event_id in finished_event_ids):
        reason = "sample_detail_missing"
    else:
        reason = None

    results = parsed.get("results") if isinstance(parsed, dict) else []
    pending = parsed.get("pending") if isinstance(parsed, dict) else []
    results = results if isinstance(results, list) else []
    pending = pending if isinstance(pending, list) else []
    accepted_event_ids = sorted(
        str(row.get("source_event_id"))
        for row in results
        if isinstance(row, Mapping) and str(row.get("source_event_id") or "")
    )
    result_count = len(results)
    if reason is None and result_count == 0:
        reason = "strict_parser_rejected"
    elif reason is None and result_count > 1:
        reason = "multiple_results_not_allowed"

    if reason is not None:
        return _blocked_result(
            competition_id=competition_id,
            sample_path=sample_path,
            sample_sha256=sample_sha256,
            evidence_fingerprint=evidence["fingerprint"],
            accepted_result_count=result_count,
            accepted_event_ids=accepted_event_ids,
            pending=list(pending),
            reason=reason,
        )
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "sample_path": sample_path,
        "sample_sha256": sample_sha256,
        "evidence_fingerprint": evidence["fingerprint"],
        "status": "verified",
        "accepted_result_count": 1,
        "accepted_event_ids": accepted_event_ids,
        "pending": list(pending),
        "reason": None,
    }


def evaluate_saved_fotmob_result_bundles(
    *,
    root: str | Path,
    entries: Sequence[tuple[str, str]],
    captured_at: datetime,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
) -> dict[str, Any]:
    captured = _captured_at(captured_at)
    values = list(entries)
    if not values:
        raise ValueError("fotmob_probe_entries_required")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in values:
        if (
            not isinstance(entry, (tuple, list))
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not entry[0].strip()
            or not isinstance(entry[1], str)
            or not entry[1]
        ):
            raise ValueError("fotmob_probe_entries_invalid")
        competition_id, sample_path = entry
        if competition_id in seen:
            raise ValueError("fotmob_probe_competition_duplicate")
        seen.add(competition_id)
        normalized.append((competition_id, sample_path))
    competitions = {
        competition_id: evaluate_saved_fotmob_result_bundle(
            root=root,
            sample_path=sample_path,
            competition_id=competition_id,
            captured_at=captured,
            identity_registry=identity_registry,
        )
        for competition_id, sample_path in sorted(normalized)
    }
    verified_count = sum(row["status"] == "verified" for row in competitions.values())
    blocked_count = len(competitions) - verified_count
    aggregate_status = (
        "verified"
        if blocked_count == 0
        else "partial"
        if verified_count > 0
        else "blocked"
    )
    return {
        "schema_version": 1,
        "captured_at": captured.isoformat(),
        "status": aggregate_status,
        "verified_count": verified_count,
        "blocked_count": blocked_count,
        "competitions": competitions,
    }


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("write_failed")
        remaining = remaining[written:]


def _atomic_write_output(root: str | Path, output_path: str, payload: bytes) -> None:
    if not fotmob_sample_path_is_sanitized(output_path):
        raise _OutputPathInvalid
    staging_name: str | None = None
    staging_created = False
    staging_descriptor: int | None = None
    payload_descriptor: int | None = None
    staged_payload = "payload"
    try:
        with result_evidence._hardened_probe_parent(root, output_path) as (
            parent_descriptor,
            filename,
        ):
            fcntl.flock(parent_descriptor, fcntl.LOCK_EX)
            try:
                existing = os.lstat(filename, dir_fd=parent_descriptor)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise _OutputPathInvalid from None
            else:
                if not stat.S_ISREG(existing.st_mode):
                    raise _OutputPathInvalid
            staging_name = f".{filename}.{uuid.uuid4().hex}.stage"
            try:
                os.mkdir(staging_name, 0o700, dir_fd=parent_descriptor)
                staging_created = True
                before_staging = os.lstat(staging_name, dir_fd=parent_descriptor)
                staging_descriptor = os.open(
                    staging_name,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_descriptor,
                )
                opened_staging = os.fstat(staging_descriptor)
                if (
                    not stat.S_ISDIR(before_staging.st_mode)
                    or not stat.S_ISDIR(opened_staging.st_mode)
                    or not result_evidence._same_inode(before_staging, opened_staging)
                    or opened_staging.st_uid != os.geteuid()
                ):
                    raise _OutputCommitFailed
                os.fchmod(staging_descriptor, 0o700)
                protected_staging = os.fstat(staging_descriptor)
                if (
                    not stat.S_ISDIR(protected_staging.st_mode)
                    or not result_evidence._same_inode(opened_staging, protected_staging)
                    or protected_staging.st_uid != os.geteuid()
                    or stat.S_IMODE(protected_staging.st_mode) != 0o700
                ):
                    raise _OutputCommitFailed
                payload_descriptor = os.open(
                    staged_payload,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=staging_descriptor,
                )
                os.fchmod(payload_descriptor, 0o600)
                _write_all(payload_descriptor, payload)
                os.fsync(payload_descriptor)
                opened_payload = os.fstat(payload_descriptor)
                bound_payload = os.lstat(staged_payload, dir_fd=staging_descriptor)
                if (
                    not stat.S_ISREG(opened_payload.st_mode)
                    or not stat.S_ISREG(bound_payload.st_mode)
                    or not result_evidence._same_inode(opened_payload, bound_payload)
                    or opened_payload.st_uid != os.geteuid()
                    or stat.S_IMODE(opened_payload.st_mode) != 0o600
                ):
                    raise _OutputCommitFailed
                os.replace(
                    staged_payload,
                    filename,
                    src_dir_fd=staging_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                staged_payload = ""
                os.fsync(parent_descriptor)
            except _OutputPathInvalid:
                raise
            except OSError:
                raise _OutputCommitFailed from None
            finally:
                if payload_descriptor is not None:
                    try:
                        os.close(payload_descriptor)
                    except OSError:
                        pass
                if staging_descriptor is not None and staged_payload:
                    try:
                        os.unlink(staged_payload, dir_fd=staging_descriptor)
                    except OSError:
                        pass
                if staging_descriptor is not None:
                    try:
                        os.close(staging_descriptor)
                    except OSError:
                        pass
                if staging_created and staging_name is not None:
                    try:
                        os.rmdir(staging_name, dir_fd=parent_descriptor)
                    except OSError:
                        pass
    except _OutputPathInvalid:
        raise
    except _OutputCommitFailed:
        raise
    except (OSError, TypeError, ValueError):
        raise _OutputPathInvalid from None


def _parse_cli_captured_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("fotmob_probe_captured_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("fotmob_probe_captured_at_invalid") from None
    return _captured_at(parsed)


def _parse_entries(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, str) or "=" not in value:
            raise ValueError("fotmob_probe_entries_invalid")
        competition_id, sample_path = value.split("=", 1)
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not sample_path:
            raise ValueError("fotmob_probe_entries_invalid")
        entries.append((competition_id, sample_path))
    return tuple(entries)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        description="Evaluate saved FotMob result samples without network or runtime state."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--entry", action="append", default=[])
    parser.add_argument("--captured-at", default=None)
    parser.add_argument("--out", default=None)
    return parser


def _safe_error(reason: str) -> dict[str, str]:
    return {"status": "error", "reason": reason}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        entries = _parse_entries(args.entry)
        result = evaluate_saved_fotmob_result_bundles(
            root=args.root,
            entries=entries,
            captured_at=_parse_cli_captured_at(args.captured_at),
        )
        encoded = (
            json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if args.out is not None:
            _atomic_write_output(args.root, args.out, encoded)
    except _OutputPathInvalid:
        print(json.dumps(_safe_error("fotmob_probe_output_invalid"), sort_keys=True))
        return 2
    except _OutputCommitFailed:
        print(json.dumps(_safe_error("fotmob_probe_output_failed"), sort_keys=True))
        return 2
    except ValueError as exc:
        reason = str(exc) if str(exc) in _CLI_ERRORS else "fotmob_probe_arguments_invalid"
        print(json.dumps(_safe_error(reason), sort_keys=True))
        return 2
    print(encoded.decode("utf-8"), end="")
    return 0 if result["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
