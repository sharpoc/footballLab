# 已完赛战绩区 + 赔率走势实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-06-12-finished-record-odds-trend-design.md` 落地两件套：完赛场次"定格"进 snapshot 的 `finished` 块（含 S/A 战绩 tally）并在页面渲染"本届信号战绩"卡 + "已完赛战绩"区；每场比赛附 `odds_trend` 走势数据并在展开详情渲染 SVG 迷你折线。

**Architecture:** 本地富化 snapshot（设计已确认方案一）：新增两个纯函数模块 `odds_trend.py`（从 history 归档提取走势，变化才记点 + 首末保留 + 30 点上限 + 文件名时间窗过滤）和 `finished_record.py`（results CSV × closing 归档 → 定格记录，**增量 store** 避免每 15 分钟全量扫描整届归档）；`refresh_runner` 发布前注入，失败只 warning；展示层在 `ledger.py` 投影 + `ledger_html.py` 渲染各加一段，服务器/ingest/静态导出零改动。

**Tech Stack:** Python 标准库；页面为服务端拼接的纯 HTML/CSS/原生 JS，SVG 手写 polyline，无任何前端依赖。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

基线 `330/330`（以实际为准）。

**已核实的代码事实（实现者必读，勿再猜）：**

- snapshot 信号字典字段：`market_type`（值为 `"1X2_90min" / "OverUnder_90min" / "AsianHandicap_90min"`）、`selection`（AH 形如 `"home_-1.0"`）、`grade`（`"S"`…）、`ev/edge/status/reasons/line`。**不含赔率**——赔率在 `match["market"]` 块（`"1x2"/"ou_2_5"` 的 `odds[selection]`；`"ah_main"` 的 `odds["home"/"away"]` + `line_home`）。
- 投影入口 `ledger.project_signal_rows(snapshot, previous_snapshot, cfg)` 逐 match 逐 signal 出 row；渲染入口 `ledger_html.build_research_ledger_html`；摘要卡 `ledger.build_summary_metrics` 返回 `{key: {label, value, tone?}}`，`ledger_html._render_summary` 按 `preferred` 列表挑卡渲染。
- 命中判定 `ledger._prediction_result(match, signal)` 接收 snapshot 字典（match 需带 `result`），返回 `{"label": "命中"|"未中"|"走水", "detail": ...}` 或 `None`。
- closing 选取 `eval_data.closing_match_entry(snapshots, kickoff_at_utc, home_canonical, away_canonical)` 已存在；`eval_data.load_snapshots(dir)` 全量加载（**不要**在新代码里全量用它，见时间窗约定）。
- history 归档文件名 `snapshot_<YYYYMMDDTHHMMSSZ>-live*.json` 可不打开文件就按名取时；目录里还有 `odds_raw_*.json.gz`，glob 必须限定 `snapshot_*.json`。
- 页面已有行展开 JS（`.signal-row` 切换详情行）；新完赛区用独立 class（`.finished-row` / `.finished-detail-row`）加 ~10 行委托点击 JS，不与现有筛选/搜索逻辑纠缠。
- **性能约定（必须遵守）**：到决赛归档会有 500+ 份 × ~0.5MB；每轮 refresh 不允许全量加载。走势提取只读"最近 `TREND_WINDOW_DAYS=10` 天"的归档（按文件名时间过滤）；finished 用增量 store（`data/local/finished_record_store.json`），已定格的比赛不再重算，新完赛比赛只加载其 kickoff 前 3 天窗口的归档。
- 安全：不出现资金字段；免责声明不动；store 与产物都在被忽略的 `data/local/`。
- 全程离线、不 push、不部署、不触发 live refresh、不调用 The Odds API；每任务本地 commit。ECS 部署在全部落地并经用户确认后单独进行。

---

### Task 1: `worldcup/odds_trend.py` 走势提取纯函数

**Files:**
- Create: `worldcup/odds_trend.py`
- Test: `tests/test_odds_trend.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_odds_trend.py`：

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.odds_trend import (
    attach_trends,
    extract_match_trend,
    list_history_files,
)


def _hist_snapshot(at: str, odds_home: float, ah_line: float = -1.0) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-15T19:00:00+00:00",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {
                        "line_home": ah_line,
                        "odds": {"home": 1.74, "away": 2.12},
                    },
                },
            }
        ],
    }


def test_extract_trend_keeps_only_changes_plus_first_and_last():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85),
        _hist_snapshot("2026-06-12T06:00:00+00:00", 1.85),  # 无变化，跳过
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.80),  # 变化，保留
        _hist_snapshot("2026-06-12T18:00:00+00:00", 1.80),  # 无变化但是最新点，保留
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    home_points = trend["1x2"]["home"]
    assert [p[1] for p in home_points] == [1.85, 1.8, 1.8]
    assert home_points[0][0] == "2026-06-12T00:00:00+00:00"
    assert home_points[-1][0] == "2026-06-12T18:00:00+00:00"
    # OU 全程无变化：只剩首点 + 最新点
    assert [p[1] for p in trend["ou_2_5"]["over"]] == [1.9, 1.9]


