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
    competition_options,
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


TEAM_FLAG_CODES = {
    "Algeria": "DZ",
    "Argentina": "AR",
    "Australia": "AU",
    "Austria": "AT",
    "Belgium": "BE",
    "Bosnia & Herzegovina": "BA",
    "Bosnia and Herzegovina": "BA",
    "Brazil": "BR",
    "Canada": "CA",
    "Cape Verde": "CV",
    "Colombia": "CO",
    "Costa Rica": "CR",
    "Croatia": "HR",
    "Curaçao": "CW",
    "Czech Republic": "CZ",
    "DR Congo": "CD",
    "Denmark": "DK",
    "Ecuador": "EC",
    "Egypt": "EG",
    "England": "GB",
    "France": "FR",
    "Germany": "DE",
    "Ghana": "GH",
    "Greece": "GR",
    "Haiti": "HT",
    "Honduras": "HN",
    "Iran": "IR",
    "Iraq": "IQ",
    "Italy": "IT",
    "Ivory Coast": "CI",
    "Japan": "JP",
    "Jordan": "JO",
    "Mexico": "MX",
    "Morocco": "MA",
    "Netherlands": "NL",
    "New Zealand": "NZ",
    "Nigeria": "NG",
    "Norway": "NO",
    "Panama": "PA",
    "Paraguay": "PY",
    "Poland": "PL",
    "Portugal": "PT",
    "Qatar": "QA",
    "Saudi Arabia": "SA",
    "Scotland": "GB",
    "Senegal": "SN",
    "Serbia": "RS",
    "South Africa": "ZA",
    "South Korea": "KR",
    "Spain": "ES",
    "Sweden": "SE",
    "Switzerland": "CH",
    "Tunisia": "TN",
    "Turkey": "TR",
    "Ukraine": "UA",
    "United States": "US",
    "Uruguay": "UY",
    "USA": "US",
    "Uzbekistan": "UZ",
    "Wales": "GB",
    "加拿大": "CA",
    "波黑": "BA",
    "美国": "US",
    "巴拉圭": "PY",
}


def _flag_emoji(country_code: str) -> str:
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


def _team_flag(source_team: Any, display_team: Any) -> str:
    source = str(source_team or "").strip()
    display = str(display_team or "").strip()
    code = TEAM_FLAG_CODES.get(source) or TEAM_FLAG_CODES.get(display)
    return _flag_emoji(code) if code else ""


def _render_team_inline(display_team: Any, source_team: Any) -> str:
    label = str(display_team or source_team or "待确认").strip() or "待确认"
    flag = _team_flag(source_team, label)
    flag_html = (
        '<span class="team-flag" aria-hidden="true">{flag}</span>'.format(flag=_text(flag))
        if flag
        else ""
    )
    return '<span class="team-inline">{flag}<span class="team-name">{label}</span></span>'.format(
        flag=flag_html,
        label=_text(label),
    )


def _render_matchup_teams(
    home_team: Any,
    away_team: Any,
    source_home_team: Any,
    source_away_team: Any,
    *,
    separator: str,
    inline: bool = False,
    fallback: Any = "",
) -> str:
    home = str(home_team or source_home_team or "").strip()
    away = str(away_team or source_away_team or "").strip()
    if not home or not away:
        return _text(_matchup_with_vs(fallback))
    classes = "team-matchup team-matchup-inline" if inline else "team-matchup"
    aria_separator = "vs" if separator == "vs" else "对"
    return (
        '<span class="{classes}" aria-label="{aria}">'
        "{home}<span class=\"team-vs\">{separator}</span>{away}"
        "</span>"
    ).format(
        classes=classes,
        aria=_text(f"{home} {aria_separator} {away}"),
        home=_render_team_inline(home, source_home_team),
        separator=_text(separator),
        away=_render_team_inline(away, source_away_team),
    )


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


def _compact_date_label(group: dict[str, Any]) -> str:
    date_iso = str(group.get("kickoff_date_iso") or "")
    if date_iso:
        try:
            parsed = datetime.fromisoformat(date_iso)
        except ValueError:
            parsed = None
        if parsed is not None:
            return f"{parsed.month}/{parsed.day}"
    label = str(group.get("kickoff_date") or "")
    parts = label.split()
    if len(parts) >= 6 and parts[1] == "年" and parts[3] == "月" and parts[5] == "日":
        return f"{parts[2]}/{parts[4]}"
    return ""


def _render_match_kickoff_cell(group: dict[str, Any]) -> str:
    kickoff = _text(group.get("kickoff_time"))
    date_label = _compact_date_label(group)
    if not date_label:
        return "<td>{kickoff}</td>".format(kickoff=kickoff)
    return (
        '<td class="match-kickoff-cell"><span>{date}</span><strong>{kickoff}</strong></td>'
    ).format(date=_text(date_label), kickoff=kickoff)


def _row_date_iso(row: dict[str, Any]) -> str:
    raw = row.get("kickoff_at_utc")
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TZ).date().isoformat()


def _date_filter_options(rows: list[dict[str, Any]]) -> str:
    dates: dict[str, dict[str, Any]] = {}
    for row in rows:
        date_iso = _row_date_iso(row)
        label = str(row.get("kickoff_date") or "")
        if not date_iso or not label:
            continue
        date_entry = dates.setdefault(
            date_iso,
            {"label": label, "matches": set(), "signals": 0},
        )
        date_entry["signals"] += 1
        date_entry["matches"].add((row.get("kickoff_at_utc"), row.get("source_matchup")))
    options = ['<option value="">选择具体日期</option>']
    for date_iso in sorted(dates):
        entry = dates[date_iso]
        options.append(
            '<option value="{value}">{label} · {matches}场 · {signals}分歧</option>'.format(
                value=_text(date_iso),
                label=_text(_date_button_label(entry["label"])),
                matches=_text(len(entry["matches"])),
                signals=_text(entry["signals"]),
            )
        )
    return "".join(options)


def _competition_filter_options(options: list[dict[str, Any]] | None) -> str:
    rendered = ['<option value="all">全部赛事</option>']
    seen: set[str] = set()
    for option in options or []:
        competition_id = str(option.get("id") or "").strip()
        if not competition_id or competition_id in seen:
            continue
        seen.add(competition_id)
        label = str(option.get("label") or competition_id).strip() or competition_id
        rendered.append(
            '<option value="{value}">{label}</option>'.format(
                value=_text(competition_id),
                label=_text(label),
            )
        )
    return "".join(rendered)


def _render_controls(rows: list[dict[str, Any]]) -> str:
    return """
    <section class="ledger-controls" aria-label="台账筛选">
      <div class="ledger-filter-row utility-date-row">
        <div class="filter-group date-filter-group" role="group" aria-label="日期筛选">
          <span class="filter-label">日期</span>
          <button type="button" class="filter-button date-filter-button active" data-date-filter="all" aria-pressed="true">全部</button>
          <button type="button" class="filter-button date-filter-button" data-date-filter="today" aria-pressed="false">今日</button>
          <button type="button" class="filter-button date-filter-button" data-date-filter="tomorrow" aria-pressed="false">明日</button>
          <button type="button" class="filter-button date-filter-button" data-date-filter="next3" aria-pressed="false">未来3天</button>
          <button type="button" class="filter-button date-filter-button" data-date-filter="next7" aria-pressed="false">未来7天</button>
          <button type="button" class="filter-button date-filter-button" data-date-filter="custom" aria-pressed="false">选择日期</button>
          <select id="date-picker" class="date-picker" autocomplete="off">
            {date_options}
          </select>
        </div>
      </div>
    </section>
    """.format(date_options=_date_filter_options(rows))


def _render_workbench_filter_row(
    search_id: str = "ledger-search",
    league_id: str = "league-filter",
    competitions: list[dict[str, Any]] | None = None,
) -> str:
    return """
      <div class="ledger-filter-row workbench-filter-row">
        <div class="filter-group grade-filter-group" role="group" aria-label="等级筛选">
          <button type="button" class="filter-button grade-filter-button active" data-filter="all" aria-pressed="true">全部</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="strong" aria-pressed="false">价值分歧 (S/A)</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="watch" aria-pressed="false">观察分歧 (B)</button>
          <button type="button" class="filter-button grade-filter-button" data-filter="weak" aria-pressed="false">低价值分歧 (C/D)</button>
        </div>
        <div class="workbench-tools">
          <label class="search-label">
            <span>搜索</span>
            <input type="search" id="{search_id}" data-search-control placeholder="搜索球队、盘口、等级" autocomplete="off">
          </label>
          <label class="league-label">
            <span>赛事筛选</span>
            <select id="{league_id}" data-league-filter-control autocomplete="off">
              {competition_options}
            </select>
          </label>
        </div>
      </div>
    """.format(
        search_id=_text(search_id),
        league_id=_text(league_id),
        competition_options=_competition_filter_options(competitions),
    )


def _render_view_tabs() -> str:
    return """
    <nav class="view-tabs" aria-label="视图切换">
      <button type="button" class="view-tab active" data-view-filter="live" aria-pressed="true">实时方向</button>
      <button type="button" class="view-tab" data-view-filter="history" aria-pressed="false">历史回顾</button>
    </nav>
    """


def _render_primary_nav() -> str:
    items = ["研究台账", "赛程", "数据", "模型", "价值分歧", "设置"]
    links = []
    for index, label in enumerate(items):
        active = " active" if index == 0 else ""
        links.append('<span class="primary-nav-item{active}">{label}</span>'.format(active=active, label=_text(label)))
    return '<nav class="primary-nav" aria-label="主导航">{}</nav>'.format("".join(links))


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
        return f"grade-{normalized.lower()}"
    return "grade-unknown"


def _freshness_class(value: Any) -> str:
    text = str(value or "").strip()
    if text == "过期":
        return "freshness-stale"
    if text in {"待更新", "待刷新", "缓存"}:
        return "freshness-pending"
    return "freshness-fresh"


def _render_freshness_badge(value: Any) -> str:
    label = str(value or "新鲜").strip() or "新鲜"
    return '<span class="freshness-badge {klass}">{label}</span>'.format(
        klass=_text(_freshness_class(label)),
        label=_text(label),
    )


def _display_signal_reason(row: dict[str, Any]) -> str:
    if row.get("stale"):
        return "盘口数据已过期，等待下一轮刷新。"
    return str(row.get("explanation") or "")


