from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
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
BLOCKING_PARSE_REASONS = {
    "duplicate_candidate",
    "invalid_date",
    "invalid_neutral",
    "invalid_score",
    "invalid_season",
    "team_alias_unmatched",
}


@dataclass(frozen=True)
class CSLParseIssue:
    source_id: str
    source_role: str
    row_number: int
    reason: str
    field: str
    value: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "source_id": self.source_id,
            "source_role": self.source_role,
            "row_number": self.row_number,
            "reason": self.reason,
            "field": self.field,
            "value": self.value,
        }


@dataclass(frozen=True)
class CSLResultRow:
    competition_id: str
    season: str
    date: str
    home_team_raw: str
    away_team_raw: str
    home_team: str
    away_team: str
    home_canonical: str
    away_canonical: str
    home_score: int
    away_score: int
    neutral: bool
    status: str
    source_id: str
    source_role: str
    source_agreement: str = "uncompared"
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
    def team_key(self) -> tuple[str, str, str]:
        return (self.season, self.home_canonical, self.away_canonical)

    def to_replay_row(self) -> dict[str, str]:
        return {
            "competition_id": self.competition_id,
            "season": self.season,
            "date": self.date,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "home_score": str(self.home_score),
            "away_score": str(self.away_score),
            "neutral": "1" if self.neutral else "0",
        }


@dataclass(frozen=True)
class CSLParseResult:
    competition_id: str
    source_id: str
    source_role: str
    raw_rows: int = 0
    rows: list[CSLResultRow] = field(default_factory=list)
    issues: list[CSLParseIssue] = field(default_factory=list)

    @property
    def valid_rows(self) -> int:
        return len(self.rows)

    def to_summary(self) -> dict[str, object]:
        return {
            "competition_id": self.competition_id,
            "source_id": self.source_id,
            "source_role": self.source_role,
            "raw_rows": self.raw_rows,
            "valid_rows": self.valid_rows,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _as_str(row: dict[str, Any], field_name: str) -> str:
    value = row.get(field_name, "")
    if value is None:
        return ""
    return str(value).strip()


def _issue(
    source_id: str,
    source_role: str,
    row_number: int,
    reason: str,
    field_name: str,
    value: str | None = None,
) -> CSLParseIssue:
    return CSLParseIssue(
        source_id=source_id,
        source_role=source_role,
        row_number=row_number,
        reason=reason,
        field=field_name,
        value=value,
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


def _source_match_id(row: dict[str, Any]) -> str | None:
    return _source_value(row, "source_match_id") or _source_value(row, "match_id")


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
            row_issues.append(_issue(source_id, source_role, row_number, "invalid_season", "season", season))

        parsed_date = _parse_iso_date(match_date)
        if parsed_date is None:
            row_issues.append(_issue(source_id, source_role, row_number, "invalid_date", "date", match_date))

        home_score = _parse_score(home_score_raw)
        if home_score is None:
            row_issues.append(_issue(source_id, source_role, row_number, "invalid_score", "home_score", home_score_raw))

        away_score = _parse_score(away_score_raw)
        if away_score is None:
            row_issues.append(_issue(source_id, source_role, row_number, "invalid_score", "away_score", away_score_raw))

        neutral = _parse_neutral(neutral_raw)
        if neutral is None:
            row_issues.append(_issue(source_id, source_role, row_number, "invalid_neutral", "neutral", neutral_raw))

        home_alias = match_known_club_alias(competition_id, home_team)
        if home_alias.canonical_key is None:
            row_issues.append(_issue(source_id, source_role, row_number, "team_alias_unmatched", "home_team", home_team))

        away_alias = match_known_club_alias(competition_id, away_team)
        if away_alias.canonical_key is None:
            row_issues.append(_issue(source_id, source_role, row_number, "team_alias_unmatched", "away_team", away_team))

        status, quality_flags, status_issue = _parse_status(
            status_raw,
            home_score is not None and away_score is not None,
        )
        if status_issue is not None:
            row_issues.append(_issue(source_id, source_role, row_number, status_issue, "status", status_raw))

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

        source_match_id = _source_match_id(row)
        source_url = _source_value(row, "source_url")
        parsed = CSLResultRow(
            competition_id=competition_id,
            season=season,
            date=parsed_date,
            home_team_raw=home_team,
            away_team_raw=away_team,
            home_team=home_team,
            away_team=away_team,
            home_canonical=home_alias.canonical_key,
            away_canonical=away_alias.canonical_key,
            home_score=home_score,
            away_score=away_score,
            neutral=neutral,
            status=status,
            source_id=source_id,
            source_role=source_role,
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
                    source_role,
                    row_number,
                    "duplicate_candidate",
                    "match_key",
                    "|".join(parsed.match_key),
                )
            )
            continue
        seen_match_keys.add(parsed.match_key)
        parsed_rows.append(parsed)

    return CSLParseResult(
        competition_id=competition_id,
        source_id=source_id,
        source_role=source_role,
        raw_rows=len(rows),
        rows=parsed_rows,
        issues=issues,
    )


@dataclass(frozen=True)
class CSLCrossCheckResult:
    competition_id: str
    primary: CSLParseResult
    check: CSLParseResult
    clean_rows: list[CSLResultRow]
    degraded_rows: list[CSLResultRow]
    quality: dict[str, Any]
    pending_gate: dict[str, Any]

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "competition_id": self.competition_id,
            "coverage": {
                "seasons": sorted({row.season for row in self.primary.rows}),
                "primary_rows": self.primary.valid_rows,
                "check_rows": self.check.valid_rows,
                "valid_finished_matches": len(self.clean_rows),
            },
            "sources": {
                "primary": self.primary.to_summary(),
                "check": self.check.to_summary(),
            },
            "quality": self.quality,
            "pending_gate": self.pending_gate,
        }


