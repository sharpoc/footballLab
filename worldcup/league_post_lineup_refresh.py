from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, formal_single_match_competitions
from worldcup.league_acceptance import acceptance_fingerprint, acceptance_row_is_active
from worldcup.league_batch_runner import run_planned_league_refresh
from worldcup.league_live_store import LeagueLiveStore
from worldcup.league_lineups_refresh import (
    _atomic_write_pending,
    _pending_path,
    _read_pending,
)
from worldcup.league_scheduled_publish import publish_committed_league_snapshots
from worldcup.league_team_identity import LeagueTeamIdentityRegistry
from worldcup.observed_clock import MonotonicUtcClock
from worldcup.theoddsapi_keys import LOW_QUOTA_SWITCH_THRESHOLD, configured_key_slots


AckItem = dict[str, Any]
QUOTA_LEDGER_MAX_AGE_SECONDS = 3600
_TERMINAL_FIXTURE_STATUSES = frozenset({
    "POSTPONED",
    "CANCELLED",
    "CANCELED",
    "STARTED",
    "LIVE",
    "IN_PROGRESS",
    "FINISHED",
})


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("post_lineup_datetime_invalid") from None
    else:
        raise ValueError("post_lineup_datetime_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("post_lineup_datetime_invalid")
    return parsed.astimezone(timezone.utc)


def _ack_token(ack_key: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(ack_key), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _valid_fingerprint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _attempt_snapshot_id(
    competition_id: str,
    observed_at: str,
    tokens: Sequence[str],
) -> str:
    payload = json.dumps(
        {
            "competition_id": competition_id,
            "observed_at": _utc(observed_at).isoformat(),
            "tokens": sorted(tokens),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"league-attempt-{digest}"


def _sport_key(competition_id: str) -> str:
    profiles = {
        profile.id: profile.theoddsapi_sport_key
        for profile in formal_single_match_competitions()
    }
    value = profiles.get(competition_id)
    if not isinstance(value, str) or not value:
        raise ValueError("post_lineup_competition_invalid")
    return value


def _without_provider_events(payload: Any, forbidden_event_ids: set[str]) -> Any:
    if not forbidden_event_ids or not isinstance(payload, list):
        return payload
    retained = []
    for row in payload:
        raw_id = row.get("id") if isinstance(row, Mapping) else None
        event_id = str(raw_id) if isinstance(raw_id, (str, int)) else None
        if event_id not in forbidden_event_ids:
            retained.append(row)
    return retained


def _normalize_receipts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping) or not set(value).issubset(FORMAL_SINGLE_MATCH_IDS):
        raise ValueError("post_lineup_receipts_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    identities: dict[tuple[str, str], str] = {}
    expected = {
        "event_id",
        "source_match_id",
        "kickoff_at_utc",
        "fetched_at",
        "lineup_fingerprint",
        "ack_key",
    }
    for competition_id, rows in value.items():
        if not isinstance(rows, list):
            raise ValueError("post_lineup_receipts_invalid")
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != expected:
                raise ValueError("post_lineup_receipts_invalid")
            event_id = row.get("event_id")
            source_match_id = row.get("source_match_id")
            fingerprint = row.get("lineup_fingerprint")
            ack_key = row.get("ack_key")
            if (
                not isinstance(event_id, str)
                or not event_id.strip()
                or not isinstance(source_match_id, str)
                or not source_match_id.strip()
                or not _valid_fingerprint(fingerprint)
                or not isinstance(ack_key, Mapping)
                or set(ack_key) != {"competition_id", "event_id", "lineup_fingerprint"}
                or ack_key.get("competition_id") != competition_id
                or ack_key.get("event_id") != event_id
                or ack_key.get("lineup_fingerprint") != fingerprint
            ):
                raise ValueError("post_lineup_receipts_invalid")
            identity = (competition_id, event_id)
            previous = identities.get(identity)
            if previous is not None and previous != fingerprint:
                raise ValueError("post_lineup_receipt_conflict")
            identities[identity] = fingerprint
            checked_ack = {
                "competition_id": competition_id,
                "event_id": event_id,
                "lineup_fingerprint": fingerprint,
            }
            token = _ack_token(checked_ack)
            normalized[token] = {
                "competition_id": competition_id,
                "event_id": event_id,
                "source_match_id": source_match_id,
                "kickoff_at_utc": _utc(row.get("kickoff_at_utc")).isoformat(),
                "fetched_at": _utc(row.get("fetched_at")).isoformat(),
                "lineup_fingerprint": fingerprint,
                "ack_key": checked_ack,
                "token": token,
            }
    return sorted(
        normalized.values(),
        key=lambda row: (row["competition_id"], row["event_id"], row["lineup_fingerprint"]),
    )


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "receipts"}:
        raise ValueError("post_lineup_refresh_state_invalid")
    if value.get("schema_version") != 1 or not isinstance(value.get("receipts"), Mapping):
        raise ValueError("post_lineup_refresh_state_invalid")
    checked: dict[str, dict[str, Any]] = {}
    for token, row in value["receipts"].items():
        if not isinstance(token, str) or not isinstance(row, Mapping):
            raise ValueError("post_lineup_refresh_state_invalid")
        phase = row.get("phase")
        if phase not in {"refresh_started", "committed", "published"}:
            raise ValueError("post_lineup_refresh_state_invalid")
        legacy = phase in {"committed", "published"} and not {
            "observed_at", "acceptance_fingerprint"
        }.intersection(row)
        legacy_started = phase == "refresh_started" and not {
            "attempt_id", "attempt_receipts"
        }.intersection(row)
        expected = {"ack_key", "phase"}
        if phase == "refresh_started":
            expected.update({"observed_at", "acceptance_fingerprint"})
            if not legacy_started:
                expected.update({"attempt_id", "attempt_receipts"})
        else:
            expected.add("snapshot_id")
            if not legacy:
                expected.update({"observed_at", "acceptance_fingerprint"})
        if phase == "published":
            expected.update({"aggregate_snapshot_id", "publish_status"})
        if set(row) != expected:
            raise ValueError("post_lineup_refresh_state_invalid")
        ack_key = row.get("ack_key")
        if (
            not isinstance(ack_key, Mapping)
            or set(ack_key) != {"competition_id", "event_id", "lineup_fingerprint"}
            or ack_key.get("competition_id") not in FORMAL_SINGLE_MATCH_IDS
            or not isinstance(ack_key.get("event_id"), str)
            or not ack_key.get("event_id")
            or not _valid_fingerprint(ack_key.get("lineup_fingerprint"))
            or _ack_token(ack_key) != token
        ):
            raise ValueError("post_lineup_refresh_state_invalid")
        projected = {
            "ack_key": dict(ack_key),
            "phase": phase,
        }
        if not legacy:
            projected["observed_at"] = _utc(row.get("observed_at")).isoformat()
            projected["acceptance_fingerprint"] = row.get("acceptance_fingerprint")
            if not _valid_fingerprint(projected["acceptance_fingerprint"]):
                raise ValueError("post_lineup_refresh_state_invalid")
        if phase == "refresh_started" and not legacy_started:
            attempt_id = row.get("attempt_id")
            attempt_receipts = row.get("attempt_receipts")
            if (
                not isinstance(attempt_id, str)
                or not attempt_id.startswith("league-attempt-")
                or not isinstance(attempt_receipts, list)
                or not attempt_receipts
            ):
                raise ValueError("post_lineup_refresh_state_invalid")
            checked_attempt: dict[str, dict[str, str]] = {}
            for member in attempt_receipts:
                if (
                    not isinstance(member, Mapping)
                    or set(member) != {"token", "event_id"}
                    or not _valid_fingerprint(member.get("token"))
                    or not isinstance(member.get("event_id"), str)
                    or not member.get("event_id")
                    or member["token"] in checked_attempt
                ):
                    raise ValueError("post_lineup_refresh_state_invalid")
                checked_attempt[member["token"]] = {
                    "token": member["token"],
                    "event_id": member["event_id"],
                }
            if token not in checked_attempt or checked_attempt[token]["event_id"] != ack_key["event_id"]:
                raise ValueError("post_lineup_refresh_state_invalid")
            if len({member["event_id"] for member in checked_attempt.values()}) != len(checked_attempt):
                raise ValueError("post_lineup_refresh_state_invalid")
            projected["attempt_id"] = attempt_id
            projected["attempt_receipts"] = [
                checked_attempt[key] for key in sorted(checked_attempt)
            ]
        if phase != "refresh_started":
            if not isinstance(row.get("snapshot_id"), str) or not row.get("snapshot_id"):
                raise ValueError("post_lineup_refresh_state_invalid")
            projected["snapshot_id"] = row["snapshot_id"]
        if phase == "published":
            if (
                not isinstance(row.get("aggregate_snapshot_id"), str)
                or not row.get("aggregate_snapshot_id")
                or row.get("publish_status") not in {"stored", "duplicate"}
            ):
                raise ValueError("post_lineup_refresh_state_invalid")
            projected["aggregate_snapshot_id"] = row["aggregate_snapshot_id"]
            projected["publish_status"] = row["publish_status"]
        checked[token] = projected
    return {
        "schema_version": 1,
        "receipts": {token: checked[token] for token in sorted(checked)},
    }


def _phase_rank(value: str) -> int:
    return {"refresh_started": 1, "committed": 2, "published": 3}[value]


def _merge_state_rows(
    current: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    current_rank = _phase_rank(str(current["phase"]))
    incoming_rank = _phase_rank(str(incoming["phase"]))
    if current_rank > incoming_rank:
        return dict(current)
    if incoming_rank > current_rank:
        return dict(incoming)
    if incoming_rank == 3:
        return dict(current)
    current_at = str(current.get("observed_at") or "")
    incoming_at = str(incoming.get("observed_at") or "")
    return dict(incoming if incoming_at >= current_at else current)


def _merge_states(current: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(current["receipts"])
    for token, row in incoming["receipts"].items():
        previous = merged.get(token)
        merged[token] = (
            _merge_state_rows(previous, row)
            if isinstance(previous, Mapping)
            else dict(row)
        )
    return _validate_state({"schema_version": 1, "receipts": merged})


def _atomic_write(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
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


class PostLineupRefreshStateStore:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / "data/local/leagues/post_lineup_refresh_state.json"
        self.lock_path = self.path.with_suffix(".lock")

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "receipts": {}}
        try:
            return _validate_state(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            raise ValueError("post_lineup_refresh_state_invalid") from None

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "receipts": {}}
        try:
            return _validate_state(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            raise ValueError("post_lineup_refresh_state_invalid") from None

    def _write_unlocked(self, state: Mapping[str, Any]) -> str:
        payload = json.dumps(
            dict(state), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
        if self.path.exists() and self.path.read_text(encoding="utf-8") == payload:
            return "unchanged"
        _atomic_write(self.path, state)
        return "stored"

    def commit(self, state: Mapping[str, Any]) -> str:
        checked = _validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            merged = _merge_states(self._read_unlocked(), checked)
            return self._write_unlocked(merged)

    def claim_refresh(self, state: Mapping[str, Any]) -> dict[str, Any]:
        checked = _validate_state(state)
        if any(row["phase"] != "refresh_started" for row in checked["receipts"].values()):
            raise ValueError("post_lineup_refresh_claim_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = self._read_unlocked()
            claimed: list[str] = []
            additions: dict[str, Any] = {}
            for token, row in checked["receipts"].items():
                if token not in current["receipts"]:
                    additions[token] = row
                    claimed.append(token)
            merged = _merge_states(
                current,
                {"schema_version": 1, "receipts": additions},
            )
            status = self._write_unlocked(merged)
        return {"status": status, "claimed": sorted(claimed), "state": merged}


def _validate_acceptance(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "competitions"}
        or value.get("schema_version") != 1
    ):
        raise ValueError("post_lineup_acceptance_invalid")
    competitions = value.get("competitions")
    if not isinstance(competitions, Mapping) or not set(competitions).issubset(FORMAL_SINGLE_MATCH_IDS):
        raise ValueError("post_lineup_acceptance_invalid")
    checked: dict[str, dict[str, Any]] = {}
    for competition_id, row in competitions.items():
        if not isinstance(row, Mapping):
            raise ValueError("post_lineup_acceptance_invalid")
        fingerprints = row.get("fingerprints")
        if not isinstance(fingerprints, Mapping):
            fingerprints = {}
        checked[competition_id] = {
            "competition_id": str(row.get("competition_id") or ""),
            "state": str(row.get("state") or ""),
            "reason": (
                str(row.get("reason"))
                if row.get("reason") is not None
                else None
            ),
            "fingerprints": {
                name: str(fingerprints.get(name) or "")
                for name in (
                    "sport_catalog", "odds_sample", "team_identity", "result_contract"
                )
                if fingerprints.get(name)
            },
        }
    return {"schema_version": 1, "competitions": checked}


def _load_acceptance(root: str | Path) -> dict[str, Any]:
    path = Path(root) / "data/local/leagues/acceptance.json"
    try:
        return _validate_acceptance(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        raise ValueError("post_lineup_acceptance_invalid") from None


def _select_quota_slot(
    env: Mapping[str, str],
    quota_ledger: Mapping[str, Any],
    minimum_remaining: int,
    *,
    now: datetime,
    reservations: Mapping[str, int],
    max_age_seconds: int = QUOTA_LEDGER_MAX_AGE_SECONDS,
) -> tuple[Any | None, str | None, int | None]:
    providers = quota_ledger.get("providers") if isinstance(quota_ledger, Mapping) else None
    if not isinstance(providers, Mapping):
        return None, "quota_unknown", None
    slots = configured_key_slots(env)
    if not slots:
        return None, "quota_key_unavailable", None
    observed: list[int | None] = []
    for slot in slots:
        entry = providers.get(slot.provider)
        remaining = entry.get("remaining") if isinstance(entry, Mapping) else None
        remaining = remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else None
        try:
            observed_at = _utc(entry.get("observed_at")) if isinstance(entry, Mapping) else None
        except ValueError:
            observed_at = None
        if (
            observed_at is None
            or observed_at > now
            or (now - observed_at).total_seconds() > max_age_seconds
        ):
            remaining = None
        if remaining is not None:
            remaining -= int(reservations.get(slot.provider) or 0)
        observed.append(remaining)
        if remaining is not None and remaining > minimum_remaining:
            return slot, None, remaining
    if any(value is None for value in observed):
        return None, "quota_unknown", None
    if all(value is not None and value <= 0 for value in observed):
        return None, "quota_exhausted", max(value for value in observed if value is not None)
    return None, "quota_below_minimum", max(value for value in observed if value is not None)


def _selected_env(env: Mapping[str, str], selected: Any) -> dict[str, str]:
    names_by_slot = {
        "primary": ("THE_ODDS_API_KEY_PRIMARY", "THE_ODDS_API_KEY"),
        "secondary": ("THE_ODDS_API_KEY_SECONDARY",),
        "tertiary": ("THE_ODDS_API_KEY_TERTIARY",),
        "quaternary": ("THE_ODDS_API_KEY_QUATERNARY",),
        "quinary": ("THE_ODDS_API_KEY_QUINARY",),
    }
    for name in names_by_slot.get(selected.slot, ()):
        value = env.get(name)
        if isinstance(value, str) and value.strip() == selected.api_key:
            return {name: value}
    raise ValueError("post_lineup_selected_key_invalid")


def _snapshot_receipts(value: Any, planned_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    receipts: dict[str, dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
            "competition", "snapshot_id", "commit_status"
        }:
            continue
        competition = row.get("competition")
        competition_id = (
            competition.get("id")
            if isinstance(competition, Mapping) and set(competition) == {"id"}
            else None
        )
        snapshot_id = row.get("snapshot_id")
        if (
            competition_id not in planned_ids
            or competition_id in receipts
            or not isinstance(snapshot_id, str)
            or not snapshot_id
            or row.get("commit_status") not in {"stored", "unchanged"}
        ):
            continue
        receipts[competition_id] = {
            "competition": {"id": competition_id},
            "snapshot_id": snapshot_id,
            "commit_status": row["commit_status"],
        }
    return receipts


def _project_refresh_result(value: Any, competition_id: str) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"status", "competitions", "snapshots"}
        or not isinstance(value.get("status"), str)
        or not isinstance(value.get("competitions"), Mapping)
        or set(value["competitions"]) != {competition_id}
        or not isinstance(value.get("snapshots"), list)
    ):
        return {"status": "invalid", "competition_id": competition_id, "snapshot_count": 0}
    snapshots = _snapshot_receipts(value["snapshots"], {competition_id})
    return {
        "status": str(value["status"]),
        "competition_id": competition_id,
        "snapshot_count": len(snapshots),
    }


def _safe_refresh_summary(rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    statuses = {str(row.get("status") or "invalid") for row in rows}
    if statuses == {"refreshed"}:
        status = "refreshed"
    elif "refreshed" in statuses:
        status = "partial"
    else:
        status = "failed"
    return {
        "status": status,
        "competitions": [dict(row) for row in rows],
    }


def _snapshot_has_expected_events(
    snapshot: Any,
    competition_id: str,
    expected_event_ids: Sequence[str],
    *,
    observed_at: str | None = None,
) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    competition = snapshot.get("competition")
    if not isinstance(competition, Mapping) or competition.get("id") != competition_id:
        return False
    if observed_at is not None:
        try:
            if _utc(snapshot.get("snapshot_at")) != _utc(observed_at):
                return False
        except ValueError:
            return False
    matches = snapshot.get("matches")
    if not isinstance(matches, list) or not matches:
        return False
    labels: dict[str, str] = {}
    for match in matches:
        if not isinstance(match, Mapping):
            return False
        declared = match.get("competition")
        event_id = match.get("source_event_id")
        decision = match.get("match_decision")
        label = decision.get("label") if isinstance(decision, Mapping) else None
        if (
            not isinstance(declared, Mapping)
            or declared.get("id") != competition_id
            or not isinstance(event_id, str)
            or not event_id
            or event_id in labels
        ):
            return False
        labels[event_id] = str(label or "")
    expected = {str(value) for value in expected_event_ids}
    return bool(expected) and expected.issubset(labels) and all(
        labels[event_id] in {"MATCH_PICK", "NO_CLEAN_MARKET"}
        for event_id in expected
    )


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _committed_snapshot_receipt(
    root: str | Path,
    competition_id: str,
    snapshot_id: str,
    expected_event_ids: Sequence[str],
) -> dict[str, Any] | None:
    snapshot = _read_snapshot(
        Path(root) / "data/cache/leagues" / competition_id / "snapshot.json"
    )
    if (
        not _snapshot_has_expected_events(snapshot, competition_id, expected_event_ids)
        or snapshot.get("snapshot_id") != snapshot_id
    ):
        return None
    return {
        "competition": {"id": competition_id},
        "snapshot_id": snapshot_id,
        "commit_status": "unchanged",
    }


def _reconcile_started_snapshot(
    root: str | Path,
    competition_id: str,
    expected_snapshot_id: str,
    observed_at: str,
    expected_event_ids: Sequence[str],
) -> dict[str, Any] | None:
    snapshot = _read_snapshot(
        Path(root) / "data/cache/leagues" / competition_id / "snapshot.json"
    )
    if (
        not _snapshot_has_expected_events(
            snapshot,
            competition_id,
            expected_event_ids,
            observed_at=observed_at,
        )
        or snapshot.get("snapshot_id") != expected_snapshot_id
    ):
        return None
    return {
        "competition": {"id": competition_id},
        "snapshot_id": expected_snapshot_id,
        "commit_status": "unchanged",
    }


def _ack_item(row: Mapping[str, Any], reason: str | None = None) -> AckItem:
    item: AckItem = {"ack_key": dict(row["ack_key"])}
    if reason is not None:
        item["reason"] = reason
    return item


def _partition_receipt_eligibility(
    rows: Sequence[Mapping[str, Any]],
    *,
    observed_at: datetime,
    context_loader: Callable[[], Any] | None,
) -> tuple[list[dict[str, Any]], list[AckItem], list[AckItem]]:
    """Revalidate each receipt against fresh event-scoped context."""
    raw_contexts: Mapping[str, Any] | None = None
    if context_loader is not None:
        try:
            loaded = context_loader()
        except Exception:
            loaded = None
        raw_contexts = loaded if isinstance(loaded, Mapping) else {}

    eligible: list[dict[str, Any]] = []
    retryable: list[AckItem] = []
    blocked: list[AckItem] = []
    for source_row in rows:
        row = dict(source_row)
        if _utc(row["kickoff_at_utc"]) <= observed_at:
            blocked.append(_ack_item(row, "match_started"))
            continue
        if raw_contexts is None:
            eligible.append(row)
            continue
        key = f"{row['competition_id']}:{row['event_id']}"
        context = raw_contexts.get(key)
        if not isinstance(context, Mapping):
            retryable.append(_ack_item(row, "receipt_context_unavailable"))
            continue
        try:
            context_kickoff = _utc(context.get("kickoff_at_utc"))
        except ValueError:
            retryable.append(_ack_item(row, "receipt_context_unavailable"))
            continue
        fixture_status = context.get("fixture_status")
        acceptance_active = context.get("acceptance_active")
        if (
            context.get("competition_id") != row["competition_id"]
            or context.get("event_id") != row["event_id"]
            or context_kickoff != _utc(row["kickoff_at_utc"])
            or not isinstance(fixture_status, str)
            or not isinstance(acceptance_active, bool)
        ):
            retryable.append(_ack_item(row, "receipt_context_unavailable"))
            continue
        if acceptance_active is not True:
            blocked.append(_ack_item(row, "acceptance_not_active"))
            continue
        if fixture_status.strip().upper() in _TERMINAL_FIXTURE_STATUSES:
            blocked.append(_ack_item(row, "fixture_not_eligible"))
            continue
        if context_kickoff <= observed_at:
            blocked.append(_ack_item(row, "match_started"))
            continue
        eligible.append(row)
    return eligible, retryable, blocked


def _remove_task4_pending(root: str | Path, ack_keys: list[Mapping[str, Any]]) -> int:
    path = _pending_path(root)
    if not path.exists():
        return 0
    expected = {_ack_token(ack_key): dict(ack_key) for ack_key in ack_keys}
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        pending = _read_pending(root)
        retained: dict[str, Any] = {}
        removed = 0
        for key, row in pending["events"].items():
            ack_key = row["ack_key"]
            expected_ack = expected.get(_ack_token(ack_key))
            if expected_ack is not None and ack_key == expected_ack:
                removed += 1
            else:
                retained[key] = row
        if removed:
            _atomic_write_pending(path, {"schema_version": 1, "events": retained})
        return removed


def _result(
    *,
    status: str,
    plan: Mapping[str, Any],
    durable: list[AckItem],
    retryable: list[AckItem],
    blocked: list[AckItem],
    refresh: Mapping[str, Any] | None = None,
    publish: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "plan": dict(plan),
        "acks": {
            "durable": durable,
            "retryable": retryable,
            "blocked": blocked,
        },
        "refresh": dict(refresh) if isinstance(refresh, Mapping) else None,
        "publish": dict(publish) if isinstance(publish, Mapping) else None,
    }


def run_post_lineup_refresh(
    *,
    root: str | Path,
    now: Any,
    newly_confirmed: Mapping[str, Any],
    live: bool = False,
    env: Mapping[str, str] | None = None,
    quota_ledger: Mapping[str, Any] | None = None,
    acceptance_report: Mapping[str, Any] | None = None,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
    env_loader: Callable[[], Any] | None = None,
    quota_loader: Callable[[], Any] | None = None,
    refresh_fn: Callable[..., Mapping[str, Any]] = run_planned_league_refresh,
    odds_fetcher: Callable[..., Any] | None = None,
    snapshot_builder: Callable[..., dict[str, Any]] | None = None,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    state_store_factory: Callable[[str | Path], Any] = PostLineupRefreshStateStore,
    live_store_factory: Callable[[str | Path], Any] = LeagueLiveStore,
    minimum_remaining: int = LOW_QUOTA_SWITCH_THRESHOLD,
    quota_max_age_seconds: int = QUOTA_LEDGER_MAX_AGE_SECONDS,
    observed_clock: Callable[[], Any] | None = None,
    receipt_context_loader: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    try:
        requested_now = _utc(now)
        receipts = _normalize_receipts(newly_confirmed)
    except ValueError:
        return _result(
            status="blocked",
            plan={"competition_ids": [], "receipt_count": 0},
            durable=[],
            retryable=[],
            blocked=[],
        )
    plan = {
        "competition_ids": sorted({row["competition_id"] for row in receipts}),
        "receipt_count": len(receipts),
    }
    if not live:
        return _result(
            status="dry_run", plan=plan, durable=[], retryable=[], blocked=[]
        )
    if not receipts:
        return _result(
            status="not_due", plan=plan, durable=[], retryable=[], blocked=[]
        )
    try:
        clock = MonotonicUtcClock(observed_clock)
        now_dt = clock.now()
    except ValueError:
        return _result(
            status="blocked",
            plan=plan,
            durable=[],
            retryable=[],
            blocked=[_ack_item(row, "observed_clock_invalid") for row in receipts],
        )

    try:
        state_store = state_store_factory(root)
        state = _validate_state(state_store.read())
    except Exception:
        return _result(
            status="blocked",
            plan=plan,
            durable=[],
            retryable=[_ack_item(row, "state_invalid") for row in receipts],
            blocked=[],
        )

    durable_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    blocked_by_token: dict[str, AckItem] = {}
    retryable_by_token: dict[str, AckItem] = {}
    initially_eligible, initial_retryable, initial_blocked = (
        _partition_receipt_eligibility(
            receipts,
            observed_at=now_dt,
            context_loader=receipt_context_loader,
        )
    )
    for item in initial_retryable:
        retryable_by_token[_ack_token(item["ack_key"])] = item
    for item in initial_blocked:
        blocked_by_token[_ack_token(item["ack_key"])] = item
    for row in initially_eligible:
        existing = state["receipts"].get(row["token"])
        if isinstance(existing, Mapping) and existing.get("phase") == "published":
            durable_rows.append(row)
        elif isinstance(existing, Mapping) and existing.get("phase") in {
            "refresh_started", "committed"
        }:
            unresolved.append(row)
        else:
            unresolved.append(row)
    if durable_rows and not unresolved and not retryable_by_token:
        try:
            _remove_task4_pending(root, [row["ack_key"] for row in durable_rows])
        except (OSError, TypeError, ValueError):
            pass
        return _result(
            status="partial" if blocked_by_token else "already_acked",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=list(retryable_by_token.values()),
            blocked=list(blocked_by_token.values()),
        )

    try:
        acceptance = _validate_acceptance(
            acceptance_report if acceptance_report is not None else _load_acceptance(root)
        )
    except ValueError:
        return _result(
            status="blocked",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=list(retryable_by_token.values()),
            blocked=list(blocked_by_token.values())
            + [_ack_item(row, "acceptance_invalid") for row in unresolved],
        )
    guarded_acceptance_fingerprint = acceptance_fingerprint(acceptance)

    eligible: list[dict[str, Any]] = []
    for row in unresolved:
        acceptance_row = acceptance["competitions"].get(row["competition_id"])
        if acceptance_row_is_active(acceptance_row, row["competition_id"]):
            eligible.append(row)
        else:
            blocked_by_token[row["token"]] = _ack_item(row, "acceptance_not_active")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        grouped.setdefault(row["competition_id"], []).append(row)

    refresh_summaries: list[dict[str, Any]] = []
    component_receipts: dict[str, dict[str, Any]] = {}
    publishable_tokens: set[str] = set()
    reservations: dict[str, int] = {}
    loaded_env: Mapping[str, Any] | None = None
    env_attempted = False

    for competition_id in sorted(grouped):
        competition_rows = grouped[competition_id]
        try:
            state = _validate_state(state_store.read())
        except Exception:
            for row in competition_rows:
                retryable_by_token[row["token"]] = _ack_item(row, "state_invalid")
            continue

        state_rows = {
            row["token"]: state["receipts"].get(row["token"])
            for row in competition_rows
        }
        drifted = [
            row for row in competition_rows
            if isinstance(state_rows[row["token"]], Mapping)
            and state_rows[row["token"]].get("acceptance_fingerprint")
            not in {None, "", guarded_acceptance_fingerprint}
        ]
        if drifted:
            for row in competition_rows:
                retryable_by_token[row["token"]] = _ack_item(row, "acceptance_changed")
            continue

        started_rows = [
            row for row in competition_rows
            if isinstance(state_rows[row["token"]], Mapping)
            and state_rows[row["token"]].get("phase") == "refresh_started"
        ]
        recovery_failed = False
        if started_rows:
            attempts = {
                str(state_rows[row["token"]].get("observed_at") or "")
                for row in started_rows
            }
            attempt_fingerprints = {
                str(state_rows[row["token"]].get("acceptance_fingerprint") or "")
                for row in started_rows
            }
            attempt_ids = {
                str(state_rows[row["token"]].get("attempt_id") or "")
                for row in started_rows
            }
            attempt_memberships = {
                json.dumps(
                    state_rows[row["token"]].get("attempt_receipts") or [],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for row in started_rows
            }
            recovered = None
            attempt_receipts: list[dict[str, str]] = []
            if (
                len(attempts) == 1
                and len(attempt_fingerprints) == 1
                and next(iter(attempt_fingerprints)) == guarded_acceptance_fingerprint
                and len(attempt_ids) == 1
                and next(iter(attempt_ids)).startswith("league-attempt-")
                and len(attempt_memberships) == 1
            ):
                decoded_membership = json.loads(next(iter(attempt_memberships)))
                if isinstance(decoded_membership, list):
                    attempt_receipts = [
                        dict(member)
                        for member in decoded_membership
                        if isinstance(member, Mapping)
                    ]
                recovered = _reconcile_started_snapshot(
                    root,
                    competition_id,
                    next(iter(attempt_ids)),
                    next(iter(attempts)),
                    [member["event_id"] for member in attempt_receipts],
                )
            if recovered is None:
                recovery_failed = True
                for row in started_rows:
                    retryable_by_token[row["token"]] = _ack_item(
                        row, "refresh_recovery_required"
                    )
                refresh_summaries.append({
                    "status": "recovery_required",
                    "competition_id": competition_id,
                    "snapshot_count": 0,
                })
            else:
                recovered_updates: dict[str, Any] = {}
                for member in attempt_receipts:
                    member_state = state["receipts"].get(member["token"])
                    ack_key = member_state.get("ack_key") if isinstance(member_state, Mapping) else None
                    if (
                        not isinstance(ack_key, Mapping)
                        or ack_key.get("competition_id") != competition_id
                        or ack_key.get("event_id") != member["event_id"]
                    ):
                        recovered_updates = {}
                        break
                    recovered_updates[member["token"]] = {
                        "ack_key": dict(ack_key),
                        "phase": "committed",
                        "snapshot_id": recovered["snapshot_id"],
                        "observed_at": next(iter(attempts)),
                        "acceptance_fingerprint": guarded_acceptance_fingerprint,
                    }
                if not recovered_updates:
                    recovery_failed = True
                    for row in started_rows:
                        retryable_by_token[row["token"]] = _ack_item(
                            row, "refresh_recovery_required"
                        )
                else:
                    try:
                        state_store.commit({
                            "schema_version": 1,
                            "receipts": recovered_updates,
                        })
                        state = _validate_state(state_store.read())
                    except Exception:
                        for row in started_rows:
                            retryable_by_token[row["token"]] = _ack_item(
                                row, "state_commit_failed"
                            )
                        recovery_failed = True
                    state_rows = {
                        row["token"]: state["receipts"].get(row["token"])
                        for row in competition_rows
                    }

        new_rows = [
            row for row in competition_rows
            if not isinstance(state_rows[row["token"]], Mapping)
        ]
        committed_before_refresh = [
            row for row in competition_rows
            if isinstance(state_rows[row["token"]], Mapping)
            and state_rows[row["token"]].get("phase") == "committed"
        ]

        if recovery_failed and new_rows:
            for row in new_rows:
                retryable_by_token[row["token"]] = _ack_item(
                    row, "waiting_for_refresh_recovery"
                )
            new_rows = []
        elif new_rows and committed_before_refresh:
            for row in new_rows:
                retryable_by_token[row["token"]] = _ack_item(
                    row, "waiting_for_committed_publish"
                )
            new_rows = []

        if new_rows:
            if not isinstance(identity_registry, LeagueTeamIdentityRegistry):
                for row in new_rows:
                    blocked_by_token[row["token"]] = _ack_item(
                        row, "strict_identity_registry_required"
                    )
                new_rows = []

        if new_rows:
            if not env_attempted:
                env_attempted = True
                try:
                    candidate_env = env if env is not None else (
                        env_loader() if env_loader is not None else None
                    )
                except Exception:
                    candidate_env = None
                loaded_env = candidate_env if isinstance(candidate_env, Mapping) else None
            if loaded_env is None:
                for row in new_rows:
                    blocked_by_token[row["token"]] = _ack_item(
                        row, "quota_key_unavailable"
                    )
                new_rows = []

        selected = None
        if new_rows:
            if quota_ledger is not None:
                loaded_quota = quota_ledger
            else:
                try:
                    loaded_quota = quota_loader() if quota_loader is not None else None
                except Exception:
                    loaded_quota = None
            selected, guard_reason, _remaining = _select_quota_slot(
                loaded_env,
                loaded_quota if isinstance(loaded_quota, Mapping) else {},
                int(minimum_remaining),
                now=now_dt,
                reservations=reservations,
                max_age_seconds=int(quota_max_age_seconds),
            )
            if selected is None:
                for row in new_rows:
                    blocked_by_token[row["token"]] = _ack_item(
                        row, str(guard_reason or "quota_unknown")
                    )
                new_rows = []

        if new_rows and odds_fetcher is None:
            for row in new_rows:
                blocked_by_token[row["token"]] = _ack_item(row, "odds_fetcher_missing")
            new_rows = []

        if new_rows and selected is not None:
            try:
                request_observed_at = clock.now()
            except ValueError:
                for row in new_rows:
                    blocked_by_token[row["token"]] = _ack_item(
                        row, "observed_clock_invalid"
                    )
                new_rows = []
            if new_rows:
                new_rows, eligibility_retryable, eligibility_blocked = (
                    _partition_receipt_eligibility(
                        new_rows,
                        observed_at=request_observed_at,
                        context_loader=receipt_context_loader,
                    )
                )
                for item in eligibility_retryable:
                    retryable_by_token[_ack_token(item["ack_key"])] = item
                for item in eligibility_blocked:
                    blocked_by_token[_ack_token(item["ack_key"])] = item
                try:
                    request_observed_at = clock.now()
                except ValueError:
                    for row in new_rows:
                        retryable_by_token[row["token"]] = _ack_item(
                            row, "observed_clock_invalid"
                        )
                    new_rows = []
                if new_rows:
                    new_rows, timing_retryable, timing_blocked = (
                        _partition_receipt_eligibility(
                            new_rows,
                            observed_at=request_observed_at,
                            context_loader=None,
                        )
                    )
                    for item in timing_retryable:
                        retryable_by_token[_ack_token(item["ack_key"])] = item
                    for item in timing_blocked:
                        blocked_by_token[_ack_token(item["ack_key"])] = item

        if new_rows and selected is not None:
            reservations[selected.provider] = reservations.get(selected.provider, 0) + 1
            attempt_rows = list(new_rows)
            attempt_receipts = [
                {"token": row["token"], "event_id": row["event_id"]}
                for row in sorted(attempt_rows, key=lambda item: item["token"])
            ]
            attempt_id = _attempt_snapshot_id(
                competition_id,
                request_observed_at.isoformat(),
                [row["token"] for row in attempt_rows],
            )
            intent = {
                row["token"]: {
                    "ack_key": dict(row["ack_key"]),
                    "phase": "refresh_started",
                    "observed_at": request_observed_at.isoformat(),
                    "acceptance_fingerprint": guarded_acceptance_fingerprint,
                    "attempt_id": attempt_id,
                    "attempt_receipts": attempt_receipts,
                }
                for row in new_rows
            }
            try:
                claimed_result = state_store.claim_refresh({
                    "schema_version": 1,
                    "receipts": intent,
                })
                claimed = set(claimed_result.get("claimed") or [])
            except Exception:
                claimed = set()
            expected_claims = {row["token"] for row in new_rows}
            if claimed != expected_claims:
                for row in new_rows:
                    retryable_by_token[row["token"]] = _ack_item(
                        row, "refresh_recovery_required"
                    )
                refresh_summaries.append({
                    "status": "recovery_required",
                    "competition_id": competition_id,
                    "snapshot_count": 0,
                })
                continue

            selected_env = _selected_env(loaded_env, selected)
            expected_sport_key = _sport_key(competition_id)
            try:
                provider_payload = odds_fetcher(expected_sport_key, selected_env)
            except Exception:
                for row in attempt_rows:
                    retryable_by_token[row["token"]] = _ack_item(row, "refresh_failed")
                refresh_summaries.append({
                    "status": "error",
                    "competition_id": competition_id,
                    "snapshot_count": 0,
                })
                continue
            try:
                response_observed_at = clock.now()
            except ValueError:
                for row in attempt_rows:
                    retryable_by_token[row["token"]] = _ack_item(
                        row, "observed_clock_invalid"
                    )
                refresh_summaries.append({
                    "status": "error",
                    "competition_id": competition_id,
                    "snapshot_count": 0,
                })
                continue

            original_attempt_rows = list(attempt_rows)
            (
                postfetch_rows,
                eligibility_retryable,
                eligibility_blocked,
            ) = _partition_receipt_eligibility(
                original_attempt_rows,
                observed_at=response_observed_at,
                context_loader=receipt_context_loader,
            )
            for item in eligibility_retryable:
                retryable_by_token[_ack_token(item["ack_key"])] = item
            for item in eligibility_blocked:
                blocked_by_token[_ack_token(item["ack_key"])] = item
            try:
                response_observed_at = clock.now()
            except ValueError:
                for row in postfetch_rows:
                    retryable_by_token[row["token"]] = _ack_item(
                        row, "observed_clock_invalid"
                    )
                postfetch_rows = []
            if postfetch_rows:
                postfetch_rows, timing_retryable, timing_blocked = (
                    _partition_receipt_eligibility(
                        postfetch_rows,
                        observed_at=response_observed_at,
                        context_loader=None,
                    )
                )
                for item in timing_retryable:
                    retryable_by_token[_ack_token(item["ack_key"])] = item
                for item in timing_blocked:
                    blocked_by_token[_ack_token(item["ack_key"])] = item
            valid_tokens = {row["token"] for row in postfetch_rows}
            invalid_rows = [
                row for row in original_attempt_rows if row["token"] not in valid_tokens
            ]
            forbidden_event_ids = {row["event_id"] for row in invalid_rows}
            if not postfetch_rows:
                refresh_summaries.append({
                    "status": "blocked",
                    "competition_id": competition_id,
                    "snapshot_count": 0,
                })
                continue

            attempt_rows = list(postfetch_rows)
            attempt_receipts = [
                {"token": row["token"], "event_id": row["event_id"]}
                for row in sorted(attempt_rows, key=lambda item: item["token"])
            ]
            attempt_id = _attempt_snapshot_id(
                competition_id,
                request_observed_at.isoformat(),
                [row["token"] for row in attempt_rows],
            )
            if invalid_rows:
                rebased_intent: dict[str, Any] = {
                    row["token"]: {
                        "ack_key": dict(row["ack_key"]),
                        "phase": "refresh_started",
                        "observed_at": request_observed_at.isoformat(),
                        "acceptance_fingerprint": guarded_acceptance_fingerprint,
                        "attempt_id": attempt_id,
                        "attempt_receipts": attempt_receipts,
                    }
                    for row in attempt_rows
                }
                for row in invalid_rows:
                    isolated_membership = [{
                        "token": row["token"],
                        "event_id": row["event_id"],
                    }]
                    rebased_intent[row["token"]] = {
                        "ack_key": dict(row["ack_key"]),
                        "phase": "refresh_started",
                        "observed_at": request_observed_at.isoformat(),
                        "acceptance_fingerprint": guarded_acceptance_fingerprint,
                        "attempt_id": _attempt_snapshot_id(
                            competition_id,
                            request_observed_at.isoformat(),
                            [row["token"]],
                        ),
                        "attempt_receipts": isolated_membership,
                    }
                try:
                    state_store.commit({
                        "schema_version": 1,
                        "receipts": rebased_intent,
                    })
                    rebased_state = _validate_state(state_store.read())
                    for row in attempt_rows:
                        state_row = rebased_state["receipts"].get(row["token"])
                        if (
                            not isinstance(state_row, Mapping)
                            or state_row.get("phase") != "refresh_started"
                            or state_row.get("attempt_id") != attempt_id
                            or state_row.get("attempt_receipts") != attempt_receipts
                        ):
                            raise ValueError("post_lineup_attempt_rebase_failed")
                except Exception:
                    for row in attempt_rows:
                        retryable_by_token[row["token"]] = _ack_item(
                            row, "state_commit_failed"
                        )
                    continue
            provider_payload = _without_provider_events(
                provider_payload, forbidden_event_ids
            )
            expected_event_ids = [row["event_id"] for row in attempt_rows]

            def commit_callback(
                receipt: Mapping[str, Any],
                *,
                rows: list[dict[str, Any]] = attempt_rows,
            ) -> None:
                snapshots = _snapshot_receipts([receipt], {competition_id})
                checked = snapshots.get(competition_id)
                if checked is None or checked["snapshot_id"] != attempt_id:
                    raise ValueError("post_lineup_commit_receipt_invalid")
                updates = {
                    row["token"]: {
                        "ack_key": dict(row["ack_key"]),
                        "phase": "committed",
                        "snapshot_id": checked["snapshot_id"],
                        "observed_at": request_observed_at.isoformat(),
                        "acceptance_fingerprint": guarded_acceptance_fingerprint,
                    }
                    for row in rows
                }
                state_store.commit({"schema_version": 1, "receipts": updates})

            refresh_kwargs: dict[str, Any] = {
                "root": root,
                "observed_at": request_observed_at.isoformat(),
                "competition_ids": [competition_id],
                "env": selected_env,
                "odds_fetcher": None,
                "acceptance_report": dict(acceptance),
                "identity_registry": identity_registry,
                "expected_event_ids_by_competition": {
                    competition_id: expected_event_ids,
                },
                "forbidden_event_ids_by_competition": {
                    competition_id: sorted(forbidden_event_ids),
                },
                "expected_snapshot_ids_by_competition": {
                    competition_id: attempt_id,
                },
                "guarded_acceptance_fingerprint": guarded_acceptance_fingerprint,
                "store_factory": live_store_factory,
                "commit_callback": commit_callback,
            }
            if snapshot_builder is not None:
                refresh_kwargs["snapshot_builder"] = snapshot_builder
            payload_served = False

            def cached_odds_fetcher(
                sport_key: str, _selected_env: Mapping[str, str]
            ) -> Any:
                nonlocal payload_served
                if sport_key != expected_sport_key or payload_served:
                    raise ValueError("post_lineup_cached_fetch_invalid")
                payload_served = True
                return provider_payload

            refresh_kwargs["odds_fetcher"] = cached_odds_fetcher
            try:
                refresh_value = refresh_fn(**refresh_kwargs)
            except Exception:
                refresh_value = None
            projected = _project_refresh_result(refresh_value, competition_id)
            refresh_summaries.append(projected)
            valid_shape = (
                isinstance(refresh_value, Mapping)
                and set(refresh_value) == {"status", "competitions", "snapshots"}
            )
            snapshot_receipts = _snapshot_receipts(
                refresh_value.get("snapshots")
                if valid_shape and isinstance(refresh_value, Mapping)
                else None,
                {competition_id},
            )
            committed_receipt = snapshot_receipts.get(competition_id)
            if committed_receipt is None or _committed_snapshot_receipt(
                root,
                competition_id,
                committed_receipt["snapshot_id"],
                expected_event_ids,
            ) is None:
                for row in attempt_rows:
                    retryable_by_token[row["token"]] = _ack_item(
                        row,
                        "refresh_failed",
                    )
                for row in committed_before_refresh:
                    publishable_tokens.discard(row["token"])
                continue
            try:
                state = _validate_state(state_store.read())
                missing_commit = [
                    row for row in attempt_rows
                    if not isinstance(state["receipts"].get(row["token"]), Mapping)
                    or state["receipts"][row["token"]].get("phase") != "committed"
                    or state["receipts"][row["token"]].get("snapshot_id")
                    != committed_receipt["snapshot_id"]
                ]
                if missing_commit:
                    commit_callback(committed_receipt)
                    state = _validate_state(state_store.read())
            except Exception:
                for row in attempt_rows:
                    retryable_by_token[row["token"]] = _ack_item(row, "state_commit_failed")
                continue
            component_receipts[competition_id] = committed_receipt
            for row in attempt_rows:
                publishable_tokens.add(row["token"])
                retryable_by_token.pop(row["token"], None)

        try:
            state = _validate_state(state_store.read())
        except Exception:
            for row in competition_rows:
                retryable_by_token[row["token"]] = _ack_item(row, "state_invalid")
            continue
        committed_rows_for_competition = [
            row for row in competition_rows
            if isinstance(state["receipts"].get(row["token"]), Mapping)
            and state["receipts"][row["token"]].get("phase") == "committed"
            and row["token"] not in retryable_by_token
        ]
        if not committed_rows_for_competition:
            continue
        try:
            publish_observed_at = clock.now()
        except ValueError:
            for row in committed_rows_for_competition:
                retryable_by_token[row["token"]] = _ack_item(
                    row, "observed_clock_invalid"
                )
            continue
        (
            committed_rows_for_competition,
            eligibility_retryable,
            eligibility_blocked,
        ) = _partition_receipt_eligibility(
            committed_rows_for_competition,
            observed_at=publish_observed_at,
            context_loader=receipt_context_loader,
        )
        for item in eligibility_retryable:
            token = _ack_token(item["ack_key"])
            retryable_by_token[token] = item
            publishable_tokens.discard(token)
        for item in eligibility_blocked:
            token = _ack_token(item["ack_key"])
            blocked_by_token[token] = item
            publishable_tokens.discard(token)
        try:
            publish_observed_at = clock.now()
        except ValueError:
            for row in committed_rows_for_competition:
                retryable_by_token[row["token"]] = _ack_item(
                    row, "observed_clock_invalid"
                )
                publishable_tokens.discard(row["token"])
            committed_rows_for_competition = []
        if committed_rows_for_competition:
            (
                committed_rows_for_competition,
                timing_retryable,
                timing_blocked,
            ) = _partition_receipt_eligibility(
                committed_rows_for_competition,
                observed_at=publish_observed_at,
                context_loader=None,
            )
            for item in timing_retryable:
                token = _ack_token(item["ack_key"])
                retryable_by_token[token] = item
                publishable_tokens.discard(token)
            for item in timing_blocked:
                token = _ack_token(item["ack_key"])
                blocked_by_token[token] = item
                publishable_tokens.discard(token)
        if not committed_rows_for_competition:
            continue
        state_snapshot_ids = {
            state["receipts"][row["token"]].get("snapshot_id")
            for row in committed_rows_for_competition
            if isinstance(state["receipts"].get(row["token"]), Mapping)
        }
        if len(state_snapshot_ids) != 1:
            for row in committed_rows_for_competition:
                retryable_by_token[row["token"]] = _ack_item(row, "publish_failed")
                publishable_tokens.discard(row["token"])
            continue
        snapshot_id = next(iter(state_snapshot_ids))
        checked_receipt = _committed_snapshot_receipt(
            root,
            competition_id,
            str(snapshot_id or ""),
            [row["event_id"] for row in committed_rows_for_competition],
        )
        if checked_receipt is None:
            for row in committed_rows_for_competition:
                retryable_by_token[row["token"]] = _ack_item(row, "publish_failed")
                publishable_tokens.discard(row["token"])
            continue
        legacy_updates: dict[str, Any] = {}
        snapshot = _read_snapshot(
            Path(root) / "data/cache/leagues" / competition_id / "snapshot.json"
        ) or {}
        for row in committed_rows_for_competition:
            state_row = state["receipts"][row["token"]]
            if not state_row.get("acceptance_fingerprint"):
                legacy_updates[row["token"]] = {
                    "ack_key": dict(row["ack_key"]),
                    "phase": "committed",
                    "snapshot_id": checked_receipt["snapshot_id"],
                    "observed_at": _utc(snapshot.get("snapshot_at")).isoformat(),
                    "acceptance_fingerprint": guarded_acceptance_fingerprint,
                }
        if legacy_updates:
            try:
                state_store.commit({"schema_version": 1, "receipts": legacy_updates})
            except Exception:
                for row in committed_rows_for_competition:
                    retryable_by_token[row["token"]] = _ack_item(row, "state_commit_failed")
                continue
        component_receipts[competition_id] = checked_receipt
        publishable_tokens.update(row["token"] for row in committed_rows_for_competition)

    refresh_result = _safe_refresh_summary(refresh_summaries)
    committed_rows = [row for row in eligible if row["token"] in publishable_tokens]
    def sorted_retryable() -> list[AckItem]:
        return sorted(
            retryable_by_token.values(),
            key=lambda item: (
                item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
            ),
        )

    retryable = sorted_retryable()
    blocked = sorted(
        blocked_by_token.values(),
        key=lambda item: (
            item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
        ),
    )

    if not committed_rows:
        status = "partial" if durable_rows and (retryable or blocked) else (
            "refresh_failed" if retryable else "blocked"
        )
        return _result(
            status=status,
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=retryable,
            blocked=blocked,
            refresh=refresh_result,
        )
    try:
        final_publish_observed_at = clock.now()
    except ValueError:
        for row in committed_rows:
            retryable_by_token[row["token"]] = _ack_item(
                row, "observed_clock_invalid"
            )
        committed_rows = []
    if committed_rows:
        committed_rows, eligibility_retryable, eligibility_blocked = (
            _partition_receipt_eligibility(
                committed_rows,
                observed_at=final_publish_observed_at,
                context_loader=receipt_context_loader,
            )
        )
        for item in eligibility_retryable:
            retryable_by_token[_ack_token(item["ack_key"])] = item
        for item in eligibility_blocked:
            blocked_by_token[_ack_token(item["ack_key"])] = item
        try:
            final_publish_observed_at = clock.now()
        except ValueError:
            for row in committed_rows:
                retryable_by_token[row["token"]] = _ack_item(
                    row, "observed_clock_invalid"
                )
            committed_rows = []
        if committed_rows:
            committed_rows, timing_retryable, timing_blocked = (
                _partition_receipt_eligibility(
                    committed_rows,
                    observed_at=final_publish_observed_at,
                    context_loader=None,
                )
            )
            for item in timing_retryable:
                retryable_by_token[_ack_token(item["ack_key"])] = item
            for item in timing_blocked:
                blocked_by_token[_ack_token(item["ack_key"])] = item
    if not committed_rows:
        retryable = sorted_retryable()
        blocked = sorted(
            blocked_by_token.values(),
            key=lambda item: (
                item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
            ),
        )
        return _result(
            status="partial" if durable_rows else (
                "refresh_failed" if retryable else "blocked"
            ),
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=retryable,
            blocked=blocked,
            refresh=refresh_result,
        )
    if publish_fn is None:
        for row in committed_rows:
            retryable_by_token[row["token"]] = _ack_item(row, "publish_failed")
        return _result(
            status="publish_failed",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=sorted_retryable(),
            blocked=blocked,
            refresh=refresh_result,
        )

    expected_components = {row["competition_id"] for row in committed_rows}
    if not expected_components or set(component_receipts) != expected_components:
        for row in committed_rows:
            retryable_by_token[row["token"]] = _ack_item(row, "publish_failed")
        return _result(
            status="publish_failed" if not durable_rows else "partial",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=sorted_retryable(),
            blocked=blocked,
            refresh=refresh_result,
        )

    publication = publish_committed_league_snapshots(
        root=root,
        snapshot_receipts=[component_receipts[key] for key in sorted(component_receipts)],
        publish_fn=publish_fn,
        expected_acceptance_fingerprint=guarded_acceptance_fingerprint,
    )
    if publication.get("status") != "published":
        for row in committed_rows:
            retryable_by_token[row["token"]] = _ack_item(row, "publish_failed")
        return _result(
            status="publish_failed" if not durable_rows else "partial",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=sorted_retryable(),
            blocked=blocked,
            refresh=refresh_result,
            publish=publication,
        )

    aggregate = publication["aggregate"]
    publish_status = publication["publish"]["status"]
    try:
        state = _validate_state(state_store.read())
    except Exception:
        state = {"schema_version": 1, "receipts": {}}
    published_updates: dict[str, Any] = {}
    for row in committed_rows:
        state_row = state["receipts"].get(row["token"])
        if not isinstance(state_row, Mapping):
            retryable_by_token[row["token"]] = _ack_item(row, "ack_state_commit_failed")
            continue
        published_updates[row["token"]] = {
            "ack_key": dict(row["ack_key"]),
            "phase": "published",
            "snapshot_id": state_row["snapshot_id"],
            "observed_at": state_row["observed_at"],
            "acceptance_fingerprint": state_row["acceptance_fingerprint"],
            "aggregate_snapshot_id": aggregate["snapshot_id"],
            "publish_status": publish_status,
        }
    try:
        if len(published_updates) != len(committed_rows):
            raise ValueError("post_lineup_ack_state_incomplete")
        state_store.commit({"schema_version": 1, "receipts": published_updates})
    except Exception:
        for row in committed_rows:
            retryable_by_token[row["token"]] = _ack_item(
                row, "ack_state_commit_failed"
            )
        return _result(
            status="publish_failed",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=sorted_retryable(),
            blocked=blocked,
            refresh=refresh_result,
            publish=publication,
        )

    durable_rows.extend(committed_rows)
    cleanup_failed = False
    try:
        _remove_task4_pending(root, [row["ack_key"] for row in durable_rows])
    except (OSError, TypeError, ValueError):
        cleanup_failed = True
    if retryable or blocked or cleanup_failed:
        status = "partial"
    else:
        status = "published"
    return _result(
        status=status,
        plan=plan,
        durable=[_ack_item(row) for row in durable_rows],
        retryable=sorted_retryable(),
        blocked=blocked,
        refresh=refresh_result,
        publish=publication,
    )
