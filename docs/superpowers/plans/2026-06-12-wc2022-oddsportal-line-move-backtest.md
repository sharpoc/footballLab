# 2022 世界杯赔率爬取 + 线移动回测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 OddsHarvester（开源 OddsPortal 爬虫）一次性爬取 2022 卡塔尔世界杯 64 场的开盘/收盘赔率（1x2 / 大小球 / 亚洲让球），与 `data/local/backtest/intl_history.csv` 中已有的赛前 Elo join 成回测 CSV，并产出"让球线移动幅度 × 信号质量"的分桶研究报告，为本届 line movement guard（线异动警示/降级）提供回测证据。

**Architecture:** 三段式：① 爬取层用外部工具 OddsHarvester（clone 到被 gitignore 的 `tools/`，独立 venv，不进项目依赖），原始输出落盘 `data/local/backtest/oddsportal_wc2022_raw/`，试爬样例存 `data/probe/`；② 解析/join 层新增纯函数模块 `worldcup/oddsportal_wc2022.py`，把爬取 JSON 标准化、按 (日期±1天, 主队, 客队) canonical join 进 intl_history，写出含开盘+收盘双口径赔率列的 `wc2022_history.csv`（收盘用 `worldcup/backtest.py` 既有可选列名，开盘用 `open_` 前缀额外列，`load_matches` 会自动忽略多余列）；③ 分析层新增 `worldcup/line_move_report.py`，复用 `backtest.replay_match` / `handicap.ev_handicap` / `backtest.ah_realized_return`，按 |Δ让球线| 分桶统计信号命中率与单位回报，输出 JSON 报告 + 研究文档。

**Tech Stack:** 爬取层 OddsHarvester（Python ≥3.12 + Playwright，独立 venv）；项目层纯标准库 Python，零新依赖；测试沿用 `tests/run_tests.py` 纯函数断言风格。

**验证命令（项目测试全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

基线以执行时实际为准（当前应全绿）。

---

## 背景与合规边界（实现者必读）

- 目的：验证假设"让球线异动大的场次，模型 EV 信号质量更差"。若成立，本届快照将给线异动场次加警示/降级（另案）；若不成立，省下后续投入。
- OddsPortal ToS 不允许爬取。本计划是**一次性、小规模（64 场）、限速、个人研究**用途：全量爬取只跑一遍，中间产物落盘后绝不重爬；批与批之间 sleep ≥ 30 秒；失败不无限重试（同一批最多重试 2 次后停下来记录）。
- 本计划**不付费、不调用 The Odds API、不触发 live refresh、不部署、不 push**。
- 爬取产物含书商赔率，全部落在被 gitignore 的 `data/probe/`、`data/local/`、`tools/`，**不进 git**。
- 报告与研究文档保持研究口径：单位回报（per 1 unit stake，与既有 `ah_ev_buckets` 口径一致）、命中率、Brier；不出现资金/本金/下注金额字段，不构成投注建议。

## 已核实的代码事实（勿再猜，已逐一验证）

- `worldcup/backtest.py`：
  - `REQUIRED_COLUMNS = (match_id, kickoff_at_utc, home_team, away_team, home_score, away_score, home_elo_before, away_elo_before)`；`neutral` 列可选（`"0"`/`"1"`，缺省按 1）。
  - 可选赔率列名（`load_matches` 解析，全组齐才生效）：1x2 → `odds_home, odds_draw, odds_away`；大小球 2.5 → `odds_over, odds_under`；让球 → `ah_line, odds_ah_home, odds_ah_away`（`ah_line` 为主队视角，如 `-1.25`）。decimal odds 必须 > 1.0，否则 `load_matches` 抛错。
  - CSV 中**多余列会被 `csv.DictReader` 自然忽略**，所以 `open_` 前缀的开盘列不影响 `load_matches`。
  - `replay_match(match, cfg) -> dict`，返回键：`dr, mu_used, model_1x2, market_1x2, model_ou, market_ou, diff_dist`。
  - `outcome_1x2(home_score, away_score) -> "home"|"draw"|"away"`。
  - `ah_realized_return(goal_diff, line, odds) -> float`：主队侧 AH 单位回报（quarter-line 半赢半输已由 `handicap.ev_handicap` 处理）；客队侧用 `ah_realized_return(-goal_diff, -line, odds_away)`。
  - `brier_multiclass(probs, outcome)`、`load_matches(path)` 均可直接 import。
- `worldcup/engine/handicap.py`：`ev_handicap(dist, line, odds) -> float`（EV per 1 unit）；`diff_distribution(matrix)`。客队视角分布取反：`{-d: p for d, p in dist.items()}`（pipeline.py 的 `_invert_dist` 是同样实现，私有，勿 import，自己写单行）。
- `worldcup/config.py`：`load_config(path)`；config 文件 `config/settings.yaml`。
- 队名规范化：`worldcup.collectors.team_aliases.canonicalize_team(name) -> str`（`worldcup/backtest_data.py` 在用，可照抄 import）。
- `data/local/backtest/intl_history.csv`：14,901 行，列与 REQUIRED_COLUMNS + `neutral` 一致；2022-11-20 ~ 2022-12-18 窗口内有 85 行（含同期非世界杯比赛，**join 时以爬到的 64 场为主表**即可天然过滤）；`kickoff_at_utc` 是 `<date>T12:00:00Z` 占位（日期可靠，时刻不可靠，join 只用日期）。
- OddsHarvester（https://github.com/jordantete/OddsHarvester）：MIT；Python ≥3.12；Playwright 驱动；CLI 形如 `... historic -s football -l <league> --season <season> -m <market> --headless`；football 市场含 `1x2`、`asian_handicap`、`over_under` 系（确切 market 名以其 README/源码为准）；有 `--odds-history` flag 可带每场赔率变动序列；输出 JSON/CSV。**世界杯（非联赛）支持未在文档明确——Task 1 必须先探测，这是本计划最大风险点，有止损出口。**
- 项目 `.gitignore` 已忽略 `data/probe/`、`data/local/`、`data/cache/`、`data/raw/`；**尚未忽略 `tools/`**（Task 1 里加）。

## 风险与止损（实现者必读）

- **止损点 A（Task 1）**：若 OddsHarvester 不支持 World Cup 2022（league 常量没有、CLI 不接受自定义 slug/URL、源码改造点超过 ~30 行），**停止执行**，在 `RECENT_WORK.md` 记录探测结论（试过哪些命令、报错原文），交回用户决策（换 The Odds API historical 付费路线，或另立自写爬虫项目）。不要自行写大型爬虫。
- **止损点 B（Task 4）**：全量爬取若同一批连续失败 3 次（反爬升级/页面改版），保留已成功批次产物，停止并记录，交回用户。已爬到的部分照常进入后续 join/分析（样本量减少如实写进报告 `sample` 字段）。
- 队名对齐失败的场次：写进 `unmatched` 清单人工处理（Task 4 Step 5），不允许静默丢弃。
- 2022 年是回测语境：模型参数用当前 `config/settings.yaml`（与既有 intl 回测同口径），报告 notes 里注明"参数为 2026 当前值，非 2022 当时可得值"。

---

### Task 1: OddsHarvester 安装与世界杯支持探测（探测关卡）

