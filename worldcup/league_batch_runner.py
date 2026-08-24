from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, formal_single_match_competitions
from worldcup.league_competition_pipeline import build_league_competition_snapshot
from worldcup.league_live_store import LeagueLiveStore
from worldcup.league_acceptance import acceptance_fingerprint, acceptance_row_is_active
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


def _validate_expected_snapshot(
    snapshot: Any,
    competition_id: str,
    expected_event_ids: Sequence[str],
) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise ValueError("planned_refresh_snapshot_invalid")
    expected = {str(value).strip() for value in expected_event_ids}
    if not expected or "" in expected:
        raise ValueError("planned_refresh_expected_events_invalid")
    matches = snapshot.get("matches")
    if not isinstance(matches, list) or not matches:
        raise ValueError("planned_refresh_trigger_events_missing")
    decisions: dict[str, str] = {}
    for match in matches:
        if not isinstance(match, Mapping):
            raise ValueError("planned_refresh_snapshot_invalid")
        declared = match.get("competition")
        event_id = match.get("source_event_id")
        decision = match.get("match_decision")
        label = decision.get("label") if isinstance(decision, Mapping) else None
        if (
            not isinstance(declared, Mapping)
            or declared.get("id") != competition_id
            or not isinstance(event_id, str)
            or not event_id.strip()
            or event_id in decisions
        ):
            raise ValueError("planned_refresh_snapshot_invalid")
        decisions[event_id] = str(label or "")
    if not expected.issubset(decisions) or any(
        decisions[event_id] not in {"MATCH_PICK", "NO_CLEAN_MARKET"}
        for event_id in expected
    ):
        raise ValueError("planned_refresh_trigger_events_missing")
    return dict(snapshot)


