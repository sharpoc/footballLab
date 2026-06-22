# CSL Club Rating Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local, competition-scoped club rating baseline for CSL 2026 while keeping `club_rating_pending` signal caps in place.

**Architecture:** Introduce a pure offline `worldcup.club_rating` module that reads ignored local CSV results, canonicalizes club names per competition, replays Elo-style ratings, and returns a quality summary. Wire the result into `worldcup.league_runner` as a conservative input: use replayed ratings only when both teams in a fixture are rated, otherwise fall back to the existing 1500 placeholders and write the downgrade reason into `data_quality`.

**Tech Stack:** Python 3.11 standard library, current `worldcup` package, existing `worldcup.elo_replay.update_pair()`, current `tests/run_tests.py`, no new dependency, no network calls, no live odds refresh.

---

## Scope And Safety

This plan implements `docs/superpowers/specs/2026-06-22-csl-club-rating-baseline-design.md`.

Do not fetch real CSL historical results, call The Odds API, deploy, publish, update LaunchAgent, modify `.env`, write ECS, or change `csl_2026.rating_policy` in this plan.

P9.2 builds the framework and test samples only. It does not declare CSL strong signals usable. Even when sample replay succeeds, `club_rating_pending` remains in `data_quality.warnings`, and S/A signals remain capped to B by the existing `cap_signals_for_pending_club_rating()` path.

Before each implementation task, run `git status --short`, touch only the files listed for that task, and do not revert unrelated dirty files.

## File Structure

Create or modify these files:

- Create `worldcup/club_rating.py`: parse local club results CSV, replay competition-scoped club ratings, expose load quality for runners.
- Create `tests/test_club_rating.py`: CSV parsing, alias canonicalization, replay, missing file, invalid rows, and sample-size tests.
- Modify `worldcup/league_runner.py`: load club ratings from local cache, apply pair-level fallback when a fixture lacks a rated team, and add `data_quality.club_rating`.
- Modify `tests/test_league_runner.py`: verify replayed sample ratings enter match Elo, missing teams fall back to placeholders, and strong signal caps remain active.
- Modify `README.md`: document the local CSL club-results CSV contract and safety boundary.
- Modify `RECENT_WORK.md`: record P9.2 implementation outcome after verification.

Do not modify:

- `worldcup/elo_local.py`
- `worldcup/competitions.py`
- `config/settings.yaml`
- scheduler, publish, ingest, ECS, LaunchAgent, or secret handling

## Task 1: Club Rating Core

**Files:**
- Create: `worldcup/club_rating.py`
- Create: `tests/test_club_rating.py`

- [ ] **Step 1: Write failing club rating tests**

Create `tests/test_club_rating.py`:

