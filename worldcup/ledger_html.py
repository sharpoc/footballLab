from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from worldcup.ledger import build_summary_metrics, derive_quality_status, project_signal_rows


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


def _quality_values(snapshot: dict[str, Any], key: str) -> list[Any]:
    data_quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    if key in data_quality:
        return _as_list(data_quality.get(key))
    return _as_list(run.get(key))


def _source_error_label(item: Any) -> str:
    if isinstance(item, dict):
        source = item.get("source", "")
        error = item.get("error", "")
        if source or error:
            return f"{source}: {error}".strip(": ")
    return str(item)


def _list_html(values: list[Any], empty: str = "None reported.") -> str:
    if not values:
        return f"<p class=\"muted\">{escape(empty)}</p>"
    items = "".join(f"<li>{_text(value)}</li>" for value in values)
    return f"<ul>{items}</ul>"


def _metric_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return "None"
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
    <section class="ledger-controls" aria-label="Ledger controls">
      <div class="filter-group" role="group" aria-label="Grade filter">
        <button type="button" class="filter-button active" data-filter="all">All</button>
        <button type="button" class="filter-button" data-filter="strong">Strong (A)</button>
        <button type="button" class="filter-button" data-filter="watch">Watch (B)</button>
        <button type="button" class="filter-button" data-filter="weak">Weak (C)</button>
      </div>
      <label class="search-label">
        <span>Search</span>
        <input type="search" id="ledger-search" placeholder="Team, market, grade" autocomplete="off">
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


def _row_search_text(row: dict[str, Any]) -> str:
    parts = [
        row.get("matchup", ""),
        row.get("kickoff_at_utc", ""),
        row.get("market_label", ""),
        row.get("model_prob", ""),
        row.get("market_prob", ""),
        row.get("ev", ""),
        row.get("edge", ""),
        row.get("grade", ""),
        row.get("freshness", ""),
        row.get("explanation", ""),
    ]
    return " ".join(str(part) for part in parts).lower()


def _render_signal_table(snapshot: dict[str, Any]) -> str:
    rows = project_signal_rows(snapshot)
    if not rows:
        return """
        <section class="ledger-table-wrap">
          <div class="empty-state">
            <h2>No research signals</h2>
            <p>Current snapshot has no rows that meet the signal thresholds.</p>
          </div>
        </section>
        """

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("kickoff_date") or "Date unavailable")].append(row)

    table_rows = []
    for kickoff_date, date_rows in grouped.items():
        table_rows.append(
            "<tr class=\"date-row\"><th colspan=\"9\">{}</th></tr>".format(_text(kickoff_date))
        )
        for row in date_rows:
            grade = row.get("grade", "")
            grade_bucket = _grade_bucket(grade)
            search_text = _row_search_text(row)
            table_rows.append(
                "<tr class=\"signal-row\" data-grade=\"{grade_bucket}\" data-search=\"{search}\">"
                "<td><strong>{matchup}</strong><span>{stage_group}</span></td>"
                "<td>{kickoff}</td>"
                "<td>{market}</td>"
                "<td>{model_prob}</td>"
                "<td>{market_prob}</td>"
                "<td><strong>{ev}</strong><span>{edge}</span></td>"
                "<td><span class=\"grade-pill\">{grade}</span></td>"
                "<td>{freshness}</td>"
                "<td>{why}</td>"
                "</tr>".format(
                    grade_bucket=_text(grade_bucket),
                    search=_text(search_text),
                    matchup=_text(row.get("matchup")),
                    stage_group=_text(
                        " | ".join(
                            part
                            for part in [str(row.get("stage") or ""), str(row.get("group") or "")]
                            if part
                        )
                    ),
                    kickoff=_text(row.get("kickoff_time") or row.get("kickoff_at_utc")),
                    market=_text(row.get("market_label")),
                    model_prob=_text(row.get("model_prob")),
                    market_prob=_text(row.get("market_prob")),
                    ev=_text(row.get("ev")),
                    edge=_text(row.get("edge")),
                    grade=_text(grade),
                    freshness=_text(row.get("freshness")),
                    why=_text(row.get("explanation")),
                )
            )

    return """
    <section class="ledger-table-wrap">
      <table class="ledger-table">
        <thead>
          <tr>
            <th>Matchup</th>
            <th>Kickoff (UTC)</th>
            <th>Market</th>
            <th>Model Prob</th>
            <th>Market Prob</th>
            <th>EV / Edge</th>
            <th>Grade</th>
            <th>Freshness</th>
            <th>Why this is a signal</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
      <p class="no-results" hidden>No rows match the current filters.</p>
    </section>
    """.format(rows="\n".join(table_rows))