**Files:**
- Modify: `.gitignore`（加 `tools/`）
- Create: `tools/OddsHarvester/`（git clone，不进 git）
- Create: `data/probe/oddsportal_wc2022_sample.json`（试爬真实样例，目录已被忽略）

- [ ] **Step 1: gitignore 加 tools/ 并 commit**

```bash
cd /Users/eagod/ai-dev/足彩
printf 'tools/\n' >> .gitignore
git add .gitignore
git commit -m "chore: ignore tools/ for external scraper checkouts"
```

- [ ] **Step 2: clone 与环境安装**

```bash
mkdir -p /Users/eagod/ai-dev/足彩/tools
git clone https://github.com/jordantete/OddsHarvester /Users/eagod/ai-dev/足彩/tools/OddsHarvester
cd /Users/eagod/ai-dev/足彩/tools/OddsHarvester
python3 --version   # 需 ≥3.12；若系统 python3 < 3.12，先探测 python3.12 / python3.13 是否存在，或用 `uv python install 3.12`
```

安装按其 README 实际指引执行（优先顺序）：

```bash
# 路线 1：uv（若本机有 uv）
uv sync && uv run playwright install chromium
# 路线 2：venv + pip
python3.12 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/playwright install chromium
```

记录实际成功的路线，后续命令统一用该环境（下文以 `<RUN>` 代指 `uv run` 或 `.venv/bin/python -m ...` 前缀，执行时替换为实际可用形式）。

- [ ] **Step 3: 探测世界杯支持**

```bash
cd /Users/eagod/ai-dev/足彩/tools/OddsHarvester
grep -rin "world.cup\|world_cup\|worldcup" src/ | head -20
# 同时看 league/sport 常量定义文件（通常是 constants/league mapping 一类）
grep -rln "premier-league\|premier_league" src/ | head
```

判定（按顺序取第一个成立的）：
1. league 常量里有 world cup（形如 `fifa-world-cup` / `world-cup`）→ 记下确切 key，走 Step 4。
2. CLI 的 `--league` 接受任意 OddsPortal slug（看参数校验代码是否白名单制）→ 试 slug `world-cup-2022`（OddsPortal URL 为 `https://www.oddsportal.com/football/world/world-cup-2022/results/`），走 Step 4。
3. 白名单制但加一个 league 映射条目 ≤ 30 行改动 → 在 tools 内本地 patch（不进 git），走 Step 4。
4. 都不行 → **止损点 A**：停止，把探测过程与报错记入 `RECENT_WORK.md`，报告用户。

- [ ] **Step 4: 试爬一个市场（限 1 页/最小范围），保存真实样例**

以下命令是模板，`<league-key>`、market 名、season 形参以 Step 3 探测结果与 README 实际为准（season 可能是 `2022` 或 `2022-2023`，世界杯赛事年通常为 `2022`）：

```bash
cd /Users/eagod/ai-dev/足彩/tools/OddsHarvester
<RUN> ... historic -s football -l <league-key> --season 2022 -m 1x2 --headless --max-pages 1 \
  --file-path /Users/eagod/ai-dev/足彩/data/probe/oddsportal_wc2022_sample.json
```

Expected: JSON 文件生成，含若干场 2022 世界杯比赛，每场有日期、主客队名、比分、各书商（或平均）1x2 赔率。

- [ ] **Step 5: 核对样例结构并记录字段事实**

```bash
python3 - <<'EOF'
import json
data = json.load(open("/Users/eagod/ai-dev/足彩/data/probe/oddsportal_wc2022_sample.json"))
print(type(data), len(data) if hasattr(data, "__len__") else "")
item = data[0] if isinstance(data, list) else data
print(json.dumps(item, ensure_ascii=False, indent=2)[:2000])
EOF
```

把以下字段的**真实键名**记到一个临时笔记（Task 2 写测试 fixture 要用）：比赛日期/时间、主队名、客队名、比分、开盘赔率、收盘（最新）赔率、（若有 `--odds-history`）变动序列、让球线字段、大小球线字段。

- [ ] **Step 6: 再试爬 asian_handicap 与 over_under 各 1 页，确认线字段形态**

```bash
<RUN> ... historic -s football -l <league-key> --season 2022 -m asian_handicap --headless --max-pages 1 \
  --file-path /Users/eagod/ai-dev/足彩/data/probe/oddsportal_wc2022_sample_ah.json
<RUN> ... historic -s football -l <league-key> --season 2022 -m over_under --headless --max-pages 1 \
  --file-path /Users/eagod/ai-dev/足彩/data/probe/oddsportal_wc2022_sample_ou.json
```

Expected: AH 样例里能看到带符号让球线（如 `-1.25`）与主客两侧赔率；OU 样例里能看到 totals 线（含 2.5）。若 market 名不对，用 `<RUN> ... historic --help` 查实际支持的 market 名称重试。

（本 Task 无项目代码改动，无测试、无 commit——产物均在被忽略目录。）

---

### Task 2: `worldcup/oddsportal_wc2022.py` 解析纯函数

**Files:**
- Create: `worldcup/oddsportal_wc2022.py`
- Test: `tests/test_oddsportal_wc2022.py`

> fixture 说明：下面测试里的输入 dict 是按 OddsHarvester README 描述拟的**形状示例**。Task 1 Step 5 拿到真实样例后，**以真实键名为准修正 fixture 的键**（这是数据形状对齐，不改测试逻辑与断言结构）。标准化输出 `NormalizedMatch` 的契约（本模块的输出键）不随 fixture 变。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_oddsportal_wc2022.py`：

```python
from worldcup.oddsportal_wc2022 import (
    NormalizedMatch,
    merge_markets,
    normalize_1x2,
    normalize_ah,
    normalize_ou,
)

# —— 输入 fixture：键名以 data/probe/oddsportal_wc2022_sample*.json 真实结构为准修正 ——
RAW_1X2 = {
    "match_date": "2022-11-22",
    "home_team": "Argentina",
    "away_team": "Saudi Arabia",
    "home_score": 1,
    "away_score": 2,
    "odds": {"1": {"open": 1.20, "close": 1.17}, "X": {"open": 6.5, "close": 7.0}, "2": {"open": 17.0, "close": 19.0}},
}
RAW_AH = {
    "match_date": "2022-11-22",
    "home_team": "Argentina",
    "away_team": "Saudi Arabia",
    "handicap_lines": [
        # open 口径主线是 -2.0（gap |1.90-1.92|=0.02），close 口径主线是 -1.75（gap |1.95-1.87|=0.08）
        {"line": -2.0, "home": {"open": 1.90, "close": 2.18}, "away": {"open": 1.92, "close": 1.70}},
        {"line": -1.75, "home": {"open": 1.85, "close": 1.95}, "away": {"open": 1.97, "close": 1.87}},
    ],
}
RAW_OU = {
    "match_date": "2022-11-22",
    "home_team": "Argentina",
    "away_team": "Saudi Arabia",
    "total_lines": [
        {"line": 2.5, "over": {"open": 1.72, "close": 1.66}, "under": {"open": 2.15, "close": 2.26}},
    ],
}