```python
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.club_rating import (
    load_club_rating_pool,
    load_club_results_csv,
    replay_club_ratings,
)


def _write_results(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "competition_id",
        "season",
        "date",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "neutral",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(
    home: str,
    away: str,
    home_score: str,
    away_score: str,
    *,
    date: str = "2026-03-01",
    competition_id: str = "csl_2026",
    neutral: str = "0",
) -> dict[str, str]:
    return {
        "competition_id": competition_id,
        "season": "2026",
        "date": date,
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "neutral": neutral,
    }


def test_load_club_results_csv_filters_competition_and_canonicalizes_clubs():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0"),
                _row(
                    "Arsenal",
                    "Chelsea",
                    "1",
                    "1",
                    competition_id="epl_2026_27",
                ),
                _row("Shanghai Shenhua", "Beijing Guoan", "x", "1"),
            ],
        )

        results = load_club_results_csv(path, "csl_2026")

        assert len(results) == 1
        result = results[0]
        assert result.competition_id == "csl_2026"
        assert result.home_team == "Shanghai Port"
        assert result.away_team == "Shandong Taishan"
        assert result.home_canonical == "shanghai_port"
        assert result.away_canonical == "shandong_taishan"
        assert result.home_score == 2
        assert result.away_score == 0
        assert result.neutral is False


def test_replay_club_ratings_moves_winner_up_and_returns_pipeline_elo():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shandong Taishan", "Beijing Guoan", "1", "1", date="2026-03-08"),
            ],
        )

        pool = replay_club_ratings(load_club_results_csv(path, "csl_2026"), "csl_2026")

        shanghai = pool.rating_for("shanghai_port")
        shandong = pool.rating_for("shandong_taishan")
        assert shanghai is not None
        assert shandong is not None
        assert shanghai.rating > 1500
        assert shandong.rating < 1500
        assert pool.matches_replayed == 2
        assert pool.to_elo_rating("shanghai_port").code == "shanghai_port"
        assert pool.to_elo_rating("shanghai_port").rating == shanghai.rating


def test_load_club_rating_pool_reports_missing_file_without_exception():
    with TemporaryDirectory() as tmp:
        result = load_club_rating_pool(Path(tmp), "csl_2026")

        assert result.pool is None
        assert result.quality.mode == "missing"
        assert result.quality.source.endswith("club_results_csl_2026.csv")
        assert result.quality.competition_id == "csl_2026"
        assert result.quality.matches_replayed == 0
        assert result.quality.teams_rated == 0
        assert result.quality.sample_too_small is True
        assert result.quality.to_dict()["missing_teams"] == []


def test_load_club_rating_pool_reports_sample_too_small_and_invalid_rows():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        path = cache_dir / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shanghai Shenhua", "Beijing Guoan", "bad", "1", date="2026-03-02"),
            ],
        )

        result = load_club_rating_pool(cache_dir, "csl_2026", min_matches=2)

        assert result.pool is None
        assert result.quality.mode == "sample_too_small"
        assert result.quality.matches_replayed == 1
        assert result.quality.teams_rated == 2
        assert result.quality.skipped_rows == 1
        assert result.quality.sample_too_small is True


def test_load_club_rating_pool_replays_when_sample_threshold_is_met():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        path = cache_dir / "club_results_csl_2026.csv"
        _write_results(
            path,
            [
                _row("Shanghai Port", "Shandong Taishan", "2", "0", date="2026-03-01"),
                _row("Shanghai Port", "Beijing Guoan", "1", "0", date="2026-03-08"),
                _row("Shanghai Shenhua", "Beijing Guoan", "0", "0", date="2026-03-15"),
            ],
        )

        result = load_club_rating_pool(cache_dir, "csl_2026", min_matches=3)

        assert result.pool is not None
        assert result.quality.mode == "sample_replay"
        assert result.quality.matches_replayed == 3
        assert result.quality.teams_rated == 4
        assert result.quality.skipped_rows == 0
        assert result.quality.sample_too_small is False
        assert result.quality.to_dict()["source"].endswith("club_results_csl_2026.csv")
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.club_rating'`.

- [ ] **Step 3: Implement `worldcup/club_rating.py`**

Create `worldcup/club_rating.py`:

