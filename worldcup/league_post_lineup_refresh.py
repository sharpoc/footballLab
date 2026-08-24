from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_acceptance import acceptance_row_is_active
from worldcup.league_batch_runner import run_planned_league_refresh
from worldcup.league_live_store import LeagueLiveStore
from worldcup.league_lineups_refresh import (
    _atomic_write_pending,
    _pending_path,
    _read_pending,
)
from worldcup.league_scheduled_publish import publish_committed_league_snapshots
from worldcup.league_team_identity import LeagueTeamIdentityRegistry
from worldcup.theoddsapi_keys import LOW_QUOTA_SWITCH_THRESHOLD, configured_key_slots


AckItem = dict[str, Any]


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
        expected = {"ack_key", "phase", "snapshot_id"}
        if phase == "published":
            expected.update({"aggregate_snapshot_id", "publish_status"})
        if phase not in {"committed", "published"} or set(row) != expected:
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
            or not isinstance(row.get("snapshot_id"), str)
            or not row.get("snapshot_id")
        ):
            raise ValueError("post_lineup_refresh_state_invalid")
        projected = {
            "ack_key": dict(ack_key),
            "phase": phase,
            "snapshot_id": row["snapshot_id"],
        }
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

    def commit(self, state: Mapping[str, Any]) -> str:
        checked = _validate_state(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            payload = json.dumps(
                checked, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ) + "\n"
            if self.path.exists() and self.path.read_text(encoding="utf-8") == payload:
                return "unchanged"
            _atomic_write(self.path, checked)
        return "stored"


def _validate_acceptance(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise ValueError("post_lineup_acceptance_invalid")
    competitions = value.get("competitions")
    if not isinstance(competitions, Mapping) or not set(competitions).issubset(FORMAL_SINGLE_MATCH_IDS):
        raise ValueError("post_lineup_acceptance_invalid")
    return {"schema_version": 1, "competitions": dict(competitions)}


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
        if not isinstance(row, Mapping):
            continue
        competition = row.get("competition")
        competition_id = competition.get("id") if isinstance(competition, Mapping) else None
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


def _ack_item(row: Mapping[str, Any], reason: str | None = None) -> AckItem:
    item: AckItem = {"ack_key": dict(row["ack_key"])}
    if reason is not None:
        item["reason"] = reason
    return item


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
) -> dict[str, Any]:
    try:
        now_dt = _utc(now)
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
    blocked: list[AckItem] = []
    for row in receipts:
        existing = state["receipts"].get(row["token"])
        if isinstance(existing, Mapping) and existing.get("phase") == "published":
            durable_rows.append(row)
        elif _utc(row["kickoff_at_utc"]) <= now_dt:
            blocked.append(_ack_item(row, "match_started"))
        else:
            unresolved.append(row)
    if durable_rows and not unresolved:
        try:
            _remove_task4_pending(root, [row["ack_key"] for row in durable_rows])
        except (OSError, TypeError, ValueError):
            pass
        return _result(
            status="partial" if blocked else "already_acked",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=[],
            blocked=blocked,
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
            retryable=[],
            blocked=blocked + [_ack_item(row, "acceptance_invalid") for row in unresolved],
        )
    if not isinstance(identity_registry, LeagueTeamIdentityRegistry):
        return _result(
            status="blocked",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=[],
            blocked=blocked + [_ack_item(row, "strict_identity_registry_required") for row in unresolved],
        )

    eligible: list[dict[str, Any]] = []
    for row in unresolved:
        acceptance_row = acceptance["competitions"].get(row["competition_id"])
        if acceptance_row_is_active(acceptance_row, row["competition_id"]):
            eligible.append(row)
        else:
            blocked.append(_ack_item(row, "acceptance_not_active"))

    committed_rows: list[dict[str, Any]] = []
    refresh_rows: list[dict[str, Any]] = []
    for row in eligible:
        existing = state["receipts"].get(row["token"])
        if isinstance(existing, Mapping) and existing.get("phase") == "committed":
            committed_rows.append(row)
        else:
            refresh_rows.append(row)

    refresh_result: Mapping[str, Any] | None = None
    retryable: list[AckItem] = []
    if refresh_rows:
        try:
            loaded_env = env if env is not None else (env_loader() if env_loader is not None else None)
            loaded_quota = (
                quota_ledger
                if quota_ledger is not None
                else (quota_loader() if quota_loader is not None else None)
            )
        except Exception:
            loaded_env = None
            loaded_quota = None
        if not isinstance(loaded_env, Mapping):
            reason = "quota_key_unavailable"
            blocked.extend(_ack_item(row, reason) for row in refresh_rows)
            return _result(
                status="blocked",
                plan=plan,
                durable=[_ack_item(row) for row in durable_rows],
                retryable=[],
                blocked=blocked,
            )
        selected, guard_reason, _remaining = _select_quota_slot(
            loaded_env,
            loaded_quota if isinstance(loaded_quota, Mapping) else {},
            int(minimum_remaining),
        )
        if selected is None:
            blocked.extend(_ack_item(row, str(guard_reason)) for row in refresh_rows)
            return _result(
                status="blocked",
                plan=plan,
                durable=[_ack_item(row) for row in durable_rows],
                retryable=[],
                blocked=blocked,
            )
        if odds_fetcher is None:
            blocked.extend(_ack_item(row, "odds_fetcher_missing") for row in refresh_rows)
            return _result(
                status="blocked",
                plan=plan,
                durable=[_ack_item(row) for row in durable_rows],
                retryable=[],
                blocked=blocked,
            )
        competition_ids = sorted({row["competition_id"] for row in refresh_rows})
        refresh_kwargs: dict[str, Any] = {
            "root": root,
            "observed_at": now_dt.isoformat(),
            "competition_ids": competition_ids,
            "env": _selected_env(loaded_env, selected),
            "odds_fetcher": odds_fetcher,
            "acceptance_report": dict(acceptance),
            "identity_registry": identity_registry,
            "store_factory": live_store_factory,
        }
        if snapshot_builder is not None:
            refresh_kwargs["snapshot_builder"] = snapshot_builder
        try:
            value = refresh_fn(**refresh_kwargs)
            refresh_result = dict(value) if isinstance(value, Mapping) else {}
        except Exception:
            refresh_result = {}
        snapshot_receipts = _snapshot_receipts(
            refresh_result.get("snapshots") if isinstance(refresh_result, Mapping) else None,
            set(competition_ids),
        )
        newly_committed: list[dict[str, Any]] = []
        for row in refresh_rows:
            receipt = snapshot_receipts.get(row["competition_id"])
            if receipt is None:
                retryable.append(_ack_item(row, "refresh_failed"))
                continue
            state["receipts"][row["token"]] = {
                "ack_key": dict(row["ack_key"]),
                "phase": "committed",
                "snapshot_id": receipt["snapshot_id"],
            }
            newly_committed.append(row)
        if newly_committed:
            try:
                state_store.commit(state)
            except Exception:
                retryable.extend(
                    _ack_item(row, "state_commit_failed") for row in newly_committed
                )
                return _result(
                    status="refresh_failed",
                    plan=plan,
                    durable=[_ack_item(row) for row in durable_rows],
                    retryable=sorted(retryable, key=lambda item: (
                        item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
                    )),
                    blocked=blocked,
                    refresh=refresh_result,
                )
            committed_rows.extend(newly_committed)

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
    if publish_fn is None:
        retryable.extend(_ack_item(row, "publish_failed") for row in committed_rows)
        return _result(
            status="publish_failed",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=retryable,
            blocked=blocked,
            refresh=refresh_result,
        )

    component_receipts: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for row in committed_rows:
        state_row = state["receipts"][row["token"]]
        competition_id = row["competition_id"]
        receipt = {
            "competition": {"id": competition_id},
            "snapshot_id": state_row["snapshot_id"],
            "commit_status": "stored",
        }
        previous = component_receipts.get(competition_id)
        if previous is not None and previous["snapshot_id"] != receipt["snapshot_id"]:
            conflicts.add(competition_id)
        component_receipts[competition_id] = receipt
    if conflicts:
        retryable.extend(
            _ack_item(row, "publish_failed")
            for row in committed_rows
            if row["competition_id"] in conflicts
        )
        return _result(
            status="publish_failed",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=retryable,
            blocked=blocked,
            refresh=refresh_result,
        )
    publication = publish_committed_league_snapshots(
        root=root,
        snapshot_receipts=[component_receipts[key] for key in sorted(component_receipts)],
        publish_fn=publish_fn,
    )
    if publication.get("status") != "published":
        retryable.extend(_ack_item(row, "publish_failed") for row in committed_rows)
        return _result(
            status="publish_failed" if not durable_rows else "partial",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=sorted(retryable, key=lambda item: (
                item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
            )),
            blocked=blocked,
            refresh=refresh_result,
            publish=publication,
        )

    aggregate = publication["aggregate"]
    publish_status = publication["publish"]["status"]
    for row in committed_rows:
        state_row = state["receipts"][row["token"]]
        state["receipts"][row["token"]] = {
            "ack_key": dict(row["ack_key"]),
            "phase": "published",
            "snapshot_id": state_row["snapshot_id"],
            "aggregate_snapshot_id": aggregate["snapshot_id"],
            "publish_status": publish_status,
        }
    try:
        state_store.commit(state)
    except Exception:
        retryable.extend(_ack_item(row, "ack_state_commit_failed") for row in committed_rows)
        return _result(
            status="publish_failed",
            plan=plan,
            durable=[_ack_item(row) for row in durable_rows],
            retryable=retryable,
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
        retryable=sorted(retryable, key=lambda item: (
            item["ack_key"]["competition_id"], item["ack_key"]["event_id"]
        )),
        blocked=blocked,
        refresh=refresh_result,
        publish=publication,
    )