def test_normalize_1x2_extracts_open_and_close():
    row = normalize_1x2(RAW_1X2)
    assert row.date == "2022-11-22"
    assert row.home_canonical and row.away_canonical
    assert row.close_1x2 == {"home": 1.17, "draw": 7.0, "away": 19.0}
    assert row.open_1x2 == {"home": 1.20, "draw": 6.5, "away": 17.0}
    assert row.home_score == 1 and row.away_score == 2


def test_normalize_ah_picks_main_line_independently_for_open_and_close():
    row = normalize_ah(RAW_AH)
    # 主线 = 该时刻两侧赔率最接近（|home-away| 最小）的那条线；open/close 独立选取
    assert row.close_ah_line == -1.75
    assert row.close_ah == {"home": 1.95, "away": 1.87}
    assert row.open_ah_line == -2.0
    assert row.open_ah == {"home": 1.90, "away": 1.92}


def test_normalize_ou_takes_2_5_line():
    row = normalize_ou(RAW_OU)
    assert row.close_ou == {"over": 1.66, "under": 2.26}
    assert row.open_ou == {"over": 1.72, "under": 2.15}


def test_merge_markets_joins_three_markets_by_match_key():
    merged = merge_markets([normalize_1x2(RAW_1X2)], [normalize_ah(RAW_AH)], [normalize_ou(RAW_OU)])
    assert len(merged) == 1
    m = merged[0]
    assert m.close_1x2 and m.close_ah and m.close_ou
    assert m.close_ah_line == -1.75


def test_merge_markets_keeps_match_with_missing_market():
    merged = merge_markets([normalize_1x2(RAW_1X2)], [], [])
    assert len(merged) == 1
    assert merged[0].close_ah is None