```python
from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from worldcup.collectors.club_aliases import canonicalize_club
from worldcup.collectors.models import EloRating
from worldcup.elo_replay import DEFAULT_HOME_ADV, DEFAULT_INITIAL_RATING, update_pair

DEFAULT_CLUB_K = 30.0
DEFAULT_MIN_MATCHES = 20


@dataclass(frozen=True)
class ClubResult:
    competition_id: str
    season: str
    date: str
    home_team: str
    away_team: str
    home_canonical: str
    away_canonical: str
    home_score: int
    away_score: int
    neutral: bool


@dataclass(frozen=True)
class ClubRating:
    code: str
    rating: int
    matches: int


@dataclass(frozen=True)
class ClubRatingPool:
    competition_id: str
    ratings: dict[str, ClubRating]
    matches_replayed: int

    def rating_for(self, team_key: str | None) -> ClubRating | None:
        if team_key is None:
            return None
        return self.ratings.get(team_key)

    def to_elo_rating(self, team_key: str | None) -> EloRating | None:
        rating = self.rating_for(team_key)
        if rating is None:
            return None
        return EloRating(code=rating.code, rank=0, rating=rating.rating)


@dataclass(frozen=True)
class ClubRatingQuality:
    mode: str
    source: str
    competition_id: str
    matches_replayed: int = 0
    teams_rated: int = 0
    missing_teams: tuple[str, ...] = ()
    skipped_rows: int = 0
    sample_too_small: bool = True
    errors: tuple[str, ...] = ()

    def with_missing_teams(self, missing_teams: set[str]) -> ClubRatingQuality:
        merged = tuple(sorted(set(self.missing_teams).union(missing_teams)))
        return replace(self, missing_teams=merged)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "source": self.source,
            "competition_id": self.competition_id,
            "matches_replayed": self.matches_replayed,
            "teams_rated": self.teams_rated,
            "missing_teams": list(self.missing_teams),
            "skipped_rows": self.skipped_rows,
            "sample_too_small": self.sample_too_small,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ClubRatingLoadResult:
    pool: ClubRatingPool | None
    quality: ClubRatingQuality


def club_results_cache_path(cache_dir: str | Path, competition_id: str) -> Path:
    return Path(cache_dir) / f"club_results_{competition_id}.csv"


def _clean(value: object) -> str:
    return str(value or "").strip()


def _parse_score(value: object) -> int | None:
    raw = _clean(value)
    if not raw.isdigit():
        return None
    score = int(raw)
    return score if score >= 0 else None


def _parse_neutral(value: object) -> bool | None:
    raw = _clean(value).lower()
    if raw in {"1", "true", "yes", "y"}:
        return True
    if raw in {"", "0", "false", "no", "n"}:
        return False
    return None


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _load_club_results_csv_with_skips(
    path: str | Path,
    competition_id: str,
) -> tuple[list[ClubResult], int]:
    results: list[ClubResult] = []
    skipped_rows = 0
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            row_competition = _clean(row.get("competition_id"))
            if row_competition != competition_id:
                continue

            match_date = _clean(row.get("date"))
            home_team = _clean(row.get("home_team"))
            away_team = _clean(row.get("away_team"))
            home_score = _parse_score(row.get("home_score"))
            away_score = _parse_score(row.get("away_score"))
            neutral = _parse_neutral(row.get("neutral"))

            if (
                not match_date
                or not _valid_date(match_date)
                or not home_team
                or not away_team
                or home_score is None
                or away_score is None
                or neutral is None
            ):
                skipped_rows += 1
                continue

            results.append(
                ClubResult(
                    competition_id=row_competition,
                    season=_clean(row.get("season")),
                    date=match_date,
                    home_team=home_team,
                    away_team=away_team,
                    home_canonical=canonicalize_club(competition_id, home_team),
                    away_canonical=canonicalize_club(competition_id, away_team),
                    home_score=home_score,
                    away_score=away_score,
                    neutral=neutral,
                )
            )
    return results, skipped_rows


def load_club_results_csv(path: str | Path, competition_id: str) -> list[ClubResult]:
    results, _ = _load_club_results_csv_with_skips(path, competition_id)
    return results


def replay_club_ratings(
    results: list[ClubResult],
    competition_id: str,
    initial: float = DEFAULT_INITIAL_RATING,
    k: float = DEFAULT_CLUB_K,
    home_adv: float = DEFAULT_HOME_ADV,
) -> ClubRatingPool:
    ratings: dict[str, float] = {}
    match_counts: dict[str, int] = {}

    for result in sorted(results, key=lambda item: item.date):
        home_key = result.home_canonical
        away_key = result.away_canonical
        home_rating = ratings.get(home_key, initial)
        away_rating = ratings.get(away_key, initial)
        new_home, new_away = update_pair(
            home_rating,
            away_rating,
            result.home_score,
            result.away_score,
            k=k,
            neutral=result.neutral,
            home_adv=home_adv,
        )
        ratings[home_key] = new_home
        ratings[away_key] = new_away
        match_counts[home_key] = match_counts.get(home_key, 0) + 1
        match_counts[away_key] = match_counts.get(away_key, 0) + 1

    return ClubRatingPool(
        competition_id=competition_id,
        ratings={
            code: ClubRating(code=code, rating=int(round(value)), matches=match_counts[code])
            for code, value in ratings.items()
        },
        matches_replayed=len(results),
    )


def _quality(
    *,
    mode: str,
    path: Path,
    competition_id: str,
    matches_replayed: int = 0,
    teams_rated: int = 0,
    skipped_rows: int = 0,
    sample_too_small: bool = True,
    errors: tuple[str, ...] = (),
) -> ClubRatingQuality:
    return ClubRatingQuality(
        mode=mode,
        source=str(path),
        competition_id=competition_id,
        matches_replayed=matches_replayed,
        teams_rated=teams_rated,
        skipped_rows=skipped_rows,
        sample_too_small=sample_too_small,
        errors=errors,
    )


def load_club_rating_pool(
    cache_dir: str | Path,
    competition_id: str,
    min_matches: int = DEFAULT_MIN_MATCHES,
    initial: float = DEFAULT_INITIAL_RATING,
    k: float = DEFAULT_CLUB_K,
    home_adv: float = DEFAULT_HOME_ADV,
) -> ClubRatingLoadResult:
    path = club_results_cache_path(cache_dir, competition_id)
    if not path.exists():
        return ClubRatingLoadResult(
            pool=None,
            quality=_quality(mode="missing", path=path, competition_id=competition_id),
        )

    try:
        results, skipped_rows = _load_club_results_csv_with_skips(path, competition_id)
        preview_pool = replay_club_ratings(
            results,
            competition_id,
            initial=initial,
            k=k,
            home_adv=home_adv,
        )
    except Exception as exc:
        return ClubRatingLoadResult(
            pool=None,
            quality=_quality(
                mode="invalid",
                path=path,
                competition_id=competition_id,
                errors=(f"{exc.__class__.__name__}: {exc}",),
            ),
        )

    if len(results) < min_matches:
        return ClubRatingLoadResult(
            pool=None,
            quality=_quality(
                mode="sample_too_small",
                path=path,
                competition_id=competition_id,
                matches_replayed=len(results),
                teams_rated=len(preview_pool.ratings),
                skipped_rows=skipped_rows,
                sample_too_small=True,
            ),
        )

    return ClubRatingLoadResult(
        pool=preview_pool,
        quality=_quality(
            mode="sample_replay",
            path=path,
            competition_id=competition_id,
            matches_replayed=preview_pool.matches_replayed,
            teams_rated=len(preview_pool.ratings),
            skipped_rows=skipped_rows,
            sample_too_small=False,
        ),
    )
```

