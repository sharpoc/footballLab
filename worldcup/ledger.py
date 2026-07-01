from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from worldcup.engine.handicap import ev_handicap
from worldcup.query import summarize_finished_block

EM_DASH = "\u2014"
BEIJING_TZ = timezone(timedelta(hours=8))
WEEKDAY_LABELS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
STRONG_GRADES = {"S", "A"}
WATCH_GRADES = {"B"}
WEAK_GRADES = {"C", "D"}
DEFAULT_CHANGE_CFG = {"ev_change": 0.03, "odds_move": 0.05}
PROB_CHANGE_THRESHOLD = 0.01
EDGE_CHANGE_THRESHOLD = 0.01
DEFAULT_COMPETITION_ID = "fifa_world_cup_2026"
DEFAULT_COMPETITION_LABEL = "2026 世界杯"
TREND_MARKET_KEYS = {
    "1X2_90min": "1x2",
    "OverUnder_90min": "ou_2_5",
    "AsianHandicap_90min": "ah_main",
}
MATCH_DECISION_LABELS = {
    "STRONG_VALUE": "强价值",
    "VALUE_CANDIDATE": "候选价值",
    "HIGH_CONFIDENCE_LEAN": "高胜率倾向",
    "LOW_CONFIDENCE_LEAN": "低置信倾向",
    "NO_CLEAN_MARKET": "无干净盘口",
}
TEAM_LABELS_ZH = {
    "Algeria": "阿尔及利亚",
    "Argentina": "阿根廷",
    "Australia": "澳大利亚",
    "Austria": "奥地利",
    "Belgium": "比利时",
    "Bosnia & Herzegovina": "波黑",
    "Bosnia and Herzegovina": "波黑",
    "Brazil": "巴西",
    "Canada": "加拿大",
    "Cape Verde": "佛得角",
    "Colombia": "哥伦比亚",
    "Costa Rica": "哥斯达黎加",
    "Croatia": "克罗地亚",
    "Curaçao": "库拉索",
    "Czech Republic": "捷克",
    "DR Congo": "刚果民主共和国",
    "Denmark": "丹麦",
    "Ecuador": "厄瓜多尔",
    "Egypt": "埃及",
    "England": "英格兰",
    "France": "法国",
    "Germany": "德国",
    "Ghana": "加纳",
    "Greece": "希腊",
    "Haiti": "海地",
    "Honduras": "洪都拉斯",
    "Iran": "伊朗",
    "Iraq": "伊拉克",
    "Italy": "意大利",
    "Ivory Coast": "科特迪瓦",
    "Japan": "日本",
    "Jordan": "约旦",
    "Mexico": "墨西哥",
    "Morocco": "摩洛哥",
    "Netherlands": "荷兰",
    "New Zealand": "新西兰",
    "Nigeria": "尼日利亚",
    "Norway": "挪威",
    "Panama": "巴拿马",
    "Paraguay": "巴拉圭",
    "Poland": "波兰",
    "Portugal": "葡萄牙",
    "Qatar": "卡塔尔",
    "Saudi Arabia": "沙特阿拉伯",
    "Scotland": "苏格兰",
    "Senegal": "塞内加尔",
    "Serbia": "塞尔维亚",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Spain": "西班牙",
    "Sweden": "瑞典",
    "Switzerland": "瑞士",
    "Tunisia": "突尼斯",
    "Turkey": "土耳其",
    "Ukraine": "乌克兰",
    "Uruguay": "乌拉圭",
    "United States": "美国",
    "USA": "美国",
    "Uzbekistan": "乌兹别克斯坦",
    "Wales": "威尔士",
}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_beijing_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TZ)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe_stable(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _quality_values(snapshot: dict[str, Any], key: str) -> list[Any]:
    data_quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    if key in data_quality:
        return _dedupe_stable(_as_list(data_quality.get(key)))
    return _dedupe_stable(_as_list(run.get(key)))


def _selection_label(selection: str | None) -> str:
    labels = {
        "home": "主队",
        "away": "客队",
        "draw": "平局",
        "over": "大球",
        "under": "小球",
    }
    if selection is None:
        return EM_DASH
    normalized = str(selection).lower()
    if normalized.startswith("home_"):
        normalized = "home"
    elif normalized.startswith("away_"):
        normalized = "away"
    return labels.get(normalized, str(selection))


def _line_label(line: Any, signed_positive: bool = False) -> str:
    if line is None:
        return ""
    try:
        value = float(line)
        sign = "+" if signed_positive and value > 0 else ""
        return f" {sign}{value:g}"
    except (TypeError, ValueError):
        return f" {line}"


def _signal_line(signal: dict[str, Any]) -> Any:
    for key in ("line", "total", "handicap"):
        if key in signal:
            return signal.get(key)
    return None


def _selection_key(selection: Any) -> str | None:
    if selection is None:
        return None
    normalized = str(selection).lower()
    if normalized.startswith("home_"):
        return "home"
    if normalized.startswith("away_"):
        return "away"
    return normalized


def _signal_model_prob(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    direct = signal.get("model_prob")
    if direct is not None:
        return direct
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    model = match.get("model") or {}
    if market_type == "1X2_90min":
        return (model.get("combined_1x2") or {}).get(selection)
    if market_type == "OverUnder_90min":
        return (model.get("ou_2_5") or {}).get(selection)
    return None


def _signal_market_prob(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    direct = signal.get("market_prob")
    if direct is not None:
        return direct
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    market = match.get("market") or {}
    if market_type == "1X2_90min":
        market_1x2 = market.get("1x2") or {}
        return (market_1x2.get("market_probs") or market_1x2.get("probs") or {}).get(selection)
    if market_type == "OverUnder_90min":
        return ((market.get("ou_2_5") or {}).get("market_probs") or {}).get(selection)
    return None


def format_percent(value: float | None, signed: bool = True) -> str:
    if value is None:
        return EM_DASH
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def format_probability(value: float | None) -> str:
    return format_percent(value, signed=False)


def format_market_label(market_type: str | None, selection: str | None, line: float | None) -> str:
    selection_label = _selection_label(selection)
    if market_type == "1X2_90min":
        return f"胜平负 - {selection_label}"
    if market_type == "OverUnder_90min":
        return f"大小球{_line_label(line)} - {selection_label}"
    if market_type == "AsianHandicap_90min":
        return f"亚洲让球{_line_label(line, signed_positive=True)} - {selection_label}"
    return f"{market_type or '盘口'} - {selection_label}"


def format_match_decision_market_label(decision: dict[str, Any] | None) -> str:
    decision = decision or {}
    market = decision.get("market")
    selection = decision.get("selection")
    line = decision.get("line")
    selection_label = _selection_label(selection)
    if market == "1X2":
        return f"胜平负 - {selection_label}"
    if market == "DNB":
        return f"平手盘 - {selection_label}"
    if market == "AH":
        return f"亚洲让球{_line_label(line, signed_positive=True)} - {selection_label}"
    if market == "OU":
        return f"大小球{_line_label(line)} - {selection_label}"
    if market:
        return f"{market} - {selection_label}"
    return EM_DASH


def format_match_decision_summary(decision: dict[str, Any] | None) -> dict[str, str | None]:
    decision = decision or {}
    raw_label = str(decision.get("label") or "")
    label_text = MATCH_DECISION_LABELS.get(raw_label, raw_label or EM_DASH)
    market_label = format_match_decision_market_label(decision)
    if raw_label == "NO_CLEAN_MARKET" or market_label == EM_DASH:
        summary = label_text
    else:
        summary = (
            f"{label_text} · {market_label} · "
            f"安全胜率 {format_probability(_as_float(decision.get('p_hit_safe')))} · "
            f"不亏概率 {format_probability(_as_float(decision.get('p_no_loss_safe')))}"
        )
    return {
        "match_decision_label": raw_label or None,
        "match_decision_label_text": label_text,
        "match_decision_market_label": market_label,
        "match_decision_p_hit_safe": format_probability(_as_float(decision.get("p_hit_safe"))),
        "match_decision_p_no_loss_safe": format_probability(_as_float(decision.get("p_no_loss_safe"))),
        "match_decision_summary": summary,
    }


def format_team_label(team: str | None) -> str:
    if not team:
        return ""
    return TEAM_LABELS_ZH.get(str(team), str(team))


def format_matchup_label(home_team: str | None, away_team: str | None) -> str:
    return f"{format_team_label(home_team)} 对 {format_team_label(away_team)}"


def _format_stage(stage: Any) -> str:
    value = str(stage or "")
    if value.startswith("Matchday "):
        round_number = value.removeprefix("Matchday ").strip()
        return f"小组赛第 {round_number} 轮"
    labels = {
        "Group Stage": "小组赛",
        "Round of 32": "32 强赛",
        "Round of 16": "16 强赛",
        "Quarter-finals": "四分之一决赛",
        "Quarterfinals": "四分之一决赛",
        "Semi-finals": "半决赛",
        "Semifinals": "半决赛",
        "Third place": "三四名决赛",
        "Third Place": "三四名决赛",
        "Final": "决赛",
    }
    return labels.get(value, value)


def _format_group(group: Any) -> str:
    value = str(group or "")
    if value.startswith("Group "):
        return f"{value.removeprefix('Group ').strip()} 组"
    return value


def _format_stage_group(stage: Any, group: Any) -> str:
    return " | ".join(part for part in [_format_stage(stage), _format_group(group)] if part)


def _competition_block(match: dict[str, Any]) -> dict[str, Any]:
    competition = match.get("competition")
    return competition if isinstance(competition, dict) else {}


def competition_id_for_match(match: dict[str, Any]) -> str:
    competition_id = str(_competition_block(match).get("id") or "").strip()
    return competition_id or DEFAULT_COMPETITION_ID


def competition_label_for_match(match: dict[str, Any]) -> str:
    competition = _competition_block(match)
    label = str(competition.get("name") or competition.get("label") or "").strip()
    return label or DEFAULT_COMPETITION_LABEL


def competition_options(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_match(match: dict[str, Any]) -> None:
        competition_id = competition_id_for_match(match)
        if competition_id in seen:
            return
        seen.add(competition_id)
        options.append(
            {
                "id": competition_id,
                "label": competition_label_for_match(match),
            }
        )

    for match in snapshot.get("matches") or []:
        add_match(match)
    for match in ((snapshot.get("finished") or {}).get("matches")) or []:
        add_match(match)
    if not options:
        options.append({"id": DEFAULT_COMPETITION_ID, "label": DEFAULT_COMPETITION_LABEL})
    return options


def derive_quality_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if _quality_values(snapshot, "source_errors"):
        reasons.append("source_errors")
        return {"label": "需关注", "tone": "error", "reasons": reasons}

    for key in (
        "stale_sources",
        "missing_odds",
        "missing_elo",
        "time_mismatches",
        "enrichment_errors",
    ):
        if _quality_values(snapshot, key):
            reasons.append(key)
    if reasons:
        return {"label": "预警", "tone": "warn", "reasons": reasons}
    return {"label": "正常", "tone": "ok", "reasons": []}


def build_signal_explanation(signal: dict[str, Any], stale: bool) -> str:
    if stale:
        return "由于一个或多个输入过期或缺失，信号已被降级。"
    market_type = signal.get("market_type")
    if market_type == "1X2_90min":
        return "模型概率高于去水后的市场概率。"
    if market_type == "OverUnder_90min":
        return "模型总进球分布与市场大小球预期存在差异。"
    if market_type == "AsianHandicap_90min":
        return "当前让球盘口下的结算 EV 为正。"
    return "模型估计与市场估计差异足够大，值得复核。"


def _signal_risk_note(signal: dict[str, Any], stale: bool) -> str:
    notes: list[str] = []
    if stale:
        notes.append("存在过期或兜底输入，信号可信度已降低")
    reason_labels = {
        "stale_odds": "赔率数据过期，等级已压低",
        "few_books": "可用书商数量少，等级已压低",
        "longshot_uncertainty": "市场概率偏低，冷门尾部不确定性较高",
        "unconfirmed_backup": "使用本地缓存兜底，需复核",
        "line_changed_unknown": "盘口线变动未完全确认",
        "model_disagreement": "Elo 与 Poisson 模型分歧，等级已压低",
        "market_dispersion": "多家赔率报价分歧较大，等级已压低",
        "x12_draw_candidate_only": "世界杯 1X2 平局价值分歧暂列研究候选，不计入正式 S/A",
        "x12_long_odds_candidate_only": "世界杯 1X2 赔率高于正式价值分歧上限，暂列研究候选",
    }
    for reason in _as_list(signal.get("reasons")):
        label = reason_labels.get(str(reason))
        if label:
            notes.append(label)
    if not notes:
        return "当前数据新鲜，未触发额外降级原因。"
    return "；".join(_dedupe_stable(notes)) + "。"


def _candidate_reason_labels(reasons: Any) -> str:
    labels = {
        "official_grade_capped_by_ah_market_edge_missing": "正式等级仍受 AH market edge 缺口限制",
        "ah_validation_shadow_candidate_validated": "AH shadow 验证通过",
    }
    rendered = [labels.get(str(reason), str(reason)) for reason in _as_list(reasons)]
    return "；".join(_dedupe_stable(rendered)) if rendered else EM_DASH


def _build_signal_detail_items(
    *,
    explanation: str,
    market_label: str,
    model_prob: str,
    market_prob: str,
    edge: str,
    ev: str,
    grade: Any,
    status: Any,
    freshness: str,
    risk_note: str,
) -> list[dict[str, str]]:
    if model_prob == EM_DASH and market_prob == EM_DASH:
        model_market = "该盘口主要依据结算 EV，不强行展示模型/市场二元概率。"
    else:
        model_market = f"模型 {model_prob}，市场 {market_prob}，Edge {edge}"
    return [
        {"label": "核心判断", "value": explanation},
        {"label": "盘口方向", "value": market_label},
        {"label": "模型与市场", "value": model_market},
        {"label": "EV", "value": ev},
        {"label": "等级状态", "value": f"{grade or EM_DASH} / {status or EM_DASH} / {freshness}"},
        {"label": "风险提示", "value": risk_note},
    ]


def _format_kickoff_date(parsed_kickoff: datetime | None) -> str:
    if parsed_kickoff is None:
        return "日期暂不可用"
    weekday = WEEKDAY_LABELS[parsed_kickoff.weekday()]
    return f"{parsed_kickoff.year} 年 {parsed_kickoff.month} 月 {parsed_kickoff.day} 日 {weekday}"


def _format_datetime_full(value: datetime | None) -> str:
    if value is None:
        return EM_DASH
    weekday = WEEKDAY_LABELS[value.weekday()]
    return f"{value.year} 年 {value.month} 月 {value.day} 日 {weekday} {value:%H:%M}"


def _format_refresh_plan(plan: dict[str, Any] | None) -> dict[str, str]:
    plan = plan or {}
    label = str(plan.get("label") or "待调度")
    description = str(plan.get("description") or "等待下一轮调度确认")
    next_update_at = str(plan.get("next_update_at") or "")
    display_next = _to_beijing_time(_parse_datetime(next_update_at))
    if display_next is None:
        next_time = label
        next_full = label
    else:
        next_time = display_next.strftime("%H:%M")
        next_full = _format_datetime_full(display_next)
    prefix = " ".join(part for part in (label, description) if part)
    detail = f"{prefix}：{next_full}" if prefix else next_full
    return {
        "next_update_at": next_update_at,
        "next_update_time": next_time,
        "next_update_label": label,
        "next_update_description": description,
        "next_update_full": next_full,
        "next_update_detail": detail,
    }


def _format_decimal(value: float | None) -> str:
    if value is None:
        return EM_DASH
    return f"{value:.2f}"


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_identity(match: dict[str, Any]) -> str:
    for key in ("source_event_id", "source_match_no"):
        value = match.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    parts = [
        match.get("kickoff_at_utc", ""),
        match.get("home_canonical") or match.get("home_team", ""),
        match.get("away_canonical") or match.get("away_team", ""),
    ]
    return "|".join(str(part).strip().lower() for part in parts)


def _finished_identity_set(snapshot: dict[str, Any]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for record in ((snapshot.get("finished") or {}).get("matches")) or []:
        kickoff = str(record.get("kickoff_at_utc") or "")
        out.add(
            (
                kickoff[:10],
                str(record.get("home_canonical") or ""),
                str(record.get("away_canonical") or ""),
            )
        )
    return out


def _match_finished_identity(match: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(match.get("kickoff_at_utc") or "")[:10],
        str(match.get("home_canonical") or ""),
        str(match.get("away_canonical") or ""),
    )


def _line_key(line: Any) -> str:
    if line is None:
        return ""
    value = _as_float(line)
    if value is None:
        return str(line)
    return f"{value:g}"


def _signal_identity(signal: dict[str, Any]) -> str:
    market_type = signal.get("market_type") or ""
    selection = _selection_key(signal.get("selection")) or ""
    return "|".join([str(market_type), selection, _line_key(_signal_line(signal))])


def _signal_trend_points(match: dict[str, Any], signal: dict[str, Any]) -> list:
    trend = match.get("odds_trend") or {}
    market_key = TREND_MARKET_KEYS.get(str(signal.get("market_type") or ""))
    if not market_key:
        return []
    selection = _selection_key(signal.get("selection")) or str(signal.get("selection") or "")
    if market_key == "ah_main":
        selection = selection.split("_", 1)[0]
    return (trend.get(market_key) or {}).get(selection) or []


def _signal_market_odds(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    direct = _as_float(signal.get("odds"))
    if direct is not None:
        return direct
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    market = match.get("market") or {}
    if market_type == "1X2_90min":
        return _as_float(((market.get("1x2") or {}).get("odds") or {}).get(selection))
    if market_type == "OverUnder_90min":
        return _as_float(((market.get("ou_2_5") or {}).get("odds") or {}).get(selection))
    return None


def _finished_score(match: dict[str, Any]) -> tuple[int, int] | None:
    result = match.get("result") or {}
    if result.get("status") != "finished":
        return None
    try:
        return int(result["home_score"]), int(result["away_score"])
    except (KeyError, TypeError, ValueError):
        return None


def _score_detail(
    match: dict[str, Any],
    home_score: int,
    away_score: int,
    direction: str,
) -> str:
    home = format_team_label(match.get("home_team"))
    away = format_team_label(match.get("away_team"))
    return f"赛果：{home} {home_score}-{away_score} {away}；方向：{direction}"


def _prediction_result(match: dict[str, Any], signal: dict[str, Any]) -> dict[str, str] | None:
    score = _finished_score(match)
    if score is None:
        return None
    home_score, away_score = score
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    line = _as_float(_signal_line(signal))

    status: str | None = None
    direction: str | None = None
    if market_type == "1X2_90min":
        actual = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        direction_labels = {"home": "主胜", "draw": "平局", "away": "客胜"}
        direction = direction_labels.get(selection or "")
        if direction is None:
            return None
        status = "hit" if selection == actual else "miss"
    elif market_type == "OverUnder_90min":
        if line is None or selection not in {"over", "under"}:
            return None
        total_goals = home_score + away_score
        if total_goals == line:
            status = "push"
        else:
            actual = "over" if total_goals > line else "under"
            status = "hit" if selection == actual else "miss"
        direction = f"{'大' if selection == 'over' else '小'} {_line_key(line)}"
    elif market_type == "AsianHandicap_90min":
        if line is None or selection not in {"home", "away"}:
            return None
        goal_diff = home_score - away_score if selection == "home" else away_score - home_score
        realized = ev_handicap({goal_diff: 1.0}, line, 2.0)
        if realized > 0:
            status = "hit"
        elif realized < 0:
            status = "miss"
        else:
            status = "push"
        direction = f"{_selection_label(selection)}{_line_label(line, signed_positive=True)}"

    if status is None or direction is None:
        return None
    labels = {"hit": "命中", "miss": "未中", "push": "走水"}
    return {
        "status": status,
        "label": labels[status],
        "detail": _score_detail(match, home_score, away_score, direction),
    }


def _snapshot_signal_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for match in snapshot.get("matches") or []:
        match_id = _match_identity(match)
        selections: dict[str, dict[str, Any]] = {}
        for signal in match.get("signals") or []:
            market_type = signal.get("market_type")
            selection = signal.get("selection")
            line = _signal_line(signal)
            selections[_signal_identity(signal)] = {
                "market_label": format_market_label(market_type, selection, line),
                "grade": signal.get("grade"),
                "ev": _as_float(signal.get("ev")),
                "edge": _as_float(signal.get("edge")),
                "model_prob": _as_float(_signal_model_prob(match, signal)),
                "market_prob": _as_float(_signal_market_prob(match, signal)),
                "odds": _signal_market_odds(match, signal),
            }
        indexed[match_id] = {
            "label": format_matchup_label(match.get("home_team"), match.get("away_team")),
            "kickoff_at_utc": match.get("kickoff_at_utc", ""),
            "selections": selections,
        }
    return indexed


def _format_change_pair(label: str, before: float | None, after: float | None, percent: bool) -> str:
    if percent:
        formatter = format_probability if "概率" in label else format_percent
        return f"{label} {formatter(before)} → {formatter(after)}"
    return f"{label} {_format_decimal(before)} → {_format_decimal(after)}"


def _grade_tone(before: Any, after: Any) -> str:
    before_score = GRADE_ORDER.get(str(before or ""), 0)
    after_score = GRADE_ORDER.get(str(after or ""), 0)
    if after_score > before_score:
        return "strong"
    if after_score < before_score:
        return "warn"
    return "neutral"


def _change_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg is not None:
        return {**DEFAULT_CHANGE_CFG, **cfg}
    try:
        from worldcup.config import load_config

        return {**DEFAULT_CHANGE_CFG, **((load_config().get("differ") or {}))}
    except Exception:
        return dict(DEFAULT_CHANGE_CFG)


def _selection_change_parts(
    before: dict[str, Any],
    after: dict[str, Any],
    change_cfg: dict[str, Any],
) -> tuple[list[str], str]:
    ev_threshold = _as_float(change_cfg.get("ev_change")) or DEFAULT_CHANGE_CFG["ev_change"]
    odds_threshold = _as_float(change_cfg.get("odds_move")) or DEFAULT_CHANGE_CFG["odds_move"]
    changes: list[str] = []
    tone = "neutral"

    if before.get("grade") != after.get("grade"):
        changes.append(f"等级 {before.get('grade') or EM_DASH} → {after.get('grade') or EM_DASH}")
        tone = _grade_tone(before.get("grade"), after.get("grade"))
    before_ev, after_ev = before.get("ev"), after.get("ev")
    if before_ev is not None and after_ev is not None and abs(after_ev - before_ev) >= ev_threshold:
        changes.append(_format_change_pair("EV", before_ev, after_ev, percent=True))
    before_edge, after_edge = before.get("edge"), after.get("edge")
    if (
        before_edge is not None
        and after_edge is not None
        and abs(after_edge - before_edge) >= EDGE_CHANGE_THRESHOLD
    ):
        changes.append(_format_change_pair("Edge", before_edge, after_edge, percent=True))
    before_model, after_model = before.get("model_prob"), after.get("model_prob")
    if (
        before_model is not None
        and after_model is not None
        and abs(after_model - before_model) >= PROB_CHANGE_THRESHOLD
    ):
        changes.append(_format_change_pair("模型概率", before_model, after_model, percent=True))
    before_market, after_market = before.get("market_prob"), after.get("market_prob")
    if (
        before_market is not None
        and after_market is not None
        and abs(after_market - before_market) >= PROB_CHANGE_THRESHOLD
    ):
        changes.append(_format_change_pair("市场概率", before_market, after_market, percent=True))
    before_odds, after_odds = before.get("odds"), after.get("odds")
    if (
        before_odds is not None
        and after_odds is not None
        and before_odds > 0
        and abs(after_odds - before_odds) / before_odds >= odds_threshold
    ):
        changes.append(_format_change_pair("赔率", before_odds, after_odds, percent=False))

    return changes, tone


def build_snapshot_change_items(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    cfg: dict[str, Any] | None = None,
    limit: int = 12,
) -> list[dict[str, str]]:
    if not previous_snapshot:
        return [
            {
                "tone": "neutral",
                "title": "暂无上一轮数据",
                "detail": "当前只有一轮快照，下一次更新后会展示等级、EV、概率和赔率变化。",
            }
        ]

    change_cfg = _change_cfg(cfg)
    prev_index = _snapshot_signal_index(previous_snapshot)
    curr_index = _snapshot_signal_index(current_snapshot)
    items: list[dict[str, str]] = []

    for match_id in sorted(set(curr_index) - set(prev_index)):
        match = curr_index[match_id]
        if match["selections"]:
            items.append(
                {
                    "tone": "neutral",
                    "title": match["label"],
                    "detail": "本轮新增可比较信号。",
                }
            )
    for match_id in sorted(set(prev_index) - set(curr_index)):
        match = prev_index[match_id]
        if match["selections"]:
            items.append(
                {
                    "tone": "neutral",
                    "title": match["label"],
                    "detail": "本轮不再出现在可比较信号中。",
                }
            )

    for match_id in sorted(set(prev_index) & set(curr_index)):
        previous_match = prev_index[match_id]
        current_match = curr_index[match_id]
        previous_selections = previous_match["selections"]
        current_selections = current_match["selections"]
        for key in sorted(set(previous_selections) & set(current_selections)):
            before = previous_selections[key]
            after = current_selections[key]
            changes, tone = _selection_change_parts(before, after, change_cfg)

            if changes:
                items.append(
                    {
                        "tone": tone,
                        "title": f"{current_match['label']} | {after['market_label']}",
                        "detail": "；".join(changes),
                    }
                )

    if not items:
        return [
            {
                "tone": "neutral",
                "title": "最近一轮无显著变化",
                "detail": "等级、EV、概率和赔率都没有超过展示阈值。",
            }
        ]
    return items[: max(1, limit)]


def _recent_signal_change(
    previous_index: dict[str, dict[str, Any]],
    match_id: str,
    signal_key: str,
    after: dict[str, Any],
    change_cfg: dict[str, Any],
) -> dict[str, str] | None:
    previous_match = previous_index.get(match_id)
    if not previous_match or signal_key not in previous_match["selections"]:
        return {"tone": "neutral", "detail": "本轮新增可比较信号。"}

    before = previous_match["selections"][signal_key]
    changes, tone = _selection_change_parts(before, after, change_cfg)
    if not changes:
        return None
    return {"tone": tone, "detail": "；".join(changes)}


def project_signal_rows(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    stale = bool(_quality_values(snapshot, "stale_sources"))
    run = snapshot.get("run") or {}
    snapshot_updated_raw = run.get("observed_at") or snapshot.get("snapshot_at")
    previous_index = _snapshot_signal_index(previous_snapshot) if previous_snapshot else {}
    current_index = _snapshot_signal_index(snapshot) if previous_snapshot else {}
    change_cfg = _change_cfg(cfg) if previous_snapshot else {}
    finished_ids = _finished_identity_set(snapshot)
    rows: list[dict[str, Any]] = []
    for match in snapshot.get("matches", []):
        if _match_finished_identity(match) in finished_ids:
            continue
        match_id = _match_identity(match)
        kickoff_at_utc = match.get("kickoff_at_utc", "")
        parsed_kickoff = _parse_datetime(kickoff_at_utc)
        display_kickoff = _to_beijing_time(parsed_kickoff)
        updated_raw = match.get("odds_updated_at") or snapshot_updated_raw
        display_updated = _to_beijing_time(_parse_datetime(updated_raw))
        updated_label = "赔率源更新" if match.get("odds_updated_at") else "分析更新"
        updated_full = _format_datetime_full(display_updated)
        refresh_plan = _format_refresh_plan(match.get("refresh_plan"))
        home_team = match.get("home_team", "")
        away_team = match.get("away_team", "")
        competition_id = competition_id_for_match(match)
        competition_label = competition_label_for_match(match)
        match_decision_view = format_match_decision_summary(match.get("match_decision"))
        for signal in match.get("signals") or []:
            signal_key = _signal_identity(signal)
            market_type = signal.get("market_type")
            selection = signal.get("selection")
            line = _signal_line(signal)
            market_label = format_market_label(market_type, selection, line)
            model_prob = format_probability(_signal_model_prob(match, signal))
            market_prob = format_probability(_signal_market_prob(match, signal))
            edge = format_percent(signal.get("edge"))
            ev = format_percent(signal.get("ev"))
            grade = signal.get("grade", "")
            status = signal.get("status", "")
            freshness = "过期" if stale else "新鲜"
            explanation = build_signal_explanation(signal, stale)
            prediction_result = _prediction_result(match, signal)
            after = ((current_index.get(match_id) or {}).get("selections") or {}).get(signal_key)
            recent_change = (
                _recent_signal_change(previous_index, match_id, signal_key, after, change_cfg)
                if after
                else None
            )
            row = {
                "matchup": format_matchup_label(home_team, away_team),
                "source_matchup": f"{home_team} vs {away_team}",
                "home_team": format_team_label(home_team),
                "away_team": format_team_label(away_team),
                "source_home_team": home_team,
                "source_away_team": away_team,
                "competition_id": competition_id,
                "competition_label": competition_label,
                "kickoff_at_utc": kickoff_at_utc,
                "kickoff_date": _format_kickoff_date(display_kickoff),
                "kickoff_time": display_kickoff.strftime("%H:%M") if display_kickoff else EM_DASH,
                "updated_time": display_updated.strftime("%H:%M") if display_updated else EM_DASH,
                "updated_label": updated_label,
                "updated_full": updated_full,
                **refresh_plan,
                "stage": _format_stage(match.get("stage", "")),
                "group": _format_group(match.get("group", "")),
                "stage_group": _format_stage_group(match.get("stage", ""), match.get("group", "")),
                "market_type": market_type,
                "market_label": market_label,
                "model_prob": model_prob,
                "market_prob": market_prob,
                "edge": edge,
                "ev": ev,
                "grade": grade,
                "candidate_grade": signal.get("candidate_grade"),
                "candidate_reasons": list(signal.get("candidate_reasons") or []),
                **match_decision_view,
                "status": status,
                "freshness": freshness,
                "stale": stale,
                "explanation": explanation,
                "prediction_result": prediction_result,
                "recent_change": recent_change,
                "odds_trend_points": _signal_trend_points(match, signal),
                "detail_items": _build_signal_detail_items(
                    explanation=explanation,
                    market_label=market_label,
                    model_prob=model_prob,
                    market_prob=market_prob,
                    edge=edge,
                    ev=ev,
                    grade=grade,
                    status=status,
                    freshness=freshness,
                    risk_note=_signal_risk_note(signal, stale),
                ),
            }
            row["detail_items"].append(
                {"label": "更新时间", "value": f"{updated_label}：{updated_full}"}
            )
            row["detail_items"].append(
                {"label": "下次更新", "value": row["next_update_detail"]}
            )
            if row.get("match_decision_summary"):
                row["detail_items"].append(
                    {"label": "本场首选方向", "value": row["match_decision_summary"]}
                )
            if row.get("candidate_grade"):
                row["detail_items"].append(
                    {
                        "label": "候选等级",
                        "value": f"{row['candidate_grade']}（研究候选，不计入正式 S/A）",
                    }
                )
                row["detail_items"].append(
                    {
                        "label": "候选依据",
                        "value": _candidate_reason_labels(row.get("candidate_reasons")),
                    }
                )
            if prediction_result:
                row["detail_items"].append(
                    {
                        "label": "赛后验证",
                        "value": f"{prediction_result['label']}；{prediction_result['detail']}",
                    }
                )
            if recent_change:
                row["detail_items"].append(
                    {"label": "本轮变化", "value": recent_change["detail"]}
                )
            rows.append(row)

    rows.sort(
        key=lambda row: (
            row["kickoff_at_utc"],
            -GRADE_ORDER.get(row.get("grade", ""), 0),
            row["matchup"],
            row["market_label"],
        )
    )
    return rows


def build_summary_metrics(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = project_signal_rows(snapshot)
    grade_counts = Counter(row.get("grade", "") for row in rows)
    quality = derive_quality_status(snapshot)
    finished_ids = _finished_identity_set(snapshot)
    upcoming = [
        match
        for match in (snapshot.get("matches") or [])
        if _match_finished_identity(match) not in finished_ids
    ]
    metrics = {
        "upcoming_matches": {"label": "即将比赛", "value": len(upcoming)},
        "strong_signals": {
            "label": "价值分歧 S/A",
            "value": sum(grade_counts[grade] for grade in STRONG_GRADES),
        },
        "watch_signals": {
            "label": "观察分歧 B",
            "value": sum(grade_counts[grade] for grade in WATCH_GRADES),
        },
        "weak_signals": {
            "label": "低价值分歧 C/D",
            "value": sum(grade_counts[grade] for grade in WEAK_GRADES),
        },
        "grade_counts": {
            "label": "等级统计",
            "value": {grade: grade_counts[grade] for grade in sorted(grade_counts)},
        },
        "stale_sources": {"label": "过期来源", "value": len(_quality_values(snapshot, "stale_sources"))},
        "overall_quality": {
            "label": "整体质量",
            "value": quality["label"],
            "tone": quality["tone"],
            "reasons": quality["reasons"],
        },
    }
    finished = snapshot.get("finished") or {}
    tally = finished.get("tally") or {}

    def _record_value(grade: str) -> str:
        entry = tally.get(grade) or {}
        hit = entry.get("hit", 0)
        miss = entry.get("miss", 0)
        push = entry.get("push", 0)
        decided = hit + miss
        rate = f"{round(hit * 100 / decided)}%" if decided else EM_DASH
        return f"命中 {hit} · 未中 {miss} · 走水 {push} · 命中率 {rate}"

    if tally:
        metrics["record_s"] = {"label": "S 级战绩", "value": _record_value("S")}
        metrics["record_a"] = {"label": "A 级战绩", "value": _record_value("A")}
    return metrics


def build_finished_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    finished = snapshot.get("finished") or {}
    records = finished.get("matches") or []
    days: dict[str, dict[str, Any]] = {}
    for record in records:
        kickoff = _to_beijing_time(_parse_datetime(record.get("kickoff_at_utc")))
        date_key = kickoff.date().isoformat() if kickoff else ""
        date_label = _format_kickoff_date(kickoff)
        day = days.setdefault(
            date_key,
            {"date_label": date_label, "date_iso": date_key, "matches": [], "_sort_key": date_key},
        )
        result = record.get("result") or {}
        detail_signals = []
        sa_badges = []
        for signal in record.get("closing_signals") or []:
            grade = str(signal.get("grade") or "")
            prediction = signal.get("prediction") or {}
            label = prediction.get("label") or ""
            item = {
                "grade": grade,
                "market_label": format_market_label(
                    signal.get("market_type"),
                    signal.get("selection"),
                    signal.get("line"),
                ),
                "odds": signal.get("odds"),
                "outcome": label,
                "detail": prediction.get("detail") or "",
                "trend_points": _signal_trend_points(record, signal),
            }
            detail_signals.append(item)
            if grade in ("S", "A") and label:
                sa_badges.append(item)
        home_score = result.get("home_score", EM_DASH)
        away_score = result.get("away_score", EM_DASH)
        day["matches"].append(
            {
                "kickoff_at_utc": record.get("kickoff_at_utc"),
                "kickoff_date": date_label,
                "kickoff_date_iso": date_key,
                "home_team": format_team_label(record.get("home_team")),
                "away_team": format_team_label(record.get("away_team")),
                "source_home_team": record.get("home_team"),
                "source_away_team": record.get("away_team"),
                "competition_id": competition_id_for_match(record),
                "competition_label": competition_label_for_match(record),
                "matchup": format_matchup_label(record.get("home_team"), record.get("away_team")),
                "score_label": f"{home_score} - {away_score}",
                "stage_group": _format_stage_group(record.get("stage"), record.get("group")),
                "kickoff_time": kickoff.strftime("%H:%M") if kickoff else "",
                "closing_snapshot_at": record.get("closing_snapshot_at"),
                "sa_badges": sa_badges,
                "detail_signals": detail_signals,
            }
        )
    ordered_days = sorted(days.values(), key=lambda day: day["_sort_key"], reverse=True)
    for day in ordered_days:
        day.pop("_sort_key", None)
    return {
        "days": ordered_days,
        "tally": finished.get("tally") or {},
        "summary": summarize_finished_block(snapshot),
    }
