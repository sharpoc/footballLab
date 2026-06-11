from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.preview import build_preview_html, write_preview


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "20260608T000000Z-live",
            "observed_at": "2026-06-08T00:00:00+00:00",
            "policy": {
                "policy_reason": "default",
                "interval_seconds": 86400,
                "next_due_at": "2026-06-08T12:00:00+00:00",
            },
            "quota": {"theoddsapi": {"remaining": 494, "used": 6}},
            "stale_sources": [],
            "source_errors": [],
        },
        "counts": {"fixtures": 104, "matches": 1, "odds_events": 1},
        "data_quality": {
            "missing_odds": [],
            "missing_elo": [],
            "time_mismatches": ["Brazil vs Haiti"],
        },
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "odds_updated_at": "2026-06-08T03:30:00+00:00",
                "refresh_plan": {
                    "next_update_at": "2026-06-11T17:30:00+00:00",
                    "policy_reason": "pre_90m_lineup_warmup",
                    "label": "T-1小时30分",
                    "description": "阵容/伤停预热",
                },
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "market": {
                    "1x2": {
                        "odds": {"home": 1.85, "draw": 3.3, "away": 4.0},
                        "market_probs": {"home": 0.57, "draw": 0.25, "away": 0.18},
                    }
                },
                "signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "grade": "A",
                        "ev": 0.052,
                        "edge": 0.041,
                        "status": "OK",
                    }
                ],
            }
        ],
    }


def test_build_preview_html_renders_research_ledger_surface():
    html = build_preview_html(_snapshot())

    assert "2026 世界杯" in html
    assert "研究台账" in html
    assert "仅用于研究分析，不构成投注建议" in html
    assert "墨西哥 对 南非" in html
    assert "2026 年 6 月 12 日 星期五" in html
    assert "03:00" in html
    assert "最后更新<br>2026 年 6 月 8 日 星期一 08:00" in html
    assert "最后更新：</strong><br>2026 年 6 月 8 日 星期一 08:00" in html
    assert "2026-06-08T00:00:00+00:00" not in html
    assert '<th scope="col">更新</th>' in html
    assert '<th scope="col">下次更新</th>' in html
    assert '<th scope="col">预测结果</th>' in html
    assert 'class="prediction-pill prediction-pending">待赛</span>' in html
    assert "赔率源更新" in html
    assert "11:30" in html
    assert "01:30" in html
    assert "T-1小时30分" in html
    assert "阵容/伤停预热" in html
    assert "胜平负 - 主队" in html
    assert "+4.1%" in html
    assert 'class="grade-pill grade-a grade-priority"' in html
    assert ".grade-s" in html
    assert ".grade-a" in html
    assert ".grade-b" in html
    assert ".grade-c" in html
    assert ".grade-d" in html
    assert "模型概率高于去水后的市场概率。" in html
    assert "方法说明" in html
    assert "数据源健康" in html
    assert "更新规则" in html
    assert "按每场比赛独立调度" in html
    assert "常规：每天 1 次" in html
    assert "临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-25分钟" in html
    assert "低额度：每天 1 次，并保留 T-90 / T-55 / T-25" in html
    assert "赛前 7 天内" not in html
    assert "当前规则：常规" in html
    assert "下次计划：2026 年 6 月 8 日 星期一 20:00" in html
    assert "注意事项" in html
    assert "Research Ledger" not in html
    assert "Research only, not betting advice." not in html
    assert "开赛 (北京时间)" in html
    assert "开赛 (UTC)" not in html
    assert "下注金额" not in html
    assert "stake" not in html.lower()
    assert "bet amount" not in html.lower()
    assert "bankroll" not in html.lower()


def test_write_preview_creates_parent_directory_and_file():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "preview.html"

        write_preview(_snapshot(), out)

        assert out.exists()
        assert "研究台账" in out.read_text(encoding="utf-8")


def test_build_preview_html_does_not_expose_raw_operational_details():
    snapshot = _snapshot()
    snapshot["run"] = {
        "run_id": "internal-run-abc-999",
        "quota": {"ultra-private-feed": {"remaining": 777777, "used": 123456}},
        "stale_sources": ["ultra-private-feed"],
        "source_errors": [
            {"source": "ultra-private-feed", "error": "TimeoutError: secret-ish upstream detail"}
        ],
    }
    snapshot["data_quality"] = {
        "source_errors": [
            {"source": "ultra-private-feed", "error": "TimeoutError: secret-ish upstream detail"}
        ],
        "missing_odds": ["Private Team vs Hidden Team"],
        "missing_elo": [],
        "time_mismatches": ["Internal fixture mismatch detail"],
    }

    html = build_preview_html(snapshot)

    assert "数据源健康" in html
    assert "数据质量：需关注" in html
    assert "赔率源：需关注" in html
    assert "赛程：可用" in html
    assert "Elo 评级：可用" in html
    assert "输入检查：需关注" in html
    assert "缺失赔率：1" in html
    assert "时间核对：1" in html
    assert "internal-run-abc-999" not in html
    assert "777777" not in html
    assert "123456" not in html
    assert "ultra-private-feed" not in html
    assert "TimeoutError: secret-ish upstream detail" not in html
    assert "Private Team vs Hidden Team" not in html
    assert "Internal fixture mismatch detail" not in html