def run_league_batch(
    *,
    root: str | Path,
    observed_at: str,
    live: bool = False,
    write: bool = False,
    odds_payloads: dict[str, list[dict[str, Any]]] | None = None,
    env_loader: Callable[..., Any] | None = None,
    odds_fetcher: Callable[..., Any] | None = None,
    score_fetcher: Callable[..., Any] | None = None,
    snapshot_builder: Callable[..., dict[str, Any]] = build_league_competition_snapshot,
    acceptance_report: dict[str, Any] | None = None,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
    planned_competition_ids: Sequence[str] | None = None,
    live_env: Mapping[str, str] | None = None,
    store_factory: Callable[[str | Path], Any] = LeagueLiveStore,
    expected_event_ids_by_competition: Mapping[str, Sequence[str]] | None = None,
    expected_snapshot_ids_by_competition: Mapping[str, str] | None = None,
    commit_callback: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    del score_fetcher
    acceptance_rows = (
        acceptance_report.get("competitions")
        if isinstance(acceptance_report, dict) and acceptance_report.get("schema_version") == 1
        else None
    )
    if (live or write) and not isinstance(acceptance_rows, dict):
        return {"status": "blocked", "reason": "live_acceptance_not_enabled"}
    if live != write:
        return {"status": "blocked", "reason": "live_write_must_be_explicit"}
    if live and identity_registry is None:
        return {"status": "blocked", "reason": "strict_identity_registry_required"}
    if planned_competition_ids is None:
        selected_ids = None
    else:
        selected_ids = [str(value) for value in planned_competition_ids]
        if (
            not selected_ids
            or len(set(selected_ids)) != len(selected_ids)
            or not set(selected_ids).issubset(FORMAL_SINGLE_MATCH_IDS)
        ):
            return {"status": "blocked", "reason": "planned_competitions_invalid"}
    payloads = dict(odds_payloads or {})
    expected_events = (
        dict(expected_event_ids_by_competition)
        if isinstance(expected_event_ids_by_competition, Mapping)
        else {}
    )
    expected_snapshot_ids = (
        dict(expected_snapshot_ids_by_competition)
        if isinstance(expected_snapshot_ids_by_competition, Mapping)
        else {}
    )
    competitions: dict[str, dict[str, Any]] = {}
    committed_receipts: list[dict[str, Any]] = []
    store = store_factory(root) if live and write else None
    resolved_env = dict(live_env) if isinstance(live_env, Mapping) else None
    registered_profiles = {
        profile.id: profile for profile in formal_single_match_competitions()
    }
    profiles = (
        list(registered_profiles.values())
        if selected_ids is None
        else [registered_profiles[competition_id] for competition_id in selected_ids]
    )
    for profile in profiles:
        if live and write:
            acceptance = acceptance_rows.get(profile.id)
            if not acceptance_row_is_active(acceptance, profile.id):
                competitions[profile.id] = {"status": "blocked", "reason": "acceptance_not_active"}
                continue
        if (
            profile.id not in payloads
            and live
            and write
            and odds_fetcher is not None
            and (resolved_env is not None or env_loader is not None)
        ):
            if resolved_env is None:
                loaded = env_loader() if env_loader is not None else None
                resolved_env = dict(loaded) if isinstance(loaded, Mapping) else {}
            try:
                payloads[profile.id] = odds_fetcher(profile.theoddsapi_sport_key, resolved_env)
            except Exception as exc:
                competitions[profile.id] = {"status": "error", "reason": type(exc).__name__}
                continue
        if profile.id not in payloads:
            competitions[profile.id] = {
                "status": "blocked",
                "reason": "odds_payload_unavailable" if live else "disabled_until_live_acceptance",
            }
            continue
        try:
            if live:
                snapshot = snapshot_builder(
                    payloads[profile.id],
                    profile.id,
                    observed_at,
                    identity_registry=identity_registry,
                )
            else:
                snapshot = snapshot_builder(payloads[profile.id], profile.id, observed_at)
            if profile.id in expected_events:
                snapshot = _validate_expected_snapshot(
                    snapshot,
                    profile.id,
                    expected_events[profile.id],
                )
            count = len(snapshot.get("matches") or [])
            if store is not None:
                identity = hashlib.sha256(f"{profile.id}|{observed_at}".encode("utf-8")).hexdigest()[:20]
                expected_snapshot_id = expected_snapshot_ids.get(profile.id)
                snapshot = {
                    **snapshot,
                    "run_id": str(snapshot.get("run_id") or f"league-{identity}"),
                    "snapshot_id": str(
                        expected_snapshot_id
                        or snapshot.get("snapshot_id")
                        or f"league-{identity}"
                    ),
                }
                commit_status = store.commit_snapshot(profile.id, snapshot)
                receipt = {
                    "competition": {"id": profile.id},
                    "snapshot_id": snapshot["snapshot_id"],
                    "commit_status": str(commit_status),
                }
                if commit_callback is not None:
                    commit_callback(receipt)
                committed_receipts.append(receipt)
                competitions[profile.id] = {
                    "status": "built" if count else "empty",
                    "match_count": count,
                    "snapshot_id": snapshot["snapshot_id"],
                    "commit_status": str(commit_status),
                }
            else:
                competitions[profile.id] = {"status": "built" if count else "empty", "match_count": count}
        except Exception as exc:
            competitions[profile.id] = {"status": "error", "reason": type(exc).__name__}
    statuses = {row["status"] for row in competitions.values()}
    if "error" in statuses:
        batch_status = "partial" if statuses - {"error", "blocked"} else "error"
    else:
        batch_status = "dry_run"
    if live and write and batch_status == "dry_run":
        batch_status = "refreshed"
    return {
        "status": batch_status,
        "competitions": competitions,
        "snapshots": committed_receipts,
    }


def run_planned_league_refresh(
    *,
    root: str | Path,
    observed_at: str,
    competition_ids: Sequence[str],
    env: Mapping[str, str],
    odds_fetcher: Callable[..., Any],
    acceptance_report: dict[str, Any],
    identity_registry: LeagueTeamIdentityRegistry,
    expected_event_ids_by_competition: Mapping[str, Sequence[str]],
    expected_snapshot_ids_by_competition: Mapping[str, str],
    guarded_acceptance_fingerprint: str,
    snapshot_builder: Callable[..., dict[str, Any]] = build_league_competition_snapshot,
    store_factory: Callable[[str | Path], Any] = LeagueLiveStore,
    commit_callback: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Refresh only planned sport keys and return receipts after durable store commits."""
    if not isinstance(env, Mapping) or not env:
        return {"status": "blocked", "reason": "planned_refresh_env_invalid"}
    if odds_fetcher is None:
        return {"status": "blocked", "reason": "planned_refresh_fetcher_missing"}
    selected = [str(value) for value in competition_ids]
    if (
        not isinstance(expected_event_ids_by_competition, Mapping)
        or set(expected_event_ids_by_competition) != set(selected)
        or any(
            not isinstance(rows, Sequence)
            or isinstance(rows, (str, bytes))
            or not rows
            for rows in expected_event_ids_by_competition.values()
        )
    ):
        return {"status": "blocked", "reason": "planned_refresh_expected_events_invalid"}
    if (
        not isinstance(expected_snapshot_ids_by_competition, Mapping)
        or set(expected_snapshot_ids_by_competition) != set(selected)
        or any(
            not isinstance(snapshot_id, str)
            or not snapshot_id.startswith("league-attempt-")
            for snapshot_id in expected_snapshot_ids_by_competition.values()
        )
    ):
        return {"status": "blocked", "reason": "planned_refresh_snapshot_ids_invalid"}
    if (
        not isinstance(guarded_acceptance_fingerprint, str)
        or guarded_acceptance_fingerprint != acceptance_fingerprint(acceptance_report)
    ):
        return {"status": "blocked", "reason": "planned_refresh_acceptance_changed"}
    return run_league_batch(
        root=root,
        observed_at=observed_at,
        live=True,
        write=True,
        live_env=env,
        odds_fetcher=odds_fetcher,
        acceptance_report=acceptance_report,
        identity_registry=identity_registry,
        planned_competition_ids=competition_ids,
        snapshot_builder=snapshot_builder,
        store_factory=store_factory,
        expected_event_ids_by_competition=expected_event_ids_by_competition,
        expected_snapshot_ids_by_competition=expected_snapshot_ids_by_competition,
        commit_callback=commit_callback,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline six-league batch pipeline.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    result = run_league_batch(
        root=args.root,
        observed_at=args.observed_at,
        live=args.live,
        write=args.write,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