- [ ] **Step 4: Run club rating tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for all `tests/test_club_rating.py` tests. Existing `tests/test_league_runner.py` still uses placeholder ratings because runner integration is not done yet.

- [ ] **Step 5: Commit club rating core**

Run:

```bash
git add worldcup/club_rating.py tests/test_club_rating.py
git commit -m "feat: add local club rating replay"
```

Expected: commit succeeds. Do not push.

## Task 2: League Runner Club Rating Integration

**Files:**
- Modify: `worldcup/league_runner.py`
- Modify: `tests/test_league_runner.py`

- [ ] **Step 1: Write failing league runner integration tests**

Modify imports at the top of `tests/test_league_runner.py`:

```python
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
```

Add this helper below `_write_csl_odds_cache()`:

```python
def _write_club_results_cache(cache_dir: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "competition_id",
        "season",
        "date",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "neutral",
    ]
    with (cache_dir / "club_results_csl_2026.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
```

Add these tests to `tests/test_league_runner.py`:

```python
def test_build_league_snapshot_reports_missing_club_rating_quality():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "missing"
        assert club_quality["source"].endswith("club_results_csl_2026.csv")
        assert club_quality["competition_id"] == "csl_2026"
        assert club_quality["matches_replayed"] == 0
        assert club_quality["teams_rated"] == 0
        assert club_quality["sample_too_small"] is True
        assert club_quality["missing_teams"] == []


def test_build_league_snapshot_uses_sample_club_ratings_but_keeps_signal_cap():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-01",
                    "home_team": "Shanghai Port",
                    "away_team": "Shandong Taishan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                },
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-08",
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                    "home_score": "1",
                    "away_score": "0",
                    "neutral": "0",
                },
            ],
        )

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
            club_rating_min_matches=2,
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "sample_replay"
        assert club_quality["matches_replayed"] == 2
        assert club_quality["teams_rated"] == 3
        assert club_quality["missing_teams"] == []
        assert "club_rating_pending" in snapshot["data_quality"]["warnings"]

        match = snapshot["matches"][0]
        assert match["elo"]["home"] > 1500
        assert match["elo"]["away"] < 1500
        assert match["signals"]
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])
        assert any("club_rating_pending" in signal["reasons"] for signal in match["signals"])


def test_build_league_snapshot_falls_back_when_fixture_team_missing_rating():
    with TemporaryDirectory() as tmp:
        cache_dir = Path(tmp) / "cache"
        _write_csl_odds_cache(cache_dir)
        _write_club_results_cache(
            cache_dir,
            [
                {
                    "competition_id": "csl_2026",
                    "season": "2026",
                    "date": "2026-03-01",
                    "home_team": "Shanghai Port",
                    "away_team": "Beijing Guoan",
                    "home_score": "2",
                    "away_score": "0",
                    "neutral": "0",
                }
            ],
        )

        snapshot = build_league_snapshot_from_cache(
            cache_dir,
            snapshot_at="2026-06-22T09:00:00+00:00",
            club_rating_min_matches=1,
        )

        club_quality = snapshot["data_quality"]["club_rating"]
        assert club_quality["mode"] == "sample_replay"
        assert club_quality["missing_teams"] == ["shandong_taishan"]
        assert "club_rating_missing" in snapshot["data_quality"]["warnings"]

        match = snapshot["matches"][0]
        assert match["elo"] == {"home": 1500, "away": 1500}
        assert all(signal["grade"] not in ("S", "A") for signal in match["signals"])
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL because `build_league_snapshot_from_cache()` does not accept `club_rating_min_matches` and `data_quality.club_rating` is absent.

- [ ] **Step 3: Modify imports and rating helpers in `worldcup/league_runner.py`**

Update the imports in `worldcup/league_runner.py`:

```python
from worldcup.club_rating import ClubRatingPool, load_club_rating_pool
from worldcup.collectors.league_odds import parse_league_odds_events
from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
```

Replace `_match_input_from_fixture_event()` with these helpers:

```python
def _ratings_for_fixture(
    fixture: Fixture,
    rating_pool: ClubRatingPool | None,
) -> tuple[EloRating, EloRating, list[str]]:
    if rating_pool is None:
        return (
            _placeholder_rating(fixture.home_canonical),
            _placeholder_rating(fixture.away_canonical),
            [],
        )

    home_rating = rating_pool.to_elo_rating(fixture.home_canonical)
    away_rating = rating_pool.to_elo_rating(fixture.away_canonical)
    missing = []
    if home_rating is None and fixture.home_canonical is not None:
        missing.append(fixture.home_canonical)
    if away_rating is None and fixture.away_canonical is not None:
        missing.append(fixture.away_canonical)
    if missing:
        return (
            _placeholder_rating(fixture.home_canonical),
            _placeholder_rating(fixture.away_canonical),
            missing,
        )
    return home_rating, away_rating, []


