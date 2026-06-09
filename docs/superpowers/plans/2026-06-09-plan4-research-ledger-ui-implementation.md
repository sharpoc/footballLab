# Plan 4 Research Ledger UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first public-facing Research Ledger UI as a static, exportable page over the existing World Cup snapshot data.

**Architecture:** Keep the current Python/static export path and avoid adding a frontend build system. Add a focused ledger projection module for safe UI data, add a focused HTML renderer for the Research Ledger page, and keep `worldcup.preview` as the compatibility entry point used by `worldcup.export` and existing local routes.

**Tech Stack:** Python 3 standard library, existing `worldcup` modules, generated static HTML/CSS/vanilla JS, current `tests/run_tests.py` test runner.

---

## Scope Check

This plan implements one subsystem: the static/public UI surface. It does not deploy, push, connect cloud resources, change odds/model logic, or add PostgreSQL behavior.

## File Structure

- Create `worldcup/ledger.py`: snapshot-to-UI projection, summary metrics, source health, formatting helpers, and deterministic signal explanations.
- Create `worldcup/ledger_html.py`: Research Ledger HTML/CSS/vanilla JS renderer using projected ledger data.
- Modify `worldcup/preview.py`: delegate `build_preview_html(snapshot)` to `worldcup.ledger_html.build_research_ledger_html(snapshot)` while preserving `write_preview` and CLI behavior.
- Modify `worldcup/export.py`: no behavior change expected; existing static export should pick up the new preview HTML automatically.
- Create `tests/test_ledger.py`: projection and formatting tests.
- Modify `tests/test_preview.py`: update expectations from the old preview to Research Ledger behavior.
- Modify `tests/test_export.py`: ensure exported `index.html` contains Research Ledger content.
- Update `README.md`, `docs/superpowers/data-contract.md`, and `RECENT_WORK.md` after implementation and verification.

## Task 1: Ledger Projection Helpers

**Files:**
- Create: `worldcup/ledger.py`
- Create: `tests/test_ledger.py`

- [ ] **Step 1: Write failing projection tests**

Add `tests/test_ledger.py`:

```python
from worldcup.ledger import (
    build_signal_explanation,
    build_summary_metrics,
    derive_quality_status,
    format_market_label,
    format_percent,
    project_signal_rows,
)


def _snapshot():
    return {
        "snapshot_at": "2026-06-09T08:00:00+00:00",
        "run": {
            "run_id": "20260609T080000Z-live",
            "quota": {"theoddsapi": {"last": 3, "remaining": 494, "used": 6}},
            "stale_sources": ["theoddsapi"],
            "source_errors": [],
        },
        "counts": {"fixtures": 104, "matches": 2, "odds_events": 72},
        "data_quality": {
            "missing_odds": ["Canada vs Qatar"],
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
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "market": {"1x2": {"probs": {"home": 0.57, "draw": 0.25, "away": 0.18}}},
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
            },
            {
                "kickoff_at_utc": "2026-06-12T01:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group B",
                "home_team": "Canada",
                "away_team": "Qatar",
                "signals": [],
            },
        ],
    }


def test_format_percent_handles_values_and_missing():
    assert format_percent(0.041) == "+4.1%"
    assert format_percent(-0.004) == "-0.4%"
    assert format_percent(None) == "—"


def test_format_market_label_maps_known_market_types():
    assert format_market_label("1X2_90min", "home", None) == "1X2 - Home"
    assert format_market_label("OverUnder_90min", "Over", 2.5) == "O/U 2.5 - Over"
    assert format_market_label("AsianHandicap_90min", "home", -0.25) == "AH -0.25 - Home"


def test_derive_quality_status_warns_on_stale_or_missing_data():
    status = derive_quality_status(_snapshot())

    assert status["label"] == "WARN"
    assert status["tone"] == "warn"
    assert "stale_sources" in status["reasons"]
    assert "missing_odds" in status["reasons"]


def test_build_summary_metrics_counts_signal_grades():
    metrics = build_summary_metrics(_snapshot())

    assert metrics["upcoming_matches"]["value"] == 2
    assert metrics["strong_signals"]["value"] == 1
    assert metrics["watch_signals"]["value"] == 0
    assert metrics["weak_signals"]["value"] == 0
    assert metrics["stale_sources"]["value"] == 1
    assert metrics["overall_quality"]["value"] == "WARN"


def test_project_signal_rows_expands_signals_without_money_fields():
    rows = project_signal_rows(_snapshot())

    assert len(rows) == 1
    assert rows[0]["matchup"] == "Mexico vs South Africa"
    assert rows[0]["kickoff_date"] == "Thursday, Jun 11, 2026"
    assert rows[0]["market_label"] == "1X2 - Home"
    assert rows[0]["model_prob"] == "61.0%"
    assert rows[0]["market_prob"] == "57.0%"
    assert rows[0]["edge"] == "+4.1%"
    assert rows[0]["ev"] == "+5.2%"
    assert rows[0]["grade"] == "A"
    assert "stake" not in rows[0]
    assert "bet_amount" not in rows[0]
    assert "bankroll" not in rows[0]


def test_build_signal_explanation_is_deterministic_and_safe():
    signal = {"market_type": "1X2_90min", "edge": 0.041, "status": "OK"}
    text = build_signal_explanation(signal, stale=False)

    assert text == "Model probability is above the devigged market probability."
    assert "bet" not in text.lower()
    assert "stake" not in text.lower()
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'worldcup.ledger'`.

