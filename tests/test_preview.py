from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.preview import build_preview_html, write_preview


def _snapshot() -> dict:
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "counts": {"matches": 2},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2099-06-11T19:00:00+00:00",
                "competition": {"id": "fifa_world_cup_2026", "name": "2026 世界杯"},
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A", "sentinel": "legacy-must-not-render"}],
                "match_decision": {
                    "schema_version": 2,
                    "label": "MATCH_PICK",
                    "market": "DNB",
                    "selection": "home",
                    "line": 0.0,
                    "odds": 1.74,
                    "p_hit_safe": 0.59,
                    "p_no_loss_safe": 0.73,
                    "valid_until": "2099-06-11T19:00:00+00:00",
                    "selected_option_id": "internal-only",
                },
            },
            {
                "kickoff_at_utc": "2099-06-12T01:00:00+00:00",
                "competition": {"id": "csl_2026", "name": "中超 2026"},
                "stage": "Round 16",
                "home_team": "Shanghai Port",
                "away_team": "Shandong Taishan",
                "match_decision": {
                    "schema_version": 2,
                    "label": "NO_CLEAN_MARKET",
                    "reasons": ["club_rating_pending"],
                },
            },
        ],
        "finished": {
            "schema_version": 2,
            "matches": [
                {
                    "kickoff_at_utc": "2026-06-07T19:00:00+00:00",
                    "competition": {"id": "fifa_world_cup_2026", "name": "2026 世界杯"},
                    "home_team": "Canada",
                    "away_team": "Qatar",
                    "result": {"home_score": 2, "away_score": 1},
                    "closing_match_decision": {
                        "schema_version": 2,
                        "label": "MATCH_PICK",
                        "market": "1X2",
                        "selection": "home",
                        "odds": 1.8,
                        "p_hit_safe": 0.61,
                        "p_no_loss_safe": 0.61,
                    },
                    "closing_signals": [
                        {"grade": "S", "sentinel": "finished-legacy-must-not-render"}
                    ],
                }
            ],
            "skipped_no_closing": 0,
        },
    }


def test_preview_renders_only_match_picks_and_no_pick_state():
    html = build_preview_html(_snapshot())

    assert "本场首选" in html
    assert "暂无可靠首选" in html
    assert "平手盘 - 主队" in html
    assert "安全命中率 <b>59.0%</b>" in html
    assert "不亏概率 <b>73.0%</b>" in html
    assert "墨西哥 对 南非" in html
    assert "上海海港 对 山东泰山" in html
    assert "仅用于研究分析，不构成投注建议。" in html


def test_preview_never_renders_legacy_grade_payloads_or_controls():
    html = build_preview_html(_snapshot())
    lowered = html.lower()

    for forbidden in (
        "legacy-must-not-render",
        "finished-legacy-must-not-render",
        "价值分歧",
        "等级",
        "s 级",
        "a 级",
        "grade-pill",
        "data-grade",
        "信号",
    ):
        assert forbidden not in lowered


def test_preview_renders_finished_pick_result_and_record():
    html = build_preview_html(_snapshot())

    assert "本场首选战绩" in html
    assert "命中 1 · 未中 0 · 走水 0 · 命中率 100%" in html
    assert "加拿大 对 卡塔尔" in html
    assert "2 - 1" in html
    assert '<span class="outcome outcome-hit">命中</span>' in html
    assert "样本不足，仅作观察" in html


def test_preview_marks_legacy_finished_decision_without_mixing_its_record():
    snapshot = _snapshot()
    snapshot["finished"]["matches"][0]["closing_match_decision"] = {
        "schema_version": 1,
        "label": "HIGH_CONFIDENCE_LEAN",
        "market": "1X2",
        "selection": "home",
    }

    html = build_preview_html(snapshot)

    assert "胜平负 - 主队（旧算法记录）" in html
    assert "命中 0 · 未中 0 · 走水 0 · 命中率 —" in html


def test_preview_distinguishes_missing_historical_pick_from_no_pick():
    snapshot = _snapshot()
    snapshot["finished"]["matches"][0].pop("closing_match_decision")

    html = build_preview_html(snapshot)

    assert "历史首选未记录" in html
    assert "暂无可靠首选</td>" not in html


def test_preview_filters_have_accessible_stable_contract():
    html = build_preview_html(_snapshot())

    assert 'id="match-search"' in html
    assert 'type="search"' in html
    assert 'aria-label="搜索球队"' in html
    assert 'id="competition-filter"' in html
    assert 'aria-label="筛选赛事"' in html
    assert 'value="fifa_world_cup_2026"' in html
    assert 'value="csl_2026"' in html
    assert "applyFilters" in html


def test_preview_escapes_dynamic_values():
    snapshot = _snapshot()
    snapshot["matches"][0]["home_team"] = '<script>alert("x")</script>'

    html = build_preview_html(snapshot)

    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;alert" in html


def test_preview_surfaces_stale_quality_without_raw_error_details():
    snapshot = _snapshot()
    snapshot["data_quality"] = {
        "stale_sources": ["private-provider"],
        "source_errors": [{"error": "secret upstream detail"}],
    }

    html = build_preview_html(snapshot)

    assert "数据质量</span><strong>需注意</strong>" in html
    assert "private-provider" not in html
    assert "secret upstream detail" not in html


def test_preview_empty_upcoming_state_is_clear():
    snapshot = _snapshot()
    snapshot["matches"] = []

    html = build_preview_html(snapshot)

    assert "当前没有待开赛场次。" in html
    assert "待开赛</span><strong>0</strong>" in html


def test_preview_keeps_responsive_layout_and_table_scroll():
    html = build_preview_html(_snapshot())

    assert "@media (max-width:820px)" in html
    assert "@media (max-width:520px)" in html
    assert '.table-wrap { overflow:auto; }' in html
    assert 'name="viewport"' in html


def test_previous_snapshot_cannot_reintroduce_legacy_content():
    previous = _snapshot()
    previous["matches"][0]["signals"][0]["sentinel"] = "previous-grade-sentinel"

    html = build_preview_html(_snapshot(), previous_snapshot=previous)

    assert "previous-grade-sentinel" not in html


def test_write_preview_creates_parent_directory_and_file():
    with TemporaryDirectory() as tmp:
        output = Path(tmp) / "nested" / "preview.html"

        write_preview(_snapshot(), output)

        assert output.exists()
        assert "本场首选" in output.read_text(encoding="utf-8")