def _render_source_health(snapshot: dict[str, Any]) -> str:
    quality = derive_quality_status(snapshot)
    run = snapshot.get("run") or {}
    quota = run.get("quota") or {}
    quota_items = []
    for provider, values in quota.items():
        if isinstance(values, dict):
            remaining = values.get("remaining", "unknown")
            used = values.get("used", "unknown")
            quota_items.append(f"{provider}: remaining {remaining}, used {used}")
        else:
            quota_items.append(f"{provider}: {values}")

    source_errors = [_source_error_label(item) for item in _quality_values(snapshot, "source_errors")]
    stale_sources = _quality_values(snapshot, "stale_sources")
    missing_odds = _quality_values(snapshot, "missing_odds")
    missing_elo = _quality_values(snapshot, "missing_elo")
    time_mismatches = _quality_values(snapshot, "time_mismatches")

    return """
    <section class="rail-card">
      <h2>Source Health</h2>
      <p><strong>Status:</strong> {status}</p>
      <p class="muted">Reasons: {reasons}</p>
      <h3>Quota</h3>
      {quota}
      <h3>Stale Sources</h3>
      {stale}
      <h3>Source Errors</h3>
      {errors}
      <h3>Missing Inputs</h3>
      {missing}
      <h3>Time Checks</h3>
      {time}
    </section>
    """.format(
        status=_text(quality.get("label")),
        reasons=_text(", ".join(quality.get("reasons") or []) or "none"),
        quota=_list_html(quota_items),
        stale=_list_html(stale_sources),
        errors=_list_html(source_errors),
        missing=_list_html([f"missing_odds: {value}" for value in missing_odds] + [f"missing_elo: {value}" for value in missing_elo]),
        time=_list_html(time_mismatches),
    )


def _render_right_rail(snapshot: dict[str, Any]) -> str:
    run = snapshot.get("run") or {}
    snapshot_at = snapshot.get("snapshot_at", "")
    run_id = run.get("run_id", "")
    return """
    <aside class="right-rail">
      <section class="rail-card">
        <h2>Methodology</h2>
        <p>Elo and Poisson model probabilities are compared with devigged market probabilities.</p>
        <p>Model probability is above the devigged market probability.</p>
        <p>Grades summarize EV, edge, and input freshness for research triage.</p>
      </section>
      {source_health}
      <section class="rail-card">
        <h2>Caveats</h2>
        <ul>
          <li>Model outputs are research signals only and can be wrong.</li>
          <li>Stale feeds, missing inputs, or kickoff mismatches reduce confidence.</li>
          <li>This page is a static export and may lag newer source data.</li>
        </ul>
      </section>
      <section class="rail-card">
        <h2>Time</h2>
        <p><strong>Last updated:</strong><br>{snapshot_at}</p>
        <p><strong>Run:</strong><br>{run_id}</p>
      </section>
    </aside>
    """.format(
        source_health=_render_source_health(snapshot),
        snapshot_at=_text(snapshot_at),
        run_id=_text(run_id),
    )


def build_research_ledger_html(snapshot: dict[str, Any]) -> str:
    snapshot_at = snapshot.get("snapshot_at", "")
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>World Cup 2026 | Research Ledger</title>
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
    .ledger-table {{ width: 100%; min-width: 980px; border-collapse: collapse; }}
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
    .grade-pill {{
      display: inline-flex;
      width: 34px;
      min-height: 28px;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: #e9eef5;
      color: #1f2937;
      font-weight: 800;
    }}
    .right-rail {{ display: grid; gap: 12px; }}
    .rail-card {{ padding: 16px; }}
    .no-results, .empty-state {{ padding: 18px; }}
    @media (max-width: 980px) {{
      header, .ledger-controls {{ flex-direction: column; align-items: stretch; }}
      .content-grid {{ grid-template-columns: 1fr; }}
      .right-rail {{ order: -1; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <p class="eyebrow">World Cup 2026</p>
        <h1>Research Ledger</h1>
        <p class="disclaimer">Research only, not betting advice. 研究分析工具，不构成投注建议。</p>
      </div>
      <p class="meta">Last updated<br>{snapshot_at}</p>
    </header>
    {summary}
    <div class="content-grid">
      <div>
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

      function applyFilters() {{
        var term = search ? search.value.trim().toLowerCase() : '';
        var visible = 0;
        rows.forEach(function (row) {{
          var gradeMatch = activeFilter === 'all' || row.dataset.grade === activeFilter;
          var textMatch = !term || row.dataset.search.indexOf(term) !== -1;
          var show = gradeMatch && textMatch;
          row.hidden = !show;
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

      buttons.forEach(function (button) {{
        button.addEventListener('click', function () {{
          activeFilter = button.dataset.filter || 'all';
          buttons.forEach(function (item) {{ item.classList.remove('active'); }});
          button.classList.add('active');
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
        table=_render_signal_table(snapshot),
        right_rail=_render_right_rail(snapshot),
    )