def test_build_preview_html_escapes_dynamic_values():
    snapshot = _snapshot()
    snapshot["matches"][0]["home_team"] = 'Mexico <script>alert("x")</script>'
    snapshot["matches"][0]["away_team"] = 'South Africa" data-break="1'
    snapshot["matches"][0]["stage"] = '<img src=x onerror="alert(1)">'
    snapshot["matches"][0]["group"] = 'Group A"><script>alert(2)</script>'
    snapshot["run"] = {
        "run_id": "run-x",
        "source_errors": [
            {"source": 'feed"><script>alert(3)</script>', "error": '<script>alert(4)</script>'}
        ],
    }

    html = build_preview_html(snapshot)
    lower_html = html.lower()

    assert "<img" not in lower_html
    assert 'Mexico <script>alert("x")</script>' not in html
    assert 'Group A"><script>alert(2)</script>' not in html
    assert 'feed"><script>alert(3)</script>' not in html
    assert "<script>alert(4)</script>" not in html
    assert 'data-break="1' not in html
    assert 'onerror="alert(1)"' not in html
    assert "Mexico &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
    assert "South Africa&quot; data-break=&quot;1" in html


def test_build_preview_html_renders_empty_signal_state():
    snapshot = _snapshot()
    snapshot["matches"][0]["signals"] = []

    html = build_preview_html(snapshot)

    assert "暂无研究信号" in html


def test_build_preview_html_includes_filter_dom_accessibility_contract():
    html = build_preview_html(_snapshot())

    assert 'data-filter="strong"' in html
    assert ">强信号 (S/A)</button>" in html
    assert ">弱信号 (C/D)</button>" in html
    assert 'id="ledger-search"' in html
    assert 'aria-pressed="true"' in html
    assert 'aria-pressed="false"' in html
    assert "<caption>研究信号台账</caption>" in html
    assert '<th scope="col">对阵</th>' in html


def test_build_preview_html_includes_expandable_signal_detail_rows():
    html = build_preview_html(_snapshot())

    assert 'class="signal-row"' in html
    assert 'role="button"' in html
    assert 'tabindex="0"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="signal-detail-0"' in html
    assert 'data-detail-target="signal-detail-0"' in html
    assert '<tr class="signal-detail-row" id="signal-detail-0" hidden>' in html
    assert "<h3>分析详情</h3>" in html
    assert "<dt>核心判断</dt><dd>模型概率高于去水后的市场概率。</dd>" in html
    assert "<dt>模型与市场</dt><dd>模型 61.0%，市场 57.0%，Edge +4.1%</dd>" in html
    assert "toggleSignalDetail" in html
    assert "keydown" in html


def test_build_preview_html_includes_recent_change_summary():
    previous = _snapshot()
    previous["matches"][0]["market"]["1x2"]["odds"]["home"] = 2.0
    current = deepcopy(previous)
    current["snapshot_at"] = "2026-06-08T12:00:00+00:00"
    current["matches"][0]["market"]["1x2"]["odds"]["home"] = 1.85
    current["matches"][0]["signals"][0]["grade"] = "S"
    current["matches"][0]["signals"][0]["ev"] = 0.092

    html = build_preview_html(current, previous_snapshot=previous)

    assert 'class="change-summary"' not in html
    assert "最近变化</h2>" not in html
    assert 'class="signal-change signal-change-strong"' in html
    assert "本轮变化" in html
    assert "等级 A → S" in html
    assert "EV +5.2% → +9.2%" in html
    assert "赔率 2.00 → 1.85" in html
    assert "<dt>本轮变化</dt><dd>等级 A → S；EV +5.2% → +9.2%；赔率 2.00 → 1.85</dd>" in html


def test_build_preview_html_shows_finished_prediction_result_in_reason_column():
    snapshot = _snapshot()
    snapshot["matches"][0]["result"] = {"status": "finished", "home_score": 2, "away_score": 0}

    html = build_preview_html(snapshot)

    assert 'class="prediction-pill prediction-hit">命中</span>' in html
    assert 'class="prediction-result prediction-result-hit"' in html
    assert "<strong>预测结果：命中</strong>" in html
    assert "赛果：墨西哥 2-0 南非；方向：主胜" in html
    assert "<dt>赛后验证</dt><dd>命中；赛果：墨西哥 2-0 南非；方向：主胜</dd>" in html


def test_build_preview_html_renders_stronger_signal_grade_badges():
    previous = _snapshot()
    current = deepcopy(previous)
    current["matches"][0]["signals"][0]["grade"] = "S"

    html = build_preview_html(current, previous_snapshot=previous)

    assert 'class="grade-pill grade-s grade-priority"' in html
    assert ".grade-priority" in html
    assert "letter-spacing" not in html


def test_build_preview_html_keeps_mobile_table_scroll_inside_ledger_panel():
    html = build_preview_html(_snapshot())

    assert 'class="ledger-panel"' in html
    assert ".ledger-panel { min-width: 0; }" in html


def test_build_preview_html_places_context_cards_below_full_width_ledger():
    html = build_preview_html(_snapshot())

    assert 'class="content-grid"' in html
    assert html.index('class="ledger-panel"') < html.index('class="right-rail"')
    assert "grid-template-columns: minmax(0, 1fr) 340px;" not in html
    assert "grid-template-columns: minmax(0, 1fr);" in html
    assert "grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));" in html
