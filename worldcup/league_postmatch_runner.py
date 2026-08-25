"""Dry-run-first orchestration for verified six-league postmatch settlement."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from worldcup.collectors.league_fotmob_results import parse_fotmob_league_results
from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_acceptance import LeagueAcceptanceStore, acceptance_row_is_active
from worldcup.league_closing import LeagueClosingStore, select_league_closings
from worldcup.league_postmatch import FORMAL_SCOPE, merge_league_postmatch
from worldcup.league_postmatch_notifications import (
    LeaguePostmatchNotificationOutbox,
    _read_state as _read_notification_state,
    build_daily_settlement_event,
    build_threshold_events,
)
from worldcup.league_postmatch_planner import plan_league_postmatch
from worldcup.league_result_evidence import (
    fotmob_result_contract_evidence_path,
    fotmob_sample_path_is_sanitized,
    verify_result_contract_evidence,
)
from worldcup.league_result_store import LeagueResultStore, _read as _read_result_receipt
from worldcup.league_statistics import (
    build_league_statistics,
    build_league_statistics_from_components,
)
from worldcup.league_team_identity import (
    accepted_league_team_identity_registry,
    league_team_identity_registry_fingerprint,
)


CalendarFetcher = Callable[[str, str], Mapping[str, Any]]
DetailFetcher = Callable[[str, str], Mapping[str, Any]]
Notifier = Callable[[str, str], Mapping[str, Any]]

LOCK_RELATIVE_PATH = Path("data/local/leagues/league_postmatch.lock")
ACCEPTANCE_RELATIVE_PATH = Path("data/local/leagues/acceptance.json")
STATISTICS_RELATIVE_PATH = Path("data/local/leagues/postmatch_statistics.json")
COMPONENTS_RELATIVE_PATH = Path("data/local/leagues/postmatch_components.json")
STATE_RELATIVE_PATH = Path("data/local/leagues/postmatch_state.json")
NOTIFICATION_STATE_RELATIVE_PATH = Path("data/local/leagues/postmatch_notification_state.json")
COMPONENT_SCHEMA_VERSION = 1
FORMAL_POSTMATCH_ARTIFACT_SCOPE = "fotmob_formal_postmatch"
FOTMOB_RESULT_PROVIDER_SCHEMA = "fotmob_league_results_v1"


def _safety(*, called_fotmob: bool = False, wrote: bool = False, notified: bool = False) -> dict[str, bool]:
    return {
        "read_env": False,
        "called_fotmob": called_fotmob,
        "wrote": wrote,
        "notified": notified,
    }


def _utc(value: datetime | str | None, *, default_now: bool = False) -> datetime:
    if value is None and default_now:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("league_postmatch_now_invalid") from None
    else:
        raise ValueError("league_postmatch_now_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("league_postmatch_now_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _saved_sample_matches(root: Path, evidence: Mapping[str, Any]) -> bool:
    configured = evidence.get("sample_path")
    if not fotmob_sample_path_is_sanitized(configured):
        return False
    relative = Path(str(configured))
    opened: list[int] = []
    try:
        root_resolved = root.resolve(strict=True)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        current = os.open(root_resolved, directory_flags)
        opened.append(current)
        for component in relative.parts[:-1]:
            metadata = os.stat(component, dir_fd=current, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode):
                return False
            current = os.open(component, directory_flags | nofollow, dir_fd=current)
            opened.append(current)
        filename = relative.parts[-1]
        before = os.stat(filename, dir_fd=current, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return False
        file_descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow,
            dir_fd=current,
        )
        opened.append(file_descriptor)
        after = os.fstat(file_descriptor)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return False
        digest_builder = hashlib.sha256()
        while chunk := os.read(file_descriptor, 1024 * 1024):
            digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
    except (OSError, ValueError):
        return False
    finally:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return digest == evidence.get("source_reference")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("league_postmatch_local_json_invalid") from None


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == encoded:
                return "unchanged"
        except OSError:
            raise ValueError("league_postmatch_local_json_invalid") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "stored"


def _load_acceptance(root: Path) -> dict[str, Any]:
    report = LeagueAcceptanceStore(root / ACCEPTANCE_RELATIVE_PATH).read()
    return report if isinstance(report, dict) else {"schema_version": 1, "competitions": {}}


def _load_runner_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = _read_json(path)
    required = {
        "schema_version",
        "statistics_scope",
        "aggregate_fingerprint",
        "previous_decided",
        "decided",
        "previous_settled_count",
        "settled_count",
        "newly_settled",
        "competitions",
    }
    schema_version = value.get("schema_version") if isinstance(value, Mapping) else None
    expected = required if schema_version == 1 else required | {
        "notification_date", "notification_transition_consumed",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != expected
        or schema_version not in {1, 2}
        or value.get("statistics_scope") != FORMAL_SCOPE
        or not isinstance(value.get("aggregate_fingerprint"), str)
        or len(value["aggregate_fingerprint"]) != 64
        or not isinstance(value.get("competitions"), Mapping)
    ):
        raise ValueError("league_postmatch_state_invalid")
    checked_counts: dict[str, int] = {}
    for key in (
        "previous_decided", "decided", "previous_settled_count", "settled_count", "newly_settled",
    ):
        count = value.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("league_postmatch_state_invalid")
        checked_counts[key] = count
    if (
        checked_counts["decided"] < checked_counts["previous_decided"]
        or checked_counts["settled_count"] < checked_counts["previous_settled_count"]
        or checked_counts["newly_settled"]
        != checked_counts["settled_count"] - checked_counts["previous_settled_count"]
    ):
        raise ValueError("league_postmatch_state_invalid")
    competitions: dict[str, dict[str, int]] = {}
    for competition_id, row in value["competitions"].items():
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not isinstance(row, Mapping):
            raise ValueError("league_postmatch_state_invalid")
        if set(row) != {"previous_settled_count", "settled_count", "newly_settled"}:
            raise ValueError("league_postmatch_state_invalid")
        counts = {}
        for key in row:
            count = row[key]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("league_postmatch_state_invalid")
            counts[key] = count
        if counts["settled_count"] < counts["previous_settled_count"] or counts["newly_settled"] != counts["settled_count"] - counts["previous_settled_count"]:
            raise ValueError("league_postmatch_state_invalid")
        competitions[competition_id] = counts
    notification_date: str | None = None
    transition_consumed = schema_version == 1
    if schema_version == 2:
        notification_date = value.get("notification_date")
        transition_consumed = value.get("notification_transition_consumed")
        if not isinstance(transition_consumed, bool):
            raise ValueError("league_postmatch_state_invalid")
        if notification_date is None:
            if not transition_consumed:
                raise ValueError("league_postmatch_state_invalid")
        elif isinstance(notification_date, str):
            try:
                if date.fromisoformat(notification_date).isoformat() != notification_date:
                    raise ValueError
            except ValueError:
                raise ValueError("league_postmatch_state_invalid") from None
        else:
            raise ValueError("league_postmatch_state_invalid")
    return {
        "schema_version": 2,
        "statistics_scope": FORMAL_SCOPE,
        "aggregate_fingerprint": value["aggregate_fingerprint"],
        **checked_counts,
        "competitions": competitions,
        "notification_date": notification_date,
        "notification_transition_consumed": transition_consumed,
    }


def _load_snapshots(root: Path, competition_id: str) -> list[dict[str, Any]]:
    history = root / "data/local/leagues" / competition_id / "history"
    if not history.exists():
        raise ValueError("history_missing")
    snapshots: list[dict[str, Any]] = []
    for path in sorted(history.glob("*.json")):
        if not path.is_file():
            continue
        value = _read_json(path)
        if not isinstance(value, dict):
            raise ValueError("history_invalid")
        competition = value.get("competition")
        if not isinstance(competition, Mapping) or competition.get("id") != competition_id:
            raise ValueError("history_partition_mismatch")
        snapshots.append(value)
    if not snapshots:
        raise ValueError("history_missing")
    return snapshots


def _fixture_rows(snapshots: list[dict[str, Any]], competition_id: str) -> list[dict[str, Any]]:
    selected: dict[str, tuple[datetime, dict[str, Any]]] = {}
    identities: dict[str, tuple[str, str, str]] = {}
    for snapshot in snapshots:
        snapshot_at = _utc(snapshot.get("snapshot_at"))
        matches = snapshot.get("matches")
        if not isinstance(matches, list):
            raise ValueError("history_invalid")
        for value in matches:
            if not isinstance(value, Mapping):
                raise ValueError("history_invalid")
            event_id = value.get("source_event_id")
            home = value.get("home_canonical")
            away = value.get("away_canonical")
            if not isinstance(event_id, str) or not event_id.strip() or not isinstance(home, str) or not home.strip() or not isinstance(away, str) or not away.strip():
                raise ValueError("history_identity_invalid")
            kickoff = _utc(value.get("kickoff_at_utc"))
            identity = (kickoff.isoformat(), home.strip(), away.strip())
            if event_id in identities and identities[event_id] != identity:
                raise ValueError("history_identity_conflict")
            identities[event_id] = identity
            row = {
                "source_event_id": event_id,
                "kickoff_at_utc": kickoff.isoformat(),
                "home_team": value.get("home_team"),
                "away_team": value.get("away_team"),
                "home_canonical": home.strip(),
                "away_canonical": away.strip(),
                "fixture_status": str(value.get("fixture_status") or value.get("status") or ""),
            }
            previous = selected.get(event_id)
            if previous is None or snapshot_at > previous[0]:
                selected[event_id] = (snapshot_at, row)
            elif snapshot_at == previous[0] and previous[1] != row:
                raise ValueError("history_snapshot_conflict")
    return [selected[event_id][1] for event_id in sorted(selected)]


def _active_inputs(
    root: Path,
    acceptance: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    rows = acceptance.get("competitions") if acceptance.get("schema_version") == 1 else None
    if not isinstance(rows, Mapping):
        raise ValueError("league_postmatch_acceptance_invalid")
    fixtures: dict[str, list[dict[str, Any]]] = {}
    snapshots: dict[str, list[dict[str, Any]]] = {}
    evidence: dict[str, dict[str, Any]] = {}
    blocked: dict[str, dict[str, str]] = {}
    registry = accepted_league_team_identity_registry()
    for competition_id in sorted(set(rows).intersection(FORMAL_SINGLE_MATCH_IDS)):
        row = rows[competition_id]
        if not acceptance_row_is_active(row, competition_id):
            continue
        evidence_path = fotmob_result_contract_evidence_path(root, competition_id)
        try:
            value = _read_json(evidence_path) if evidence_path.exists() else None
        except ValueError:
            value = None
        fingerprints = row.get("fingerprints") if isinstance(row, Mapping) else None
        if (
            not isinstance(value, dict)
            or not verify_result_contract_evidence(value, competition_id, provider_schema="fotmob_league_results_v1")
            or not isinstance(fingerprints, Mapping)
            or fingerprints.get("result_contract") != value.get("fingerprint")
            or not _saved_sample_matches(root, value)
        ):
            blocked[competition_id] = {"status": "blocked", "reason": "result_contract_evidence_invalid"}
            continue
        if fingerprints.get("team_identity") != league_team_identity_registry_fingerprint(
            registry, competition_id
        ):
            blocked[competition_id] = {"status": "blocked", "reason": "team_identity_evidence_invalid"}
            continue
        try:
            competition_snapshots = _load_snapshots(root, competition_id)
            competition_fixtures = _fixture_rows(competition_snapshots, competition_id)
        except ValueError as exc:
            reason = str(exc) if str(exc) in {
                "history_missing", "history_invalid", "history_partition_mismatch",
                "history_identity_invalid", "history_identity_conflict", "history_snapshot_conflict",
            } else "history_invalid"
            blocked[competition_id] = {"status": "blocked", "reason": reason}
            continue
        evidence[competition_id] = value
        snapshots[competition_id] = competition_snapshots
        fixtures[competition_id] = competition_fixtures
    return fixtures, snapshots, evidence, blocked


def _accepted_receipts(root: Path, competition_ids: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    receipts: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    for competition_id in sorted(competition_ids):
        path = root / "data/local/leagues" / competition_id / "results.json"
        try:
            receipts[competition_id] = _read_result_receipt(path, competition_id)
        except ValueError:
            invalid[competition_id] = "accepted_result_receipt_invalid"
    return receipts, invalid


def _project_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "due_count": len(plan.get("due") or []),
        "next_due_at": plan.get("next_due_at"),
        "competitions": {
            competition_id: {
                "fixture_count": int(row.get("fixture_count") or 0),
                "due_count": int(row.get("due_count") or 0),
                "blocked": dict(row.get("blocked") or {}),
            }
            for competition_id, row in sorted((plan.get("competitions") or {}).items())
            if competition_id in FORMAL_SINGLE_MATCH_IDS and isinstance(row, Mapping)
        },
    }


def _default_calendar_fetcher(_competition_id: str, date: str) -> Mapping[str, Any]:
    from worldcup.sources.league_fotmob_lineups import fetch_fotmob_calendar

    return fetch_fotmob_calendar(date=date)


def _default_detail_fetcher(_competition_id: str, event_id: str) -> Mapping[str, Any]:
    from worldcup.sources.league_fotmob_lineups import fetch_fotmob_details

    return fetch_fotmob_details(match_id=event_id)


def _default_notifier(content: str, summary: str) -> Mapping[str, Any]:
    from worldcup.notifications import send_wxpusher_notification

    return send_wxpusher_notification(content, summary=summary)


def _outbox_notifier(notifier: Notifier | None) -> Callable[..., Mapping[str, Any]]:
    sender = notifier or _default_notifier

    def send(content: str, *, summary: str) -> Mapping[str, Any]:
        return sender(content, summary)

    return send


def _load_postmatch(root: Path, competition_id: str) -> dict[str, Any] | None:
    path = root / "data/local/leagues" / competition_id / "postmatch.json"
    if not path.exists():
        return None
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("postmatch_existing_invalid")
    if (
        value.get("artifact_scope") == "legacy_theoddsapi_scores_compatibility"
        or value.get("result_provider_schema") == "theoddsapi_scores_v1"
        or not isinstance(value.get("accepted_result_receipts"), Mapping)
        or not isinstance(value.get("missing_closing_results"), Mapping)
        or not isinstance(value.get("missing_closing_event_ids"), list)
    ):
        raise ValueError("postmatch_existing_invalid")
    report = build_league_statistics([{
        **value,
        "_expected_partition_competition_id": competition_id,
    }])
    if (
        set(report["competitions"]) != {competition_id}
        or report["excluded_competitions"]
    ):
        raise ValueError("postmatch_existing_invalid")
    return value


def _validated_partition_component(
    root: Path, competition_id: str
) -> dict[str, Any]:
    path = root / "data/local/leagues" / competition_id / "postmatch.json"
    try:
        block = _read_json(path)
    except ValueError:
        raise ValueError("postmatch_partition_unreadable") from None
    if not isinstance(block, dict):
        raise ValueError("postmatch_partition_invalid")
    artifact_scope = block.get("artifact_scope")
    provider_schema = block.get("result_provider_schema")
    if (
        artifact_scope not in {None, FORMAL_POSTMATCH_ARTIFACT_SCOPE}
        or provider_schema not in {None, FOTMOB_RESULT_PROVIDER_SCHEMA}
        or not isinstance(block.get("accepted_result_receipts"), Mapping)
        or not isinstance(block.get("missing_closing_results"), Mapping)
        or not isinstance(block.get("missing_closing_event_ids"), list)
    ):
        raise ValueError("postmatch_partition_invalid")
    report = build_league_statistics([{
        **block,
        "_expected_partition_competition_id": competition_id,
    }])
    if (
        set(report["competitions"]) != {competition_id}
        or report["excluded_competitions"]
    ):
        raise ValueError("postmatch_partition_invalid")
    settled_event_ids = sorted(
        str(row["source_event_id"])
        for row in block["matches"]
    )
    result_event_ids = sorted(set(settled_event_ids).union(
        str(event_id) for event_id in block["missing_closing_event_ids"]
    ))
    return _component_entry(
        competition_id,
        status="fresh",
        reason=None,
        postmatch_fingerprint=_fingerprint(block),
        statistics=report["competitions"][competition_id],
        membership={
            "settled_event_ids": settled_event_ids,
            "result_event_ids": result_event_ids,
        },
    )


def _settled_count(row: Mapping[str, Any]) -> int:
    tally = row.get("decision_tally") if isinstance(row.get("decision_tally"), Mapping) else {}
    return sum(int(tally.get(key) or 0) for key in ("hit", "miss", "push", "no_pick"))


def _validate_statistics(
    statistics: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    *,
    expected_competitions: set[str],
) -> None:
    competitions = statistics.get("competitions")
    aggregate = statistics.get("aggregate")
    excluded = statistics.get("excluded_competitions")
    if (
        set(statistics) != {"statistics_scope", "competitions", "excluded_competitions", "aggregate"}
        or statistics.get("statistics_scope") != FORMAL_SCOPE
        or not isinstance(competitions, Mapping)
        or set(competitions) != expected_competitions
        or not isinstance(excluded, Mapping)
        or excluded
        or not isinstance(aggregate, Mapping)
    ):
        raise ValueError("league_postmatch_statistics_invalid")
    rows = {**competitions, "_aggregate": aggregate}
    for row in rows.values():
        if not isinstance(row, Mapping):
            raise ValueError("league_postmatch_statistics_invalid")
        tally = row.get("decision_tally")
        sample = row.get("decision_sample")
        coverage = row.get("decision_coverage")
        if not isinstance(tally, Mapping) or not isinstance(sample, Mapping) or not isinstance(coverage, Mapping):
            raise ValueError("league_postmatch_statistics_invalid")
        for key in ("hit", "miss", "push", "no_pick"):
            count = tally.get(key)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("league_postmatch_statistics_invalid")
        for key in ("decided", "decision_count"):
            count = sample.get(key)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("league_postmatch_statistics_invalid")
        finished = coverage.get("finished_result_count")
        if isinstance(finished, bool) or not isinstance(finished, int) or finished < 0:
            raise ValueError("league_postmatch_statistics_invalid")
    if previous is None:
        return
    previous_competitions = previous.get("competitions")
    previous_aggregate = previous.get("aggregate")
    if (
        previous.get("statistics_scope") != FORMAL_SCOPE
        or not isinstance(previous_competitions, Mapping)
        or not set(previous_competitions).issubset(competitions)
        or not isinstance(previous_aggregate, Mapping)
    ):
        raise ValueError("league_postmatch_statistics_invalid")
    comparisons = [
        (previous_competitions[competition_id], competitions[competition_id])
        for competition_id in previous_competitions
    ] + [(previous_aggregate, aggregate)]
    for old_row, new_row in comparisons:
        if (
            _settled_count(new_row) < _settled_count(old_row)
            or int(new_row["decision_sample"]["decided"]) < int(old_row["decision_sample"]["decided"])
            or int(new_row["decision_coverage"]["finished_result_count"])
            < int(old_row["decision_coverage"]["finished_result_count"])
        ):
            raise ValueError("league_postmatch_statistics_regression")


def _statistics_component(
    competition_id: str, value: Mapping[str, Any]
) -> dict[str, Any]:
    report = build_league_statistics_from_components({competition_id: value})
    return report["competitions"][competition_id]


def _component_entry(
    competition_id: str,
    *,
    status: str,
    reason: str | None,
    postmatch_fingerprint: str | None,
    statistics: Mapping[str, Any],
    membership: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "component_schema_version": COMPONENT_SCHEMA_VERSION,
        "competition_id": competition_id,
        "postmatch_schema_version": 2,
        "artifact_scope": FORMAL_POSTMATCH_ARTIFACT_SCOPE,
        "result_provider_schema": FOTMOB_RESULT_PROVIDER_SCHEMA,
        "status": status,
        "reason": reason,
        "postmatch_fingerprint": postmatch_fingerprint,
        "statistics": _statistics_component(competition_id, statistics),
        "membership": dict(membership) if membership is not None else None,
    }


def _validated_component_membership(value: Any) -> dict[str, list[str]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "settled_event_ids",
        "result_event_ids",
    }:
        raise ValueError("league_postmatch_component_membership_invalid")
    checked: dict[str, list[str]] = {}
    for key in ("settled_event_ids", "result_event_ids"):
        event_ids = value.get(key)
        if (
            not isinstance(event_ids, list)
            or any(not isinstance(event_id, str) or not event_id for event_id in event_ids)
            or event_ids != sorted(set(event_ids))
        ):
            raise ValueError("league_postmatch_component_membership_invalid")
        checked[key] = list(event_ids)
    if not set(checked["settled_event_ids"]).issubset(checked["result_event_ids"]):
        raise ValueError("league_postmatch_component_membership_invalid")
    return checked


def _component_regressed(
    previous: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    old_statistics = previous["statistics"]
    new_statistics = candidate["statistics"]
    for key in ("hit", "miss", "push", "no_pick"):
        if int(new_statistics["decision_tally"][key]) < int(
            old_statistics["decision_tally"][key]
        ):
            return True
    for key in ("decided", "actionable", "decision_count"):
        if int(new_statistics["decision_sample"][key]) < int(
            old_statistics["decision_sample"][key]
        ):
            return True
    for key in (
        "finished_result_count",
        "closing_available_count",
        "decision_available_count",
        "invalid_decision_count",
        "legacy_decision_count",
    ):
        if int(new_statistics["decision_coverage"][key]) < int(
            old_statistics["decision_coverage"][key]
        ):
            return True
    old_membership = previous.get("membership")
    new_membership = candidate.get("membership")
    if isinstance(old_membership, Mapping):
        if not isinstance(new_membership, Mapping):
            return True
        for key in ("settled_event_ids", "result_event_ids"):
            if not set(old_membership[key]).issubset(new_membership[key]):
                return True
    return False


def _previous_statistics_components(
    previous_statistics: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if previous_statistics is None:
        return {}
    competitions = previous_statistics.get("competitions")
    if not isinstance(competitions, Mapping):
        raise ValueError("league_postmatch_statistics_invalid")
    expected = {str(competition_id) for competition_id in competitions}
    _validate_statistics(
        previous_statistics,
        None,
        expected_competitions=expected,
    )
    return {
        competition_id: _component_entry(
            competition_id,
            status="stale",
            reason="component_manifest_upgrade",
            postmatch_fingerprint=None,
            statistics=row,
            membership=None,
        )
        for competition_id, row in competitions.items()
    }


def _load_statistics_components(
    path: Path,
    previous_statistics: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    fallback = _previous_statistics_components(previous_statistics)
    if not path.exists():
        return fallback
    try:
        payload = _read_json(path)
    except ValueError:
        return fallback
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != 1
        or payload.get("statistics_scope") != FORMAL_SCOPE
        or not isinstance(payload.get("components"), Mapping)
    ):
        return fallback
    checked = dict(fallback)
    for raw_competition_id, raw_entry in payload["components"].items():
        competition_id = str(raw_competition_id)
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not isinstance(raw_entry, Mapping):
            continue
        status = raw_entry.get("status")
        reason = raw_entry.get("reason")
        fingerprint = raw_entry.get("postmatch_fingerprint")
        contract_fields = {
            "component_schema_version",
            "competition_id",
            "postmatch_schema_version",
            "artifact_scope",
            "result_provider_schema",
            "membership",
        }
        has_component_contract = any(key in raw_entry for key in contract_fields)
        if (
            status not in {"fresh", "stale"}
            or (status == "fresh" and reason is not None)
            or (status == "fresh" and fingerprint is None)
            or (status == "stale" and (not isinstance(reason, str) or not reason))
            or (
                fingerprint is not None
                and (
                    not isinstance(fingerprint, str)
                    or len(fingerprint) != 64
                    or any(character not in "0123456789abcdef" for character in fingerprint)
                )
            )
            or not isinstance(raw_entry.get("statistics"), Mapping)
        ):
            continue
        if has_component_contract and (
            raw_entry.get("component_schema_version") != COMPONENT_SCHEMA_VERSION
            or raw_entry.get("competition_id") != competition_id
            or raw_entry.get("postmatch_schema_version") != 2
            or raw_entry.get("artifact_scope") != FORMAL_POSTMATCH_ARTIFACT_SCOPE
            or raw_entry.get("result_provider_schema") != FOTMOB_RESULT_PROVIDER_SCHEMA
        ):
            continue
        try:
            statistics = _statistics_component(
                competition_id,
                raw_entry["statistics"],
            )
            membership = _validated_component_membership(
                raw_entry.get("membership") if has_component_contract else None
            )
            if has_component_contract and status == "fresh" and membership is None:
                raise ValueError("league_postmatch_component_membership_invalid")
        except ValueError:
            continue
        checked[competition_id] = _component_entry(
            competition_id,
            status=status,
            reason=reason,
            postmatch_fingerprint=fingerprint,
            statistics=statistics,
            membership=membership,
        )
    return checked


def _collect_statistics_components(
    root: Path,
    previous_statistics: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    component_path = root / COMPONENTS_RELATIVE_PATH
    previous_components = _load_statistics_components(
        component_path,
        previous_statistics,
    )
    competition_ids = set(previous_components)
    competition_ids.update(
        competition_id
        for competition_id in FORMAL_SINGLE_MATCH_IDS
        if (root / "data/local/leagues" / competition_id / "postmatch.json").exists()
    )
    components: dict[str, dict[str, Any]] = {}
    issues: dict[str, dict[str, Any]] = {}
    for competition_id in sorted(competition_ids):
        path = root / "data/local/leagues" / competition_id / "postmatch.json"
        previous = previous_components.get(competition_id)
        if path.exists():
            try:
                candidate = _validated_partition_component(
                    root,
                    competition_id,
                )
                if previous is None or not _component_regressed(previous, candidate):
                    components[competition_id] = candidate
                    continue
                reason = "postmatch_partition_regression"
            except ValueError as exc:
                reason = str(exc)
                if reason not in {
                    "postmatch_partition_unreadable",
                    "postmatch_partition_invalid",
                }:
                    reason = "postmatch_partition_invalid"
        else:
            reason = "postmatch_partition_missing"
        if previous is None:
            issues[competition_id] = {
                "status": "blocked",
                "reason": reason,
                "using_last_known_good": False,
            }
            continue
        components[competition_id] = _component_entry(
            competition_id,
            status="stale",
            reason=reason,
            postmatch_fingerprint=previous.get("postmatch_fingerprint"),
            statistics=previous["statistics"],
            membership=previous.get("membership"),
        )
        issues[competition_id] = {
            "status": "stale",
            "reason": reason,
            "using_last_known_good": True,
        }
    statistics = build_league_statistics_from_components({
        competition_id: entry["statistics"]
        for competition_id, entry in components.items()
    })
    manifest = {
        "schema_version": 1,
        "statistics_scope": FORMAL_SCOPE,
        "components": {
            competition_id: components[competition_id]
            for competition_id in sorted(components)
        },
    }
    return statistics, manifest, issues


def _state_for_statistics(
    statistics: Mapping[str, Any],
    old: dict[str, Any] | None,
    *,
    notification_date: str,
) -> dict[str, Any]:
    aggregate_fingerprint = _fingerprint(statistics)
    if old is not None and old["aggregate_fingerprint"] == aggregate_fingerprint:
        return old
    if old is not None and not old["notification_transition_consumed"]:
        raise ValueError("league_postmatch_notification_transition_unconsumed")
    aggregate = statistics["aggregate"]
    current_decided = int(aggregate["decision_sample"]["decided"])
    current_settled = _settled_count(aggregate)
    previous_decided = int(old["decided"]) if old is not None else 0
    previous_settled = int(old["settled_count"]) if old is not None else 0
    if current_decided < previous_decided or current_settled < previous_settled:
        raise ValueError("league_postmatch_state_regression")
    old_competitions = old.get("competitions", {}) if old is not None else {}
    if not set(old_competitions).issubset(statistics["competitions"]):
        raise ValueError("league_postmatch_state_regression")
    competitions: dict[str, dict[str, int]] = {}
    for competition_id, row in statistics["competitions"].items():
        current = _settled_count(row)
        previous = int((old_competitions.get(competition_id) or {}).get("settled_count") or 0)
        if current < previous:
            raise ValueError("league_postmatch_state_regression")
        competitions[competition_id] = {
            "previous_settled_count": previous,
            "settled_count": current,
            "newly_settled": current - previous,
        }
    return {
        "schema_version": 2,
        "statistics_scope": FORMAL_SCOPE,
        "aggregate_fingerprint": aggregate_fingerprint,
        "previous_decided": previous_decided,
        "decided": current_decided,
        "previous_settled_count": previous_settled,
        "settled_count": current_settled,
        "newly_settled": current_settled - previous_settled,
        "competitions": competitions,
        "notification_date": notification_date,
        "notification_transition_consumed": False,
    }


def _notification_competitions(statistics: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    projected: dict[str, dict[str, int]] = {}
    for competition_id, row in statistics["competitions"].items():
        tally = row["decision_tally"]
        projected[competition_id] = {
            "hit": int(tally["hit"]),
            "miss": int(tally["miss"]),
            "push": int(tally["push"]),
            "no_pick": int(tally["no_pick"]),
            "newly_settled": int(state["competitions"][competition_id]["newly_settled"]),
            "missing_closing": int(row["decision_coverage"]["missing_closing_count"]),
            "decided": int(row["decision_sample"]["decided"]),
        }
    return projected


def _result_subset(
    parsed: Mapping[str, Any],
    due_rows: list[dict[str, str]],
    competition_id: str,
) -> dict[str, Any]:
    if parsed.get("competition_id") != competition_id:
        raise ValueError("result_due_identity_mismatch")
    due = {row["source_event_id"]: row for row in due_rows}
    results: list[dict[str, Any]] = []
    parsed_results = parsed.get("results")
    if not isinstance(parsed_results, list):
        raise ValueError("result_due_identity_mismatch")
    for value in parsed_results:
        if not isinstance(value, Mapping) or value.get("source_event_id") not in due:
            raise ValueError("result_due_identity_mismatch")
        row = dict(value)
        expected = due[row["source_event_id"]]
        try:
            actual_identity = (
                row.get("competition_id"),
                _utc(row.get("kickoff_at_utc")).isoformat(),
                str(row.get("home_canonical") or "").strip(),
                str(row.get("away_canonical") or "").strip(),
            )
        except ValueError:
            raise ValueError("result_due_identity_mismatch") from None
        expected_identity = (
            competition_id,
            _utc(expected["kickoff_at_utc"]).isoformat(),
            expected["home_canonical"],
            expected["away_canonical"],
        )
        if actual_identity != expected_identity:
            raise ValueError("result_due_identity_mismatch")
        results.append(row)
    pending = [
        dict(row) for row in parsed.get("pending", [])
        if isinstance(row, Mapping) and row.get("source_event_id") in due
    ]
    return {"competition_id": competition_id, "results": results, "pending": pending, "source_events": []}


def _notification_events(
    statistics: Mapping[str, Any],
    state: Mapping[str, Any],
    outbox: LeaguePostmatchNotificationOutbox,
) -> list[dict[str, Any]]:
    settlement_date = state.get("notification_date")
    if not isinstance(settlement_date, str):
        raise ValueError("league_postmatch_state_invalid")
    events: list[dict[str, Any]] = []
    daily = build_daily_settlement_event(
        settlement_date=settlement_date,
        newly_settled=state["newly_settled"],
        competitions=_notification_competitions(statistics, state),
        aggregate_fingerprint=state["aggregate_fingerprint"],
    )
    if daily is not None:
        events.append(daily)
    events.extend(build_threshold_events(
        previous_decided=state["previous_decided"],
        current_decided=state["decided"],
        sent_thresholds=outbox.sent_thresholds(),
        aggregate_fingerprint=state["aggregate_fingerprint"],
    ))
    return events


def _persist_notification_transition(
    *,
    statistics: Mapping[str, Any],
    state: dict[str, Any],
    state_path: Path,
    notification_path: Path,
    notify: bool,
    notifier: Notifier | None,
) -> dict[str, Any]:
    deliveries: list[dict[str, str]] = []
    wrote = False
    notified = False
    if not notify:
        try:
            consumed = {**state, "notification_transition_consumed": True}
            state_status = _write_json_atomic(state_path, consumed)
        except Exception:
            return {
                "status": "error",
                "reason": "notification_intent_commit_failed",
                "notifications": [],
                "wrote": False,
                "notified": False,
            }
        return {
            "status": "complete",
            "notifications": [],
            "wrote": state_status == "stored",
            "notified": False,
        }
    outbox = LeaguePostmatchNotificationOutbox(notification_path, _outbox_notifier(notifier))
    try:
        events = _notification_events(statistics, state, outbox)
        for event in events:
            delivery = outbox.deliver(event)
            delivery_status = str(delivery.get("status") or "failed")
            projected_status = "pending" if not notify and delivery_status == "failed" else delivery_status
            deliveries.append({"event_type": event["event_type"], "status": projected_status})
            wrote = wrote or delivery_status in {"sent", "failed"}
            notified = notified or delivery_status == "sent"
        consumed = {**state, "notification_transition_consumed": True}
        state_status = _write_json_atomic(state_path, consumed)
        wrote = wrote or state_status == "stored"
    except Exception:
        return {
            "status": "error",
            "reason": "notification_intent_commit_failed",
            "notifications": deliveries,
            "wrote": wrote,
            "notified": notified,
        }
    pending = any(row["status"] in {"pending", "failed", "already_pending"} for row in deliveries)
    return {
        "status": "pending" if pending else "complete",
        "notifications": deliveries,
        "wrote": wrote,
        "notified": notified,
    }


def _recover_notification_transition(
    *,
    root: Path,
    state: dict[str, Any],
    state_path: Path,
    notification_path: Path,
    notify: bool,
    notifier: Notifier | None,
) -> dict[str, Any]:
    statistics_path = root / STATISTICS_RELATIVE_PATH
    try:
        statistics = _read_json(statistics_path)
        if not isinstance(statistics, Mapping) or _fingerprint(statistics) != state["aggregate_fingerprint"]:
            raise ValueError
        _validate_statistics(
            statistics,
            None,
            expected_competitions=set(state["competitions"]),
        )
        persisted = _persist_notification_transition(
            statistics=statistics,
            state=state,
            state_path=state_path,
            notification_path=notification_path,
            notify=notify,
            notifier=notifier,
        )
    except Exception:
        return {
            "status": "error",
            "mode": "live",
            "reason": "notification_transition_recovery_failed",
            "notifications": [],
            "safety": _safety(),
        }
    if persisted["status"] == "error":
        return {
            "status": "error",
            "mode": "live",
            "reason": persisted["reason"],
            "notifications": persisted["notifications"],
            "safety": _safety(wrote=persisted["wrote"], notified=persisted["notified"]),
        }
    return {
        "status": "notification_pending" if persisted["status"] == "pending" else "notification_recovered",
        "mode": "live",
        "reason": "notify_not_enabled" if not notify and persisted["status"] == "pending" else None,
        "notifications": persisted["notifications"],
        "safety": _safety(wrote=persisted["wrote"], notified=persisted["notified"]),
    }


def run_league_postmatch(
    root: Path,
    *,
    live: bool = False,
    write: bool = False,
    notify: bool = False,
    now: datetime | None = None,
    calendar_fetcher: CalendarFetcher | None = None,
    detail_fetcher: DetailFetcher | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Run a local six-league postmatch cycle; only exact live+write may mutate state."""
    root_path = Path(root)
    now_dt = _utc(now, default_now=True)
    if not live:
        if write or notify:
            return {
                "status": "blocked",
                "mode": "blocked",
                "reason": "live_write_flags_required",
                "safety": _safety(),
            }
        try:
            acceptance = _load_acceptance(root_path)
        except ValueError:
            return {
                "status": "blocked",
                "mode": "dry_run",
                "reason": "local_inputs_invalid",
                "errors": [{"scope": "acceptance", "reason": "acceptance_invalid"}],
                "plan": _project_plan({"due": [], "next_due_at": None, "competitions": {}}),
                "competitions": {},
                "safety": _safety(),
            }
        try:
            fixtures, _snapshots, _evidence, blocked = _active_inputs(root_path, acceptance)
            receipts, invalid_receipts = _accepted_receipts(root_path, set(fixtures))
            for competition_id, reason in invalid_receipts.items():
                blocked[competition_id] = {"status": "blocked", "reason": reason}
            fixtures = {key: value for key, value in fixtures.items() if key not in invalid_receipts}
            plan = plan_league_postmatch(acceptance, fixtures, {"accepted_results": receipts}, now=now_dt)
        except ValueError:
            return {
                "status": "blocked",
                "mode": "dry_run",
                "reason": "local_inputs_invalid",
                "errors": [{"scope": "local", "reason": "local_input_invalid"}],
                "plan": _project_plan({"due": [], "next_due_at": None, "competitions": {}}),
                "competitions": {},
                "safety": _safety(),
            }
        return {
            "status": "blocked" if blocked and not fixtures else "dry_run",
            "mode": "dry_run",
            "plan": _project_plan(plan),
            "competitions": blocked,
            "safety": _safety(),
        }
    if not write:
        return {
            "status": "blocked",
            "mode": "blocked",
            "reason": "live_write_flags_required",
            "safety": _safety(),
        }

    lock_path = root_path / LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "locked", "mode": "live", "safety": _safety()}

        state_path = root_path / STATE_RELATIVE_PATH
        notification_path = root_path / NOTIFICATION_STATE_RELATIVE_PATH
        try:
            notification_state = _read_notification_state(notification_path)
        except ValueError:
            return {
                "status": "blocked",
                "mode": "live",
                "reason": "local_state_invalid",
                "safety": _safety(),
            }

        pending_count = len(notification_state["pending"])
        if pending_count:
            if not notify:
                return {
                    "status": "notification_pending",
                    "mode": "live",
                    "reason": "notify_not_enabled",
                    "pending_count": pending_count,
                    "safety": _safety(),
                }
            outbox = LeaguePostmatchNotificationOutbox(notification_path, _outbox_notifier(notifier))
            retry = outbox.retry_pending()
            return {
                "status": "notification_retried" if retry["failed"] == 0 else "notification_pending",
                "mode": "live",
                "notification_retry": retry,
                "safety": _safety(wrote=pending_count > 0, notified=retry["sent"] > 0),
            }

        try:
            old_state = _load_runner_state(state_path)
        except ValueError:
            return {
                "status": "blocked",
                "mode": "live",
                "reason": "local_state_invalid",
                "safety": _safety(),
            }
        if old_state is not None and not old_state["notification_transition_consumed"]:
            return _recover_notification_transition(
                root=root_path,
                state=old_state,
                state_path=state_path,
                notification_path=notification_path,
                notify=notify,
                notifier=notifier,
            )

        try:
            acceptance = _load_acceptance(root_path)
        except ValueError:
            return {
                "status": "blocked",
                "mode": "live",
                "reason": "local_inputs_invalid",
                "safety": _safety(),
            }

        try:
            fixtures, snapshots, evidence, blocked = _active_inputs(root_path, acceptance)
            receipts, invalid_receipts = _accepted_receipts(root_path, set(fixtures))
        except ValueError:
            return {
                "status": "blocked",
                "mode": "live",
                "reason": "local_inputs_invalid",
                "safety": _safety(),
            }
        for competition_id, reason in invalid_receipts.items():
            blocked[competition_id] = {"status": "blocked", "reason": reason}
            fixtures.pop(competition_id, None)
            snapshots.pop(competition_id, None)
            evidence.pop(competition_id, None)
            receipts.pop(competition_id, None)
        plan = plan_league_postmatch(acceptance, fixtures, {"accepted_results": receipts}, now=now_dt)
        outcomes: dict[str, dict[str, Any]] = dict(blocked)
        called_fotmob = False
        wrote_any = False
        provider_touched: set[str] = set()
        provider_failed: set[str] = set()
        result_added: dict[str, int] = defaultdict(int)
        provider_results: dict[str, int] = defaultdict(int)
        provider_pending: dict[str, dict[str, str]] = defaultdict(dict)
        provider_source_errors: dict[str, set[str]] = defaultdict(set)

        grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
        for row in plan["due"]:
            kickoff = _utc(row["kickoff_at_utc"])
            grouped[(row["competition_id"], kickoff.strftime("%Y%m%d"))].append(row)
        calendar_fn = calendar_fetcher or _default_calendar_fetcher
        detail_fn = detail_fetcher or _default_detail_fetcher
        registry = accepted_league_team_identity_registry()
        for (competition_id, date), due_rows in sorted(grouped.items()):
            if competition_id in provider_failed:
                continue
            provider_touched.add(competition_id)
            called_fotmob = True
            try:
                calendar_payload = calendar_fn(competition_id, date)
                if not isinstance(calendar_payload, Mapping):
                    raise TypeError
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "calendar_fetch_failed"}
                provider_failed.add(competition_id)
                continue
            details: dict[str, dict[str, Any]] = {}
            detail_failures: set[str] = set()
            for row in due_rows:
                try:
                    value = detail_fn(competition_id, row["source_event_id"])
                    if not isinstance(value, Mapping):
                        raise TypeError
                    details[row["source_event_id"]] = dict(value)
                except Exception:
                    detail_failures.add(row["source_event_id"])
            try:
                parsed = parse_fotmob_league_results(
                    dict(calendar_payload),
                    details,
                    competition_id,
                    result_contract_evidence=evidence[competition_id],
                    identity_registry=registry,
                    captured_at=now_dt,
                )
                subset = _result_subset(parsed, due_rows, competition_id)
            except Exception as exc:
                reason = (
                    "result_due_identity_mismatch"
                    if isinstance(exc, ValueError) and str(exc) == "result_due_identity_mismatch"
                    else "result_parse_failed"
                )
                outcomes[competition_id] = {"status": "error", "reason": reason}
                provider_failed.add(competition_id)
                continue
            provider_results[competition_id] += len(subset["results"])
            for pending_row in subset["pending"]:
                event_id = str(pending_row.get("source_event_id") or "")
                if not event_id:
                    continue
                reason = "details_fetch_failed" if event_id in detail_failures else str(
                    pending_row.get("reason") or "terminal_result_not_verified"
                )
                provider_pending[competition_id][event_id] = reason
            for event_id in detail_failures:
                provider_pending[competition_id][event_id] = "details_fetch_failed"
                provider_source_errors[competition_id].add(event_id)
            try:
                merge_result = LeagueResultStore(
                    root_path / "data/local/leagues" / competition_id / "results.json"
                ).merge(subset)
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "result_commit_failed"}
                provider_failed.add(competition_id)
                continue
            if merge_result["status"] == "conflict":
                outcomes[competition_id] = {
                    "status": "conflict",
                    "reason": "result_evidence_conflict",
                    "conflict_count": len(merge_result["conflicts"]),
                }
                provider_failed.add(competition_id)
                continue
            if merge_result["status"] == "stored":
                wrote_any = True
            result_added[competition_id] += int(merge_result["added"])
            if not subset["results"]:
                outcomes[competition_id] = {
                    "status": "pending",
                    "reason": "details_fetch_failed" if detail_failures else "terminal_result_not_verified",
                }

        newly_settled_total = 0
        derivative_changed = False
        for competition_id in sorted(fixtures):
            if competition_id in provider_failed:
                continue
            result_path = root_path / "data/local/leagues" / competition_id / "results.json"
            if not result_path.exists():
                outcomes.setdefault(competition_id, {"status": "pending" if competition_id in provider_touched else "no_due"})
                continue
            try:
                receipt = _read_result_receipt(result_path, competition_id)
                if not receipt["results"]:
                    outcomes.setdefault(competition_id, {"status": "pending"})
                    continue
                closing_path = root_path / "data/local/leagues" / competition_id / "closing.json"
                closing = select_league_closings(snapshots[competition_id], competition_id)
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "closing_build_failed"}
                continue
            try:
                closing_status = LeagueClosingStore(closing_path).merge(closing)
                wrote_any = wrote_any or closing_status == "stored"
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "closing_commit_failed"}
                continue
            try:
                committed_closing = _read_json(closing_path)
                existing_postmatch = _load_postmatch(root_path, competition_id)
                previous_count = len(existing_postmatch.get("matches") or []) if existing_postmatch is not None else 0
                postmatch = merge_league_postmatch(existing_postmatch, committed_closing, receipt, competition_id)
                postmatch = {
                    **postmatch,
                    "artifact_scope": FORMAL_POSTMATCH_ARTIFACT_SCOPE,
                    "result_provider_schema": FOTMOB_RESULT_PROVIDER_SCHEMA,
                }
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "postmatch_build_failed"}
                continue
            try:
                postmatch_status = _write_json_atomic(
                    root_path / "data/local/leagues" / competition_id / "postmatch.json",
                    postmatch,
                )
                wrote_any = wrote_any or postmatch_status == "stored"
                derivative_changed = derivative_changed or postmatch_status == "stored"
            except Exception:
                outcomes[competition_id] = {"status": "error", "reason": "postmatch_commit_failed"}
                continue
            newly_settled = len(postmatch["matches"]) - previous_count
            newly_settled_total += newly_settled
            pending_rows = [
                {"source_event_id": event_id, "reason": provider_pending[competition_id][event_id]}
                for event_id in sorted(provider_pending[competition_id])
            ]
            if pending_rows:
                outcomes[competition_id] = {
                    "status": "partial",
                    "newly_settled": newly_settled,
                    "result_count": provider_results[competition_id],
                    "pending_count": len(pending_rows),
                    "source_error_count": len(provider_source_errors[competition_id]),
                    "pending": pending_rows,
                }
            elif newly_settled:
                outcomes[competition_id] = {"status": "settled", "newly_settled": newly_settled}
            elif result_added[competition_id]:
                outcomes[competition_id] = {
                    "status": "stored",
                    "newly_settled": 0,
                    "missing_closing": int(postmatch["decision_coverage"]["missing_closing_count"]),
                }
            else:
                outcomes.setdefault(competition_id, {"status": "unchanged"})

        try:
            statistics_path = root_path / STATISTICS_RELATIVE_PATH
            previous_statistics = _read_json(statistics_path) if statistics_path.exists() else None
            if previous_statistics is not None and not isinstance(previous_statistics, Mapping):
                raise ValueError("league_postmatch_statistics_invalid")
            statistics, component_manifest, partition_issues = _collect_statistics_components(
                root_path,
                previous_statistics,
            )
        except ValueError:
            return {
                "status": "error",
                "mode": "live",
                "reason": "statistics_build_failed",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }
        outcomes.update(partition_issues)
        if not statistics["competitions"] and previous_statistics is None:
            has_errors = any(row.get("status") in {"error", "conflict"} for row in outcomes.values())
            status = "error" if has_errors else "pending" if provider_touched else "no_due"
            result = {
                "status": status,
                "mode": "live",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }
            if has_errors:
                reasons = {
                    str(row.get("reason"))
                    for row in outcomes.values()
                    if row.get("status") in {"error", "conflict"} and row.get("reason")
                }
                if len(reasons) == 1:
                    result["reason"] = reasons.pop()
            return result
        try:
            _validate_statistics(
                statistics,
                previous_statistics,
                expected_competitions=set(statistics["competitions"]),
            )
            new_state = _state_for_statistics(
                statistics,
                old_state,
                notification_date=now_dt.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            )
            transition_created = (
                old_state is None
                or old_state["aggregate_fingerprint"] != new_state["aggregate_fingerprint"]
            )
        except Exception:
            return {
                "status": "error",
                "mode": "live",
                "reason": "statistics_validation_failed",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }
        try:
            components_status = _write_json_atomic(
                root_path / COMPONENTS_RELATIVE_PATH,
                component_manifest,
            )
            wrote_any = wrote_any or components_status == "stored"
        except Exception:
            return {
                "status": "error",
                "mode": "live",
                "reason": "components_commit_failed",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }
        try:
            statistics_status = _write_json_atomic(statistics_path, statistics)
            wrote_any = wrote_any or statistics_status == "stored"
        except Exception:
            return {
                "status": "error",
                "mode": "live",
                "reason": "statistics_commit_failed",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }
        try:
            state_status = _write_json_atomic(state_path, new_state)
            wrote_any = wrote_any or state_status == "stored"
        except Exception:
            return {
                "status": "error",
                "mode": "live",
                "reason": "state_commit_failed",
                "competitions": outcomes,
                "plan": _project_plan(plan),
                "safety": _safety(called_fotmob=called_fotmob, wrote=wrote_any),
            }

        notification_results: list[dict[str, str]] = []
        notification_succeeded = False
        if not new_state["notification_transition_consumed"]:
            persisted = _persist_notification_transition(
                statistics=statistics,
                state=new_state,
                state_path=state_path,
                notification_path=notification_path,
                notify=notify,
                notifier=notifier,
            )
            notification_results = persisted["notifications"]
            wrote_any = wrote_any or persisted["wrote"]
            notification_succeeded = persisted["notified"]
            if persisted["status"] == "error":
                return {
                    "status": "error",
                    "mode": "live",
                    "reason": persisted["reason"],
                    "competitions": outcomes,
                    "plan": _project_plan(plan),
                    "notifications": notification_results,
                    "safety": _safety(
                        called_fotmob=called_fotmob,
                        wrote=wrote_any,
                        notified=notification_succeeded,
                    ),
                }

        has_errors = any(row.get("status") in {"error", "conflict"} for row in outcomes.values())
        has_partial = any(row.get("status") == "partial" for row in outcomes.values())
        reported_newly_settled = (
            new_state["newly_settled"] if transition_created else newly_settled_total
        )
        if reported_newly_settled:
            status = "partial" if has_errors or has_partial else "settled"
        elif has_errors:
            status = "error"
        elif has_partial:
            status = "partial"
        elif result_added or derivative_changed:
            status = "stored"
        elif provider_touched:
            status = "pending"
        else:
            status = "no_due"
        return {
            "status": status,
            "mode": "live",
            "competitions": outcomes,
            "plan": _project_plan(plan),
            "statistics": {
                "statistics_scope": FORMAL_SCOPE,
                "aggregate_fingerprint": new_state["aggregate_fingerprint"],
                "newly_settled": reported_newly_settled,
                "decided": new_state["decided"],
            },
            "notifications": notification_results,
            "safety": _safety(
                called_fotmob=called_fotmob,
                wrote=wrote_any,
                notified=notification_succeeded,
            ),
        }
    finally:
        lock.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the six-league FotMob postmatch loop.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--now", default=None, help="Dry-run replay time; forbidden with --live.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.live and args.now is not None:
        parser.error("--now cannot be combined with --live")
    parsed_now = _utc(args.now) if args.now is not None else None
    result = run_league_postmatch(
        Path(args.root),
        live=args.live,
        write=args.write,
        notify=args.notify,
        now=parsed_now,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] not in {"blocked", "error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