def _row_search_text(row: dict[str, Any]) -> str:
    recent_change = row.get("recent_change") or {}
    parts = [
        row.get("matchup", ""),
        row.get("source_matchup", ""),
        row.get("source_home_team", ""),
        row.get("source_away_team", ""),
        row.get("competition_label", ""),
        row.get("kickoff_at_utc", ""),
        row.get("kickoff_date", ""),
        row.get("kickoff_time", ""),
        row.get("updated_time", ""),
        row.get("updated_label", ""),
        row.get("next_update_time", ""),
        row.get("next_update_label", ""),
        row.get("next_update_description", ""),
        row.get("market_label", ""),
        row.get("match_decision_summary", ""),
        row.get("match_decision_label_text", ""),
        row.get("match_decision_market_label", ""),
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
        "<p class=\"detail-note\">这些内容只解释价值分歧来源，不构成投注建议。</p>"
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
        line = ""
        if len(point) > 2 and point[2] is not None:
            try:
                line = f" 盘口 {float(point[2]):g}"
            except (TypeError, ValueError):
                line = f" 盘口 {point[2]}"
        return f"{_format_snapshot_time(point[0])} {point[1]}{line}"

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
        why=_text(_display_signal_reason(row)),
    )


def _percent_number(value: Any) -> float | None:
    text = str(value or "").strip().replace("%", "").replace("+", "")
    if not text or text == "—":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _signal_sort_score(row: dict[str, Any]) -> tuple[int, float]:
    grade_score = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(
        str(row.get("grade") or "").upper(),
        0,
    )
    value = _percent_number(row.get("ev"))
    if value is None:
        value = _percent_number(row.get("edge")) or 0.0
    return (grade_score, value)


def _signal_display_value(row: dict[str, Any]) -> str:
    ev = str(row.get("ev") or "").strip()
    if ev and ev != "—":
        return ev
    edge = str(row.get("edge") or "").strip()
    return edge if edge else "—"


def _market_chip_label(row: dict[str, Any]) -> str:
    label = str(row.get("market_label") or "")
    if label.startswith("胜平负"):
        return "胜平负"
    if label.startswith("亚洲让球"):
        return "让球"
    if label.startswith("大小球"):
        return "大小球"
    return label.split(" - ", 1)[0] if label else "盘口"


