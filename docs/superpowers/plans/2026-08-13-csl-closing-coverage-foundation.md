# CSL Closing Coverage Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline, auditable foundation that freezes the 128 pre-archive CSL gaps by match ID, assigns exactly one provenance/coverage state to every accepted finished match, and continuously detects future observed-closing gaps without changing picks, public APIs, provider quota, or official performance semantics.

**Architecture:** Add one pure coverage domain module and one local-only runner. The domain module owns identity, dual-source kickoff resolution, state precedence, observed-closing selection, full reconciliation, and fingerprints; the runner owns ignored-file reads, dry-run/write boundaries, atomic report/pending updates, and CLI summaries. The existing CSL scheduler only consumes a read-only coverage projection when making its current due decision and invokes the local audit as a non-blocking side effect; it never lets coverage bypass `due`, quota, provider availability, or `--live`.

**Tech Stack:** Python 3 standard library (`dataclasses`, `datetime`, `hashlib`, `json`, `os`, `pathlib`, `tempfile`), existing CSL fixture parsers, existing `ClubResult` CSV loader, existing `settle_match_decision()` / `summarize_decision_records()`, and the repository's dependency-free `tests/run_tests.py` runner.

## Global Constraints

- This plan implements only the confirmed foundation: state contract, fixed 128-match manifest, observed-only full reconciliation, future archive coverage projection, pending recovery, scheduler integration, tests, and documentation.
- Do not implement a historical odds source probe, source-specific parser, normalized quote schema, reconstructed decision runner, source approval workflow, bulk network collection, or immutable reconstructed bundle in this plan.
- No new coverage/manifest/audit path may access the network, read `.env`, call The Odds API, consume quota, publish, deploy, write a database, or change `club_rating_pending`. Task 6 preserves the existing live scheduler's separately authorized env/provider/publish behavior after its current gates; coverage itself remains local-only.
- `provenance_class` is exactly one of `observed`, `reconstructed`, `none`.
- `coverage_status` is exactly one of `observed_current_decision`, `observed_missing_current_decision`, `reconstructed`, `market_baseline_only`, `manual_review`, `missing`.
- The fixed precedence and reason-code whitelist from `docs/superpowers/specs/2026-08-12-csl-closing-backfill-and-coverage-design.md` are normative; observed evidence always wins and can never be overwritten or downgraded by later evidence.
- The initial reconstruction allowlist is exactly the 128 missing match IDs observed before `2026-06-29`; the date is used only during one-time bootstrap, never as future reconstruction membership.
- Recover kickoff from the two already accepted local result-source samples (`cfl_official_2026.json` and `sevenm_2026_fixture.js`); both sources must resolve the same canonical home/away identity and exact UTC kickoff.
- A current observed decision requires `schema_version=2`, `policy_version=match_pick_v3`, and `label` in `MATCH_PICK` / `NO_CLEAN_MARKET`; only settled observed `MATCH_PICK` records enter the official hit/miss headline.
- Reconstructed evidence remains absent in the runtime path delivered here. The pure state classifier may accept a future normalized evidence object so precedence can be unit-tested, but the runner must pass no reconstructed evidence.
- The canonical coverage report is one self-contained ignored JSON file updated with temp-file + `fsync` + `os.replace`; pending is a separate recovery signal.
- The canonical report carries a deduplicated safe operational-event history so `quota_blocked`, `provider_refresh_failed`, `snapshot_archive_failed`, `archive_validation_failed`, and unresolved `closing_archive_missing` remain distinguishable without storing exception text or provider payloads.
- Manifest/report/pending read-modify-write and archive target creation use process-level file locks; concurrent scheduler/manual runs must serialize, preserve both event sets, and never clear another run's pending state.
- Standalone runner defaults to dry-run and zero writes. Manifest/report/pending writes require explicit `--write-initial-manifest` or `--write`.
- Audit failure is non-blocking and exposes only stable reason/error-type fields; exception messages, secrets, raw odds, request headers, and source payloads must not appear in scheduler output or public snapshots.
- Coverage annotations may explain an already-due scheduler decision but must not independently set `should_refresh=true`, relax global throttling, revive postponed/started matches, or override quota exhaustion.
- Reuse current snapshot selection and settlement contracts; do not alter `match_pick_v3`, result scores, API schemas, SQLite schema, or existing `csl_postmatch_shadow` calculations.
- All implementation uses TDD. Run the full dependency-free suite after each task because it currently completes quickly.
- Each implementation commit must contain only the files named in that task. Push, PR, merge, deploy, and source-network work remain separately confirmed actions.

## File Map

- Create `worldcup/csl_closing_coverage.py`: pure types, fixed state/reason contract, stable match identity, dual-source kickoff resolution, exact observed closing selection, initial manifest construction, full coverage report, official observed tally, fingerprints, and read-only scheduler coverage candidates.
- Create `worldcup/csl_closing_coverage_runner.py`: local input loading, CLI, dry-run/write behavior, atomic JSON/pending lifecycle, immutable initial-manifest enforcement, and safe summaries.
- Create `tests/test_csl_closing_coverage.py`: unit tests for status precedence, fixture conflicts, manifest freezing, full reconciliation, performance separation, fingerprints, and scheduler candidates.
- Create `tests/test_csl_closing_coverage_runner.py`: local runner tests for dry-run, atomic writes, idempotency, pending recovery, failures, and sensitive-data projection.
- Modify `worldcup/csl_snapshot_archive.py`: make first-time archive creation atomic and re-open/validate the stored file before returning success.
- Modify `tests/test_csl_snapshot_archive.py`: prove interrupted/replaced writes do not expose partial archive content and stored identity matches the source.
- Modify `worldcup/csl_scheduled_publish.py`: add read-only candidate annotation, inject and invoke the local full audit without provider calls, preserve pending recovery on non-due wakes, and keep failures non-blocking.
- Modify `tests/test_csl_scheduled_publish.py`: prove call order, zero behavior change to due/quota, failure isolation, safe summaries, and non-due pending recovery.
- Modify `README.md`: document component boundaries, commands, ignored artifacts, observed/reconstructed separation, and operational behavior.
- Modify `docs/superpowers/data-contract.md`: add the formal status/provenance/report/pending/scheduler contract.
- Modify `AGENTS.md` and `CLAUDE.md`: keep the local project rules synchronized with the new coverage boundary.
- Modify `RECENT_WORK.md`: record the verified implementation result, preserving the existing recent-work retention rule.

---

### Task 1: Freeze the coverage state and reason contract

**Files:**
- Create: `worldcup/csl_closing_coverage.py`
- Create: `tests/test_csl_closing_coverage.py`

**Interfaces:**
- Consumes: `worldcup.club_rating.ClubResult`, `worldcup.csl_eval_data.ClosingMatch`.
- Produces: `stable_match_id(result: ClubResult) -> str`, `HistoricalCoverageEvidence`, `CoverageClassification`, `classify_coverage(*, observed: ClosingMatch | None, historical: HistoricalCoverageEvidence | tuple[HistoricalCoverageEvidence, ...] | None = None) -> CoverageClassification`, `classification_dict(value: CoverageClassification) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing state-contract tests**

Create `tests/test_csl_closing_coverage.py` with these imports, helpers, and tests:

```python
from __future__ import annotations

from worldcup.club_rating import ClubResult
from worldcup.csl_closing_coverage import (
    HistoricalCoverageEvidence,
    classify_coverage,
    classification_dict,
    stable_match_id,
)
from worldcup.csl_eval_data import ClosingMatch


def _result(date: str = "2026-03-06") -> ClubResult:
    return ClubResult(
        competition_id="csl_2026",
        season="2026",
        date=date,
        home_team="成都蓉城",
        away_team="深圳新鹏城",
        home_canonical="chengdu_rongcheng",
        away_canonical="shenzhen_peng_city",
        home_score=5,
        away_score=1,
        neutral=False,
    )


def _closing(decision: object) -> ClosingMatch:
    return ClosingMatch(
        entry={
            "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
            "match_decision": decision,
        },
        snapshot_at="2026-03-06T11:10:00+00:00",
        snapshot_run_id="observed-run",
    )


def test_stable_match_id_uses_accepted_result_identity():
    assert stable_match_id(_result()) == (
        "csl_2026:2026-03-06:chengdu_rongcheng:shenzhen_peng_city"
    )


def test_observed_current_decision_has_highest_precedence():
    result = classify_coverage(
        observed=_closing(
            {
                "schema_version": 2,
                "policy_version": "match_pick_v3",
                "label": "MATCH_PICK",
            }
        ),
        historical=HistoricalCoverageEvidence(
            status="manual_review",
            reason_codes=("source_conflict",),
        ),
    )
    assert classification_dict(result) == {
        "provenance_class": "observed",
        "coverage_status": "observed_current_decision",
        "reason_code": "observed_closing",
        "reason_codes": ["observed_closing"],
    }


def test_observed_legacy_and_missing_decisions_are_not_current():
    legacy = classify_coverage(
        observed=_closing({"schema_version": 1, "label": "S"})
    )
    missing = classify_coverage(observed=_closing(None))
    assert legacy.coverage_status == "observed_missing_current_decision"
    assert legacy.reason_codes == ("legacy_decision",)
    assert missing.coverage_status == "observed_missing_current_decision"
    assert missing.reason_codes == ("no_current_decision",)


def test_non_observed_statuses_enforce_provenance_and_reason_whitelist():
    reconstructed = classify_coverage(
        observed=None,
        historical=HistoricalCoverageEvidence(
            status="reconstructed",
            reason_codes=("reconstructed_eligible",),
        ),
    )
    assert reconstructed.provenance_class == "reconstructed"

    try:
        classify_coverage(
            observed=None,
            historical=HistoricalCoverageEvidence(
                status="missing",
                reason_codes=("source_conflict",),
            ),
        )
    except ValueError as exc:
        assert str(exc) == "reason_not_allowed:missing:source_conflict"
    else:
        raise AssertionError("expected reason whitelist violation")


def test_same_priority_reasons_use_deterministic_primary_order():
    value = classify_coverage(
        observed=None,
        historical=HistoricalCoverageEvidence(
            status="missing",
            reason_codes=("post_kickoff_only", "source_unavailable"),
        ),
    )
    assert value.reason_code == "source_unavailable"
    assert value.reason_codes == ("source_unavailable", "post_kickoff_only")


def test_historical_candidates_follow_manual_reconstructed_baseline_missing_priority():
    value = classify_coverage(
        observed=None,
        historical=(
            HistoricalCoverageEvidence("missing", ("no_market_record",)),
            HistoricalCoverageEvidence("market_baseline_only", ("aggregate_only",)),
            HistoricalCoverageEvidence("reconstructed", ("reconstructed_eligible",)),
            HistoricalCoverageEvidence("manual_review", ("source_conflict",)),
        ),
    )
    assert value.coverage_status == "manual_review"
    assert value.reason_code == "source_conflict"
