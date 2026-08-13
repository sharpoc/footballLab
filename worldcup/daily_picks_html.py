from __future__ import annotations

from html import escape
from typing import Any


def _text(value: Any, fallback: str = "—") -> str:
    text = str(value if value is not None else fallback)
    return escape(text, quote=True)


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _render_nav() -> str:
    return (
        '<nav class="primary-nav" aria-label="主导航">'
        '<a class="primary-nav-item" href="/preview">单场分析</a>'
        '<a class="primary-nav-item active" aria-current="page" href="/daily-picks">每日精选</a>'
        '</nav>'
    )


def _render_single(index: int, row: dict[str, Any]) -> str:
    return (
        '<article class="daily-pick">'
        f'<h3>第{index}场：{_text(row.get("home_team"))} 对 {_text(row.get("away_team"))}</h3>'
        f'<p>比赛标识：{_text(row.get("match_id"))}</p>'
        f'<p>{_text(row.get("competition_label"))} · {_text(row.get("market"))} · {_text(row.get("selection"))}</p>'
        f'<p>预测概率 {_percent(row.get("prediction_probability"))} · 参考赔率 {_text(row.get("reference_odds"))}</p>'
        f'<p class="muted">比赛时间（UTC）：{_text(row.get("kickoff_at_utc"))}</p>'
        "</article>"
    )


def _render_combination(title: str, values: Any) -> str:
    items = values or []
    if not items:
        return (
            f'<section class="combination"><h3>{escape(title)}</h3>'
            f'<p>{escape("独立性近似组合分数")}：当前不足以形成组合。</p></section>'
        )
    item = items[0]
    ids = ", ".join(_text(value) for value in sorted(item.get("match_ids") or []))
    return (
        f'<section class="combination"><h3>{escape(title)}</h3>'
        f"<p>比赛：{ids}</p>"
        f'<p>{_text(item.get("score_label"), "独立性近似组合分数")}：{_percent(item.get("approximate_score"))}</p>'
        "<p>仅作组合概率研究，不是校准后的联合命中率。</p></section>"
    )


def _render_sidecar_single(index: int, row: dict[str, Any]) -> str:
    return (
        '<article class="daily-pick">'
        f'<h3>第{index}场：{_text(row.get("home_team"))} 对 {_text(row.get("away_team"))}</h3>'
        f'<p>{_text(row.get("competition_label"))} · {_text(row.get("market"))} · {_text(row.get("selection"))}</p>'
        f'<p>模型概率 {_percent(row.get("model_probability"))} · 市场隐含概率 {_percent(row.get("market_implied_probability"))} · Edge {_percent(row.get("edge"))}</p>'
        f'<p>最后更新（UTC）：{_text(row.get("last_update"))}</p>'
        f'<p class="muted">选择理由：{_text(row.get("selection_reason"))}</p>'
        '</article>'
    )


def build_daily_sidecar_html(payload: dict[str, Any]) -> str:
    safe = dict(payload)
    safe["singles"] = safe.get("top4") or []
    singles = safe["singles"]
    cycle = safe.get("cycle") or {}
    coverage = safe.get("coverage") or []
    degradation = safe.get("degradation_reasons") or []
    coverage_html = "".join(
        "<li><strong>{name}</strong>：{status}；{reason}</li>".format(
            name=_text(item.get("name")),
            status=_text(item.get("status")),
            reason=_text(item.get("reason")),
        )
        for item in coverage
    )
    singles_html = "".join(_render_sidecar_single(index, row) for index, row in enumerate(singles, 1))
    if not singles_html:
        singles_html = '<p class="empty">当前 sidecar 快照没有合格单场候选。</p>'
    template = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>每日精选 Sidecar</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f5f7fa;color:#18212f;line-height:1.6}}main{{max-width:1080px;margin:0 auto;padding:32px 20px}}.primary-nav{{display:flex;gap:16px;margin-bottom:16px}}.primary-nav-item{{color:#2b5876;text-decoration:none}}.primary-nav-item.active{{font-weight:700}}header,section,article{{background:#fff;border:1px solid #dfe5ec;border-radius:12px;padding:18px;margin:0 0 16px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}.muted,.empty{{color:#657286}}ul{{padding-left:22px}}</style></head>