def test_extract_trend_records_ah_line_per_point():
    snapshots = [
        _hist_snapshot("2026-06-12T00:00:00+00:00", 1.85, ah_line=-1.0),
        _hist_snapshot("2026-06-12T12:00:00+00:00", 1.85, ah_line=-1.25),
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa")

    ah_points = trend["ah_main"]["home"]
    assert ah_points[0][2] == -1.0
    assert ah_points[-1][2] == -1.25


def test_extract_trend_caps_points_per_selection():
    snapshots = [
        _hist_snapshot(f"2026-06-12T{h:02d}:{m:02d}:00+00:00", 1.5 + h * 0.01 + m * 0.0001)
        for h in range(20)
        for m in (0, 30)
    ]

    trend = extract_match_trend(snapshots, "mexico", "south_africa", max_points=30)

    assert len(trend["1x2"]["home"]) == 30
    # 上限裁剪保最新：末点必须是时间最大的那轮
    assert trend["1x2"]["home"][-1][0] == "2026-06-12T19:30:00+00:00"


def test_list_history_files_filters_by_filename_window():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "snapshot_20260601T000000Z-live.json"
        new = root / "snapshot_20260612T010000Z-live.json"
        raw = root / "odds_raw_20260612T010000Z-live.json.gz"
        for path in (old, new):
            path.write_text("{}")
        raw.write_text("x")

        files = list_history_files(root, since="2026-06-10T00:00:00+00:00")

        assert files == [new]


def test_attach_trends_writes_into_snapshot_matches():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        for at, odds in (
            ("20260612T000000Z", 1.85),
            ("20260612T120000Z", 1.80),
        ):
            iso = f"{at[:4]}-{at[4:6]}-{at[6:8]}T{at[9:11]}:{at[11:13]}:00+00:00"
            (root / f"snapshot_{at}-live.json").write_text(
                json.dumps(_hist_snapshot(iso, odds))
            )
        snapshot = _hist_snapshot("2026-06-12T13:00:00+00:00", 1.79)

        attach_trends(snapshot, root, now="2026-06-12T13:00:00+00:00")

        points = snapshot["matches"][0]["odds_trend"]["1x2"]["home"]
        assert [p[1] for p in points] == [1.85, 1.8]
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.odds_trend'`。

- [ ] **Step 3: 实现 `worldcup/odds_trend.py`**

```python
"""Extract per-match aggregated-odds trends from archived history snapshots.

纯离线：只读 data/local/history/，按文件名时间窗过滤，避免全量加载整届归档。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TREND_WINDOW_DAYS = 10
MAX_POINTS = 30
TREND_MARKETS = (
    ("1x2", ("home", "draw", "away")),
    ("ou_2_5", ("over", "under")),
    ("ah_main", ("home", "away")),
)
_NAME_RE = re.compile(r"^snapshot_(\d{8}T\d{6})Z.*\.json$")


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _file_time(path: Path) -> datetime | None:
    matched = _NAME_RE.match(path.name)
    if not matched:
        return None
    return datetime.strptime(matched.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def list_history_files(history_dir: str | Path, since: str | None = None) -> list[Path]:
    cutoff = _parse_at(since) if since else None
    out: list[tuple[datetime, Path]] = []
    for path in Path(history_dir).glob("snapshot_*.json"):
        at = _file_time(path)
        if at is None:
            continue
        if cutoff is not None and at < cutoff:
            continue
        out.append((at, path))
    return [path for _at, path in sorted(out)]


def _match_entry(snapshot: dict, home_canonical: str, away_canonical: str) -> dict | None:
    for entry in snapshot.get("matches", []):
        if (
            entry.get("home_canonical") == home_canonical
            and entry.get("away_canonical") == away_canonical
        ):
            return entry
    return None


def _point(at: str, market: str, block: dict, selection: str) -> list | None:
    odds = (block.get("odds") or {}).get(selection)
    if odds is None:
        return None
    if market == "ah_main":
        return [at, odds, block.get("line_home")]
    return [at, odds]


def _compress(raw: list[list], max_points: int) -> list[list]:
    """保留首点 + 所有数值变化点 + 最新点，再按上限裁剪（裁剪保首点和最新段）。"""
    if not raw:
        return []
    compressed = [raw[0]]
    for prev, point in zip(raw, raw[1:]):
        if point[1:] != prev[1:]:
            compressed.append(point)
    if compressed[-1][0] != raw[-1][0]:
        compressed.append(raw[-1])
    if len(compressed) > max_points:
        compressed = [compressed[0]] + compressed[-(max_points - 1):]
    return compressed


def extract_match_trend(
    snapshots: list[dict],
    home_canonical: str,
    away_canonical: str,
    max_points: int = MAX_POINTS,
) -> dict[str, dict[str, list]]:
    raw: dict[str, dict[str, list]] = {
        market: {selection: [] for selection in selections}
        for market, selections in TREND_MARKETS
    }
    ordered = sorted(
        (s for s in snapshots if s.get("snapshot_at")), key=lambda s: s["snapshot_at"]
    )
    for snapshot in ordered:
        entry = _match_entry(snapshot, home_canonical, away_canonical)
        if entry is None:
            continue
        at = snapshot["snapshot_at"]
        market_blocks = entry.get("market") or {}
        for market, selections in TREND_MARKETS:
            block = market_blocks.get(market) or {}
            for selection in selections:
                point = _point(at, market, block, selection)
                if point is not None:
                    raw[market][selection].append(point)
    return {
        market: {
            selection: _compress(series, max_points)
            for selection, series in selections.items()
        }
        for market, selections in raw.items()
    }


def attach_trends(
    snapshot: dict[str, Any],
    history_dir: str | Path,
    now: str | None = None,
    window_days: int = TREND_WINDOW_DAYS,
) -> None:
    now_at = _parse_at(now) if now else datetime.now(timezone.utc)
    since = (now_at - timedelta(days=window_days)).isoformat()
    history: list[dict] = []
    for path in list_history_files(history_dir, since=since):
        try:
            history.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if not history:
        return
    for match in snapshot.get("matches", []):
        home = match.get("home_canonical")
        away = match.get("away_canonical")
        if not home or not away:
            continue
        match["odds_trend"] = extract_match_trend(history, home, away)
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/odds_trend.py tests/test_odds_trend.py
git commit -m "feat: extract odds trends from history archives"
```

---

### Task 2: `worldcup/finished_record.py` 完赛定格与增量 store

**Files:**
- Create: `worldcup/finished_record.py`
- Test: `tests/test_finished_record.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_finished_record.py`：

```python
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.finished_record import build_finished_block


def _closing_snapshot(at: str) -> dict:
    return {
        "snapshot_at": at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "stage": "Matchday 1",
                "group": "Group A",
                "market": {
                    "1x2": {"odds": {"home": 1.78, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                    "ah_main": {"line_home": -1.0, "odds": {"home": 1.74, "away": 2.12}},
                },
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "S", "line": None},
                    {"market_type": "AsianHandicap_90min", "selection": "home_-2.0", "grade": "A", "line": -2.0},
                    {"market_type": "1X2_90min", "selection": "away", "grade": "C", "line": None},
                ],
            }
        ],
    }


def _write_results(path: Path, rows: list[dict]) -> None:
    columns = [
        "kickoff_at_utc", "home_team", "away_team",
        "home_canonical", "away_canonical", "home_score", "away_score", "captured_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


MEXICO_ROW = {
    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
    "home_team": "Mexico",
    "away_team": "South Africa",
    "home_canonical": "mexico",
    "away_canonical": "south_africa",
    "home_score": "2",
    "away_score": "0",
    "captured_at": "2026-06-12T01:00:00+00:00",
}


def test_build_finished_block_freezes_closing_and_tallies_sa():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        (history / "snapshot_20260611T180000Z-live.json").write_text(
            json.dumps(_closing_snapshot("2026-06-11T18:00:00+00:00"))
        )
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert len(block["matches"]) == 1
        record = block["matches"][0]
        assert record["result"] == {"home_score": 2, "away_score": 0}
        assert record["closing_snapshot_at"] == "2026-06-11T18:00:00+00:00"
        # 2-0 主胜：S 级主胜命中；A 级让球 home -2.0 净胜恰好 2 球走水
        by_grade = {s["grade"]: s for s in record["closing_signals"] if s["grade"] in ("S", "A")}
        assert by_grade["S"]["prediction"]["label"] == "命中"
        assert by_grade["A"]["prediction"]["label"] == "走水"
        assert block["tally"]["S"] == {"hit": 1, "miss": 0, "push": 0}
        assert block["tally"]["A"] == {"hit": 0, "miss": 0, "push": 1}
        # C 级信号保留在明细但不进 tally
        assert any(s["grade"] == "C" for s in record["closing_signals"])
        # closing 赔率从 market 块解析
        assert by_grade["S"]["odds"] == 1.78


def test_build_finished_block_is_incremental_via_store():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()
        closing = history / "snapshot_20260611T180000Z-live.json"
        closing.write_text(json.dumps(_closing_snapshot("2026-06-11T18:00:00+00:00")))
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])
        store = root / "store.json"

        first = build_finished_block(history, results, store)
        # 删掉 closing 归档：若第二次重算会丢失记录；增量 store 必须保住
        closing.unlink()
        second = build_finished_block(history, results, store)

        assert len(second["matches"]) == 1
        assert second["tally"] == first["tally"]


def test_build_finished_block_counts_missing_closing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        history = root / "history"
        history.mkdir()  # 没有任何归档
        results = root / "results.csv"
        _write_results(results, [MEXICO_ROW])

        block = build_finished_block(history, results, root / "store.json")

        assert block["matches"] == []
        assert block["skipped_no_closing"] == 1
        assert block["tally"]["S"] == {"hit": 0, "miss": 0, "push": 0}
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.finished_record'`。

- [ ] **Step 3: 实现 `worldcup/finished_record.py`**

```python
"""Freeze finished matches (closing signals x results) into a snapshot block.

增量 store：已定格的比赛不再重算；新完赛比赛只加载其 kickoff 前 3 天窗口的归档。
纯离线：只读 data/local/history/ 与 results CSV，store 写在被忽略的 data/local/。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worldcup.eval_data import closing_match_entry
from worldcup.ledger import _prediction_result
from worldcup.odds_trend import extract_match_trend, list_history_files

TRACKED_GRADES = ("S", "A")
_LABEL_TO_KEY = {"命中": "hit", "未中": "miss", "走水": "push"}
CLOSING_WINDOW_DAYS = 3


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _record_key(row: dict) -> str:
    return f"{row['kickoff_at_utc'][:10]}_{row['home_canonical']}_{row['away_canonical']}"


def _closing_odds(entry: dict, signal: dict) -> float | None:
    market = entry.get("market") or {}
    market_type = signal.get("market_type")
    selection = str(signal.get("selection") or "")
    if market_type == "1X2_90min":
        value = ((market.get("1x2") or {}).get("odds") or {}).get(selection)
    elif market_type == "OverUnder_90min":
        value = ((market.get("ou_2_5") or {}).get("odds") or {}).get(selection)
    elif market_type == "AsianHandicap_90min":
        side = selection.split("_", 1)[0]
        value = ((market.get("ah_main") or {}).get("odds") or {}).get(side)
    else:
        value = None
    return float(value) if isinstance(value, (int, float)) else None


def _freeze_record(entry: dict, row: dict, history: list[dict]) -> dict:
    result = {"home_score": int(row["home_score"]), "away_score": int(row["away_score"])}
    settled_match = {**entry, "result": {"status": "finished", **result}}
    closing_signals = []
    for signal in entry.get("signals") or []:
        prediction = _prediction_result(settled_match, signal)
        closing_signals.append(
            {
                "market_type": signal.get("market_type"),
                "selection": signal.get("selection"),
                "line": signal.get("line"),
                "grade": signal.get("grade"),
                "odds": _closing_odds(entry, signal),
                "prediction": prediction,
            }
        )
    return {
        "kickoff_at_utc": row["kickoff_at_utc"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "home_canonical": row["home_canonical"],
        "away_canonical": row["away_canonical"],
        "stage": entry.get("stage"),
        "group": entry.get("group"),
        "result": result,
        "closing_snapshot_at": None,  # caller 填
        "closing_signals": closing_signals,
        "odds_trend": extract_match_trend(history, row["home_canonical"], row["away_canonical"]),
    }


def _empty_tally() -> dict[str, dict[str, int]]:
    return {grade: {"hit": 0, "miss": 0, "push": 0} for grade in TRACKED_GRADES}


def _tally(records: list[dict]) -> dict[str, dict[str, int]]:
    tally = _empty_tally()
    for record in records:
        for signal in record.get("closing_signals") or []:
            grade = signal.get("grade")
            if grade not in tally:
                continue
            label = ((signal.get("prediction") or {}).get("label")) or ""
            key = _LABEL_TO_KEY.get(label)
            if key:
                tally[grade][key] += 1
    return tally


def build_finished_block(
    history_dir: str | Path,
    results_csv: str | Path,
    store_path: str | Path,
) -> dict[str, Any]:
    store_file = Path(store_path)
    store: dict[str, dict] = {}
    if store_file.exists():
        try:
            store = json.loads(store_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            store = {}

    results_rows: list[dict] = []
    results_file = Path(results_csv)
    if results_file.exists():
        with open(results_file, newline="", encoding="utf-8") as fh:
            results_rows = list(csv.DictReader(fh))

    skipped = 0
    for row in results_rows:
        key = _record_key(row)
        if key in store:
            continue
        kickoff = _parse_at(row["kickoff_at_utc"])
        since = (kickoff - timedelta(days=CLOSING_WINDOW_DAYS)).isoformat()
        window: list[dict] = []
        for path in list_history_files(history_dir, since=since):
            try:
                window.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        entry = closing_match_entry(
            window, row["kickoff_at_utc"], row["home_canonical"], row["away_canonical"]
        )
        if entry is None:
            skipped += 1
            continue
        closing_at = max(
            (s["snapshot_at"] for s in window
             if s.get("snapshot_at") and _parse_at(s["snapshot_at"]) < kickoff
             and any(
                 m.get("home_canonical") == row["home_canonical"]
                 and m.get("away_canonical") == row["away_canonical"]
                 for m in s.get("matches", [])
             )),
            default=None,
        )
        record = _freeze_record(entry, row, window)
        record["closing_snapshot_at"] = closing_at
        store[key] = record

    try:
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    records = sorted(store.values(), key=lambda r: r.get("kickoff_at_utc") or "")
    return {
        "matches": records,
        "tally": _tally(records),
        "skipped_no_closing": skipped,
    }
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/finished_record.py tests/test_finished_record.py
git commit -m "feat: freeze finished matches with incremental store"
```

---

### Task 3: `refresh_runner` 注入接线（容错）

**Files:**
- Modify: `worldcup/refresh_runner.py`
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: 写失败测试**

`tests/test_refresh_runner.py` 追加（fixture 复用文件内 `_elo_cache_fixture` 与 transports 模式）：

```python
def test_refresh_attaches_trend_and_finished_block():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        history = root / "history"
        history.mkdir()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        # 先跑一轮：归档产生第一份 history；再跑第二轮：走势应能从第一轮归档取到点
        for observed in ("2026-06-08T00:00:00+00:00", "2026-06-08T01:00:00+00:00"):
            result = refresh_cache_and_build_snapshot(
                api_key="fake-key",
                cache_dir=cache,
                snapshot_path=root / "out" / "snapshot.json",
                quota_path=cache / "quota.json",
                openfootball_transport=openfootball_transport,
                theoddsapi_transport=theoddsapi_transport,
                elo_transport=elo_transport,
                history_dir=history,
                observed_at=observed,
            )

        match = result.snapshot["matches"][0]
        assert "odds_trend" in match
        assert match["odds_trend"]["1x2"]["home"], "trend points should exist from first archive"
        assert "finished" in result.snapshot
        assert result.snapshot["finished"]["tally"]["S"] == {"hit": 0, "miss": 0, "push": 0}


def test_refresh_survives_enrichment_failure(monkeypatch=None):
    # 注入失败不阻断：把 results CSV 路径指向一个目录制造异常
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_cache_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()
        bad_results = root / "results_dir"
        bad_results.mkdir()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def elo_transport(url):
            if url.endswith("World.tsv"):
                return FakeResponse(b"1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
            if url.endswith("en.teams.tsv"):
                return FakeResponse(b"MX\tMexico\nZA\tSouth Africa\n")
            raise AssertionError(url)

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=elo_transport,
            history_dir=root / "history",
            results_csv=bad_results,  # 是目录不是文件，读取会抛 OSError
            observed_at="2026-06-08T00:00:00+00:00",
        )

        assert result.snapshot["counts"]["matches"] == 1  # 主链路不受影响
```

- [ ] **Step 2: 运行确认失败**

Expected: `refresh_cache_and_build_snapshot` 不接受 `results_csv` 参数 / snapshot 无 `odds_trend` / 无 `finished`。

- [ ] **Step 3: 最小实现**

3a. `worldcup/refresh_runner.py` import 区加：

```python
from worldcup.finished_record import build_finished_block
from worldcup.odds_trend import attach_trends
```

3b. `refresh_cache_and_build_snapshot` 签名追加两个参数（带默认值，放在 `theoddsapi_provider` 之后）：

```python
    results_csv: str | Path = "data/local/results/wc2026_results.csv",
    finished_store: str | Path = "data/local/finished_record_store.json",
```

3c. 在 `snapshot["run"] = run_metadata` 之后、`write_snapshot(snapshot, snapshot_output)` **之前**插入：

```python
    try:
        attach_trends(snapshot, history_dir, now=observed)
        snapshot["finished"] = build_finished_block(history_dir, results_csv, finished_store)
    except Exception as exc:
        print(f"warning: site enrichment failed: {exc}", file=sys.stderr)
```

（注意顺序：富化必须发生在 `write_snapshot` 与后续 history 归档之前，发布件与归档件才一致。）

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/refresh_runner.py tests/test_refresh_runner.py
git commit -m "feat: enrich snapshot with trends and finished block"
```

---

### Task 4: `ledger.py` 投影层（战绩卡指标 + 行走势 + 完赛视图 + 去重）

**Files:**
- Modify: `worldcup/ledger.py`
- Test: `tests/test_ledger.py`

- [ ] **Step 1: 写失败测试**

`tests/test_ledger.py` 追加：

```python
def _snapshot_with_finished() -> dict:
    return {
        "snapshot_at": "2026-06-12T08:00:00+00:00",
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",  # 与 finished 同场：应被主台账去重
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "S", "line": None}
                ],
                "odds_trend": {
                    "1x2": {"home": [["2026-06-10T00:00:00+00:00", 1.85], ["2026-06-11T18:00:00+00:00", 1.78]]},
                },
            },
            {
                "kickoff_at_utc": "2026-06-13T19:00:00+00:00",
                "home_team": "Canada",
                "away_team": "Qatar",
                "home_canonical": "canada",
                "away_canonical": "qatar",
                "signals": [
                    {"market_type": "1X2_90min", "selection": "home", "grade": "A", "line": None}
                ],
                "odds_trend": {
                    "1x2": {"home": [["2026-06-12T00:00:00+00:00", 1.60], ["2026-06-12T06:00:00+00:00", 1.55]]},
                },
            },
        ],
        "finished": {
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
                            "market_type": "1X2_90min", "selection": "home", "line": None,
                            "grade": "S", "odds": 1.78,
                            "prediction": {"label": "命中", "detail": "全场 2-0"},
                        },
                        {
                            "market_type": "1X2_90min", "selection": "away", "line": None,
                            "grade": "C", "odds": 4.8,
                            "prediction": {"label": "未中", "detail": "全场 2-0"},
                        },
                    ],
                    "odds_trend": {"1x2": {"home": [["2026-06-10T00:00:00+00:00", 1.85], ["2026-06-11T18:00:00+00:00", 1.78]]}},
                }
            ],
            "tally": {"S": {"hit": 1, "miss": 0, "push": 0}, "A": {"hit": 0, "miss": 0, "push": 0}},
            "skipped_no_closing": 0,
        },
    }