def _match_input_from_fixture_event(
    fixture: Fixture,
    odds_event: ParsedOddsEvent,
    rating_pool: ClubRatingPool | None = None,
) -> tuple[MatchAnalysisInput, list[str]]:
    home_elo, away_elo, missing = _ratings_for_fixture(fixture, rating_pool)
    return (
        MatchAnalysisInput(
            fixture=fixture,
            odds_event=odds_event,
            home_elo=home_elo,
            away_elo=away_elo,
            quotes=odds_event.quotes,
            neutral=False,
        ),
        missing,
    )
```

This is intentionally pair-level conservative: if one side is missing from the sample replay, both teams in that fixture use 1500 placeholders. That avoids manufacturing a one-sided advantage from partial local samples.

- [ ] **Step 4: Wire club rating quality into `build_league_snapshot_from_cache()`**

Replace the function signature and body of `build_league_snapshot_from_cache()` with:

```python
def build_league_snapshot_from_cache(
    cache_dir: str | Path,
    competition_id: str = "csl_2026",
    snapshot_at: str | None = None,
    cfg: dict | None = None,
    club_rating_min_matches: int = 20,
) -> dict:
    competition = get_competition(competition_id)
    cfg = cfg or load_config()
    observed_at = snapshot_at or _now_utc_iso()
    cache_path = Path(cache_dir) / _odds_cache_name(competition_id)
    parse_result = parse_league_odds_events(_read_json(cache_path), competition_id)

    club_rating_result = load_club_rating_pool(
        cache_dir,
        competition_id,
        min_matches=club_rating_min_matches,
        home_adv=float(cfg["elo"]["home_adv"]),
    )
    rating_pool = club_rating_result.pool

    matches = []
    missing_rating_teams: set[str] = set()
    club_rating_pending = competition.rating_policy == "club_rating_pending"
    for fixture, odds_event in zip(parse_result.fixtures, parse_result.odds_events):
        match_input, missing_for_fixture = _match_input_from_fixture_event(
            fixture,
            odds_event,
            rating_pool,
        )
        missing_rating_teams.update(missing_for_fixture)
        analysis = analyze_match_input(match_input, cfg)
        signals = generate_value_signals(analysis, cfg, observed_at=observed_at)
        if club_rating_pending:
            signals = cap_signals_for_pending_club_rating(signals)
        matches.append(
            _analysis_to_dict(
                analysis,
                signals,
                competition_id=competition_id,
            )
        )

    warnings: list[str] = []
    if parse_result.fixture_source == "odds_event_only":
        warnings.append("odds_event_only")
    if club_rating_pending:
        warnings.append("club_rating_pending")
    club_quality = club_rating_result.quality.with_missing_teams(missing_rating_teams)
    if club_quality.mode == "missing":
        warnings.append("club_rating_missing")
    if club_quality.mode == "sample_too_small":
        warnings.append("club_rating_sample_too_small")
    if club_quality.mode == "invalid":
        warnings.append("club_rating_invalid")
    if missing_rating_teams:
        warnings.append("club_rating_missing")
    warnings = sorted(set(warnings))

    return {
        "snapshot_at": observed_at,
        "competition": competition.snapshot_block(),
        "counts": {
            "fixtures": len(parse_result.fixtures),
            "odds_events": len(parse_result.odds_events),
            "match_inputs": len(matches),
            "matches": len(matches),
        },
        "data_quality": {
            "fixture_source": parse_result.fixture_source,
            "warnings": warnings,
            "club_alias_unmatched": parse_result.unmatched_clubs,
            "club_rating": club_quality.to_dict(),
            **_invalid_odds_quality(parse_result.odds_events, cache_path),
        },
        "matches": matches,
    }
