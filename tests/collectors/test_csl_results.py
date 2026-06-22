from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.collectors.csl_results import (
    compare_csl_sources,
    parse_csl_result_rows,
    write_replay_candidate_csv,
)


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
    assert result.competition_id == "csl_2026"
    assert result.source_id == "primary"
    assert result.source_role == "primary"
    assert result.issues == []
    summary = result.to_summary()
    assert summary["competition_id"] == "csl_2026"
    assert summary["source_id"] == "primary"
    assert summary["source_role"] == "primary"
    assert summary["valid_rows"] == 1
    parsed = result.rows[0]
    assert parsed.competition_id == "csl_2026"
    assert parsed.source_role == "primary"
    assert parsed.source_agreement == "uncompared"
    assert parsed.season == "2026"
    assert parsed.date == "2026-03-01"
    assert parsed.home_team_raw == "Shanghai Port"
    assert parsed.away_team_raw == "Shandong Taishan"
    assert parsed.home_team == "Shanghai Port"
    assert parsed.away_team == "Shandong Taishan"
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
    assert parsed.team_key == ("2026", "shanghai_port", "shandong_taishan")
    assert parsed.to_replay_row()["home_team"] == "Shanghai Port"
    assert parsed.to_replay_row()["away_team"] == "Shandong Taishan"


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
    assert result.issues[0].source_role == "primary"
    assert result.issues[0].to_dict()["source_role"] == "primary"


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


def test_parse_csl_result_rows_records_invalid_neutral():
    result = parse_csl_result_rows(
        [_row(neutral="maybe")],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.rows == []
    assert result.issues[0].reason == "invalid_neutral"
    assert result.issues[0].field == "neutral"
    assert result.issues[0].value == "maybe"


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


def test_parse_csl_result_rows_check_source_uses_match_id_fallback():
    result = parse_csl_result_rows(
        [
            _row(
                source_match_id="",
                match_id="c1",
                source_url="https://example.invalid/check/c1",
            )
        ],
        competition_id="csl_2026",
        source_id="check",
        source_role="check",
    )

    parsed = result.rows[0]
    assert parsed.source_check_id == "c1"
    assert parsed.source_check_url == "https://example.invalid/check/c1"
    assert parsed.source_primary_id is None
    assert parsed.source_primary_url is None


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
