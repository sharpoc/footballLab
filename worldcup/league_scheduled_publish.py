from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.league_acceptance import (
    LeagueAcceptanceStore,
    acceptance_fingerprint,
    acceptance_row_is_active,
)
from worldcup.league_lifecycle import run_league_lifecycle
from worldcup.league_live_planner import plan_league_live_refresh
from worldcup.quota import load_quota_ledger
from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_acceptance_report(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != 1
        or not isinstance(value.get("competitions"), Mapping)
        or not set(value["competitions"]).issubset(FORMAL_SINGLE_MATCH_IDS)
    ):
        raise ValueError("league_aggregate_acceptance_missing")
    competitions: dict[str, dict[str, Any]] = {}
    for competition_id, row in value["competitions"].items():
        if not isinstance(row, Mapping):
            raise ValueError("league_aggregate_acceptance_missing")
        fingerprints = row.get("fingerprints")
        if not isinstance(fingerprints, Mapping):
            fingerprints = {}
        competitions[competition_id] = {
            "competition_id": str(row.get("competition_id") or ""),
            "state": str(row.get("state") or ""),
            "reason": str(row["reason"]) if row.get("reason") is not None else None,
            "fingerprints": {
                name: str(fingerprints.get(name) or "")
                for name in (
                    "sport_catalog", "odds_sample", "team_identity", "result_contract"
                )
                if fingerprints.get(name)
            },
        }
    return {"schema_version": 1, "competitions": competitions}


def build_aggregate_league_snapshot(
    *,
    root: str | Path,
    snapshots: list[Mapping[str, Any]],
    expected_acceptance_fingerprint: str | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    loaded_acceptance = LeagueAcceptanceStore(
        root_path / "data/local/leagues/acceptance.json"
    ).read()
    acceptance = _project_acceptance_report(loaded_acceptance)
    if (
        expected_acceptance_fingerprint is not None
        and acceptance_fingerprint(acceptance) != expected_acceptance_fingerprint
    ):
        raise ValueError("league_aggregate_acceptance_changed")
    rows = acceptance.get("competitions") if isinstance(acceptance, Mapping) else {}
    active_ids = {
        str(competition_id)
        for competition_id, row in (rows or {}).items()
        if acceptance_row_is_active(row, str(competition_id))
    }

    by_competition: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise ValueError("league_aggregate_snapshot_invalid")
        competition = snapshot.get("competition")
        competition_id = str(competition.get("id") or "") if isinstance(competition, Mapping) else ""
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            raise ValueError("league_aggregate_competition_invalid")
        if competition_id not in active_ids:
            raise ValueError("league_aggregate_competition_not_active")
        if competition_id in by_competition:
            raise ValueError("league_aggregate_duplicate_component")
        by_competition[competition_id] = dict(snapshot)

    for competition_id in sorted(active_ids - set(by_competition)):
        path = root_path / "data/cache/leagues" / competition_id / "snapshot.json"
        if path.exists():
            cached = _read_json(path)
            if isinstance(cached, Mapping):
                by_competition[competition_id] = dict(cached)

    missing = sorted(active_ids - set(by_competition))
    if missing:
        raise ValueError("league_aggregate_active_snapshot_missing")
    matches: list[dict[str, Any]] = []
    components: list[dict[str, str]] = []
    seen: set[str] = set()
    snapshot_times: list[str] = []
    for competition_id in sorted(by_competition):
        snapshot = by_competition[competition_id]
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise ValueError("league_aggregate_snapshot_id_missing")
        declared = snapshot.get("competition")
        if not isinstance(declared, Mapping) or declared.get("id") != competition_id:
            raise ValueError("league_aggregate_competition_mismatch")
        components.append({"competition_id": competition_id, "snapshot_id": snapshot_id})
        snapshot_times.append(str(snapshot.get("snapshot_at") or ""))
        for raw_match in snapshot.get("matches") or []:
            if not isinstance(raw_match, Mapping):
                raise ValueError("league_aggregate_match_invalid")
            match = dict(raw_match)
            match_competition = match.get("competition")
            if not isinstance(match_competition, Mapping) or match_competition.get("id") != competition_id:
                raise ValueError("league_aggregate_match_competition_mismatch")
            event_id = str(match.get("source_event_id") or "").strip()
            if not event_id or event_id in seen:
                raise ValueError("league_aggregate_match_identity_invalid")
            seen.add(event_id)
            matches.append(match)

    if not components:
        raise ValueError("league_aggregate_empty")
    digest_payload = json.dumps(components, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:20]
    statistics_path = root_path / "data/local/leagues/statistics.json"
    statistics = _read_json(statistics_path) if statistics_path.exists() else None
    result: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_id": f"league-aggregate-{digest}",
        "snapshot_at": max(snapshot_times),
        "run": {"run_id": f"league-aggregate-{digest}"},
        "competition": {"id": "multi_league", "name": "联赛聚合"},
        "components": components,
        "matches": matches,
        "league_acceptance": acceptance or {"schema_version": 1, "competitions": {}},
        "data_quality": {"missing_competition_snapshots": missing},
    }
    if isinstance(statistics, Mapping):
        result["league_statistics"] = dict(statistics)
    return result


