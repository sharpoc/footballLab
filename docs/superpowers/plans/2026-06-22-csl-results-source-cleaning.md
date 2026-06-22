# CSL Results Source Cleaning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-only CSL historical results parsing, cross-checking, and diagnostics path for 2023-2026 without wiring it into production runners or lifting `club_rating_pending`.

**Architecture:** Add a pure offline collector module that parses saved CSL result samples, requires strict competition-scoped club aliases, compares a primary source with a check source, and emits local diagnostics plus optional replay-candidate CSV. Add a dry-run CLI that reads ignored local sample files and writes `data/local/diagnostics/csl_results_source_probe.json`; it does not fetch the web by default and never writes online state.

**Tech Stack:** Python 3.11 standard library, current `worldcup` package, existing `worldcup.collectors.club_aliases`, current `tests/run_tests.py`, no new dependency, no default network calls, no live odds refresh.

---

## Scope And Safety

This plan implements `docs/superpowers/specs/2026-06-22-csl-results-source-cleaning-design.md`.

Do not call The Odds API, consume quota, read `.env`, print secrets, deploy, publish, update LaunchAgent, write ECS, or modify `worldcup.league_runner` in this plan. P9.3 may build a local replay-candidate CSV writer, but it must not replace `data/cache/club_results_csl_2026.csv` automatically and must not change `csl_2026.rating_policy`.

Public-source probing remains a separately confirmed execution action. The implementation below supports parsing saved public-source samples from ignored paths. If an execution session needs live public web reads, stop and ask for confirmation before running them.

Before each implementation task, run `git status --short`, touch only the files listed for that task, and do not revert unrelated dirty files.

## File Structure

Create or modify these files:

- Modify `worldcup/collectors/club_aliases.py`: add a strict known-alias matcher for CSL cleaning so unknown clubs cannot silently slugify into replay.
- Modify `tests/collectors/test_club_aliases.py`: cover strict known alias behavior separately from existing permissive canonicalization.
- Create `worldcup/collectors/csl_results.py`: parse local primary/check result rows, normalize fields, record row issues, compare sources, compute quality gates, and write replay-candidate CSV.
- Create `tests/collectors/test_csl_results.py`: parser, alias, status, mismatch, missing-row, quality-gate, and replay-candidate tests.
- Create `worldcup/csl_results_probe.py`: dry-run CLI that reads local sample files, runs parser/comparison, and writes diagnostics JSON to ignored paths.
- Create `tests/test_csl_results_probe.py`: CLI tests using temporary local sample files and injected argv.
- Modify `README.md`: document P9.3 local sample contract, diagnostics output, and safety boundary.
- Modify `RECENT_WORK.md`: record P9.3 implementation outcome after verification.

Do not modify:

- `worldcup/league_runner.py`
- `worldcup/club_rating.py`
- `worldcup/engine/`
- scheduler, publish, ingest, ECS, LaunchAgent, or secret handling

## Task 1: Strict CSL Alias Gate

**Files:**
- Modify: `worldcup/collectors/club_aliases.py`
- Modify: `tests/collectors/test_club_aliases.py`

- [ ] **Step 1: Write failing strict alias tests**

Append to `tests/collectors/test_club_aliases.py`:

```python
from worldcup.collectors.club_aliases import match_known_club_alias


def test_match_known_club_alias_accepts_configured_csl_alias_only():
    result = match_known_club_alias("csl_2026", "Shanghai SIPG")

    assert result.raw_name == "Shanghai SIPG"
    assert result.canonical_key == "shanghai_port"
    assert result.unmatched_name is None


def test_match_known_club_alias_blocks_slug_fallback_for_unknown_csl_team():
    result = match_known_club_alias("csl_2026", "Unknown FC")

    assert result.raw_name == "Unknown FC"
    assert result.canonical_key is None
    assert result.unmatched_name == "Unknown FC"


def test_permissive_canonicalize_club_remains_available_for_existing_callers():
    assert canonicalize_club("csl_2026", "Unknown FC") == "unknown_fc"
```

- [ ] **Step 2: Run tests and verify the new tests fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `cannot import name 'match_known_club_alias'`.

- [ ] **Step 3: Add strict matcher**

