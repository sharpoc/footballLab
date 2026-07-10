from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from worldcup.ledger import (
    format_match_decision_market_label,
    format_probability,
    format_team_label,
)
from worldcup.query import project_finished_rows, project_match_rows


BEIJING_TZ = timezone(timedelta(hours=8))


def _text(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _parse_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_label(value: Any) -> str:
    parsed = _parse_at(value)
    if parsed is None:
        return "待确认"
    local = parsed.astimezone(BEIJING_TZ)
    return f"{local:%m-%d %H:%M}"


def _snapshot_label(value: Any) -> str:
    parsed = _parse_at(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def _line_label(value: Any, *, signed: bool = False) -> str:
    try:
        line = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(line) < 1e-12:
        return "0"
    return f"{line:+g}" if signed else f"{line:g}"


def _market_label(decision: dict[str, Any] | None) -> str:
    if not isinstance(decision, dict):
        return ""
    # The shared formatter also handles historical v1 decisions.
    return format_match_decision_market_label(decision).replace("—", "")


def _decision_view(decision: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(decision, dict) or decision.get("label") == "NO_CLEAN_MARKET":
        return {
            "state": "none",
            "title": "暂无可靠首选",
            "market": "数据质量或概率门槛未通过",
            "probability": "—",
            "no_loss": "—",
            "odds": "—",
        }
    market = _market_label(decision)
    if not market:
        return {
            "state": "none",
            "title": "暂无可靠首选",
            "market": "首选记录不完整",
            "probability": "—",
            "no_loss": "—",
            "odds": "—",
        }
    odds_value = decision.get("odds")
    odds_label = f"{float(odds_value):.2f}" if isinstance(odds_value, (int, float)) else "—"
    return {
        "state": "pick",
        "title": "本场首选",
        "market": market,
        "probability": format_probability(decision.get("p_hit_safe")),
        "no_loss": format_probability(decision.get("p_no_loss_safe")),
        "odds": odds_label,
    }


def _finished_keys(snapshot: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (
            str(record.get("kickoff_at_utc") or ""),
            str(record.get("home_team") or "").casefold(),
            str(record.get("away_team") or "").casefold(),
        )
        for record in ((snapshot.get("finished") or {}).get("matches") or [])
    }


def _live_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    finished = _finished_keys(snapshot)
    now_at = datetime.now(timezone.utc)
    rows = []
    for row in project_match_rows(snapshot):
        key = (
            str(row.get("kickoff_at_utc") or ""),
            str(row.get("home_team") or "").casefold(),
            str(row.get("away_team") or "").casefold(),
        )
        kickoff = _parse_at(row.get("kickoff_at_utc"))
        if key in finished:
            continue
        if kickoff is not None and kickoff <= now_at:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("kickoff_at_utc") or ""))


def _team_matchup(row: dict[str, Any]) -> str:
    home = format_team_label(str(row.get("home_team") or ""))
    away = format_team_label(str(row.get("away_team") or ""))
    return f"{home} 对 {away}"


def _competition_options(rows: list[dict[str, Any]]) -> str:
    seen: dict[str, str] = {}
    for row in rows:
        competition_id = str(row.get("competition_id") or "")
        if competition_id:
            seen[competition_id] = str(row.get("competition_label") or competition_id)
    return "".join(
        f'<option value="{_text(key)}">{_text(value)}</option>'
        for key, value in sorted(seen.items(), key=lambda item: item[1])
    )


def _render_live(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="empty">当前没有待开赛场次。</div>'
    cards = []
    for row in rows:
        decision = _decision_view(row.get("match_decision"))
        searchable = " ".join(
            [
                str(row.get("home_team") or ""),
                str(row.get("away_team") or ""),
                _team_matchup(row),
                str(row.get("competition_label") or ""),
            ]
        ).casefold()
        cards.append(
            """
            <article class="match-card" data-search="{search}" data-competition="{competition}">
              <div class="match-head">
                <div>
                  <span class="meta">{competition} · {kickoff}</span>
                  <h3>{matchup}</h3>
                  <span class="subtle">{stage}</span>
                </div>
                <span class="state state-{state}">{title}</span>
              </div>
              <div class="pick-line">
                <strong>{market}</strong>
                <div class="pick-stats">
                  <span>安全命中率 <b>{probability}</b></span>
                  <span>不亏概率 <b>{no_loss}</b></span>
                  <span>参考赔率 <b>{odds}</b></span>
                </div>
              </div>
            </article>
            """.format(
                search=_text(searchable),
                competition=_text(row.get("competition_id") or ""),
                kickoff=_text(_time_label(row.get("kickoff_at_utc"))),
                matchup=_text(_team_matchup(row)),
                stage=_text(" · ".join(filter(None, [row.get("stage"), row.get("group")])) or "赛程"),
                state=_text(decision["state"]),
                title=_text(decision["title"]),
                market=_text(decision["market"]),
                probability=_text(decision["probability"]),
                no_loss=_text(decision["no_loss"]),
                odds=_text(decision["odds"]),
            )
        )
    return "".join(cards)


def _render_history(finished: dict[str, Any]) -> str:
    matches = finished.get("matches") or []
    if not matches:
        return '<div class="empty">暂无已完赛首选记录。</div>'
    rows = []
    for match in reversed(matches):
        decision = match.get("closing_match_decision")
        outcome = match.get("decision_outcome") or {}
        if outcome.get("status") == "missing_decision":
            decision_text = "历史首选未记录"
        else:
            view = _decision_view(decision)
            decision_text = view["market"] if view["state"] == "pick" else view["title"]
            if (
                isinstance(decision, dict)
                and decision.get("policy_version") == "legacy_match_decision_v1"
            ):
                decision_text = f"{decision_text}（旧算法记录）"
        rows.append(
            """
            <tr>
              <td>{kickoff}</td>
              <td>{competition}</td>
              <td>{matchup}</td>
              <td class="score">{score}</td>
              <td>{decision}</td>
              <td><span class="outcome outcome-{status}">{outcome}</span></td>
            </tr>
            """.format(
                kickoff=_text(_time_label(match.get("kickoff_at_utc"))),
                competition=_text(match.get("competition_label") or ""),
                matchup=_text(_team_matchup(match)),
                score=_text(match.get("score_label") or "—"),
                decision=_text(decision_text),
                status=_text(outcome.get("status") or "unknown"),
                outcome=_text(outcome.get("label") or "待确认"),
            )
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>开赛</th><th>赛事</th><th>对阵</th><th>比分</th><th>收盘首选</th><th>结果</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _record_label(summary: dict[str, Any]) -> str:
    tally = summary.get("decision_tally") or {}
    hit = int(tally.get("hit") or 0)
    miss = int(tally.get("miss") or 0)
    push = int(tally.get("push") or 0)
    decided = hit + miss
    rate = f"{hit * 100 / decided:.0f}%" if decided else "—"
    return f"命中 {hit} · 未中 {miss} · 走水 {push} · 命中率 {rate}"


def build_match_decision_html(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> str:
    del previous_snapshot
    live_rows = _live_rows(snapshot)
    finished = project_finished_rows(snapshot)
    summary = finished.get("summary") or {}
    pick_count = sum(
        1
        for row in live_rows
        if isinstance(row.get("match_decision"), dict)
        and row["match_decision"].get("label") != "NO_CLEAN_MARKET"
        and row["match_decision"].get("market")
    )
    no_pick_count = len(live_rows) - pick_count
    sample = summary.get("sample") or {}
    sample_note = "样本不足，仅作观察" if sample.get("sample_too_small") else "样本达到观察门槛"
    stale = bool((snapshot.get("data_quality") or {}).get("stale_sources"))
    quality_label = "需注意" if stale else "正常"
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>本场首选</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{ color-scheme: light; --bg:#f4f7f6; --panel:#fff; --text:#15231f; --muted:#6b7b75; --line:#dfe8e4; --accent:#087f6d; --soft:#e8f5f2; --warn:#9a6700; --danger:#b42318; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:linear-gradient(180deg,#eef7f4 0,#f7f8f7 260px); color:var(--text); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .shell {{ width:min(1120px,calc(100% - 32px)); margin:0 auto; padding:34px 0 56px; }}
    header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:22px; }}
    h1 {{ margin:0; font-size:clamp(28px,4vw,44px); letter-spacing:-.04em; }}
    header p {{ margin:8px 0 0; color:var(--muted); }}
    .updated {{ color:var(--muted); white-space:nowrap; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:20px; }}
    .metric {{ background:rgba(255,255,255,.92); border:1px solid var(--line); border-radius:16px; padding:16px; box-shadow:0 8px 28px rgba(32,61,53,.05); }}
    .metric span {{ display:block; color:var(--muted); font-size:13px; }}
    .metric strong {{ display:block; margin-top:6px; font-size:20px; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; padding:20px; margin-top:16px; box-shadow:0 12px 35px rgba(32,61,53,.06); }}
    .section-head {{ display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:16px; }}
    h2 {{ margin:0; font-size:20px; }}
    .filters {{ display:flex; gap:10px; }}
    input,select {{ min-height:40px; border:1px solid var(--line); border-radius:10px; background:#fff; padding:0 12px; color:var(--text); }}
    .match-list {{ display:grid; gap:12px; }}
    .match-card {{ border:1px solid var(--line); border-radius:15px; padding:17px; background:#fff; }}
    .match-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }}
    .match-head h3 {{ margin:3px 0 0; font-size:18px; }}
    .meta,.subtle {{ color:var(--muted); font-size:13px; }}
    .state {{ border-radius:999px; padding:6px 10px; font-size:13px; font-weight:700; white-space:nowrap; }}
    .state-pick {{ color:var(--accent); background:var(--soft); }}
    .state-none {{ color:var(--warn); background:#fff6dc; }}
    .pick-line {{ display:flex; align-items:center; justify-content:space-between; gap:20px; margin-top:15px; padding-top:14px; border-top:1px solid #edf1ef; }}
    .pick-line > strong {{ font-size:17px; }}
    .pick-stats {{ display:flex; gap:18px; color:var(--muted); font-size:13px; }}
    .pick-stats b {{ color:var(--text); }}
    .table-wrap {{ overflow:auto; }}
    table {{ width:100%; min-width:760px; border-collapse:collapse; }}
    th,td {{ padding:12px 10px; border-bottom:1px solid #edf1ef; text-align:left; }}
    th {{ color:var(--muted); font-size:12px; font-weight:600; }}
    .score {{ font-weight:750; }}
    .outcome {{ font-weight:700; }}
    .outcome-hit {{ color:var(--accent); }} .outcome-miss {{ color:var(--danger); }} .outcome-push,.outcome-no_pick {{ color:var(--warn); }}
    .empty {{ padding:32px; text-align:center; color:var(--muted); border:1px dashed var(--line); border-radius:14px; }}
    footer {{ margin-top:24px; text-align:center; color:var(--muted); font-size:13px; }}
    @media (max-width:820px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }} header,.section-head,.pick-line {{ align-items:stretch; flex-direction:column; }} .filters {{ width:100%; }} input,select {{ min-width:0; width:100%; }} .pick-stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }} }}
    @media (max-width:520px) {{ .shell {{ width:min(100% - 20px,1120px); padding-top:22px; }} .metrics {{ grid-template-columns:1fr; }} .panel {{ padding:14px; }} .pick-stats {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div><h1>本场首选</h1><p>每场只保留一个高置信方向；不够可靠时主动放弃。</p></div>
      <div class="updated">更新于 {snapshot_at}</div>
    </header>
    <section class="metrics" aria-label="首选摘要">
      <div class="metric"><span>待开赛</span><strong>{upcoming}</strong></div>
      <div class="metric"><span>本场首选</span><strong>{picks}</strong></div>
      <div class="metric"><span>暂无可靠首选</span><strong>{no_picks}</strong></div>
      <div class="metric"><span>数据质量</span><strong>{quality}</strong></div>
    </section>
    <section class="panel">
      <div class="section-head">
        <h2>待开赛场次</h2>
        <div class="filters">
          <input id="match-search" type="search" placeholder="搜索球队" aria-label="搜索球队">
          <select id="competition-filter" aria-label="筛选赛事"><option value="">全部赛事</option>{competition_options}</select>
        </div>
      </div>
      <div id="match-list" class="match-list">{live}</div>
    </section>
    <section class="panel">
      <div class="section-head"><h2>本场首选战绩</h2><span class="subtle">{record} · {sample_note}</span></div>
      {history}
    </section>
    <footer>仅用于研究分析，不构成投注建议。</footer>
  </main>
  <script>
    (function () {{
      var search = document.getElementById('match-search');
      var competition = document.getElementById('competition-filter');
      function applyFilters() {{
        var query = (search.value || '').trim().toLowerCase();
        var league = competition.value || '';
        document.querySelectorAll('.match-card').forEach(function (card) {{
          var visible = (!query || (card.dataset.search || '').includes(query)) && (!league || card.dataset.competition === league);
          card.hidden = !visible;
        }});
      }}
      search.addEventListener('input', applyFilters);
      competition.addEventListener('change', applyFilters);
    }}());
  </script>
</body>
</html>""".format(
        snapshot_at=_text(_snapshot_label(snapshot.get("snapshot_at"))),
        upcoming=_text(len(live_rows)),
        picks=_text(pick_count),
        no_picks=_text(no_pick_count),
        quality=_text(quality_label),
        competition_options=_competition_options(live_rows),
        live=_render_live(live_rows),
        record=_text(_record_label(summary)),
        sample_note=_text(sample_note),
        history=_render_history(finished),
    )