def _read_committed_refresh_snapshots(
    root: str | Path, refreshed: list[Any]
) -> list[dict[str, Any]]:
    if not isinstance(refreshed, list) or not refreshed:
        raise ValueError("league_refresh_receipts_required")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in refreshed:
        if not isinstance(row, Mapping) or set(row) != {
            "competition", "snapshot_id", "commit_status"
        }:
            raise ValueError("league_refresh_snapshot_invalid")
        competition = row.get("competition")
        if not isinstance(competition, Mapping) or set(competition) != {"id"}:
            raise ValueError("league_refresh_snapshot_invalid")
        competition_id = str(competition.get("id") or "")
        snapshot_id = row.get("snapshot_id")
        if (
            competition_id not in FORMAL_SINGLE_MATCH_IDS
            or competition_id in seen
            or not isinstance(snapshot_id, str)
            or not snapshot_id.strip()
            or row.get("commit_status") not in {"stored", "unchanged"}
        ):
            raise ValueError("league_refresh_snapshot_invalid")
        seen.add(competition_id)
        normalized.append((competition_id, snapshot_id))
    committed: list[dict[str, Any]] = []
    for competition_id, snapshot_id in normalized:
        path = Path(root) / "data/cache/leagues" / competition_id / "snapshot.json"
        if not path.exists():
            raise ValueError("league_refresh_snapshot_not_committed")
        stored = _read_json(path)
        if not isinstance(stored, Mapping) or stored.get("snapshot_id") != snapshot_id:
            raise ValueError("league_refresh_snapshot_commit_mismatch")
        committed.append(dict(stored))
    return committed


