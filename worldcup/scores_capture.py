"""Capture finished scores from The Odds API into the local results csv.

Default mode is a dry-run: no network access and no writes. Pass --live to fetch scores.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from worldcup.collectors.theoddsapi_scores import parse_theoddsapi_scores
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.results_capture import _load_rows, _write_rows, upsert_results
from worldcup.sources.theoddsapi_scores import fetch_worldcup_scores
from worldcup.theoddsapi_keys import choose_key_slot


def run_scores_capture(
    live: bool,
    env: dict[str, str],
    cache_path: str | Path = "data/cache/theoddsapi_scores.json",
    quota_path: str | Path = "data/cache/quota.json",
    results_out: str | Path = "data/local/results/wc2026_results.csv",
    transport: Callable[[str], object] | None = None,
    observed_at: str | None = None,
) -> dict:
    if not live:
        return {"status": "dry_run", "note": "pass --live to fetch scores (~2 credits)"}

    providers = load_quota_ledger(quota_path).get("providers", {})
    selected = choose_key_slot(env, providers)
    if selected is None:
        return {"status": "blocked", "reason": "quota_exhausted"}

    observed = observed_at or datetime.now(timezone.utc).isoformat()
    fetch_result = fetch_worldcup_scores(
        api_key=selected.api_key,
        transport=transport,
        cache_path=cache_path,
        quota_path=quota_path,
        observed_at=observed,
        quota_provider=selected.provider,
    )
    raw = fetch_result.json_body
    results = parse_theoddsapi_scores(raw)
    out = Path(results_out)
    rows, added, updated = upsert_results(results, _load_rows(out), observed)
    _write_rows(rows, out)
    return {
        "status": "captured",
        "events": len(raw),
        "completed": len(results),
        "added": added,
        "updated": updated,
        "total": len(rows),
        "slot": selected.slot,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture scores from The Odds API (default dry-run).")
    parser.add_argument("--live", action="store_true", help="Fetch for real (~2 credits).")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--cache-path", default="data/cache/theoddsapi_scores.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--out", default="data/local/results/wc2026_results.csv")
    args = parser.parse_args(argv)

    result = run_scores_capture(
        live=args.live,
        env=_load_env(args.env),
        cache_path=args.cache_path,
        quota_path=args.quota_path,
        results_out=args.out,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
