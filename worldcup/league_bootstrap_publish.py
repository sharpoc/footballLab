from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.ingest import build_ingest_request
from worldcup.league_acceptance import (
    LeagueAcceptanceStore,
    acceptance_fingerprint,
    acceptance_row_is_active,
)
from worldcup.league_batch_runner import run_planned_league_refresh
from worldcup.league_scheduled_publish import publish_committed_league_snapshots
from worldcup.league_team_identity import accepted_league_team_identity_registry
from worldcup.publish import DEFAULT_ENDPOINT, _default_sender
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.secrets import validate_hmac_secret
from worldcup.sources.theoddsapi import DEFAULT_MARKETS, fetch_odds_for_sport
from worldcup.theoddsapi_keys import choose_key_slot


BOOTSTRAP_STATE_RELATIVE_PATH = Path("data/local/leagues/bootstrap_publish_state.json")
BOOTSTRAP_LOCK_RELATIVE_PATH = Path("data/local/leagues/bootstrap_publish.lock")


def _utc(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("bootstrap_now_invalid")
    return parsed.astimezone(timezone.utc)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _blocked(reason: str, competition_id: str | None = None) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        **({"competition_id": competition_id} if competition_id else {}),
    }


def build_league_bootstrap_plan(*, root: str | Path, now: str) -> dict[str, Any]:
    root_path = Path(root)
    try:
        now_dt = _utc(now)
        acceptance = LeagueAcceptanceStore(
            root_path / "data/local/leagues/acceptance.json"
        ).read()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked("bootstrap_acceptance_invalid")
    if not isinstance(acceptance, Mapping):
        return _blocked("bootstrap_acceptance_invalid")
    rows = acceptance.get("competitions")
    if not isinstance(rows, Mapping):
        return _blocked("bootstrap_acceptance_invalid")
    active_ids = sorted(
        str(competition_id)
        for competition_id, row in rows.items()
        if acceptance_row_is_active(row, str(competition_id))
    )
    if not active_ids:
        return _blocked("bootstrap_active_competitions_missing")

    expected: dict[str, list[str]] = {}
    for competition_id in active_ids:
        path = root_path / "data/probe/leagues" / competition_id / "events.json"
        try:
            events = _read_json(path)
        except (OSError, json.JSONDecodeError):
            return _blocked("bootstrap_events_invalid", competition_id)
        if not isinstance(events, list):
            return _blocked("bootstrap_events_invalid", competition_id)
        event_ids: list[str] = []
        for event in events:
            if not isinstance(event, Mapping):
                return _blocked("bootstrap_events_invalid", competition_id)
            try:
                kickoff = _utc(str(event.get("commence_time") or event.get("kickoff_utc") or ""))
            except (TypeError, ValueError):
                return _blocked("bootstrap_events_invalid", competition_id)
            if kickoff <= now_dt:
                continue
            event_id = event.get("id")
            if not isinstance(event_id, str) or not event_id.strip():
                return _blocked("bootstrap_event_ids_invalid", competition_id)
            event_ids.append(event_id.strip())
        if not event_ids:
            return _blocked("bootstrap_future_events_missing", competition_id)
        if len(set(event_ids)) != len(event_ids):
            return _blocked("bootstrap_event_ids_invalid", competition_id)
        expected[competition_id] = sorted(event_ids)

    fingerprint = acceptance_fingerprint(acceptance)
    observed_at = now_dt.isoformat()
    attempts = {
        competition_id: "league-attempt-bootstrap-" + hashlib.sha256(
            f"{fingerprint}|{competition_id}|{observed_at}".encode("utf-8")
        ).hexdigest()[:20]
        for competition_id in active_ids
    }
    existing = [
        competition_id
        for competition_id in active_ids
        if (root_path / "data/cache/leagues" / competition_id / "snapshot.json").exists()
    ]
    return {
        "status": "ready",
        "competition_ids": active_ids,
        "expected_event_ids_by_competition": expected,
        "expected_snapshot_ids_by_competition": attempts,
        "acceptance_fingerprint": fingerprint,
        "existing_partition_ids": existing,
        "estimated_credits": len(active_ids) * len(DEFAULT_MARKETS),
    }


def _receipt_competitions(receipts: Any) -> set[str] | None:
    if not isinstance(receipts, list):
        return None
    result: set[str] = set()
    for receipt in receipts:
        competition = receipt.get("competition") if isinstance(receipt, Mapping) else None
        competition_id = competition.get("id") if isinstance(competition, Mapping) else None
        if not isinstance(competition_id, str) or not competition_id or competition_id in result:
            return None
        result.add(competition_id)
    return result