def test_project_signal_rows_skips_matches_present_in_finished():
    rows = project_signal_rows(_snapshot_with_finished())

    assert all(row["match_id"] != "2026-06-11_mexico_south_africa" for row in rows)
    assert any("Canada" in (row.get("matchup") or "") for row in rows)


def test_signal_rows_carry_trend_points_for_their_selection():
    rows = project_signal_rows(_snapshot_with_finished())

    canada = next(row for row in rows if "Canada" in (row.get("matchup") or ""))
    assert [p[1] for p in canada["odds_trend_points"]] == [1.6, 1.55]


def test_summary_metrics_include_sa_record_when_finished_present():
    metrics = build_summary_metrics(_snapshot_with_finished())

    assert metrics["record_s"]["value"] == "命中 1 · 未中 0 · 走水 0 · 命中率 100%"
    assert metrics["record_a"]["value"] == "命中 0 · 未中 0 · 走水 0 · 命中率 —"
    # upcoming_matches 同步去重：完赛重叠场不计入
    assert metrics["upcoming_matches"]["value"] == 1


def test_build_finished_view_groups_by_beijing_day():
    view = build_finished_view(_snapshot_with_finished())

    assert len(view["days"]) == 1
    day = view["days"][0]
    assert day["date_label"].startswith("2026 年 6 月 12 日")  # 19:00 UTC = 北京次日 03:00
    match = day["matches"][0]
    assert match["score_label"] == "2 - 0"
    assert match["sa_badges"][0]["grade"] == "S"
    assert match["sa_badges"][0]["outcome"] == "命中"
    # C 级进明细不进徽章
    assert all(badge["grade"] in ("S", "A") for badge in match["sa_badges"])
    assert any(item["grade"] == "C" for item in match["detail_signals"])
