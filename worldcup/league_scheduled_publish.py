from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Mapping


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
        published = dict(publish_fn(pending_payload))
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
    refreshed = dict(refresh_fn(root=root, now=now, plan=safe_plan, env=env))
    if refreshed.get("status") != "refreshed":
        return {"status": "refresh_failed", "plan": safe_plan, "refresh": refreshed, "publish": None}
    snapshots = refreshed.get("snapshots") if isinstance(refreshed.get("snapshots"), list) else []
    published: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping) or not str(snapshot.get("snapshot_id") or ""):
            continue
        published.append(dict(publish_fn(snapshot)))
    ok = bool(published) and all(row.get("status") in {"stored", "duplicate"} for row in published)
    return {
        "status": "published" if ok else "publish_failed",
        "plan": safe_plan,
        "refresh": refreshed,
        "publish": published,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the six-league lifecycle scheduler; defaults to dry-run.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--now", required=True)
    args = parser.parse_args(argv)
    result = run_league_scheduled_publish(
        root=args.root,
        now=args.now,
        plan={"requests": [], "estimated_credits": 0},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