Modify `worldcup/collectors/club_aliases.py`:

```python
def match_known_club_alias(competition_id: str, name: str) -> TeamAliasResult:
    stripped = name.strip()
    aliases = _KNOWN_BY_COMPETITION.get(competition_id, {})
    canonical = aliases.get(stripped.lower())
    if canonical is None:
        return TeamAliasResult(stripped, None, stripped)
    return TeamAliasResult(stripped, canonical)
```

Do not change `canonicalize_club()` or `match_club_alias()` in this task. Existing odds parsing still uses the permissive path; P9.3 cleaning uses the strict path.

- [ ] **Step 4: Run alias tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for all `tests/collectors/test_club_aliases.py` tests.

- [ ] **Step 5: Commit strict alias gate**

```bash
git status --short
git add worldcup/collectors/club_aliases.py tests/collectors/test_club_aliases.py
git commit -m "feat: add strict csl alias gate"
```

## Task 2: CSL Result Row Parser

**Files:**
- Create: `worldcup/collectors/csl_results.py`
- Create: `tests/collectors/test_csl_results.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/collectors/test_csl_results.py`:

```python
from worldcup.collectors.csl_results import parse_csl_result_rows


def _row(**overrides):
    row = {
        "season": "2026",
        "round": "1",
        "date": "2026-03-01",
        "kickoff_time_local": "19:35",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "home_score": "2",
        "away_score": "0",
        "neutral": "0",
        "status": "finished",
        "source_match_id": "m1",
        "source_url": "https://example.invalid/m1",
    }
    row.update(overrides)
    return row


def test_parse_csl_result_rows_returns_finished_clean_rows():
    result = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.raw_rows == 1
    assert result.valid_rows == 1
    assert result.issues == []
    parsed = result.rows[0]
    assert parsed.competition_id == "csl_2026"
    assert parsed.season == "2026"
    assert parsed.date == "2026-03-01"
    assert parsed.home_team_raw == "Shanghai Port"
    assert parsed.away_team_raw == "Shandong Taishan"
    assert parsed.home_canonical == "shanghai_port"
    assert parsed.away_canonical == "shandong_taishan"
    assert parsed.home_score == 2
    assert parsed.away_score == 0
    assert parsed.neutral is False
    assert parsed.status == "finished"
    assert parsed.source_primary_id == "m1"
    assert parsed.source_primary_url == "https://example.invalid/m1"
    assert parsed.source_check_id is None
    assert parsed.match_key == ("2026", "2026-03-01", "shanghai_port", "shandong_taishan")


def test_parse_csl_result_rows_blocks_unknown_team_without_slug_fallback():
    result = parse_csl_result_rows(
        [_row(home_team="Unknown FC")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.rows == []
    assert result.valid_rows == 0
    assert result.issues[0].reason == "team_alias_unmatched"
    assert result.issues[0].field == "home_team"
    assert result.issues[0].value == "Unknown FC"


def test_parse_csl_result_rows_records_invalid_score_and_bad_date():
    result = parse_csl_result_rows(
        [
            _row(home_score="x"),
            _row(date="20260302"),
        ],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.rows == []
    assert [issue.reason for issue in result.issues] == ["invalid_score", "invalid_date"]


def test_parse_csl_result_rows_excludes_unfinished_status():
    result = parse_csl_result_rows(
        [_row(status="postponed")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.rows == []
    assert result.issues[0].reason == "status_not_finished"
    assert result.issues[0].value == "postponed"


def test_parse_csl_result_rows_infers_finished_when_status_is_blank_and_scores_exist():
    result = parse_csl_result_rows(
        [_row(status="")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert len(result.rows) == 1
    assert result.rows[0].status == "finished_inferred"
    assert result.rows[0].quality_flags == ("status_inferred_finished",)


def test_parse_csl_result_rows_reports_duplicate_match_candidate():
    result = parse_csl_result_rows(
        [_row(source_match_id="m1"), _row(source_match_id="m2")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert len(result.rows) == 1
    assert result.issues[0].reason == "duplicate_candidate"
    assert result.issues[0].field == "match_key"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.collectors.csl_results'`.

- [ ] **Step 3: Implement parser module**

Create `worldcup/collectors/csl_results.py`:

