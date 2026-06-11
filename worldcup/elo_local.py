"""Locally replayed Elo from a frozen official baseline.

The live pipeline can use this module offline: freeze a trusted eloratings
cache, then replay finished openfootball results from that baseline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from worldcup.collectors.eloratings import parse_elo_ratings, parse_elo_team_aliases
from worldcup.collectors.models import EloRating
from worldcup.collectors.openfootball import parse_openfootball_results
from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.elo_replay import update_pair

BASELINE_WORLD = "elo_baseline_world.tsv"
BASELINE_TEAMS = "elo_baseline_teams.tsv"
BASELINE_META = "elo_baseline_meta.json"
WORLD_CUP_K = 60.0


def freeze_baseline(cache_dir: str | Path, baseline_at: str) -> dict:
    cache = Path(cache_dir)
    world_text = (cache / "elo_world.tsv").read_text(encoding="utf-8")
    teams_text = (cache / "elo_teams.tsv").read_text(encoding="utf-8")
    if not parse_elo_ratings(world_text):
        raise ValueError("refusing to freeze baseline: elo_world.tsv parsed 0 rows")
    if not parse_elo_team_aliases(teams_text):
        raise ValueError("refusing to freeze baseline: elo_teams.tsv parsed 0 aliases")
    (cache / BASELINE_WORLD).write_text(world_text, encoding="utf-8")
    (cache / BASELINE_TEAMS).write_text(teams_text, encoding="utf-8")
    meta = {"baseline_at": baseline_at}
    (cache / BASELINE_META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def has_baseline(cache_dir: str | Path) -> bool:
    cache = Path(cache_dir)
    return all((cache / name).exists() for name in (BASELINE_WORLD, BASELINE_TEAMS, BASELINE_META))


def load_baseline(cache_dir: str | Path) -> tuple[dict[str, EloRating], dict[str, str], str]:
    cache = Path(cache_dir)
    if not has_baseline(cache):
        raise FileNotFoundError(f"elo baseline missing in {cache}")
    ratings = parse_elo_ratings((cache / BASELINE_WORLD).read_text(encoding="utf-8"))
    aliases = parse_elo_team_aliases((cache / BASELINE_TEAMS).read_text(encoding="utf-8"))
    meta = json.loads((cache / BASELINE_META).read_text(encoding="utf-8"))
    return ratings, aliases, str(meta["baseline_at"])


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def compute_updated_world_tsv(cache_dir: str | Path, min_rows: int | None = None) -> str:
    cache = Path(cache_dir)
    baseline, aliases, baseline_at = load_baseline(cache)
    if min_rows is None:
        min_rows = len(baseline)
    cutoff = _parse_at(baseline_at)
    code_by_canonical = {canonicalize_team(name): code for name, code in aliases.items()}

    current: dict[str, float] = {code: float(rating.rating) for code, rating in baseline.items()}
    results = parse_openfootball_results(
        json.loads((cache / "openfootball_2026.json").read_text(encoding="utf-8"))
    )
    for result in sorted(results, key=lambda row: row.kickoff_at_utc):
        if result.kickoff_at_utc.astimezone(timezone.utc) < cutoff:
            continue
        home_key = result.home_canonical or canonicalize_team(result.home_team_name)
        away_key = result.away_canonical or canonicalize_team(result.away_team_name)
        home_code = code_by_canonical.get(home_key)
        away_code = code_by_canonical.get(away_key)
        if home_code not in current or away_code not in current:
            continue
        new_home, new_away = update_pair(
            current[home_code],
            current[away_code],
            result.home_score,
            result.away_score,
            k=WORLD_CUP_K,
            neutral=True,
        )
        current[home_code] = new_home
        current[away_code] = new_away

    ordered = sorted(current.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        f"{rank}\t{rank}\t{code}\t{round(rating)}"
        for rank, (code, rating) in enumerate(ordered, start=1)
    ]
    out = "\n".join(lines) + "\n"
    parsed_rows = len(parse_elo_ratings(out))
    if parsed_rows < min_rows:
        raise ValueError(f"computed elo has {parsed_rows} rows, expected >= {min_rows}")
    return out


def _default_baseline_at(cache: Path) -> str:
    mtime = (cache / "elo_world.tsv").stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Freeze or inspect the local Elo baseline.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--freeze", action="store_true", help="Freeze current cache as baseline.")
    parser.add_argument("--baseline-at", default=None, help="ISO time; default: elo_world.tsv mtime.")
    parser.add_argument("--check", action="store_true", help="Report baseline and computed rows.")
    args = parser.parse_args(argv)

    cache = Path(args.cache_dir)
    if args.freeze:
        baseline_at = args.baseline_at or _default_baseline_at(cache)
        meta = freeze_baseline(cache, baseline_at=baseline_at)
        print(json.dumps({"frozen": True, **meta}, ensure_ascii=False))
        return 0
    if args.check:
        ratings, _aliases, baseline_at = load_baseline(cache)
        out = compute_updated_world_tsv(cache)
        print(
            json.dumps(
                {
                    "baseline_at": baseline_at,
                    "baseline_teams": len(ratings),
                    "computed_rows": len(parse_elo_ratings(out)),
                },
                ensure_ascii=False,
            )
        )
        return 0
    parser.error("pass --freeze or --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
