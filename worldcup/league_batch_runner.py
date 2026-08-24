from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from worldcup.competitions import formal_single_match_competitions
from worldcup.league_competition_pipeline import build_league_competition_snapshot
from worldcup.league_live_store import LeagueLiveStore
from worldcup.league_acceptance import acceptance_row_is_active
from worldcup.league_team_identity import LeagueTeamIdentityRegistry


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
    payloads = odds_payloads or {}
    competitions: dict[str, dict[str, Any]] = {}
    store = LeagueLiveStore(root) if live and write else None
    live_env: dict[str, str] | None = None
    for profile in formal_single_match_competitions():
        if live and write:
            acceptance = acceptance_rows.get(profile.id)
            if not acceptance_row_is_active(acceptance, profile.id):
                competitions[profile.id] = {"status": "blocked", "reason": "acceptance_not_active"}
                continue
        if profile.id not in payloads and live and write and odds_fetcher is not None and env_loader is not None:
            if live_env is None:
                loaded = env_loader()
                live_env = dict(loaded) if isinstance(loaded, dict) else {}
            try:
                payloads[profile.id] = odds_fetcher(profile.theoddsapi_sport_key, live_env)
            except (KeyError, OSError, TypeError, ValueError) as exc:
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
            count = len(snapshot.get("matches") or [])
            if store is not None:
                identity = hashlib.sha256(f"{profile.id}|{observed_at}".encode("utf-8")).hexdigest()[:20]
                snapshot = {
                    **snapshot,
                    "run_id": str(snapshot.get("run_id") or f"league-{identity}"),
                    "snapshot_id": str(snapshot.get("snapshot_id") or f"league-{identity}"),
                }
                store.commit_snapshot(profile.id, snapshot)
            competitions[profile.id] = {"status": "built" if count else "empty", "match_count": count}
        except (KeyError, OSError, TypeError, ValueError) as exc:
            competitions[profile.id] = {"status": "error", "reason": type(exc).__name__}
    statuses = {row["status"] for row in competitions.values()}
    if "error" in statuses:
        batch_status = "partial" if statuses - {"error", "blocked"} else "error"
    else:
        batch_status = "dry_run"
    if live and write and batch_status == "dry_run":
        batch_status = "refreshed"
    return {"status": batch_status, "competitions": competitions}


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