```python
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class CSLParseIssue:
    source_id: str
    source_role: str
    row_number: int
    reason: str
    field: str
    value: str

    def to_dict(self) -> dict[str, Any]:
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
    round: str | None
    date: str
    kickoff_time_local: str | None
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
    source_primary_id: str | None = None
    source_primary_url: str | None = None
    source_check_id: str | None = None
    source_check_url: str | None = None
    source_agreement: str = "uncompared"
    quality_flags: tuple[str, ...] = field(default_factory=tuple)

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
    raw_rows: int
    rows: list[CSLResultRow]
    issues: list[CSLParseIssue]

    @property
    def valid_rows(self) -> int:
        return len(self.rows)

    def to_summary(self) -> dict[str, Any]:
        return {
            "competition_id": self.competition_id,
            "source_id": self.source_id,
            "source_role": self.source_role,
            "raw_rows": self.raw_rows,
            "valid_rows": self.valid_rows,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _string(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def _issue(
    source_id: str,
    source_role: str,
    row_number: int,
    reason: str,
    field: str,
    value: str,
) -> CSLParseIssue:
    return CSLParseIssue(
        source_id=source_id,
        source_role=source_role,
        row_number=row_number,
        reason=reason,
        field=field,
        value=value,
    )


def _parse_int_score(value: str) -> int | None:
    if not value.isdigit():
        return None
    parsed = int(value)
    if parsed < 0:
        return None
    return parsed


def _parse_neutral(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered == "":
        return False
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    return None


def _parse_date(value: str) -> str | None:
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _source_match_id(row: dict[str, Any]) -> str | None:
    return _string(row, "source_match_id") or _string(row, "match_id") or None


def _source_url(row: dict[str, Any]) -> str | None:
    return _string(row, "source_url") or None


def _parse_status(value: str, has_scores: bool) -> tuple[str | None, tuple[str, ...]]:
    lowered = value.lower()
    if lowered in FINISHED_STATUSES:
        return "finished", ()
    if lowered == "" and has_scores:
        return "finished_inferred", ("status_inferred_finished",)
    return None, ()


def parse_csl_result_rows(
    rows: list[dict[str, Any]],
    competition_id: str,
    source_id: str,
    source_role: str,
) -> CSLParseResult:
    parsed_rows: list[CSLResultRow] = []
    issues: list[CSLParseIssue] = []
    seen_match_keys: set[tuple[str, str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        season = _string(row, "season")
        if season not in CSL_SEASONS:
            issues.append(_issue(source_id, source_role, index, "season_out_of_scope", "season", season))
            continue

        parsed_date = _parse_date(_string(row, "date"))
        if parsed_date is None:
            issues.append(_issue(source_id, source_role, index, "invalid_date", "date", _string(row, "date")))
            continue

        home_score = _parse_int_score(_string(row, "home_score"))
        away_score = _parse_int_score(_string(row, "away_score"))
        if home_score is None or away_score is None:
            issues.append(_issue(source_id, source_role, index, "invalid_score", "score", f"{_string(row, 'home_score')}:{_string(row, 'away_score')}"))
            continue

        status, status_flags = _parse_status(_string(row, "status"), has_scores=True)
        if status is None:
            issues.append(_issue(source_id, source_role, index, "status_not_finished", "status", _string(row, "status")))
            continue

        neutral = _parse_neutral(_string(row, "neutral"))
        if neutral is None:
            issues.append(_issue(source_id, source_role, index, "invalid_neutral", "neutral", _string(row, "neutral")))
            continue

        home_raw = _string(row, "home_team")
        away_raw = _string(row, "away_team")
        home_match = match_known_club_alias(competition_id, home_raw)
        away_match = match_known_club_alias(competition_id, away_raw)
        if home_match.unmatched_name:
            issues.append(_issue(source_id, source_role, index, "team_alias_unmatched", "home_team", home_raw))
            continue
        if away_match.unmatched_name:
            issues.append(_issue(source_id, source_role, index, "team_alias_unmatched", "away_team", away_raw))
            continue

        result_row = CSLResultRow(
            competition_id=competition_id,
            season=season,
            round=_string(row, "round") or None,
            date=parsed_date,
            kickoff_time_local=_string(row, "kickoff_time_local") or None,
            home_team_raw=home_raw,
            away_team_raw=away_raw,
            home_team=home_raw,
            away_team=away_raw,
            home_canonical=home_match.canonical_key or "",
            away_canonical=away_match.canonical_key or "",
            home_score=home_score,
            away_score=away_score,
            neutral=neutral,
            status=status,
            source_id=source_id,
            source_role=source_role,
            source_primary_id=_source_match_id(row) if source_role == "primary" else None,
            source_primary_url=_source_url(row) if source_role == "primary" else None,
            source_check_id=_source_match_id(row) if source_role == "check" else None,
            source_check_url=_source_url(row) if source_role == "check" else None,
            quality_flags=status_flags,
        )
        if result_row.match_key in seen_match_keys:
            issues.append(
                _issue(
                    source_id,
                    source_role,
                    index,
                    "duplicate_candidate",
                    "match_key",
                    "|".join(result_row.match_key),
                )
            )
            continue
        seen_match_keys.add(result_row.match_key)
        parsed_rows.append(result_row)

    return CSLParseResult(
        competition_id=competition_id,
        source_id=source_id,
        source_role=source_role,
        raw_rows=len(rows),
        rows=parsed_rows,
        issues=issues,
    )
```