```

注意：`project_signal_rows` / `build_summary_metrics` 在该测试文件应已 import；新增 `build_finished_view` 加进 import。`matchup` / `match_id` 字段名以 `project_signal_rows` 现有输出为准（实现时先 `grep "match_id"` 核对 row 键名，若现有键名不同则改测试断言为现有键名，**不要**改投影既有键名）。

- [ ] **Step 2: 运行确认失败**

Expected: 去重测试 FAIL（墨西哥行仍在）、`odds_trend_points` KeyError、`record_s` KeyError、`build_finished_view` ImportError。

- [ ] **Step 3: 最小实现（`worldcup/ledger.py` 追加/修改）**

3a. 模块常量区加：

```python
TREND_MARKET_KEYS = {
    "1X2_90min": "1x2",
    "OverUnder_90min": "ou_2_5",
    "AsianHandicap_90min": "ah_main",
}
```

3b. 新增辅助：

```python
def _finished_identity_set(snapshot: dict[str, Any]) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for record in ((snapshot.get("finished") or {}).get("matches")) or []:
        kickoff = str(record.get("kickoff_at_utc") or "")
        out.add((kickoff[:10], str(record.get("home_canonical") or ""), str(record.get("away_canonical") or "")))
    return out


def _match_finished_identity(match: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(match.get("kickoff_at_utc") or "")[:10],
        str(match.get("home_canonical") or ""),
        str(match.get("away_canonical") or ""),
    )