def _bootstrap_is_complete(root: Path, plan: Mapping[str, Any]) -> bool:
    path = root / BOOTSTRAP_STATE_RELATIVE_PATH
    try:
        state = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(state, Mapping)
        and state.get("schema_version") == 1
        and state.get("acceptance_fingerprint") == plan.get("acceptance_fingerprint")
        and state.get("competition_ids") == plan.get("competition_ids")
        and isinstance(state.get("aggregate_snapshot_id"), str)
        and bool(state.get("aggregate_snapshot_id"))
        and state.get("publish_status") in {"stored", "duplicate"}
    )


def _commit_bootstrap_state(root: Path, payload: Mapping[str, Any]) -> None:
    path = root / BOOTSTRAP_STATE_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
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


def run_league_bootstrap_publish(
    *,
    root: str | Path,
    now: str,
    live: bool = False,
    write: bool = False,
    force_initial: bool = False,
    endpoint: str = DEFAULT_ENDPOINT,
    env_loader: Callable[[], Mapping[str, str]] | None = None,
    odds_fetcher: Callable[..., Any] | None = None,
    refresh_fn: Callable[..., Mapping[str, Any]] = run_planned_league_refresh,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    identity_registry: Any = None,
    _lock_acquired: bool = False,
) -> dict[str, Any]:
    live_flags = (live, write, force_initial)
    if any(live_flags) and not all(live_flags):
        return _blocked("bootstrap_live_flags_invalid")
    if live and (endpoint == DEFAULT_ENDPOINT or not endpoint.startswith("https://")):
        return _blocked("bootstrap_endpoint_invalid")
    root_path = Path(root)
    if live and not _lock_acquired:
        lock_path = root_path / BOOTSTRAP_LOCK_RELATIVE_PATH
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _blocked("bootstrap_lock_contended")
            return run_league_bootstrap_publish(
                root=root_path,
                now=now,
                live=live,
                write=write,
                force_initial=force_initial,
                endpoint=endpoint,
                env_loader=env_loader,
                odds_fetcher=odds_fetcher,
                refresh_fn=refresh_fn,
                publish_fn=publish_fn,
                identity_registry=identity_registry,
                _lock_acquired=True,
            )
    plan = build_league_bootstrap_plan(root=root, now=now)
    if plan.get("status") != "ready":
        return plan
    if _bootstrap_is_complete(root_path, plan):
        return _blocked("bootstrap_already_complete")
    if not live:
        return {
            "status": "dry_run_ready",
            "plan": plan,
            "safety": {
                "read_env": False,
                "called_theoddsapi": False,
                "wrote_partitions": False,
                "published": False,
            },
        }
    if env_loader is None or odds_fetcher is None or publish_fn is None:
        return _blocked("bootstrap_live_dependencies_missing")
    try:
        env = env_loader()
    except Exception:
        return _blocked("bootstrap_env_invalid")
    if not isinstance(env, Mapping) or not env:
        return _blocked("bootstrap_env_invalid")
    registry = identity_registry or accepted_league_team_identity_registry()
    try:
        refreshed = refresh_fn(
            root=root,
            observed_at=_utc(now).isoformat(),
            competition_ids=plan["competition_ids"],
            env=env,
            odds_fetcher=odds_fetcher,
            acceptance_report=LeagueAcceptanceStore(
                Path(root) / "data/local/leagues/acceptance.json"
            ).read(),
            identity_registry=registry,
            expected_event_ids_by_competition=plan["expected_event_ids_by_competition"],
            expected_snapshot_ids_by_competition=plan["expected_snapshot_ids_by_competition"],
            guarded_acceptance_fingerprint=plan["acceptance_fingerprint"],
        )
    except Exception:
        refreshed = None
    status = refreshed.get("status") if isinstance(refreshed, Mapping) else "invalid"
    receipts = refreshed.get("snapshots") if isinstance(refreshed, Mapping) else None
    summary = {
        "status": str(status),
        "snapshot_count": len(receipts) if isinstance(receipts, list) else 0,
    }
    if status != "refreshed" or _receipt_competitions(receipts) != set(plan["competition_ids"]):
        return {"status": "refresh_failed", "refresh": summary, "publish": None}
    publication = publish_committed_league_snapshots(
        root=root,
        snapshot_receipts=receipts,
        publish_fn=publish_fn,
        expected_acceptance_fingerprint=plan["acceptance_fingerprint"],
    )
    if publication.get("status") != "published":
        return {
            "status": "publish_failed",
            "reason": publication.get("reason") or "league_aggregate_invalid",
            "refresh": summary,
            "publish": publication.get("publish"),
        }
    try:
        _commit_bootstrap_state(root_path, {
            "schema_version": 1,
            "acceptance_fingerprint": plan["acceptance_fingerprint"],
            "competition_ids": plan["competition_ids"],
            "aggregate_snapshot_id": publication["aggregate"]["snapshot_id"],
            "publish_status": publication["publish"]["status"],
        })
    except OSError:
        return {
            "status": "state_failed",
            "reason": "bootstrap_state_commit_failed",
            "refresh": summary,
            "publish": publication["publish"],
            "aggregate": publication["aggregate"],
        }
    return {
        "status": "published",
        "plan": {
            "competition_ids": plan["competition_ids"],
            "estimated_credits": plan["estimated_credits"],
        },
        "refresh": summary,
        "publish": publication["publish"],
        "aggregate": publication["aggregate"],
    }


