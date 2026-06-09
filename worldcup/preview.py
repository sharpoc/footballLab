from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
from typing import Any

from worldcup.query import project_match_rows


def _list_items(values: list[Any]) -> str:
    if not values:
        return "<li>无</li>"
    return "".join(f"<li>{escape(str(value))}</li>" for value in values)


def _quality_html(data_quality: dict[str, Any]) -> str:
    source_errors = data_quality.get("source_errors") or []
    source_error_items = [
        f"{item.get('source', '')}: {item.get('error', '')}" if isinstance(item, dict) else str(item)
        for item in source_errors
    ]
    return f"""
    <section>
      <h2>数据质量</h2>
      <div class="quality-grid">
        <div><strong>stale_sources</strong><ul>{_list_items(data_quality.get("stale_sources") or [])}</ul></div>
        <div><strong>source_errors</strong><ul>{_list_items(source_error_items)}</ul></div>
        <div><strong>missing_odds</strong><ul>{_list_items(data_quality.get("missing_odds") or [])}</ul></div>
        <div><strong>missing_elo</strong><ul>{_list_items(data_quality.get("missing_elo") or [])}</ul></div>
        <div><strong>time_mismatches</strong><ul>{_list_items(data_quality.get("time_mismatches") or [])}</ul></div>
      </div>
    </section>
    """


def _matches_table(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        stale = "是" if row.get("stale") else "否"
        body.append(
            "<tr>"
            f"<td>{escape(row.get('kickoff_at_utc', ''))}</td>"
            f"<td>{escape(row.get('stage', ''))}</td>"
            f"<td>{escape(row.get('group', '') or '')}</td>"
            f"<td>{escape(row.get('match_label', ''))}</td>"
            f"<td>{escape(str(row.get('signal_count', 0)))}</td>"
            f"<td>{escape(row.get('top_grade', '') or '')}</td>"
            f"<td>{stale}</td>"
            "</tr>"
        )
    return """
    <section>
      <h2>比赛</h2>
      <table>
        <thead>
          <tr>
            <th>UTC 开赛</th>
            <th>阶段</th>
            <th>小组</th>
            <th>对阵</th>
            <th>信号数</th>
            <th>最高等级</th>
            <th>缓存兜底</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </section>
    """.format(rows="\n".join(body))


def build_preview_html(snapshot: dict[str, Any]) -> str:
    counts = snapshot.get("counts") or {}
    run = snapshot.get("run") or {}
    rows = project_match_rows(snapshot)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>世界杯足彩分析站</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #17202a;
      background: #f7f9fb;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 48px;
    }}
    h1 {{ margin: 0 0 10px; font-size: 30px; }}
    h2 {{ margin-top: 28px; font-size: 20px; }}
    .meta, .disclaimer {{
      color: #4c5a67;
      line-height: 1.6;
    }}
    .disclaimer {{
      padding: 12px 14px;
      border-left: 4px solid #1f7a8c;
      background: #eef7f9;
    }}
    .counts, .quality-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .metric, .quality-grid > div {{
      background: #fff;
      border: 1px solid #d8e0e8;
      border-radius: 6px;
      padding: 12px;
    }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      border: 1px solid #d8e0e8;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid #e7edf3;
      text-align: left;
      font-size: 14px;
    }}
    th {{ background: #f0f4f8; }}
    ul {{ margin: 8px 0 0; padding-left: 18px; }}
  </style>
</head>
<body>
  <main>
    <h1>世界杯足彩分析站</h1>
    <p class="disclaimer">研究分析工具，不构成投注建议；不显示资金相关字段，不做追损、重注或串关喊单。</p>
    <p class="meta">snapshot_at: {escape(snapshot.get('snapshot_at', '') or '')}<br>run_id: {escape(run.get('run_id', '') or '')}</p>
    <section>
      <h2>概览</h2>
      <div class="counts">
        <div class="metric">fixtures<strong>{escape(str(counts.get('fixtures', 0)))}</strong></div>
        <div class="metric">matches<strong>{escape(str(counts.get('matches', 0)))}</strong></div>
        <div class="metric">odds_events<strong>{escape(str(counts.get('odds_events', 0)))}</strong></div>
        <div class="metric">match_inputs<strong>{escape(str(counts.get('match_inputs', 0)))}</strong></div>
      </div>
    </section>
    {_quality_html(snapshot.get('data_quality') or {})}
    {_matches_table(rows)}
  </main>
</body>
</html>
"""


def write_preview(snapshot: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_preview_html(snapshot), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a static local HTML preview from an analysis snapshot.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--out", default="data/cache/preview.html")
    args = parser.parse_args(argv)

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    write_preview(snapshot, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
