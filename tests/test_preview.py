from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ledger_html import _trend_text
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
                "match_decision": {
                    "schema_version": 1,
                    "label": "HIGH_CONFIDENCE_LEAN",
                    "market": "DNB",
                    "selection": "home",
                    "line": 0.0,
                    "odds": 1.55,
                    "p_hit_safe": 0.59,
                    "p_no_loss_safe": 0.73,
                    "edge_safe": 0.01,
                    "ev_safe": 0.02,
                    "signal_source": "lean",
                    "reasons": ["highest_safe_probability"],
                    "risks": ["no_official_edge"],
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
    assert "数据更新 2026 年 6 月 8 日 星期一 08:00" in html
    assert "刷新成功" in html
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
    assert "本场首选" in html
    assert "高胜率倾向 · 平手盘 - 主队 · 安全胜率 59.0% · 不亏概率 73.0%" in html
    assert "+4.1%" in html
    assert 'class="grade-pill grade-a"' in html
    assert "grade-priority" not in html
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
    assert "临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-35分钟 / T-25分钟" in html
    assert "低额度：每天 1 次，并保留 T-90 / T-55 / T-35 / T-25" in html
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

    assert "暂无价值分歧" in html


def test_build_preview_html_includes_filter_dom_accessibility_contract():
    html = build_preview_html(_snapshot())

    assert 'class="view-tabs"' in html
    assert 'aria-label="视图切换"' in html
    assert 'data-view-filter="live"' in html
    assert 'data-view-filter="history"' in html
    assert ">实时方向</button>" in html
    assert ">历史回顾</button>" in html
    assert 'data-view-panel="live"' in html
    assert 'data-view-panel="history"' in html
    assert 'data-date-filter="all"' in html
    assert 'data-date-filter="today"' in html
    assert 'data-date-filter="tomorrow"' in html
    assert 'data-date-filter="next3"' in html
    assert 'data-date-filter="next7"' in html
    assert 'data-date-filter="custom"' in html
    assert ">全部</button>" in html
    assert ">今日</button>" in html
    assert ">明日</button>" in html
    assert ">未来3天</button>" in html
    assert ">未来7天</button>" in html
    assert ">选择日期</button>" in html
    assert 'id="date-picker"' in html
    assert '<option value="2026-06-12">6/12 周五 · 1场 · 1分歧</option>' in html
    assert 'data-date="2026 年 6 月 12 日 星期五"' in html
    assert 'data-date-iso="2026-06-12"' in html
    assert 'id="league-filter"' in html
    assert '<option value="all">全部赛事</option>' in html
    assert '<option value="fifa_world_cup_2026">2026 世界杯</option>' in html
    assert 'data-league="fifa_world_cup_2026"' in html
    assert "function getSelectedLeague()" in html
    assert "var selectedLeague = getSelectedLeague();" in html
    assert 'data-filter="strong"' in html
    assert ">价值分歧 (S/A)</button>" in html
    assert ">低价值分歧 (C/D)</button>" in html
    assert ">强信号 (S/A)</button>" not in html
    assert 'id="ledger-search"' in html
    assert 'aria-pressed="true"' in html
    assert 'aria-pressed="false"' in html
    assert "<caption>价值分歧台账</caption>" in html
    assert '<th scope="col">对阵</th>' in html


def test_build_preview_html_uses_snapshot_competition_labels_and_filter_values():
    snapshot = _snapshot()
    snapshot["matches"][0]["competition"] = {
        "id": "csl_2026",
        "name": "中超 2026",
        "kind": "domestic_league",
        "country": "CN",
        "season": "2026",
        "source": "theoddsapi",
        "fixture_source": "odds_event_only",
        "rating_policy": "club_rating_pending",
    }

    html = build_preview_html(snapshot)

    assert "中超 2026" in html
    assert 'data-league="csl_2026"' in html
    assert '<option value="csl_2026">中超 2026</option>' in html
    assert "club_rating_pending" not in html
    assert "下注金额" not in html


def _snapshot_with_two_matches_many_signals() -> dict:
    snapshot = deepcopy(_snapshot())
    first = snapshot["matches"][0]
    first["kickoff_at_utc"] = "2026-06-12T19:00:00+00:00"
    first["stage"] = "Matchday 2"
    first["group"] = "Group B"
    first["home_team"] = "Canada"
    first["away_team"] = "Bosnia and Herzegovina"
    first["signals"] = [
        {"market_type": "1X2_90min", "selection": "home", "grade": "S", "ev": 0.246, "edge": 0.154, "status": "OK"},
        {"market_type": "1X2_90min", "selection": "draw", "grade": "C", "ev": -0.282, "edge": -0.069, "status": "OK"},
        {"market_type": "1X2_90min", "selection": "away", "grade": "C", "ev": -0.428, "edge": -0.086, "status": "OK"},
        {"market_type": "AsianHandicap_90min", "selection": "away", "line": 0.5, "grade": "C", "ev": -0.308, "edge": None, "status": "OK"},
        {"market_type": "OverUnder_90min", "selection": "over", "line": 2.5, "grade": "C", "ev": -0.024, "edge": 0.013, "status": "OK"},
        {"market_type": "OverUnder_90min", "selection": "under", "line": 2.5, "grade": "C", "ev": -0.075, "edge": -0.013, "status": "OK"},
        {"market_type": "AsianHandicap_90min", "selection": "home", "line": -0.5, "grade": "B", "ev": 0.052, "edge": None, "status": "OK"},
    ]
    second = deepcopy(first)
    second["kickoff_at_utc"] = "2026-06-13T01:00:00+00:00"
    second["group"] = "Group D"
    second["home_team"] = "United States"
    second["away_team"] = "Paraguay"
    second["signals"][0]["ev"] = 0.56
    second["signals"][0]["edge"] = 0.142
    snapshot["matches"] = [first, second]
    snapshot["counts"]["matches"] = 2
    return snapshot


def test_build_preview_html_defaults_to_match_grouped_ledger():
    html = build_preview_html(_snapshot_with_two_matches_many_signals())

    assert 'data-mode-filter="match"' in html
    assert 'data-mode-filter="signal"' in html
    assert 'data-mode-panel="match"' in html
    assert 'data-mode-panel="signal" hidden' in html
    assert "<span>按比赛查看</span>" in html
    assert "本日比赛：2 场 / 14 条价值分歧" in html
    assert html.count('class="match-row"') == 2
    assert html.count('class="match-detail-row"') == 2
    assert 'data-signal-count="7"' in html
    assert 'data-grade-buckets="strong watch weak"' in html
    assert "展开 7条" in html
    assert "加拿大 对 波黑" in html
    assert "美国 对 巴拉圭" in html


def test_build_preview_html_uses_workbench_signal_layout():
    html = build_preview_html(_snapshot_with_two_matches_many_signals())

    assert 'class="workbench-shell premium-intelligence-workbench"' in html
    assert 'class="date-strip"' in html
    assert 'class="date-card active" data-date-filter="all"' in html
    assert 'class="date-card" data-date-filter="2026-06-13"' in html
    assert "全部 日期" in html
    assert "2场 · 14分歧" in html
    assert 'class="ledger-workbench"' in html
    assert 'class="match-list-panel"' in html
    assert 'class="signal-detail-panel"' in html
    assert 'class="match-list-row active"' in html
    assert 'data-workbench-match-target="workbench-match-0"' in html
    assert 'class="workbench-detail active" id="workbench-match-0"' in html
    assert "加拿大 vs 波黑" in html
    assert "价值分歧" in html
    assert "分歧 EDGE" in html
    assert "<span>本场首选</span><strong>高胜率倾向 · 平手盘 - 主队 · 安全胜率 59.0% · 不亏概率 73.0%</strong>" in html
    assert "<span>下一次更新</span><strong>2026 年 6 月 12 日 星期五 01:30</strong>" in html
    assert (
        "<tr><th>开赛时间</th><th>对阵</th><th>组别</th>"
        "<th>价值分歧</th><th>安全概率</th></tr>"
    ) in html
    match_list_row = html[
        html.index('<tr class="match-list-row active"') : html.index(
            "</tr>", html.index('<tr class="match-list-row active"')
        )
    ]
    visible_match_list_row = match_list_row[match_list_row.index('"><td') :]
    decision_row = html[
        html.index('<tr class="match-list-decision-row active"') : html.index(
            "</tr>", html.index('<tr class="match-list-decision-row active"')
        )
    ]
    assert (
        '<td class="match-kickoff-cell"><span>6/13</span><strong>03:00</strong></td>'
        in match_list_row
    )
    assert match_list_row.index('<td class="match-kickoff-cell"') < match_list_row.index(
        '<td><strong><span class="team-matchup"'
    ) < match_list_row.index(
        "<td>2026 世界杯 · 小组赛第 2 轮 | B 组</td>"
    )
    assert "高胜率倾向 · 平手盘 - 主队" not in visible_match_list_row
    assert 'data-workbench-decision-for="workbench-match-0"' in decision_row
    assert '<td colspan="5">' in decision_row
    assert '<span>本场首选</span>' in decision_row
    assert "高胜率倾向 · 平手盘 - 主队 · 安全胜率 59.0% · 不亏概率 73.0%" in decision_row
    assert (
        "<th>市场 / 盘口</th><th>分歧等级</th><th>预测</th><th>模型概率</th>"
        "<th>市场概率</th><th>EV / EDGE</th><th>新鲜度</th><th>分歧原因</th>"
    ) in html
    assert (
        "<td>胜平负 - 主队</td>"
        '<td><span class="grade-pill grade-s">S</span></td>'
        '<td><span class="prediction-pill prediction-pending">待赛</span></td>'
    ) in html
    assert ".grade-pill" in html
    assert "height: 24px;" in html
    assert "width: 30px;" in html
    assert "grade-priority" not in html


def test_build_preview_html_renders_workbench_flags_and_compact_interaction():
    html = build_preview_html(_snapshot_with_two_matches_many_signals())

    assert 'class="team-matchup"' in html
    assert '<span class="team-flag" aria-hidden="true">🇨🇦</span>' in html
    assert '<span class="team-flag" aria-hidden="true">🇧🇦</span>' in html
    assert '<span class="team-flag" aria-hidden="true">🇺🇸</span>' in html
    assert '<span class="team-flag" aria-hidden="true">🇵🇾</span>' in html
    assert "加拿大 vs 波黑" in html
    assert "美国 vs 巴拉圭" in html
    assert "Bosnia and Herzegovina · 信号明细" not in html
    assert "United States vs 巴拉圭" not in html
    assert ".match-list-scroll" in html
    assert "overflow-x: hidden;" in html
    assert "width: 22%;" in html
    assert 'class="workbench-signal-row" role="button" tabindex="0" aria-expanded="false"' in html
    assert 'class="workbench-inline-detail-row" id="workbench-signal-detail-0-0" hidden' in html
    assert "function setWorkbenchSignalDetail" in html
    assert "盘口方向" in html
    assert "信号原因" in html


def test_build_preview_html_renders_premium_stale_signal_state():
    snapshot = _snapshot_with_two_matches_many_signals()
    snapshot["run"]["stale_sources"] = ["theoddsapi"]
    snapshot["data_quality"]["stale_sources"] = ["theoddsapi"]

    html = build_preview_html(snapshot)

    assert 'class="workbench-signal-row is-stale"' in html
    assert 'class="freshness-badge freshness-stale">过期</span>' in html
    assert "盘口数据已过期，等待下一轮刷新。" in html
    assert 'class="workbench-warning-strip"' in html
    assert "部分信号来自缓存数据，盘口数据已过期，等待下一轮刷新。" in html


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

    assert 'class="grade-pill grade-s"' in html
    assert "grade-priority" not in html
    assert "height: 24px;" in html
    assert "width: 30px;" in html
    assert "letter-spacing: 0;" in html


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


def _snapshot_with_finished_for_preview() -> dict:
    snapshot = deepcopy(_snapshot())
    match = snapshot["matches"][0]
    match["home_canonical"] = "mexico"
    match["away_canonical"] = "south_africa"
    match["odds_trend"] = {
        "1x2": {
            "home": [
                ["2026-06-10T00:00:00+00:00", 1.85],
                ["2026-06-11T18:00:00+00:00", 1.78],
            ]
        }
    }
    snapshot["finished"] = {
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "stage": "Matchday 1",
                "group": "Group A",
                "result": {"home_score": 2, "away_score": 0},
                "closing_snapshot_at": "2026-06-11T18:00:00+00:00",
                "closing_signals": [
                    {
                        "market_type": "1X2_90min",
                        "selection": "home",
                        "line": None,
                        "grade": "S",
                        "odds": 1.78,
                        "prediction": {"label": "命中", "detail": "赛果：墨西哥 2-0 南非；方向：主胜"},
                    }
                ],
                "odds_trend": match["odds_trend"],
            }
        ],
        "tally": {
            "S": {"hit": 1, "miss": 0, "push": 0},
            "A": {"hit": 0, "miss": 0, "push": 0},
        },
        "skipped_no_closing": 0,
    }
    return snapshot


