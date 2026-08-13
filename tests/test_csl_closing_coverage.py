from __future__ import annotations

from copy import deepcopy

from worldcup.club_rating import ClubResult
from worldcup.csl_closing_coverage import (
    HistoricalCoverageEvidence,
    build_coverage_report,
    build_initial_missing_manifest,
    classify_coverage,
    classification_dict,
    closing_archive_candidates,
    coverage_input_fingerprint,
    initial_match_ids_sha256,
    manifest_match_ids,
    normalize_audit_events,
    resolve_fixture,
    select_observed_closing_exact,
    stable_match_id,
    validate_initial_manifest,
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


def _fixture_row(
    source_id: str, kickoff: str = "2026-03-06T11:35:00+00:00"
) -> dict[str, str]:
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


def test_same_priority_historical_evidence_unions_reasons_in_whitelist_order():
    value = classify_coverage(
        observed=None,
        historical=(
            HistoricalCoverageEvidence("missing", ("post_kickoff_only",)),
            HistoricalCoverageEvidence("missing", ("source_unavailable",)),
        ),
    )

    assert value.coverage_status == "missing"
    assert value.reason_code == "source_unavailable"
    assert value.reason_codes == ("source_unavailable", "post_kickoff_only")


def test_same_priority_reason_union_ignores_lower_priority_evidence():
    value = classify_coverage(
        observed=None,
        historical=(
            HistoricalCoverageEvidence("missing", ("post_kickoff_only",)),
            HistoricalCoverageEvidence("market_baseline_only", ("aggregate_only",)),
            HistoricalCoverageEvidence("missing", ("source_unavailable",)),
        ),
    )

    assert value.coverage_status == "market_baseline_only"
    assert value.reason_code == "aggregate_only"
    assert value.reason_codes == ("aggregate_only",)


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
        (
            2,
            [_fixture_row("official")],
            [_fixture_row("sevenm")],
            "initial_gap_count_mismatch:1:2",
        ),
        (
            1,
            [_fixture_row("official")],
            [_fixture_row("sevenm", "2026-03-06T12:35:00+00:00")],
            "initial_fixture_unverified",
        ),
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


def test_fixture_resolution_rejects_empty_or_whitespace_source_ids():
    for source, source_id in (("official", ""), ("sevenm", "   ")):
        official = _fixture_row("official")
        sevenm = _fixture_row("sevenm")
        (official if source == "official" else sevenm)["source_match_id"] = source_id

        resolution = resolve_fixture(_result(), [official], [sevenm])

        assert resolution.kickoff_at_utc is None
        assert resolution.source_match_ids == {}
        assert resolution.reason_codes == ("identity_mismatch",)


def test_initial_manifest_build_rejects_empty_source_id():
    official = _fixture_row("official")
    official["source_match_id"] = ""

    try:
        build_initial_missing_manifest(
            results=[_result()],
            snapshots=[],
            official_rows=[official],
            sevenm_rows=[_fixture_row("sevenm")],
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=1,
        )
    except ValueError as exc:
        assert str(exc) == (
            "initial_fixture_unverified:"
            "csl_2026:2026-03-06:chengdu_rongcheng:shenzhen_peng_city:"
            "identity_mismatch"
        )
    else:
        raise AssertionError("expected empty source ID to block manifest build")


def test_initial_manifest_validator_rejects_whitespace_source_id():
    manifest = build_initial_missing_manifest(
        results=[_result()],
        snapshots=[],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        created_at="2026-08-13T02:00:00+00:00",
        expected_count=1,
    )
    official = _fixture_row("official")
    official["source_match_id"] = "\t "

    try:
        validate_initial_manifest(
            manifest,
            results=[_result()],
            official_rows=[official],
            sevenm_rows=[_fixture_row("sevenm")],
            expected_count=1,
            expected_ids_sha256=initial_match_ids_sha256({stable_match_id(_result())}),
        )
    except ValueError as exc:
        assert str(exc) == (
            "initial_manifest_fixture_unverified:"
            "csl_2026:2026-03-06:chengdu_rongcheng:shenzhen_peng_city"
        )
    else:
        raise AssertionError("expected whitespace source ID to block validation")


def test_initial_manifest_rejects_duplicate_source_match_ids_across_rows():
    second = ClubResult(
        competition_id="csl_2026",
        season="2026",
        date="2026-03-07",
        home_team="山东泰山",
        away_team="河南队",
        home_canonical="shandong_taishan",
        away_canonical="henan_fc",
        home_score=2,
        away_score=0,
        neutral=False,
    )
    official_first = _fixture_row("official")
    sevenm_first = _fixture_row("sevenm")
    official_second = {
        **official_first,
        "kickoff_at_utc": "2026-03-07T11:35:00+00:00",
        "home_team": second.home_team,
        "away_team": second.away_team,
        "home_canonical": second.home_canonical,
        "away_canonical": second.away_canonical,
    }
    sevenm_second = {
        **sevenm_first,
        "kickoff_at_utc": "2026-03-07T11:35:00+00:00",
        "home_team": second.home_team,
        "away_team": second.away_team,
        "home_canonical": second.home_canonical,
        "away_canonical": second.away_canonical,
    }

    try:
        build_initial_missing_manifest(
            results=[_result(), second],
            snapshots=[],
            official_rows=[official_first, official_second],
            sevenm_rows=[sevenm_first, sevenm_second],
            created_at="2026-08-13T02:00:00+00:00",
            expected_count=2,
        )
    except ValueError as exc:
        assert str(exc) == "initial_manifest_source_identity_duplicate:cfl_official"
    else:
        raise AssertionError("expected duplicate source match identity to fail closed")


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
    assert report["matches"][0]["settlement"] is None


def test_observed_legacy_decision_is_not_settled_per_match():
    legacy = {
        "schema_version": 1,
        "label": "STRONG_VALUE",
        "market": "1X2",
        "selection": "home",
    }
    report = build_coverage_report(
        snapshots=[_snapshot(snapshot_at="2026-03-06T11:00:00+00:00", decision=legacy)],
        results=[_result()],
        official_rows=[_fixture_row("official")],
        sevenm_rows=[_fixture_row("sevenm")],
        initial_manifest={"expected_match_count": 0, "matches": []},
        generated_at="2026-08-13T02:00:00+00:00",
    )
    assert report["matches"][0]["coverage_status"] == (
        "observed_missing_current_decision"
    )
    assert report["matches"][0]["settlement"] is None


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


def test_audit_event_tie_break_is_independent_of_input_order_and_fingerprint():
    base_event = {
        "observed_at": "2026-06-29T10:30:00+00:00",
        "kickoff_at_utc": "2026-06-29T11:35:00+00:00",
        "home_canonical": "chengdu_rongcheng",
        "away_canonical": "shenzhen_peng_city",
        "issue_code": "provider_refresh_failed",
    }
    events = [
        {**base_event, "match_id": "provider-z"},
        {**base_event, "match_id": "provider-a"},
    ]
    forward = normalize_audit_events(events)
    reverse = normalize_audit_events(list(reversed(events)))

    assert forward == reverse
    assert forward[0]["match_id"] == "provider-a"
    report = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "operational_events": forward,
        "matches": [],
    }
    reversed_report = {**report, "operational_events": reverse}
    assert coverage_input_fingerprint(report) == coverage_input_fingerprint(
        reversed_report
    )


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


def test_closing_archive_candidate_fallback_matches_scheduler_display_identity():
    current = _snapshot(
        snapshot_at="2026-03-06T10:00:00+00:00",
        decision=_current_pick(),
    )
    due_id = "2026-03-06T11:35:00+00:00|成都蓉城|深圳新鹏城"

    candidates = closing_archive_candidates(
        snapshot=current,
        archived_snapshots=[],
        due_match_ids={due_id},
    )

    assert [row["match_id"] for row in candidates] == [due_id]
