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