def _signal_trend_points(match: dict[str, Any], signal: dict[str, Any]) -> list:
    trend = match.get("odds_trend") or {}
    market_key = TREND_MARKET_KEYS.get(str(signal.get("market_type") or ""))
    if not market_key:
        return []
    selection = str(signal.get("selection") or "")
    if market_key == "ah_main":
        selection = selection.split("_", 1)[0]
    return (trend.get(market_key) or {}).get(selection) or []
```

3c. `project_signal_rows` 的 match 循环开头加去重（`for match in snapshot.get("matches", []):` 之后第一行）：

```python
        if _match_finished_identity(match) in finished_ids:
            continue
```

并在循环前初始化 `finished_ids = _finished_identity_set(snapshot)`；在每个 signal 的 row 字典里追加一个键（与现有键并列）：

```python
                "odds_trend_points": _signal_trend_points(match, signal),
```

3d. `build_summary_metrics`：

- `upcoming_matches` 改为去重后计数：

```python
    finished_ids = _finished_identity_set(snapshot)
    upcoming = [
        m for m in (snapshot.get("matches") or [])
        if _match_finished_identity(m) not in finished_ids
    ]
```

`"value": len(upcoming)`。

- 末尾（return 前）当 `snapshot.get("finished")` 存在时合并战绩指标：

```python
    finished = snapshot.get("finished") or {}
    tally = finished.get("tally") or {}
    def _record_value(grade: str) -> str:
        entry = tally.get(grade) or {}
        hit, miss, push = entry.get("hit", 0), entry.get("miss", 0), entry.get("push", 0)
        decided = hit + miss
        rate = f"{round(hit * 100 / decided)}%" if decided else "—"
        return f"命中 {hit} · 未中 {miss} · 走水 {push} · 命中率 {rate}"
    if tally:
        metrics["record_s"] = {"label": "S 级战绩", "value": _record_value("S")}
        metrics["record_a"] = {"label": "A 级战绩", "value": _record_value("A")}