- [ ] **Step 3: Implement projection helpers**

Create `worldcup/ledger.py`:

```python
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
STRONG_GRADES = {"S", "A"}
WATCH_GRADES = {"B"}
WEAK_GRADES = {"C", "D"}


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_percent(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def format_probability(value: float | None) -> str:
    return format_percent(value, signed=False)


def format_kickoff_date(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Date unavailable"
    return parsed.strftime("%A, %b %-d, %Y")


def format_kickoff_time(value: str) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "—"
    return parsed.strftime("%H:%M")


def _selection_label(selection: str | None) -> str:
    labels = {
        "home": "Home",
        "away": "Away",
        "draw": "Draw",
        "over": "Over",
        "under": "Under",
    }
    if selection is None:
        return "—"
    return labels.get(str(selection).lower(), str(selection))


def format_market_label(market_type: str | None, selection: str | None, line: float | None) -> str:
    label = _selection_label(selection)
    if market_type == "1X2_90min":
        return f"1X2 - {label}"
    if market_type == "OverUnder_90min":
        line_label = f" {line:g}" if line is not None else ""
        return f"O/U{line_label} - {label}"
    if market_type == "AsianHandicap_90min":
        line_label = f" {line:g}" if line is not None else ""
        return f"AH{line_label} - {label}"
    return f"{market_type or 'Market'} - {label}"


def _quality_lists(snapshot: dict[str, Any]) -> dict[str, list[Any]]:
    data_quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    return {
        "stale_sources": list(data_quality.get("stale_sources") or run.get("stale_sources") or []),
        "source_errors": list(data_quality.get("source_errors") or run.get("source_errors") or []),
        "missing_odds": list(data_quality.get("missing_odds") or []),
        "missing_elo": list(data_quality.get("missing_elo") or []),
        "time_mismatches": list(data_quality.get("time_mismatches") or []),
    }


def derive_quality_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    quality = _quality_lists(snapshot)
    reasons = [name for name, values in quality.items() if values]
    if quality["source_errors"]:
        return {"label": "ATTENTION", "tone": "error", "reasons": reasons}
    if reasons:
        return {"label": "WARN", "tone": "warn", "reasons": reasons}
    return {"label": "GOOD", "tone": "ok", "reasons": []}


def _signal_grade(signal: dict[str, Any]) -> str:
    grade = str(signal.get("grade") or "")
    return grade if grade in GRADE_ORDER else ""


def build_summary_metrics(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = project_signal_rows(snapshot)
    counts = Counter(row["grade"] for row in rows)
    quality = _quality_lists(snapshot)
    overall = derive_quality_status(snapshot)
    matches = snapshot.get("matches") or []
    return {
        "upcoming_matches": {"label": "Upcoming matches", "value": len(matches)},
        "strong_signals": {
            "label": "Strong signals",
            "value": sum(counts[grade] for grade in STRONG_GRADES),
            "hint": "Grade A or stronger",
        },
        "watch_signals": {
            "label": "Watch signals",
            "value": sum(counts[grade] for grade in WATCH_GRADES),
            "hint": "Grade B",
        },
        "weak_signals": {
            "label": "Weak / no edge",
            "value": sum(counts[grade] for grade in WEAK_GRADES),
            "hint": "Grade C or lower",
        },
        "stale_sources": {"label": "Stale sources", "value": len(quality["stale_sources"])},
        "overall_quality": {
            "label": "Data quality overall",
            "value": overall["label"],
            "tone": overall["tone"],
        },
    }


def _model_probability(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    market_type = signal.get("market_type")
    selection = str(signal.get("selection") or "").lower()
    model = match.get("model") or {}
    if market_type == "1X2_90min":
        return (model.get("combined_1x2") or {}).get(selection)
    if market_type == "OverUnder_90min":
        key = "over" if selection == "over" else "under"
        return (model.get("ou_2_5") or {}).get(key)
    return None


def _market_probability(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    market_type = signal.get("market_type")
    selection = str(signal.get("selection") or "").lower()
    market = match.get("market") or {}
    if market_type == "1X2_90min":
        return ((market.get("1x2") or {}).get("probs") or {}).get(selection)
    if market_type == "OverUnder_90min":
        key = "over" if selection == "over" else "under"
        return ((market.get("ou_2_5") or {}).get("probs") or {}).get(key)
    return None


def build_signal_explanation(signal: dict[str, Any], stale: bool) -> str:
    if stale:
        return "Signal is capped because one or more inputs are stale or missing."
    market_type = signal.get("market_type")
    if market_type == "1X2_90min":
        return "Model probability is above the devigged market probability."
    if market_type == "OverUnder_90min":
        return "Model total-goals distribution differs from the market total."
    if market_type == "AsianHandicap_90min":
        return "Settlement EV is positive at the current handicap line."
    return "Model and market estimates differ enough to flag for review."


def project_signal_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    stale = bool(_quality_lists(snapshot)["stale_sources"])
    rows: list[dict[str, Any]] = []
    for match in snapshot.get("matches") or []:
        home = match.get("home_team") or ""
        away = match.get("away_team") or ""
        kickoff = match.get("kickoff_at_utc") or ""
        for signal in match.get("signals") or []:
            grade = _signal_grade(signal)
            rows.append(
                {
                    "matchup": f"{home} vs {away}".strip(),
                    "home_team": home,
                    "away_team": away,
                    "kickoff_at_utc": kickoff,
                    "kickoff_date": format_kickoff_date(kickoff),
                    "kickoff_time": format_kickoff_time(kickoff),
                    "stage": match.get("stage") or "",
                    "group": match.get("group") or "",
                    "market_type": signal.get("market_type") or "",
                    "market_label": format_market_label(
                        signal.get("market_type"),
                        signal.get("selection"),
                        signal.get("line"),
                    ),
                    "model_prob": format_probability(_model_probability(match, signal)),
                    "market_prob": format_probability(_market_probability(match, signal)),
                    "edge": format_percent(signal.get("edge")),
                    "ev": format_percent(signal.get("ev")),
                    "grade": grade,
                    "status": signal.get("status") or "",
                    "freshness": "Stale" if stale else "Fresh",
                    "stale": stale,
                    "explanation": build_signal_explanation(signal, stale),
                }
            )
    return sorted(rows, key=lambda row: (row["kickoff_at_utc"], -GRADE_ORDER.get(row["grade"], 0)))
```