```

- [ ] **Step 5: Add a CLI min-match argument**

In `main()` inside `worldcup/league_runner.py`, add:

```python
    parser.add_argument("--club-rating-min-matches", type=int, default=20)
```

Then pass it into `build_league_snapshot_from_cache()`:

```python
    snapshot = build_league_snapshot_from_cache(
        args.cache_dir,
        competition_id=args.competition_id,
        snapshot_at=args.snapshot_at,
        club_rating_min_matches=args.club_rating_min_matches,
    )
```

- [ ] **Step 6: Run league runner tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for `tests/test_club_rating.py` and `tests/test_league_runner.py`. Existing league snapshot tests now include `data_quality.club_rating` while preserving current `club_rating_pending` signal caps.

- [ ] **Step 7: Commit league runner integration**

Run:

```bash
git add worldcup/league_runner.py tests/test_league_runner.py
git commit -m "feat: wire club ratings into league runner"
```

Expected: commit succeeds. Do not push.

## Task 3: Documentation And Recent Work

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README with the local CSV contract**

Add this section near the current CSL local snapshot documentation in `README.md`:

````markdown
### 中超 Club Rating 本地基线

P9.2 新增本地 `club_rating` 基线能力，但仍保持 `csl_2026.rating_policy=club_rating_pending`。这表示样例或本地历史赛果可以进入模型输入，强信号仍会被降级，不把中超输出包装成高置信结论。

本地历史赛果 CSV 默认路径：

```bash
data/cache/club_results_csl_2026.csv
```

字段契约：

```text
competition_id,season,date,home_team,away_team,home_score,away_score,neutral
```

规则：

- `competition_id` 必须等于 `csl_2026`。
- `date` 使用 `YYYY-MM-DD`。
- `home_team` / `away_team` 通过俱乐部 alias 映射到 competition-scoped canonical key。
- `home_score` / `away_score` 必须是非负整数；无效行会跳过并进入 `data_quality.club_rating.skipped_rows`。
- `neutral` 支持 `0/1`、`true/false`、`yes/no`；中超常规主客场默认 `0`。

`league_runner` 只读取本地 cache，不联网：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

缺少 CSV、样本不足、CSV 无效或 fixture 球队缺少 rating 时，snapshot 会在 `data_quality.club_rating` 和 `data_quality.warnings` 标记原因，并回退到 1500 占位。真实中超历史数据来源、清洗规则、回测和解除强信号压制需后续单独确认。
````

- [ ] **Step 2: Update RECENT_WORK**

Add this entry near the top of `RECENT_WORK.md`, below the introductory paragraph:

```markdown
## 2026-06-22 P9.2 中超 Club Rating 本地基线