<body><main><nav class="primary-nav" aria-label="主导航"><a class="primary-nav-item" href="/preview">单场分析</a><a class="primary-nav-item active" aria-current="page" href="/daily-picks-sidecar">每日精选 Sidecar</a></nav>
<header><h1>每日精选 Sidecar</h1><p>周期：{cycle_label}</p><p>生成时间（UTC）：{generated_at} · 快照 schema {schema_version}</p><p>候选比赛 {candidate_count} 场 · 已选 {selected_count} 场 · 最多 4 场</p></header>
<section><h2>全局 Top 4</h2>{singles}</section><div class="grid">{parlay_2}{parlay_3}</div><section><h2>覆盖状态</h2><ul>{coverage}</ul></section><section><h2>降级与 fail-closed</h2><ul>{degradation}</ul></section><section><p>本页仅读取已生成的 daily sidecar snapshot，不会因刷新页面联网。</p><p>仅用于研究分析，不构成投注建议。</p></section></main></body></html>"""
    return template.format(
        cycle_label=_text(cycle.get("label") or f'{cycle.get("start_at")} 至 {cycle.get("end_at")}'),
        generated_at=_text(safe.get("generated_at")),
        schema_version=_text(safe.get("schema_version")),
        candidate_count=_text(safe.get("candidate_count"), "0"),
        selected_count=_text(safe.get("selected_count"), "0"),
        singles=singles_html,
        parlay_2=_render_combination("2 串 1", safe.get("parlay_2")),
        parlay_3=_render_combination("3 串 1", safe.get("parlay_3")),
        coverage=coverage_html or "<li>暂无覆盖信息。</li>",
        degradation="".join(f"<li>{_text(reason)}</li>" for reason in degradation) or "<li>无降级原因。</li>",
    )


def build_daily_picks_html(payload: dict[str, Any]) -> str:
    cycle = payload.get("cycle") or {}
    singles = payload.get("singles") or []
    coverage = payload.get("coverage") or []
    degradation = payload.get("degradation_reasons") or []
    coverage_html = "".join(
        "<li><strong>{name}</strong>：{status}；{reason}</li>".format(
            name=_text(item.get("name")),
            status=_text(item.get("status")),
            reason=_text(item.get("reason")),
        )
        for item in coverage
    )
    degradation_html = "".join(f"<li>{_text(reason)}</li>" for reason in degradation)
    singles_html = "".join(_render_single(index, row) for index, row in enumerate(singles, 1))
    if not singles_html:
        singles_html = '<p class="empty">当前周期没有合格单场候选。</p>'
    template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日精选</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f5f7fa;color:#18212f;line-height:1.6}}
main{{max-width:1080px;margin:0 auto;padding:32px 20px}}
.primary-nav{{display:flex;gap:16px;margin-bottom:16px}}
.primary-nav-item{{color:#2b5876;text-decoration:none}}
.primary-nav-item.active{{font-weight:700}}
header,section,article{{background:#fff;border:1px solid #dfe5ec;border-radius:12px;padding:18px;margin:0 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}}
.daily-pick{{margin:0 0 12px}}
.daily-pick:last-child{{margin-bottom:0}}
.muted,.empty{{color:#657286}}
ul{{padding-left:22px}}
</style>
</head>
<body>
<main>
{nav}
<header>
<h1>每日精选</h1>
<p>推荐周期：{cycle_label}</p>
<p>生成时间（UTC）：{generated_at} · 数据时间（UTC）：{data_as_of}</p>
<p>候选比赛 {candidate_count} 场 · 已选 {selected_count} 场 · 最多 4 场</p>
</header>
<section>
<h2>本周期单场 Top 4</h2>
{singles}
</section>
<div class="grid">
{parlay_2}
{parlay_3}
</div>
<section>
<h2>覆盖状态</h2>
<ul>{coverage}</ul>
</section>
<section>
<h2>周期降级与空池原因</h2>
<ul>{degradation}</ul>
</section>
<section>
<p>本页是组合概率研究，仅展示独立性近似组合分数。</p>
<p>仅用于研究分析，不构成投注建议。</p>
</section>
</main>
</body>
</html>"""
    return template.format(
        nav=_render_nav(),
        cycle_label=_text(cycle.get("label")),
        generated_at=_text(payload.get("generated_at")),
        data_as_of=_text(payload.get("data_as_of")),
        candidate_count=_text(payload.get("candidate_count"), "0"),
        selected_count=_text(payload.get("selected_count"), "0"),
        singles=singles_html,
        parlay_2=_render_combination("2 串 1", payload.get("parlay_2")),
        parlay_3=_render_combination("3 串 1", payload.get("parlay_3")),
        coverage=coverage_html or "<li>暂无覆盖信息。</li>",
        degradation=degradation_html or "<li>无降级原因。</li>",
    )
