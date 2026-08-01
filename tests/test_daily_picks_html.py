from __future__ import annotations

import re

from worldcup.daily_picks_html import build_daily_picks_html


def _payload():
    return {
        "schema_version": 1,
        "cycle": {
            "timezone": "Asia/Shanghai",
            "start_at": "2026-07-31T18:00:00+08:00",
            "end_at": "2026-08-01T18:00:00+08:00",
            "label": "2026年7月31日 18:00 至 8月1日 18:00（北京时间）",
        },
        "generated_at": "2026-07-31T12:00:00+00:00",
        "data_as_of": "2026-07-31T11:59:00+00:00",
        "candidate_count": 2,
        "selected_count": 2,
        "singles": [
            {
                "match_id": "<unsafe>",
                "competition_label": "中超 & <script>",
                "home_team": "A <b>",
                "away_team": "B & C",
                "market": "1X2",
                "selection": "home",
                "prediction_probability": 0.72,
                "reference_odds": 1.80,
            }
        ],
        "parlay_2": [],
        "parlay_3": [],
        "coverage": [
            {"name": "中超", "status": "enabled", "reason": "已验证真实数据链路"},
            {"name": "英冠", "status": "unsupported", "reason": "未接入：缺少真实数据证据"},
        ],
        "degradation_reasons": ["fewer_than_4_candidates"],
    }


def test_html_escapes_dynamic_teams_and_competition_labels():
    html = build_daily_picks_html(_payload())
    assert "&lt;unsafe&gt;" in html
    assert "中超 &amp; &lt;script&gt;" in html
    assert "A &lt;b&gt;" in html
    assert "B &amp; C" in html
    assert "<script>" not in html


def test_html_shows_cycle_counts_coverage_and_research_boundary():
    html = build_daily_picks_html(_payload())
    for text in (
        "每日精选",
        "18:00",
        "候选比赛 2 场",
        "最多 4 场",
        "2 串 1",
        "3 串 1",
        "覆盖状态",
        "未接入：缺少真实数据证据",
        "独立性近似组合分数",
        "组合概率研究",
        "仅用于研究分析，不构成投注建议。",
        'href="/preview"',
        'class="primary-nav-item active" aria-current="page" href="/daily-picks">每日精选</a>',
    ):
        assert text in html


def test_html_does_not_publish_money_ev_or_forbidden_confidence_claims():
    html = build_daily_picks_html(_payload())
    lowered = html.lower()
    structured_forbidden = (
        r'"ev"',
        r"'ev'",
        r'\bev\s*=',
        r'\bedge\s*=',
        r'data-(?:ev|edge)=',
        r'class="[^"]*\b(?:ev|edge)\b',
        r'>\s*(?:ev|edge)\s*<',
    )
    for pattern in structured_forbidden:
        assert re.search(pattern, lowered) is None
    for forbidden in ("金额", "下注", "稳胆", "必胜", "执行建议"):
        assert forbidden not in html
