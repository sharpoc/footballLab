from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from worldcup.club_rating import ClubResult, load_club_results_csv
from worldcup.collectors.csl_result_sources import (
    parse_cfl_official_fixture_rows,
    parse_cfl_official_result_rows,
    parse_sevenm_fixture_rows,
    parse_sevenm_fixture_result_rows,
)
from worldcup.collectors.csl_results import compare_csl_sources, parse_csl_result_rows
from worldcup.sources.csl_results import (
    CFL_OFFICIAL_2026_URL,
    SEVENM_2026_FIXTURE_URL,
    fetch_cfl_official_results,
    fetch_sevenm_fixture,
)


DEFAULT_REPLAY_PATH = "data/cache/club_results_csl_2026.csv"
DEFAULT_RAW_DIR = "data/cache/csl_results_sources"
REPLAY_FIELDS = (
    "competition_id",
    "season",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
)

OfficialFetch = Callable[[str], dict[str, Any]]
SevenMFetch = Callable[[str], str]


def _existing_results(path: Path, competition_id: str) -> list[ClubResult]:
    if not path.exists():
        return []
    return load_club_results_csv(path, competition_id)


def _match_key(result: ClubResult) -> tuple[str, str, str, str]:
    return (
        result.season,
        result.date,
        result.home_canonical,
        result.away_canonical,
    )


def _write_replay_atomic(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPLAY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _write_raw_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_raw_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
    )


def _fixture_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("kickoff_at_utc") or ""),
        str(row.get("home_canonical") or ""),
        str(row.get("away_canonical") or ""),
    )