def _project_publish_result(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {"status": "invalid"}
    status = value.get("status")
    if status not in {"stored", "duplicate", "error", "rejected", "failed"}:
        return {"status": "invalid"}
    return {"status": str(status)}


def publish_committed_league_snapshots(
    *,
    root: str | Path,
    snapshot_receipts: list[Any],
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    expected_acceptance_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Re-read committed partitions, build one complete aggregate, and publish it."""
    try:
        committed = _read_committed_refresh_snapshots(root, snapshot_receipts)
        aggregate = build_aggregate_league_snapshot(
            root=root,
            snapshots=committed,
            expected_acceptance_fingerprint=expected_acceptance_fingerprint,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc)
        if not reason.startswith("league_"):
            reason = "league_aggregate_invalid"
        return {
            "status": "publish_failed",
            "reason": reason,
            "publish": None,
            "aggregate": None,
        }
    try:
        published_value = publish_fn(aggregate)
        published = _project_publish_result(published_value)
    except Exception:
        return {
            "status": "publish_failed",
            "reason": "league_aggregate_publisher_failed",
            "publish": None,
            "aggregate": None,
        }
    if published.get("status") not in {"stored", "duplicate"}:
        return {
            "status": "publish_failed",
            "reason": "league_aggregate_ingest_not_confirmed",
            "publish": published,
            "aggregate": None,
        }
    run = aggregate.get("run") if isinstance(aggregate.get("run"), Mapping) else {}
    return {
        "status": "published",
        "publish": published,
        "aggregate": {
            "snapshot_id": aggregate["snapshot_id"],
            "run_id": str(run.get("run_id") or ""),
            "components": list(aggregate.get("components") or []),
        },
    }


def build_local_league_plan(*, root: str | Path, now: str) -> dict[str, Any]:
    root_path = Path(root)
    acceptance = LeagueAcceptanceStore(
        root_path / "data/local/leagues/acceptance.json"
    ).read()
    rows = acceptance.get("competitions") if isinstance(acceptance, Mapping) else {}
    states = {
        str(competition_id): str(row.get("state") or "disabled_until_live_acceptance")
        for competition_id, row in (rows or {}).items()
        if acceptance_row_is_active(row, str(competition_id))
    }
    events_by_competition: dict[str, list[dict[str, Any]]] = {}
    for competition_id in sorted(states):
        event_path = root_path / "data/probe/leagues" / competition_id / "events.json"
        if not event_path.exists():
            events_by_competition[competition_id] = []
            continue
        raw_events = _read_json(event_path)
        if not isinstance(raw_events, list):
            events_by_competition[competition_id] = []
            continue
        events = [dict(event) for event in raw_events if isinstance(event, Mapping)]
        snapshot_path = root_path / "data/cache/leagues" / competition_id / "snapshot.json"
        if snapshot_path.exists():
            snapshot = _read_json(snapshot_path)
            decisions = {
                str(match.get("source_event_id") or ""): match.get("match_decision")
                for match in (snapshot.get("matches") or [])
                if isinstance(match, Mapping) and isinstance(match.get("match_decision"), Mapping)
            } if isinstance(snapshot, Mapping) else {}
            for event in events:
                decision = decisions.get(str(event.get("id") or ""))
                if decision is not None:
                    event["match_decision"] = decision
        events_by_competition[competition_id] = events
    providers = load_quota_ledger(root_path / "data/cache/quota.json").get("providers") or {}
    remaining_values = [
        row.get("remaining")
        for row in providers.values()
        if isinstance(row, Mapping) and isinstance(row.get("remaining"), int)
    ]
    quota_upper_bound = max(remaining_values) if remaining_values else None
    return plan_league_live_refresh(
        now=now,
        events_by_competition=events_by_competition,
        acceptance_by_competition=states,
        quota_remaining=quota_upper_bound,
    )


def run_local_league_scheduler(
    *,
    root: str | Path,
    now: str,
    live: bool = False,
    write: bool = False,
    pending_payload: Mapping[str, Any] | None = None,
    env_loader: Callable[[], Mapping[str, str]] | None = None,
    refresh_fn: Callable[..., Mapping[str, Any]] | None = None,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    acceptance = LeagueAcceptanceStore(
        root_path / "data/local/leagues/acceptance.json"
    ).read()
    rows = acceptance.get("competitions") if isinstance(acceptance, Mapping) else {}
    active_ids = [
        str(competition_id)
        for competition_id, row in (rows or {}).items()
        if acceptance_row_is_active(row, str(competition_id))
    ]
    lifecycle = run_league_lifecycle(
        root=root_path,
        competition_ids=sorted(active_ids),
        write=live and write,
    )
    plan = build_local_league_plan(root=root_path, now=now)
    scheduler = run_league_scheduled_publish(
        root=root_path,
        now=now,
        plan=plan,
        live=live,
        write=write,
        pending_payload=pending_payload,
        env_loader=env_loader,
        refresh_fn=refresh_fn,
        publish_fn=publish_fn,
    )
    return {"status": scheduler["status"], "lifecycle": lifecycle, "scheduler": scheduler}


def run_league_scheduled_publish(
    *,
    root: str | Path,
    now: str,
    plan: Mapping[str, Any],
    live: bool = False,
    write: bool = False,
    pending_payload: Mapping[str, Any] | None = None,
    env_loader: Callable[[], Mapping[str, str]] | None = None,
    refresh_fn: Callable[..., Mapping[str, Any]] | None = None,
    publish_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    safe_plan = {"requests": list(plan.get("requests") or []), "estimated_credits": int(plan.get("estimated_credits") or 0)}
    if not live:
        return {"status": "dry_run", "plan": safe_plan, "refresh": None, "publish": None}
    if not write:
        return {"status": "blocked", "reason": "league_live_write_not_confirmed"}
    if env_loader is None or publish_fn is None:
        return {"status": "blocked", "reason": "league_live_dependencies_missing"}
    env = env_loader()
    if not isinstance(env, Mapping):
        return {"status": "blocked", "reason": "league_live_env_invalid"}
    if pending_payload is not None:
        try:
            published = _project_publish_result(publish_fn(pending_payload))
        except Exception:
            published = {"status": "failed"}
        return {
            "status": "published_pending" if published.get("status") in {"stored", "duplicate"} else "publish_pending_failed",
            "plan": safe_plan,
            "refresh": None,
            "publish": published,
        }
    if not safe_plan["requests"]:
        return {"status": "not_due", "plan": safe_plan, "refresh": None, "publish": None}
    if refresh_fn is None:
        return {"status": "blocked", "reason": "league_live_refresh_missing"}
    loaded_acceptance = LeagueAcceptanceStore(
        Path(root) / "data/local/leagues/acceptance.json"
    ).read()
    try:
        acceptance = _project_acceptance_report(loaded_acceptance)
    except ValueError:
        return {"status": "blocked", "reason": "league_aggregate_acceptance_missing"}
    guarded_fingerprint = acceptance_fingerprint(acceptance)
    try:
        refreshed_value = refresh_fn(root=root, now=now, plan=safe_plan, env=env)
    except Exception:
        refreshed_value = None
    if not isinstance(refreshed_value, Mapping) or set(refreshed_value) != {
        "status", "competitions", "snapshots"
    }:
        return {
            "status": "refresh_failed",
            "plan": safe_plan,
            "refresh": {"status": "invalid"},
            "publish": None,
        }
    refresh_status = refreshed_value.get("status")
    snapshots = refreshed_value.get("snapshots")
    refreshed = {
        "status": str(refresh_status) if isinstance(refresh_status, str) else "invalid",
        "snapshot_count": len(snapshots) if isinstance(snapshots, list) else 0,
    }
    if refresh_status != "refreshed" or not isinstance(snapshots, list):
        return {"status": "refresh_failed", "plan": safe_plan, "refresh": refreshed, "publish": None}
    publication = publish_committed_league_snapshots(
        root=root,
        snapshot_receipts=snapshots,
        publish_fn=publish_fn,
        expected_acceptance_fingerprint=guarded_fingerprint,
    )
    ok = publication.get("status") == "published"
    return {
        "status": "published" if ok else "publish_failed",
        **({"reason": publication["reason"]} if not ok and publication.get("reason") else {}),
        "plan": safe_plan,
        "refresh": refreshed,
        "publish": publication.get("publish"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the six-league lifecycle scheduler; defaults to dry-run.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)
    result = run_local_league_scheduler(
        root=args.root,
        now=args.now,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