- 新增 `worldcup.club_rating`：只读本地 `data/cache/club_results_<competition_id>.csv`，按 competition 独立解析、canonicalize 俱乐部名称并重放 Elo-style rating。
- `worldcup.league_runner` 现在会尝试加载 club rating；缺文件、样本不足、CSV 无效或 fixture 球队缺 rating 时回退到 1500 占位，并在 `data_quality.club_rating` / `data_quality.warnings` 标记。
- P9.2 不接真实中超历史数据源、不联网、不消耗 The Odds API quota、不部署、不更新 LaunchAgent、不改 `csl_2026.rating_policy`，强信号仍按 `club_rating_pending` 降级。
- 验证：`PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 全量通过；`git diff --check` 通过。
```

- [ ] **Step 3: Run docs diff check**

Run:

```bash
git diff --check -- README.md RECENT_WORK.md
```

Expected: no trailing whitespace or conflict marker output.

- [ ] **Step 4: Commit documentation**

Run:

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: document csl club rating baseline"
```

Expected: commit succeeds. Do not push.

## Task 4: Full Verification

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run full standard tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 2: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Run local league runner smoke against an empty temp cache**

Run:

```bash
tmpdir="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir "$tmpdir" --out "$tmpdir/league_analysis_snapshot.json"
```

Expected: fails with a local missing-file error for `theoddsapi_csl_2026_odds.json`. This is the safe result because the runner must not fetch odds from the network to fill missing cache.

- [ ] **Step 4: Run local league runner smoke with test-shaped local cache**

Run:

```bash
tmpdir="$(mktemp -d)"
python3 - <<'PY' "$tmpdir"
import csv
import json
import sys
from pathlib import Path