- [ ] **Step 4: Run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass, with the total increased by the new ledger tests.

- [ ] **Step 5: Commit**

```bash
git add worldcup/ledger.py tests/test_ledger.py
git commit -m "feat: add research ledger projection"
```

## Task 2: Research Ledger HTML Renderer

**Files:**
- Create: `worldcup/ledger_html.py`
- Modify: `tests/test_preview.py`

- [ ] **Step 1: Write failing preview tests**

Update `tests/test_preview.py` to assert the new Research Ledger surface while preserving safety checks:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.preview import build_preview_html, write_preview


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {
            "run_id": "20260608T000000Z-live",
            "quota": {"theoddsapi": {"remaining": 494, "used": 6}},
            "stale_sources": ["theoddsapi"],
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
                "model": {"combined_1x2": {"home": 0.61, "draw": 0.23, "away": 0.16}},
                "market": {"1x2": {"probs": {"home": 0.57, "draw": 0.25, "away": 0.18}}},
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

    assert "World Cup 2026" in html
    assert "Research Ledger" in html
    assert "Research only, not betting advice." in html
    assert "研究分析工具，不构成投注建议" in html
    assert "Mexico vs South Africa" in html
    assert "1X2 - Home" in html
    assert "+4.1%" in html
    assert "Model probability is above the devigged market probability." in html
    assert "Methodology" in html
    assert "Source Health" in html
    assert "Caveats" in html
    assert "下注金额" not in html
    assert "stake" not in html.lower()
    assert "bet amount" not in html.lower()
    assert "bankroll" not in html.lower()


def test_write_preview_creates_parent_directory_and_file():
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "preview.html"

        write_preview(_snapshot(), out)

        assert out.exists()
        assert "Research Ledger" in out.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL because `build_preview_html` still renders the old Chinese preview.

- [ ] **Step 3: Implement HTML renderer**

Create `worldcup/ledger_html.py`:

```python
from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Any

from worldcup.ledger import build_summary_metrics, derive_quality_status, project_signal_rows


def _attr(value: str) -> str:
    return escape(value, quote=True)


def _metric_html(metrics: dict[str, dict[str, Any]]) -> str:
    cards = []
    for key in [
        "upcoming_matches",
        "strong_signals",
        "watch_signals",
        "weak_signals",
        "stale_sources",
        "overall_quality",
    ]:
        metric = metrics[key]
        tone = metric.get("tone", key)
        cards.append(
            f'<div class="metric metric-{_attr(str(tone))}">'
            f'<span>{escape(str(metric["label"]))}</span>'
            f'<strong>{escape(str(metric["value"]))}</strong>'
            f'<small>{escape(str(metric.get("hint", "")))}</small>'
            "</div>"
        )
    return "".join(cards)


def _grouped_rows_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<tr><td colspan="9" class="empty">No signals match these filters</td></tr>'
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["kickoff_date"]].append(row)
    body: list[str] = []
    for date, date_rows in grouped.items():
        body.append(
            f'<tr class="date-row"><td colspan="9">{escape(date)}'
            f'<span>{len(date_rows)} signals</span></td></tr>'
        )
        for row in date_rows:
            grade = row["grade"] or "NA"
            body.append(
                '<tr class="signal-row" '
                f'data-grade="{_attr(grade)}" '
                f'data-group="{_attr(row["group"])}" '
                f'data-market="{_attr(row["market_type"])}" '
                f'data-search="{_attr((row["matchup"] + " " + row["market_label"]).lower())}">'
                f'<td class="match">{escape(row["matchup"])}</td>'
                f'<td>{escape(row["kickoff_time"])}</td>'
                f'<td>{escape(row["market_label"])}</td>'
                f'<td>{escape(row["model_prob"])}</td>'
                f'<td>{escape(row["market_prob"])}</td>'
                f'<td class="edge">{escape(row["edge"])}</td>'
                f'<td><span class="grade grade-{_attr(grade)}">{escape(grade)}</span></td>'
                f'<td><span class="freshness">{escape(row["freshness"])}</span></td>'
                f'<td class="why">{escape(row["explanation"])}</td>'
                "</tr>"
            )
    return "".join(body)


def _source_health_html(snapshot: dict[str, Any]) -> str:
    quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    stale_sources = set(quality.get("stale_sources") or run.get("stale_sources") or [])
    source_errors = quality.get("source_errors") or run.get("source_errors") or []
    error_sources = {
        item.get("source")
        for item in source_errors
        if isinstance(item, dict) and item.get("source")
    }
    sources = [
        ("openfootball", "Fixtures"),
        ("eloratings", "Elo Ratings"),
        ("theoddsapi", "Odds"),
        ("quota", "Quota / Usage"),
    ]
    items = []
    for key, label in sources:
        if key in error_sources:
            status = "ATTENTION"
            tone = "error"
        elif key in stale_sources:
            status = "WARN"
            tone = "warn"
        else:
            status = "OK"
            tone = "ok"
        items.append(
            f'<li><span>{escape(label)}</span><strong class="pill {tone}">{status}</strong></li>'
        )
    return "".join(items)


def build_research_ledger_html(snapshot: dict[str, Any]) -> str:
    rows = project_signal_rows(snapshot)
    metrics = build_summary_metrics(snapshot)
    quality = derive_quality_status(snapshot)
    snapshot_at = snapshot.get("snapshot_at") or ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>World Cup 2026 Research Ledger</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f8fafc;
      --surface: #ffffff;
      --line: #dce3ec;
      --line-soft: #edf1f5;
      --text: #0f1f3a;
      --muted: #64748b;
      --ok: #15905d;
      --warn: #c77800;
      --error: #c92a2a;
      --blue: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    .page {{ max-width: 1440px; margin: 0 auto; padding: 20px 22px 40px; }}
    .topbar, .metrics, .content {{ display: grid; gap: 16px; }}
    .topbar {{
      grid-template-columns: 1fr auto;
      align-items: center;
      border-bottom: 1px solid var(--line);
      padding-bottom: 18px;
    }}
    h1 {{ margin: 0; font-size: 28px; line-height: 1.1; }}
    .subtitle, .meta, .disclaimer, small {{ color: var(--muted); }}
    .disclaimer {{ font-size: 14px; }}
    .metrics {{ grid-template-columns: repeat(6, minmax(0, 1fr)); margin: 18px 0 22px; }}
    .metric {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-height: 92px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 28px; }}
    .content {{ grid-template-columns: minmax(0, 1fr) 300px; align-items: start; }}
    .ledger, .rail-section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }}
    .controls {{ display: flex; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
    button, select, input {{ height: 36px; border: 1px solid var(--line); background: white; border-radius: 6px; padding: 0 10px; color: var(--text); font: inherit; }}
    button.active {{ background: #0f1f3a; color: white; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--line-soft); text-align: left; font-size: 14px; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; background: #fbfdff; }}
    .date-row td {{ background: #f8fafc; color: var(--muted); font-weight: 650; }}
    .date-row span {{ margin-left: 12px; font-weight: 400; }}
    .match {{ font-weight: 650; }}
    .edge {{ color: var(--ok); font-weight: 650; }}
    .grade, .pill {{ display: inline-flex; min-width: 30px; justify-content: center; border-radius: 6px; padding: 3px 8px; font-weight: 700; font-size: 12px; }}
    .grade-S, .grade-A, .ok {{ background: #dcfce7; color: #166534; }}
    .grade-B, .warn {{ background: #fef3c7; color: #92400e; }}
    .grade-C, .grade-D {{ background: #f1f5f9; color: #475569; }}
    .error {{ background: #fee2e2; color: #991b1b; }}
    .why {{ max-width: 300px; color: #334155; line-height: 1.45; }}
    .rail {{ display: grid; gap: 12px; }}
    .rail-section {{ padding: 16px; }}
    .rail-section h2 {{ margin: 0 0 12px; font-size: 16px; }}
    .rail-section ul {{ margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }}
    .rail-section li {{ display: flex; justify-content: space-between; gap: 10px; color: #334155; }}
    .caveats li {{ display: list-item; list-style: disc; margin-left: 18px; }}
    .quality-banner {{ margin-top: 8px; color: var(--muted); font-size: 13px; }}
    .empty {{ color: var(--muted); text-align: center; padding: 32px; }}
    @media (max-width: 1100px) {{
      .metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .content {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .page {{ padding: 16px; }}
      .topbar {{ grid-template-columns: 1fr; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .ledger {{ overflow-x: auto; }}
      table {{ min-width: 980px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="topbar">
      <div>
        <h1>World Cup 2026 <span class="subtitle">| Research Ledger</span></h1>
        <p class="meta">Last updated: {escape(snapshot_at)} UTC</p>
      </div>
      <p class="disclaimer">Research only, not betting advice. 研究分析工具，不构成投注建议。</p>
    </header>
    <section class="metrics" aria-label="Summary metrics">{_metric_html(metrics)}</section>
    <section class="content">
      <div class="ledger">
        <div class="controls" aria-label="Ledger controls">
          <button class="active" type="button" data-grade-filter="all">All</button>
          <button type="button" data-grade-filter="strong">Strong (A)</button>
          <button type="button" data-grade-filter="watch">Watch (B)</button>
          <button type="button" data-grade-filter="weak">Weak (C)</button>
          <input id="ledger-search" type="search" placeholder="Search match or market" aria-label="Search match or market">
        </div>
        <table>
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
          <tbody id="ledger-body">{_grouped_rows_html(rows)}</tbody>
        </table>
      </div>
      <aside class="rail" aria-label="Research context">
        <section class="rail-section">
          <h2>Methodology</h2>
          <ul>
            <li><span>Elo Ratings</span></li>
            <li><span>Poisson Goal Model</span></li>
            <li><span>Market De-vig</span></li>
          </ul>
          <p class="quality-banner">Overall quality: {escape(quality["label"])}</p>
        </section>
        <section class="rail-section">
          <h2>Source Health</h2>
          <ul>{_source_health_html(snapshot)}</ul>
        </section>
        <section class="rail-section caveats">
          <h2>Caveats</h2>
          <ul>
            <li>Model probabilities are estimates, not guarantees.</li>
            <li>Markets can move for reasons models do not capture.</li>
            <li>Injuries, lineups, weather, and late news can change edge.</li>
          </ul>
        </section>
        <section class="rail-section">
          <h2>Time</h2>
          <p class="meta">All times in UTC.</p>
        </section>
      </aside>
    </section>
  </main>
  <script>
    const buttons = Array.from(document.querySelectorAll('[data-grade-filter]'));
    const search = document.getElementById('ledger-search');
    const rows = Array.from(document.querySelectorAll('tr.signal-row'));
    function rowMatchesGrade(row, filter) {{
      const grade = row.dataset.grade;
      if (filter === 'all') return true;
      if (filter === 'strong') return grade === 'S' || grade === 'A';
      if (filter === 'watch') return grade === 'B';
      if (filter === 'weak') return grade === 'C' || grade === 'D';
      return true;
    }}
    function applyFilters() {{
      const active = document.querySelector('[data-grade-filter].active')?.dataset.gradeFilter || 'all';
      const query = (search?.value || '').trim().toLowerCase();
      rows.forEach((row) => {{
        const visible = rowMatchesGrade(row, active) && (!query || row.dataset.search.includes(query));
        row.style.display = visible ? '' : 'none';
      }});
    }}
    buttons.forEach((button) => button.addEventListener('click', () => {{
      buttons.forEach((item) => item.classList.remove('active'));
      button.classList.add('active');
      applyFilters();
    }}));
    search?.addEventListener('input', applyFilters);
  </script>
</body>
</html>
"""
```

- [ ] **Step 4: Wire preview compatibility**

Modify `worldcup/preview.py` imports and `build_preview_html`:

```python
from worldcup.ledger_html import build_research_ledger_html


def build_preview_html(snapshot: dict[str, Any]) -> str:
    return build_research_ledger_html(snapshot)
```

Remove old private HTML helpers from `worldcup/preview.py` if they are no longer used.

- [ ] **Step 5: Run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add worldcup/ledger_html.py worldcup/preview.py tests/test_preview.py
git commit -m "feat: render research ledger preview"
```

## Task 3: Static Export Contract

**Files:**
- Modify: `tests/test_export.py`
- Modify: `worldcup/export.py` only if tests reveal a manifest mismatch

- [ ] **Step 1: Write export expectations**

Update the existing `test_export_static_site_writes_html_snapshot_and_matches_json` assertion:

```python
html = (out_dir / "index.html").read_text(encoding="utf-8")
assert "Research Ledger" in html
assert "Research only, not betting advice." in html
assert "stake" not in html.lower()
assert "bet amount" not in html.lower()
```

Keep the current JSON assertions for `api/snapshot/latest.json` and `api/matches.json`.

- [ ] **Step 2: Run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: PASS. If this fails because `export_static_site` still writes the old preview, confirm `worldcup.export` imports `build_preview_html` from `worldcup.preview` and no further export code change is needed after Task 2.

- [ ] **Step 3: Commit**

```bash
git add tests/test_export.py worldcup/export.py
git commit -m "test: verify research ledger export"
```

## Task 4: Generate Local Research Ledger Artifacts

**Files:**
- Generated ignored outputs: `data/cache/preview.html`, `data/cache/site/`
- No tracked source files expected

- [ ] **Step 1: Generate local preview**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.preview --snapshot data/cache/analysis_snapshot.json --out data/cache/preview.html
```

Expected:

```text
wrote data/cache/preview.html
```

- [ ] **Step 2: Export static site bundle**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.export --snapshot data/cache/analysis_snapshot.json --out-dir data/cache/site
```

Expected: JSON output with `index_path`, `snapshot_path`, `matches_path`, and `manifest_path` under `data/cache/site`.

- [ ] **Step 3: Verify generated HTML safety**

Run:

```bash
rg -n "Research Ledger|Research only, not betting advice|下注金额|stake|bet amount|bankroll" data/cache/preview.html data/cache/site/index.html
```

Expected:

- `Research Ledger` appears.
- `Research only, not betting advice` appears.
- `下注金额`, `stake`, `bet amount`, and `bankroll` do not appear.

- [ ] **Step 4: Confirm ignored outputs are not staged**

Run:

```bash
git status --short
```

Expected: no `data/cache/` files shown.

## Task 5: Browser QA

**Files:**
- No source files expected unless QA finds a layout bug

- [ ] **Step 1: Start a local static server**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m http.server 8790 --directory data/cache/site
```

Expected: server listens on `http://127.0.0.1:8790`.

- [ ] **Step 2: Open desktop viewport**

Use Browser plugin or Playwright to open:

```text
http://127.0.0.1:8790/
```

Desktop viewport target: `1440 x 1024`.

Expected:

- First viewport is not blank.
- Header, summary metrics, ledger table, and right rail are visible.
- Text does not overlap.
- The page does not look like a betting slip or promotional betting product.

- [ ] **Step 3: Open mobile viewport**

Use Browser plugin or Playwright with a narrow viewport around `390 x 844`.

Expected:

- Header, metrics, controls, and ledger remain usable.
- Table scrolls horizontally if needed.
- Right rail appears below or after the ledger.
- Disclaimer remains visible near the top.

- [ ] **Step 4: Stop the local server**

Stop the `http.server` process before continuing. Do not leave long-running sessions open.

## Task 6: Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README status**

Add a current status bullet:

```markdown
- Plan 4 Research Ledger UI is implemented as a static/exportable page over the existing snapshot; it remains local-only until deployment is separately confirmed.
```

Add `worldcup/ledger.py` and `worldcup/ledger_html.py` to the directory structure:

```text
  ledger.py                    # Research Ledger UI projection and formatting
  ledger_html.py               # Research Ledger static HTML renderer
```

- [ ] **Step 2: Update data contract**

Add a short `Research Ledger UI projection` subsection near the local preview/export section:

```markdown
### Research Ledger UI projection

`worldcup.ledger` projects snapshot data into public UI rows and summary metrics. It must not expose stake, bet amount, bankroll, payout, raw API keys, HMAC secrets, database URLs, cookies, or tokens.

`worldcup.ledger_html` renders the local Research Ledger preview. It uses deterministic signal explanations only; no runtime AI-generated claims are added to the public page.
```

- [ ] **Step 3: Update RECENT_WORK**

Add:

```markdown
- Implemented Plan 4 Research Ledger static UI over the existing local snapshot/export path.
- Generated ignored local preview artifacts in `data/cache/`; no deployment, push, live API call, or online write was performed.
```

- [ ] **Step 4: Run docs checks**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit docs**

```bash
git add README.md docs/superpowers/data-contract.md RECENT_WORK.md
git commit -m "docs: record research ledger ui workflow"
```

## Task 7: Final Verification

**Files:**
- No source files expected unless verification finds a bug

- [ ] **Step 1: Run full test suite**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. Note any existing warnings separately.

- [ ] **Step 2: Run readiness**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
```

Expected: `ok: true`, 12 checks, 0 errors, 0 warnings.

- [ ] **Step 3: Run sensitive value scan**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path
import subprocess

root = Path('.')
env_path = root / '.env'
tracked = subprocess.check_output(['git', 'ls-files'], text=True).splitlines()
values = []
if env_path.exists():
    for line in env_path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in stripped:
            continue
        key, value = stripped.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value and any(token in key.upper() for token in ('KEY', 'SECRET', 'TOKEN', 'PASSWORD', 'DATABASE_URL')):
            values.append((key, value))
leaks = []
for file_name in tracked:
    path = root / file_name
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    for key, value in values:
        if value in text:
            leaks.append((file_name, key))
print('tracked_sensitive_value_leaks=', len(leaks))
for file_name, key in leaks:
    print(f'{file_name}: {key}')
PY
```

Expected:

```text
tracked_sensitive_value_leaks= 0
```

- [ ] **Step 4: Confirm final git state**

Run:

```bash
git status --short --branch
```

Expected: clean branch with no unstaged or untracked source/doc changes.

## Implementation Notes

- Keep all generated preview/site output in ignored `data/cache/`.
- Do not add a JavaScript package manager or frontend build tool in this plan.
- Do not commit generated image files from `/Users/eagod/.codex/generated_images/`.
- Do not use real live API refresh unless separately confirmed.
- Do not deploy, push, or connect cloud resources in this plan.
