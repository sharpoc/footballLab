from __future__ import annotations

import argparse
import gzip
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from worldcup.collectors.openfootball import parse_openfootball_results
from worldcup.elo_local import compute_updated_world_tsv, freeze_baseline, has_baseline, load_baseline
from worldcup.finished_record import build_finished_block
from worldcup.local_runner import attach_refresh_plans, build_snapshot_from_cache, write_snapshot
from worldcup.odds_trend import attach_trends
from worldcup.quota import load_quota_ledger
from worldcup.scheduler import build_match_refresh_decision, build_run_metadata, make_run_id
from worldcup.sources.eloratings import fetch_elo_files
from worldcup.sources.openfootball import fetch_openfootball_2026
from worldcup.sources.theoddsapi import fetch_worldcup_odds
from worldcup.theoddsapi_keys import LEGACY_PROVIDER


def _count_results_applied(cache: Path) -> int:
    _ratings, _aliases, baseline_at = load_baseline(cache)
    cutoff = datetime.fromisoformat(baseline_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    results = parse_openfootball_results(
        json.loads((cache / "openfootball_2026.json").read_text(encoding="utf-8"))
    )
    return sum(1 for result in results if result.kickoff_at_utc.astimezone(timezone.utc) >= cutoff)


@dataclass(frozen=True)
class RefreshResult:
    cache_dir: Path
    snapshot_path: Path
    quota_path: Path
    snapshot: dict
    source_errors: list[dict]
    stale_sources: list[str]
    run_metadata: dict
    archive_path: Path | None = None
    odds_raw_archive_path: Path | None = None


def _load_env(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_kickoff_from_snapshot(snapshot: dict, observed_at: str) -> str | None:
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    upcoming: list[datetime] = []
    for match in snapshot.get("matches", []):
        kickoff_raw = match.get("kickoff_at_utc")
        if not kickoff_raw:
            continue
        kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        if kickoff >= observed:
            upcoming.append(kickoff)
    if not upcoming:
        return None
    return min(upcoming).isoformat()


def refresh_cache_and_build_snapshot(
    api_key: str,
    cache_dir: str | Path = "data/cache",
    snapshot_path: str | Path = "data/cache/analysis_snapshot.json",
    quota_path: str | Path = "data/cache/quota.json",
    openfootball_transport: Callable[[str], object] | None = None,
    theoddsapi_transport: Callable[[str], object] | None = None,
    elo_transport: Callable[[str], object] | None = None,
    history_dir: str | Path = "data/local/history",
    observed_at: str | None = None,
    theoddsapi_provider: str = LEGACY_PROVIDER,
    results_csv: str | Path = "data/local/results/wc2026_results.csv",
    finished_store: str | Path = "data/local/finished_record_store.json",
) -> RefreshResult:
    observed = observed_at or _now_utc_iso()
    cache = Path(cache_dir)
    snapshot_output = Path(snapshot_path)
    quota_output = Path(quota_path)
    source_errors: list[dict] = []
    stale_sources: list[str] = []

    openfootball_cache = cache / "openfootball_2026.json"
    elo_world_cache = cache / "elo_world.tsv"
    elo_teams_cache = cache / "elo_teams.tsv"
    odds_cache = cache / "theoddsapi_wc_odds.json"

    try:
        fetch_openfootball_2026(
            transport=openfootball_transport,
            cache_path=openfootball_cache,
        )
    except Exception as exc:
        if not openfootball_cache.exists():
            raise
        source_errors.append({"source": "openfootball", "error": f"{type(exc).__name__}: {exc}"})
        stale_sources.append("openfootball")

    elo_quality: dict = {"mode": "local_replay"}
    try:
        fetch_elo_files(cache_dir=cache, transport=elo_transport)
        freeze_baseline(cache, baseline_at=observed)
        elo_quality["reanchored"] = True
    except Exception as exc:
        if not (elo_world_cache.exists() and elo_teams_cache.exists()):
            raise
        source_errors.append({"source": "eloratings", "error": f"{type(exc).__name__}: {exc}"})

    if not has_baseline(cache):
        baseline_at = datetime.fromtimestamp(elo_world_cache.stat().st_mtime, tz=timezone.utc).isoformat()
        freeze_baseline(cache, baseline_at=baseline_at)

    try:
        computed = compute_updated_world_tsv(cache)
        elo_world_cache.write_text(computed, encoding="utf-8")
        elo_quality["results_applied"] = _count_results_applied(cache)
    except Exception as exc:
        source_errors.append({"source": "elo_local", "error": f"{type(exc).__name__}: {exc}"})
        elo_quality["mode"] = "cache_passthrough"

    try:
        fetch_worldcup_odds(
            api_key=api_key,
            transport=theoddsapi_transport,
            cache_path=odds_cache,
            quota_path=quota_output,
            observed_at=observed,
            quota_provider=theoddsapi_provider,
        )
    except Exception as exc:
        if not odds_cache.exists():
            raise
        source_errors.append({"source": "theoddsapi", "error": f"{type(exc).__name__}: {exc}"})
        stale_sources.append("theoddsapi")

    snapshot = build_snapshot_from_cache(cache, snapshot_at=observed, stale_sources=stale_sources)
    snapshot.setdefault("data_quality", {})["source_errors"] = source_errors
    snapshot.setdefault("data_quality", {})["stale_sources"] = stale_sources
    snapshot.setdefault("data_quality", {})["elo"] = elo_quality
    quota = load_quota_ledger(quota_output).get("providers", {})
    quota_entry = quota.get(theoddsapi_provider) or quota.get(LEGACY_PROVIDER) or {}
    quota_remaining = quota_entry.get("remaining")
    decision = build_match_refresh_decision(
        now=observed,
        last_refresh_at=observed,
        matches=snapshot.get("matches") or [],
        quota_remaining=quota_remaining,
    )
    attach_refresh_plans(snapshot.get("matches") or [], decision.match_plans)
    run_metadata = build_run_metadata(
        run_id=make_run_id(observed, "live"),
        mode="live",
        observed_at=observed,
        decision=decision,
        quota=quota,
        source_errors=source_errors,
        stale_sources=stale_sources,
    )
    snapshot["run"] = run_metadata
    try:
        attach_trends(snapshot, history_dir, now=observed)
        snapshot["finished"] = build_finished_block(history_dir, results_csv, finished_store)
    except Exception as exc:
        snapshot.setdefault("data_quality", {}).setdefault("enrichment_errors", []).append(
            {"source": "site_enrichment", "error": f"{type(exc).__name__}: {exc}"}
        )
        print(f"warning: site enrichment failed: {exc}", file=sys.stderr)
    write_snapshot(snapshot, snapshot_output)
    archive_path: Path | None = None
    try:
        archive_dir = Path(history_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"snapshot_{run_metadata['run_id']}.json"
        write_snapshot(snapshot, archive_path)
    except OSError as exc:
        print(f"warning: snapshot archive failed: {exc}", file=sys.stderr)
        archive_path = None
    odds_raw_archive_path: Path | None = None
    if "theoddsapi" not in stale_sources and odds_cache.exists():
        try:
            archive_dir = Path(history_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            odds_raw_archive_path = archive_dir / f"odds_raw_{run_metadata['run_id']}.json.gz"
            with gzip.open(odds_raw_archive_path, "wb") as fh:
                fh.write(odds_cache.read_bytes())
        except OSError as exc:
            print(f"warning: raw odds archive failed: {exc}", file=sys.stderr)
            odds_raw_archive_path = None
    return RefreshResult(
        cache_dir=cache,
        snapshot_path=snapshot_output,
        quota_path=quota_output,
        snapshot=snapshot,
        source_errors=source_errors,
        stale_sources=stale_sources,
        run_metadata=run_metadata,
        archive_path=archive_path,
        odds_raw_archive_path=odds_raw_archive_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh local source cache and build analysis snapshot.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--snapshot-out", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--live", action="store_true", help="Actually call external sources and consume quota.")
    args = parser.parse_args(argv)

    if not args.live:
        print("dry-run only: pass --live to call external sources and consume quota")
        return 0

    api_key = _load_env(args.env).get("THE_ODDS_API_KEY")
    if not api_key:
        raise SystemExit("THE_ODDS_API_KEY is missing in .env")

    result = refresh_cache_and_build_snapshot(
        api_key=api_key,
        cache_dir=args.cache_dir,
        snapshot_path=args.snapshot_out,
        quota_path=args.quota_path,
    )
    print(f"wrote {result.snapshot_path} with {result.snapshot['counts']['matches']} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