```

- [ ] **Step 2: 运行确认失败**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: `No module named 'worldcup.oddsportal_wc2022'`。

- [ ] **Step 3: 实现 `worldcup/oddsportal_wc2022.py`（解析部分）**

```python
"""Normalize OddsHarvester (OddsPortal) WC2022 exports for backtest CSV.

一次性离线 backfill 工具：只读本地爬取产物，不联网。
输入字段名以 data/probe/oddsportal_wc2022_sample*.json 真实结构为准；
本模块输出契约（NormalizedMatch）固定，供 join/报告层使用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from worldcup.collectors.team_aliases import canonicalize_team


@dataclass
class NormalizedMatch:
    date: str  # YYYY-MM-DD (UTC date as shown by source)
    home_canonical: str
    away_canonical: str
    home_team: str = ""
    away_team: str = ""
    home_score: int | None = None
    away_score: int | None = None
    open_1x2: dict | None = None
    close_1x2: dict | None = None
    open_ou: dict | None = None
    close_ou: dict | None = None
    open_ah_line: float | None = None
    open_ah: dict | None = None
    close_ah_line: float | None = None
    close_ah: dict | None = None


def _key(date: str, home_c: str, away_c: str) -> tuple[str, str, str]:
    return (date, home_c, away_c)


def _base(raw: dict) -> NormalizedMatch:
    home = str(raw["home_team"]).strip()
    away = str(raw["away_team"]).strip()
    return NormalizedMatch(
        date=str(raw["match_date"])[:10],
        home_canonical=canonicalize_team(home),
        away_canonical=canonicalize_team(away),
        home_team=home,
        away_team=away,
        home_score=raw.get("home_score"),
        away_score=raw.get("away_score"),
    )


def normalize_1x2(raw: dict) -> NormalizedMatch:
    row = _base(raw)
    odds = raw["odds"]
    row.open_1x2 = {"home": float(odds["1"]["open"]), "draw": float(odds["X"]["open"]), "away": float(odds["2"]["open"])}
    row.close_1x2 = {"home": float(odds["1"]["close"]), "draw": float(odds["X"]["close"]), "away": float(odds["2"]["close"])}
    return row


def _closest_line(lines: list[dict], when: str) -> dict | None:
    """两侧赔率最接近的线 = 主盘口；when in {open, close}。"""
    best, best_gap = None, None
    for entry in lines:
        home, away = entry["home"].get(when), entry["away"].get(when)
        if home is None or away is None:
            continue
        gap = abs(float(home) - float(away))
        if best_gap is None or gap < best_gap:
            best, best_gap = entry, gap
    return best


def normalize_ah(raw: dict) -> NormalizedMatch:
    row = _base(raw)
    lines = raw["handicap_lines"]
    close_main = _closest_line(lines, "close")
    if close_main is not None:
        row.close_ah_line = float(close_main["line"])
        row.close_ah = {"home": float(close_main["home"]["close"]), "away": float(close_main["away"]["close"])}
    open_main = _closest_line(lines, "open")
    if open_main is not None:
        row.open_ah_line = float(open_main["line"])
        row.open_ah = {"home": float(open_main["home"]["open"]), "away": float(open_main["away"]["open"])}
    return row


def normalize_ou(raw: dict, target_line: float = 2.5) -> NormalizedMatch:
    row = _base(raw)
    for entry in raw["total_lines"]:
        if abs(float(entry["line"]) - target_line) < 1e-9:
            row.open_ou = {"over": float(entry["over"]["open"]), "under": float(entry["under"]["open"])}
            row.close_ou = {"over": float(entry["over"]["close"]), "under": float(entry["under"]["close"])}
            break
    return row


def merge_markets(
    rows_1x2: list[NormalizedMatch],
    rows_ah: list[NormalizedMatch],
    rows_ou: list[NormalizedMatch],
) -> list[NormalizedMatch]:
    """以 1x2 行为主表（含比分），并入 AH/OU 同场数据。"""
    by_key_ah = {_key(r.date, r.home_canonical, r.away_canonical): r for r in rows_ah}
    by_key_ou = {_key(r.date, r.home_canonical, r.away_canonical): r for r in rows_ou}
    out = []
    for row in rows_1x2:
        key = _key(row.date, row.home_canonical, row.away_canonical)
        ah = by_key_ah.get(key)
        if ah is not None:
            row.open_ah_line, row.open_ah = ah.open_ah_line, ah.open_ah
            row.close_ah_line, row.close_ah = ah.close_ah_line, ah.close_ah
        ou = by_key_ou.get(key)
        if ou is not None:
            row.open_ou, row.close_ou = ou.open_ou, ou.close_ou
        out.append(row)
    return out
```

注意：若真实样例的 open/close 表达方式不同（例如只有 `--odds-history` 序列而无显式 open/close 字段），约定：**open = 序列时间最早的点，close = 序列时间最晚的点**，在 normalize 内做这一步提取；测试 fixture 同步用序列形态表达，断言值不变思路（最早点/最晚点）。

- [ ] **Step 4: 运行确认通过**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add worldcup/oddsportal_wc2022.py tests/test_oddsportal_wc2022.py
git commit -m "feat: normalize oddsportal wc2022 exports"
```

---

### Task 3: join intl_history 并写出回测 CSV

**Files:**
- Modify: `worldcup/oddsportal_wc2022.py`（追加 join + CSV 写出 + CLI）
- Test: `tests/test_oddsportal_wc2022.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 `tests/test_oddsportal_wc2022.py`）**

```python
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.oddsportal_wc2022 import join_with_history, write_backtest_csv


def _intl_rows() -> list[dict]:
    return [
        {
            "match_id": "2022-11-22_argentina_saudi-arabia",
            "kickoff_at_utc": "2022-11-22T12:00:00Z",
            "home_team": "Argentina",
            "away_team": "Saudi Arabia",
            "home_score": "1",
            "away_score": "2",
            "home_elo_before": "2143.0",
            "away_elo_before": "1640.0",
            "neutral": "0",
        },
        {
            "match_id": "2022-11-23_germany_japan",
            "kickoff_at_utc": "2022-11-23T12:00:00Z",
            "home_team": "Germany",
            "away_team": "Japan",
            "home_score": "1",
            "away_score": "2",
            "home_elo_before": "1963.0",
            "away_elo_before": "1787.0",
            "neutral": "1",
        },
    ]


def _normalized_argentina() -> "NormalizedMatch":
    row = normalize_1x2(RAW_1X2)
    row.close_ah_line, row.close_ah = -1.75, {"home": 1.95, "away": 1.87}
    row.open_ah_line, row.open_ah = -1.75, {"home": 1.85, "away": 1.97}
    row.close_ou = {"over": 1.66, "under": 2.26}
    row.open_ou = {"over": 1.72, "under": 2.15}
    return row


def test_join_matches_by_date_and_canonical_names():
    joined, unmatched = join_with_history([_normalized_argentina()], _intl_rows())
    assert len(joined) == 1 and not unmatched
    rec = joined[0]
    assert rec["match_id"] == "2022-11-22_argentina_saudi-arabia"
    assert rec["home_elo_before"] == "2143.0"
    assert rec["odds_home"] == "1.17" and rec["ah_line"] == "-1.75"
    assert rec["open_ah_line"] == "-1.75" and rec["open_odds_home"] == "1.2"


def test_join_tolerates_one_day_offset():
    moved = _normalized_argentina()
    moved.date = "2022-11-21"  # 比 intl 早一天，应仍匹配
    joined, unmatched = join_with_history([moved], _intl_rows())
    assert len(joined) == 1 and not unmatched


def test_join_reports_unmatched():
    ghost = _normalized_argentina()
    ghost.away_canonical = "atlantis"
    joined, unmatched = join_with_history([ghost], _intl_rows())
    assert not joined
    assert unmatched and unmatched[0]["away_canonical"] == "atlantis"


def test_write_backtest_csv_is_loadable_by_backtest_module():
    from worldcup.backtest import load_matches

    joined, _ = join_with_history([_normalized_argentina()], _intl_rows())
    with TemporaryDirectory() as tmp:
        out = Path(tmp) / "wc2022_history.csv"
        write_backtest_csv(joined, out)
        matches = load_matches(out)
    assert len(matches) == 1
    m = matches[0]
    assert m.odds_1x2 == {"home": 1.17, "draw": 7.0, "away": 19.0}
    assert m.ah_line == -1.75 and m.odds_ah == {"home": 1.95, "away": 1.87}
    assert m.odds_ou == {"over": 1.66, "under": 2.26}
```

- [ ] **Step 2: 运行确认失败**

Expected: `cannot import name 'join_with_history'`。

- [ ] **Step 3: 实现（追加到 `worldcup/oddsportal_wc2022.py`）**

```python
from datetime import date as _date


CSV_COLUMNS = (
    "match_id", "kickoff_at_utc", "home_team", "away_team",
    "home_score", "away_score", "home_elo_before", "away_elo_before", "neutral",
    "odds_home", "odds_draw", "odds_away",
    "odds_over", "odds_under",
    "ah_line", "odds_ah_home", "odds_ah_away",
    "open_odds_home", "open_odds_draw", "open_odds_away",
    "open_odds_over", "open_odds_under",
    "open_ah_line", "open_odds_ah_home", "open_odds_ah_away",
)


def _date_near(a: str, b: str) -> bool:
    da, db = _date.fromisoformat(a), _date.fromisoformat(b)
    return abs((da - db).days) <= 1


def _fmt(value) -> str:
    return "" if value is None else str(value)


def join_with_history(
    normalized: list[NormalizedMatch], intl_rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    """以爬取场次为主表，按 (canonical home, canonical away, 日期±1) 配 intl 行。"""
    index: dict[tuple[str, str], list[dict]] = {}
    for row in intl_rows:
        key = (canonicalize_team(row["home_team"]), canonicalize_team(row["away_team"]))
        index.setdefault(key, []).append(row)

    joined, unmatched = [], []
    for match in normalized:
        candidates = index.get((match.home_canonical, match.away_canonical), [])
        hit = next(
            (r for r in candidates if _date_near(r["kickoff_at_utc"][:10], match.date)),
            None,
        )
        if hit is None:
            unmatched.append(
                {"date": match.date, "home_canonical": match.home_canonical,
                 "away_canonical": match.away_canonical, "home_team": match.home_team,
                 "away_team": match.away_team}
            )
            continue
        rec = {k: hit.get(k, "") for k in CSV_COLUMNS[:9]}
        c1, o1 = match.close_1x2 or {}, match.open_1x2 or {}
        cou, oou = match.close_ou or {}, match.open_ou or {}
        cah, oah = match.close_ah or {}, match.open_ah or {}
        rec.update({
            "odds_home": _fmt(c1.get("home")), "odds_draw": _fmt(c1.get("draw")), "odds_away": _fmt(c1.get("away")),
            "odds_over": _fmt(cou.get("over")), "odds_under": _fmt(cou.get("under")),
            "ah_line": _fmt(match.close_ah_line), "odds_ah_home": _fmt(cah.get("home")), "odds_ah_away": _fmt(cah.get("away")),
            "open_odds_home": _fmt(o1.get("home")), "open_odds_draw": _fmt(o1.get("draw")), "open_odds_away": _fmt(o1.get("away")),
            "open_odds_over": _fmt(oou.get("over")), "open_odds_under": _fmt(oou.get("under")),
            "open_ah_line": _fmt(match.open_ah_line), "open_odds_ah_home": _fmt(oah.get("home")), "open_odds_ah_away": _fmt(oah.get("away")),
        })
        joined.append(rec)
    return joined, unmatched


def write_backtest_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
```

文件顶部补 `import csv`（与既有 import 合并整理）。

- [ ] **Step 4: 实现 CLI main（追加）**

```python
def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Join scraped WC2022 odds with intl history into backtest CSV")
    parser.add_argument("--raw-1x2", required=True, help="OddsHarvester 1x2 JSON export")
    parser.add_argument("--raw-ah", required=True, help="OddsHarvester asian_handicap JSON export")
    parser.add_argument("--raw-ou", required=True, help="OddsHarvester over_under JSON export")
    parser.add_argument("--history", default="data/local/backtest/intl_history.csv")
    parser.add_argument("--out", default="data/local/backtest/wc2022_history.csv")
    args = parser.parse_args(argv)

    def _load(path: str) -> list[dict]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("matches", [])

    rows_1x2 = [normalize_1x2(item) for item in _load(args.raw_1x2)]
    rows_ah = [normalize_ah(item) for item in _load(args.raw_ah)]
    rows_ou = [normalize_ou(item) for item in _load(args.raw_ou)]
    merged = merge_markets(rows_1x2, rows_ah, rows_ou)

    with open(args.history, newline="", encoding="utf-8") as fh:
        intl_rows = list(csv.DictReader(fh))
    joined, unmatched = join_with_history(merged, intl_rows)
    write_backtest_csv(joined, args.out)
    print(json.dumps({"scraped": len(merged), "joined": len(joined),
                      "unmatched": unmatched}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

（若真实导出 JSON 的顶层结构不是 list/`matches`，按真实结构调整 `_load`，其余不动。）

- [ ] **Step 5: 运行确认通过；Step 6: Commit**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git add worldcup/oddsportal_wc2022.py tests/test_oddsportal_wc2022.py
git commit -m "feat: join wc2022 scraped odds into backtest csv"
```

---

### Task 4: 全量爬取（2026-06-12 修订版：漏斗式，先 1x2/OU 后 AH）

> **修订背景（Task 1 探测结论，推翻原假设）：**
> ① OddsHarvester 没有裸 `asian_handicap` market——AH 必须按盘口值逐 market 抓（如 `asian_handicap_-0_5`），单场单盘口带 `--odds-history` 约 29 秒，"每场全部 AH 线"全量枚举需数小时且反爬风险高，**不可行**；
> ② World Cup results 页首次采集只得 50 个 match links（页面分页），需翻页参数；
> ③ 因此放弃"一次全量三市场"，改为漏斗：先抓便宜且已验证可行的 1x2 + OU 2.5 全量 → 跑 1x2 赔率移动维度的粗检报告（Task 5 模块已支持双维度）→ **只有粗检显示苗头**才进入 Task 5.5 的 AH 限定抓取。

**Files:**
- Create: `data/local/backtest/oddsportal_wc2022_raw/`（爬取产物，不进 git）
- Create: `data/local/backtest/wc2022_history.csv`（join 产物，不进 git）
- Modify: `worldcup/oddsportal_wc2022.py`（`--raw-ah` / `--raw-ou` 改为可选参数）
- Test: `tests/test_oddsportal_wc2022.py`（追加）
- Modify（仅在需要时）: `worldcup/collectors/team_aliases.py`（补缺失队名 alias）

- [ ] **Step 0a: main 的 `--raw-ah` / `--raw-ou` 改可选（先写失败测试）**

追加到 `tests/test_oddsportal_wc2022.py`：

```python
def test_merge_markets_with_only_1x2_keeps_rows():
    # main 在缺 AH/OU 导出时仍应产出仅含 1x2 的行（联动 join 的 CSV 空列）
    merged = merge_markets([normalize_1x2(RAW_1X2)], [], [])
    joined, _ = join_with_history(merged, _intl_rows())
    assert len(joined) == 1
    assert joined[0]["odds_home"] == "1.17"
    assert joined[0]["ah_line"] == "" and joined[0]["odds_over"] == ""
```

实现：`main()` 里 `--raw-ah`、`--raw-ou` 改 `required=False, default=None`；为 None 时对应市场传空列表。跑测试确认绿后 commit：

```bash
git add worldcup/oddsportal_wc2022.py tests/test_oddsportal_wc2022.py
git commit -m "feat: make ah/ou exports optional in wc2022 join cli"
```

- [ ] **Step 0b: 解决 50/64 links（翻页）**

用工具的分页参数（`--max-pages 2` 或其实际等价物，见 `--help`）重采 results 链接，目标拿到 64 场；若页面确实只列出部分场次，对照公开的 64 场赛程清单记录缺失场次与原因，**样本 ≥ 60 可继续**，否则按止损点 B 处理。

- [ ] **Step 1: 全量抓 1x2 与 OU 2.5（带 `--odds-history`，分批可恢复）**

```bash
cd /Users/eagod/ai-dev/足彩/tools/OddsHarvester
RAW=/Users/eagod/ai-dev/足彩/data/local/backtest/oddsportal_wc2022_raw
mkdir -p "$RAW"
# 按工具实际形态：若 CLI 一次跑整届，则直接两条命令；若逐场，则 8 场/批、批间 sleep 30，
# 输出文件已存在的场次跳过（断点续抓），产物分别为：
#   "$RAW/wc2022_1x2.json"      （market: 1x2）
#   "$RAW/wc2022_ou.json"       （market: over_under_2_5，确切名以 --help 为准）
```

预算参考：~29 秒/场/市场 → 64 场 × 2 市场 ≈ 65 分钟 + sleep。
失败处理：单批失败重试最多 2 次（间隔 ≥ 60s）；同一批连续 3 次失败 → **止损点 B**（保留已有批次，已抓部分照常进入后续分析，样本量如实记录）。

- [ ] **Step 2: 跑 join（无 AH 列）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.oddsportal_wc2022 \
  --raw-1x2 data/local/backtest/oddsportal_wc2022_raw/wc2022_1x2.json \
  --raw-ou  data/local/backtest/oddsportal_wc2022_raw/wc2022_ou.json \
  --out data/local/backtest/wc2022_history.csv
```

Expected: stdout JSON 给出 `scraped` ≈ 64、`joined` 数、`unmatched` 列表；AH 列全空（Task 5.5 视粗检结果再补）。

- [ ] **Step 3: 处理 unmatched（若有）**

unmatched 通常是队名 alias 缺失（OddsPortal 命名 vs martj42 命名，如 `South Korea`/`Korea Republic`、`USA`/`United States`、`Ireland`/`Republic of Ireland` 一类）。处理方式：在 `worldcup/collectors/team_aliases.py` 的既有 alias 映射里补条目（先读该文件确认数据结构与命名风格，照既有条目格式加），**不要**在 oddsportal 模块里私设第二套映射。补完重跑 Step 2 直到 `unmatched` 为空或仅剩确属数据缺失的场次（记录原因）。

- [ ] **Step 4: 数据 sanity check**

```bash
python3 - <<'EOF'
import csv
rows = list(csv.DictReader(open("data/local/backtest/wc2022_history.csv")))
full_close = sum(1 for r in rows if r["odds_home"] and r["odds_over"])
full_open = sum(1 for r in rows if r["open_odds_home"] and r["open_odds_over"])
odds_moved = sum(
    1 for r in rows
    if r["odds_home"] and r["open_odds_home"]
    and abs(float(r["odds_home"]) - float(r["open_odds_home"])) / float(r["open_odds_home"]) >= 0.02
)
print({"rows": len(rows), "full_close": full_close, "full_open": full_open, "odds_home_moved_ge_2pct": odds_moved})
EOF
```

Expected: `rows` ≥ 60；`full_close`/`full_open` 接近 rows；`odds_home_moved_ge_2pct` > 0（2022 世界杯必有明显移价场次，若为 0 说明 open/close 提取有 bug，回 Task 2 排查）。

- [ ] **Step 5: 若改了 team_aliases.py，跑测试并 commit**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git add worldcup/collectors/team_aliases.py
git commit -m "feat: add team aliases for oddsportal wc2022 names"
```

（爬取与 CSV 产物在被忽略目录，无须也不得 commit。）

---

### Task 5: `worldcup/line_move_report.py` 线移动分桶报告

**Files:**
- Create: `worldcup/line_move_report.py`
- Test: `tests/test_line_move_report.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_line_move_report.py`：

```python
from worldcup.line_move_report import build_report, move_bucket, odds_move_bucket


def _row(open_line, close_line, **extra):
    base = {
        "match_id": "m1",
        "kickoff_at_utc": "2022-11-22T12:00:00Z",
        "home_team": "Argentina",
        "away_team": "Saudi Arabia",
        "home_score": "1",
        "away_score": "2",
        "home_elo_before": "2143.0",
        "away_elo_before": "1640.0",
        "neutral": "1",
        "odds_home": "1.17", "odds_draw": "7.0", "odds_away": "19.0",
        "odds_over": "1.66", "odds_under": "2.26",
        "ah_line": str(close_line), "odds_ah_home": "1.95", "odds_ah_away": "1.87",
        "open_odds_home": "1.20", "open_odds_draw": "6.5", "open_odds_away": "17.0",
        "open_odds_over": "1.72", "open_odds_under": "2.15",
        "open_ah_line": str(open_line), "open_odds_ah_home": "1.85", "open_odds_ah_away": "1.97",
    }
    base.update(extra)
    return base


def test_move_bucket_boundaries():
    assert move_bucket(0.0) == "0.00"
    assert move_bucket(0.25) == "0.25"
    assert move_bucket(0.5) == "0.50"
    assert move_bucket(0.75) == ">=0.75"
    assert move_bucket(1.5) == ">=0.75"


def test_odds_move_bucket_boundaries():
    assert odds_move_bucket(0.0) == "<2%"
    assert odds_move_bucket(0.02) == "2-5%"
    assert odds_move_bucket(0.05) == "5-10%"
    assert odds_move_bucket(0.10) == ">=10%"
    assert odds_move_bucket(0.30) == ">=10%"


def test_build_report_groups_by_1x2_odds_move():
    rows = [
        # 默认 open 1.20 → close 1.17：|Δ|/open = 2.5% → "2-5%" 桶
        _row(-1.75, -1.75, match_id="m_a"),
        # open=close=1.17：0% → "<2%" 桶
        _row(-1.75, -1.75, match_id="m_b", open_odds_home="1.17"),
    ]
    report = build_report(rows)
    assert report["sample"]["n_with_1x2_open_close"] == 2
    buckets = {b["bucket"]: b for b in report["by_1x2_move"]}
    assert buckets["2-5%"]["n_matches"] == 1
    assert buckets["<2%"]["n_matches"] == 1


def test_build_report_emits_1x2_move_even_without_ah_columns():
    rows = [_row("", "", match_id="m_no_ah", ah_line="", odds_ah_home="", odds_ah_away="",
                 open_ah_line="", open_odds_ah_home="", open_odds_ah_away="")]
    report = build_report(rows)
    assert report["sample"]["n_with_both_ah_lines"] == 0
    assert report["by_abs_move"] == []
    assert len(report["by_1x2_move"]) == 1


def test_build_report_groups_by_abs_line_move():
    rows = [
        _row(-1.75, -1.75, match_id="m_still"),
        _row(-2.25, -1.75, match_id="m_moved"),
    ]
    report = build_report(rows)
    assert report["sample"]["n_rows"] == 2
    assert report["sample"]["n_with_both_ah_lines"] == 2
    buckets = {b["bucket"]: b for b in report["by_abs_move"]}
    assert buckets["0.00"]["n_matches"] == 1
    assert buckets["0.50"]["n_matches"] == 1


def test_build_report_settles_ah_return_per_unit():
    # 收盘线 -1.75 @1.95，比分 1-2（净胜 -1）：主队侧全输 → -1.0；客队侧全赢 → +0.87
    rows = [_row(-1.75, -1.75)]
    report = build_report(rows)
    bucket = next(b for b in report["by_abs_move"] if b["bucket"] == "0.00")
    assert bucket["n_matches"] == 1
    # 模型按 Elo dr=+503 强烈看好主队 → home 侧 EV 为正成为信号 → 实际回报 -1.0
    assert bucket["ah"]["n_signals"] == 1
    assert abs(bucket["ah"]["mean_return"] - (-1.0)) < 1e-9


def test_build_report_skips_rows_without_open_line():
    rows = [_row(-1.75, -1.75, open_ah_line="")]
    report = build_report(rows)
    assert report["sample"]["n_with_both_ah_lines"] == 0
    assert report["by_abs_move"] == []
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.line_move_report'`。

- [ ] **Step 3: 实现 `worldcup/line_move_report.py`**

```python
"""Line-movement bucket report: does a moved AH line predict worse signal quality?

