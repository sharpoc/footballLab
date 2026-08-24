from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from worldcup.competitions import formal_single_match_competitions
from worldcup.league_competition_pipeline import build_league_competition_snapshot


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
) -> dict[str, Any]:
    del root, env_loader, odds_fetcher, score_fetcher
    if live or write:
        return {"status": "blocked", "reason": "live_acceptance_not_enabled"}
    payloads = odds_payloads or {}
    competitions: dict[str, dict[str, Any]] = {}
    for profile in formal_single_match_competitions():
        if profile.id not in payloads:
            competitions[profile.id] = {
                "status": "blocked",
                "reason": "disabled_until_live_acceptance",
            }
            continue
        try:
            snapshot = snapshot_builder(payloads[profile.id], profile.id, observed_at)
            count = len(snapshot.get("matches") or [])
            competitions[profile.id] = {"status": "built" if count else "empty", "match_count": count}
        except (KeyError, OSError, TypeError, ValueError) as exc:
            competitions[profile.id] = {"status": "error", "reason": type(exc).__name__}
    statuses = {row["status"] for row in competitions.values()}
    if "error" in statuses:
        batch_status = "partial" if statuses - {"error", "blocked"} else "error"
    else:
        batch_status = "dry_run"
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
