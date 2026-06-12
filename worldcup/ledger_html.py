from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any

from worldcup.ledger import (
    BEIJING_TZ,
    WEEKDAY_LABELS,
    build_finished_view,
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
        "record_s",
        "record_a",
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


def _date_button_label(label: str) -> str:
    parts = label.split()
    if len(parts) >= 7 and parts[1] == "年" and parts[3] == "月" and parts[5] == "日":
        weekday = parts[6].replace("星期", "周")
        return f"{parts[2]}/{parts[4]} {weekday}"
    return label


def _date_filter_buttons(rows: list[dict[str, Any]]) -> str:
    dates: list[str] = []
    for row in rows:
        label = str(row.get("kickoff_date") or "")
        if label and label not in dates:
            dates.append(label)
    buttons = [
        '<button type="button" class="filter-button date-filter-button active" '
        'data-date-filter="all" aria-pressed="true">全部日期</button>'
    ]
    for label in dates:
        buttons.append(
            '<button type="button" class="filter-button date-filter-button" '
            'data-date-filter="{value}" aria-pressed="false">{label}</button>'.format(
                value=_text(label),
                label=_text(_date_button_label(label)),
            )
        )
    return "".join(buttons)


def _render_controls(rows: list[dict[str, Any]]) -> str:
    return """
    <section class="ledger-controls" aria-label="台账筛选">
      <div class="ledger-filter-row">
        <div class="filter-group date-filter-group" role="group" aria-label="日期筛选">
          <span class="filter-label">日期</span>
          {date_buttons}
        </div>
        <label class="search-label">
          <span>搜索</span>
          <input type="search" id="ledger-search" placeholder="球队、盘口、等级" autocomplete="off">
        </label>
      </div>
      <div class="ledger-filter-row">
        <label class="league-label">
          <span>赛事</span>
          <select id="league-filter" autocomplete="off">
            <option value="all">全部赛事</option>
            <option value="worldcup">世界杯</option>
            <option value="premier-league">英超</option>
            <option value="la-liga">西甲</option>
            <option value="serie-a">意甲</option>
            <option value="bundesliga">德甲</option>
            <option value="ligue-1">法甲</option>
            <option value="csl">中超</option>
          </select>
        </label>
        <div class="filter-group grade-filter-group" role="group" aria-label="等级筛选">
          <span class="filter-label">等级</span>
          <button type="button" class="filter-button grade-filter-button active" data-filter="all" aria-pressed="true">全部</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="strong" aria-pressed="false">强信号 (S/A)</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="watch" aria-pressed="false">观察 (B)</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="weak" aria-pressed="false">弱信号 (C/D)</button>
        </div>
      </div>
    </section>
    """.format(date_buttons=_date_filter_buttons(rows))


def _render_view_tabs() -> str:
    return """
    <nav class="view-tabs" aria-label="视图切换">
      <button type="button" class="view-tab active" data-view-filter="live" aria-pressed="true">实时信号</button>
      <button type="button" class="view-tab" data-view-filter="history" aria-pressed="false">历史回顾</button>
    </nav>
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
        row.get("next_update_time", ""),
        row.get("next_update_label", ""),
        row.get("next_update_description", ""),
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
        "{trend}"
        "<p class=\"detail-note\">这些内容只解释研究信号来源，不构成投注建议。</p>"
        "</div>"
    ).format(items="".join(items), trend=_render_odds_trend(row.get("odds_trend_points") or []))


def _svg_sparkline(values: list[float]) -> str:
    if len(values) < 2:
        return ""
    width, height, pad = 220, 44, 4
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    points = []
    for index, value in enumerate(values):
        x = pad + index * (width - 2 * pad) / (len(values) - 1)
        y = height - pad - (value - lo) * (height - 2 * pad) / span
        points.append(f"{x:.1f},{y:.1f}")
    if values[-1] < values[0]:
        color = "var(--error)"
    elif values[-1] > values[0]:
        color = "var(--accent)"
    else:
        color = "var(--muted)"
    return (
        '<svg class="trend-spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        'role="img" aria-label="赔率走势">'
        '<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/>'
        "</svg>"
    ).format(width=width, height=height, color=color, points=" ".join(points))


def _trend_text(points: list) -> str:
    if len(points) < 2:
        return ""

    def _label(point: list) -> str:
        return f"{_format_snapshot_time(point[0])} {point[1]}"

    shown = points if len(points) <= 6 else [points[0]] + points[-5:]
    first, last = points[0][1], points[-1][1]
    delta = (last - first) / first * 100 if first else 0.0
    arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
    return " → ".join(_label(point) for point in shown) + f"（累计 {arrow}{abs(delta):.1f}%）"


def _render_odds_trend(points: list) -> str:
    if not points or len(points) < 2:
        return ""
    try:
        values = [float(point[1]) for point in points]
    except (TypeError, ValueError):
        return ""
    return (
        '<div class="trend-block"><h3>赔率走势</h3>{spark}'
        '<p class="muted trend-text">{text}</p></div>'
    ).format(spark=_svg_sparkline(values), text=_text(_trend_text(points)))


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


def _render_prediction_cell(row: dict[str, Any]) -> str:
    result = row.get("prediction_result") or {}
    if result:
        status = _slug(result.get("status", "unknown"))
        label = result.get("label") or "已完赛"
    else:
        status = "pending"
        label = "待赛"
    return "<span class=\"prediction-pill prediction-{status}\">{label}</span>".format(
        status=_text(status),
        label=_text(label),
    )


def _render_signal_reason(row: dict[str, Any]) -> str:
    return "{result}{change}<span class=\"signal-why\">{why}</span>".format(
        result=_render_prediction_result(row),
        change=_render_signal_change(row),
        why=_text(row.get("explanation")),
    )


def _render_signal_table(rows: list[dict[str, Any]]) -> str:
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
            "<tr class=\"date-row\" data-date-row=\"{date}\"><th colspan=\"12\">{label}</th></tr>".format(
                date=_text(kickoff_date),
                label=_text(kickoff_date),
            )
        )
        for row in date_rows:
            grade = row.get("grade", "")
            grade_bucket = _grade_bucket(grade)
            grade_class = _grade_class(grade)
            search_text = _row_search_text(row)
            detail_id = f"signal-detail-{detail_index}"
            reason = _render_signal_reason(row)
            prediction = _render_prediction_cell(row)
            table_rows.append(
                "<tr class=\"signal-row\" role=\"button\" tabindex=\"0\" "
                "aria-expanded=\"false\" aria-controls=\"{detail_id}\" "
                "data-detail-target=\"{detail_id}\" data-grade=\"{grade_bucket}\" "
                "data-date=\"{date}\" data-league=\"worldcup\" data-search=\"{search}\">"
                "<td><strong>{matchup}</strong><span>{stage_group}</span></td>"
                "<td>{kickoff}</td>"
                "<td><strong>{updated}</strong><span>{updated_label}</span></td>"
                "<td><strong>{next_update}</strong><span>{next_update_label}</span></td>"
                "<td>{market}</td>"
                "<td>{prediction}</td>"
                "<td>{model_prob}</td>"
                "<td>{market_prob}</td>"
                "<td><strong>{ev}</strong><span>{edge}</span></td>"
                "<td><span class=\"grade-pill {grade_class}\">{grade}</span></td>"
                "<td>{freshness}</td>"
                "<td>{why}</td>"
                "</tr>".format(
                    detail_id=_text(detail_id),
                    grade_bucket=_text(grade_bucket),
                    date=_text(kickoff_date),
                    search=_text(search_text),
                    matchup=_text(row.get("matchup")),
                    stage_group=_text(row.get("stage_group")),
                    kickoff=_text(row.get("kickoff_time") or row.get("kickoff_at_utc")),
                    updated=_text(row.get("updated_time")),
                    updated_label=_text(row.get("updated_label")),
                    next_update=_text(row.get("next_update_time")),
                    next_update_label=_text(row.get("next_update_label")),
                    market=_text(row.get("market_label")),
                    prediction=prediction,
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
                "<td colspan=\"12\">{detail}</td>"
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
            <th scope="col">下次更新</th>
            <th scope="col">盘口</th>
            <th scope="col">预测结果</th>
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
        "quota_low": "低额度",
        "pre_12h_checkpoint": "T-12小时",
        "pre_6h_checkpoint": "T-6小时",
        "pre_90m_lineup_warmup": "T-1小时30分",
        "pre_55m_lineup_main": "T-55分钟",
        "pre_25m_final_check": "T-25分钟",
    }
    return labels.get(str(reason or ""), "按当前调度策略")


def _match_plan_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    policy = (snapshot.get("run") or {}).get("policy") or {}
    plans = policy.get("match_plans")
    if isinstance(plans, list) and plans:
        return [plan for plan in plans if isinstance(plan, dict)]
    candidates = []
    for match in snapshot.get("matches") or []:
        refresh_plan = match.get("refresh_plan")
        if not isinstance(refresh_plan, dict):
            continue
        home = str(match.get("home_team") or "").strip()
        away = str(match.get("away_team") or "").strip()
        candidates.append(
            {
                **refresh_plan,
                "match_label": f"{home} vs {away}".strip(),
                "kickoff_at_utc": match.get("kickoff_at_utc"),
            }
        )
    return candidates


def _render_nearest_match_plan(snapshot: dict[str, Any]) -> str:
    active = [
        plan
        for plan in _match_plan_candidates(snapshot)
        if plan.get("next_update_at")
    ]
    if not active:
        return "<p><strong>最近一次计划：</strong>待下一轮调度确认</p>"
    first = min(
        active,
        key=lambda plan: (
            str(plan.get("next_update_at") or ""),
            str(plan.get("kickoff_at_utc") or ""),
            str(plan.get("match_label") or ""),
        ),
    )
    when = _format_snapshot_time(first.get("next_update_at"))
    label = str(first.get("label") or "按规则")
    description = str(first.get("description") or "")
    rule = " ".join(part for part in (label, description) if part)
    return (
        "<p><strong>最近一次计划：</strong>{match}，{when}</p>"
        "<p><strong>规则：</strong>{rule}</p>"
    ).format(
        match=_text(first.get("match_label") or "待确认比赛"),
        when=_text(when),
        rule=_text(rule),
    )


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
        <li>常规：每天 1 次</li>
        <li>临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-25分钟</li>
        <li>低额度：每天 1 次，并保留 T-90 / T-55 / T-25</li>
        <li>额度耗尽：暂停自动刷新</li>
      </ul>
      <p><strong>当前模式：按每场比赛独立调度</strong></p>
      {nearest_match_plan}
      <p><strong>当前规则：{reason}</strong></p>
      <p><strong>当前间隔：</strong>{interval}</p>
      {next_due}
    </section>
    """.format(
        reason=_text(reason_label),
        interval=_text(interval_label),
        nearest_match_plan=_render_nearest_match_plan(snapshot),
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


def _outcome_class(value: Any) -> str:
    return {"命中": "hit", "未中": "miss", "走水": "push"}.get(str(value or ""), "unknown")


def _finished_grade_bucket(match: dict[str, Any]) -> str:
    buckets = [_grade_bucket(item.get("grade")) for item in match.get("detail_signals") or []]
    if "strong" in buckets:
        return "strong"
    if "watch" in buckets:
        return "watch"
    if "weak" in buckets:
        return "weak"
    return "all"


def _finished_search_text(match: dict[str, Any]) -> str:
    parts = [
        match.get("matchup", ""),
        match.get("score_label", ""),
        match.get("stage_group", ""),
    ]
    for item in match.get("detail_signals") or []:
        parts.extend(
            [
                item.get("grade", ""),
                item.get("market_label", ""),
                item.get("outcome", ""),
                item.get("detail", ""),
            ]
        )
    return " ".join(str(part) for part in parts).lower()


def _render_finished_section(snapshot: dict[str, Any]) -> str:
    view = build_finished_view(snapshot)
    if not view["days"]:
        return (
            '<section class="panel finished-panel" data-view-panel="history" hidden>'
            "<h2>历史回顾</h2>"
            '<div class="empty-state">'
            "<h3>暂无历史回顾</h3>"
            "<p>还没有可用于复盘的已完赛 closing 记录。</p>"
            "</div>"
            "</section>"
        )
    day_blocks = []
    for day in view["days"]:
        match_rows = []
        for match in day["matches"]:
            grade_bucket = _finished_grade_bucket(match)
            search_text = _finished_search_text(match)
            badges = "".join(
                '<span class="grade-chip {grade_class}">{grade}</span>'
                '<span class="outcome outcome-{slug}">{label}</span>'.format(
                    grade_class=_text(_grade_class(item["grade"])),
                    grade=_text(item["grade"]),
                    slug=_text(_outcome_class(item["outcome"])),
                    label=_text(item["outcome"]),
                )
                for item in match["sa_badges"]
            ) or '<span class="muted">无 S/A 信号</span>'
            details = "".join(
                (
                    '<li><span class="grade-chip {grade_class}">{grade}</span> '
                    "{market} @ {odds} - {outcome}（{detail}）{trend}</li>"
                ).format(
                    grade_class=_text(_grade_class(item["grade"])),
                    grade=_text(item["grade"]),
                    market=_text(item["market_label"]),
                    odds=_text(item["odds"] if item["odds"] is not None else "—"),
                    outcome=_text(item["outcome"] or "—"),
                    detail=_text(item["detail"]),
                    trend=_render_odds_trend(item.get("trend_points") or []),
                )
                for item in match["detail_signals"]
            ) or '<li class="muted">暂无 closing 信号</li>'
            match_rows.append(
                '<tr class="finished-row" role="button" tabindex="0" aria-expanded="false" '
                'data-date="{date}" data-league="worldcup" data-grade="{grade_bucket}" '
                'data-search="{search}">'
                "<td>{time}</td><td>{matchup}</td><td>{score}</td><td>{stage}</td><td>{badges}</td>"
                "</tr>"
                '<tr class="finished-detail-row" hidden><td colspan="5"><ul>{details}</ul></td></tr>'.format(
                    date=_text(day["date_label"]),
                    grade_bucket=_text(grade_bucket),
                    search=_text(search_text),
                    time=_text(match["kickoff_time"]),
                    matchup=_text(match["matchup"]),
                    score=_text(match["score_label"]),
                    stage=_text(match["stage_group"]),
                    badges=badges,
                    details=details,
                )
            )
        day_blocks.append(
            '<tr class="finished-day" data-finished-date-row="{date}"><td colspan="5">{label}</td></tr>{rows}'.format(
                date=_text(day["date_label"]),
                label=_text(day["date_label"]),
                rows="".join(match_rows),
            )
        )
    return (
        '<section class="panel finished-panel" data-view-panel="history" hidden><h2>历史回顾</h2>'
        '<p class="muted"><strong>已完赛战绩</strong>：closing（开球前最后一轮）口径；'
        "仅用于研究分析，不构成投注建议。</p>"
        '<div class="table-scroll"><table class="finished-table">'
        "<thead><tr><th>开赛 (北京时间)</th><th>对阵</th><th>比分</th><th>阶段</th>"
        "<th>S/A 信号与结果</th></tr></thead>"
        "<tbody>{body}</tbody></table></div>"
        '<p class="history-no-results" hidden>没有符合当前筛选的历史记录。</p>'
        "</section>"
    ).format(body="".join(day_blocks))


def build_research_ledger_html(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> str:
    snapshot_at = _format_snapshot_time(snapshot.get("snapshot_at"))
    signal_rows = project_signal_rows(snapshot, previous_snapshot=previous_snapshot)
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
    .header-actions {{
      display: grid;
      justify-items: end;
      gap: 10px;
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
    .view-tabs {{
      display: inline-flex;
      gap: 4px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .view-tab {{
      min-height: 34px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: #334155;
      cursor: pointer;
      padding: 0 12px;
      font: inherit;
      font-weight: 700;
    }}
    .view-tab.active {{
      background: var(--accent-soft);
      color: #115e59;
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
      grid-template-columns: minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }}
    .ledger-panel {{ min-width: 0; }}
    .ledger-controls {{
      display: grid;
      gap: 12px;
      margin-bottom: 12px;
      min-width: 0;
    }}
    .ledger-filter-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      min-width: 0;
      width: 100%;
    }}
    .filter-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .date-filter-group {{
      flex: 1 1 620px;
      flex-wrap: nowrap;
      overflow-x: auto;
      padding-bottom: 2px;
      min-width: 0;
      max-width: 100%;
    }}
    .date-filter-button {{ flex: 0 0 auto; }}
    .filter-label {{
      display: inline-flex;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      margin-right: 2px;
      white-space: nowrap;
    }}
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
    .league-label, .search-label {{
      display: grid;
      gap: 4px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .search-label {{ min-width: min(320px, 100%); }}
    .league-label {{ min-width: min(180px, 100%); }}
    .search-label input, .league-label select {{
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }}
    .league-label select {{ font-weight: 700; }}
    .ledger-table-wrap {{ overflow-x: auto; }}
    .ledger-table {{ width: 100%; min-width: 1160px; border-collapse: collapse; }}
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
    .prediction-pill {{
      display: inline-flex;
      min-width: 52px;
      min-height: 28px;
      align-items: center;
      justify-content: center;
      border-radius: 7px;
      border: 1px solid #d1d5db;
      background: #f8fafc;
      color: #475569;
      font-weight: 800;
      font-size: 13px;
      white-space: nowrap;
    }}
    .prediction-hit {{
      border-color: #86efac;
      background: #dcfce7;
      color: #166534;
    }}
    .prediction-miss {{
      border-color: #fecaca;
      background: #fee2e2;
      color: #991b1b;
    }}
    .prediction-push {{
      border-color: #cbd5e1;
      background: #f1f5f9;
      color: #334155;
    }}
    .prediction-pending {{
      border-color: #d1d5db;
      background: #f8fafc;
      color: #64748b;
    }}
    .trend-block {{
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid #edf1f5;
    }}
    svg[aria-label="赔率走势"] {{
      width: 220px;
      height: 44px;
      display: block;
      margin: 6px 0 2px;
    }}
    .trend-text {{ font-size: 12px; }}
    .finished-panel {{
      margin-top: 18px;
      padding: 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }}
    .table-scroll {{ overflow-x: auto; }}
    .finished-table {{
      width: 100%;
      min-width: 760px;
      border-collapse: collapse;
    }}
    .finished-day td {{
      background: #f8fafc;
      color: #334155;
      font-weight: 700;
    }}
    .finished-row {{ cursor: pointer; }}
    .finished-row:focus {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .finished-row[aria-expanded="true"] td {{
      background: #fbfdfd;
      border-bottom-color: #d6e7e4;
    }}
    .finished-detail-row td {{ background: #fbfdfd; }}
    .finished-detail-row ul {{ margin: 0; padding-left: 18px; }}
    .grade-chip {{
      display: inline-flex;
      min-width: 30px;
      min-height: 24px;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      font-weight: 800;
      font-size: 12px;
      margin-right: 4px;
    }}
    .outcome {{
      display: inline-block;
      margin: 0 8px 0 2px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }}
    .outcome-hit {{ color: var(--accent); }}
    .outcome-miss {{ color: var(--error); }}
    .outcome-push {{ color: var(--muted); }}
    .signal-why {{ color: var(--muted); }}
    .policy-list {{ margin-bottom: 12px; }}
    .right-rail {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
    }}
    .rail-card {{ padding: 16px; }}
    .no-results, .empty-state {{ padding: 18px; }}
    @media (max-width: 980px) {{
      header {{ flex-direction: column; align-items: stretch; }}
      .header-actions {{ justify-items: stretch; }}
      .view-tabs {{ width: 100%; }}
      .view-tab {{ flex: 1; }}
      .ledger-filter-row {{ align-items: stretch; }}
      .filter-group, .league-label, .search-label {{ width: 100%; }}
      .league-label, .search-label {{ min-width: 0; }}
      .date-filter-group {{ flex-basis: 100%; }}
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
      <div class="header-actions">
        {view_tabs}
        <p class="meta">最后更新<br>{snapshot_at}</p>
      </div>
    </header>
    {summary}
    <div class="content-grid">
      <div class="ledger-panel">
        {controls}
        <div data-view-panel="live">
          {table}
        </div>
        {finished_section}
      </div>
      {right_rail}
    </div>
  </main>
  <script>
    (function () {{
      var activeView = 'live';
      var activeDate = 'all';
      var activeGrade = 'all';
      var viewButtons = Array.prototype.slice.call(document.querySelectorAll('[data-view-filter]'));
      var viewPanels = Array.prototype.slice.call(document.querySelectorAll('[data-view-panel]'));
      var dateButtons = Array.prototype.slice.call(document.querySelectorAll('[data-date-filter]'));
      var gradeButtons = Array.prototype.slice.call(document.querySelectorAll('[data-filter]'));
      var league = document.getElementById('league-filter');
      var search = document.getElementById('ledger-search');
      var rows = Array.prototype.slice.call(document.querySelectorAll('.signal-row'));
      var dateRows = Array.prototype.slice.call(document.querySelectorAll('.date-row'));
      var finishedRows = Array.prototype.slice.call(document.querySelectorAll('.finished-row'));
      var finishedDateRows = Array.prototype.slice.call(document.querySelectorAll('.finished-day'));
      var noResults = document.querySelector('.no-results');
      var historyNoResults = document.querySelector('.history-no-results');

      function setActiveButton(buttons, activeButton) {{
        buttons.forEach(function (button) {{
          var active = button === activeButton;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
      }}

      function setView(view) {{
        activeView = view;
        viewButtons.forEach(function (button) {{
          var active = (button.dataset.viewFilter || 'live') === activeView;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
        viewPanels.forEach(function (panel) {{
          panel.hidden = (panel.dataset.viewPanel || 'live') !== activeView;
        }});
        applyFilters();
      }}

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

      function setFinishedDetail(row, expanded) {{
        var detail = row.nextElementSibling;
        row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        if (detail && detail.classList.contains('finished-detail-row')) {{
          detail.hidden = !expanded || row.hidden;
        }}
      }}

      function toggleFinishedDetail(row) {{
        var expanded = row.getAttribute('aria-expanded') === 'true';
        setFinishedDetail(row, !expanded);
      }}

      function getSelectedLeague() {{
        return league ? (league.value || 'all') : 'all';
      }}

      function rowMatches(row, term) {{
        var selectedLeague = getSelectedLeague();
        var gradeMatch = activeGrade === 'all' || row.dataset.grade === activeGrade;
        var dateMatch = activeDate === 'all' || row.dataset.date === activeDate;
        var leagueMatch = selectedLeague === 'all' || row.dataset.league === selectedLeague;
        var searchText = row.dataset.search || '';
        var textMatch = !term || searchText.indexOf(term) !== -1;
        return gradeMatch && dateMatch && leagueMatch && textMatch;
      }}

      function applyFilters() {{
        var term = search ? search.value.trim().toLowerCase() : '';
        var visible = 0;
        rows.forEach(function (row) {{
          var wasExpanded = row.getAttribute('aria-expanded') === 'true';
          var show = rowMatches(row, term);
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

        var historyVisible = 0;
        finishedRows.forEach(function (row) {{
          var wasExpanded = row.getAttribute('aria-expanded') === 'true';
          var show = rowMatches(row, term);
          row.hidden = !show;
          setFinishedDetail(row, show && wasExpanded);
          if (show) {{
            historyVisible += 1;
          }}
        }});
        finishedDateRows.forEach(function (dateRow) {{
          var date = dateRow.dataset.finishedDateRow || '';
          var hasVisible = finishedRows.some(function (row) {{
            return row.dataset.date === date && !row.hidden;
          }});
          dateRow.hidden = !hasVisible;
        }});
        if (historyNoResults) {{
          historyNoResults.hidden = historyVisible !== 0;
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

      viewButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          setView(button.dataset.viewFilter || 'live');
        }});
      }});

      dateButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeDate = button.dataset.dateFilter || 'all';
          setActiveButton(dateButtons, button);
          applyFilters();
        }});
      }});

      gradeButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeGrade = button.dataset.filter || 'all';
          setActiveButton(gradeButtons, button);
          applyFilters();
        }});
      }});
      if (league) {{
        league.addEventListener('input', applyFilters);
        league.addEventListener('change', applyFilters);
      }}
      if (search) {{
        search.addEventListener('input', applyFilters);
      }}
      document.addEventListener('click', function (event) {{
        var row = event.target.closest('.finished-row');
        if (!row) return;
        toggleFinishedDetail(row);
      }});
      document.addEventListener('keydown', function (event) {{
        var row = event.target.closest('.finished-row');
        if (!row || (event.key !== 'Enter' && event.key !== ' ')) return;
        event.preventDefault();
        toggleFinishedDetail(row);
      }});
      setView('live');
    }}());
  </script>
</body>
</html>
""".format(
        snapshot_at=_text(snapshot_at),
        view_tabs=_render_view_tabs(),
        summary=_render_summary(snapshot),
        controls=_render_controls(signal_rows),
        table=_render_signal_table(signal_rows),
        finished_section=_render_finished_section(snapshot),
        right_rail=_render_right_rail(snapshot),
    )
