from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.openfootball import parse_openfootball_fixtures
from worldcup.collectors.theoddsapi import parse_theoddsapi_events
from worldcup.config import load_config
from worldcup.models import Signal
from worldcup.pipeline import analyze_match_input, build_match_inputs, generate_value_signals
from worldcup.scheduler import build_refresh_decision, build_run_metadata, make_run_id


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signal_to_dict(signal: Signal) -> dict[str, Any]:
    return {
        "market_type": signal.market_type.value,
        "selection": signal.selection,
        "grade": signal.grade.value,
        "ev": signal.ev,
        "edge": signal.edge,
        "status": signal.status,
        "reasons": signal.reasons,
        "line": signal.line,
    }


def _latest_quote_update_iso(analysis) -> str | None:
    fetched_times = [
        quote.fetched_at.astimezone(timezone.utc)
        for quote in analysis.match_input.quotes
        if quote.fetched_at is not None
    ]
    if not fetched_times:
        return None
    return max(fetched_times).isoformat()


def _analysis_to_dict(analysis, signals: list[Signal]) -> dict[str, Any]:
    match_input = analysis.match_input
    fixture = match_input.fixture
    return {
        "source_event_id": match_input.odds_event.source_event_id,
        "source_match_no": fixture.source_match_no,
        "kickoff_at_utc": fixture.kickoff_at_utc.isoformat(),
        "stage": fixture.stage,
        "group": fixture.group,
        "venue_name": fixture.venue_name,
        "home_team": fixture.home_team_name,
        "away_team": fixture.away_team_name,
        "home_canonical": fixture.home_canonical,
        "away_canonical": fixture.away_canonical,
        "odds_updated_at": _latest_quote_update_iso(analysis),
        "elo": {
            "home": match_input.home_elo.rating,
            "away": match_input.away_elo.rating,
        },
        "model": {
            "lambdas": {
                "home": analysis.lambdas[0],
                "away": analysis.lambdas[1],
            },
            "poisson_tail": analysis.poisson_tail,
            "elo_1x2": analysis.elo_1x2,
            "poisson_1x2": analysis.poisson_1x2,
            "combined_1x2": analysis.combined_1x2,
            "ou_2_5": analysis.ou_2_5,
        },
        "market": {
            "1x2": analysis.market_1x2,
            "ou_2_5": analysis.market_ou_2_5,
        },
        "signals": [_signal_to_dict(signal) for signal in signals],
    }


def _next_kickoff_from_matches(matches: list[dict[str, Any]], observed_at: str) -> str | None:
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    upcoming: list[datetime] = []
    for match in matches:
        kickoff_raw = match.get("kickoff_at_utc")
        if not kickoff_raw:
            continue
        kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        if kickoff >= observed:
            upcoming.append(kickoff)
    if not upcoming:
        return None
    return min(upcoming).isoformat()


def _local_run_metadata(
    snapshot_at: str,
    matches: list[dict[str, Any]],
    stale_sources: list[str] | None = None,
) -> dict[str, Any]:
    decision = build_refresh_decision(
        now=snapshot_at,
        last_refresh_at=None,
        next_kickoff_at=_next_kickoff_from_matches(matches, snapshot_at),
        quota_remaining=None,
    )
    return build_run_metadata(
        run_id=make_run_id(snapshot_at, "local"),
        mode="local",
        observed_at=snapshot_at,
        decision=decision,
        quota={},
        source_errors=[],
        stale_sources=list(stale_sources or []),
    )


def build_snapshot_from_probe(
    probe_dir: str | Path,
    snapshot_at: str | None = None,
    cfg: dict | None = None,
    stale_sources: list[str] | None = None,
) -> dict[str, Any]:
    probe_path = Path(probe_dir)
    cfg = cfg or load_config()
    observed_at = snapshot_at or _now_utc_iso()
    stale_source_list = list(stale_sources or [])
    fixtures = parse_openfootball_fixtures(_read_json(probe_path / "openfootball_2026.json"))
    odds_events = parse_theoddsapi_events(_read_json(probe_path / "theoddsapi_wc_odds.json"))
    elo_ratings = parse_elo_ratings((probe_path / "elo_world.tsv").read_text(encoding="utf-8"))
    elo_aliases = parse_elo_team_aliases((probe_path / "elo_teams.tsv").read_text(encoding="utf-8"))
    build_result = build_match_inputs(fixtures, odds_events, elo_ratings, elo_aliases)

    matches = []
    for match_input in build_result.inputs:
        analysis = analyze_match_input(match_input, cfg)
        signals = generate_value_signals(
            analysis,
            cfg,
            observed_at=observed_at,
            stale_sources=stale_source_list,
        )
        matches.append(_analysis_to_dict(analysis, signals))

    data_quality = {
        "missing_odds": build_result.missing_odds,
        "missing_elo": build_result.missing_elo,
        "time_mismatches": build_result.time_mismatches,
    }
    if stale_source_list:
        data_quality["stale_sources"] = stale_source_list
    return {
        "snapshot_at": observed_at,
        "run": _local_run_metadata(observed_at, matches, stale_sources=stale_source_list),
        "counts": {
            "fixtures": len(fixtures),
            "odds_events": len(odds_events),
            "match_inputs": len(build_result.inputs),
            "matches": len(matches),
        },
        "data_quality": data_quality,
        "matches": matches,
    }


def build_snapshot_from_cache(
    cache_dir: str | Path,
    snapshot_at: str | None = None,
    cfg: dict | None = None,
    stale_sources: list[str] | None = None,
) -> dict[str, Any]:
    return build_snapshot_from_probe(
        cache_dir,
        snapshot_at=snapshot_at,
        cfg=cfg,
        stale_sources=stale_sources,
    )


def write_snapshot(snapshot: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local analysis snapshot from saved probe samples.")
    parser.add_argument("--probe-dir", default="data/probe")
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--out", default="data/cache/analysis_snapshot.json")
    args = parser.parse_args(argv)
    input_dir = args.input_dir or args.probe_dir
    snapshot = build_snapshot_from_cache(input_dir)
    write_snapshot(snapshot, args.out)
    print(f"wrote {args.out} with {snapshot['counts']['matches']} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
