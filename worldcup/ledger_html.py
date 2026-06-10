from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from worldcup.ledger import (
    BEIJING_TZ,
    WEEKDAY_LABELS,
    build_summary_metrics,
    derive_quality_status,
    project_signal_rows,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _slug(value: Any) -> str:
    return _text(value).lower().replace(" ", "-")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _format_snapshot_time(value: Any) -> str:
    if not value:
        return ""
    raw = str(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    display = parsed.astimezone(BEIJING_TZ)
    weekday = WEEKDAY_LABELS[display.weekday()]
    return f"{display.year} 年 {display.month} 月 {display.day} 日 {weekday} {display:%H:%M}"


def _quality_values(snapshot: dict[str, Any], key: str) -> list[Any]:
    data_quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    if key in data_quality:
        return _as_list(data_quality.get(key))
    return _as_list(run.get(key))


def _metric_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return "无"
        return ", ".join(f"{_text(key)}: {_text(count)}" for key, count in value.items())
    return _text(value)


def _render_summary(snapshot: dict[str, Any]) -> str:
    metrics = build_summary_metrics(snapshot)
    preferred = [
        "upcoming_matches",
        "strong_signals",
        "watch_signals",
        "weak_signals",
        "stale_sources",
        "overall_quality",
        "grade_counts",
    ]
    cards = []
    for key in preferred:
        metric = metrics.get(key)
        if not metric:
            continue
        tone = _slug(metric.get("tone", "neutral"))
        cards.append(
            "<div class=\"metric-card\" data-tone=\"{tone}\">"
            "<span>{label}</span>"
            "<strong>{value}</strong>"
            "</div>".format(
                tone=tone,
                label=_text(metric.get("label", key)),
                value=_metric_value(metric.get("value")),
            )
        )
    return "<section class=\"summary-grid\">{}</section>".format("".join(cards))


def _render_controls() -> str:
    return """
    <section class="ledger-controls" aria-label="台账筛选">
      <div class="filter-group" role="group" aria-label="等级筛选">
        <button type="button" class="filter-button active" data-filter="all" aria-pressed="true">全部</button>
        <button type="button" class="filter-button" data-filter="strong" aria-pressed="false">强信号 (S/A)</button>
        <button type="button" class="filter-button" data-filter="watch" aria-pressed="false">观察 (B)</button>
        <button type="button" class="filter-button" data-filter="weak" aria-pressed="false">弱信号 (C/D)</button>
      </div>
      <label class="search-label">
        <span>搜索</span>
        <input type="search" id="ledger-search" placeholder="球队、盘口、等级" autocomplete="off">
      </label>
    </section>
    """


def _grade_bucket(grade: Any) -> str:
    normalized = str(grade or "").upper()
    if normalized in {"S", "A"}:
        return "strong"
    if normalized == "B":
        return "watch"
    if normalized in {"C", "D"}:
        return "weak"
    return "all"


def _grade_class(grade: Any) -> str:
    normalized = str(grade or "").upper()
    if normalized in {"S", "A", "B", "C", "D"}:
        priority = " grade-priority" if normalized in {"S", "A"} else ""
        return f"grade-{normalized.lower()}{priority}"
    return "grade-unknown"


def _row_search_text(row: dict[str, Any]) -> str:
    recent_change = row.get("recent_change") or {}
    parts = [
        row.get("matchup", ""),
        row.get("source_matchup", ""),
        row.get("source_home_team", ""),
        row.get("source_away_team", ""),
        row.get("kickoff_at_utc", ""),
        row.get("kickoff_date", ""),
        row.get("kickoff_time", ""),
        row.get("updated_time", ""),
        row.get("updated_label", ""),
        row.get("market_label", ""),
        row.get("model_prob", ""),
        row.get("market_prob", ""),
        row.get("ev", ""),
        row.get("edge", ""),
        row.get("grade", ""),
        row.get("freshness", ""),
        row.get("explanation", ""),
        recent_change.get("detail", ""),
    ]
    return " ".join(str(part) for part in parts).lower()


def _render_signal_detail(row: dict[str, Any]) -> str:
    detail_items = row.get("detail_items") or []
    items = []
    for item in detail_items:
        items.append(
            "<div><dt>{label}</dt><dd>{value}</dd></div>".format(
                label=_text(item.get("label")),
                value=_text(item.get("value")),
            )
        )
    return (
        "<div class=\"signal-detail\">"
        "<h3>分析详情</h3>"
        "<dl class=\"detail-grid\">{items}</dl>"
        "<p class=\"detail-note\">这些内容只解释研究信号来源，不构成投注建议。</p>"
        "</div>"
    ).format(items="".join(items))


def _render_signal_change(row: dict[str, Any]) -> str:
    recent_change = row.get("recent_change") or {}
    detail = recent_change.get("detail")
    if not detail:
        return ""
    tone = _slug(recent_change.get("tone", "neutral"))
    return (
        "<span class=\"signal-change signal-change-{tone}\">"
        "<strong>本轮变化</strong>{detail}"
        "</span>"
    ).format(
        tone=_text(tone),
        detail=_text(detail),
    )


def _render_prediction_result(row: dict[str, Any]) -> str:
    result = row.get("prediction_result") or {}
    if not result:
        return ""
    status = _slug(result.get("status", "unknown"))
    return (
        "<span class=\"prediction-result prediction-result-{status}\">"
        "<strong>预测结果：{label}</strong>{detail}"
        "</span>"
    ).format(
        status=_text(status),
        label=_text(result.get("label")),
        detail=_text(result.get("detail")),
    )


def _render_signal_reason(row: dict[str, Any]) -> str:
    return "{result}{change}<span class=\"signal-why\">{why}</span>".format(
        result=_render_prediction_result(row),
        change=_render_signal_change(row),
        why=_text(row.get("explanation")),
    )


def _render_signal_table(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> str:
    rows = project_signal_rows(snapshot, previous_snapshot=previous_snapshot)
    if not rows:
        return """
        <section class="ledger-table-wrap">
          <div class="empty-state">
            <h2>暂无研究信号</h2>
            <p>当前快照没有达到阈值的信号行。</p>
          </div>
        </section>
        """

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("kickoff_date") or "日期暂不可用")].append(row)

    table_rows = []
    detail_index = 0
    for kickoff_date, date_rows in grouped.items():
        table_rows.append(
            "<tr class=\"date-row\"><th colspan=\"10\">{}</th></tr>".format(_text(kickoff_date))
        )
        for row in date_rows:
            grade = row.get("grade", "")
            grade_bucket = _grade_bucket(grade)
            grade_class = _grade_class(grade)
            search_text = _row_search_text(row)
            detail_id = f"signal-detail-{detail_index}"
            reason = _render_signal_reason(row)
            table_rows.append(
                "<tr class=\"signal-row\" role=\"button\" tabindex=\"0\" "
                "aria-expanded=\"false\" aria-controls=\"{detail_id}\" "
                "data-detail-target=\"{detail_id}\" data-grade=\"{grade_bucket}\" "
                "data-search=\"{search}\">"
                "<td><strong>{matchup}</strong><span>{stage_group}</span></td>"
                "<td>{kickoff}</td>"
                "<td><strong>{updated}</strong><span>{updated_label}</span></td>"
                "<td>{market}</td>"
                "<td>{model_prob}</td>"
                "<td>{market_prob}</td>"
                "<td><strong>{ev}</strong><span>{edge}</span></td>"
                "<td><span class=\"grade-pill {grade_class}\">{grade}</span></td>"
                "<td>{freshness}</td>"
                "<td>{why}</td>"
                "</tr>".format(
                    detail_id=_text(detail_id),
                    grade_bucket=_text(grade_bucket),
                    search=_text(search_text),
                    matchup=_text(row.get("matchup")),
                    stage_group=_text(row.get("stage_group")),
                    kickoff=_text(row.get("kickoff_time") or row.get("kickoff_at_utc")),
                    updated=_text(row.get("updated_time")),
                    updated_label=_text(row.get("updated_label")),
                    market=_text(row.get("market_label")),
                    model_prob=_text(row.get("model_prob")),
                    market_prob=_text(row.get("market_prob")),
                    ev=_text(row.get("ev")),
                    edge=_text(row.get("edge")),
                    grade_class=_text(grade_class),
                    grade=_text(grade),
                    freshness=_text(row.get("freshness")),
                    why=reason,
                )
            )
            table_rows.append(
                "<tr class=\"signal-detail-row\" id=\"{detail_id}\" hidden>"
                "<td colspan=\"10\">{detail}</td>"
                "</tr>".format(
                    detail_id=_text(detail_id),
                    detail=_render_signal_detail(row),
                )
            )
            detail_index += 1

    return """
    <section class="ledger-table-wrap">
      <table class="ledger-table">
        <caption>研究信号台账</caption>
        <thead>
          <tr>
            <th scope="col">对阵</th>
            <th scope="col">开赛 (北京时间)</th>
            <th scope="col">更新</th>
            <th scope="col">盘口</th>
            <th scope="col">模型概率</th>
            <th scope="col">市场概率</th>
            <th scope="col">EV / Edge</th>
            <th scope="col">等级</th>
            <th scope="col">新鲜度</th>
            <th scope="col">信号原因</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      <p class="no-results" hidden>没有符合当前筛选的信号。</p>
    </section>
    """.format(rows="\n".join(table_rows))


def _format_interval(seconds: Any) -> str:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return "未记录"
    if value % 86400 == 0:
        return f"{value // 86400} 天"
    if value % 3600 == 0:
        return f"{value // 3600} 小时"
    return f"{value} 秒"


def _policy_reason_label(reason: Any) -> str:
    labels = {
        "default": "常规",
        "pre_7d_window": "赛前 7 天内",
        "pre_3d_window": "赛前 3 天内",
        "pre_1d_window": "赛前 1 天内",
        "quota_low": "低额度",
    }
    return labels.get(str(reason or ""), "按当前调度策略")


def _render_update_policy(snapshot: dict[str, Any]) -> str:
    policy = (snapshot.get("run") or {}).get("policy") or {}
    reason_label = _policy_reason_label(policy.get("policy_reason"))
    interval_label = _format_interval(policy.get("interval_seconds"))
    next_due = _format_snapshot_time(policy.get("next_due_at"))
    next_due_html = (
        "<p><strong>下次计划：{}</strong></p>".format(_text(next_due))
        if next_due
        else "<p><strong>下次计划：待下一轮调度确认</strong></p>"
    )
    return """
    <section class="rail-card">
      <h2>更新规则</h2>
      <ul class="policy-list">
        <li>常规：24 小时</li>
        <li>赛前 7 天内：12 小时</li>
        <li>赛前 3 天内：6 小时</li>
        <li>赛前 1 天内：2 小时</li>
        <li>低额度：24 小时</li>
      </ul>
      <p><strong>当前规则：{reason}</strong></p>
      <p><strong>当前间隔：</strong>{interval}</p>
      {next_due}
    </section>
    """.format(
        reason=_text(reason_label),
        interval=_text(interval_label),
        next_due=next_due_html,
    )


def _render_source_health(snapshot: dict[str, Any]) -> str:
    quality = derive_quality_status(snapshot)
    stale_sources = _quality_values(snapshot, "stale_sources")
    source_errors = _quality_values(snapshot, "source_errors")
    missing_odds = _quality_values(snapshot, "missing_odds")
    missing_elo = _quality_values(snapshot, "missing_elo")
    time_mismatches = _quality_values(snapshot, "time_mismatches")
    counts = snapshot.get("counts") or {}
    matches = snapshot.get("matches") or []
    fixtures_available = bool(counts.get("fixtures") or matches)
    odds_attention = bool(source_errors or stale_sources or missing_odds)
    elo_attention = bool(missing_elo)
    input_attention = bool(source_errors or stale_sources or missing_odds or missing_elo or time_mismatches)

    return """
    <section class="rail-card">
      <h2>数据源健康</h2>
      <p><strong>数据质量：{quality}</strong></p>
      <p><strong>赔率源：{odds_feed}</strong></p>
      <p><strong>赛程：{fixtures}</strong></p>
      <p><strong>Elo 评级：{elo}</strong></p>
      <p><strong>输入检查：{input_checks}</strong></p>
      <ul class="health-counts">
        <li>源异常：{source_error_count}</li>
        <li>过期输入：{stale_count}</li>
        <li>缺失赔率：{missing_odds_count}</li>
        <li>缺失 Elo：{missing_elo_count}</li>
        <li>时间核对：{time_count}</li>
      </ul>
    </section>
    """.format(
        quality=_text(quality.get("label")),
        odds_feed="需关注" if odds_attention else "可用",
        fixtures="可用" if fixtures_available else "需关注",
        elo="需关注" if elo_attention else "可用",
        input_checks="需关注" if input_attention else "可用",
        source_error_count=_text(len(source_errors)),
        stale_count=_text(len(stale_sources)),
        missing_odds_count=_text(len(missing_odds)),
        missing_elo_count=_text(len(missing_elo)),
        time_count=_text(len(time_mismatches)),
    )


def _render_right_rail(snapshot: dict[str, Any]) -> str:
    snapshot_at = _format_snapshot_time(snapshot.get("snapshot_at"))
    return """
    <aside class="right-rail">
      <section class="rail-card">
        <h2>方法说明</h2>
        <p>用 Elo 与 Poisson 模型概率，对比去水后的市场概率。</p>
        <p>信号来自模型概率、EV、Edge 与盘口之间的差异。</p>
        <p>等级用于研究优先级排序，并会受输入新鲜度影响。</p>
      </section>
      {source_health}
      {update_policy}
      <section class="rail-card">
        <h2>注意事项</h2>
        <ul>
          <li>模型输出只作为研究信号，可能出错。</li>
          <li>数据过期、输入缺失或开赛时间不一致都会降低可信度。</li>
          <li>页面可能滞后于最新数据源。</li>
        </ul>
      </section>
      <section class="rail-card">
        <h2>更新时间</h2>
        <p><strong>最后更新：</strong><br>{snapshot_at}</p>
      </section>
    </aside>
    """.format(
        source_health=_render_source_health(snapshot),
        update_policy=_render_update_policy(snapshot),
        snapshot_at=_text(snapshot_at),
    )


def build_research_ledger_html(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> str:
    snapshot_at = _format_snapshot_time(snapshot.get("snapshot_at"))
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>2026 世界杯 | 研究台账</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #596674;
      --line: #dce3ea;
      --accent: #0f766e;
      --accent-soft: #e5f4f2;
      --warn: #9a6700;
      --error: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      margin-bottom: 18px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 32px; line-height: 1.15; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    h3 {{ margin: 14px 0 6px; font-size: 13px; text-transform: uppercase; color: var(--muted); }}
    p {{ margin: 0 0 10px; line-height: 1.55; }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 5px 0; }}
    .eyebrow {{ margin: 0 0 6px; color: var(--accent); font-weight: 700; }}
    .muted, .meta {{ color: var(--muted); }}
    .meta, .metric-card, .rail-card, .empty-state, .disclaimer {{
      overflow-wrap: anywhere;
    }}
    .disclaimer {{
      margin: 14px 0 0;
      padding: 12px 14px;
      border: 1px solid #b7d8d4;
      border-left: 4px solid var(--accent);
      border-radius: 6px;
      background: var(--accent-soft);
      color: #164e47;
      max-width: 760px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin: 18px 0;
    }}
    .metric-card, .rail-card, .ledger-table-wrap, .empty-state {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .metric-card {{ padding: 13px 14px; min-height: 84px; }}
    .metric-card span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric-card strong {{ display: block; margin-top: 8px; font-size: 26px; line-height: 1.1; }}
    .metric-card[data-tone="warn"] strong {{ color: var(--warn); }}
    .metric-card[data-tone="error"] strong {{ color: var(--error); }}
    .content-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 18px;
      align-items: start;
    }}
    .ledger-panel {{ min-width: 0; }}
    .ledger-controls {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
    }}
    .filter-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .filter-button {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      cursor: pointer;
      padding: 0 12px;
      font: inherit;
    }}
    .filter-button.active {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: #115e59;
      font-weight: 700;
    }}
    .search-label {{
      display: grid;
      gap: 4px;
      min-width: min(320px, 100%);
      color: var(--muted);
      font-size: 13px;
    }}
    .search-label input {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }}
    .ledger-table-wrap {{ overflow-x: auto; }}
    .ledger-table {{ width: 100%; min-width: 1080px; border-collapse: collapse; }}
    caption {{
      padding: 12px;
      text-align: left;
      font-weight: 700;
      color: var(--muted);
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid #edf1f5;
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }}
    thead th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #eef3f7;
      color: #334155;
      font-size: 12px;
      text-transform: uppercase;
    }}
    td span {{ display: block; color: var(--muted); margin-top: 4px; }}
    .date-row th {{
      background: #f8fafc;
      color: #334155;
      font-size: 13px;
      text-transform: none;
      position: static;
    }}
    .signal-row {{
      cursor: pointer;
    }}
    .signal-row:focus {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .signal-row[aria-expanded="true"] td {{
      background: #fbfdfd;
      border-bottom-color: #d6e7e4;
    }}
    .signal-detail-row td {{
      background: #fbfdfd;
      padding: 0 12px 14px;
    }}
    .signal-detail {{
      border: 1px solid #d6e7e4;
      border-radius: 8px;
      padding: 14px;
      background: #ffffff;
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 10px 14px;
      margin: 0;
    }}
    .detail-grid div {{
      min-width: 0;
      padding: 10px;
      border: 1px solid #e5edf3;
      border-radius: 6px;
      background: #f8fafc;
    }}
    .detail-grid dt {{
      margin: 0 0 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .detail-grid dd {{
      margin: 0;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }}
    .detail-note {{
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .grade-pill {{
      display: inline-flex;
      width: 36px;
      min-height: 28px;
      align-items: center;
      justify-content: center;
      border-radius: 7px;
      background: #e9eef5;
      color: #1f2937;
      font-weight: 800;
    }}
    .grade-priority {{
      width: 48px;
      min-height: 34px;
      border-radius: 8px;
      color: #fff;
      font-size: 16px;
      font-weight: 900;
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.18);
    }}
    .grade-s {{ background: #14532d; color: #f0fdf4; border: 1px solid #22c55e; }}
    .grade-a {{ background: #1d4ed8; color: #eff6ff; border: 1px solid #60a5fa; }}
    .grade-b {{ background: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe; }}
    .grade-c {{ background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }}
    .grade-d {{ background: #fff7ed; color: #9a3412; border: 1px solid #fed7aa; }}
    .grade-unknown {{ background: #f3f4f6; color: #4b5563; border: 1px solid #d1d5db; }}
    .signal-change {{
      display: block;
      margin: 0 0 8px;
      padding: 8px 10px;
      border: 1px solid #d5dee8;
      border-left: 4px solid #64748b;
      border-radius: 6px;
      background: #f8fafc;
      color: #243447;
      font-size: 13px;
      line-height: 1.45;
    }}
    .signal-change strong {{
      display: block;
      margin: 0 0 3px;
      color: #0f172a;
      font-size: 12px;
    }}
    .signal-change-strong {{
      border-color: #bbf7d0;
      border-left-color: #16a34a;
      background: #f0fdf4;
      color: #14532d;
    }}
    .signal-change-warn {{
      border-color: #fde68a;
      border-left-color: var(--warn);
      background: #fffbeb;
      color: #7c2d12;
    }}
    .prediction-result {{
      display: block;
      margin: 0 0 8px;
      padding: 8px 10px;
      border: 1px solid #d5dee8;
      border-left: 4px solid #64748b;
      border-radius: 6px;
      background: #f8fafc;
      color: #243447;
      font-size: 13px;
      line-height: 1.45;
    }}
    .prediction-result strong {{
      display: block;
      margin: 0 0 3px;
      color: #0f172a;
      font-size: 12px;
    }}
    .prediction-result-hit {{
      border-color: #bbf7d0;
      border-left-color: #16a34a;
      background: #f0fdf4;
      color: #14532d;
    }}
    .prediction-result-miss {{
      border-color: #fecaca;
      border-left-color: #dc2626;
      background: #fef2f2;
      color: #7f1d1d;
    }}
    .prediction-result-push {{
      border-color: #cbd5e1;
      border-left-color: #64748b;
      background: #f8fafc;
      color: #334155;
    }}
    .signal-why {{ color: var(--muted); }}
    .policy-list {{ margin-bottom: 12px; }}
    .right-rail {{ display: grid; gap: 12px; }}
    .rail-card {{ padding: 16px; }}
    .no-results, .empty-state {{ padding: 18px; }}
    @media (max-width: 980px) {{
      header, .ledger-controls {{ flex-direction: column; align-items: stretch; }}
      .content-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <p class="eyebrow">2026 世界杯</p>
        <h1>研究台账</h1>
        <p class="disclaimer">仅用于研究分析，不构成投注建议。</p>
      </div>
      <p class="meta">最后更新<br>{snapshot_at}</p>
    </header>
    {summary}
    <div class="content-grid">
      <div class="ledger-panel">
        {controls}
        {table}
      </div>
      {right_rail}
    </div>
  </main>
  <script>
    (function () {{
      var activeFilter = 'all';
      var buttons = Array.prototype.slice.call(document.querySelectorAll('.filter-button'));
      var search = document.getElementById('ledger-search');
      var rows = Array.prototype.slice.call(document.querySelectorAll('.signal-row'));
      var dateRows = Array.prototype.slice.call(document.querySelectorAll('.date-row'));
      var noResults = document.querySelector('.no-results');

      function setSignalDetail(row, expanded) {{
        var targetId = row.dataset.detailTarget;
        var detail = targetId ? document.getElementById(targetId) : null;
        row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        if (detail) {{
          detail.hidden = !expanded || row.hidden;
        }}
      }}

      function toggleSignalDetail(row) {{
        var expanded = row.getAttribute('aria-expanded') === 'true';
        setSignalDetail(row, !expanded);
      }}

      function applyFilters() {{
        var term = search ? search.value.trim().toLowerCase() : '';
        var visible = 0;
        rows.forEach(function (row) {{
          var wasExpanded = row.getAttribute('aria-expanded') === 'true';
          var gradeMatch = activeFilter === 'all' || row.dataset.grade === activeFilter;
          var textMatch = !term || row.dataset.search.indexOf(term) !== -1;
          var show = gradeMatch && textMatch;
          row.hidden = !show;
          setSignalDetail(row, show && wasExpanded);
          if (show) {{
            visible += 1;
          }}
        }});
        dateRows.forEach(function (dateRow) {{
          var cursor = dateRow.nextElementSibling;
          var hasVisible = false;
          while (cursor && !cursor.classList.contains('date-row')) {{
            if (cursor.classList.contains('signal-row') && !cursor.hidden) {{
              hasVisible = true;
              break;
            }}
            cursor = cursor.nextElementSibling;
          }}
          dateRow.hidden = !hasVisible;
        }});
        if (noResults) {{
          noResults.hidden = visible !== 0;
        }}
      }}

      rows.forEach(function (row) {{
        row.addEventListener('click', function () {{
          toggleSignalDetail(row);
        }});
        row.addEventListener('keydown', function (event) {{
          if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            toggleSignalDetail(row);
          }}
        }});
      }});

      buttons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeFilter = button.dataset.filter || 'all';
          buttons.forEach(function (item) {{
            item.classList.remove('active');
            item.setAttribute('aria-pressed', 'false');
          }});
          button.classList.add('active');
          button.setAttribute('aria-pressed', 'true');
          applyFilters();
        }});
      }});
      if (search) {{
        search.addEventListener('input', applyFilters);
      }}
    }}());
  </script>
</body>
</html>
""".format(
        snapshot_at=_text(snapshot_at),
        summary=_render_summary(snapshot),
        controls=_render_controls(),
        table=_render_signal_table(snapshot, previous_snapshot),
        right_rail=_render_right_rail(snapshot),
    )