def test_preview_renders_record_card_and_finished_section():
    html = build_preview_html(_snapshot_with_finished_for_preview())

    assert '<link rel="icon" href="data:,">' in html
    assert "S 级战绩" in html
    assert "命中 1 · 未中 0 · 走水 0 · 命中率 100%" in html
    assert "已完赛战绩" in html
    assert "2 - 0" in html
    assert "history-match-0" in html
    assert "复盘样本仍偏小" in html
    assert "仅作为观察" in html


def test_preview_renders_trend_sparkline_in_detail():
    html = build_preview_html(_snapshot_with_finished_for_preview())

    assert "trend-spark" in html
    assert "<polyline" in html
    assert "赔率走势" in html
    assert "1.85" in html and "1.78" in html


def test_preview_trend_text_includes_market_line_when_present():
    text = _trend_text(
        [
            ["2026-06-10T00:00:00+00:00", 1.85, 2.5],
            ["2026-06-11T18:00:00+00:00", 1.78, 3.5],
        ]
    )

    assert "2.5" in text
    assert "3.5" in text


def test_preview_renders_history_with_workbench_interaction_contract():
    html = build_preview_html(_snapshot_with_finished_for_preview())

    assert 'data-view-panel="history"' in html
    assert 'class="workbench-shell history-workbench-shell premium-intelligence-workbench"' in html
    assert 'class="date-card active" data-date-filter="all"' in html
    assert 'class="match-list-row active history-match-row"' in html
    assert 'data-workbench-match-target="history-match-0"' in html
    assert 'class="workbench-detail active history-workbench-detail" id="history-match-0"' in html
    assert 'data-workbench-market-filter="all"' in html
    assert 'data-workbench-market-filter="1x2"' in html
    assert 'class="workbench-signal-row history-signal-row" role="button"' in html
    assert 'data-workbench-signal-detail="history-signal-detail-0-0"' in html
    assert "预测状态</dt><dd>命中" in html
    assert "已完赛战绩" in html
    assert "closing（开球前最后一轮）口径" in html
    assert 'class="finished-table"' not in html
    assert 'class="finished-row"' not in html