def _cli_odds_fetcher(*, root: Path, quota_path: Path, observed_at: str):
    def fetch(sport_key: str, env: Mapping[str, str]) -> Any:
        providers = load_quota_ledger(quota_path).get("providers") or {}
        selected = choose_key_slot(env, providers)
        if selected is None:
            raise ValueError("bootstrap_quota_unavailable")
        result = fetch_odds_for_sport(
            api_key=selected.api_key,
            sport_key=sport_key,
            quota_path=quota_path,
            observed_at=observed_at,
            quota_provider=selected.provider,
            markets=DEFAULT_MARKETS,
        )
        if not isinstance(result.json_body, list):
            raise ValueError("bootstrap_odds_payload_invalid")
        return result.json_body

    return fetch


def _cli_publisher(*, env: Mapping[str, str], endpoint: str, timestamp: str):
    secret = env.get("INGEST_HMAC_SECRET")
    validate_hmac_secret(secret)

    def publish(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        request = build_ingest_request(
            snapshot=dict(snapshot), endpoint=endpoint, secret=str(secret), timestamp=timestamp
        )
        response = _default_sender(request)
        if not isinstance(response, Mapping) or response.get("http_status") not in range(200, 300):
            return {"status": "failed"}
        try:
            body = json.loads(str(response.get("body") or ""))
        except json.JSONDecodeError:
            return {"status": "failed"}
        status = body.get("status") if isinstance(body, Mapping) else None
        return {"status": status if status in {"stored", "duplicate"} else "failed"}

    return publish


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create and publish the first complete active-league snapshot; defaults to dry-run."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--now", default=None)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--force-initial", action="store_true")
    args = parser.parse_args(argv)
    live_flags = (args.live, args.write, args.force_initial)
    if any(live_flags) and not all(live_flags):
        print(json.dumps(_blocked("bootstrap_live_flags_invalid"), ensure_ascii=False, sort_keys=True))
        return 2
    if args.live and args.now is not None:
        print(json.dumps(_blocked("bootstrap_live_now_override_forbidden"), ensure_ascii=False, sort_keys=True))
        return 2
    if args.live and (args.endpoint == DEFAULT_ENDPOINT or not args.endpoint.startswith("https://")):
        print(json.dumps(_blocked("bootstrap_endpoint_invalid"), ensure_ascii=False, sort_keys=True))
        return 2
    root = Path(args.root)
    now = args.now or _now_utc_iso()
    if not args.live:
        result = run_league_bootstrap_publish(root=root, now=now)
    else:
        env = _load_env(root / args.env)
        try:
            publisher = _cli_publisher(env=env, endpoint=args.endpoint, timestamp=now)
        except ValueError:
            result = _blocked("bootstrap_secret_invalid")
        else:
            result = run_league_bootstrap_publish(
                root=root,
                now=now,
                live=args.live,
                write=args.write,
                force_initial=args.force_initial,
                endpoint=args.endpoint,
                env_loader=lambda: env,
                odds_fetcher=_cli_odds_fetcher(
                    root=root, quota_path=root / args.quota_path, observed_at=now
                ),
                publish_fn=publisher,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"dry_run_ready", "published"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
