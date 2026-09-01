"""Safe odds-component versions and endpoint-bound durable publication outbox."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.ingest import build_ingest_payload, canonical_json

ERRORS = frozenset({"league_publication_invalid", "league_component_regression",
                    "league_component_conflict", "league_publication_contract_required",
                    "league_publication_unsupported", "league_publication_migration_required"})


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ValueError("league_publication_invalid") from None


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _scalars(value, fields):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in fields if key in value and
            (value[key] is None or type(value[key]) in (str, int, float, bool))}


def project_publication_match(match: dict) -> dict:
    """Explicit public fields only; nested provider/diagnostic objects never pass through."""
    result = _scalars(match, ("source_event_id", "source_match_no", "kickoff_at_utc",
        "stage", "group", "venue_name", "home_team", "away_team", "home_canonical",
        "away_canonical", "odds_updated_at", "fixture_status"))
    result["competition"] = _scalars(match.get("competition"), ("id", "name", "label", "season", "country", "rating_policy"))
    if isinstance(match.get("refresh_plan"), dict):
        result["refresh_plan"] = _scalars(match["refresh_plan"], ("next_update_at", "label", "description"))
    decision = match.get("match_decision")
    if isinstance(decision, dict):
        projected = _scalars(decision, ("schema_version", "policy_version", "label", "selected_option_id",
            "market", "selection", "line", "odds", "p_hit", "p_hit_safe", "p_no_loss_safe",
            "uncertainty_penalty", "evidence_score", "computed_at", "odds_latest_at", "valid_until",
            "method", "rejected_count"))
        for key in ("reasons", "risks"):
            if isinstance(decision.get(key), list):
                projected[key] = [item for item in decision[key] if isinstance(item, str)]
        result["match_decision"] = projected
    for key, fields in (("elo", ("home", "away")), ("result", ("status", "home_score", "away_score"))):
        if isinstance(match.get(key), dict):
            result[key] = _scalars(match[key], fields)
    return result


def _validate_vector(vector):
    if not isinstance(vector, dict):
        raise ValueError("league_publication_invalid")
    for key, value in vector.items():
        if key not in {"odds:" + item for item in FORMAL_SINGLE_MATCH_IDS} or not isinstance(value, dict):
            raise ValueError("league_publication_invalid")
        if set(value) != {"snapshot_id", "snapshot_at", "content_sha256"}:
            raise ValueError("league_publication_invalid")
        _time(value["snapshot_at"])
        if not isinstance(value["snapshot_id"], str) or not value["snapshot_id"].strip() or not isinstance(value["content_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["content_sha256"]):
            raise ValueError("league_publication_invalid")


def validate_component_vector(previous: dict, current: dict) -> None:
    _validate_vector(previous); _validate_vector(current)
    for key, old in previous.items():
        new = current.get(key)
        if new is None or _time(new["snapshot_at"]) < _time(old["snapshot_at"]):
            raise ValueError("league_component_regression")
        if _time(new["snapshot_at"]) == _time(old["snapshot_at"]) and (new["snapshot_id"], new["content_sha256"]) != (old["snapshot_id"], old["content_sha256"]):
            raise ValueError("league_component_conflict")


def _component_events(snapshot: dict, component_keys: set[str]) -> dict[str, dict[str, dict]]:
    matches = snapshot.get("matches")
    if not isinstance(matches, list):
        raise ValueError("league_publication_invalid")
    result = {key: {} for key in component_keys}
    seen = set()
    for raw in matches:
        match = project_publication_match(raw)
        competition_id = (match.get("competition") or {}).get("id")
        key = "odds:" + str(competition_id)
        event_id = match.get("source_event_id")
        if key not in result or not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            raise ValueError("league_publication_invalid")
        seen.add(event_id)
        result[key][event_id] = match
    return result


def _validate_event_membership(previous: dict, current: dict,
                               previous_keys: set[str], current_keys: set[str]) -> None:
    old_events = _component_events(previous, previous_keys)
    new_events = _component_events(current, current_keys)
    for key, old_rows in old_events.items():
        new_rows = new_events.get(key, {})
        if not set(old_rows).issubset(new_rows):
            raise ValueError("league_component_regression")
        for event_id, old in old_rows.items():
            new = new_rows[event_id]
            for field in ("kickoff_at_utc", "home_canonical", "away_canonical"):
                if field in old and field in new and old[field] != new[field]:
                    raise ValueError("league_component_conflict")


def build_publication_vector(snapshots: list[dict]) -> dict:
    if not isinstance(snapshots, list):
        raise ValueError("league_publication_invalid")
    vector = {}; seen = set()
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("competition"), dict):
            raise ValueError("league_publication_invalid")
        competition_id = snapshot["competition"].get("id")
        key = "odds:" + str(competition_id)
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or key in vector:
            raise ValueError("league_publication_invalid")
        matches = snapshot.get("matches")
        if not isinstance(matches, list):
            raise ValueError("league_publication_invalid")
        safe = []
        for match in matches:
            if not isinstance(match, dict) or not isinstance(match.get("competition"), dict) or match["competition"].get("id") != competition_id:
                raise ValueError("league_publication_invalid")
            ident = match.get("source_event_id")
            if not isinstance(ident, str) or not ident.strip() or ident in seen:
                raise ValueError("league_publication_invalid")
            seen.add(ident); safe.append(project_publication_match(match))
        content = {"competition_id": competition_id, "snapshot_id": snapshot.get("snapshot_id"),
                   "snapshot_at": snapshot.get("snapshot_at"), "matches": sorted(safe, key=lambda row: row["source_event_id"])}
        vector[key] = {name: content[name] for name in ("snapshot_id", "snapshot_at")}
        vector[key]["content_sha256"] = _hash(content)
    _validate_vector(vector)
    return dict(sorted(vector.items()))


def publication_vector(snapshot: dict) -> dict:
    manifest = snapshot.get("league_publication")
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "components"} or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 or (snapshot.get("competition") or {}).get("id") != "multi_league":
        raise ValueError("league_publication_invalid")
    vector = manifest["components"]; _validate_vector(vector)
    if not vector or not isinstance(snapshot.get("matches"), list):
        raise ValueError("league_publication_invalid")
    partitions = {key[5:]: {"competition": {"id": key[5:]}, "snapshot_id": value["snapshot_id"], "snapshot_at": value["snapshot_at"], "matches": []} for key, value in vector.items()}
    for match in snapshot["matches"]:
        if not isinstance(match, dict) or match != project_publication_match(match):
            raise ValueError("league_publication_invalid")
        cid = (match.get("competition") or {}).get("id")
        if cid not in partitions:
            raise ValueError("league_publication_invalid")
        partitions[cid]["matches"].append(match)
    if build_publication_vector(list(partitions.values())) != vector:
        raise ValueError("league_publication_invalid")
    acceptance = snapshot.get("league_acceptance")
    if acceptance is not None:
        from worldcup.league_acceptance import acceptance_row_is_active
        if not isinstance(acceptance, dict):
            raise ValueError("league_publication_invalid")
        rows = acceptance.get("competitions", {})
        if not isinstance(rows, dict):
            raise ValueError("league_publication_invalid")
        active = {"odds:" + cid for cid, row in rows.items() if acceptance_row_is_active(row, cid)}
        if active != set(vector):
            raise ValueError("league_publication_invalid")
    return vector


def validate_publication_transition(previous: dict | None, current: dict) -> None:
    vector = publication_vector(current) if "league_publication" in current else None
    if previous is None:
        return
    if "league_publication" in previous:
        if vector is None:
            raise ValueError("league_publication_contract_required")
        previous_vector = publication_vector(previous)
        validate_component_vector(previous_vector, vector)
        _validate_event_membership(previous, current, set(previous_vector), set(vector))
    elif vector is not None:
        try:
            lower = _time(previous["snapshot_at"])
            old_components = previous["components"]
            if not isinstance(old_components, list) or not old_components:
                raise ValueError()
            membership = set()
            for row in old_components:
                cid = row["competition_id"]; key = "odds:" + cid
                if cid in membership or key not in vector:
                    raise ValueError()
                membership.add(cid)
                new = vector[key]
                old_matches = sorted([project_publication_match(m) for m in previous["matches"] if m["competition"]["id"] == cid], key=lambda m: m["source_event_id"])
                new_matches = sorted([m for m in current["matches"] if m["competition"]["id"] == cid], key=lambda m: m["source_event_id"])
                unchanged = new["snapshot_id"] == row["snapshot_id"] and old_matches == new_matches
                if not unchanged and _time(new["snapshot_at"]) <= lower:
                    raise ValueError()
            if any(_time(value["snapshot_at"]) <= lower for key, value in vector.items() if key[5:] not in membership):
                raise ValueError()
            if any(match["competition"]["id"] not in membership for match in previous["matches"]):
                raise ValueError()
            _validate_event_membership(
                previous,
                current,
                {"odds:" + competition_id for competition_id in membership},
                set(vector),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("league_publication_migration_required") from None


def _write_state(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical_json(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def supersede_pending(*, root: Path, reason: str, now: str, expected_body_sha256: str) -> dict:
    """CAS retirement; caller proves this exact post-lineup payload has expired."""
    from worldcup.league_daily_runner import read_daily_publication, _safe_path
    _time(now)
    if reason != 'post_lineup_pending_expired':
        raise ValueError('league_publication_invalid')
    directory = Path(root) / 'data/local/leagues'
    path = directory / 'publication_state.json'
    _safe_path(path); _safe_path(directory / 'publication.lock')
    with (directory / 'publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_daily_publication(root)
        pending = state['pending']
        if pending is None or pending['body_sha256'] != expected_body_sha256:
            return {'status': 'rejected', 'reason': 'league_publication_pending_changed'}
        state['superseded'].append({'pending': pending, 'reason': reason, 'at': now})
        state['pending'] = None
        _write_state(path, state)
        return {'status': 'superseded'}


def deliver_league_publication(*, root: Path, endpoint: str, snapshot: dict | None,
                              publish_fn, now: str) -> dict:
    """publish_fn(*, payload, endpoint, timestamp) must sign this frozen payload."""
    directory = Path(root)/"data/local/leagues"; directory.mkdir(parents=True, exist_ok=True)
    path = directory/"publication_state.json"
    with (directory/"publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(path.read_text()) if path.exists() else {"schema_version": 1, "components": {}, "pending": None, "sent": None, "superseded": []}
            if state.get("schema_version") != 1:
                raise ValueError("league_publication_invalid")
            pending = state["pending"]
            if pending and pending["endpoint"] != endpoint:
                return {"status": "rejected", "reason": "league_publication_endpoint_mismatch"}
            if not pending:
                if snapshot is None:
                    return {"status": "rejected", "reason": "league_publication_missing"}
                vector = publication_vector(snapshot)
                validate_component_vector(state["components"], vector)
                payload = build_ingest_payload(snapshot, generated_at=now)
                from worldcup.league_acceptance import acceptance_fingerprint
                pending = {"endpoint": endpoint, "payload": payload, "body": canonical_json(payload),
                           "body_sha256": _hash(payload), "components": vector,
                           "accepted_fingerprint": acceptance_fingerprint(snapshot.get("league_acceptance") or {})}
                state["pending"] = pending; _write_state(path, state)
            if pending["body"] != canonical_json(pending["payload"]) or pending["body_sha256"] != _hash(pending["payload"]) or pending["components"] != publication_vector(pending["payload"]["snapshot"]):
                raise ValueError("league_publication_invalid")
            from worldcup.league_acceptance import acceptance_fingerprint
            if pending["accepted_fingerprint"] != acceptance_fingerprint(pending["payload"]["snapshot"].get("league_acceptance") or {}):
                raise ValueError("league_publication_invalid")
            try:
                validate_component_vector(state["components"], pending["components"])
            except ValueError as exc:
                state["superseded"].append({"pending": pending, "reason": str(exc), "at": now})
                state["pending"] = None; _write_state(path, state)
                return {"status": "rejected", "reason": str(exc)}
            try:
                result = publish_fn(payload=json.loads(pending["body"]), endpoint=endpoint, timestamp=now)
            except Exception:
                return {"status": "pending", "reason": "league_publication_send_failed"}
            status = result.get("status") if isinstance(result, dict) else None
            if status in {"stored", "duplicate"}:
                expected_ids = {"run_id": pending["payload"]["run_id"], "snapshot_id": pending["payload"]["snapshot_id"],
                                "idempotency_key": f"{pending['payload']['run_id']}:{pending['payload']['snapshot_id']}"}
                if any(key in result and result[key] != value for key, value in expected_ids.items()):
                    return {"status": "rejected", "reason": "league_publication_receipt_mismatch"}
            if status not in {"stored", "duplicate"}:
                if status == "rejected" and result.get("reason") in {"league_component_regression", "league_component_conflict"}:
                    state["superseded"].append({"pending": pending, "reason": result["reason"], "at": now})
                    state["pending"] = None
                    _write_state(path, state)
                return {"status": "rejected" if status == "rejected" else "pending",
                        "reason": result.get("reason") if isinstance(result, dict) and result.get("reason") in ERRORS else "league_publication_not_accepted"}
            state["components"] = pending["components"]
            state["sent"] = {"status": status, "body_sha256": pending["body_sha256"], "at": now, "endpoint": endpoint}
            _write_state(path, state)
            state["pending"] = None; _write_state(path, state)
            return {"status": status}
        except (ValueError, KeyError, TypeError):
            return {"status": "rejected", "reason": "league_publication_invalid"}
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
