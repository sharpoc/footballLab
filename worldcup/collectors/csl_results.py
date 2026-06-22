from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from worldcup.collectors.club_aliases import match_known_club_alias


CSL_SEASONS = {"2023", "2024", "2025", "2026"}
FINISHED_STATUSES = {"finished", "final", "ft", "fulltime", "full time"}
REPLAY_FIELDS = [
    "competition_id",
    "season",
    "date",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
]


@dataclass(frozen=True)
class CSLParseIssue:
    source_id: str
    row_number: int
    reason: str
    field: str
    value: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class CSLResultRow:
    competition_id: str
    season: str
    date: str
    home_team_raw: str
    away_team_raw: str
    home_canonical: str
    away_canonical: str
    home_score: int
    away_score: int
    neutral: bool
    status: str
    source_id: str
    source_primary_id: str | None = None
    source_primary_url: str | None = None
    source_check_id: str | None = None
    source_check_url: str | None = None
    round: str | None = None
    kickoff_time_local: str | None = None
    quality_flags: tuple[str, ...] = ()

    @property
    def match_key(self) -> tuple[str, str, str, str]:
        return (self.season, self.date, self.home_canonical, self.away_canonical)

    @property
    def team_key(self) -> tuple[str, str]:
        return (self.home_canonical, self.away_canonical)

    def to_replay_row(self) -> dict[str, str]:
        return {
            "competition_id": self.competition_id,
            "season": self.season,
            "date": self.date,
            "home_team": self.home_canonical,
            "away_team": self.away_canonical,
            "home_score": str(self.home_score),
            "away_score": str(self.away_score),
            "neutral": "1" if self.neutral else "0",
        }


@dataclass(frozen=True)
class CSLParseResult:
    rows: list[CSLResultRow] = field(default_factory=list)
    issues: list[CSLParseIssue] = field(default_factory=list)
    raw_rows: int = 0

    @property
    def valid_rows(self) -> int:
        return len(self.rows)


def _as_str(row: dict[str, Any], field_name: str) -> str:
    value = row.get(field_name, "")
    if value is None:
        return ""
    return str(value).strip()


def _issue(
    source_id: str,
    row_number: int,
    reason: str,
    field_name: str,
    value: str | None = None,
    message: str | None = None,
) -> CSLParseIssue:
    return CSLParseIssue(
        source_id=source_id,
        row_number=row_number,
        reason=reason,
        field=field_name,
        value=value,
        message=message,
    )


def _parse_score(value: str) -> int | None:
    if not value.isdigit():
        return None
    return int(value)


def _parse_neutral(value: str) -> bool | None:
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"", "0", "false", "no", "n"}:
        return False
    return None


def _parse_iso_date(value: str) -> str | None:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _source_value(row: dict[str, Any], field_name: str) -> str | None:
    value = _as_str(row, field_name)
    return value or None


def _parse_status(status: str, has_scores: bool) -> tuple[str | None, tuple[str, ...], str | None]:
    normalized = status.lower()
    if normalized in FINISHED_STATUSES:
        return "finished", (), None
    if not normalized and has_scores:
        return "finished_inferred", ("status_inferred_finished",), None
    return None, (), "status_not_finished"


def parse_csl_result_rows(
    rows: list[dict[str, Any]],
    *,
    competition_id: str,
    source_id: str,
    source_role: str,
) -> CSLParseResult:
    parsed_rows: list[CSLResultRow] = []
    issues: list[CSLParseIssue] = []
    seen_match_keys: set[tuple[str, str, str, str]] = set()

    for row_number, row in enumerate(rows, start=1):
        season = _as_str(row, "season")
        match_date = _as_str(row, "date")
        home_team = _as_str(row, "home_team")
        away_team = _as_str(row, "away_team")
        home_score_raw = _as_str(row, "home_score")
        away_score_raw = _as_str(row, "away_score")
        neutral_raw = _as_str(row, "neutral")
        status_raw = _as_str(row, "status")
        row_issues: list[CSLParseIssue] = []

        if season not in CSL_SEASONS:
            row_issues.append(_issue(source_id, row_number, "invalid_season", "season", season))

        parsed_date = _parse_iso_date(match_date)
        if parsed_date is None:
            row_issues.append(_issue(source_id, row_number, "invalid_date", "date", match_date))

        home_score = _parse_score(home_score_raw)
        if home_score is None:
            row_issues.append(_issue(source_id, row_number, "invalid_score", "home_score", home_score_raw))

        away_score = _parse_score(away_score_raw)
        if away_score is None:
            row_issues.append(_issue(source_id, row_number, "invalid_score", "away_score", away_score_raw))

        neutral = _parse_neutral(neutral_raw)
        if neutral is None:
            row_issues.append(_issue(source_id, row_number, "invalid_neutral", "neutral", neutral_raw))

        home_alias = match_known_club_alias(competition_id, home_team)
        if home_alias.canonical_key is None:
            row_issues.append(_issue(source_id, row_number, "team_alias_unmatched", "home_team", home_team))

        away_alias = match_known_club_alias(competition_id, away_team)
        if away_alias.canonical_key is None:
            row_issues.append(_issue(source_id, row_number, "team_alias_unmatched", "away_team", away_team))

        status, quality_flags, status_issue = _parse_status(
            status_raw,
            home_score is not None and away_score is not None,
        )
        if status_issue is not None:
            row_issues.append(_issue(source_id, row_number, status_issue, "status", status_raw))

        if row_issues:
            issues.extend(row_issues)
            continue

        assert parsed_date is not None
        assert home_score is not None
        assert away_score is not None
        assert neutral is not None
        assert status is not None
        assert home_alias.canonical_key is not None
        assert away_alias.canonical_key is not None

        source_match_id = _source_value(row, "source_match_id")
        source_url = _source_value(row, "source_url")
        parsed = CSLResultRow(
            competition_id=competition_id,
            season=season,
            date=parsed_date,
            home_team_raw=home_team,
            away_team_raw=away_team,
            home_canonical=home_alias.canonical_key,
            away_canonical=away_alias.canonical_key,
            home_score=home_score,
            away_score=away_score,
            neutral=neutral,
            status=status,
            source_id=source_id,
            source_primary_id=source_match_id if source_role == "primary" else None,
            source_primary_url=source_url if source_role == "primary" else None,
            source_check_id=source_match_id if source_role == "check" else None,
            source_check_url=source_url if source_role == "check" else None,
            round=_source_value(row, "round"),
            kickoff_time_local=_source_value(row, "kickoff_time_local"),
            quality_flags=quality_flags,
        )
        if parsed.match_key in seen_match_keys:
            issues.append(
                _issue(
                    source_id,
                    row_number,
                    "duplicate_candidate",
                    "match_key",
                    "|".join(parsed.match_key),
                )
            )
            continue
        seen_match_keys.add(parsed.match_key)
        parsed_rows.append(parsed)

    return CSLParseResult(rows=parsed_rows, issues=issues, raw_rows=len(rows))