- [ ] **Step 4: Run parser tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for `tests/collectors/test_csl_results.py` parser tests.

- [ ] **Step 5: Commit parser**

```bash
git status --short
git add worldcup/collectors/csl_results.py tests/collectors/test_csl_results.py
git commit -m "feat: add csl results parser"
```

## Task 3: Dual-Source Comparison And Quality Gate

**Files:**
- Modify: `worldcup/collectors/csl_results.py`
- Modify: `tests/collectors/test_csl_results.py`

- [ ] **Step 1: Add failing comparison and gate tests**

Append to `tests/collectors/test_csl_results.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.collectors.csl_results import (
    compare_csl_sources,
    write_replay_candidate_csv,
)


def test_compare_csl_sources_allows_exact_dual_source_agreement():
    primary = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [_row(source_match_id="c1")],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert len(comparison.clean_rows) == 1
    assert comparison.clean_rows[0].source_agreement == "match_agree"
    assert comparison.quality["dual_source_score_agreement"] == 1.0
    assert comparison.quality["date_home_away_agreement"] == 1.0
    assert comparison.pending_gate["can_enter_replay"] is False
    assert comparison.pending_gate["can_lift_club_rating_pending"] is False
    assert "valid_finished_matches_below_300" in comparison.pending_gate["reasons"]


def test_compare_csl_sources_blocks_score_mismatch_from_replay():
    primary = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [_row(home_score="1", source_match_id="c1")],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert comparison.clean_rows == []
    assert comparison.quality["score_mismatches"][0]["primary_score"] == "2:0"
    assert comparison.quality["score_mismatches"][0]["check_score"] == "1:0"
    assert comparison.pending_gate["can_enter_replay"] is False


def test_compare_csl_sources_reports_date_mismatch_without_auto_merge():
    primary = parse_csl_result_rows(
        [_row(date="2026-03-01")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [_row(date="2026-03-02", source_match_id="c1")],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert comparison.clean_rows == []
    assert comparison.quality["manual_review_required"][0]["reason"] == "date_mismatch"
    assert comparison.quality["date_home_away_agreement"] == 0.0


def test_compare_csl_sources_reports_home_away_mismatch_without_auto_merge():
    primary = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [_row(home_team="Shandong Taishan", away_team="Shanghai Port", source_match_id="c1")],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert comparison.clean_rows == []
    assert comparison.quality["manual_review_required"][0]["reason"] == "home_away_mismatch"
    assert comparison.quality["date_home_away_agreement"] == 0.0


def test_compare_csl_sources_reports_missing_check_row_as_degraded_candidate():
    primary = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert comparison.clean_rows == []
    assert comparison.degraded_rows[0].source_agreement == "missing_in_check"
    assert comparison.pending_gate["can_enter_replay"] is False


def test_compare_csl_sources_reports_check_only_rows_as_missing_primary():
    primary = parse_csl_result_rows(
        [_row()],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [
            _row(source_match_id="c1"),
            _row(date="2026-03-08", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="c2"),
        ],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    comparison = compare_csl_sources(primary, check)

    assert comparison.quality["missing_in_primary"] == [
        {
            "reason": "missing_in_primary",
            "season": "2026",
            "date": "2026-03-08",
            "home_canonical": "shanghai_shenhua",
            "away_canonical": "beijing_guoan",
        }
    ]


def test_write_replay_candidate_csv_uses_p92_contract():
    primary = parse_csl_result_rows(
        [_row(), _row(date="2026-03-08", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )
    check = parse_csl_result_rows(
        [_row(source_match_id="c1"), _row(date="2026-03-08", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="c2")],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )
    comparison = compare_csl_sources(primary, check, min_valid_matches=2)

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "club_results_csl_2026.candidate.csv"
        write_replay_candidate_csv(path, comparison.clean_rows)

        assert path.read_text(encoding="utf-8").splitlines() == [
            "competition_id,season,date,home_team,away_team,home_score,away_score,neutral",
            "csl_2026,2026,2026-03-01,Shanghai Port,Shandong Taishan,2,0,0",
            "csl_2026,2026,2026-03-08,Shanghai Shenhua,Beijing Guoan,1,1,0",
        ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing missing `compare_csl_sources` and `write_replay_candidate_csv`.

- [ ] **Step 3: Implement comparison, quality gate, and CSV writer**

Append to `worldcup/collectors/csl_results.py`:

```python
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
    return CSLResultRow(
        competition_id=row.competition_id,
        season=row.season,
        round=row.round,
        date=row.date,
        kickoff_time_local=row.kickoff_time_local,
        home_team_raw=row.home_team_raw,
        away_team_raw=row.away_team_raw,
        home_team=row.home_team,
        away_team=row.away_team,
        home_canonical=row.home_canonical,
        away_canonical=row.away_canonical,
        home_score=row.home_score,
        away_score=row.away_score,
        neutral=row.neutral,
        status=row.status,
        source_id=row.source_id,
        source_role=row.source_role,
        source_primary_id=row.source_primary_id,
        source_primary_url=row.source_primary_url,
        source_check_id=check.source_check_id if check else row.source_check_id,
        source_check_url=check.source_check_url if check else row.source_check_url,
        source_agreement=agreement,
        quality_flags=row.quality_flags,
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
    if valid_finished_matches < min_valid_matches:
        reasons.append(f"valid_finished_matches_below_{min_valid_matches}")
    return {
        "can_enter_replay": len(reasons) == 0,
        "can_lift_club_rating_pending": False,
        "reasons": reasons,
    }


def compare_csl_sources(
    primary: CSLParseResult,
    check: CSLParseResult,
    min_valid_matches: int = 300,
) -> CSLCrossCheckResult:
    check_by_team = {row.team_key: row for row in check.rows}
    check_by_reversed_team = {
        (row.season, row.away_canonical, row.home_canonical): row
        for row in check.rows
    }
    clean_rows: list[CSLResultRow] = []
    degraded_rows: list[CSLResultRow] = []
    score_mismatches: list[dict[str, Any]] = []
    manual_review_required: list[dict[str, Any]] = []
    seen_check_keys: set[tuple[str, str, str]] = set()
    comparable = 0
    score_agree = 0
    date_home_away_agree = 0

    for row in primary.rows:
        check_row = check_by_team.get(row.team_key)
        if check_row is None:
            reversed_row = check_by_reversed_team.get(row.team_key)
            if reversed_row is not None and reversed_row.date == row.date:
                seen_check_keys.add(reversed_row.team_key)
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
                comparable += 1
                continue
            degraded_rows.append(_with_agreement(row, "missing_in_check"))
            continue
        seen_check_keys.add(check_row.team_key)
        comparable += 1
        if check_row.date != row.date:
            manual_review_required.append(
                {
                    "reason": "date_mismatch",
                    "season": row.season,
                    "home_canonical": row.home_canonical,
                    "away_canonical": row.away_canonical,
                    "primary_date": row.date,
                    "check_date": check_row.date,
                }
            )
            continue
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
        if row.team_key not in seen_check_keys
    ]
    primary_issue_count = len(primary.issues)
    primary_required_fields_coverage = _rate(primary.valid_rows, primary.valid_rows + primary_issue_count)
    team_alias_unmatched = sorted(
        {
            issue.value
            for issue in primary.issues + check.issues
            if issue.reason == "team_alias_unmatched"
        }
    )
    quality = {
        "primary_required_fields_coverage": primary_required_fields_coverage,
        "dual_source_score_agreement": _rate(score_agree, comparable),
        "date_home_away_agreement": _rate(date_home_away_agree, comparable),
        "team_alias_unmatched": team_alias_unmatched,
        "score_mismatches": score_mismatches,
        "manual_review_required": manual_review_required,
        "missing_in_primary": missing_in_primary,
        "degraded_candidates": [
            {
                "reason": row.source_agreement,
                "season": row.season,
                "date": row.date,
                "home_canonical": row.home_canonical,
                "away_canonical": row.away_canonical,
            }
            for row in degraded_rows
        ],
    }
    pending_gate = _build_pending_gate(
        seasons={row.season for row in primary.rows},
        primary_required_fields_coverage=quality["primary_required_fields_coverage"],
        dual_source_score_agreement=quality["dual_source_score_agreement"],
        date_home_away_agreement=quality["date_home_away_agreement"],
        team_alias_unmatched=team_alias_unmatched,
        manual_review_required=manual_review_required,
        missing_in_primary=missing_in_primary,
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
```

- [ ] **Step 4: Run comparison tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for comparison and replay-candidate tests.

- [ ] **Step 5: Commit comparison and gate**

```bash
git status --short
git add worldcup/collectors/csl_results.py tests/collectors/test_csl_results.py
git commit -m "feat: compare csl result sources"
```

## Task 4: Local Dry-Run Probe CLI

**Files:**
- Create: `worldcup/csl_results_probe.py`
- Create: `tests/test_csl_results_probe.py`

- [ ] **Step 1: Add failing CLI tests**

Create `tests/test_csl_results_probe.py`:

```python
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_results_probe import main, read_sample_rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "season",
        "round",
        "date",
        "kickoff_time_local",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "neutral",
        "status",
        "source_match_id",
        "source_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sample_row(**overrides):
    row = {
        "season": "2026",
        "round": "1",
        "date": "2026-03-01",
        "kickoff_time_local": "19:35",
        "home_team": "Shanghai Port",
        "away_team": "Shandong Taishan",
        "home_score": "2",
        "away_score": "0",
        "neutral": "0",
        "status": "finished",
        "source_match_id": "m1",
        "source_url": "https://example.invalid/m1",
    }
    row.update(overrides)
    return row


def test_read_sample_rows_supports_csv_and_json_files():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        csv_path = root / "sample.csv"
        json_path = root / "sample.json"
        _write_csv(csv_path, [_sample_row()])
        json_path.write_text(json.dumps([_sample_row(home_score="1")]), encoding="utf-8")

        assert read_sample_rows(csv_path)[0]["home_score"] == "2"
        assert read_sample_rows(json_path)[0]["home_score"] == "1"


def test_probe_main_writes_diagnostics_and_candidate_without_network():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        primary_path = root / "primary.csv"
        check_path = root / "check.csv"
        output_path = root / "diagnostics.json"
        replay_path = root / "candidate.csv"
        primary_rows = [
            _sample_row(season="2023", date="2023-04-01", source_match_id="m2023"),
            _sample_row(season="2024", date="2024-04-01", home_team="Shanghai Port", away_team="Beijing Guoan", home_score="1", away_score="0", source_match_id="m2024"),
            _sample_row(season="2025", date="2025-04-01", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="m2025"),
            _sample_row(season="2026", date="2026-03-01", source_match_id="m2026"),
        ]
        check_rows = [
            _sample_row(season="2023", date="2023-04-01", source_match_id="c2023"),
            _sample_row(season="2024", date="2024-04-01", home_team="Shanghai Port", away_team="Beijing Guoan", home_score="1", away_score="0", source_match_id="c2024"),
            _sample_row(season="2025", date="2025-04-01", home_team="Shanghai Shenhua", away_team="Beijing Guoan", home_score="1", away_score="1", source_match_id="c2025"),
            _sample_row(season="2026", date="2026-03-01", source_match_id="c2026"),
        ]
        _write_csv(primary_path, primary_rows)
        _write_csv(check_path, check_rows)

        code = main(
            [
                "--competition",
                "csl_2026",
                "--primary-source-id",
                "primary",
                "--primary-sample",
                str(primary_path),
                "--check-source-id",
                "check",
                "--check-sample",
                str(check_path),
                "--output",
                str(output_path),
                "--write-replay-candidate",
                str(replay_path),
                "--min-valid-matches",
                "4",
            ]
        )

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert code == 0
        assert payload["competition_id"] == "csl_2026"
        assert payload["coverage"]["valid_finished_matches"] == 4
        assert payload["pending_gate"]["can_enter_replay"] is True
        assert payload["pending_gate"]["can_lift_club_rating_pending"] is False
        assert replay_path.exists()
        assert "INGEST_HMAC_SECRET" not in output_path.read_text(encoding="utf-8")


def test_probe_main_does_not_write_replay_candidate_when_gate_fails():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        primary_path = root / "primary.csv"
        check_path = root / "check.csv"
        output_path = root / "diagnostics.json"
        replay_path = root / "candidate.csv"
        _write_csv(primary_path, [_sample_row()])
        _write_csv(check_path, [_sample_row(home_score="1")])

        code = main(
            [
                "--competition",
                "csl_2026",
                "--primary-source-id",
                "primary",
                "--primary-sample",
                str(primary_path),
                "--check-source-id",
                "check",
                "--check-sample",
                str(check_path),
                "--output",
                str(output_path),
                "--write-replay-candidate",
                str(replay_path),
            ]
        )

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert code == 1
        assert payload["quality"]["score_mismatches"]
        assert replay_path.exists() is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL containing `No module named 'worldcup.csl_results_probe'`.

- [ ] **Step 3: Implement CLI**

Create `worldcup/csl_results_probe.py`:

```python
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from worldcup.collectors.csl_results import (
    compare_csl_sources,
    parse_csl_result_rows,
    write_replay_candidate_csv,
)


def read_sample_rows(path: str | Path) -> list[dict[str, Any]]:
    sample_path = Path(path)
    suffix = sample_path.suffix.lower()
    if suffix == ".json":
        data = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON sample must be a list of row objects")
        return [dict(item) for item in data]
    if suffix == ".csv":
        with sample_path.open(newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    raise ValueError(f"Unsupported sample format: {sample_path}")


def _write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local CSL results source probe from saved samples.")
    parser.add_argument("--competition", default="csl_2026")
    parser.add_argument("--primary-source-id", required=True)
    parser.add_argument("--primary-sample", required=True)
    parser.add_argument("--check-source-id", required=True)
    parser.add_argument("--check-sample", required=True)
    parser.add_argument("--output", default="data/local/diagnostics/csl_results_source_probe.json")
    parser.add_argument("--write-replay-candidate", default=None)
    parser.add_argument("--min-valid-matches", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    primary = parse_csl_result_rows(
        read_sample_rows(args.primary_sample),
        competition_id=args.competition,
        source_id=args.primary_source_id,
        source_role="primary",
    )
    check = parse_csl_result_rows(
        read_sample_rows(args.check_sample),
        competition_id=args.competition,
        source_id=args.check_source_id,
        source_role="check",
    )
    comparison = compare_csl_sources(
        primary,
        check,
        min_valid_matches=args.min_valid_matches,
    )
    payload = comparison.to_diagnostics()
    _write_json(args.output, payload)
    if args.write_replay_candidate and comparison.pending_gate["can_enter_replay"]:
        write_replay_candidate_csv(args.write_replay_candidate, comparison.clean_rows)
    return 0 if not comparison.quality["manual_review_required"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

The CLI has no URL arguments and no transport hook in P9.3. A person may save public-source samples to `data/probe/` after separate confirmation, then run this CLI against those local files.

- [ ] **Step 4: Run CLI tests and verify they pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS for `tests/test_csl_results_probe.py`.

- [ ] **Step 5: Commit dry-run probe CLI**

```bash
git status --short
git add worldcup/csl_results_probe.py tests/test_csl_results_probe.py
git commit -m "feat: add csl results probe cli"
```

## Task 5: Documentation, Full Verification, And Handoff

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README with local P9.3 contract**

Add a short section to `README.md` near the local data or CSL league documentation:

````markdown
### CSL Historical Results Probe

P9.3 adds a local-only CSL results probe for 2023-2026. Save manually reviewed public-source samples under ignored paths such as `data/probe/`, then run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_results_probe \
  --competition csl_2026 \
  --primary-source-id <primary_id> \
  --primary-sample data/probe/csl_results_primary_sample.csv \
  --check-source-id <check_id> \
  --check-sample data/probe/csl_results_check_sample.csv \
  --output data/local/diagnostics/csl_results_source_probe.json
```

The probe reads local CSV/JSON samples, performs strict club alias matching, compares scores and dates across two sources, and writes diagnostics to `data/local/diagnostics/`. It does not fetch the web, read `.env`, call The Odds API, publish snapshots, deploy, or lift `club_rating_pending`. A replay-candidate CSV is only written when `--write-replay-candidate` is passed and the local quality gate allows replay entry; this candidate is not installed into the production `club_rating` cache by the probe.
````

- [ ] **Step 2: Update RECENT_WORK after implementation verification**

Add a new top entry to `RECENT_WORK.md`:

```markdown
## 2026-06-22 P9.3 中超历史赛果来源与清洗实现计划

- 新增 implementation plan：`docs/superpowers/plans/2026-06-22-csl-results-source-cleaning.md`。
- 计划分为严格 alias gate、CSL 赛果解析、双源校验与质量门槛、本地 dry-run probe CLI、文档与全量验证。
- P9.3 实现范围保持本地只读/本地写 ignored 诊断，不接 `league_runner`，不解除 `club_rating_pending`，不联网执行、不使用密钥、不部署。
```

If this entry is added before code implementation begins, keep the wording as “实现计划”。After actual code implementation finishes, update the same entry with verified test counts and commit hashes instead of adding a duplicate entry.

- [ ] **Step 3: Run full verification**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass with a final line like `N/N tests passed`.

Run:

```bash
git diff --check
```

Expected: no output.

Run:

```bash
rg -n -e "TB[D]" -e "TO[D]O" -e "implement late[r]" -e "fill in detail[s]" -e "Add appropriate error handlin[g]" -e "add validatio[n]" -e "handle edge case[s]" -e "Similar to Tas[k]" -e "Write tests for the abov[e]" docs/superpowers/plans/2026-06-22-csl-results-source-cleaning.md worldcup/collectors/csl_results.py worldcup/csl_results_probe.py tests/collectors/test_csl_results.py tests/test_csl_results_probe.py README.md RECENT_WORK.md
```

Expected: no output.

- [ ] **Step 4: Commit docs**

```bash
git status --short
git add README.md RECENT_WORK.md
git commit -m "docs: document csl results probe"
```

- [ ] **Step 5: Final status report**

Report:

- Exact commits created by this implementation.
- Test command and final pass count.
- `git diff --check` result.
- Confirmation that no network call, The Odds API quota, `.env`, HMAC secret, ECS, LaunchAgent, publish, or deploy action was used.
- Confirmation that `worldcup.league_runner` and `worldcup/club_rating.py` were not modified, and `club_rating_pending` remains enforced.

## Plan Self-Review

- Spec coverage: the plan covers 2023-2026 scope, strict alias handling, primary/check source parsing, dual-source date/score comparison, local diagnostics JSON, P9.2 replay CSV contract, and the explicit rule that `can_lift_club_rating_pending` remains false.
- Scope control: the plan does not add production runner wiring, model parameter changes, odds refresh, online publish, deployment, LaunchAgent changes, or secret handling.
- Type consistency: `CSLParseResult`, `CSLResultRow`, `CSLCrossCheckResult`, `parse_csl_result_rows()`, `compare_csl_sources()`, `write_replay_candidate_csv()`, and `read_sample_rows()` are introduced before later tasks reference them.
- Verification: each code task starts with failing tests, then implementation, then the standard local test runner, then a focused commit.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-22-csl-results-source-cleaning.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