def build_verified_fixture_status_payload(
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    *,
    competition_id: str,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    official = {
        _fixture_key(row): row for row in official_rows if row.get("status") != "PLAYED"
    }
    sevenm = {
        _fixture_key(row): row for row in sevenm_rows if row.get("status") != "PLAYED"
    }
    missing_in_sevenm = sorted("|".join(key) for key in official.keys() - sevenm.keys())
    missing_in_official = sorted("|".join(key) for key in sevenm.keys() - official.keys())
    mismatched = sorted(
        "|".join(key)
        for key in official.keys() & sevenm.keys()
        if official[key].get("status") != sevenm[key].get("status")
    )
    verified = bool(official) and not missing_in_sevenm and not missing_in_official and not mismatched
    fixtures = []
    if verified:
        for key in sorted(official):
            primary = official[key]
            check = sevenm[key]
            fixtures.append(
                {
                    key_name: primary[key_name]
                    for key_name in (
                        "season",
                        "round",
                        "kickoff_at_utc",
                        "home_team",
                        "away_team",
                        "home_canonical",
                        "away_canonical",
                        "status",
                    )
                }
                | {
                    "source_match_ids": {
                        "cfl_official": primary.get("source_match_id"),
                        "sevenm": check.get("source_match_id"),
                    }
                }
            )
    payload = {
        "schema_version": 1,
        "competition_id": competition_id,
        "observed_at": observed_at,
        "source": "cfl_official+sevenm",
        "fixtures": fixtures,
        "quality": {
            "verified": verified,
            "official_active_matches": len(official),
            "sevenm_active_matches": len(sevenm),
            "missing_in_sevenm": missing_in_sevenm,
            "missing_in_official": missing_in_official,
            "status_mismatches": mismatched,
        },
    }
    summary = {
        "status": "verified" if verified else "blocked",
        "reason": None if verified else "fixture_status_dual_source_verification_failed",
        "active_matches": len(fixtures),
        "postponed_matches": sum(
            1 for fixture in fixtures if fixture.get("status") == "POSTPONED"
        ),
    }
    return payload, summary


def _existing_replay_row(result: ClubResult) -> dict[str, str]:
    return {
        "competition_id": result.competition_id,
        "season": result.season,
        "date": result.date,
        "home_team": result.home_team,
        "away_team": result.away_team,
        "home_score": str(result.home_score),
        "away_score": str(result.away_score),
        "neutral": "1" if result.neutral else "0",
    }


def run_csl_results_refresh(
    *,
    live: bool = False,
    write: bool = False,
    competition_id: str = "csl_2026",
    season: str = "2026",
    replay_path: str | Path = DEFAULT_REPLAY_PATH,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    fixture_status_path: str | Path | None = None,
    official_url: str = CFL_OFFICIAL_2026_URL,
    sevenm_url: str = SEVENM_2026_FIXTURE_URL,
    official_fetch: OfficialFetch = fetch_cfl_official_results,
    sevenm_fetch: SevenMFetch = fetch_sevenm_fixture,
) -> dict[str, Any]:
    replay = Path(replay_path)
    existing = _existing_results(replay, competition_id)
    if not live:
        return {
            "status": "dry_run",
            "write": write,
            "existing_matches": len(existing),
            "latest_result_date": max((row.date for row in existing), default=None),
        }

    official_payload = official_fetch(official_url)
    sevenm_source = sevenm_fetch(sevenm_url)
    observed_at = datetime.now(timezone.utc).isoformat()
    official_fixtures = parse_cfl_official_fixture_rows(
        official_payload,
        season=season,
        source_url=official_url,
    )
    sevenm_fixtures = parse_sevenm_fixture_rows(
        sevenm_source,
        season=season,
        source_url=sevenm_url,
    )
    fixture_payload, fixture_status = build_verified_fixture_status_payload(
        official_fixtures,
        sevenm_fixtures,
        competition_id=competition_id,
        observed_at=observed_at,
    )
    resolved_fixture_status_path = (
        Path(fixture_status_path)
        if fixture_status_path is not None
        else replay.parent / f"csl_fixture_status_{competition_id}.json"
    )
    if write and fixture_status["status"] == "verified":
        _write_json_atomic(resolved_fixture_status_path, fixture_payload)
        fixture_status["status"] = "updated"
    primary = parse_csl_result_rows(
        parse_sevenm_fixture_result_rows(
            sevenm_source,
            season=season,
            source_url=sevenm_url,
        ),
        competition_id=competition_id,
        source_id="sevenm",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        parse_cfl_official_result_rows(
            official_payload,
            season=season,
            source_url=official_url,
        ),
        competition_id=competition_id,
        source_id="cfl-official",
        source_role="check",
    )
    compared = compare_csl_sources(primary, check, min_valid_matches=1)
    quality = compared.quality
    verified = (
        primary.valid_rows > 0
        and primary.valid_rows == check.valid_rows == len(compared.clean_rows)
        and not primary.issues
        and not check.issues
        and not quality["manual_review_required"]
        and not quality["missing_in_primary"]
        and not quality["degraded_candidates"]
        and quality["dual_source_score_agreement"] == 1.0
        and quality["date_home_away_agreement"] == 1.0
    )
    if not verified:
        return {
            "status": "blocked",
            "reason": "dual_source_verification_failed",
            "write": write,
            "primary_matches": primary.valid_rows,
            "check_matches": check.valid_rows,
            "verified_matches": len(compared.clean_rows),
            "quality": quality,
            "fixture_status": fixture_status,
        }

    new_current = {row.match_key: row for row in compared.clean_rows}
    existing_current = {
        _match_key(row): row for row in existing if row.season == season
    }
    regression = []
    for key, row in existing_current.items():
        candidate = new_current.get(key)
        if candidate is None or (candidate.home_score, candidate.away_score) != (
            row.home_score,
            row.away_score,
        ):
            regression.append("|".join(key))
    if regression:
        return {
            "status": "blocked",
            "reason": "existing_results_regression",
            "write": write,
            "regression_count": len(regression),
            "fixture_status": fixture_status,
        }

    historical = [_existing_replay_row(row) for row in existing if row.season != season]
    current = [row.to_replay_row() for row in compared.clean_rows]
    combined = sorted(
        [*historical, *current],
        key=lambda row: (
            row["season"],
            row["date"],
            row["home_team"],
            row["away_team"],
        ),
    )
    latest = max((row["date"] for row in combined), default=None)
    if write:
        _write_replay_atomic(replay, combined)
        raw_root = Path(raw_dir)
        _write_raw_atomic(
            raw_root / f"cfl_official_{season}.json",
            json.dumps(official_payload, ensure_ascii=False, indent=2, sort_keys=True),
        )
        _write_raw_atomic(raw_root / f"sevenm_{season}_fixture.js", sevenm_source)

    return {
        "status": "updated" if write else "verified",
        "write": write,
        "existing_matches": len(existing),
        "verified_current_season_matches": len(current),
        "total_matches": len(combined),
        "latest_result_date": latest,
        "fixture_status": fixture_status,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh CSL replay results from two public sources. Defaults to dry-run.",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--replay-path", default=DEFAULT_REPLAY_PATH)
    parser.add_argument("--raw-dir", default=DEFAULT_RAW_DIR)
    args = parser.parse_args(argv)
    if args.write and not args.live:
        parser.error("--write requires --live")
    result = run_csl_results_refresh(
        live=args.live,
        write=args.write,
        replay_path=args.replay_path,
        raw_dir=args.raw_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"dry_run", "verified", "updated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