```

- [ ] **Step 2: Run the suite and verify the new module is missing**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL while importing `worldcup.csl_closing_coverage`.

- [ ] **Step 3: Implement the fixed contract and classifier**

Create `worldcup/csl_closing_coverage.py` with this contract at the top of the file:

```python
"""Pure CSL closing coverage contracts and reconciliation helpers.

This module never reads files, secrets, databases, or network resources.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from worldcup.club_rating import ClubResult
from worldcup.csl_eval_data import ClosingMatch


ProvenanceClass = Literal["observed", "reconstructed", "none"]
CoverageStatus = Literal[
    "observed_current_decision",
    "observed_missing_current_decision",
    "reconstructed",
    "market_baseline_only",
    "manual_review",
    "missing",
]

STATUS_PROVENANCE: dict[str, ProvenanceClass] = {
    "observed_current_decision": "observed",
    "observed_missing_current_decision": "observed",
    "reconstructed": "reconstructed",
    "market_baseline_only": "none",
    "manual_review": "none",
    "missing": "none",
}
ALLOWED_REASON_CODES: dict[str, tuple[str, ...]] = {
    "observed_current_decision": ("observed_closing",),
    "observed_missing_current_decision": (
        "legacy_decision",
        "no_current_decision",
    ),
    "manual_review": (
        "identity_mismatch",
        "kickoff_conflict",
        "source_conflict",
        "duplicate_event_conflict",
    ),
    "reconstructed": ("reconstructed_eligible",),
    "market_baseline_only": (
        "quote_time_unverifiable",
        "insufficient_bookmakers",
        "no_complete_main_market",
        "aggregate_only",
    ),
    "missing": (
        "source_unavailable",
        "source_access_blocked",
        "source_unapproved",
        "kickoff_unverifiable",
        "no_market_record",
        "post_kickoff_only",
    ),
}
HISTORICAL_STATUS_PRIORITY = {
    "manual_review": 3,
    "reconstructed": 4,
    "market_baseline_only": 5,
    "missing": 6,
}


@dataclass(frozen=True)
class HistoricalCoverageEvidence:
    status: Literal[
        "reconstructed", "market_baseline_only", "manual_review", "missing"
    ]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class CoverageClassification:
    provenance_class: ProvenanceClass
    coverage_status: CoverageStatus
    reason_code: str
    reason_codes: tuple[str, ...]


def stable_match_id(result: ClubResult) -> str:
    return (
        f"{result.competition_id}:{result.date}:"
        f"{result.home_canonical}:{result.away_canonical}"
    )


def _current_decision(decision: Any) -> bool:
    return (
        isinstance(decision, dict)
        and decision.get("schema_version") == 2
        and decision.get("policy_version") == "match_pick_v3"
        and decision.get("label") in {"MATCH_PICK", "NO_CLEAN_MARKET"}
    )


def _classification(status: CoverageStatus, reason_codes: tuple[str, ...]) -> CoverageClassification:
    allowed = ALLOWED_REASON_CODES[status]
    unique = set(reason_codes)
    for reason in unique:
        if reason not in allowed:
            raise ValueError(f"reason_not_allowed:{status}:{reason}")
    ordered = tuple(reason for reason in allowed if reason in unique)
    if not ordered:
        raise ValueError(f"missing_reason:{status}")
    return CoverageClassification(
        provenance_class=STATUS_PROVENANCE[status],
        coverage_status=status,
        reason_code=ordered[0],
        reason_codes=ordered,
    )


def classify_coverage(
    *,
    observed: ClosingMatch | None,
    historical: HistoricalCoverageEvidence | tuple[HistoricalCoverageEvidence, ...] | None = None,
) -> CoverageClassification:
    if observed is not None:
        decision = observed.entry.get("match_decision")
        if _current_decision(decision):
            return _classification(
                "observed_current_decision", ("observed_closing",)
            )
        reason = "legacy_decision" if isinstance(decision, dict) else "no_current_decision"
        return _classification("observed_missing_current_decision", (reason,))
    if historical is None:
        return _classification("missing", ("no_market_record",))
    candidates = (historical,) if isinstance(historical, HistoricalCoverageEvidence) else historical
    if not candidates:
        return _classification("missing", ("no_market_record",))
    classified = [
        _classification(item.status, item.reason_codes)
        for item in candidates
    ]
    return min(
        classified,
        key=lambda item: HISTORICAL_STATUS_PRIORITY[item.coverage_status],
    )


def classification_dict(value: CoverageClassification) -> dict[str, Any]:
    return {
        "provenance_class": value.provenance_class,
        "coverage_status": value.coverage_status,
        "reason_code": value.reason_code,
        "reason_codes": list(value.reason_codes),
    }
```

- [ ] **Step 4: Run the full suite and verify the state tests pass**

Run the repository test command. Expected: all existing tests and the five new tests PASS.

- [ ] **Step 5: Commit the state contract**

```bash
git add worldcup/csl_closing_coverage.py tests/test_csl_closing_coverage.py
git commit -m "feat: define CSL closing coverage contract"
```

---

### Task 2: Resolve dual-source kickoff and freeze the initial 128 IDs

**Files:**
- Modify: `worldcup/csl_closing_coverage.py`
- Modify: `tests/test_csl_closing_coverage.py`

**Interfaces:**
- Consumes: `stable_match_id()`, parsed rows returned by `parse_cfl_official_fixture_rows()` and `parse_sevenm_fixture_rows()`, and exact observed snapshot identity.
- Produces: `FixtureResolution`, `resolve_fixture(result: ClubResult, official_rows: list[dict[str, str]], sevenm_rows: list[dict[str, str]]) -> FixtureResolution`, `select_observed_closing_exact(...) -> ClosingMatch | None`, `build_initial_missing_manifest(...) -> dict[str, Any]`, `manifest_match_ids(manifest: dict[str, Any]) -> frozenset[str]`, `initial_match_ids_sha256(ids: Collection[str]) -> str`, `validate_initial_manifest(...) -> frozenset[str]`, `initial_manifest_fingerprint(manifest: dict[str, Any]) -> str`.

- [ ] **Step 1: Add failing tests for exact kickoff, conflicts, and fixed membership**

Append the following tests and helper to `tests/test_csl_closing_coverage.py`:

```python
from worldcup.csl_closing_coverage import (
    build_initial_missing_manifest,
    initial_match_ids_sha256,
    manifest_match_ids,
    resolve_fixture,
    select_observed_closing_exact,
    validate_initial_manifest,
)


def _fixture_row(source_id: str, kickoff: str = "2026-03-06T11:35:00+00:00") -> dict[str, str]:
    return {
        "season": "2026",
        "round": "1",
        "kickoff_at_utc": kickoff,
        "home_team": "成都蓉城",
        "away_team": "深圳新鹏城",
        "home_canonical": "chengdu_rongcheng",
        "away_canonical": "shenzhen_peng_city",
        "status": "PLAYED",
        "source_match_id": f"{source_id}-1",
        "source_url": f"https://example.test/{source_id}",
    }


def test_fixture_resolution_requires_exact_dual_source_kickoff():
    accepted = resolve_fixture(
        _result(), [_fixture_row("official")], [_fixture_row("sevenm")]
    )
    assert accepted.kickoff_at_utc == "2026-03-06T11:35:00+00:00"
    assert accepted.reason_codes == ()
    assert accepted.source_match_ids == {
        "cfl_official": "official-1",
        "sevenm": "sevenm-1",
    }

    conflict = resolve_fixture(
        _result(),
        [_fixture_row("official")],
        [_fixture_row("sevenm", "2026-03-06T12:35:00+00:00")],
    )
    assert conflict.kickoff_at_utc is None
    assert conflict.reason_codes == ("kickoff_conflict",)


def test_initial_manifest_freezes_only_pre_cutoff_missing_ids():
    manifest = build_initial_missing_manifest(
        results=[_result(), _result("2026-06-29")],
        snapshots=[],
        official_rows=[
            _fixture_row("official"),
            {
                **_fixture_row("official-629", "2026-06-29T11:35:00+00:00"),
                "source_match_id": "official-629",
            },
        ],
        sevenm_rows=[
            _fixture_row("sevenm"),
            {
                **_fixture_row("sevenm-629", "2026-06-29T11:35:00+00:00"),
                "source_match_id": "sevenm-629",
            },
        ],
        created_at="2026-08-13T02:00:00+00:00",
        expected_count=1,
    )
    assert manifest["observed_cutoff"] == "2026-06-29"
    assert manifest["expected_match_count"] == 1
    assert manifest_match_ids(manifest) == frozenset({stable_match_id(_result())})
    assert manifest["matches"][0]["kickoff_at_utc"] == "2026-03-06T11:35:00+00:00"
    assert manifest["matches"][0]["coverage_status"] == "missing"
    assert manifest["matches"][0]["reason_code"] == "source_unapproved"


def test_initial_manifest_fails_closed_on_count_or_fixture_conflict():
    for expected_count, official, sevenm, message in (
        (2, [_fixture_row("official")], [_fixture_row("sevenm")], "initial_gap_count_mismatch:1:2"),
        (1, [_fixture_row("official")], [_fixture_row("sevenm", "2026-03-06T12:35:00+00:00")], "initial_fixture_unverified"),
    ):
        try:
            build_initial_missing_manifest(
                results=[_result()],
                snapshots=[],
                official_rows=official,
                sevenm_rows=sevenm,
                created_at="2026-08-13T02:00:00+00:00",
                expected_count=expected_count,
            )
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("expected initial manifest guard to fail")


def test_initial_manifest_validator_rejects_self_consistent_membership_tampering():
    manifest = build_initial_missing_manifest(
        results=[_result()],
        snapshots=[],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        created_at="2026-08-13T02:00:00+00:00",
        expected_count=1,
    )
    expected_hash = initial_match_ids_sha256({stable_match_id(_result())})
    assert validate_initial_manifest(
        manifest,
        results=[_result()],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        expected_count=1,
        expected_ids_sha256=expected_hash,
    ) == frozenset({stable_match_id(_result())})

    tampered = deepcopy(manifest)
    tampered["matches"][0]["match_id"] = (
        "csl_2026:2026-03-06:shandong_taishan:shenzhen_peng_city"
    )
    try:
        validate_initial_manifest(
            tampered,
            results=[_result()],
            official_rows=[_fixture_row("official")],
            sevenm_rows=[_fixture_row("sevenm")],
            expected_count=1,
            expected_ids_sha256=expected_hash,
        )
    except ValueError as exc:
        assert str(exc) == "initial_manifest_membership_hash_mismatch"
    else:
        raise AssertionError("expected fixed membership tamper to fail")


def test_exact_observed_selector_rejects_wrong_kickoff_and_postponed():
    valid = {
        "snapshot_at": "2026-03-06T11:00:00+00:00",
        "run": {"run_id": "observed-run"},
        "competition": {"id": "csl_2026"},
        "matches": [
            {
                "competition": {"id": "csl_2026"},
                "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
                "home_canonical": "chengdu_rongcheng",
                "away_canonical": "shenzhen_peng_city",
                "match_decision": {
                    "schema_version": 2,
                    "policy_version": "match_pick_v3",
                    "label": "MATCH_PICK",
                },
            }
        ],
    }
    wrong_kickoff = deepcopy(valid)
    wrong_kickoff["matches"][0]["kickoff_at_utc"] = "2026-03-06T12:35:00+00:00"
    postponed = deepcopy(valid)
    postponed["matches"][0]["fixture_status"] = "POSTPONED"
    kwargs = {
        "competition_id": "csl_2026",
        "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
        "home_canonical": "chengdu_rongcheng",
        "away_canonical": "shenzhen_peng_city",
    }
    assert select_observed_closing_exact([valid], **kwargs) is not None
    assert select_observed_closing_exact([wrong_kickoff], **kwargs) is None
    assert select_observed_closing_exact([postponed], **kwargs) is None
```

Add `from copy import deepcopy` to the test imports.

When constructing the June 29 rows in the implementation, match by `(date, home_canonical, away_canonical)`, not only by team identity. The helper rows above intentionally retain the same teams to prove the date participates in identity.

- [ ] **Step 2: Run the suite and verify the new symbols are missing**

Run the full test command. Expected: FAIL importing `build_initial_missing_manifest`.

- [ ] **Step 3: Implement fixture resolution and manifest construction**

Append these types and functions to `worldcup/csl_closing_coverage.py`; also add `from datetime import datetime, timezone` to its imports:

```python
INITIAL_OBSERVED_CUTOFF = "2026-06-29"
INITIAL_EXPECTED_GAPS = 128
INITIAL_MATCH_IDS_SHA256 = "530acaa872d753c911861e2cab1e1bf6a2a0a87c595028d9c5e369523a7f6a40"


@dataclass(frozen=True)
class FixtureResolution:
    kickoff_at_utc: str | None
    source_match_ids: dict[str, str]
    reason_codes: tuple[str, ...]


def _utc_iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timezone_required:{value}")
    return parsed.astimezone(timezone.utc).isoformat()


def _fixture_candidates(
    result: ClubResult, rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("season") == result.season
        and str(row.get("kickoff_at_utc") or "")[:10] == result.date
        and row.get("home_canonical") == result.home_canonical
        and row.get("away_canonical") == result.away_canonical
    ]


def resolve_fixture(
    result: ClubResult,
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
) -> FixtureResolution:
    official = _fixture_candidates(result, official_rows)
    sevenm = _fixture_candidates(result, sevenm_rows)
    if len(official) > 1 or len(sevenm) > 1:
        return FixtureResolution(None, {}, ("duplicate_event_conflict",))
    if len(official) != 1 or len(sevenm) != 1:
        return FixtureResolution(None, {}, ("identity_mismatch",))
    official_kickoff = _utc_iso(official[0]["kickoff_at_utc"])
    sevenm_kickoff = _utc_iso(sevenm[0]["kickoff_at_utc"])
    if official_kickoff != sevenm_kickoff:
        return FixtureResolution(None, {}, ("kickoff_conflict",))
    return FixtureResolution(
        kickoff_at_utc=official_kickoff,
        source_match_ids={
            "cfl_official": str(official[0].get("source_match_id") or ""),
            "sevenm": str(sevenm[0].get("source_match_id") or ""),
        },
        reason_codes=(),
    )


def select_observed_closing_exact(
    snapshots: list[dict[str, Any]],
    *,
    competition_id: str,
    kickoff_at_utc: str,
    home_canonical: str,
    away_canonical: str,
) -> ClosingMatch | None:
    kickoff = datetime.fromisoformat(kickoff_at_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    selected: ClosingMatch | None = None
    selected_at: datetime | None = None
    for snapshot in snapshots:
        snapshot_at_raw = snapshot.get("snapshot_at")
        if not snapshot_at_raw:
            continue
        snapshot_at = datetime.fromisoformat(str(snapshot_at_raw).replace("Z", "+00:00"))
        if snapshot_at.tzinfo is None:
            continue
        snapshot_at = snapshot_at.astimezone(timezone.utc)
        if snapshot_at >= kickoff:
            continue
        for entry in snapshot.get("matches") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("fixture_status") or "").upper() == "POSTPONED":
                continue
            entry_competition = (
                ((entry.get("competition") or {}).get("id"))
                or ((snapshot.get("competition") or {}).get("id"))
            )
            if entry_competition != competition_id:
                continue
            try:
                entry_kickoff = _utc_iso(str(entry.get("kickoff_at_utc") or ""))
            except (TypeError, ValueError):
                continue
            if entry_kickoff != _utc_iso(kickoff_at_utc):
                continue
            if entry.get("home_canonical") != home_canonical or entry.get("away_canonical") != away_canonical:
                continue
            if selected_at is None or snapshot_at > selected_at:
                run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
                selected = ClosingMatch(
                    entry=entry,
                    snapshot_at=str(snapshot_at_raw),
                    snapshot_run_id=str(run.get("run_id")) if run.get("run_id") else None,
                )
                selected_at = snapshot_at
    return selected


def build_initial_missing_manifest(
    *,
    results: list[ClubResult],
    snapshots: list[dict[str, Any]],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    created_at: str,
    competition_id: str = "csl_2026",
    season: str = "2026",
    observed_cutoff: str = INITIAL_OBSERVED_CUTOFF,
    expected_count: int = INITIAL_EXPECTED_GAPS,
) -> dict[str, Any]:
    candidates = []
    for result in sorted(results, key=stable_match_id):
        if result.competition_id != competition_id or result.season != season:
            continue
        if result.date >= observed_cutoff:
            continue
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        if fixture.kickoff_at_utc is None:
            raise ValueError(
                f"initial_fixture_unverified:{stable_match_id(result)}:"
                f"{','.join(fixture.reason_codes)}"
            )
        observed = select_observed_closing_exact(
            snapshots,
            competition_id=competition_id,
            kickoff_at_utc=fixture.kickoff_at_utc,
            home_canonical=result.home_canonical,
            away_canonical=result.away_canonical,
        )
        if observed is not None:
            continue
        candidates.append(
            {
                "match_id": stable_match_id(result),
                "competition_id": competition_id,
                "season": season,
                "match_date": result.date,
                "kickoff_at_utc": fixture.kickoff_at_utc,
                "home_team": result.home_team,
                "away_team": result.away_team,
                "home_canonical": result.home_canonical,
                "away_canonical": result.away_canonical,
                "source_match_ids": fixture.source_match_ids,
                "provenance_class": "none",
                "coverage_status": "missing",
                "reason_code": "source_unapproved",
                "reason_codes": ["source_unapproved"],
                "probe_status": "awaiting_source_approval",
                "approved_source_ids": [],
                "expected_request_scope": "single_match_page_only",
            }
        )
    if len(candidates) != expected_count:
        raise ValueError(
            f"initial_gap_count_mismatch:{len(candidates)}:{expected_count}"
        )
    return {
        "schema_version": 1,
        "competition_id": competition_id,
        "season": season,
        "created_at": _utc_iso(created_at),
        "observed_cutoff": observed_cutoff,
        "expected_match_count": expected_count,
        "membership_policy": "fixed_match_ids_v1",
        "matches": candidates,
    }


def manifest_match_ids(manifest: dict[str, Any]) -> frozenset[str]:
    rows = manifest.get("matches")
    if not isinstance(rows, list):
        raise ValueError("invalid_initial_manifest_matches")
    ids = [str(row.get("match_id") or "") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_initial_manifest_identity")
    expected = manifest.get("expected_match_count")
    if expected != len(ids):
        raise ValueError(f"initial_manifest_count_mismatch:{len(ids)}:{expected}")
    return frozenset(ids)


def initial_match_ids_sha256(ids: Collection[str]) -> str:
    encoded = json.dumps(
        sorted(ids),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_initial_manifest(
    manifest: dict[str, Any],
    *,
    results: list[ClubResult],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    expected_count: int = INITIAL_EXPECTED_GAPS,
    expected_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
) -> frozenset[str]:
    expected_metadata = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "observed_cutoff": INITIAL_OBSERVED_CUTOFF,
        "expected_match_count": expected_count,
        "membership_policy": "fixed_match_ids_v1",
    }
    for key, expected in expected_metadata.items():
        if manifest.get(key) != expected:
            raise ValueError(f"initial_manifest_metadata_mismatch:{key}")
    ids = manifest_match_ids(manifest)
    if len(ids) != expected_count:
        raise ValueError(f"initial_manifest_count_mismatch:{len(ids)}:{expected_count}")
    if initial_match_ids_sha256(ids) != expected_ids_sha256:
        raise ValueError("initial_manifest_membership_hash_mismatch")
    by_id = {stable_match_id(result): result for result in results}
    for row in manifest["matches"]:
        match_id = str(row["match_id"])
        result = by_id.get(match_id)
        if result is None:
            raise ValueError(f"initial_manifest_result_missing:{match_id}")
        expected_identity = {
            "competition_id": result.competition_id,
            "season": result.season,
            "match_date": result.date,
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_canonical": result.home_canonical,
            "away_canonical": result.away_canonical,
            "provenance_class": "none",
            "coverage_status": "missing",
            "reason_code": "source_unapproved",
            "reason_codes": ["source_unapproved"],
            "probe_status": "awaiting_source_approval",
            "approved_source_ids": [],
            "expected_request_scope": "single_match_page_only",
        }
        for key, expected in expected_identity.items():
            if row.get(key) != expected:
                raise ValueError(f"initial_manifest_row_mismatch:{match_id}:{key}")
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        if fixture.kickoff_at_utc is None:
            raise ValueError(f"initial_manifest_fixture_unverified:{match_id}")
        if row.get("kickoff_at_utc") != fixture.kickoff_at_utc:
            raise ValueError(f"initial_manifest_row_mismatch:{match_id}:kickoff_at_utc")
        if row.get("source_match_ids") != fixture.source_match_ids:
            raise ValueError(f"initial_manifest_row_mismatch:{match_id}:source_match_ids")
    return ids


def initial_manifest_fingerprint(manifest: dict[str, Any]) -> str:
    payload = {
        "schema_version": manifest.get("schema_version"),
        "competition_id": manifest.get("competition_id"),
        "season": manifest.get("season"),
        "observed_cutoff": manifest.get("observed_cutoff"),
        "expected_match_count": manifest.get("expected_match_count"),
        "membership_policy": manifest.get("membership_policy"),
        "matches": sorted(
            manifest.get("matches") or [],
            key=lambda row: str(row.get("match_id") or ""),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

Move the `hashlib` and `json` imports otherwise introduced in Task 3 into Task 2 because the manifest hash functions require them; import `Collection` from `collections.abc`. Task 3 reuses those imports and does not add duplicates. The production ID digest above is a bootstrap invariant computed from the independently reviewed local 128-ID baseline, not from dates at runtime.

- [ ] **Step 4: Run the full suite and verify all manifest tests pass**

Run the repository test command. Expected: PASS.

- [ ] **Step 5: Commit the dual-source manifest domain**

```bash
git add worldcup/csl_closing_coverage.py tests/test_csl_closing_coverage.py
git commit -m "feat: build fixed CSL closing gap manifest"
```

---

### Task 3: Build full observed-only reconciliation and separated performance

**Files:**
- Modify: `worldcup/csl_closing_coverage.py`
- Modify: `tests/test_csl_closing_coverage.py`

**Interfaces:**
- Consumes: `resolve_fixture()`, `classify_coverage()`, `manifest_match_ids()`, `settle_match_decision()`, `summarize_decision_records()`.
- Produces: `select_observed_closing_exact(...) -> ClosingMatch | None`, `normalize_audit_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]`, `build_coverage_report(...) -> dict[str, Any]`, `coverage_input_fingerprint(report: dict[str, Any]) -> str`, `closing_archive_candidates(...) -> list[dict[str, Any]]`.

- [ ] **Step 1: Add failing reconciliation and candidate tests**

Append compact snapshot helpers and the following assertions to `tests/test_csl_closing_coverage.py`:

```python
from worldcup.csl_closing_coverage import (
    build_coverage_report,
    closing_archive_candidates,
    coverage_input_fingerprint,
)


def _snapshot(
    *,
    snapshot_at: str,
    kickoff: str = "2026-03-06T11:35:00+00:00",
    decision: object = None,
) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "run": {"run_id": f"run-{snapshot_at}"},
        "competition": {"id": "csl_2026"},
        "matches": [
            {
                "kickoff_at_utc": kickoff,
                "home_team": "成都蓉城",
                "away_team": "深圳新鹏城",
                "home_canonical": "chengdu_rongcheng",
                "away_canonical": "shenzhen_peng_city",
                "competition": {"id": "csl_2026"},
                "match_decision": decision,
            }
        ],
    }


def _current_pick() -> dict:
    return {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "market": "1X2",
        "selection": "home",
        "odds": 1.80,
    }


def test_full_reconciliation_is_exact_mutually_exclusive_and_observed_only():
    result = _result()
    report = build_coverage_report(
        snapshots=[
            _snapshot(snapshot_at="2026-03-06T11:00:00+00:00", decision=_current_pick()),
            _snapshot(snapshot_at="2026-03-06T11:40:00+00:00", decision={"label": "S"}),
        ],
        results=[result],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        initial_manifest={
            "expected_match_count": 0,
            "matches": [],
        },
        generated_at="2026-08-13T02:00:00+00:00",
    )
    assert report["summary"] == {
        "finished_result_count": 1,
        "observed_closing_count": 1,
        "observed_current_decision_count": 1,
        "observed_missing_current_decision_count": 0,
        "reconstructed_count": 0,
        "market_baseline_only_count": 0,
        "manual_review_count": 0,
        "missing_count": 0,
    }
    assert report["matches"][0]["coverage_status"] == "observed_current_decision"
    assert report["matches"][0]["closing_snapshot_at"] == "2026-03-06T11:00:00+00:00"
    assert report["performance"]["observed"]["decision_tally"] == {
        "hit": 1,
        "miss": 0,
        "push": 0,
        "no_pick": 0,
    }
    assert report["performance"]["reconstructed"] == {
        "status": "not_implemented",
        "combined_with_observed": False,
    }
    assert "combined" not in report["performance"]


def test_observed_no_clean_market_is_covered_but_excluded_from_official_performance():
    decision = {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "NO_CLEAN_MARKET",
    }
    report = build_coverage_report(
        snapshots=[_snapshot(snapshot_at="2026-03-06T11:00:00+00:00", decision=decision)],
        results=[_result()],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        initial_manifest={"expected_match_count": 0, "matches": []},
        generated_at="2026-08-13T02:00:00+00:00",
    )
    assert report["matches"][0]["coverage_status"] == "observed_current_decision"
    assert report["performance"]["observed"]["decision_tally"] == {
        "hit": 0,
        "miss": 0,
        "push": 0,
        "no_pick": 0,
    }
    assert report["performance"]["observed"]["decision_sample"]["decision_count"] == 0


def test_fixture_conflict_blocks_snapshot_from_becoming_observed():
    report = build_coverage_report(
        snapshots=[_snapshot(snapshot_at="2026-03-06T11:00:00+00:00", decision=_current_pick())],
        results=[_result()],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm", "2026-03-06T12:35:00+00:00")],
        initial_manifest={"expected_match_count": 0, "matches": []},
        generated_at="2026-08-13T02:00:00+00:00",
    )
    assert report["matches"][0]["coverage_status"] == "manual_review"
    assert report["matches"][0]["reason_code"] == "kickoff_conflict"
    assert report["summary"]["observed_closing_count"] == 0


def test_initial_gap_membership_uses_id_and_future_gap_gets_operational_issue():
    manifest = build_initial_missing_manifest(
        results=[_result()],
        snapshots=[],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        created_at="2026-08-13T02:00:00+00:00",
        expected_count=1,
    )
    report = build_coverage_report(
        snapshots=[],
        results=[_result(), _result("2026-06-29")],
        official_rows=[
            _fixture_row("official"),
            {**_fixture_row("official", "2026-06-29T11:35:00+00:00"), "source_match_id": "official-629"},
        ],
        sevenm_rows=[
            _fixture_row("sevenm"),
            {**_fixture_row("sevenm", "2026-06-29T11:35:00+00:00"), "source_match_id": "sevenm-629"},
        ],
        initial_manifest=manifest,
        generated_at="2026-08-13T02:00:00+00:00",
        audit_events=[
            {
                "observed_at": "2026-06-29T10:30:00+00:00",
                "match_id": "provider-event-629",
                "kickoff_at_utc": "2026-06-29T11:35:00+00:00",
                "home_canonical": "chengdu_rongcheng",
                "away_canonical": "shenzhen_peng_city",
                "issue_code": "provider_refresh_failed",
            }
        ],
    )
    by_id = {row["match_id"]: row for row in report["matches"]}
    assert by_id[stable_match_id(_result())]["reason_code"] == "source_unapproved"
    future = by_id[stable_match_id(_result("2026-06-29"))]
    assert future["reason_code"] == "no_market_record"
    assert future["audit_issue_codes"] == [
        "closing_archive_missing",
        "provider_refresh_failed",
    ]
    assert report["operational_event_counts"] == {"provider_refresh_failed": 1}


def test_coverage_fingerprint_ignores_generation_time_and_sort_order():
    base = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": "first",
        "matches": [{"match_id": "b"}, {"match_id": "a"}],
    }
    changed_time = {**base, "generated_at": "second", "matches": list(reversed(base["matches"]))}
    changed_evidence = {
        **base,
        "matches": [{"match_id": "b"}, {"match_id": "a", "reason_code": "source_unapproved"}],
    }
    assert coverage_input_fingerprint(base) == coverage_input_fingerprint(changed_time)
    assert coverage_input_fingerprint(base) != coverage_input_fingerprint(changed_evidence)


def test_closing_archive_candidates_only_annotate_already_due_matches():
    current = _snapshot(
        snapshot_at="2026-03-06T10:00:00+00:00",
        decision=_current_pick(),
    )
    due_id = "event-1"
    current["matches"][0]["source_event_id"] = due_id
    missing = closing_archive_candidates(
        snapshot=current,
        archived_snapshots=[],
        due_match_ids={due_id},
    )
    not_due = closing_archive_candidates(
        snapshot=current,
        archived_snapshots=[],
        due_match_ids=set(),
    )
    present = closing_archive_candidates(
        snapshot=current,
        archived_snapshots=[current],
        due_match_ids={due_id},
    )
    assert [row["match_id"] for row in missing] == [due_id]
    assert not_due == []
    assert present == []
```

- [ ] **Step 2: Run the suite and verify the reconciliation symbols are missing**

Run the full test command. Expected: FAIL importing `build_coverage_report`.

- [ ] **Step 3: Implement exact observed selection, report rows, and fingerprint**

Add `hashlib`, `json`, `collections.Counter`, and the settlement imports. Implement these exact rules:

```python
from collections import Counter
import hashlib
import json

from worldcup.decision_settlement import (
    settle_match_decision,
    summarize_decision_records,
)


def _status_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row["coverage_status"]) for row in rows)
    return {
        "finished_result_count": len(rows),
        "observed_closing_count": counts["observed_current_decision"] + counts["observed_missing_current_decision"],
        "observed_current_decision_count": counts["observed_current_decision"],
        "observed_missing_current_decision_count": counts["observed_missing_current_decision"],
        "reconstructed_count": counts["reconstructed"],
        "market_baseline_only_count": counts["market_baseline_only"],
        "manual_review_count": counts["manual_review"],
        "missing_count": counts["missing"],
    }


AUDIT_ISSUE_ORDER = (
    "closing_archive_missing",
    "quota_blocked",
    "provider_refresh_failed",
    "snapshot_archive_failed",
    "archive_validation_failed",
)


def normalize_audit_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for event in events:
        issue = str(event.get("issue_code") or "")
        if issue not in AUDIT_ISSUE_ORDER:
            raise ValueError(f"invalid_audit_issue_code:{issue}")
        kickoff = _utc_iso(str(event.get("kickoff_at_utc") or ""))
        home = str(event.get("home_canonical") or "").strip()
        away = str(event.get("away_canonical") or "").strip()
        observed_at = _utc_iso(str(event.get("observed_at") or ""))
        if not home or not away:
            raise ValueError("invalid_audit_event_identity")
        key = (kickoff, home, away, issue)
        candidate = {
            "observed_at": observed_at,
            "match_id": str(event.get("match_id") or ""),
            "kickoff_at_utc": kickoff,
            "home_canonical": home,
            "away_canonical": away,
            "issue_code": issue,
        }
        existing = normalized.get(key)
        if existing is None or candidate["observed_at"] < existing["observed_at"]:
            normalized[key] = candidate
    return sorted(
        normalized.values(),
        key=lambda row: (
            row["kickoff_at_utc"],
            row["home_canonical"],
            row["away_canonical"],
            AUDIT_ISSUE_ORDER.index(row["issue_code"]),
        ),
    )


def build_coverage_report(
    *,
    snapshots: list[dict[str, Any]],
    results: list[ClubResult],
    official_rows: list[dict[str, str]],
    sevenm_rows: list[dict[str, str]],
    initial_manifest: dict[str, Any],
    generated_at: str,
    audit_events: list[dict[str, Any]] | None = None,
    competition_id: str = "csl_2026",
    season: str = "2026",
    min_sample: int = 50,
) -> dict[str, Any]:
    initial_ids = manifest_match_ids(initial_manifest)
    operational_events = normalize_audit_events(audit_events or [])
    rows: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    for result in sorted(results, key=stable_match_id):
        if result.competition_id != competition_id or result.season != season:
            continue
        fixture = resolve_fixture(result, official_rows, sevenm_rows)
        observed = (
            select_observed_closing_exact(
                snapshots,
                competition_id=competition_id,
                kickoff_at_utc=fixture.kickoff_at_utc,
                home_canonical=result.home_canonical,
                away_canonical=result.away_canonical,
            )
            if fixture.kickoff_at_utc is not None
            else None
        )
        if observed is not None:
            historical = None
        elif fixture.reason_codes:
            historical = HistoricalCoverageEvidence(
                status="manual_review",
                reason_codes=fixture.reason_codes,
            )
        elif stable_match_id(result) in initial_ids:
            historical = HistoricalCoverageEvidence(
                status="missing",
                reason_codes=("source_unapproved",),
            )
        else:
            historical = HistoricalCoverageEvidence(
                status="missing",
                reason_codes=("no_market_record",),
            )
        classification = classify_coverage(observed=observed, historical=historical)
        match_kickoff_raw = (
            observed.entry.get("kickoff_at_utc")
            if observed is not None
            else fixture.kickoff_at_utc
        )
        match_kickoff = _utc_iso(str(match_kickoff_raw)) if match_kickoff_raw else None
        event_codes = {
            event["issue_code"]
            for event in operational_events
            if event["kickoff_at_utc"] == match_kickoff
            and event["home_canonical"] == result.home_canonical
            and event["away_canonical"] == result.away_canonical
        }
        audit_issues = set(event_codes) if observed is None else set()
        if observed is None and stable_match_id(result) not in initial_ids:
            audit_issues.add("closing_archive_missing")
        row = {
            "match_id": stable_match_id(result),
            "competition_id": competition_id,
            "season": season,
            "match_date": result.date,
            "kickoff_at_utc": (
                observed.entry.get("kickoff_at_utc")
                if observed is not None
                else fixture.kickoff_at_utc
            ),
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_canonical": result.home_canonical,
            "away_canonical": result.away_canonical,
            **classification_dict(classification),
            "closing_snapshot_at": observed.snapshot_at if observed else None,
            "closing_snapshot_run_id": observed.snapshot_run_id if observed else None,
            "audit_issue_codes": [
                code for code in AUDIT_ISSUE_ORDER if code in audit_issues
            ],
            "operational_history_codes": [
                code for code in AUDIT_ISSUE_ORDER if code in event_codes
            ],
        }
        result_payload = {
            "home_score": result.home_score,
            "away_score": result.away_score,
        }
        row["settlement"] = (
            settle_match_decision(observed.entry.get("match_decision"), result_payload)
            if observed is not None
            else None
        )
        rows.append(row)
        if (
            observed is not None
            and classification.coverage_status == "observed_current_decision"
            and (observed.entry.get("match_decision") or {}).get("label") == "MATCH_PICK"
        ):
            decision = observed.entry.get("match_decision")
            settled.append(
                {
                    "closing_match_decision": decision,
                    "result": result_payload,
                }
            )
    canonical = summarize_decision_records(settled, min_sample=min_sample)
    report = {
        "schema_version": 1,
        "competition_id": competition_id,
        "season": season,
        "generated_at": _utc_iso(generated_at),
        "membership": {
            "initial_missing_count": len(initial_ids),
            "initial_missing_match_ids": sorted(initial_ids),
            "observed_cutoff": initial_manifest.get("observed_cutoff"),
        },
        "summary": _status_summary(rows),
        "reason_counts": dict(sorted(Counter(row["reason_code"] for row in rows).items())),
        "month_counts": dict(sorted(Counter(row["match_date"][:7] for row in rows).items())),
        "reason_by_month": {
            month: dict(
                sorted(
                    Counter(
                        row["reason_code"]
                        for row in rows
                        if row["match_date"][:7] == month
                    ).items()
                )
            )
            for month in sorted({row["match_date"][:7] for row in rows})
        },
        "audit_issue_counts": dict(
            sorted(Counter(code for row in rows for code in row["audit_issue_codes"]).items())
        ),
        "operational_events": operational_events,
        "operational_event_counts": dict(
            sorted(Counter(event["issue_code"] for event in operational_events).items())
        ),
        "performance": {
            "observed": {
                "decision_tally": canonical["decision_tally"],
                "decision_sample": canonical["sample"],
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            },
            "reconstructed": {
                "status": "not_implemented",
                "combined_with_observed": False,
            },
        },
        "matches": rows,
        "research_notice": "仅用于研究分析，不构成投注建议。",
    }
    report["input_fingerprint"] = coverage_input_fingerprint(report)
    return report


def coverage_input_fingerprint(report: dict[str, Any]) -> str:
    payload = {
        "schema_version": report.get("schema_version"),
        "competition_id": report.get("competition_id"),
        "season": report.get("season"),
        "membership": report.get("membership"),
        "operational_events": report.get("operational_events") or [],
        "matches": sorted(
            report.get("matches") or [],
            key=lambda row: str(row.get("match_id") or ""),
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_match_id(match: dict[str, Any]) -> str:
    explicit = str(match.get("source_event_id") or match.get("match_id") or "").strip()
    return explicit or "|".join(
        str(match.get(key) or "").strip()
        for key in ("kickoff_at_utc", "home_canonical", "away_canonical")
    )


def closing_archive_candidates(
    *,
    snapshot: dict[str, Any],
    archived_snapshots: list[dict[str, Any]],
    due_match_ids: set[str],
) -> list[dict[str, Any]]:
    candidates = []
    for match in snapshot.get("matches") or []:
        if not isinstance(match, dict) or _snapshot_match_id(match) not in due_match_ids:
            continue
        kickoff = str(match.get("kickoff_at_utc") or "")
        home = str(match.get("home_canonical") or "")
        away = str(match.get("away_canonical") or "")
        competition = str(
            ((match.get("competition") or {}).get("id"))
            or ((snapshot.get("competition") or {}).get("id"))
            or ""
        )
        if not kickoff or not home or not away or not competition:
            candidates.append(
                {
                    "match_id": _snapshot_match_id(match),
                    "kickoff_at_utc": kickoff or None,
                    "home_canonical": home or None,
                    "away_canonical": away or None,
                    "issue_code": "closing_identity_incomplete",
                }
            )
            continue
        observed = select_observed_closing_exact(
            archived_snapshots,
            competition_id=competition,
            kickoff_at_utc=kickoff,
            home_canonical=home,
            away_canonical=away,
        )
        if observed is None:
            candidates.append(
                {
                    "match_id": _snapshot_match_id(match),
                    "kickoff_at_utc": _utc_iso(kickoff),
                    "home_canonical": home,
                    "away_canonical": away,
                    "issue_code": "closing_archive_missing",
                }
            )
    return sorted(candidates, key=lambda row: (str(row["kickoff_at_utc"]), row["match_id"]))
```

- [ ] **Step 4: Run the full suite and verify reconciliation passes**

Run the repository test command. Expected: PASS, including the late snapshot exclusion and observed/reconstructed separation assertions.

- [ ] **Step 5: Commit the pure reconciliation layer**

```bash
git add worldcup/csl_closing_coverage.py tests/test_csl_closing_coverage.py
git commit -m "feat: reconcile CSL closing coverage"
```

---

### Task 4: Add the default-dry-run local runner and pending recovery

**Files:**
- Create: `worldcup/csl_closing_coverage_runner.py`
- Create: `tests/test_csl_closing_coverage_runner.py`

**Interfaces:**
- Consumes: `build_initial_missing_manifest()`, `build_coverage_report()`, local result CSV, local history JSON, and the two existing saved result-source samples.
- Produces: `run_initial_manifest(...) -> dict[str, Any]`, `run_closing_coverage(...) -> dict[str, Any]`, `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write failing runner tests for zero-write, freeze, pending, and redaction**

Create `tests/test_csl_closing_coverage_runner.py` with these deterministic local fixtures:

```python
from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.csl_closing_coverage_runner import (
    run_closing_coverage,
    run_initial_manifest,
)

ONE_ID_SHA256 = "1de1a3be233ae01a142505365f039d75b4c874a3f0eb78de0aa87b3d8d8efd00"


def _run_test_coverage(*, root: Path, **kwargs):
    return run_closing_coverage(
        root=root,
        expected_initial_count=1,
        expected_initial_ids_sha256=ONE_ID_SHA256,
        **kwargs,
    )


def _seed_inputs(root: Path) -> None:
    results = root / "data/cache/club_results_csl_2026.csv"
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "competition_id", "season", "date", "home_team", "away_team",
                "home_score", "away_score", "neutral",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "competition_id": "csl_2026",
                "season": "2026",
                "date": "2026-03-06",
                "home_team": "成都蓉城",
                "away_team": "深圳新鹏城",
                "home_score": "5",
                "away_score": "1",
                "neutral": "0",
            }
        )
    raw = root / "data/cache/csl_results_sources"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "cfl_official_2026.json").write_text(
        json.dumps(
            {
                "data": {
                    "dataList": [
                        {
                            "id": "official-1",
                            "match_status": "Played",
                            "local_date": "2026-03-06",
                            "local_time": "19:35:00",
                            "week": 1,
                            "home_contestant_name": "成都蓉城",
                            "away_contestant_name": "深圳新鹏城",
                            "ft_home_score": 5,
                            "ft_away_score": 1,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (raw / "sevenm_2026_fixture.js").write_text(
        '''
        var Tmp_bh_Arr = [ 7001 ];
        var Run_Arr = [ 1 ];
        var Time_Arr = [ "2026,03,06,19,35,00" ];
        var Scores_Arr = [ "5-1(2-0)" ];
        var TeamA_Arr = [ "成都蓉城" ];
        var TeamB_Arr = [ "深圳新鹏城" ];
        var Stat_Arr = [ 4 ];
        var Memo_Arr = [ "" ];
        ''',
        encoding="utf-8",
    )
```

Add this complete dry-run/freeze test:

```python
def test_manifest_defaults_to_zero_write_then_freezes_content():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        target = root / "data/local/backfill/csl_2026/initial_missing_manifest.json"
        dry = run_initial_manifest(
            root=root,
            write=False,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert dry == {
            "status": "dry_run",
            "write": False,
            "competition_id": "csl_2026",
            "season": "2026",
            "matches": 1,
            "observed_cutoff": "2026-06-29",
        }
        assert not target.exists()
        assert not (root / "data/local/diagnostics/csl_closing_coverage.lock").exists()
        assert not (root / "data/local").exists()

        stored = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        original = target.read_bytes()
        unchanged = run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T03:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        assert stored["status"] == "stored"
        assert unchanged["status"] == "unchanged"
        assert unchanged["matches"] == 1
        assert target.read_bytes() == original
```

Add a first-write membership guard test by calling `run_initial_manifest(write=True, expected_count=1, expected_ids_sha256="0" * 64)` on a freshly seeded root. Assert `status="blocked"`, `reason="coverage_inputs_unavailable"`, `error_type="ValueError"`, and that the target does not exist. This proves the fixed membership digest is validated before both dry-run success and first write.

Add one test that rewrites the replay home team to `山东泰山`, writes matching dual-source fixture rows for that new identity, and verifies the already-stored bytes remain unchanged while the second call returns:

```python
{
    "status": "blocked",
    "reason": "initial_manifest_identity_mismatch",
    "write": True,
    "competition_id": "csl_2026",
    "season": "2026",
}
```

Add this report lifecycle test:

```python
def test_report_pending_lifecycle_is_atomic_and_idempotent():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_inputs(root)
        run_initial_manifest(
            root=root,
            write=True,
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
            expected_ids_sha256=ONE_ID_SHA256,
        )
        report_path = root / "data/local/diagnostics/csl_closing_coverage.json"
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        dry_run_lock = root / "dry-run-coverage.lock"
        dry = _run_test_coverage(
            root=root,
            write=False,
            generated_at="2026-08-13T02:00:00+00:00",
            lock_path=dry_run_lock,
        )
        assert dry["status"] == "dry_run"
        assert not report_path.exists()
        assert not pending_path.exists()
        assert not dry_run_lock.exists()

        stored = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        assert stored["status"] == "stored"
        assert not pending_path.exists()

        event = {
            "observed_at": "2026-03-06T10:30:00+00:00",
            "match_id": "event-1",
            "kickoff_at_utc": "2026-03-06T11:35:00+00:00",
            "home_canonical": "chengdu_rongcheng",
            "away_canonical": "shenzhen_peng_city",
            "issue_code": "quota_blocked",
        }
        updated = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:30:00+00:00",
            audit_events=[event, event],
        )
        same_event = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:45:00+00:00",
            audit_events=[event],
        )
        canonical_payload = json.loads(report_path.read_text(encoding="utf-8"))
        canonical = report_path.read_bytes()
        assert updated["status"] == "stored"
        assert same_event["status"] == "unchanged"
        assert canonical_payload["operational_event_counts"] == {"quota_blocked": 1}
        assert len(canonical_payload["operational_events"]) == 1

        pending_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "input_fingerprint": updated["input_fingerprint"],
                }
            ),
            encoding="utf-8",
        )
        unchanged = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T03:00:00+00:00",
        )
        assert unchanged["status"] == "unchanged"
        assert unchanged["stale_pending_cleared"] is True
        assert report_path.read_bytes() == canonical
        assert not pending_path.exists()
```

Add this failure/redaction test, using the same seeded root and stored initial manifest:

```python
def broken_report_write(_path, _payload):
    raise OSError("private odds secret must not leak")

failed = _run_test_coverage(
    root=root,
    write=True,
    generated_at="2026-08-13T02:00:00+00:00",
    report_write=broken_report_write,
)
pending = json.loads(pending_path.read_text(encoding="utf-8"))
assert failed["status"] == "error"
assert failed["reason"] == "coverage_report_commit_failed"
assert set(pending) == {
    "schema_version", "status", "attempted_at", "input_fingerprint", "reason", "error_type"
}
serialized = json.dumps({"failed": failed, "pending": pending}, ensure_ascii=False)
for forbidden in (
    "private odds secret", "api_key", "Authorization", "Cookie", "bookmaker", "THE_ODDS_API_KEY"
):
    assert forbidden not in serialized
```

Add a missing-input recovery test:

```python
def test_write_mode_persists_pending_before_reconciliation_failure():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        pending_path = root / "data/local/diagnostics/csl_closing_coverage_pending.json"
        result = _run_test_coverage(
            root=root,
            write=True,
            generated_at="2026-08-13T02:00:00+00:00",
        )
        pending = json.loads(pending_path.read_text(encoding="utf-8"))

    assert result["status"] == "blocked"
    assert result["reason"] == "coverage_inputs_unavailable"
    assert pending["reason"] == "coverage_reconciliation_failed"
    assert pending["input_fingerprint"] is None
    assert pending["error_type"] == "FileNotFoundError"
```

- [ ] **Step 2: Run the suite and verify the runner module is missing**

Run the full test command. Expected: FAIL importing `worldcup.csl_closing_coverage_runner`.

- [ ] **Step 3: Implement local loading and atomic JSON**

Create the runner with these constants and atomic primitive:

```python
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_RESULTS = "data/cache/club_results_csl_2026.csv"
DEFAULT_HISTORY = "data/local/diagnostics/csl_history"
DEFAULT_RAW_DIR = "data/cache/csl_results_sources"
DEFAULT_INITIAL_MANIFEST = "data/local/backfill/csl_2026/initial_missing_manifest.json"
DEFAULT_REPORT = "data/local/diagnostics/csl_closing_coverage.json"
DEFAULT_PENDING = "data/local/diagnostics/csl_closing_coverage_pending.json"
DEFAULT_LOCK = "data/local/diagnostics/csl_closing_coverage.lock"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def exclusive_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
```

Import `fcntl` and `contextmanager` from `contextlib`. The lock file contains no data and remains ignored with its directory.

Add `_resolve()` and implement `_load_inputs()` with explicit injectable paths:

```python
def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_inputs(
    root: Path,
    *,
    results_path: str | Path,
    history: str | Path,
    raw_dir: str | Path,
) -> tuple[list[ClubResult], list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    results = load_club_results_csv(_resolve(root, results_path), DEFAULT_COMPETITION_ID)
    snapshots = load_snapshots(_resolve(root, history))
    raw = _resolve(root, raw_dir)
    official_payload = json.loads(
        (raw / "cfl_official_2026.json").read_text(encoding="utf-8")
    )
    sevenm_source = (raw / "sevenm_2026_fixture.js").read_text(encoding="utf-8")
    official_rows = parse_cfl_official_fixture_rows(
        official_payload,
        season=DEFAULT_SEASON,
        source_url=CFL_OFFICIAL_2026_URL,
    )
    sevenm_rows = parse_sevenm_fixture_rows(
        sevenm_source,
        season=DEFAULT_SEASON,
        source_url=SEVENM_2026_FIXTURE_URL,
    )
    return results, snapshots, official_rows, sevenm_rows
```

Catch file/JSON/parser errors only at the public runner boundary and return `{"status":"blocked","reason":"coverage_inputs_unavailable","error_type": type(exc).__name__}`. Never return `str(exc)`.

- [ ] **Step 4: Implement immutable manifest and report/pending runners**

Add the safe summary helper and implement the two runners directly:

```python
def _report_summary(report: dict[str, Any], *, status: str, write: bool) -> dict[str, Any]:
    return {
        "status": status,
        "write": write,
        "competition_id": report["competition_id"],
        "season": report["season"],
        "input_fingerprint": report["input_fingerprint"],
        "finished_result_count": report["summary"]["finished_result_count"],
        "observed_closing_count": report["summary"]["observed_closing_count"],
        "observed_current_decision_count": report["summary"]["observed_current_decision_count"],
        "missing_count": report["summary"]["missing_count"],
        "sample_too_small": report["performance"]["observed"]["decision_sample"]["sample_too_small"],
    }


def _run_initial_manifest_locked(
    *,
    root: str | Path = ".",
    write: bool = False,
    created_at: str,
    expected_count: int = 128,
    expected_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
    results_path: str | Path = DEFAULT_RESULTS,
    history: str | Path = DEFAULT_HISTORY,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    output: str | Path = DEFAULT_INITIAL_MANIFEST,
) -> dict[str, Any]:
    base = {
        "write": write,
        "competition_id": DEFAULT_COMPETITION_ID,
        "season": DEFAULT_SEASON,
    }
    root_path = Path(root)
    try:
        results, snapshots, official_rows, sevenm_rows = _load_inputs(
            root_path,
            results_path=results_path,
            history=history,
            raw_dir=raw_dir,
        )
        candidate = build_initial_missing_manifest(
            results=results,
            snapshots=snapshots,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            created_at=created_at,
            expected_count=expected_count,
        )
        validate_initial_manifest(
            candidate,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            expected_count=expected_count,
            expected_ids_sha256=expected_ids_sha256,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked",
            "reason": "coverage_inputs_unavailable",
            "error_type": type(exc).__name__,
            **base,
        }
    target = _resolve(root_path, output)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            existing_ids = manifest_match_ids(existing)
            candidate_ids = manifest_match_ids(candidate)
            same = initial_manifest_fingerprint(existing) == initial_manifest_fingerprint(candidate)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {
                "status": "blocked",
                "reason": "initial_manifest_invalid",
                "error_type": type(exc).__name__,
                **base,
            }
        if existing_ids != candidate_ids or not same:
            return {
                "status": "blocked",
                "reason": "initial_manifest_identity_mismatch",
                **base,
            }
        try:
            validate_initial_manifest(
                existing,
                results=results,
                official_rows=official_rows,
                sevenm_rows=sevenm_rows,
                expected_count=expected_count,
                expected_ids_sha256=expected_ids_sha256,
            )
        except ValueError:
            return {
                "status": "blocked",
                "reason": "initial_manifest_identity_mismatch",
                **base,
            }
        return {
            "status": "unchanged",
            **base,
            "matches": len(existing_ids),
            "observed_cutoff": existing["observed_cutoff"],
        }
    summary = {
        "status": "stored" if write else "dry_run",
        **base,
        "matches": len(manifest_match_ids(candidate)),
        "observed_cutoff": candidate["observed_cutoff"],
    }
    if write:
        try:
            write_json_atomic(target, candidate)
        except OSError as exc:
            return {
                "status": "error",
                "reason": "initial_manifest_commit_failed",
                "error_type": type(exc).__name__,
                **base,
            }
    return summary


def _run_closing_coverage_locked(
    *,
    root: str | Path = ".",
    write: bool = False,
    generated_at: str,
    results_path: str | Path = DEFAULT_RESULTS,
    history: str | Path = DEFAULT_HISTORY,
    raw_dir: str | Path = DEFAULT_RAW_DIR,
    initial_manifest_path: str | Path = DEFAULT_INITIAL_MANIFEST,
    report_path: str | Path = DEFAULT_REPORT,
    pending_path: str | Path = DEFAULT_PENDING,
    audit_events: list[dict[str, Any]] | None = None,
    expected_initial_count: int = INITIAL_EXPECTED_GAPS,
    expected_initial_ids_sha256: str = INITIAL_MATCH_IDS_SHA256,
    report_write: Callable[[Path, dict[str, Any]], None] = write_json_atomic,
) -> dict[str, Any]:
    root_path = Path(root)
    canonical_path = _resolve(root_path, report_path)
    recovery_path = _resolve(root_path, pending_path)
    existing = None
    if canonical_path.exists():
        try:
            value = json.loads(canonical_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                existing = value
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            existing = None
    prior_events = (
        existing.get("operational_events")
        if isinstance(existing, dict) and isinstance(existing.get("operational_events"), list)
        else []
    )
    initial_pending = {
        "schema_version": 1,
        "status": "pending",
        "attempted_at": generated_at,
        "input_fingerprint": None,
        "reason": "coverage_reconciliation_pending",
        "error_type": None,
    }
    if write:
        try:
            write_json_atomic(recovery_path, initial_pending)
        except OSError as exc:
            return {
                "status": "error",
                "reason": "coverage_pending_commit_failed",
                "error_type": type(exc).__name__,
                "write": True,
            }
    try:
        results, snapshots, official_rows, sevenm_rows = _load_inputs(
            root_path,
            results_path=results_path,
            history=history,
            raw_dir=raw_dir,
        )
        manifest = json.loads(
            _resolve(root_path, initial_manifest_path).read_text(encoding="utf-8")
        )
        validate_initial_manifest(
            manifest,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            expected_count=expected_initial_count,
            expected_ids_sha256=expected_initial_ids_sha256,
        )
        report = build_coverage_report(
            snapshots=snapshots,
            results=results,
            official_rows=official_rows,
            sevenm_rows=sevenm_rows,
            initial_manifest=manifest,
            generated_at=generated_at,
            audit_events=[*prior_events, *(audit_events or [])],
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        if write:
            try:
                write_json_atomic(
                    recovery_path,
                    {
                        **initial_pending,
                        "reason": "coverage_reconciliation_failed",
                        "error_type": type(exc).__name__,
                    },
                )
            except OSError:
                pass
        return {
            "status": "blocked",
            "reason": "coverage_inputs_unavailable",
            "error_type": type(exc).__name__,
            "write": write,
        }
    if not write:
        return _report_summary(report, status="dry_run", write=False)

    pending = {
        **initial_pending,
        "input_fingerprint": report["input_fingerprint"],
        "reason": "coverage_report_commit_pending",
    }
    try:
        write_json_atomic(recovery_path, pending)
    except OSError as exc:
        return {
            "status": "error",
            "reason": "coverage_pending_commit_failed",
            "error_type": type(exc).__name__,
            "write": True,
        }

    if existing is not None and existing.get("input_fingerprint") == report["input_fingerprint"]:
        try:
            recovery_path.unlink(missing_ok=True)
        except OSError as exc:
            summary = _report_summary(existing, status="unchanged_pending_cleanup", write=True)
            summary["error_type"] = type(exc).__name__
            return summary
        summary = _report_summary(existing, status="unchanged", write=True)
        summary["stale_pending_cleared"] = True
        return summary

    try:
        report_write(canonical_path, report)
    except OSError as exc:
        failed_pending = {
            **pending,
            "reason": "coverage_report_commit_failed",
            "error_type": type(exc).__name__,
        }
        try:
            write_json_atomic(recovery_path, failed_pending)
        except OSError:
            pass
        return {
            "status": "error",
            "reason": "coverage_report_commit_failed",
            "error_type": type(exc).__name__,
            "write": True,
            "input_fingerprint": report["input_fingerprint"],
        }
    try:
        recovery_path.unlink(missing_ok=True)
    except OSError as exc:
        summary = _report_summary(report, status="stored_pending_cleanup", write=True)
        summary["error_type"] = type(exc).__name__
        return summary
    return _report_summary(report, status="stored", write=True)
```

Add the public lock-owning wrappers. The internal functions above must never be imported by scheduler or CLI code:

```python
def run_initial_manifest(
    *,
    root: str | Path = ".",
    lock_path: str | Path = DEFAULT_LOCK,
    **kwargs: Any,
) -> dict[str, Any]:
    root_path = Path(root)
    if not bool(kwargs.get("write", False)):
        return _run_initial_manifest_locked(root=root_path, **kwargs)
    with exclusive_file_lock(_resolve(root_path, lock_path)):
        return _run_initial_manifest_locked(root=root_path, **kwargs)


def run_closing_coverage(
    *,
    root: str | Path = ".",
    lock_path: str | Path = DEFAULT_LOCK,
    **kwargs: Any,
) -> dict[str, Any]:
    root_path = Path(root)
    if not bool(kwargs.get("write", False)):
        return _run_closing_coverage_locked(root=root_path, **kwargs)
    with exclusive_file_lock(_resolve(root_path, lock_path)):
        return _run_closing_coverage_locked(root=root_path, **kwargs)
```

Only write mode acquires the file lock; dry-run performs no `mkdir`, lock creation, or output write. Add an inter-process regression test using `multiprocessing.get_context("fork")`: seed and freeze one test root, start two processes that both call `_run_test_coverage(write=True)` with distinct `quota_blocked` and `provider_refresh_failed` events for the same fixture, join both with a 10-second timeout, and assert both exit codes are zero. The final canonical report must contain exactly both events and both counts; pending must be absent. This proves the lock surrounds input/report read, event merge, pending write, report replace, and pending cleanup as one critical section.

- [ ] **Step 5: Implement CLI flags and verify default zero-write behavior**

The CLI must expose:

```text
--root PATH
--generated-at ISO8601
--initial-manifest
--write-initial-manifest
--write
```

`--write-initial-manifest` implies the manifest action and cannot be combined with `--write`. With neither manifest flag, run the coverage report. Neither action performs network I/O. Exit `0` for `dry_run`, `stored`, `unchanged`, `unchanged_pending_cleanup`, and `stored_pending_cleanup`; otherwise exit `2`. Default `--generated-at` to `datetime.now(timezone.utc).isoformat()` and pass the parsed flags directly to the matching runner.

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit local CSL observed closing coverage. Defaults to dry-run."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--initial-manifest", action="store_true")
    parser.add_argument("--write-initial-manifest", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.write_initial_manifest and args.write:
        parser.error("--write-initial-manifest cannot be combined with --write")
    observed = args.generated_at or datetime.now(timezone.utc).isoformat()
    if args.initial_manifest or args.write_initial_manifest:
        result = run_initial_manifest(
            root=args.root,
            write=args.write_initial_manifest,
            created_at=observed,
        )
    else:
        result = run_closing_coverage(
            root=args.root,
            write=args.write,
            generated_at=observed,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {
        "dry_run",
        "stored",
        "unchanged",
        "unchanged_pending_cleanup",
        "stored_pending_cleanup",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Run the full test command. Expected: PASS.

- [ ] **Step 6: Commit the local runner**

```bash
git add worldcup/csl_closing_coverage_runner.py tests/test_csl_closing_coverage_runner.py
git commit -m "feat: add CSL closing coverage runner"
```

---

### Task 5: Make observed archive commit atomic and self-validating

**Files:**
- Modify: `worldcup/csl_snapshot_archive.py:44-165`
- Modify: `tests/test_csl_snapshot_archive.py`

**Interfaces:**
- Consumes: current `archive_snapshot()` arguments and target naming.
- Produces: `validate_archive_fixture_coverage(snapshot: dict[str, Any], competition_id: str) -> dict[str, int]` and an unchanged public summary schema, plus the guarantee that `status in {created, duplicate}` means the target was re-opened, parsed, competition/fixture-identity validated, and byte-equivalent to canonical source content. The validator counts late rows but does not reject an otherwise valid mixed snapshot; strict pre-kickoff eligibility remains per-match in `select_observed_closing_exact()`.

- [ ] **Step 1: Add failing archive atomicity tests**

Import `load_snapshot` and `validate_snapshot` in `tests/test_csl_snapshot_archive.py`, then add:

```python
def test_archive_commit_failure_never_exposes_final_target():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())

        def broken_commit(path: Path, _content: str, **_kwargs) -> None:
            path.with_name(f".{path.name}.partial").write_text("{", encoding="utf-8")
            raise OSError("interrupted")

        try:
            archive_snapshot(
                source=source,
                history=history,
                commit_new=broken_commit,
            )
        except OSError:
            pass
        else:
            raise AssertionError("expected interrupted archive write")

        target = history / "snapshot_20260703T113000Z-live.json"
        assert not target.exists()


def test_created_archive_is_reopened_and_identity_validated():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "snapshot.json"
        history = root / "history"
        _write_json(source, _snapshot())
        summary = archive_snapshot(source=source, history=history)
        stored = load_snapshot(summary["path"])
        metadata = validate_snapshot(stored)

    assert metadata == {
        "competition_id": summary["competition_id"],
        "snapshot_at": summary["snapshot_at"],
        "matches": summary["matches"],
    }
```

Update the existing `_snapshot()` default match with `home_canonical="yunnan_yukun"`, `away_canonical="henan_fc"`, and nested `competition={"id":"csl_2026"}`. Add two fail-closed assertions: a nested `fifa_world_cup_2026` competition and missing canonical identity must each raise a stable `ValueError` and leave history empty. A `POSTPONED` row still requires valid identity/kickoff.

Add a mixed-time test with one non-postponed row whose kickoff is before `snapshot_at` and a second row whose kickoff is after it. Archive must return `created`, `late_matches=1`, and retain both rows. Pass the archived snapshot to `select_observed_closing_exact()` for each identity: the started row returns `None`, while the future row returns a closing. This proves one late row neither becomes observed nor blocks valid future coverage in the same archive.

Add an inter-process archive race test with `multiprocessing.get_context("fork")`: create two valid source snapshots with the same `snapshot_at` but different match content, start two processes calling `archive_snapshot()` into one history directory, and join with a 10-second timeout. Exactly one call returns `created`, the other returns `archive_conflict`; the final target parses as exactly one complete source and is never a byte mix or overwrite.

- [ ] **Step 2: Run the suite and verify `commit_new` is unsupported**

Run the full test command. Expected: FAIL with unexpected keyword `commit_new`.

- [ ] **Step 3: Add the atomic text writer and post-write validation**

Add a non-overwriting commit primitive to `worldcup/csl_snapshot_archive.py`; import `fcntl`, `os`, `contextmanager`, and `mkstemp`:

```python
@contextmanager
def _archive_lock(history: Path):
    history.mkdir(parents=True, exist_ok=True)
    lock_path = history / ".csl_snapshot_archive.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _commit_new_archive(
    path: Path,
    content: str,
    *,
    competition_id: str,
    min_matches: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        staged = load_snapshot(temp_path)
        validate_snapshot(
            staged,
            competition_id=competition_id,
            min_matches=min_matches,
        )
        validate_archive_fixture_coverage(staged, competition_id=competition_id)
        if _canonical_json(staged) != content:
            raise ValueError("archive_staging_content_mismatch")
        os.link(temp_path, path)
    except BaseException:
        raise
    finally:
        temp_path.unlink(missing_ok=True)
```

Add the fixture-level validator and call it immediately after `validate_snapshot()` for source, duplicate, and newly reopened target:

```python
def validate_archive_fixture_coverage(
    snapshot: dict[str, Any],
    *,
    competition_id: str = DEFAULT_COMPETITION_ID,
) -> dict[str, int]:
    snapshot_at = _parse_utc(snapshot.get("snapshot_at"))
    late_matches = 0
    for index, match in enumerate(snapshot.get("matches") or []):
        if not isinstance(match, dict):
            raise ValueError(f"invalid_match:{index}")
        actual_competition = str(
            ((match.get("competition") or {}).get("id"))
            or ((snapshot.get("competition") or {}).get("id"))
            or ""
        )
        if actual_competition != competition_id:
            raise ValueError(f"unexpected_match_competition:{index}:{actual_competition}")
        home = str(match.get("home_canonical") or "").strip()
        away = str(match.get("away_canonical") or "").strip()
        if not home or not away:
            raise ValueError(f"missing_match_identity:{index}")
        kickoff = _parse_utc(match.get("kickoff_at_utc"))
        if str(match.get("fixture_status") or "").upper() != "POSTPONED" and snapshot_at >= kickoff:
            late_matches += 1
    return {"late_matches": late_matches}
```

Merge `late_matches` into the local archive summary. Existing summary tests should expect `late_matches=0`; this remains local scheduler output and is not written into the archived snapshot.

Extend `archive_snapshot(..., commit_new: Callable[..., None] = _commit_new_archive)`. After source validation and content construction, acquire `_archive_lock(history_path)` before checking target existence and hold it through duplicate/conflict resolution, new-file commit, and final reopen validation. Replace `target.write_text(...)` with:

```python
commit_new(
    target,
    content,
    competition_id=competition_id,
    min_matches=min_matches,
)
try:
    stored = load_snapshot(target)
    stored_metadata = validate_snapshot(
        stored,
        competition_id=competition_id,
        min_matches=min_matches,
    )
    if _canonical_json(stored) != content or stored_metadata != metadata:
        raise ValueError(f"archive_validation_failed: {target}")
except BaseException:
    raise
```

If `os.link` raises `FileExistsError` despite the lock, re-open the winner: identical validated content returns `duplicate`; different content raises `archive_conflict`. Do the same validation on the ordinary existing duplicate path before returning `duplicate`. Never unlink a final target on validation failure—cleanup is limited to the temp file owned by this invocation; fail closed and preserve the final target for manual inspection. The standard commit validates staging before the non-overwriting `os.link`, so invalid content is never made visible by normal operation.

- [ ] **Step 4: Run the suite and verify archive tests pass**

Run the full test command. Expected: PASS with unchanged archive summary assertions.

- [ ] **Step 5: Commit archive hardening**

```bash
git add worldcup/csl_snapshot_archive.py tests/test_csl_snapshot_archive.py
git commit -m "fix: atomically validate CSL snapshot archives"
```

---

### Task 6: Annotate due decisions and wire non-blocking full audit

**Files:**
- Modify: `worldcup/csl_scheduled_publish.py:17-47,260-351,364-430,515-813`
- Modify: `tests/test_csl_scheduled_publish.py`

**Interfaces:**
- Consumes: `closing_archive_candidates()`, `load_snapshots()`, `run_closing_coverage()`.
- Produces: optional `archived_snapshots` input on `build_csl_publish_decision()`, `decision["closing_coverage_candidates"]`, injected `closing_coverage_fn`, safe/deduplicated operational events, safe `closing_coverage` summaries on scheduler results, and non-due pending recovery with no provider call.

- [ ] **Step 1: Add failing policy tests proving annotation cannot change refresh authority**

Add tests that call:

```python
decision = build_csl_publish_decision(
    snapshot=snapshot,
    quota_remaining=0,
    now="2026-07-10T10:30:00+00:00",
    archived_snapshots=[],
)
assert decision["should_refresh"] is False
assert decision["reason"] == "quota_exhausted"
assert decision["closing_coverage_candidates"][0]["issue_code"] == "closing_archive_missing"
```

Use a snapshot helper containing `home_canonical` and `away_canonical`. Add corresponding assertions for global throttle and not-due states: throttle retains `should_refresh=False`, while not-due has an empty candidate list. Add an archived snapshot and assert an already-due match is not listed as missing.

Add a corrupt-history policy test that passes `archive_history_status="unreadable"` with an empty snapshot list. Even when the match is due, assert `closing_coverage_candidates == []` and `closing_coverage_quality == {"history_status":"unreadable","warning":"coverage_history_unreadable"}`; it must not infer `closing_archive_missing` from unknown history.

- [ ] **Step 2: Add failing scheduler integration tests**

In `test_live_force_refreshes_builds_snapshot_and_publishes`, add this fake, pass it as `closing_coverage_fn`, and add `"coverage": 0` to the `calls` dictionary:

```python
def fake_coverage(**kwargs):
    calls["coverage"] += 1
    events.append("coverage")
    assert kwargs["write"] is True
    assert Path(kwargs["root"]) == root
    assert Path(kwargs["history"]) == root / "csl_history"
    assert Path(kwargs["results_path"]) == root / "club_results_csl_2026.csv"
    assert kwargs["generated_at"] == "2026-07-10T10:30:00+00:00"
    return {
        "status": "stored",
        "competition_id": "csl_2026",
        "season": "2026",
        "input_fingerprint": "coverage-fingerprint",
        "finished_result_count": 136,
        "observed_closing_count": 43,
        "observed_current_decision_count": 35,
        "missing_count": 93,
        "sample_too_small": True,
    }
```

The updated assertions are:

```python
assert calls == {
    "results": 1,
    "coverage": 1,
    "shadow": 1,
    "refresh": 1,
    "publish": 1,
}
assert events[:4] == ["results", "coverage", "shadow", "odds"]
assert result["closing_coverage"]["input_fingerprint"] == "coverage-fingerprint"
```

Add this complete non-due recovery test:

```python
def test_live_not_due_reconciles_local_pending_without_provider_calls():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot_path = root / "csl_publish_snapshot.json"
        quota_path = root / "quota.json"
        _write_json(
            snapshot_path,
            _snapshot(
                ["2026-07-11T12:00:00+00:00"],
                observed_at="2026-07-10T10:00:00+00:00",
            ),
        )
        _write_json(quota_path, {"providers": {"theoddsapi_secondary": {"remaining": 200}}})
        calls = {"coverage": 0}

        def fake_coverage(**kwargs):
            calls["coverage"] += 1
            assert kwargs["write"] is True
            return {
                "status": "unchanged",
                "competition_id": "csl_2026",
                "season": "2026",
                "input_fingerprint": "same",
                "finished_result_count": 171,
                "observed_closing_count": 43,
                "observed_current_decision_count": 35,
                "missing_count": 128,
                "sample_too_small": True,
            }

        def forbidden(**_kwargs):
            raise AssertionError("not-due recovery must not call provider or publish")

        result = run_csl_scheduled_publish(
            now="2026-07-10T10:10:00+00:00",
            live=True,
            cache_dir=root,
            quota_path=quota_path,
            snapshot_path=snapshot_path,
            diagnostics_snapshot_path=root / "csl_live_league_snapshot.json",
        load_env=lambda _path: (_ for _ in ()).throw(
            AssertionError("non-due coverage recovery must not read .env")
        ),
            closing_coverage_fn=fake_coverage,
            closing_coverage_root=root,
            results_refresh_fn=forbidden,
            postmatch_shadow_fn=forbidden,
            refresh_fn=forbidden,
            snapshot_builder=forbidden,
            archive_fn=forbidden,
            publish_fn=forbidden,
        )

    assert result["status"] == "skipped"
    assert result["closing_coverage"]["status"] == "unchanged"
    assert calls == {"coverage": 1}
```

Add a coverage-error version of the current live-force test by replacing only `closing_coverage_fn` with:

```python
def broken_coverage(**_kwargs):
    raise RuntimeError("private odds secret")
```

Keep all existing successful odds/build/archive/publish fakes. Assert `result["status"] == "published"`, the safe coverage summary equals `{"status":"error","reason":"csl_closing_coverage_failed","error_type":"RuntimeError"}`, the local scheduler result carries that error, and the built/published snapshot contains neither `csl_closing_coverage_failed` nor `"private odds secret"`.

In the existing dry-run no-side-effect test, pass `closing_coverage_fn=forbidden`; the current assertions then prove dry-run never invokes it. In `test_shadow_runs_before_odds_refresh_failure_and_summary_is_preserved`, make the coverage fake collect each `audit_events` argument: assert the event order is `results, coverage, shadow, odds, coverage`, the first event list is empty, and the second contains one `provider_refresh_failed` record for the due fixture. In `test_results_failure_marks_cached_fixture_status_stale_without_blocking_odds_publish`, add a counting coverage fake, assert it runs once, and keep the existing exploding shadow assertion to prove failed result refresh blocks only shadow, not full coverage reconciliation. In `test_archive_failure_warns_but_does_not_block_current_publish`, collect coverage calls and assert the second call contains one `snapshot_archive_failed` record while publish still succeeds.

Update `test_csl_publish_retries_pending_snapshot_without_consuming_refresh_again`: inject a counting coverage fake into the second invocation and assert it runs once before the second HMAC publish attempt, while results refresh, odds refresh, snapshot build, and postmatch shadow still do not rerun. Add a variant whose second invocation has an existing HMAC pending plus `load_env=lambda _: {}`; assert coverage runs and its summary is returned even though the publish retry is blocked with `missing_ingest_hmac_secret`.

- [ ] **Step 3: Run the suite and verify new scheduler arguments are unsupported**

Run the full test command. Expected: FAIL because `archived_snapshots` / `closing_coverage_fn` do not exist.

- [ ] **Step 4: Add read-only coverage candidate annotation**

Import `closing_archive_candidates`, `load_snapshots`, and `run_closing_coverage`. Extend the policy signature:

```python
def build_csl_publish_decision(
    *,
    snapshot: dict[str, Any],
    quota_remaining: int | None,
    now: str,
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    archived_snapshots: list[dict[str, Any]] | None = None,
    archive_history_status: str = "ok",
) -> dict[str, Any]:
```

Compute anchor/expiry due items before the quota early return, then add to `base`:

```python
due_ids = {
    str(item.get("match_id") or "")
    for item in [*anchor_due_matches, *expiry_due_matches]
    if item.get("match_id")
}
if archive_history_status == "ok":
    base["closing_coverage_candidates"] = closing_archive_candidates(
        snapshot=snapshot,
        archived_snapshots=archived_snapshots or [],
        due_match_ids=due_ids,
    )
    base["closing_coverage_quality"] = {"history_status": "ok", "warning": None}
else:
    base["closing_coverage_candidates"] = []
    base["closing_coverage_quality"] = {
        "history_status": "unreadable",
        "warning": "coverage_history_unreadable",
    }
```

Do not use this list when computing `potential_refresh`; existing anchors, expiry, discovery, throttle, and quota remain the only authority.

Move `history_path` resolution above decision construction in `run_csl_scheduled_publish()`. Load history with this result-bearing helper and pass both fields to the decision:

```python
def _load_archive_history_safe(history_path: Path) -> dict[str, Any]:
    try:
        snapshots = load_snapshots(history_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "status": "unreadable",
            "warning": "coverage_history_unreadable",
            "snapshots": [],
        }
    return {"status": "ok", "warning": None, "snapshots": snapshots}
```

Never pass the empty list from an unreadable result to `closing_archive_candidates()`. The helper never exposes a path or exception text. Dry-run remains read-only. Add an integration test with one corrupt `snapshot_*.json`; decision quality must be unreadable, candidates empty, and serialized output must not contain the corrupt content or filesystem error.

- [ ] **Step 5: Add safe audit invocation and pending recovery**

Add:

```python
ClosingCoverageFn = Callable[..., dict[str, Any]]


def _safe_closing_coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_closing_coverage_result"}
    safe = {"status": str(value.get("status") or "error")}
    for key in ("reason", "competition_id", "season", "input_fingerprint", "error_type"):
        if value.get(key) is not None:
            safe[key] = str(value[key])
    for key in (
        "finished_result_count",
        "observed_closing_count",
        "observed_current_decision_count",
        "missing_count",
    ):
        if isinstance(value.get(key), int) and not isinstance(value.get(key), bool):
            safe[key] = value[key]
    if isinstance(value.get("sample_too_small"), bool):
        safe["sample_too_small"] = value["sample_too_small"]
    return safe
```

Extend the runner signature with:

```python
closing_coverage_fn: ClosingCoverageFn = run_closing_coverage,
closing_coverage_root: str | Path = ".",
```

Use one helper `_run_closing_coverage_safe()` that calls:

```python
closing_coverage_fn(
    root=closing_coverage_root,
    history=history_path,
    results_path=Path(cache_dir) / f"club_results_{DEFAULT_COMPETITION_ID}.csv",
    write=True,
    generated_at=observed,
    audit_events=audit_events,
)
```

and catches `Exception` into only `status`, `reason="csl_closing_coverage_failed"`, and `error_type`. Its `audit_events` parameter defaults to an empty list.

Add this safe event projection; it deliberately drops candidates without complete canonical identity:

```python
def _coverage_audit_events(
    candidates: list[dict[str, Any]],
    *,
    observed_at: str,
    issue_code: str,
) -> list[dict[str, Any]]:
    events = []
    for candidate in candidates:
        kickoff = candidate.get("kickoff_at_utc")
        home = candidate.get("home_canonical")
        away = candidate.get("away_canonical")
        if not kickoff or not home or not away:
            continue
        events.append(
            {
                "observed_at": observed_at,
                "match_id": str(candidate.get("match_id") or ""),
                "kickoff_at_utc": str(kickoff),
                "home_canonical": str(home),
                "away_canonical": str(away),
                "issue_code": issue_code,
            }
        )
    return events
```

Execution order is exact:

- Dry-run returns before calling the audit.
- Immediately after dry-run returns, read only the HMAC pending filename and handle every live non-due run locally: call coverage once before `load_env()`, HMAC validation, or any provider/publish operation. If the decision reason is `quota_exhausted`, pass deduplicated `quota_blocked` events for its coverage candidates.
- If live non-due has no HMAC pending, return `skipped` with coverage summary before `load_env()`; it does not call result refresh, postmatch shadow, odds refresh, builder, archive, or publish. The exploding-`load_env` test above proves this ordering.
- If live non-due has an HMAC pending snapshot, run coverage first, then enter the existing env/secret validation and HMAC retry path; include coverage summary in blocked/invalid/republished returns. Never rerun odds/results/snapshot build. A missing or weak HMAC secret may block that publish retry but cannot prevent the preceding local coverage reconciliation.
- Live due/forced runs accepted-result refresh, then the local full audit once, then the existing postmatch shadow gate, then odds refresh.
- The local audit runs even when result refresh failed because the existing replay is still the canonical accepted result set.
- Any audit error is included only in the local scheduler return/diagnostic summary; it never mutates the built/public snapshot and never blocks refresh/archive/publish.
- Preserve audit summaries in every return path after the audit ran, including odds blocked/error, empty snapshot, publish pending, and published.
- If odds refresh returns non-`fetched`, run the audit a second time with `quota_blocked` when `refresh.reason == "quota_exhausted"`, otherwise `provider_refresh_failed`; return the second audit summary so the canonical report preserves the cause.
- If archive returns neither `created` nor `duplicate`, derive affected identities from every valid match in the just-built snapshot (the failed archive would have covered all of them), then run the audit a second time with `archive_validation_failed` when `archive.reason == "archive_validation_failed"`, otherwise `snapshot_archive_failed`; publication remains non-blocking and the returned summary is the second audit result.
- Provider/quota events come only from `decision["closing_coverage_candidates"]`; do not attach a provider failure to unrelated fixtures in the competition snapshot. Archive events may use the just-built snapshot because one archive file owns that entire snapshot.
- Derive archive-affected candidates with:

```python
built_match_ids = {
    _match_id(match)
    for match in built.get("matches") or []
    if isinstance(match, dict)
}
archive_affected = closing_archive_candidates(
    snapshot=built,
    archived_snapshots=[],
    due_match_ids=built_match_ids,
)
```

Then pass `archive_affected` through `_coverage_audit_events()` with the archive issue code.

Keep coverage projection scheduler-local. Add a public-policy projector and use it in `_attach_run_metadata()` instead of storing the whole local decision:

```python
LOCAL_POLICY_FIELDS = {
    "closing_coverage_candidates",
    "closing_coverage_quality",
}


def _public_policy_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in decision.items()
        if key not in LOCAL_POLICY_FIELDS
    }
```

The scheduler function's local JSON return may contain those fields for diagnostics; `built["run"]["policy"]`, archived snapshot, HMAC input snapshot, API, SQLite, preview, and static export must not. In the live publish test, inspect the written/published snapshot and assert neither key appears under `run.policy`; serialize the full built snapshot and assert the coverage candidates/quality strings are absent. Keep the dry-run local-decision assertions so operators can still see them without changing the public contract.

Change the existing archive exception projection so `ValueError` maps to safe reason `archive_validation_failed` and `OSError` maps to `snapshot_archive_failed`; expose only `error_type`, never the exception message:

```python
except (OSError, ValueError) as exc:
    archive = {
        "status": "error",
        "reason": (
            "archive_validation_failed"
            if isinstance(exc, ValueError)
            else "snapshot_archive_failed"
        ),
        "error_type": type(exc).__name__,
    }
```
- A pending HMAC publish retry keeps its existing no-odds-refresh behavior; the local idempotent coverage reconciliation still runs before secret handling so its own pending cannot starve behind an unrelated publish pending.

- [ ] **Step 6: Run the full suite and verify all scheduler behavior**

Run the repository test command. Expected: PASS, with no change to existing quota/anchor/outbox assertions except the intentional coverage summary and call order additions.

- [ ] **Step 7: Commit scheduler integration**

```bash
git add worldcup/csl_scheduled_publish.py tests/test_csl_scheduled_publish.py
git commit -m "feat: audit CSL closing coverage in scheduler"
```

---

### Task 7: Document the contract and verify the real local baseline

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`

**Interfaces:**
- Consumes: final runner CLI and report fields from Tasks 1-6.
- Produces: synchronized project rules, operator commands, and a verifiable baseline record.

- [ ] **Step 1: Add the architecture and command documentation**

In `README.md`, add `csl_closing_coverage.py` and `csl_closing_coverage_runner.py` to the module map, then add a concise section containing these commands:

```bash
# 默认 dry-run：全量读取已接受赛果、双源赛程和 observed history，零写入
python3 -m worldcup.csl_closing_coverage_runner

# 一次性冻结初始 128 场 match ID 清单（ignored 本地产物）
python3 -m worldcup.csl_closing_coverage_runner --initial-manifest --write-initial-manifest

# 原子更新 observed-only coverage 报告与 pending 恢复状态
python3 -m worldcup.csl_closing_coverage_runner --write
```

Document all of the following without reporting reconstructed data as official performance:

- Initial 128 IDs are fixed reconstruction eligibility membership; `2026-06-29` is only the bootstrap cutoff.
- `csl_closing_coverage.json` is full-reconciliation coverage; `csl_closing_coverage_pending.json` is only recovery state.
- The canonical report's safe `operational_events` / `operational_event_counts` distinguish quota, provider, archive, validation, and unresolved closing gaps; repeated wakes deduplicate by exact kickoff + canonical teams + issue code.
- Official headline remains observed schema-v2 `MATCH_PICK` settlement; reconstructed performance is absent until a later approved source-specific implementation and will never be merged into the headline.
- Scheduler annotations do not create new due authority and the audit makes no provider calls.
- Report/audit failure is a local warning and does not hide or block a valid current pick.

- [ ] **Step 2: Add the formal data contract**

In `docs/superpowers/data-contract.md`, add one CSL Closing Coverage subsection containing:

- the exact provenance/status enums;
- the exact reason whitelist and precedence table;
- exact report paths and pending lifecycle;
- exact operational-event allowlist, identity key, first-observed deduplication, and sensitive-field projection;
- exact `input_fingerprint` exclusions (`generated_at`, paths, input ordering) and inclusions (membership, identities, kickoff, observed snapshot identity, coverage state/reasons);
- exact official performance filter;
- exact no-combined-performance rule;
- scheduler candidate non-authority rule;
- archive atomic validation rule;
- dry-run/network/quota/secret boundaries.

- [ ] **Step 3: Synchronize local project instructions**

Add the same short rule paragraph to both `AGENTS.md` and `CLAUDE.md`:

```markdown
- 中超 closing coverage 使用固定初始 128 个 match id + 全量 finished/history reconciliation；`csl_closing_coverage.json` 只把 observed schema v2 `MATCH_PICK` 计入正式战绩，reconstructed 必须独立统计且不得混算。audit 默认 dry-run、不得联网或调用 provider；scheduled publish 中 audit/pending 失败只记安全 warning，不得绕过 due/quota/live 边界或阻断已有有效首选。
```

Do not copy transient counts beyond the verified baseline note into permanent rules.

- [ ] **Step 4: Run full tests before creating local artifacts**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: every test passes; optional FastAPI remains an explicit skip if the dependency is unavailable.

- [ ] **Step 5: Run real-data dry-run acceptance**

From the implementation worktree, point the local-only runner at the shared project data root:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_closing_coverage_runner \
  --root /Users/eagod/ai-dev/足彩 \
  --initial-manifest
```

Expected safe summary at the current frozen baseline: `matches=128`, `observed_cutoff=2026-06-29`, and no manifest/report/pending write.

- [ ] **Step 6: Write the ignored manifest and canonical coverage report**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_closing_coverage_runner \
  --root /Users/eagod/ai-dev/足彩 \
  --initial-manifest \
  --write-initial-manifest

/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_closing_coverage_runner \
  --root /Users/eagod/ai-dev/足彩 \
  --write
```

Verify with a read-only script that:

- the manifest has exactly 128 unique match IDs, each with exact UTC kickoff and dual source IDs;
- all 128 dates fall from `2026-03-06` through `2026-06-28`, but membership is read from IDs;
- the report has exactly one row per accepted `csl_2026` / `2026` result;
- every row has one valid status/reason pair;
- the current baseline contains 43 observed closings, 35 observed current decisions, 8 observed missing-current-decision records, and 128 initial missing records;
- the current official observed tally remains 17 hit / 18 miss, with no reconstructed tally or combined rate;
- pending is absent after successful report commit;
- neither ignored artifact contains `Authorization`, `Cookie`, `api_key`, `secret`, `.env`, or request headers.

If accepted results or new genuine observed closings changed after this plan date, do not force the 171/43/35 performance totals. Require only the frozen 128 initial IDs and explain the additional post-cutoff observed/missing rows in `RECENT_WORK.md`.

- [ ] **Step 7: Run idempotency and operational regression checks**

Run the two write commands again. Expected: both return `unchanged`; file hashes for the manifest and report remain unchanged. Then run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_scheduled_publish
```

Expected: dry-run only; no `.env` read, no provider call, no quota change, no report/pending write, and decision includes `closing_coverage_candidates` only for matches already due under existing policy.

- [ ] **Step 8: Update recent work and run final verification**

Add one `RECENT_WORK.md` entry with date, implementation commits, exact full-test count, manifest/report safe counts, idempotency result, and remaining boundary: source approval + real saved samples + a separate source-specific plan are still required before historical reconstruction.

Run the full test command one final time and inspect:

```bash
git status --short
git diff --check
git log --oneline --max-count=8
```

Expected: tests pass, no whitespace errors, ignored local artifacts are absent from `git status`, and only the planned tracked documentation remains uncommitted for this task.

- [ ] **Step 9: Commit documentation and implementation record**

```bash
git add README.md docs/superpowers/data-contract.md AGENTS.md CLAUDE.md RECENT_WORK.md
git commit -m "docs: document CSL closing coverage audit"
```

---

## Adversarial Self-Review Gate Before Execution

The implementing agent must stop before Task 1 and re-check these blockers against the current branch:

1. **False accuracy improvement:** This work improves evidence coverage, not model win rate. No parameter, pick filter, or sample deletion is authorized.
2. **Historical leakage:** Result scores may enter only settlement after a decision already exists. This plan does not reconstruct decisions, so no score can influence selection.
3. **Membership drift:** The 128 initial IDs are written once and compared by exact IDs on every subsequent run. Date cutoff cannot admit later matches.
4. **Kickoff fabrication:** Manifest creation fails if the two saved accepted schedule sources do not agree exactly. Midnight fallback is forbidden.
5. **Observed downgrade:** State precedence tests force observed evidence above any future reconstructed/manual evidence.
6. **Mixed performance:** The report has separate observed and reconstructed blocks and deliberately no combined performance key.
7. **Trigger loss:** Every audit invocation rebuilds from complete accepted results + history. Pending speeds recovery but is not correctness-critical.
8. **Pending/report torn state:** Pending precedes canonical report; canonical is atomic; same-fingerprint retry clears stale pending without provider calls or duplicate counting.
9. **Quota expansion:** Coverage candidates are computed only for already-due items and are excluded from `potential_refresh`. Quota/throttle/live tests must prove unchanged authority.
10. **Public contract leakage:** Artifacts stay under ignored local paths; public API, SQLite, preview, notification, and HMAC payloads are untouched.
11. **Archive partial write:** Atomic archive creation plus re-open validation is required before success is reported.
12. **Hidden source assumptions:** Historical odds source probing/parsing is explicitly outside this plan. No source adapter may be written until approval and real saved samples exist.
13. **Dirty main overlap:** Execute in the existing isolated worktree. Before later integration, compare the five documentation files against the user's dirty main worktree and resolve overlaps without overwriting user changes.
14. **Validation illusion:** Unit tests alone do not prove the 128 baseline. The real-data dry-run, explicit ignored writes, exact count/status checks, rerun idempotency, sensitive scan, and scheduler dry-run are all required.

If any gate reveals a business-semantic, API, source-access, quota, secret, migration, or destructive change beyond this document, pause and request a new confirmation. Otherwise proceed task by task.

## Follow-up Plan Boundary

After this foundation is implemented and verified, the next plan is source-specific and cannot be drafted as executable parser work until all of the following exist: a confirmed candidate source, terms/robots/retention/reuse/rate-limit approval, separately confirmed small-sample network access, and real raw samples saved under `data/probe/csl_historical_odds/<source_id>/`. That later plan will cover the offline parser, normalized quote-time interval, reconstruction isolation, immutable bundle, and reconstructed-only report. It must not modify the observed report semantics delivered here.