cache = Path(sys.argv[1])
cache.mkdir(parents=True, exist_ok=True)
(cache / "theoddsapi_csl_2026_odds.json").write_text(json.dumps([
    {
        "id": "csl-smoke-1",
        "sport_key": "soccer_china_superleague",
        "commence_time": "2026-06-24T11:35:00Z",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "bookmakers": [
            {
                "key": "bk1",
                "last_update": "2026-06-22T08:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-22T08:01:00Z",
                        "outcomes": [
                            {"name": "Shanghai Port", "price": 2.35},
                            {"name": "Shandong Taishan", "price": 3.2},
                            {"name": "Draw", "price": 3.4}
                        ]
                    }
                ]
            },
            {
                "key": "bk2",
                "last_update": "2026-06-22T08:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-22T08:01:00Z",
                        "outcomes": [
                            {"name": "Shanghai Port", "price": 2.40},
                            {"name": "Shandong Taishan", "price": 3.1},
                            {"name": "Draw", "price": 3.5}
                        ]
                    }
                ]
            },
            {
                "key": "bk3",
                "last_update": "2026-06-22T08:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": "2026-06-22T08:01:00Z",
                        "outcomes": [
                            {"name": "Shanghai Port", "price": 2.45},
                            {"name": "Shandong Taishan", "price": 3.0},
                            {"name": "Draw", "price": 3.6}
                        ]
                    }
                ]
            }
        ]
    }
]), encoding="utf-8")

with (cache / "club_results_csl_2026.csv").open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=[
        "competition_id", "season", "date", "home_team", "away_team",
        "home_score", "away_score", "neutral",
    ])
    writer.writeheader()
    writer.writerow({
        "competition_id": "csl_2026",
        "season": "2026",
        "date": "2026-03-01",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "home_score": "2",
        "away_score": "0",
        "neutral": "0",
    })
PY
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.league_runner --competition csl_2026 --cache-dir "$tmpdir" --club-rating-min-matches 1 --out "$tmpdir/league_analysis_snapshot.json"
python3 - <<'PY' "$tmpdir/league_analysis_snapshot.json"
import json
import sys
snapshot = json.load(open(sys.argv[1], encoding="utf-8"))
print(snapshot["data_quality"]["club_rating"]["mode"])
print(snapshot["matches"][0]["elo"])
print(snapshot["data_quality"]["warnings"])
PY
```

Expected:

```text
sample_replay
{'home': <integer greater than 1500>, 'away': <integer less than 1500>}
```

The printed warnings must include `club_rating_pending`. If the shell prints JSON-style double quotes instead of Python single quotes for the dict, the rating direction is the important assertion.

- [ ] **Step 5: Inspect final git status**

Run:

```bash
git status --short
```

Expected: only intended P9.2 files are modified or untracked unless the repository already had unrelated dirty files before this plan started.

## Self-Review

Spec coverage:

- Generic `club_rating` local module: Task 1.
- CSL CSV contract and sample tests: Task 1 and Task 3.
- Competition-scoped replay using local results: Task 1.
- `league_runner` reads club rating and falls back safely: Task 2.
- `data_quality.club_rating` includes source, mode, sample count, missing teams, skipped rows, and sample-size state: Task 2.
- `club_rating_pending` remains active and S/A caps stay in place: Task 2.
- No real data source, no network, no live odds, no deploy: Scope And Safety plus Task 4.

Placeholder scan:

- This plan contains concrete file paths, code snippets, commands, and expected outcomes.
- It does not require new dependencies or hidden services.

Type consistency:

- `ClubRatingPool.to_elo_rating()` returns the existing `worldcup.collectors.models.EloRating` used by `MatchAnalysisInput`.
- `build_league_snapshot_from_cache(..., club_rating_min_matches=20)` keeps the existing call pattern valid because the new parameter has a default.
- `data_quality.club_rating` is a plain dict produced by `ClubRatingQuality.to_dict()`, matching existing snapshot JSON style.

Adversarial review:

- Root cause: the plan adds a rating foundation but does not solve real CSL historical data sourcing. Real data ingestion and backtesting remain a separate confirmation point.
- Data risk: sample CSV proves interface behavior only. The plan keeps `club_rating_pending` active to avoid overstating signal quality.
- Scope risk: no API calls, no quota usage, no deployment, no scheduler change, no LaunchAgent change.
- Model risk: `home_adv=100` is reused as an engineering default, not a CSL-calibrated parameter.
- Migration risk: the CSV path and replay API are competition-scoped so EPL and big-five leagues can reuse the same module without hard-coding CSL semantics.

Plan complete and saved to `docs/superpowers/plans/2026-06-22-csl-club-rating-baseline.md`. Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