研究口径：单位回报（per 1 unit stake，与 backtest ah_ev_buckets 一致）、命中率、Brier。
不含资金字段，不构成投注建议。纯离线：只读本地 CSV 与 config。
"""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from worldcup.backtest import (
    BacktestMatch,
    ah_realized_return,
    brier_multiclass,
    load_matches,
    outcome_1x2,
    replay_match,
)
from worldcup.config import load_config
from worldcup.engine import handicap

BUCKETS = ("0.00", "0.25", "0.50", ">=0.75")
ODDS_BUCKETS_1X2 = ("<2%", "2-5%", "5-10%", ">=10%")


def move_bucket(abs_move: float) -> str:
    if abs_move < 0.125:
        return "0.00"
    if abs_move < 0.375:
        return "0.25"
    if abs_move < 0.625:
        return "0.50"
    return ">=0.75"


def odds_move_bucket(rel_move: float) -> str:
    """|close − open| / open（主胜赔率相对漂移）。"""
    if rel_move < 0.02:
        return "<2%"
    if rel_move < 0.05:
        return "2-5%"
    if rel_move < 0.10:
        return "5-10%"
    return ">=10%"


def _open_lines_by_id(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        value = (row.get("open_ah_line") or "").strip()
        if value:
            out[row["match_id"]] = float(value)
    return out


def _ah_signal(match: BacktestMatch, replayed: dict) -> dict | None:
    """模型视角 EV 更高且为正的一侧作为信号；返回 {side, ev, realized}。"""
    if match.ah_line is None or not match.odds_ah:
        return None
    dist = replayed["diff_dist"]
    inv = {-d: p for d, p in dist.items()}
    goal_diff = match.home_score - match.away_score
    home_ev = handicap.ev_handicap(dist, match.ah_line, match.odds_ah["home"])
    away_ev = handicap.ev_handicap(inv, -match.ah_line, match.odds_ah["away"])
    side, ev = ("home", home_ev) if home_ev >= away_ev else ("away", away_ev)
    if ev <= 0:
        return None
    if side == "home":
        realized = ah_realized_return(goal_diff, match.ah_line, match.odds_ah["home"])
    else:
        realized = ah_realized_return(-goal_diff, -match.ah_line, match.odds_ah["away"])
    return {"side": side, "ev": ev, "realized": realized}


def _1x2_signal(match: BacktestMatch, replayed: dict) -> dict | None:
    if not match.odds_1x2:
        return None
    model = replayed["model_1x2"]
    best_side, best_ev = None, 0.0
    for side in ("home", "draw", "away"):
        ev = model[side] * match.odds_1x2[side] - 1.0
        if ev > best_ev:
            best_side, best_ev = side, ev
    if best_side is None:
        return None
    actual = outcome_1x2(match.home_score, match.away_score)
    realized = match.odds_1x2[best_side] - 1.0 if best_side == actual else -1.0
    return {"side": best_side, "ev": best_ev, "hit": best_side == actual, "realized": realized}


def build_report(rows: list[dict], cfg: dict | None = None) -> dict:
    """rows: wc2022_history.csv 的 DictReader 行（含 open_ 列）。"""
    if cfg is None:
        cfg = load_config("config/settings.yaml")
    open_lines = _open_lines_by_id(rows)

    # rows 原样写入临时文件交给 load_matches：保证与 backtest 同一解析/校验路径
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = fh.name
    matches = load_matches(tmp_path)
    Path(tmp_path).unlink(missing_ok=True)

    open_1x2_by_id = {
        row["match_id"]: float(row["open_odds_home"])
        for row in rows
        if (row.get("open_odds_home") or "").strip()
    }
    grouped_ah: dict[str, list] = {b: [] for b in BUCKETS}
    grouped_1x2: dict[str, list] = {b: [] for b in ODDS_BUCKETS_1X2}
    n_with_both = 0
    n_with_1x2 = 0
    for match in matches:
        replayed = None
        open_line = open_lines.get(match.match_id)
        if open_line is not None and match.ah_line is not None:
            n_with_both += 1
            replayed = replay_match(match, cfg)
            grouped_ah[move_bucket(abs(match.ah_line - open_line))].append((match, replayed))
        open_home = open_1x2_by_id.get(match.match_id)
        if open_home is not None and match.odds_1x2:
            n_with_1x2 += 1
            if replayed is None:
                replayed = replay_match(match, cfg)
            rel = abs(match.odds_1x2["home"] - open_home) / open_home
            grouped_1x2[odds_move_bucket(rel)].append((match, replayed))

    return {
        "sample": {
            "n_rows": len(rows),
            "n_with_both_ah_lines": n_with_both,
            "n_with_1x2_open_close": n_with_1x2,
        },
        "by_abs_move": _bucket_table(grouped_ah, BUCKETS),
        "by_1x2_move": _bucket_table(grouped_1x2, ODDS_BUCKETS_1X2),
        "notes": [
            "research-only; unit returns per 1 stake; no staking advice",
            "model params are current config/settings.yaml values, not 2022-era",
            "open/close lines scraped from OddsPortal (one-off, personal research)",
        ],
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _bucket_table(grouped: dict[str, list], order: tuple[str, ...]) -> list[dict]:
    out = []
    for bucket in order:
        entries = grouped[bucket]
        if not entries:
            continue
        ah_signals = [s for s in (_ah_signal(m, r) for m, r in entries) if s]
        x2_signals = [s for s in (_1x2_signal(m, r) for m, r in entries) if s]
        briers = [
            brier_multiclass(r["model_1x2"], outcome_1x2(m.home_score, m.away_score))
            for m, r in entries
        ]
        out.append({
            "bucket": bucket,
            "n_matches": len(entries),
            "model_brier_1x2": _mean(briers),
            "ah": {
                "n_signals": len(ah_signals),
                "mean_ev": _mean([s["ev"] for s in ah_signals]),
                "mean_return": _mean([s["realized"] for s in ah_signals]),
            },
            "1x2": {
                "n_signals": len(x2_signals),
                "hit_rate": _mean([1.0 if s["hit"] else 0.0 for s in x2_signals]),
                "mean_return": _mean([s["realized"] for s in x2_signals]),
            },
        })
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AH line-movement bucket report")
    parser.add_argument("--csv", default="data/local/backtest/wc2022_history.csv")
    parser.add_argument("--out", default="data/local/backtest/line_move_report.json")
    args = parser.parse_args(argv)

    with open(args.csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    report = build_report(rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["sample"], ensure_ascii=False))
    for key in ("by_1x2_move", "by_abs_move"):
        print(f"-- {key} --")
        for bucket in report[key]:
            print(json.dumps(bucket, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: 全绿（若 `test_build_report_settles_ah_return_per_unit` 因 cfg 加载失败，确认测试在仓库根目录运行、`config/settings.yaml` 存在）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/line_move_report.py tests/test_line_move_report.py
git commit -m "feat: add line-movement bucket report (1x2 + AH dims)"
```

- [ ] **Step 6: 用真实数据跑粗检，执行决策检查点**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.line_move_report
```

看 `by_1x2_move`（此时 `by_abs_move` 为空，AH 未抓）。**决策规则：**
- **进入 Task 5.5 的条件（两条都满足）**：① 移动大的桶（`5-10%` 或 `>=10%`）相对 `<2%` 桶，`1x2.mean_return` / `1x2.hit_rate` / `model_brier_1x2` 至少两项方向性更差；② 大桶合计 `n_matches` ≥ 8。
- **否则跳过 Task 5.5**，直接 Task 6，研究文档结论记"1x2 移动维度未显示信号质量劣化（或样本不足），AH 细化不再投入，本案到此"。
- 检查点结果（各桶数字 + 走/不走 5.5 的判断）记入 `RECENT_WORK.md`。

---

### Task 5.5（条件执行）: AH 限定抓取与线移动维度补全

> 仅当 Task 5 Step 6 决策检查点通过才执行。目标：每场只抓"理论主线 ±0.25"共 3 条 AH 盘口（而非全部盘口），把 `by_abs_move`（让球线移动维度）补出来。预算 ≈ 64 场 × 3 线 × 29s ≈ 93 分钟 + sleep，分批可恢复。

**Files:**
- Create: `data/local/backtest/oddsportal_wc2022_raw/wc2022_ah.json`（或逐场文件后合并，不进 git）
- Modify: `data/local/backtest/wc2022_history.csv`（重跑 join 覆盖）

- [ ] **Step 1: 按开盘主胜赔率查表生成每场目标线清单**

```bash
cd /Users/eagod/ai-dev/足彩
python3 - <<'EOF'
import csv, json

def theory_line(open_home: float) -> float:
    table = [
        (1.30, -2.0), (1.45, -1.5), (1.65, -1.25), (1.85, -0.75),
        (2.10, -0.5), (2.50, -0.25), (3.20, 0.0), (4.50, 0.5),
    ]
    for ceiling, line in table:
        if open_home < ceiling:
            return line
    return 1.0

rows = list(csv.DictReader(open("data/local/backtest/wc2022_history.csv")))
plan = []
for r in rows:
    if not (r.get("open_odds_home") or "").strip():
        continue
    base = theory_line(float(r["open_odds_home"]))
    lines = sorted({base - 0.25, base, base + 0.25})
    plan.append({"match_id": r["match_id"], "home": r["home_team"], "away": r["away_team"], "lines": lines})
print(json.dumps(plan, ensure_ascii=False, indent=1))
EOF
```

把输出保存为 `data/local/backtest/oddsportal_wc2022_raw/ah_fetch_plan.json`（重定向即可）。AH 的 market key 命名（如 `asian_handicap_-0_5` / 整数线写法）以 OddsHarvester `--help` 与源码实际为准，按清单逐场逐线映射成命令。

- [ ] **Step 2: 分批抓取（8 场/批、批间 sleep 30、断点续抓）**

逐场逐线调用 historic 命令（带 `--odds-history`），每场产物落 `"$RAW/ah_by_match/<match_id>.json"`；**文件已存在则跳过**（断点续抓）。单批失败重试 ≤ 2 次（间隔 ≥ 60s），连续 3 批失败 → **止损点 B**（已抓部分照常进入分析）。
若某场 3 条线在 close 口径全部严重失衡（min |home−away| > 0.6），允许补抓相邻一条（每场至多 4 条）；仍失衡则该场 AH 记缺失，原因记录。

- [ ] **Step 3: 合并逐场产物为 `wc2022_ah.json`，重跑 join**

```bash
python3 - <<'EOF'
import json
from pathlib import Path
items = []
for path in sorted(Path("data/local/backtest/oddsportal_wc2022_raw/ah_by_match").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    items.extend(data if isinstance(data, list) else [data])
Path("data/local/backtest/oddsportal_wc2022_raw/wc2022_ah.json").write_text(
    json.dumps(items, ensure_ascii=False), encoding="utf-8")
print({"merged": len(items)})
EOF
python3 -m worldcup.oddsportal_wc2022 \
  --raw-1x2 data/local/backtest/oddsportal_wc2022_raw/wc2022_1x2.json \
  --raw-ah  data/local/backtest/oddsportal_wc2022_raw/wc2022_ah.json \
  --raw-ou  data/local/backtest/oddsportal_wc2022_raw/wc2022_ou.json \
  --out data/local/backtest/wc2022_history.csv
```

（合并脚本若与逐场产物的真实顶层结构不符，按真实结构调整聚合方式；`normalize_ah` 的输入形状若与 Task 2 fixture 不同，回 Task 2 修 fixture 与解析，跑测试保持绿。）

- [ ] **Step 4: AH sanity check 与最终报告**

```bash
python3 - <<'EOF'
import csv
rows = list(csv.DictReader(open("data/local/backtest/wc2022_history.csv")))
with_ah = sum(1 for r in rows if r["ah_line"] and r["open_ah_line"])
moved = sum(1 for r in rows if r["ah_line"] and r["open_ah_line"] and float(r["ah_line"]) != float(r["open_ah_line"]))
print({"rows": len(rows), "with_ah_both": with_ah, "ah_line_moved": moved})
EOF
python3 -m worldcup.line_move_report
```

Expected: `with_ah_both` ≥ 50；`ah_line_moved` > 0；`by_abs_move` 各桶有数据。

---

### Task 6: 真实数据出报告 + 研究文档 + 收尾

**Files:**
- Create: `docs/research/2026-06-12-wc2022-line-move.md`
- Modify: `README.md`、`RECENT_WORK.md`

- [ ] **Step 1: 跑线移动报告**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.line_move_report
```

Expected: stdout 打出 sample 与各桶统计；生成 `data/local/backtest/line_move_report.json`。

- [ ] **Step 2: 顺带跑一次既有回测器（收盘价口径全套指标）**

```bash
python3 -m worldcup.backtest --csv data/local/backtest/wc2022_history.csv \
  > data/local/backtest/wc2022_backtest_report.json
```

（`worldcup.backtest` CLI 参数以 `--help` 实际为准；它输出含 `calibration_1x2`、`ev_buckets_1x2`、`ah_ev_buckets` 的 JSON。）

- [ ] **Step 3: 写研究文档 `docs/research/2026-06-12-wc2022-line-move.md`**

结构（数值**必须**从两份报告 JSON 原样粘贴，不允许编造/美化）：

```markdown
# 2022 世界杯：让球线移动幅度与信号质量（一次性回测）

## 数据
- 来源：OddsPortal（OddsHarvester 一次性爬取，个人研究）；赛前 Elo：intl_history.csv（martj42 重放）
- 样本：<n> 场（join 成功 <n>，unmatched <n>，原因…）
- 口径：open = 开盘点，close = 收盘点；主线 = 两侧赔率最接近的让球线

## 结果（line_move_report.json 原样数值）
### 维度一：1x2 主胜赔率漂移（粗检主轴）
| 漂移桶 | n | 1x2 信号数 | 命中率 | 单位回报 | 模型 Brier |
（逐桶填 by_1x2_move）

### 维度二：让球线移动（仅 Task 5.5 执行过才有）
| |Δ线| 桶 | n | AH 信号数 | AH 单位回报 | 1x2 命中率 | 模型 Brier |
（逐桶填 by_abs_move；未执行 5.5 则写明跳过原因与检查点数字）

## 结论
- 假设"线异动大 → 信号质量差"是否被支持（看 mean_return / hit_rate / Brier 随桶单调性；n<10 的桶注明样本不足）
- 对本届 line movement guard 的建议（做/不做/阈值取多少）

## 限制
- 单届 64 场，样本小；参数为 2026 当前值；OddsPortal open 点定义随书商而异
```

- [ ] **Step 4: 文档同步**

- `README.md`：研究工具一节加两行——`worldcup/oddsportal_wc2022.py`（2022 世界杯爬取产物 join 回测 CSV，一次性 backfill）与 `worldcup/line_move_report.py`（让球线移动分桶报告）；注明数据产物在被忽略的 `data/local/backtest/`。
- `RECENT_WORK.md` 顶部按既有格式追加本次工作：背景、探测结论（OddsHarvester 是否支持世界杯、用的 league key）、样本数、报告核心数字、止损点是否触发、"未付费、未调用 The Odds API、未 push、未部署"。

- [ ] **Step 5: 最终全量验证与 commit（不 push）**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
git add docs/research/2026-06-12-wc2022-line-move.md README.md RECENT_WORK.md
git commit -m "docs: record wc2022 line-movement backtest findings"
```

Expected: 全绿；无空白错误。

---

## 范围外（明确不做）

- 不做本届（2026）snapshot/页面/调度的任何改动——line movement guard 的落地规则**另案**，等本报告结论出来由用户决策。
- 不接 The Odds API historical 付费端点，不充值。
- 不做全 AH 盘口枚举抓取（探测证实成本数小时级）；AH 仅按"理论主线 ±0.25"限定抓取，且仅在 1x2 粗检通过后执行（Task 5.5）。
- 不做逐书商走势分析；open/close 用 OddsPortal 聚合口径。
- 不把爬虫做成可复用采集层；OddsHarvester 用完即弃（tools/ 不进 git，不写进项目依赖）。

## 已知取舍（实现者与复核者知悉）

- OddsPortal 的 "opening odds" 是其平台口径的开盘点，不同书商挂盘时间不同，开盘线本身有噪声；分桶分析对此不敏感（桶宽 0.25），可接受。
- 主线选取规则（两侧赔率最接近）与本届 pipeline 的主线规则（书商覆盖数最多）口径不同；回测内部自洽即可，文档里注明。
- AH 主线在受限集合（理论主线 ±0.25，至多 4 条）内选取，是"全部盘口选平衡线"的近似；理论主线查表为经验映射，偶有偏离时该场以失衡/缺失处理，不强行外推。
- 1x2 主胜赔率漂移是"市场移动"的代理指标（与 AH 线移动同源但非同一现象）；粗检通过仅说明值得投入 AH 维度，最终结论以 AH 维度为准。
- 2022 与 2026 的模型参数/市场结构差异：报告只用于"方向性证据"（移线场次信号是否更差），不用于精确阈值标定；阈值最终用本届数据滚动验证。
- `intl_history.csv` 若未来重生成（Elo 重放参数变化），`wc2022_history.csv` 需重跑 join；两个产物都在被忽略目录，无 git 一致性问题。