```

（注意：`metrics` 为函数内构建的 dict，按现有代码结构把这段放在 return 之前，必要时把现有字面量 return 改为先赋值再 return。）

3e. 新增完赛视图投影：

```python
def build_finished_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    finished = snapshot.get("finished") or {}
    records = finished.get("matches") or []
    days: dict[str, dict[str, Any]] = {}
    for record in records:
        kickoff = _to_beijing_time(_parse_datetime(record.get("kickoff_at_utc")))
        date_label = _format_kickoff_date(kickoff)
        day = days.setdefault(date_label, {"date_label": date_label, "matches": []})
        result = record.get("result") or {}
        sa_badges = []
        detail_signals = []
        for signal in record.get("closing_signals") or []:
            grade = str(signal.get("grade") or "")
            label = ((signal.get("prediction") or {}).get("label")) or ""
            detail = ((signal.get("prediction") or {}).get("detail")) or ""
            item = {
                "grade": grade,
                "market_label": format_market_label(
                    signal.get("market_type"), signal.get("selection"), signal.get("line")
                ),
                "odds": signal.get("odds"),
                "outcome": label,
                "detail": detail,
                "trend_points": _signal_trend_points(record, signal),
            }
            detail_signals.append(item)
            if grade in ("S", "A") and label:
                sa_badges.append(item)
        day["matches"].append(
            {
                "matchup": format_matchup_label(record.get("home_team"), record.get("away_team")),
                "score_label": f"{result.get('home_score')} - {result.get('away_score')}",
                "stage_group": _format_stage_group(record.get("stage"), record.get("group")),
                "kickoff_time": kickoff.strftime("%H:%M") if kickoff else "",
                "sa_badges": sa_badges,
                "detail_signals": detail_signals,
            }
        )
    ordered_days = sorted(days.values(), key=lambda d: d["date_label"], reverse=True)
    return {"days": ordered_days, "tally": finished.get("tally") or {}}
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/ledger.py tests/test_ledger.py
git commit -m "feat: project finished view and trend points"
```

---

### Task 5: `ledger_html.py` 渲染层（卡片 + sparkline + 完赛区）

**Files:**
- Modify: `worldcup/ledger_html.py`
- Test: `tests/test_preview.py`

- [ ] **Step 1: 写失败测试**

`tests/test_preview.py` 追加（snapshot fixture 在该文件 `_snapshot()` 基础上拷一份加 `finished` 与 `odds_trend`，结构同 Task 4 测试的 `_snapshot_with_finished`，比赛/信号字段照抄该文件现有 fixture 的完整 match 结构以满足渲染所需键）：

```python
def test_preview_renders_record_card_and_finished_section():
    snapshot = _snapshot_with_finished_for_preview()  # 本测试文件内新建的 fixture helper
    html = build_research_ledger_html(snapshot)

    assert "S 级战绩" in html
    assert "命中 1 · 未中 0 · 走水 0 · 命中率 100%" in html
    assert "已完赛战绩" in html
    assert "2 - 0" in html
    assert "finished-row" in html