def _group_signal_rows_by_match(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("kickoff_at_utc") or ""),
            str(row.get("source_matchup") or row.get("matchup") or ""),
        )
        group = grouped.setdefault(
            key,
            {
                "rows": [],
                "matchup": row.get("matchup"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "source_home_team": row.get("source_home_team"),
                "source_away_team": row.get("source_away_team"),
                "competition_id": row.get("competition_id"),
                "competition_label": row.get("competition_label"),
                "stage_group": row.get("stage_group"),
                "kickoff_at_utc": row.get("kickoff_at_utc"),
                "kickoff_date": row.get("kickoff_date"),
                "kickoff_date_iso": _row_date_iso(row),
                "kickoff_time": row.get("kickoff_time") or row.get("kickoff_at_utc"),
                "updated_time": row.get("updated_time"),
                "updated_label": row.get("updated_label"),
                "next_update_time": row.get("next_update_time"),
                "next_update_label": row.get("next_update_label"),
                "match_decision_summary": row.get("match_decision_summary") or "—",
                "match_decision_p_hit_safe": row.get("match_decision_p_hit_safe") or "—",
            },
        )
        group["rows"].append(row)

    match_groups = list(grouped.values())
    for group in match_groups:
        signals = group["rows"]
        top = max(signals, key=_signal_sort_score)
        grade_bucket_set = set()
        for signal in signals:
            bucket = _grade_bucket(signal.get("grade"))
            if bucket != "all":
                grade_bucket_set.add(bucket)
        grade_buckets = [
            bucket for bucket in ("strong", "watch", "weak") if bucket in grade_bucket_set
        ]
        markets = []
        for signal in signals:
            chip = _market_chip_label(signal)
            if chip and chip not in markets:
                markets.append(chip)
        group["top_signal"] = top
        group["grade_buckets"] = grade_buckets or ["all"]
        group["market_chips"] = markets
        group["search_text"] = " ".join(_row_search_text(signal) for signal in signals)
    match_groups.sort(
        key=lambda group: (
            str(group.get("kickoff_at_utc") or ""),
            str(group.get("matchup") or ""),
        )
    )
    return match_groups


def _render_market_chips(labels: list[str]) -> str:
    return "".join('<span class="market-chip">{}</span>'.format(_text(label)) for label in labels)


def _date_strip_entries(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates: dict[str, dict[str, Any]] = {}
    for group in groups:
        date_iso = str(group.get("kickoff_date_iso") or "")
        date_label = str(group.get("kickoff_date") or "日期暂不可用")
        if not date_iso:
            continue
        entry = dates.setdefault(
            date_iso,
            {
                "label": date_label,
                "match_count": 0,
                "signal_count": 0,
            },
        )
        entry["match_count"] += 1
        entry["signal_count"] += len(group.get("rows") or [])
    return [
        {
            "iso": date_iso,
            "label": values["label"],
            "match_count": values["match_count"],
            "signal_count": values["signal_count"],
        }
        for date_iso, values in sorted(dates.items())
    ]


def _render_date_strip(groups: list[dict[str, Any]]) -> str:
    entries = _date_strip_entries(groups)
    total_matches = len(groups)
    total_signals = sum(len(group.get("rows") or []) for group in groups)
    cards = [
        (
            '<button class="date-card active" data-date-filter="all" type="button" '
            'aria-pressed="true"><span>全部 日期</span><strong>{matches}场 · {signals}分歧</strong></button>'
        ).format(matches=_text(total_matches), signals=_text(total_signals))
    ]
    for entry in entries:
        cards.append(
            (
                '<button class="date-card" data-date-filter="{date_iso}" type="button" '
                'aria-pressed="false"><span>{label}</span><strong>{matches}场 · {signals}分歧</strong></button>'
            ).format(
                date_iso=_text(entry["iso"]),
                label=_text(_date_button_label(str(entry["label"]))),
                matches=_text(entry["match_count"]),
                signals=_text(entry["signal_count"]),
            )
        )
    return '<section class="date-strip" aria-label="比赛日期">{}</section>'.format("".join(cards))


def _matchup_with_vs(value: Any) -> str:
    text = str(value or "").strip()
    return text.replace(" 对 ", " vs ") if text else "待确认对阵"


def _market_bucket(row: dict[str, Any]) -> str:
    label = str(row.get("market_label") or "")
    if label.startswith("胜平负"):
        return "1x2"
    if label.startswith("亚洲让球"):
        return "handicap"
    if label.startswith("大小球"):
        return "total"
    return "other"


def _render_workbench_market_tabs(signals: list[dict[str, Any]]) -> str:
    counts = {"1x2": 0, "handicap": 0, "total": 0, "other": 0}
    for signal in signals:
        counts[_market_bucket(signal)] += 1
    tabs = [
        ("all", "全部", len(signals)),
        ("1x2", "胜平负", counts["1x2"]),
        ("handicap", "让球", counts["handicap"]),
        ("total", "大小球", counts["total"]),
    ]
    if counts["other"]:
        tabs.append(("other", "其他", counts["other"]))
    buttons = []
    for index, (bucket, label, count) in enumerate(tabs):
        active = index == 0
        buttons.append(
            (
                '<button type="button" class="market-tab{active}" data-workbench-market-filter="{bucket}" '
                'aria-pressed="{pressed}">{label}<span>{count}</span></button>'
            ).format(
                active=" active" if active else "",
                bucket=_text(bucket),
                pressed="true" if active else "false",
                label=_text(label),
                count=_text(count),
            )
        )
    return '<div class="market-tabs" aria-label="盘口分类">{}</div>'.format("".join(buttons))


def _workbench_detail_value(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "—"


def _render_workbench_inline_detail(signal: dict[str, Any]) -> str:
    prediction_result = signal.get("prediction_result") or {}
    prediction_label = prediction_result.get("label") or "待赛"
    items = signal.get("inline_detail_items") or [
        ("盘口方向", signal.get("market_label")),
        ("预测状态", prediction_label),
        ("模型", signal.get("model_prob")),
        ("市场", signal.get("market_prob")),
        ("EV", signal.get("ev")),
        ("EDGE", signal.get("edge")),
        ("等级", signal.get("grade")),
        ("新鲜度", signal.get("freshness")),
        ("信号原因", _display_signal_reason(signal)),
    ]
    detail_items = []
    for label, value in items:
        detail_items.append(
            "<div><dt>{label}</dt><dd>{value}</dd></div>".format(
                label=_text(label),
                value=_text(_workbench_detail_value(value)),
            )
        )
    return (
        '<div class="workbench-inline-detail">'
        '<dl class="workbench-inline-detail-grid">{items}</dl>'
        "{trend}"
        "</div>"
    ).format(
        items="".join(detail_items),
        trend=_render_odds_trend(signal.get("odds_trend_points") or []),
    )


def _render_workbench_signal_rows(
    signals: list[dict[str, Any]],
    match_index: int,
    detail_prefix: str = "workbench-signal-detail",
    row_class: str = "",
) -> str:
    rows = []
    for signal_index, signal in enumerate(signals):
        stale_class = " is-stale" if signal.get("stale") else ""
        detail_id = f"{detail_prefix}-{match_index}-{signal_index}"
        rows.append(
            '<tr class="workbench-signal-row{stale_class}{row_class}" role="button" tabindex="0" '
            'aria-expanded="false" aria-controls="{detail_id}" data-workbench-signal-detail="{detail_id}" '
            'data-workbench-market="{market_bucket}" '
            'data-grade="{grade_bucket}" data-date="{date}" data-date-iso="{date_iso}" '
            'data-league="{competition_id}" data-search="{search}">'
            "<td>{market}</td>"
            '<td><span class="grade-pill {grade_class}">{grade}</span></td>'
            "<td>{prediction}</td>"
            '<td class="numeric">{model_prob}</td>'
            '<td class="numeric">{market_prob}</td>'
            '<td class="numeric edge-cell"><strong>{ev}</strong><span>{edge}</span></td>'
            "<td>{freshness}</td>"
            "<td>{why}</td>"
            "</tr>"
            '<tr class="workbench-inline-detail-row" id="{detail_id}" hidden>'
            '<td colspan="8">{detail}</td></tr>'.format(
                stale_class=stale_class,
                row_class=row_class,
                detail_id=_text(detail_id),
                market_bucket=_text(_market_bucket(signal)),
                grade_bucket=_text(_grade_bucket(signal.get("grade"))),
                date=_text(signal.get("kickoff_date")),
                date_iso=_text(_row_date_iso(signal)),
                competition_id=_text(signal.get("competition_id")),
                search=_text(_row_search_text(signal)),
                market=_text(signal.get("market_label")),
                prediction=_render_prediction_cell(signal),
                model_prob=_text(signal.get("model_prob")),
                market_prob=_text(signal.get("market_prob")),
                ev=_text(signal.get("ev")),
                edge=_text(signal.get("edge")),
                grade_class=_text(_grade_class(signal.get("grade"))),
                grade=_text(signal.get("grade")),
                freshness=_render_freshness_badge(signal.get("freshness")),
                why=_render_signal_reason(signal),
                detail=_render_workbench_inline_detail(signal),
            )
        )
    return "".join(rows)


def _render_workbench_warning(signals: list[dict[str, Any]]) -> str:
    if not any(signal.get("stale") for signal in signals):
        return ""
    return (
        '<p class="workbench-warning-strip">'
        '<span class="warning-mark">!</span>'
        "部分信号来自缓存数据，盘口数据已过期，等待下一轮刷新。"
        "</p>"
    )


def _render_workbench_ledger(
    rows: list[dict[str, Any]],
    competitions: list[dict[str, Any]] | None = None,
) -> str:
    if not rows:
        return """
        <section class="workbench-shell premium-intelligence-workbench">
          <section class="date-strip" aria-label="比赛日期">
            <button class="date-card active" data-date-filter="all" type="button" aria-pressed="true">
              <span>全部 日期</span><strong>0场 · 0信号</strong>
            </button>
          </section>
          {workbench_filters}
          <section class="ledger-workbench">
            <div class="empty-state">
              <h2>暂无价值分歧</h2>
              <p>当前快照没有达到阈值的价值分歧行。</p>
            </div>
          </section>
        </section>
        """.format(workbench_filters=_render_workbench_filter_row(competitions=competitions))

    groups = _group_signal_rows_by_match(rows)
    list_rows = []
    detail_panels = []
    for index, group in enumerate(groups):
        signals = group["rows"]
        top = group["top_signal"]
        detail_id = f"workbench-match-{index}"
        active_class = " active" if index == 0 else ""
        grade = top.get("grade", "")
        grade_class = _grade_class(grade)
        signal_count = len(signals)
        matchup = group.get("matchup")
        matchup_html = _render_matchup_teams(
            group.get("home_team"),
            group.get("away_team"),
            group.get("source_home_team"),
            group.get("source_away_team"),
            separator="对",
            fallback=matchup,
        )
        title_html = _render_matchup_teams(
            group.get("home_team"),
            group.get("away_team"),
            group.get("source_home_team"),
            group.get("source_away_team"),
            separator="vs",
            inline=True,
            fallback=matchup,
        )
        list_rows.append(
            '<tr class="match-list-row{active}" role="button" tabindex="0" aria-expanded="{expanded}" '
            'data-workbench-match-target="{detail_id}" data-signal-count="{signal_count}" '
            'data-grade="{grade_bucket}" data-grade-buckets="{grade_buckets}" '
            'data-date="{date}" data-date-iso="{date_iso}" data-league="{competition_id}" data-search="{search}">'
            "{kickoff_cell}"
            "<td><strong>{matchup}</strong></td>"
            "<td><strong>{match_decision}</strong></td>"
            "<td>{stage_group}</td>"
            "<td><strong>{signal_count}条分歧</strong></td>"
            "<td><strong>{decision_prob}</strong><span>安全胜率</span></td>"
            "</tr>".format(
                active=active_class,
                expanded="true" if index == 0 else "false",
                detail_id=_text(detail_id),
                signal_count=_text(signal_count),
                grade_bucket=_text(_grade_bucket(grade)),
                grade_buckets=_text(" ".join(group["grade_buckets"])),
                date=_text(group.get("kickoff_date")),
                date_iso=_text(group.get("kickoff_date_iso")),
                competition_id=_text(group.get("competition_id")),
                search=_text(group.get("search_text")),
                kickoff_cell=_render_match_kickoff_cell(group),
                matchup=matchup_html,
                stage_group=_text(
                    " · ".join(
                        str(part)
                        for part in (group.get("competition_label"), group.get("stage_group"))
                        if part
                    )
                ),
                match_decision=_text(group.get("match_decision_summary") or "—"),
                decision_prob=_text(group.get("match_decision_p_hit_safe") or "—"),
            )
        )
        detail_panels.append(
            '<section class="workbench-detail{active}" id="{detail_id}" data-workbench-detail="{detail_id}" {hidden}>'
            '<div class="detail-title-row"><h2>{title} · 价值分歧明细</h2></div>'
            '<div class="detail-metrics">'
            '<div><span>本场首选</span><strong>{match_decision}</strong></div>'
            '<div><span>价值分歧</span><strong><span class="grade-pill {grade_class}">{grade}</span></strong></div>'
            '<div><span>分歧 EDGE</span><strong>{top_edge}</strong></div>'
            '<div><span>更新时间</span><strong>{updated}</strong></div>'
            '<div><span>下一次更新</span><strong>{next_update}</strong></div>'
            '</div>'
            '{tabs}'
            '<div class="workbench-table-wrap"><table class="workbench-signal-table">'
            '<thead><tr><th>市场 / 盘口</th><th>分歧等级</th><th>预测</th><th>模型概率</th>'
            '<th>市场概率</th><th>EV / EDGE</th><th>新鲜度</th><th>分歧原因</th></tr></thead>'
            '<tbody>{signal_rows}</tbody></table></div>'
            '{warning}'
            '<p class="workbench-market-empty" hidden>当前分类下没有价值分歧。</p>'
            '</section>'.format(
                active=active_class,
                detail_id=_text(detail_id),
                hidden="" if index == 0 else "hidden",
                title=title_html,
                match_decision=_text(group.get("match_decision_summary") or "—"),
                grade_class=_text(grade_class),
                grade=_text(grade),
                top_edge=_text(top.get("edge") or _signal_display_value(top)),
                updated=_text(group.get("updated_time")),
                next_update=_text(group.get("next_update_time")),
                tabs=_render_workbench_market_tabs(signals),
                signal_rows=_render_workbench_signal_rows(signals, index),
                warning=_render_workbench_warning(signals),
            )
        )

    return """
    <section class="workbench-shell premium-intelligence-workbench">
      {date_strip}
      {workbench_filters}
      <section class="ledger-workbench">
        <aside class="match-list-panel">
          <div class="panel-heading">
            <h2>本日比赛 {match_count}场</h2>
          </div>
          <div class="match-list-scroll">
            <table class="match-list-table">
              <thead>
                <tr><th>开赛时间</th><th>对阵</th><th>本场首选</th><th>组别</th><th>价值分歧</th><th>安全概率</th></tr>
              </thead>
              <tbody>{list_rows}</tbody>
            </table>
          </div>
          <p class="workbench-no-results" hidden>没有符合当前筛选的比赛。</p>
        </aside>
        <section class="signal-detail-panel">
          {detail_panels}
        </section>
      </section>
    </section>
    """.format(
        date_strip=_render_date_strip(groups),
        workbench_filters=_render_workbench_filter_row(competitions=competitions),
        match_count=_text(len(groups)),
        list_rows="".join(list_rows),
        detail_panels="".join(detail_panels),
    )


def _render_match_grouped_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return """
        <section class="ledger-table-wrap match-ledger-wrap" data-mode-panel="match">
          <div class="empty-state">
            <h2>暂无价值分歧</h2>
            <p>当前快照没有达到阈值的价值分歧行。</p>
          </div>
        </section>
        """

    groups = _group_signal_rows_by_match(rows)
    grouped_by_date: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        grouped_by_date[str(group.get("kickoff_date") or "日期暂不可用")].append(group)

    total_matches = len(groups)
    total_signals = len(rows)
    table_rows = []
    detail_index = 0
    for kickoff_date, date_groups in grouped_by_date.items():
        table_rows.append(
            "<tr class=\"match-date-row\" data-match-date-row=\"{date}\"><th colspan=\"9\">{label}</th></tr>".format(
                date=_text(kickoff_date),
                label=_text(kickoff_date),
            )
        )
        for group in date_groups:
            signals = group["rows"]
            top = group["top_signal"]
            detail_id = f"match-detail-{detail_index}"
            grade = top.get("grade", "")
            grade_class = _grade_class(grade)
            signal_count = len(signals)
            child_rows = []
            for signal in signals:
                child_rows.append(
                    '<tr class="match-signal-row" data-grade="{grade_bucket}" '
                    'data-date="{date}" data-date-iso="{date_iso}" data-league="{competition_id}" '
                    'data-search="{search}">'
                    "<td>{market}</td><td>{prediction}</td><td>{model_prob}</td>"
                    "<td>{market_prob}</td><td><strong>{ev}</strong><span>{edge}</span></td>"
                    '<td><span class="grade-pill {grade_class}">{grade}</span></td>'
                    "<td>{freshness}</td><td>{why}</td>"
                    "</tr>".format(
                        grade_bucket=_text(_grade_bucket(signal.get("grade"))),
                        date=_text(group.get("kickoff_date")),
                        date_iso=_text(group.get("kickoff_date_iso")),
                        competition_id=_text(signal.get("competition_id")),
                        search=_text(_row_search_text(signal)),
                        market=_text(signal.get("market_label")),
                        prediction=_render_prediction_cell(signal),
                        model_prob=_text(signal.get("model_prob")),
                        market_prob=_text(signal.get("market_prob")),
                        ev=_text(signal.get("ev")),
                        edge=_text(signal.get("edge")),
                        grade_class=_text(_grade_class(signal.get("grade"))),
                        grade=_text(signal.get("grade")),
                        freshness=_text(signal.get("freshness")),
                        why=_render_signal_reason(signal),
                    )
                )
            table_rows.append(
                '<tr class="match-row" role="button" tabindex="0" aria-expanded="false" '
                'aria-controls="{detail_id}" data-match-detail-target="{detail_id}" '
                'data-signal-count="{signal_count}" data-grade="{grade_bucket}" '
                'data-grade-buckets="{grade_buckets}" data-date="{date}" data-date-iso="{date_iso}" '
                'data-league="{competition_id}" data-search="{search}">'
                "<td><strong>{matchup}</strong><span>{stage_group}</span></td>"
                "<td>{kickoff}</td>"
                "<td><strong>{updated}</strong><span>{updated_label}</span></td>"
                "<td><strong>{next_update}</strong><span>{next_update_label}</span></td>"
                "<td><strong>{signal_count}条分歧</strong><span>{markets}</span></td>"
                "<td><span class=\"grade-pill {grade_class}\">{grade}</span></td>"
                "<td><strong>{top_value}</strong><span>{top_market}</span></td>"
                "<td>{freshness}</td>"
                '<td><button type="button" class="expand-button">展开 {signal_count}条</button></td>'
                "</tr>".format(
                    detail_id=_text(detail_id),
                    signal_count=_text(signal_count),
                    grade_bucket=_text(_grade_bucket(grade)),
                    grade_buckets=_text(" ".join(group["grade_buckets"])),
                    date=_text(group.get("kickoff_date")),
                    date_iso=_text(group.get("kickoff_date_iso")),
                    competition_id=_text(group.get("competition_id")),
                    search=_text(group.get("search_text")),
                    matchup=_text(group.get("matchup")),
                    stage_group=_text(
                        " · ".join(
                            str(part)
                            for part in (group.get("competition_label"), group.get("stage_group"))
                            if part
                        )
                    ),
                    kickoff=_text(group.get("kickoff_time")),
                    updated=_text(group.get("updated_time")),
                    updated_label=_text(group.get("updated_label")),
                    next_update=_text(group.get("next_update_time")),
                    next_update_label=_text(group.get("next_update_label")),
                    markets=_render_market_chips(group["market_chips"]),
                    grade_class=_text(grade_class),
                    grade=_text(grade),
                    top_value=_text(_signal_display_value(top)),
                    top_market=_text(top.get("market_label")),
                    freshness=_text(top.get("freshness")),
                )
            )
            table_rows.append(
                '<tr class="match-detail-row" id="{detail_id}" hidden>'
                '<td colspan="9"><div class="match-detail">'
                '<div class="match-detail-heading"><strong>{matchup} · {signal_count}条价值分歧</strong>'
                '<span>按盘口拆开查看研究分歧，仍不构成投注建议。</span></div>'
                '<div class="table-scroll"><table class="match-signal-table">'
                "<thead><tr><th>盘口</th><th>预测结果</th><th>模型概率</th><th>市场概率</th>"
                "<th>EV / Edge</th><th>分歧等级</th><th>新鲜度</th><th>分歧原因</th></tr></thead>"
                "<tbody>{child_rows}</tbody></table></div>"
                "</div></td></tr>".format(
                    detail_id=_text(detail_id),
                    matchup=_text(group.get("matchup")),
                    signal_count=_text(signal_count),
                    child_rows="".join(child_rows),
                )
            )
            detail_index += 1

    return """
    <section class="ledger-table-wrap match-ledger-wrap" data-mode-panel="match">
      <table class="match-ledger-table">
        <caption>
          <span>按比赛查看</span>
          <small>本日比赛：{match_count} 场 / {signal_count} 条价值分歧</small>
        </caption>
        <thead>
          <tr>
            <th scope="col">对阵</th>
            <th scope="col">开赛 (北京时间)</th>
            <th scope="col">更新</th>
            <th scope="col">下次更新</th>
            <th scope="col">价值分歧</th>
            <th scope="col">分歧等级</th>
            <th scope="col">分歧 EV / Edge</th>
            <th scope="col">新鲜度</th>
            <th scope="col">明细</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      <p class="match-no-results" hidden>没有符合当前筛选的比赛。</p>
    </section>
    """.format(
        match_count=_text(total_matches),
        signal_count=_text(total_signals),
        rows="\n".join(table_rows),
    )


def _render_signal_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return """
        <section class="ledger-table-wrap signal-ledger-wrap" data-mode-panel="signal" hidden>
          <div class="empty-state">
          <h2>暂无价值分歧</h2>
          <p>当前快照没有达到阈值的价值分歧行。</p>
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
                "data-date=\"{date}\" data-date-iso=\"{date_iso}\" data-league=\"{competition_id}\" data-search=\"{search}\">"
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
                    date_iso=_text(_row_date_iso(row)),
                    competition_id=_text(row.get("competition_id")),
                    search=_text(search_text),
                    matchup=_text(row.get("matchup")),
                    stage_group=_text(
                        " · ".join(
                            str(part)
                            for part in (row.get("competition_label"), row.get("stage_group"))
                            if part
                        )
                    ),
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
    <section class="ledger-table-wrap signal-ledger-wrap" data-mode-panel="signal" hidden>
      <table class="ledger-table">
        <caption>价值分歧台账</caption>
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


def _render_live_ledger(
    rows: list[dict[str, Any]],
    competitions: list[dict[str, Any]] | None = None,
) -> str:
    return """
    <section class="live-ledger">
      <div class="ledger-mode-bar legacy-mode-bar" aria-hidden="true">
        <div>
          <h2>实时方向</h2>
          <p class="muted">默认按比赛聚合，先看每场首选方向，展开后查看价值分歧。</p>
        </div>
        <div class="mode-tabs" aria-label="信号展示方式">
          <button type="button" class="mode-tab active" data-mode-filter="match" aria-pressed="true">按比赛</button>
          <button type="button" class="mode-tab" data-mode-filter="signal" aria-pressed="false">按信号</button>
        </div>
      </div>
      {workbench}
      <div class="legacy-ledger-views" hidden>
        {match_table}
        {signal_table}
      </div>
    </section>
    """.format(
        workbench=_render_workbench_ledger(rows, competitions=competitions),
        match_table=_render_match_grouped_table(rows),
        signal_table=_render_signal_table(rows),
    )


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
        "pre_35m_lineup_confirm": "T-35分钟",
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
        <li>临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-35分钟 / T-25分钟</li>
        <li>低额度：每天 1 次，并保留 T-90 / T-55 / T-35 / T-25</li>
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
    enrichment_errors = _quality_values(snapshot, "enrichment_errors")
    counts = snapshot.get("counts") or {}
    matches = snapshot.get("matches") or []
    fixtures_available = bool(counts.get("fixtures") or matches)
    odds_attention = bool(source_errors or stale_sources or missing_odds)
    elo_attention = bool(missing_elo)
    input_attention = bool(
        source_errors
        or stale_sources
        or missing_odds
        or missing_elo
        or time_mismatches
        or enrichment_errors
    )

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
        <li>富化异常：{enrichment_error_count}</li>
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
        enrichment_error_count=_text(len(enrichment_errors)),
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
          <li>模型输出只作为研究分析，可能出错。</li>
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
        match.get("match_decision_summary", ""),
        match.get("competition_label", ""),
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


def _format_odds(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _history_top_signal(signals: list[dict[str, Any]]) -> dict[str, Any]:
    if not signals:
        return {"grade": "", "ev": "—", "edge": "—"}
    scores = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    return max(signals, key=lambda signal: scores.get(str(signal.get("grade") or ""), 0))


def _history_signal_row(
    item: dict[str, Any],
    match: dict[str, Any],
    day: dict[str, Any],
) -> dict[str, Any]:
    outcome = str(item.get("outcome") or "")
    odds = _format_odds(item.get("odds"))
    prediction_result = {
        "status": _outcome_class(outcome),
        "label": outcome or "已完赛",
        "detail": item.get("detail") or "",
    }
    return {
        "kickoff_at_utc": match.get("kickoff_at_utc"),
        "kickoff_date": day.get("date_label"),
        "kickoff_time": match.get("kickoff_time"),
        "matchup": match.get("matchup"),
        "source_matchup": match.get("matchup"),
        "source_home_team": match.get("source_home_team"),
        "source_away_team": match.get("source_away_team"),
        "competition_id": match.get("competition_id"),
        "competition_label": match.get("competition_label"),
        "stage_group": match.get("stage_group"),
        "market_label": item.get("market_label"),
        "prediction_result": prediction_result,
        "model_prob": "—",
        "market_prob": "—",
        "ev": odds,
        "edge": "收盘赔率",
        "grade": item.get("grade"),
        "freshness": "收盘",
        "explanation": "使用开球前最后一轮 closing 快照定格，赛后按实际比分复盘。",
        "odds_trend_points": item.get("trend_points") or [],
        "inline_detail_items": [
            ("盘口方向", item.get("market_label")),
            ("预测状态", outcome or "已完赛"),
            ("收盘赔率", odds),
            ("等级", item.get("grade")),
            ("赛果说明", item.get("detail") or "—"),
            ("复盘口径", "closing（开球前最后一轮）定格"),
        ],
    }


def _history_workbench_groups(view: dict[str, Any]) -> list[dict[str, Any]]:
    groups = []
    for day in view["days"]:
        for match in day["matches"]:
            signals = [
                _history_signal_row(item, match, day)
                for item in match.get("detail_signals") or []
            ]
            top = _history_top_signal(signals)
            grade_buckets = []
            for signal in signals:
                bucket = _grade_bucket(signal.get("grade"))
                if bucket != "all" and bucket not in grade_buckets:
                    grade_buckets.append(bucket)
            groups.append(
                {
                    "rows": signals,
                    "top_signal": top,
                    "matchup": match.get("matchup"),
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                    "source_home_team": match.get("source_home_team"),
                    "source_away_team": match.get("source_away_team"),
                    "competition_id": match.get("competition_id"),
                    "competition_label": match.get("competition_label"),
                    "stage_group": match.get("stage_group"),
                    "kickoff_at_utc": match.get("kickoff_at_utc"),
                    "kickoff_date": day.get("date_label"),
                    "kickoff_date_iso": day.get("date_iso"),
                    "kickoff_time": match.get("kickoff_time"),
                    "closing_snapshot_at": match.get("closing_snapshot_at"),
                    "match_decision_summary": match.get("match_decision_summary"),
                    "score_label": match.get("score_label"),
                    "grade_buckets": grade_buckets or ["all"],
                    "search_text": _finished_search_text(match),
                }
            )
    return groups


def _render_history_review_notes(summary: dict[str, Any] | None) -> str:
    summary = summary or {}
    match_count = int(summary.get("match_count") or 0)
    skipped = int(summary.get("skipped_no_closing") or 0)
    sample = summary.get("sample") or {}
    notes = []
    if match_count and sample.get("sample_too_small"):
        notes.append(
            "复盘样本仍偏小：当前仅 {count} 场 closing 复盘，"
            "仅作为观察，不用于调参或执行建议。".format(count=_text(match_count))
        )
    if skipped:
        notes.append(
            "缺少 closing 记录：{count} 场；这些赛果未纳入 closing 口径复盘。".format(
                count=_text(skipped)
            )
        )
    if not notes:
        return ""
    return '<div class="history-review-notes">{}</div>'.format(
        "".join("<p>{}</p>".format(_text(note)) for note in notes)
    )


def _render_history_workbench(
    groups: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
    competitions: list[dict[str, Any]] | None = None,
) -> str:
    review_notes = _render_history_review_notes(summary)
    if not groups:
        return (
            '<div data-view-panel="history" hidden>'
            '<section class="workbench-shell history-workbench-shell premium-intelligence-workbench">'
            '<section class="date-strip" aria-label="历史日期">'
            '<button class="date-card active" data-date-filter="all" type="button" aria-pressed="true">'
            "<span>全部 日期</span><strong>0场 · 0信号</strong></button></section>"
            '{filters}{review_notes}<section class="ledger-workbench"><div class="empty-state">'
            "<h2>暂无历史回顾</h2><p>还没有可用于复盘的已完赛 closing 记录。</p>"
            "</div></section></section></div>"
        ).format(
            filters=_render_workbench_filter_row(
                search_id="history-ledger-search",
                league_id="history-league-filter",
                competitions=competitions,
            ),
            review_notes=review_notes,
        )

    list_rows = []
    detail_panels = []
    for index, group in enumerate(groups):
        signals = group["rows"]
        top = group["top_signal"]
        detail_id = f"history-match-{index}"
        active_class = " active" if index == 0 else ""
        grade = top.get("grade", "")
        grade_class = _grade_class(grade)
        signal_count = len(signals)
        matchup_html = _render_matchup_teams(
            group.get("home_team"),
            group.get("away_team"),
            group.get("source_home_team"),
            group.get("source_away_team"),
            separator="对",
            fallback=group.get("matchup"),
        )
        title_html = _render_matchup_teams(
            group.get("home_team"),
            group.get("away_team"),
            group.get("source_home_team"),
            group.get("source_away_team"),
            separator="vs",
            inline=True,
            fallback=group.get("matchup"),
        )
        list_rows.append(
            '<tr class="match-list-row{active} history-match-row" role="button" tabindex="0" '
            'aria-expanded="{expanded}" data-workbench-match-target="{detail_id}" '
            'data-signal-count="{signal_count}" data-grade="{grade_bucket}" '
            'data-grade-buckets="{grade_buckets}" data-date="{date}" data-date-iso="{date_iso}" '
            'data-league="{competition_id}" data-search="{search}">'
            "<td>{kickoff}</td><td><strong>{matchup}</strong></td>"
            '<td><span class="grade-pill {grade_class}">{grade}</span></td>'
            "<td>{stage_group}</td><td><strong>{signal_count}条信号</strong></td>"
            "<td><strong>{score}</strong><span>赛果</span></td></tr>".format(
                active=active_class,
                expanded="true" if index == 0 else "false",
                detail_id=_text(detail_id),
                signal_count=_text(signal_count),
                grade_bucket=_text(_grade_bucket(grade)),
                grade_buckets=_text(" ".join(group["grade_buckets"])),
                date=_text(group.get("kickoff_date")),
                date_iso=_text(group.get("kickoff_date_iso")),
                competition_id=_text(group.get("competition_id")),
                search=_text(group.get("search_text")),
                kickoff=_text(group.get("kickoff_time")),
                matchup=matchup_html,
                stage_group=_text(
                    " · ".join(
                        str(part)
                        for part in (group.get("competition_label"), group.get("stage_group"))
                        if part
                    )
                ),
                grade_class=_text(grade_class),
                grade=_text(grade or "—"),
                score=_text(group.get("score_label")),
            )
        )
        detail_panels.append(
            '<section class="workbench-detail{active} history-workbench-detail" id="{detail_id}" '
            'data-workbench-detail="{detail_id}" {hidden}>'
            '<div class="detail-title-row"><h2>{title} · 历史回顾</h2></div>'
            '<p class="muted history-workbench-note"><strong>已完赛战绩</strong>：'
            "closing（开球前最后一轮）口径；仅用于研究分析，不构成投注建议。</p>"
            '<div class="detail-metrics">'
            '<div><span>最强等级</span><strong><span class="grade-pill {grade_class}">{grade}</span></strong></div>'
            "<div><span>赛果</span><strong>{score}</strong></div>"
            "<div><span>收盘首选</span><strong>{closing_decision}</strong></div>"
            "<div><span>收盘快照</span><strong>{closing}</strong></div>"
            "<div><span>信号数</span><strong>{signal_count}</strong></div>"
            "</div>"
            "{tabs}"
            '<div class="workbench-table-wrap"><table class="workbench-signal-table">'
            "<thead><tr><th>市场 / 盘口</th><th>等级</th><th>预测</th><th>模型概率</th>"
            "<th>市场概率</th><th>收盘赔率</th><th>状态</th><th>信号原因</th></tr></thead>"
            "<tbody>{signal_rows}</tbody></table></div>"
            '<p class="workbench-market-empty" hidden>当前分类下没有信号。</p>'
            "</section>".format(
                active=active_class,
                detail_id=_text(detail_id),
                hidden="" if index == 0 else "hidden",
                title=title_html,
                grade_class=_text(grade_class),
                grade=_text(grade or "—"),
                score=_text(group.get("score_label")),
                closing_decision=_text(group.get("match_decision_summary") or "—"),
                closing=_text(_format_snapshot_time(group.get("closing_snapshot_at")) or "未记录"),
                signal_count=_text(signal_count),
                tabs=_render_workbench_market_tabs(signals),
                signal_rows=_render_workbench_signal_rows(
                    signals,
                    index,
                    detail_prefix="history-signal-detail",
                    row_class=" history-signal-row",
                ),
            )
        )

    return """
    <div data-view-panel="history" hidden>
        <section class="workbench-shell history-workbench-shell premium-intelligence-workbench">
        {date_strip}
        {filters}
        {review_notes}
        <section class="ledger-workbench">
          <aside class="match-list-panel">
            <div class="panel-heading">
              <h2>历史比赛 {match_count}场</h2>
            </div>
            <div class="match-list-scroll">
              <table class="match-list-table">
                <thead>
                  <tr><th>开赛时间</th><th>对阵</th><th>最强等级</th><th>阶段</th><th>信号数</th><th>赛果</th></tr>
                </thead>
                <tbody>{list_rows}</tbody>
              </table>
            </div>
            <p class="workbench-no-results" hidden>没有符合当前筛选的历史比赛。</p>
          </aside>
          <section class="signal-detail-panel">
            {detail_panels}
          </section>
        </section>
      </section>
    </div>
    """.format(
        date_strip=_render_date_strip(groups),
        filters=_render_workbench_filter_row(
            search_id="history-ledger-search",
            league_id="history-league-filter",
            competitions=competitions,
        ),
        review_notes=review_notes,
        match_count=_text(len(groups)),
        list_rows="".join(list_rows),
        detail_panels="".join(detail_panels),
    )


def _render_finished_section(
    snapshot: dict[str, Any],
    competitions: list[dict[str, Any]] | None = None,
) -> str:
    view = build_finished_view(snapshot)
    return _render_history_workbench(
        _history_workbench_groups(view),
        view.get("summary"),
        competitions=competitions,
    )


def build_research_ledger_html(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> str:
    snapshot_at = _format_snapshot_time(snapshot.get("snapshot_at"))
    signal_rows = project_signal_rows(snapshot, previous_snapshot=previous_snapshot)
    competitions = competition_options(snapshot)
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>2026 世界杯 | 研究台账</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --panel-soft: #fbfcfe;
      --text: #102033;
      --muted: #64748b;
      --line: #dce4ee;
      --line-soft: #eef2f7;
      --accent: #0f8f85;
      --accent-strong: #047c73;
      --accent-soft: #e8f7f5;
      --emerald: #0b7a43;
      --cobalt: #2563eb;
      --warn: #a16207;
      --warn-soft: #fff7e6;
      --error: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
      overflow-x: hidden;
    }}
    main {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 18px 20px 42px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      margin: 0 -20px 18px;
      padding: 0 20px 14px;
      border-bottom: 1px solid var(--line-soft);
      background: rgba(255, 255, 255, 0.76);
    }}
    .header-actions {{
      display: grid;
      justify-items: end;
      gap: 8px;
    }}
    .brand-row {{
      display: flex;
      align-items: center;
      gap: 28px;
      min-width: 0;
    }}
    .brand-title {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--text);
      font-size: 22px;
      font-weight: 900;
      white-space: nowrap;
    }}
    .primary-nav {{
      display: flex;
      align-items: center;
      gap: 24px;
      min-width: 0;
      overflow-x: auto;
      scrollbar-width: none;
    }}
    .primary-nav-item {{
      position: relative;
      display: inline-flex;
      align-items: center;
      min-height: 44px;
      color: #334155;
      font-size: 14px;
      font-weight: 850;
      white-space: nowrap;
    }}
    .primary-nav-item.active {{
      color: var(--accent-strong);
    }}
    .primary-nav-item.active::after {{
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 3px;
      border-radius: 999px;
      background: var(--accent);
    }}
    .status-line {{
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .refresh-dot {{
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-soft);
    }}
    .screen-title {{
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; line-height: 1.15; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    h3 {{ margin: 14px 0 6px; font-size: 13px; text-transform: uppercase; color: var(--muted); }}
    p {{ margin: 0 0 10px; line-height: 1.55; }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 5px 0; }}
    .eyebrow {{ margin: 0 0 6px; color: var(--accent); font-weight: 800; }}
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
      margin: 0;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: var(--muted);
      max-width: 760px;
      font-size: 12px;
      text-align: right;
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
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
    }}
    .utility-date-row {{
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
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
    .workbench-filter-row {{
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
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
    .date-picker {{
      flex: 0 0 220px;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }}
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
    .workbench-tools {{
      display: flex;
      align-items: end;
      gap: 10px;
      flex: 1 1 420px;
      justify-content: flex-end;
      min-width: 0;
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
    .legacy-mode-bar, .legacy-ledger-views {{ display: none !important; }}
    .workbench-shell {{
      display: grid;
      gap: 10px;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
    }}
    .date-strip {{
      display: grid;
      grid-auto-flow: column;
      grid-auto-columns: minmax(166px, 1fr);
      gap: 10px;
      width: 100%;
      min-width: 0;
      max-width: 100%;
      overflow-x: auto;
      contain: inline-size layout paint;
      padding: 1px 0 7px;
      scrollbar-width: thin;
    }}
    .date-card {{
      min-height: 58px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      cursor: pointer;
      padding: 9px 13px;
      text-align: center;
      font: inherit;
      box-shadow: 0 1px 2px rgba(16, 32, 51, 0.04);
    }}
    .date-card span {{
      display: block;
      color: var(--text);
      font-weight: 800;
      font-size: 14px;
      line-height: 1.25;
    }}
    .date-card strong {{
      display: block;
      margin-top: 5px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.25;
    }}
    .date-card.active {{
      border-color: #76c7bf;
      background: var(--accent-soft);
      color: #ffffff;
      box-shadow: inset 0 -3px 0 var(--accent), 0 4px 14px rgba(15, 143, 133, 0.10);
    }}
    .date-card.active span {{ color: #075e58; }}
    .date-card.active strong {{ color: #0f766e; }}
    .ledger-workbench {{
      display: grid;
      grid-template-columns: minmax(520px, 0.72fr) minmax(0, 1.28fr);
      min-height: 600px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(16, 32, 51, 0.05);
    }}
    .match-list-panel {{
      min-width: 0;
      border-right: 1px solid var(--line);
      background: #ffffff;
    }}
    .signal-detail-panel {{
      min-width: 0;
      padding: 18px 20px;
      background: var(--panel-soft);
    }}
    .panel-heading {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 18px 18px 13px;
      border-bottom: 1px solid var(--line-soft);
    }}
    .panel-heading h2 {{ margin: 0; font-size: 18px; }}
    .match-list-scroll {{
      overflow-x: hidden;
      overflow-y: auto;
      max-height: 600px;
    }}
    .match-list-table {{
      width: 100%;
      min-width: 0;
      table-layout: fixed;
      border-collapse: collapse;
    }}
    .match-list-table th:nth-child(1),
    .match-list-table td:nth-child(1) {{ width: 12%; }}
    .match-list-table th:nth-child(2),
    .match-list-table td:nth-child(2) {{ width: 22%; }}
    .match-list-table th:nth-child(3),
    .match-list-table td:nth-child(3) {{ width: 10%; text-align: center; }}
    .match-list-table th:nth-child(4),
    .match-list-table td:nth-child(4) {{ width: 29%; }}
    .match-list-table th:nth-child(5),
    .match-list-table td:nth-child(5) {{ width: 12%; }}
    .match-list-table th:nth-child(6),
    .match-list-table td:nth-child(6) {{ width: 15%; text-align: right; }}
    .match-list-table th {{
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f8fafc;
      color: #475569;
      font-size: 11px;
      text-transform: none;
      padding: 10px 10px;
    }}
    .match-list-table td {{
      min-width: 0;
      padding: 11px 10px;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .match-list-row {{
      cursor: pointer;
      background: #ffffff;
    }}
    .match-list-row td:first-child {{
      border-left: 3px solid transparent;
      font-weight: 800;
      white-space: nowrap;
    }}
    .match-list-row .match-kickoff-cell {{
      line-height: 1.15;
    }}
    .match-list-row .match-kickoff-cell span {{
      margin-top: 0;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.1;
    }}
    .match-list-row .match-kickoff-cell strong {{
      display: block;
      margin-top: 3px;
      color: var(--text);
      font-size: 13px;
      line-height: 1.1;
    }}
    .match-list-row strong {{ font-weight: 850; }}
    .match-list-row span {{
      display: block;
      color: var(--muted);
      margin-top: 4px;
      font-size: 11px;
    }}
    .team-matchup,
    .match-list-row .team-matchup {{
      display: inline-grid;
      gap: 2px;
      min-width: 0;
      max-width: 100%;
      color: var(--text);
      margin-top: 0;
      line-height: 1.15;
      vertical-align: middle;
    }}
    .team-matchup-inline,
    .detail-title-row .team-matchup-inline {{
      display: inline-flex;
      align-items: center;
      gap: 0;
      width: auto;
      max-width: 100%;
    }}
    .team-inline,
    .match-list-row .team-inline,
    .signal-row .team-inline {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-width: 0;
      max-width: 100%;
      color: inherit;
      margin-top: 0;
      white-space: nowrap;
      vertical-align: -2px;
    }}
    .team-inline span,
    .match-list-row .team-inline span,
    .signal-row .team-inline span {{
      display: inline-flex;
      align-items: center;
      color: inherit;
      margin-top: 0;
      min-width: 0;
      font-size: inherit;
      line-height: inherit;
    }}
    .team-name,
    .match-list-row .team-name {{
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .team-flag,
    .match-list-row .team-flag {{
      flex: 0 0 auto;
      width: 16px;
      height: 12px;
      justify-content: center;
      font-size: 11px;
      line-height: 12px;
      overflow: hidden;
      filter: saturate(0.95);
    }}
    .team-vs,
    .match-list-row .team-vs {{
      display: block;
      color: #94a3b8;
      margin: -1px 0 -1px 21px;
      font-size: 10px;
      font-weight: 800;
      line-height: 1;
    }}
    .detail-title-row h2 .team-inline {{
      gap: 7px;
      vertical-align: -1px;
    }}
    .detail-title-row h2 .team-flag {{
      width: 20px;
      height: 15px;
      font-size: 14px;
      line-height: 15px;
    }}
    .detail-title-row h2 .team-vs {{
      margin: 0 8px;
      color: #64748b;
      font-size: 14px;
      line-height: 1;
    }}
    .match-list-row.active td {{
      background: #effbf8;
      border-bottom-color: #cae8e3;
    }}
    .match-list-row.active td:first-child {{ border-left-color: var(--accent); }}
    .match-list-row:focus {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .workbench-no-results, .workbench-market-empty {{
      padding: 18px;
      color: var(--muted);
    }}
    .workbench-detail {{ min-width: 0; }}
    .workbench-detail[hidden] {{ display: none; }}
    .detail-title-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
    .detail-title-row h2 {{
      margin: 0;
      font-size: 20px;
      line-height: 1.35;
    }}
    .detail-metrics {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      margin-bottom: 14px;
      overflow: hidden;
    }}
    .detail-metrics div {{
      min-width: 0;
      padding: 11px 14px;
      border-right: 1px solid var(--line-soft);
      text-align: center;
    }}
    .detail-metrics div:last-child {{ border-right: 0; }}
    .detail-metrics span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      margin-bottom: 5px;
    }}
    .detail-metrics strong {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 26px;
      color: var(--text);
      font-size: 17px;
      line-height: 1.2;
    }}
    .detail-metrics div:nth-child(2) strong {{ color: var(--accent-strong); }}
    .market-tabs {{
      display: flex;
      flex-wrap: wrap;
      gap: 0;
      margin: 0 0 14px;
      border-bottom: 1px solid var(--line);
    }}
    .market-tab {{
      min-height: 38px;
      border: 0;
      border-bottom: 3px solid transparent;
      border-radius: 0;
      background: transparent;
      color: #334155;
      cursor: pointer;
      padding: 0 20px;
      font: inherit;
      font-weight: 800;
    }}
    .market-tab span {{
      margin-left: 10px;
      color: var(--muted);
    }}
    .market-tab.active {{
      border-bottom-color: var(--accent);
      background: transparent;
      color: var(--accent-strong);
    }}
    .market-tab.active span {{ color: var(--accent-strong); }}
    .workbench-table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
    }}
    .workbench-signal-table {{
      width: 100%;
      min-width: 900px;
      border-collapse: collapse;
    }}
    .workbench-signal-table th {{
      position: sticky;
      top: 0;
      background: #f8fafc;
      color: #334155;
      font-size: 11px;
      text-transform: none;
    }}
    .workbench-signal-table td {{
      padding: 10px 12px;
      font-size: 13px;
      vertical-align: middle;
    }}
    .workbench-signal-table td span {{
      color: var(--muted);
    }}
    .workbench-signal-table .numeric {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .edge-cell strong {{ color: #0f766e; }}
    .edge-cell span {{ font-size: 12px; }}
    .workbench-signal-row {{
      cursor: pointer;
    }}
    .workbench-signal-row:hover td {{ background: #f7fcfb; }}
    .workbench-signal-row:focus {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .workbench-signal-row[aria-expanded="true"] td {{
      background: #f6fbfa;
      border-bottom-color: #cfeae6;
    }}
    .workbench-signal-row.is-stale td {{
      background: #fffaf0;
      color: #64748b;
    }}
    .workbench-signal-row.is-stale .edge-cell strong,
    .workbench-signal-row.is-stale .edge-cell span {{
      color: #64748b;
    }}
    .workbench-inline-detail-row td {{
      padding: 0;
      background: #f8fcfb;
      border-bottom: 1px solid #d9ebe8;
    }}
    .workbench-inline-detail {{
      padding: 12px 14px 14px;
      border-top: 1px solid #e2f0ed;
      color: #334155;
    }}
    .workbench-inline-detail-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px 12px;
      margin: 0;
    }}
    .workbench-inline-detail-grid div {{
      min-width: 0;
      padding: 9px 10px;
      border: 1px solid #e3ecef;
      border-radius: 6px;
      background: #ffffff;
    }}
    .workbench-inline-detail-grid dt {{
      margin: 0 0 4px;
      color: #64748b;
      font-size: 11px;
      font-weight: 800;
      line-height: 1.25;
    }}
    .workbench-inline-detail-grid dd {{
      margin: 0;
      color: #102033;
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}
    .workbench-inline-detail-grid div:last-child {{
      grid-column: span 4;
    }}
    .workbench-warning-strip {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 12px 0 0;
      padding: 10px 12px;
      border: 1px solid #fde6b8;
      border-radius: 8px;
      background: var(--warn-soft);
      color: #7c4a03;
      font-size: 13px;
      line-height: 1.45;
    }}
    .warning-mark {{
      display: inline-flex;
      width: 18px;
      height: 18px;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: #f59e0b;
      color: #ffffff;
      font-size: 12px;
      font-weight: 900;
      flex: 0 0 auto;
    }}
    .ledger-mode-bar {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin: 8px 0 12px;
      min-width: 0;
    }}
    .ledger-mode-bar h2 {{ margin-bottom: 4px; }}
    .mode-tabs {{
      display: inline-flex;
      gap: 4px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      flex: 0 0 auto;
    }}
    .mode-tab {{
      min-height: 32px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: #334155;
      cursor: pointer;
      padding: 0 12px;
      font: inherit;
      font-weight: 700;
    }}
    .mode-tab.active {{
      background: var(--accent-soft);
      color: #115e59;
    }}
    .ledger-table-wrap {{ overflow-x: auto; }}
    .ledger-table {{ width: 100%; min-width: 1160px; border-collapse: collapse; }}
    .match-ledger-table {{
      width: 100%;
      min-width: 980px;
      border-collapse: collapse;
    }}
    caption {{
      padding: 12px;
      text-align: left;
      font-weight: 700;
      color: var(--muted);
    }}
    caption span {{ display: inline; color: var(--text); margin-right: 10px; }}
    caption small {{ color: var(--muted); font-weight: 700; }}
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
    .match-date-row th {{
      background: #f8fafc;
      color: #334155;
      font-size: 13px;
      text-transform: none;
      position: static;
    }}
    .match-row {{
      cursor: pointer;
      background: #ffffff;
    }}
    .match-row:focus {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    .match-row[aria-expanded="true"] td {{
      background: #fbfdfd;
      border-bottom-color: #d6e7e4;
    }}
    .match-detail-row td {{
      background: #fbfdfd;
      padding: 0 12px 14px;
    }}
    .match-detail {{
      border: 1px solid #d6e7e4;
      border-radius: 8px;
      padding: 12px;
      background: #ffffff;
    }}
    .match-detail-heading {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .match-detail-heading strong {{ color: var(--text); }}
    .match-signal-table {{
      width: 100%;
      min-width: 860px;
      border-collapse: collapse;
    }}
    .match-signal-table th {{
      position: static;
      background: #f8fafc;
    }}
    .market-chip {{
      display: inline-flex;
      margin: 5px 5px 0 0;
      padding: 2px 7px;
      border: 1px solid #dbe5ed;
      border-radius: 999px;
      background: #f8fafc;
      color: #475569;
      font-size: 12px;
      font-weight: 700;
    }}
    .expand-button {{
      min-height: 30px;
      border: 1px solid #cbd5e1;
      border-radius: 7px;
      background: #f8fafc;
      color: #334155;
      cursor: pointer;
      font: inherit;
      font-weight: 800;
      padding: 0 10px;
      white-space: nowrap;
    }}
    .match-row[aria-expanded="true"] .expand-button {{
      border-color: var(--accent);
      background: var(--accent-soft);
      color: #115e59;
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
      --grade-bg: #f8fafc;
      --grade-border: #cbd5e1;
      --grade-text: #475569;
      --grade-shadow: rgba(15, 23, 42, 0.06);
      display: inline-grid;
      place-items: center;
      width: 28px;
      min-width: 28px;
      height: 22px;
      border: 1px solid var(--grade-border);
      border-radius: 7px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.66), rgba(255, 255, 255, 0)) var(--grade-bg);
      color: var(--grade-text);
      font-weight: 850;
      font-size: 11px;
      letter-spacing: 0;
      line-height: 1;
      text-align: center;
      vertical-align: middle;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72), 0 1px 2px var(--grade-shadow);
    }}
    .match-list-row .grade-pill,
    .detail-metrics .grade-pill,
    .workbench-signal-table .grade-pill,
    .signal-row .grade-pill {{
      display: inline-grid;
      place-items: center;
      margin-top: 0;
      color: var(--grade-text);
      font-size: 11px;
      line-height: 1;
      text-align: center;
    }}
    .detail-metrics .grade-pill {{
      width: 30px;
      min-width: 30px;
      height: 24px;
      border-radius: 8px;
      font-size: 12px;
    }}
    .grade-s {{
      --grade-bg: #fff7d6;
      --grade-border: #e4c35d;
      --grade-text: #8a5a00;
      --grade-shadow: rgba(138, 90, 0, 0.10);
    }}
    .grade-a {{
      --grade-bg: #f1f0ff;
      --grade-border: #b7b3ff;
      --grade-text: #4f46a5;
      --grade-shadow: rgba(79, 70, 165, 0.08);
    }}
    .grade-b {{
      --grade-bg: #ecfdf5;
      --grade-border: #86efac;
      --grade-text: #047857;
      --grade-shadow: rgba(4, 120, 87, 0.08);
    }}
    .grade-c,
    .grade-d,
    .grade-unknown {{
      --grade-bg: #f8fafc;
      --grade-border: #cbd5e1;
      --grade-text: #64748b;
      --grade-shadow: rgba(15, 23, 42, 0.06);
    }}
    .freshness-badge {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-height: 22px;
      padding: 0 8px;
      border-radius: 999px;
      border: 1px solid #d8e1ea;
      background: #f8fafc;
      color: #475569;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .freshness-badge::before {{
      content: "";
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: currentColor;
    }}
    .freshness-fresh {{
      border-color: #b7ebd2;
      background: #f0fdf7;
      color: #07804f;
    }}
    .freshness-pending {{
      border-color: #cbd5e1;
      background: #f8fafc;
      color: #475569;
    }}
    .freshness-stale {{
      border-color: #f5d491;
      background: #fff7e6;
      color: #a16207;
    }}
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
      .date-picker {{ flex-basis: 210px; }}
      .workbench-tools {{ width: 100%; flex-direction: column; align-items: stretch; }}
      .date-strip {{ grid-auto-columns: minmax(150px, 74vw); }}
      .ledger-workbench {{ grid-template-columns: minmax(0, 1fr); }}
      .match-list-panel {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      .match-list-scroll {{ max-height: 360px; }}
      .signal-detail-panel {{ padding: 14px; }}
      .detail-title-row h2 {{ font-size: 18px; }}
      .detail-metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .detail-metrics div:nth-child(2n) {{ border-right: 0; }}
      .detail-metrics div:nth-child(-n+2) {{ border-bottom: 1px solid #edf1f5; }}
      .ledger-mode-bar {{ align-items: stretch; flex-direction: column; }}
      .mode-tabs {{ width: 100%; }}
      .mode-tab {{ flex: 1; }}
      .match-detail-heading {{ display: block; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="brand-row">
          <div class="brand-title">2026 世界杯</div>
          {primary_nav}
        </div>
        <h1 class="screen-title">研究台账</h1>
      </div>
      <div class="header-actions">
        {view_tabs}
        <div class="status-line"><span>数据更新 {snapshot_at}</span><span class="refresh-dot" aria-hidden="true"></span><span>刷新成功</span></div>
        <p class="disclaimer">仅用于研究分析，不构成投注建议。</p>
      </div>
    </header>
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
    {summary}
  </main>
  <script>
    (function () {{
      var activeView = 'live';
      var activeDate = 'all';
      var activeGrade = 'all';
      var activeMode = 'match';
      var filterStateByView = {{
        live: {{ date: 'all', grade: 'all' }},
        history: {{ date: 'all', grade: 'all' }}
      }};
      var viewButtons = Array.prototype.slice.call(document.querySelectorAll('[data-view-filter]'));
      var viewPanels = Array.prototype.slice.call(document.querySelectorAll('[data-view-panel]'));
      var dateButtons = Array.prototype.slice.call(document.querySelectorAll('[data-date-filter]'));
      var datePicker = document.getElementById('date-picker');
      var gradeButtons = Array.prototype.slice.call(document.querySelectorAll('[data-filter]'));
      var modeButtons = Array.prototype.slice.call(document.querySelectorAll('[data-mode-filter]'));
      var modePanels = Array.prototype.slice.call(document.querySelectorAll('[data-mode-panel]'));
      var leagueControls = Array.prototype.slice.call(document.querySelectorAll('[data-league-filter-control]'));
      var searchControls = Array.prototype.slice.call(document.querySelectorAll('[data-search-control]'));
      var rows = Array.prototype.slice.call(document.querySelectorAll('.signal-row'));
      var dateRows = Array.prototype.slice.call(document.querySelectorAll('.date-row'));
      var matchRows = Array.prototype.slice.call(document.querySelectorAll('.match-row'));
      var matchDateRows = Array.prototype.slice.call(document.querySelectorAll('.match-date-row'));
      var matchSignalRows = Array.prototype.slice.call(document.querySelectorAll('.match-signal-row'));
      var workbenchRows = Array.prototype.slice.call(document.querySelectorAll('.match-list-row'));
      var workbenchDetails = Array.prototype.slice.call(document.querySelectorAll('.workbench-detail'));
      var workbenchMarketButtons = Array.prototype.slice.call(document.querySelectorAll('[data-workbench-market-filter]'));
      var workbenchSignalRows = Array.prototype.slice.call(document.querySelectorAll('.workbench-signal-row'));
      var finishedRows = Array.prototype.slice.call(document.querySelectorAll('.finished-row'));
      var finishedDateRows = Array.prototype.slice.call(document.querySelectorAll('.finished-day'));
      var noResults = document.querySelector('.no-results');
      var matchNoResults = document.querySelector('.match-no-results');
      var workbenchNoResults = Array.prototype.slice.call(document.querySelectorAll('.workbench-no-results'));
      var historyNoResults = document.querySelector('.history-no-results');
      var activeWorkbenchId = workbenchRows[0] ? (workbenchRows[0].dataset.workbenchMatchTarget || '') : '';

      function setActiveButton(buttons, activeButton) {{
        var activeValue = activeButton.dataset.dateFilter || activeButton.dataset.filter || '';
        buttons.forEach(function (button) {{
          var value = button.dataset.dateFilter || button.dataset.filter || '';
          var active = value === activeValue;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
      }}

      function currentFilterState() {{
        if (!filterStateByView[activeView]) {{
          filterStateByView[activeView] = {{ date: 'all', grade: 'all' }};
        }}
        return filterStateByView[activeView];
      }}

      function setButtonsByValue(buttons, datasetKey, activeValue) {{
        buttons.forEach(function (button) {{
          var value = button.dataset[datasetKey] || '';
          var active = value === activeValue;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
      }}

      function syncFilterButtons() {{
        setButtonsByValue(dateButtons, 'dateFilter', activeDate);
        setButtonsByValue(gradeButtons, 'filter', activeGrade);
      }}

      function setView(view) {{
        activeView = view;
        var state = currentFilterState();
        activeDate = state.date || 'all';
        activeGrade = state.grade || 'all';
        syncFilterButtons();
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

      function setMode(mode) {{
        activeMode = mode;
        modeButtons.forEach(function (button) {{
          var active = (button.dataset.modeFilter || 'match') === activeMode;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
        modePanels.forEach(function (panel) {{
          panel.hidden = (panel.dataset.modePanel || 'match') !== activeMode;
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

      function setMatchDetail(row, expanded) {{
        var targetId = row.dataset.matchDetailTarget;
        var detail = targetId ? document.getElementById(targetId) : null;
        row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        if (detail) {{
          detail.hidden = !expanded || row.hidden;
        }}
      }}

      function toggleMatchDetail(row) {{
        var expanded = row.getAttribute('aria-expanded') === 'true';
        setMatchDetail(row, !expanded);
      }}

      function setWorkbenchSignalDetail(row, expanded) {{
        var targetId = row.dataset.workbenchSignalDetail;
        var detail = targetId ? document.getElementById(targetId) : null;
        row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        if (detail) {{
          detail.hidden = !expanded || row.hidden;
        }}
      }}

      function toggleWorkbenchSignalDetail(row) {{
        var expanded = row.getAttribute('aria-expanded') === 'true';
        setWorkbenchSignalDetail(row, !expanded);
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

      function getActiveViewPanel() {{
        return document.querySelector('[data-view-panel="' + activeView + '"]');
      }}

      function getCurrentControl(selector, fallbackId) {{
        var panel = getActiveViewPanel();
        var scoped = panel ? panel.querySelector(selector) : null;
        return scoped || document.getElementById(fallbackId);
      }}

      function getSelectedLeague() {{
        var control = getCurrentControl('[data-league-filter-control]', 'league-filter');
        return control ? (control.value || 'all') : 'all';
      }}

      function getSearchTerm() {{
        var control = getCurrentControl('[data-search-control]', 'ledger-search');
        return control ? control.value.trim().toLowerCase() : '';
      }}

      function workbenchView(element) {{
        var panel = element ? element.closest('[data-view-panel]') : null;
        return panel ? (panel.dataset.viewPanel || 'live') : 'live';
      }}

      function getBeijingTodayIso() {{
        return new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
      }}

      function addDaysIso(dateIso, days) {{
        var parts = dateIso.split('-').map(function (part) {{ return parseInt(part, 10); }});
        var date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + days));
        return date.toISOString().slice(0, 10);
      }}

      function dateMatches(row) {{
        if (activeDate === 'all') return true;
        var rowDate = row.dataset.dateIso || '';
        if (!rowDate) return false;
        var today = getBeijingTodayIso();
        if (activeDate === 'today') return rowDate === today;
        if (activeDate === 'tomorrow') return rowDate === addDaysIso(today, 1);
        if (activeDate === 'next3') return rowDate >= today && rowDate <= addDaysIso(today, 2);
        if (activeDate === 'next7') return rowDate >= today && rowDate <= addDaysIso(today, 6);
        if (activeDate === 'custom') {{
          var selected = datePicker ? datePicker.value : '';
          return !!selected && rowDate === selected;
        }}
        if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(activeDate)) {{
          return rowDate === activeDate;
        }}
        return row.dataset.date === activeDate;
      }}

      function rowMatches(row, term) {{
        var selectedLeague = getSelectedLeague();
        var buckets = row.dataset.gradeBuckets || row.dataset.grade || '';
        var gradeMatch = activeGrade === 'all' || buckets.split(' ').indexOf(activeGrade) !== -1;
        var dateMatch = dateMatches(row);
        var leagueMatch = selectedLeague === 'all' || row.dataset.league === selectedLeague;
        var searchText = row.dataset.search || '';
        var textMatch = !term || searchText.indexOf(term) !== -1;
        return gradeMatch && dateMatch && leagueMatch && textMatch;
      }}

      function setWorkbenchMarket(detail, market) {{
        var visible = 0;
        var buttons = Array.prototype.slice.call(detail.querySelectorAll('[data-workbench-market-filter]'));
        var signalRows = Array.prototype.slice.call(detail.querySelectorAll('.workbench-signal-row'));
        buttons.forEach(function (button) {{
          var active = (button.dataset.workbenchMarketFilter || 'all') === market;
          button.classList.toggle('active', active);
          button.setAttribute('aria-pressed', active ? 'true' : 'false');
        }});
        signalRows.forEach(function (row) {{
          var wasExpanded = row.getAttribute('aria-expanded') === 'true';
          var show = market === 'all' || row.dataset.workbenchMarket === market;
          row.hidden = !show;
          setWorkbenchSignalDetail(row, show && wasExpanded);
          if (show) {{
            visible += 1;
          }}
        }});
        var empty = detail.querySelector('.workbench-market-empty');
        if (empty) {{
          empty.hidden = visible !== 0;
        }}
      }}

      function setWorkbenchMatch(row) {{
        activeWorkbenchId = row ? (row.dataset.workbenchMatchTarget || '') : '';
        var targetView = row ? workbenchView(row) : activeView;
        workbenchRows.forEach(function (candidate) {{
          if (workbenchView(candidate) !== targetView) return;
          var active = !!activeWorkbenchId && candidate.dataset.workbenchMatchTarget === activeWorkbenchId && !candidate.hidden;
          candidate.classList.toggle('active', active);
          candidate.setAttribute('aria-expanded', active ? 'true' : 'false');
        }});
        workbenchDetails.forEach(function (detail) {{
          if (workbenchView(detail) !== targetView) return;
          var active = !!activeWorkbenchId && detail.dataset.workbenchDetail === activeWorkbenchId;
          detail.classList.toggle('active', active);
          detail.hidden = !active;
          if (active) {{
            setWorkbenchMarket(detail, 'all');
          }}
        }});
      }}

      function syncWorkbenchSelection() {{
        var visibleRows = workbenchRows.filter(function (row) {{
          return workbenchView(row) === activeView && !row.hidden;
        }});
        if (!visibleRows.length) {{
          activeWorkbenchId = '';
          workbenchDetails.forEach(function (detail) {{
            if (workbenchView(detail) !== activeView) return;
            detail.classList.remove('active');
            detail.hidden = true;
          }});
          return;
        }}
        var activeRow = visibleRows.filter(function (row) {{
          return row.dataset.workbenchMatchTarget === activeWorkbenchId;
        }})[0] || visibleRows[0];
        setWorkbenchMatch(activeRow);
      }}

      function applyFilters() {{
        var term = getSearchTerm();
        var workbenchVisible = 0;
        workbenchRows.forEach(function (row) {{
          var show = rowMatches(row, term);
          row.hidden = !show;
          if (workbenchView(row) === activeView && show) {{
            workbenchVisible += 1;
          }}
        }});
        workbenchNoResults.forEach(function (empty) {{
          if (workbenchView(empty) !== activeView) return;
          empty.hidden = workbenchVisible !== 0;
        }});
        syncWorkbenchSelection();

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

        matchSignalRows.forEach(function (row) {{
          row.hidden = !rowMatches(row, term);
        }});
        var matchesVisible = 0;
        matchRows.forEach(function (row) {{
          var targetId = row.dataset.matchDetailTarget;
          var detail = targetId ? document.getElementById(targetId) : null;
          var childSignals = detail
            ? Array.prototype.slice.call(detail.querySelectorAll('.match-signal-row'))
            : [];
          var hasVisibleSignal = childSignals.some(function (signalRow) {{
            return !signalRow.hidden;
          }});
          var show = rowMatches(row, term) && hasVisibleSignal;
          var wasExpanded = row.getAttribute('aria-expanded') === 'true';
          row.hidden = !show;
          setMatchDetail(row, show && wasExpanded);
          if (show) {{
            matchesVisible += 1;
          }}
        }});
        matchDateRows.forEach(function (dateRow) {{
          var date = dateRow.dataset.matchDateRow || '';
          var hasVisible = matchRows.some(function (row) {{
            return row.dataset.date === date && !row.hidden;
          }});
          dateRow.hidden = !hasVisible;
        }});
        if (matchNoResults) {{
          matchNoResults.hidden = matchesVisible !== 0;
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

      workbenchRows.forEach(function (row) {{
        row.addEventListener('click', function () {{
          setWorkbenchMatch(row);
        }});
        row.addEventListener('keydown', function (event) {{
          if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            setWorkbenchMatch(row);
          }}
        }});
      }});

      workbenchMarketButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          var detail = button.closest('.workbench-detail');
          if (!detail) return;
          setWorkbenchMarket(detail, button.dataset.workbenchMarketFilter || 'all');
        }});
      }});

      workbenchSignalRows.forEach(function (row) {{
        row.addEventListener('click', function () {{
          toggleWorkbenchSignalDetail(row);
        }});
        row.addEventListener('keydown', function (event) {{
          if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            toggleWorkbenchSignalDetail(row);
          }}
        }});
      }});

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

      matchRows.forEach(function (row) {{
        row.addEventListener('click', function (event) {{
          if (event.target.closest('button')) {{
            event.preventDefault();
          }}
          toggleMatchDetail(row);
        }});
        row.addEventListener('keydown', function (event) {{
          if (event.key === 'Enter' || event.key === ' ') {{
            event.preventDefault();
            toggleMatchDetail(row);
          }}
        }});
      }});

      viewButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          setView(button.dataset.viewFilter || 'live');
        }});
      }});

      modeButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          setMode(button.dataset.modeFilter || 'match');
        }});
      }});

      dateButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeDate = button.dataset.dateFilter || 'all';
          currentFilterState().date = activeDate;
          syncFilterButtons();
          applyFilters();
        }});
      }});
      if (datePicker) {{
        datePicker.addEventListener('change', function () {{
          activeDate = 'custom';
          currentFilterState().date = activeDate;
          syncFilterButtons();
          applyFilters();
        }});
      }}

      gradeButtons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeGrade = button.dataset.filter || 'all';
          currentFilterState().grade = activeGrade;
          syncFilterButtons();
          applyFilters();
        }});
      }});
      leagueControls.forEach(function (control) {{
        control.addEventListener('input', applyFilters);
        control.addEventListener('change', applyFilters);
      }});
      searchControls.forEach(function (control) {{
        control.addEventListener('input', applyFilters);
      }});
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
      setMode('match');
    }}());
  </script>
</body>
</html>
""".format(
        snapshot_at=_text(snapshot_at),
    primary_nav=_render_primary_nav(),
    view_tabs=_render_view_tabs(),
        summary=_render_summary(snapshot),
        controls=_render_controls(signal_rows),
        table=_render_live_ledger(signal_rows, competitions=competitions),
        finished_section=_render_finished_section(snapshot, competitions=competitions),
        right_rail=_render_right_rail(snapshot),
    )