def _with_agreement(row: CSLResultRow, agreement: str, check: CSLResultRow | None = None) -> CSLResultRow:
    return replace(
        row,
        source_agreement=agreement,
        source_check_id=check.source_check_id if check is not None else row.source_check_id,
        source_check_url=check.source_check_url if check is not None else row.source_check_url,
    )


def _score(row: CSLResultRow) -> str:
    return f"{row.home_score}:{row.away_score}"


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _build_pending_gate(
    *,
    seasons: set[str],
    primary_required_fields_coverage: float,
    dual_source_score_agreement: float,
    date_home_away_agreement: float,
    team_alias_unmatched: list[str],
    manual_review_required: list[dict[str, Any]],
    missing_in_primary: list[dict[str, Any]],
    degraded_candidates: list[dict[str, Any]],
    valid_finished_matches: int,
    min_valid_matches: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not CSL_SEASONS.issubset(seasons):
        reasons.append("season_coverage_incomplete")
    if primary_required_fields_coverage < 0.99:
        reasons.append("primary_required_fields_coverage_below_99pct")
    if dual_source_score_agreement < 0.99:
        reasons.append("dual_source_score_agreement_below_99pct")
    if date_home_away_agreement < 0.98:
        reasons.append("date_home_away_agreement_below_98pct")
    if team_alias_unmatched:
        reasons.append("team_alias_unmatched")
    if manual_review_required:
        reasons.append("manual_review_required")
    if missing_in_primary:
        reasons.append("missing_in_primary")
    if degraded_candidates:
        reasons.append("missing_in_check")
    if valid_finished_matches < min_valid_matches:
        reasons.append(f"valid_finished_matches_below_{min_valid_matches}")
    return {
        "can_enter_replay": len(reasons) == 0,
        "can_lift_club_rating_pending": False,
        "reasons": reasons,
    }


def _blocking_parse_issue_reviews(*results: CSLParseResult) -> list[dict[str, Any]]:
    return [
        {
            "reason": issue.reason,
            "source_id": issue.source_id,
            "source_role": issue.source_role,
            "row_number": issue.row_number,
            "field": issue.field,
            "value": issue.value,
        }
        for result in results
        for issue in result.issues
        if issue.reason in BLOCKING_PARSE_REASONS
    ]


def compare_csl_sources(
    primary: CSLParseResult,
    check: CSLParseResult,
    min_valid_matches: int = 300,
) -> CSLCrossCheckResult:
    check_by_match = {row.match_key: row for row in check.rows}
    check_by_team: dict[tuple[str, str, str], list[CSLResultRow]] = {}
    check_by_reversed_match: dict[tuple[str, str, str, str], list[CSLResultRow]] = {}
    for row in check.rows:
        check_by_team.setdefault(row.team_key, []).append(row)
        reversed_match_key = (row.season, row.date, row.away_canonical, row.home_canonical)
        check_by_reversed_match.setdefault(reversed_match_key, []).append(row)

    clean_rows: list[CSLResultRow] = []
    degraded_rows: list[CSLResultRow] = []
    score_mismatches: list[dict[str, Any]] = []
    manual_review_required: list[dict[str, Any]] = []
    seen_check_keys: set[tuple[str, str, str, str]] = set()
    comparable = 0
    score_agree = 0
    date_home_away_agree = 0

    for row in primary.rows:
        check_row = check_by_match.get(row.match_key)
        if check_row is None:
            same_team_candidates = check_by_team.get(row.team_key, [])
            if len(same_team_candidates) > 1:
                seen_check_keys.update(candidate.match_key for candidate in same_team_candidates)
                comparable += 1
                manual_review_required.append(
                    {
                        "reason": "duplicate_candidate",
                        "season": row.season,
                        "home_canonical": row.home_canonical,
                        "away_canonical": row.away_canonical,
                        "primary_date": row.date,
                        "check_dates": sorted({candidate.date for candidate in same_team_candidates}),
                    }
                )
                continue
            if len(same_team_candidates) == 1:
                same_team_candidate = same_team_candidates[0]
                seen_check_keys.add(same_team_candidate.match_key)
                comparable += 1
                manual_review_required.append(
                    {
                        "reason": "date_mismatch",
                        "season": row.season,
                        "home_canonical": row.home_canonical,
                        "away_canonical": row.away_canonical,
                        "primary_date": row.date,
                        "check_date": same_team_candidate.date,
                    }
                )
                continue
            reversed_candidates = check_by_reversed_match.get(row.match_key, [])
            if reversed_candidates:
                reversed_row = reversed_candidates[0]
                seen_check_keys.add(reversed_row.match_key)
                comparable += 1
                manual_review_required.append(
                    {
                        "reason": "home_away_mismatch",
                        "season": row.season,
                        "date": row.date,
                        "primary_home_canonical": row.home_canonical,
                        "primary_away_canonical": row.away_canonical,
                        "check_home_canonical": reversed_row.home_canonical,
                        "check_away_canonical": reversed_row.away_canonical,
                    }
                )
                continue
            degraded_rows.append(_with_agreement(row, "missing_in_check"))
            continue

        seen_check_keys.add(check_row.match_key)
        comparable += 1
        date_home_away_agree += 1
        if (check_row.home_score, check_row.away_score) != (row.home_score, row.away_score):
            score_mismatches.append(
                {
                    "season": row.season,
                    "date": row.date,
                    "home_canonical": row.home_canonical,
                    "away_canonical": row.away_canonical,
                    "primary_score": _score(row),
                    "check_score": _score(check_row),
                }
            )
            manual_review_required.append(
                {
                    "reason": "score_mismatch",
                    "season": row.season,
                    "date": row.date,
                    "home_canonical": row.home_canonical,
                    "away_canonical": row.away_canonical,
                }
            )
            continue

        score_agree += 1
        clean_rows.append(_with_agreement(row, "match_agree", check_row))

    missing_in_primary = [
        {
            "reason": "missing_in_primary",
            "season": row.season,
            "date": row.date,
            "home_canonical": row.home_canonical,
            "away_canonical": row.away_canonical,
        }
        for row in check.rows
        if row.match_key not in seen_check_keys
    ]
    primary_issue_count = len(primary.issues)
    primary_required_fields_coverage = _rate(primary.valid_rows, primary.valid_rows + primary_issue_count)
    team_alias_unmatched = sorted(
        {
            issue.value
            for issue in primary.issues + check.issues
            if issue.reason == "team_alias_unmatched" and issue.value is not None
        }
    )
    degraded_candidates = [
        {
            "reason": row.source_agreement,
            "season": row.season,
            "date": row.date,
            "home_canonical": row.home_canonical,
            "away_canonical": row.away_canonical,
        }
        for row in degraded_rows
    ]
    parse_issue_reviews = _blocking_parse_issue_reviews(primary, check)
    manual_review_with_parse_issues = manual_review_required + parse_issue_reviews
    quality = {
        "primary_required_fields_coverage": primary_required_fields_coverage,
        "dual_source_score_agreement": _rate(score_agree, comparable),
        "date_home_away_agreement": _rate(date_home_away_agree, comparable),
        "team_alias_unmatched": team_alias_unmatched,
        "score_mismatches": score_mismatches,
        "manual_review_required": manual_review_with_parse_issues,
        "missing_in_primary": missing_in_primary,
        "degraded_candidates": degraded_candidates,
    }
    pending_gate = _build_pending_gate(
        seasons={row.season for row in primary.rows},
        primary_required_fields_coverage=quality["primary_required_fields_coverage"],
        dual_source_score_agreement=quality["dual_source_score_agreement"],
        date_home_away_agreement=quality["date_home_away_agreement"],
        team_alias_unmatched=team_alias_unmatched,
        manual_review_required=manual_review_with_parse_issues,
        missing_in_primary=missing_in_primary,
        degraded_candidates=degraded_candidates,
        valid_finished_matches=len(clean_rows),
        min_valid_matches=min_valid_matches,
    )
    return CSLCrossCheckResult(
        competition_id=primary.competition_id,
        primary=primary,
        check=check,
        clean_rows=clean_rows,
        degraded_rows=degraded_rows,
        quality=quality,
        pending_gate=pending_gate,
    )


def write_replay_candidate_csv(path: str | Path, rows: list[CSLResultRow]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPLAY_FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item.match_key):
            writer.writerow(row.to_replay_row())
    return output