def test_preview_renders_trend_sparkline_in_detail():
    snapshot = _snapshot_with_finished_for_preview()
    html = build_research_ledger_html(snapshot)

    assert "trend-spark" in html          # SVG sparkline class
    assert "<polyline" in html
    assert "赔率走势" in html
    assert "1.85" in html and "1.78" in html  # 文本兜底首末点


def test_preview_tolerates_missing_finished_and_trend():
    html = build_research_ledger_html(_snapshot())  # 既有无新键 fixture

    assert "已完赛战绩" not in html
    assert "trend-spark" not in html
```

（`_snapshot_with_finished_for_preview` 写成本文件 helper：取现有 `_snapshot()` 返回值，往第一场 match 加 `odds_trend`（1x2.home 两点 1.85→1.78），再加顶层 `finished` 块——字段同 Task 4 fixture。）

- [ ] **Step 2: 运行确认失败**

Expected: 三个断言组全 FAIL（无战绩卡文案、无 finished-row、无 trend-spark；第三个测试此时通过属正常——以前两个红灯为准）。

- [ ] **Step 3: 最小实现（`worldcup/ledger_html.py`）**

3a. import 行加 `build_finished_view`。

3b. `_render_summary` 的 `preferred` 列表在 `"upcoming_matches"` 之后插入 `"record_s", "record_a"`。

3c. 新增 sparkline 与走势块渲染：

```python
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
        '<svg class="trend-spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        'role="img" aria-label="赔率走势">'
        '<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/>'
        "</svg>"
    ).format(w=width, h=height, color=color, points=" ".join(points))


def _trend_text(points: list) -> str:
    if len(points) < 2:
        return ""
    def _label(point):
        at = _format_snapshot_time(point[0])
        return f"{at} {point[1]}"
    shown = points if len(points) <= 6 else [points[0]] + points[-5:]
    first, last = points[0][1], points[-1][1]
    delta = (last - first) / first * 100 if first else 0.0
    arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
    return " → ".join(_label(p) for p in shown) + f"（累计 {arrow}{abs(delta):.1f}%）"


def _render_odds_trend(points: list) -> str:
    if not points or len(points) < 2:
        return ""
    values = [float(p[1]) for p in points]
    return (
        '<div class="trend-block"><h3>赔率走势</h3>{spark}'
        '<p class="muted trend-text">{text}</p></div>'
    ).format(spark=_svg_sparkline(values), text=_text(_trend_text(points)))
```

3d. 在 `_render_signal_detail(row)` 的输出末尾拼接 `_render_odds_trend(row.get("odds_trend_points") or [])`（具体插入点：该函数现有 detail 项 HTML 之后、返回字符串之前）。

3e. 新增完赛区渲染：

```python
def _render_finished_section(snapshot: dict[str, Any]) -> str:
    view = build_finished_view(snapshot)
    if not view["days"]:
        return ""
    day_blocks = []
    for day in view["days"]:
        match_rows = []
        for index, match in enumerate(day["matches"]):
            badges = "".join(
                '<span class="grade-chip grade-{g}">{g}</span>'
                '<span class="outcome outcome-{slug}">{label}</span>'.format(
                    g=_text(item["grade"]), slug=_slug(item["outcome"]), label=_text(item["outcome"])
                )
                for item in match["sa_badges"]
            ) or '<span class="muted">无 S/A 信号</span>'
            details = "".join(
                '<li><span class="grade-chip grade-{g}">{g}</span> {market} @ {odds} — {outcome}（{detail}）{trend}</li>'.format(
                    g=_text(item["grade"]),
                    market=_text(item["market_label"]),
                    odds=_text(item["odds"] if item["odds"] is not None else "—"),
                    outcome=_text(item["outcome"] or "—"),
                    detail=_text(item["detail"]),
                    trend=_render_odds_trend(item.get("trend_points") or []),
                )
                for item in match["detail_signals"]
            )
            match_rows.append(
                '<tr class="finished-row" data-finished="1">'
                "<td>{time}</td><td>{matchup}</td><td>{score}</td><td>{stage}</td><td>{badges}</td>"
                "</tr>"
                '<tr class="finished-detail-row" hidden><td colspan="5"><ul>{details}</ul></td></tr>'.format(
                    time=_text(match["kickoff_time"]),
                    matchup=_text(match["matchup"]),
                    score=_text(match["score_label"]),
                    stage=_text(match["stage_group"]),
                    badges=badges,
                    details=details,
                )
            )
        day_blocks.append(
            '<tr class="finished-day"><td colspan="5">{label}</td></tr>{rows}'.format(
                label=_text(day["date_label"]), rows="".join(match_rows)
            )
        )
    return (
        '<section class="panel finished-panel"><h2>已完赛战绩</h2>'
        '<p class="muted">closing（开球前最后一轮）口径；仅用于研究分析，不构成投注建议。</p>'
        '<div class="table-scroll"><table class="finished-table">'
        "<thead><tr><th>开赛 (北京时间)</th><th>对阵</th><th>比分</th><th>阶段</th><th>S/A 信号与结果</th></tr></thead>"
        "<tbody>{body}</tbody></table></div></section>"
    ).format(body="".join(day_blocks))