def test_preview_renders_finished_closing_match_decision():
    snapshot = _snapshot_with_finished_for_preview()
    snapshot["finished"]["matches"][0]["closing_match_decision"] = {
        "schema_version": 1,
        "label": "HIGH_CONFIDENCE_LEAN",
        "market": "DNB",
        "selection": "home",
        "line": 0.0,
        "p_hit_safe": 0.59,
        "p_no_loss_safe": 0.73,
    }

    html = build_preview_html(snapshot)

    assert "收盘首选" in html
    assert "高胜率倾向 · 平手盘 - 主队 · 安全胜率 59.0% · 不亏概率 73.0%" in html


def test_preview_surfaces_missing_closing_review_count():
    snapshot = _snapshot_with_finished_for_preview()
    snapshot["finished"]["skipped_no_closing"] = 1

    html = build_preview_html(snapshot)

    assert "缺少 closing 记录：1 场" in html


def test_preview_surfaces_enrichment_error_count_without_raw_details():
    snapshot = _snapshot()
    snapshot["data_quality"]["enrichment_errors"] = [
        {"source": "site_enrichment", "error": "ValueError: private enrichment detail"}
    ]

    html = build_preview_html(snapshot)

    assert "富化异常：1" in html
    assert "private enrichment detail" not in html


def test_preview_tolerates_missing_finished_and_trend():
    html = build_preview_html(_snapshot())

    assert "已完赛战绩" not in html
    assert "trend-spark" not in html
