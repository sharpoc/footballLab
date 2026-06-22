from __future__ import annotations

import csv
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ClubRatingPool:
    competition_id: str
    ratings: dict[str, ClubRating]
    matches_replayed: int

    def rating_for(self, code: str) -> ClubRating | None:
        return self.ratings.get(code)

    def to_elo_rating(self, code: str) -> EloRating:
        rating = self.ratings.get(code)
        value = DEFAULT_INITIAL_RATING if rating is None else rating.rating
        return EloRating(code=code, rank=0, rating=int(round(value)))


@dataclass(frozen=True)
class ClubRatingQuality:
    mode: str
    source: str
    competition_id: str
    matches_replayed: int = 0
    teams_rated: int = 0
    missing_teams: tuple[str, ...] = field(default_factory=tuple)
    skipped_rows: int = 0
    sample_too_small: bool = True
    errors: tuple[str, ...] = field(default_factory=tuple)

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

    def with_missing_teams(self, teams: set[str]) -> ClubRatingQuality:
        merged = sorted(set(self.missing_teams) | set(teams))
        return ClubRatingQuality(
            mode=self.mode,
            source=self.source,
            competition_id=self.competition_id,
            matches_replayed=self.matches_replayed,
            teams_rated=self.teams_rated,
            missing_teams=tuple(merged),
            skipped_rows=self.skipped_rows,
            sample_too_small=self.sample_too_small,
            errors=self.errors,
        )


@dataclass(frozen=True)
class ClubRatingLoadResult:
    pool: ClubRatingPool | None
    quality: ClubRatingQuality


def club_results_cache_path(cache_dir: str | Path, competition_id: str) -> Path:
    return Path(cache_dir) / f"club_results_{competition_id}.csv"


def load_club_results_csv(path: str | Path, competition_id: str) -> list[ClubResult]:
    results, _skipped = _load_club_results_csv_with_skipped(path, competition_id)
    return results


def replay_club_ratings(
    results: list[ClubResult],
    competition_id: str,
    initial: float = DEFAULT_INITIAL_RATING,
    k: float = DEFAULT_CLUB_K,
    home_adv: float = DEFAULT_HOME_ADV,
) -> ClubRatingPool:
    ratings: dict[str, float] = {}
    matches_replayed = 0
    for result in sorted(results, key=lambda item: item.date):
        home_rating = ratings.get(result.home_canonical, initial)
        away_rating = ratings.get(result.away_canonical, initial)
        new_home, new_away = update_pair(
            home_rating,
            away_rating,
            result.home_score,
            result.away_score,
            k=k,
            neutral=result.neutral,
            home_adv=home_adv,
        )
        ratings[result.home_canonical] = new_home
        ratings[result.away_canonical] = new_away
        matches_replayed += 1

    pool_ratings = {
        code: ClubRating(code=code, rating=int(round(value)))
        for code, value in ratings.items()
    }
    return ClubRatingPool(
        competition_id=competition_id,
        ratings=pool_ratings,
        matches_replayed=matches_replayed,
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
    source = str(path)
    if not path.exists():
        return ClubRatingLoadResult(
            pool=None,
            quality=ClubRatingQuality(
                mode="missing",
                source=source,
                competition_id=competition_id,
            ),
        )

    try:
        results, skipped_rows = _load_club_results_csv_with_skipped(path, competition_id)
    except OSError as exc:
        return ClubRatingLoadResult(
            pool=None,
            quality=ClubRatingQuality(
                mode="invalid",
                source=source,
                competition_id=competition_id,
                errors=(str(exc),),
            ),
        )

    pool = replay_club_ratings(
        results,
        competition_id,
        initial=initial,
        k=k,
        home_adv=home_adv,
    )
    if pool.matches_replayed == 0:
        return ClubRatingLoadResult(
            pool=None,
            quality=ClubRatingQuality(
                mode="invalid",
                source=source,
                competition_id=competition_id,
                matches_replayed=0,
                teams_rated=0,
                skipped_rows=skipped_rows,
                sample_too_small=True,
                errors=("no_valid_rows",),
            ),
        )

    sample_too_small = pool.matches_replayed < min_matches
    mode = "sample_too_small" if sample_too_small else "sample_replay"
    return ClubRatingLoadResult(
        pool=None if sample_too_small else pool,
        quality=ClubRatingQuality(
            mode=mode,
            source=source,
            competition_id=competition_id,
            matches_replayed=pool.matches_replayed,
            teams_rated=len(pool.ratings),
            skipped_rows=skipped_rows,
            sample_too_small=sample_too_small,
        ),
    )


def _load_club_results_csv_with_skipped(
    path: str | Path, competition_id: str
) -> tuple[list[ClubResult], int]:
    results: list[ClubResult] = []
    skipped_rows = 0
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("competition_id") or "").strip() != competition_id:
                continue
            result = _parse_result_row(row, competition_id)
            if result is None:
                skipped_rows += 1
                continue
            results.append(result)
    return results, skipped_rows


def _parse_result_row(row: dict[str, str], competition_id: str) -> ClubResult | None:
    raw_date = (row.get("date") or "").strip()
    try:
        date.fromisoformat(raw_date)
    except ValueError:
        return None

    home_team = (row.get("home_team") or "").strip()
    away_team = (row.get("away_team") or "").strip()
    if not home_team or not away_team:
        return None

    home_score = _parse_score(row.get("home_score"))
    away_score = _parse_score(row.get("away_score"))
    neutral = _parse_neutral(row.get("neutral"))
    if home_score is None or away_score is None or neutral is None:
        return None

    return ClubResult(
        competition_id=competition_id,
        season=(row.get("season") or "").strip(),
        date=raw_date,
        home_team=home_team,
        away_team=away_team,
        home_canonical=canonicalize_club(competition_id, home_team),
        away_canonical=canonicalize_club(competition_id, away_team),
        home_score=home_score,
        away_score=away_score,
        neutral=neutral,
    )


def _parse_score(value: str | None) -> int | None:
    stripped = (value or "").strip()
    if not stripped.isdigit():
        return None
    return int(stripped)


def _parse_neutral(value: str | None) -> bool | None:
    stripped = (value or "").strip().lower()
    if stripped == "":
        return False
    if stripped in {"1", "true", "yes", "y"}:
        return True
    if stripped in {"0", "false", "no", "n"}:
        return False
    return None