```

3f. 页面组装：在 `build_research_ledger_html` 模板中，主台账 panel 之后、右栏卡片区之前插入 `{finished_section}` 占位并传入 `_render_finished_section(snapshot)`；CSS 增加（追加到模板 style 内）：

```css
.trend-spark { width: 220px; height: 44px; display: block; margin: 6px 0 2px; }
.trend-text { font-size: 12px; }
.finished-panel { margin-top: 18px; }
.finished-table { min-width: 760px; }
.finished-row { cursor: pointer; }
.outcome { margin: 0 8px 0 2px; font-size: 12px; }
.outcome-命中 { color: var(--accent); font-weight: 700; }
.outcome-未中 { color: var(--error); font-weight: 700; }
.outcome-走水 { color: var(--muted); font-weight: 700; }
```

（`_slug` 对中文的行为以现有实现为准：若 `_slug("命中")` 产出空串导致 class 无效，改用映射 `{"命中": "hit", "未中": "miss", "走水": "push"}` 生成 `outcome-hit` 等 class，并同步 CSS 与测试断言——实现时二选一保持一致。）

3g. 行内 JS 末尾追加完赛区展开（与现有脚本同一 `<script>` 块）：

```javascript
document.addEventListener('click', function (event) {
  var row = event.target.closest('.finished-row');
  if (!row) return;
  var detail = row.nextElementSibling;
  if (detail && detail.classList.contains('finished-detail-row')) {
    detail.hidden = !detail.hidden;
  }
});
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/ledger_html.py tests/test_preview.py
git commit -m "feat: render record card, finished section and sparklines"
```

---

### Task 6: 真实数据 smoke、浏览器 QA 与文档

**Files:**
- Modify: `README.md`、`RECENT_WORK.md`

- [ ] **Step 1: 用真实归档跑一次离线富化 smoke**

```bash
cd /Users/eagod/ai-dev/足彩
python3 - <<'EOF'
import json
from worldcup.finished_record import build_finished_block
from worldcup.odds_trend import attach_trends

snapshot = json.load(open("data/cache/analysis_snapshot.json"))
attach_trends(snapshot, "data/local/history")
snapshot["finished"] = build_finished_block(
    "data/local/history",
    "data/local/results/wc2026_results.csv",
    "data/local/finished_record_store.json",
)
with_trend = sum(1 for m in snapshot["matches"] if (m.get("odds_trend") or {}).get("1x2", {}).get("home"))
print({
    "matches": len(snapshot["matches"]),
    "with_trend": with_trend,
    "finished": len(snapshot["finished"]["matches"]),
    "tally": snapshot["finished"]["tally"],
    "skipped_no_closing": snapshot["finished"]["skipped_no_closing"],
})
json.dump(snapshot, open("data/local/backtest/p3_smoke_snapshot.json", "w"), ensure_ascii=False)
EOF
python3 -m worldcup.preview --snapshot data/local/backtest/p3_smoke_snapshot.json --out data/cache/preview.html 2>/dev/null || python3 - <<'EOF'
import json
from worldcup.ledger_html import build_research_ledger_html
snapshot = json.load(open("data/local/backtest/p3_smoke_snapshot.json"))
open("data/cache/preview.html", "w").write(build_research_ledger_html(snapshot))
EOF
```

Expected: `finished >= 1`（揭幕战墨西哥 2-0 已定格，S 级主胜应计 hit）、`with_trend` 接近全部场次；生成本地 `data/cache/preview.html`。（`worldcup.preview` CLI 参数以实际为准，不匹配就用第二段直接渲染。）

- [ ] **Step 2: 浏览器 QA**

打开 `data/cache/preview.html`：桌面宽度检查战绩卡数字、完赛区分组与徽章、点击完赛行展开明细、展开主台账信号详情看 sparkline 与文本点列；390px 宽度检查横向滚动仍限制在表格容器内、无页面级溢出；控制台无 error/warn。

- [ ] **Step 3: 文档同步**

3a. `README.md`：功能说明处加一段——snapshot 新增 `finished` 块与 `odds_trend` 字段（本地富化、老快照缺键页面容忍）；页面新增"本届信号战绩"卡与"已完赛战绩"区（closing 口径、S/A 进统计、走水不进分母）；模块列表加 `finished_record.py` / `odds_trend.py`。

3b. `RECENT_WORK.md` 顶部按既有格式追加"2026-06-12 已完赛战绩区与赔率走势"一节：背景（完赛场从 odds feed 消失）、数据契约、增量 store 与 10 天窗口的性能约定、smoke 与浏览器 QA 结果，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。

- [ ] **Step 4: 最终全量验证与 commit（不 push）**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
git add README.md RECENT_WORK.md
git commit -m "docs: record finished section and odds trend"
```

Expected: 全绿；无空白错误。

---

## 范围外（明确不做）

- 不做单家 bookmaker 走势、不做 line movement 信号化（另案回测立项）。
- 不做跨届战绩；只覆盖 2026 本届。
- 不改模型、信号分级、调度、评估链；不动服务器端代码。
- 不部署 ECS、不 push（落地验证后由用户单独确认，部署时顺带把"更新规则"卡新文案带上线）。

## 已知取舍（实现者与复核者知悉）

- 走势提取只看最近 10 天归档：小组赛后期场次的最早"开盘点"可能不在窗口内，文本点列从窗口首点开始；展示研究用途可接受。
- finished 增量 store 一旦定格不回算：若赛果后续被修正（极罕见），需手工删 store 对应键触发重算（README 不写此操作，避免误用）。
- `data/local/finished_record_store.json` 是站点数据的真相缓存，位于被忽略目录，不进 git。
