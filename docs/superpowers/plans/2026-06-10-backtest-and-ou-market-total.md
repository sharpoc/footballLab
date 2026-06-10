# 最小回测框架 + OU 市场锚定总进球 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `docs/superpowers/specs/2026-06-10-signal-quality-backtest-design.md` 第二阶段的最小离线回测框架，并修复 OU 模型对所有比赛输出恒定概率（P(over 2.5)=0.4816）的缺陷。

**Architecture:** 引擎层新增纯函数 `prob_total_over` / `implied_total_mu` / `blended_mu`，让每场比赛的总进球期望 `mu_total` 由「市场 OU 去水概率反推的总进球」与「配置先验 2.6」按权重混合得出；pipeline 在算 Poisson 矩阵前先聚合 OU 市场并计算逐场 `mu_total_used`。回测框架是独立模块 `worldcup/backtest.py`（不进线上 pipeline），读本地历史 CSV，重放 Elo+Poisson+ensemble 概率，输出 Brier / Log Loss / 校准分箱 / EV 分层 / 赔率分层 / 总进球诊断的 JSON 报告，与去水市场基线和 uniform 基线对比。

**Tech Stack:** 纯标准库 Python（math/csv/json/argparse/dataclasses），无新依赖；测试用 `tests/run_tests.py` 纯函数断言风格。

---

## 背景与约束

- 设计依据：`docs/superpowers/specs/2026-06-10-signal-quality-backtest-design.md` 第二阶段（输入契约、指标、模块命名 `worldcup/backtest.py` 均已在 spec 确认）。
- OU 缺陷已数值验证：`lambdas()` 中 `lh + la = mu_total = 2.6` 恒成立，两独立 Poisson 之和仍是 Poisson(2.6)，dr 从 0 到 600 时 `P(over 2.5)` 全部为 0.4816（仅极端 clamp 时 0.5063）。当前 OU 信号没有信息量。
- 引擎必须保持纯函数：不联网、不连数据库。`blended_mu` 的输入是已聚合好的市场概率，仍是纯函数。
- 回测只读本地文件，不联网。**真实历史数据（含赛前 Elo 和收盘赔率）的来源 spec 注明需单独确认，不在本计划范围内**；本计划交付框架 + 小型合成样例 CSV（仅用于测试和演示命令）。
- 报告只输出研究指标，不含任何下注金额、仓位、凯利建议。
- 验证命令（无 pytest 环境）：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

该 runner 一次跑全部测试；下文「运行测试」步骤均指此命令，预期输出关注对应 `PASS/FAIL` 行和末尾 `N/N tests passed`。

- 本项目允许本地 commit，不允许 push（见项目 `CLAUDE.md`）。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `worldcup/engine/poisson.py` | 修改 | 新增 `prob_total_over`、`implied_total_mu`、`blended_mu`；`lambdas` 增加 `mu_total` 覆盖参数 |
| `worldcup/pipeline.py` | 修改 | `analyze_match_input` 先聚合 OU 市场、计算逐场 `mu_total_used`；`MatchAnalysis` 增加 `mu_total_used` 字段 |
| `worldcup/local_runner.py` | 修改 | snapshot 的 `model` 块增加 `mu_total` 字段（纯增量） |
| `config/settings.yaml` | 修改 | `poisson` 块新增 `mu_market_weight: 0.7` |
| `worldcup/backtest.py` | 新建 | 回测框架：CSV 加载、结果判定、指标、重放、报告组装、CLI |
| `tests/engine/test_poisson.py` | 修改 | 新增引擎函数测试 |
| `tests/test_pipeline.py` | 修改 | 新增 OU 随市场变化 / 无市场回退测试 |
| `tests/test_backtest.py` | 新建 | 回测模块测试 |
| `tests/data/backtest_sample.csv` | 新建 | 合成样例历史数据（仅测试/演示用） |
| `README.md`、`RECENT_WORK.md` | 修改 | 文档同步 |

---

## Part A：引擎层（市场锚定总进球）

### Task 1: `prob_total_over` 与 `implied_total_mu`

**Files:**
- Modify: `worldcup/engine/poisson.py`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试**

在 `tests/engine/test_poisson.py` 末尾追加（文件顶部已有 `import math` 和 `CFG`）：

```python
def test_prob_total_over_matches_matrix():
    from worldcup.engine.poisson import prob_total_over

    matrix, _ = score_matrix(1.3, 1.3, CFG)
    p_matrix = prob_over(matrix, 2.5)
    assert math.isclose(prob_total_over(2.6, 2.5), p_matrix, abs_tol=1e-3)


def test_implied_total_mu_roundtrip():
    from worldcup.engine.poisson import implied_total_mu, prob_total_over

    for mu in (1.8, 2.6, 3.4):
        p = prob_total_over(mu, 2.5)
        assert math.isclose(implied_total_mu(p, 2.5), mu, abs_tol=1e-6)


def test_implied_total_mu_clamps_extreme_probs():
    from worldcup.engine.poisson import implied_total_mu

    assert 0.1 <= implied_total_mu(0.0, 2.5) <= 8.0
    assert 0.1 <= implied_total_mu(1.0, 2.5) <= 8.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 3 个新测试 `FAIL ... cannot import name 'prob_total_over'`，其余 PASS。

- [ ] **Step 3: 实现**

在 `worldcup/engine/poisson.py` 的 `lambdas` 之后追加：

```python
def prob_total_over(mu: float, line: float) -> float:
    """P(total > line) when total goals ~ Poisson(mu); line must be k + 0.5."""
    k = int(line)
    pk = exp(-mu)
    cdf = pk
    for i in range(1, k + 1):
        pk = pk * mu / i
        cdf += pk
    return 1.0 - cdf


def implied_total_mu(p_over: float, line: float, lo: float = 0.1, hi: float = 8.0) -> float:
    p = _clamp(p_over, prob_total_over(lo, line), prob_total_over(hi, line))
    for _ in range(80):
        mid = (lo + hi) / 2
        if prob_total_over(mid, line) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/engine/poisson.py tests/engine/test_poisson.py
git commit -m "feat: add poisson total-goals inversion helpers"
```

### Task 2: `blended_mu` 与 `lambdas` 的 `mu_total` 覆盖

**Files:**
- Modify: `worldcup/engine/poisson.py`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试**

在 `tests/engine/test_poisson.py` 末尾追加：

```python
def test_lambdas_mu_override():
    lh, la = lambdas(0, CFG, mu_total=3.0)
    assert math.isclose(lh + la, 3.0)


def test_blended_mu_without_market_falls_back_to_prior():
    from worldcup.engine.poisson import blended_mu

    cfg = dict(CFG)
    cfg["mu_market_weight"] = 0.7
    assert math.isclose(blended_mu(None, 2.5, cfg), CFG["mu_total"])


def test_blended_mu_weight_zero_keeps_prior():
    from worldcup.engine.poisson import blended_mu

    assert math.isclose(blended_mu(0.9, 2.5, dict(CFG)), CFG["mu_total"])


def test_blended_mu_full_weight_tracks_market():
    from worldcup.engine.poisson import blended_mu, prob_total_over

    cfg = dict(CFG)
    cfg["mu_market_weight"] = 1.0
    p_high = prob_total_over(3.2, 2.5)
    assert math.isclose(blended_mu(p_high, 2.5, cfg), 3.2, abs_tol=1e-6)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `test_lambdas_mu_override` 报 `TypeError`（多余参数），其余新测试报 import 失败。

- [ ] **Step 3: 实现**

修改 `worldcup/engine/poisson.py` 的 `lambdas`：

```python
def lambdas(dr: float, cfg: dict, mu_total: float | None = None) -> tuple[float, float]:
    total = cfg["mu_total"] if mu_total is None else mu_total
    gd = _clamp(dr / cfg["gd_div"], -cfg["gd_clamp"], cfg["gd_clamp"])
    half = total / 2
    lh = _clamp(half + gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    la = _clamp(half - gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    return lh, la
```

并在 `implied_total_mu` 之后追加：

```python
def blended_mu(p_over_market: float | None, line: float, cfg: dict) -> float:
    """Blend market-implied total goals with the config prior.

    `mu_market_weight` 缺省为 0，行为与历史版本完全一致（恒用 mu_total 先验）。
    """
    base = cfg["mu_total"]
    weight = cfg.get("mu_market_weight", 0.0)
    if p_over_market is None or weight <= 0:
        return base
    mu_market = implied_total_mu(p_over_market, line)
    return weight * mu_market + (1.0 - weight) * base
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/engine/poisson.py tests/engine/test_poisson.py
git commit -m "feat: support per-match total goals via market blend"
```

---

## Part B：pipeline 接线

### Task 3: `analyze_match_input` 使用逐场 `mu_total_used`

**Files:**
- Modify: `worldcup/pipeline.py`
- Modify: `config/settings.yaml`
- Modify: `worldcup/local_runner.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_pipeline.py` 顶部 import 区补充：

```python
import math
from datetime import datetime, timezone

from worldcup.collectors.models import EloRating, Fixture, ParsedOddsEvent
from worldcup.pipeline import MatchAnalysisInput
```

（`OddsQuote`、`MarketType`、`analyze_match_input`、`load_config` 已有 import。）

在文件末尾追加：

```python
def _ou_match_input(over_odds: float, under_odds: float, with_ou: bool = True) -> MatchAnalysisInput:
    kickoff = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
    quotes = [
        OddsQuote("book1", MarketType.X12, "home", 2.5),
        OddsQuote("book1", MarketType.X12, "draw", 3.2),
        OddsQuote("book1", MarketType.X12, "away", 2.9),
    ]
    if with_ou:
        for book in ("book1", "book2", "book3"):
            quotes.append(OddsQuote(book, MarketType.OU, "over", over_odds, line=2.5))
            quotes.append(OddsQuote(book, MarketType.OU, "under", under_odds, line=2.5))
    fixture = Fixture(
        source_match_no=1,
        kickoff_at_utc=kickoff,
        kickoff_time_raw="18:00",
        home_team_name="Team A",
        away_team_name="Team B",
        home_canonical="team_a",
        away_canonical="team_b",
    )
    event = ParsedOddsEvent(
        source_event_id="event-ou",
        sport_key="soccer_fifa_world_cup",
        kickoff_at_utc=kickoff,
        home_team_name="Team A",
        away_team_name="Team B",
        home_canonical="team_a",
        away_canonical="team_b",
        quotes=quotes,
    )
    return MatchAnalysisInput(
        fixture=fixture,
        odds_event=event,
        home_elo=EloRating("AA", 1, 1800),
        away_elo=EloRating("BB", 2, 1800),
        quotes=quotes,
    )


def test_ou_probability_varies_with_market_total():
    cfg = load_config()
    high_total = analyze_match_input(_ou_match_input(1.55, 2.45), cfg)
    low_total = analyze_match_input(_ou_match_input(2.45, 1.55), cfg)
    assert high_total.mu_total_used > low_total.mu_total_used
    assert high_total.ou_2_5["over"] > low_total.ou_2_5["over"]


def test_ou_falls_back_to_prior_without_market():
    cfg = load_config()
    analysis = analyze_match_input(_ou_match_input(0.0, 0.0, with_ou=False), cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 两个新测试 FAIL（`MatchAnalysis` 无 `mu_total_used` 属性）。

- [ ] **Step 3: 实现**

3a. `config/settings.yaml` 的 `poisson` 块末尾加一行：

```yaml
poisson:
  mu_total: 2.6
  gd_div: 250
  gd_clamp: 2.5
  lambda_min: 0.15
  lambda_max: 4.5
  max_goals: 10
  tail_mass_max: 0.01
  mu_market_weight: 0.7
```

3b. `worldcup/pipeline.py` 中 `MatchAnalysis` 增加字段（加在 `poisson_tail` 之后）：

```python
@dataclass(frozen=True)
class MatchAnalysis:
    match_input: MatchAnalysisInput
    lambdas: tuple[float, float]
    poisson_tail: float
    mu_total_used: float
    elo_1x2: dict[str, float]
    ...  # 其余字段不动
```

3c. `analyze_match_input` 改为先聚合 OU 市场，再算 lambdas（替换函数开头到 `matrix, tail = ...` 的部分，并在构造 `MatchAnalysis` 时传 `mu_total_used`、`market_ou_2_5` 复用前面算好的变量）：

```python
def analyze_match_input(match_input: MatchAnalysisInput, cfg: dict) -> MatchAnalysis:
    dr = _adjusted_dr(match_input, cfg)
    ou_line = cfg.get("ou_main_line", 2.5)
    ratio = cfg["odds"]["outlier_ratio"]
    market_ou_2_5 = odds.aggregate_market(
        match_input.quotes,
        market_type=MarketType.OU,
        line=ou_line,
        selections=["over", "under"],
        ratio=ratio,
    )
    mu_total_used = poisson.blended_mu(
        market_ou_2_5["market_probs"].get("over"),
        ou_line,
        cfg["poisson"],
    )
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_total_used)
    matrix, tail = poisson.score_matrix(lh, la, cfg["poisson"])
    # ... handicap_dist / poisson_1x2 / elo_1x2 / combined_1x2 / p_over 不变
    return MatchAnalysis(
        match_input=match_input,
        lambdas=(lh, la),
        poisson_tail=tail,
        mu_total_used=mu_total_used,
        elo_1x2=elo_1x2,
        poisson_1x2=poisson_1x2,
        combined_1x2=combined_1x2,
        ou_2_5={"over": p_over, "under": 1.0 - p_over},
        handicap_dist=handicap_dist,
        market_1x2=odds.aggregate_market(
            match_input.quotes,
            market_type=MarketType.X12,
            line=None,
            selections=["home", "draw", "away"],
            ratio=ratio,
        ),
        market_ou_2_5=market_ou_2_5,
    )
```

3d. `worldcup/local_runner.py` 的 `model` 块在 `"poisson_tail"` 行后加：

```python
            "poisson_tail": analysis.poisson_tail,
            "mu_total": analysis.mu_total_used,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（含原有 pipeline/local_runner/export/preview 测试不回退）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/pipeline.py worldcup/local_runner.py config/settings.yaml tests/test_pipeline.py
git commit -m "feat: anchor per-match total goals to OU market"
```

---

## Part C：回测框架 `worldcup/backtest.py`

### Task 4: 指标函数（Brier / Log Loss / 校准分箱）

**Files:**
- Create: `worldcup/backtest.py`
- Create: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_backtest.py`：

```python
import math

from worldcup.backtest import brier_multiclass, calibration_bins, log_loss


def test_brier_perfect_prediction_is_zero():
    assert brier_multiclass({"home": 1.0, "draw": 0.0, "away": 0.0}, "home") == 0.0


def test_brier_uniform_three_way():
    probs = {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    assert math.isclose(brier_multiclass(probs, "home"), 2 / 3, abs_tol=1e-9)


def test_log_loss_clamps_zero_probability():
    value = log_loss({"home": 0.0, "draw": 0.5, "away": 0.5}, "home")
    assert math.isfinite(value)
    assert value > 20


def test_calibration_bins_groups_and_rates():
    records = [(0.05, False), (0.05, True), (0.95, True), (0.95, True)]
    bins = calibration_bins(records, n_bins=10)
    assert len(bins) == 2
    low, high = bins
    assert low["n"] == 2 and math.isclose(low["hit_rate"], 0.5)
    assert high["n"] == 2 and math.isclose(high["hit_rate"], 1.0)
    assert math.isclose(high["p_mean"], 0.95)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `tests/test_backtest.py` 加载失败（`No module named 'worldcup.backtest'`）。

- [ ] **Step 3: 实现**

新建 `worldcup/backtest.py`：

```python
"""Offline backtest for the worldcup engine.

只读本地历史 CSV，不联网，不参与线上 pipeline。输出仅为研究指标，
不包含任何下注金额、仓位或执行建议。
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from math import log
from pathlib import Path

from worldcup.config import load_config
from worldcup.engine import elo, ensemble, handicap, poisson
from worldcup.engine.odds import devig

OUTCOMES = ("home", "draw", "away")
OU_LINE = 2.5


def brier_multiclass(probs: dict[str, float], outcome: str) -> float:
    return sum((p - (1.0 if k == outcome else 0.0)) ** 2 for k, p in probs.items())


def log_loss(probs: dict[str, float], outcome: str, eps: float = 1e-12) -> float:
    p = min(max(probs[outcome], eps), 1.0)
    return -log(p)


def calibration_bins(records: list[tuple[float, bool]], n_bins: int = 10) -> list[dict]:
    raw = [
        {"lo": i / n_bins, "hi": (i + 1) / n_bins, "n": 0, "p_sum": 0.0, "hits": 0}
        for i in range(n_bins)
    ]
    for p, hit in records:
        bucket = raw[min(int(p * n_bins), n_bins - 1)]
        bucket["n"] += 1
        bucket["p_sum"] += p
        bucket["hits"] += int(hit)
    return [
        {
            "range": [b["lo"], b["hi"]],
            "n": b["n"],
            "p_mean": b["p_sum"] / b["n"],
            "hit_rate": b["hits"] / b["n"],
        }
        for b in raw
        if b["n"]
    ]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add backtest probability metrics"
```

### Task 5: 结果判定与 AH 实盈helper

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_outcome_1x2():
    from worldcup.backtest import outcome_1x2

    assert outcome_1x2(2, 0) == "home"
    assert outcome_1x2(1, 1) == "draw"
    assert outcome_1x2(0, 3) == "away"


def test_ah_realized_return_win_push_quarter():
    from worldcup.backtest import ah_realized_return

    assert math.isclose(ah_realized_return(1, -0.5, 2.0), 1.0)
    assert math.isclose(ah_realized_return(0, 0.0, 1.9), 0.0)
    assert math.isclose(ah_realized_return(0, -0.25, 1.9), -0.5)
    assert math.isclose(ah_realized_return(-1, 0.5, 1.8), -1.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（import 失败）。

- [ ] **Step 3: 实现**

`worldcup/backtest.py` 追加（复用 `handicap.ev_handicap`，把实际净胜球当成单点分布即得到已实现回报）：

```python
def outcome_1x2(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return "home"
    if home_score < away_score:
        return "away"
    return "draw"


def ah_realized_return(goal_diff: int, line: float, odds: float) -> float:
    """Realized profit per 1 unit stake for the home side of an AH bet."""
    return handicap.ev_handicap({goal_diff: 1.0}, line, odds)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add backtest outcome settlement helpers"
```

### Task 6: CSV 加载器（含明确报错）

**Files:**
- Modify: `worldcup/backtest.py`
- Create: `tests/data/backtest_sample.csv`
- Test: `tests/test_backtest.py`

CSV 列契约（对应 spec 输入字段，赔率为收盘十进制赔率，缺市场留空）：

```text
match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,
home_elo_before,away_elo_before,neutral,
odds_home,odds_draw,odds_away,odds_over,odds_under,ah_line,odds_ah_home,odds_ah_away
```

- [ ] **Step 1: 创建合成样例数据**

新建 `tests/data/backtest_sample.csv`（合成数据，仅供测试/演示，不得用于正式结论）：

```csv
match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,home_elo_before,away_elo_before,neutral,odds_home,odds_draw,odds_away,odds_over,odds_under,ah_line,odds_ah_home,odds_ah_away
m1,2025-06-01T18:00:00Z,Alpha,Beta,2,0,1900,1700,1,1.60,3.90,6.00,1.85,1.95,-1.0,1.95,1.87
m2,2025-06-02T18:00:00Z,Gamma,Delta,1,1,1800,1795,1,2.60,3.10,2.85,2.10,1.74,0.0,1.90,1.92
m3,2025-06-03T18:00:00Z,Epsilon,Zeta,0,2,1750,1850,0,3.40,3.30,2.15,1.95,1.87,0.5,1.93,1.89
m4,2025-06-04T18:00:00Z,Eta,Theta,3,1,1880,1760,1,1.75,3.60,4.80,1.80,2.00,-0.75,1.88,1.94
m5,2025-06-05T18:00:00Z,Iota,Kappa,0,0,1820,1810,1,2.40,3.10,3.05,2.20,1.68,-0.25,1.96,1.86
m6,2025-06-06T18:00:00Z,Lambda,Mu,1,2,1700,1900,1,5.50,3.80,1.65,1.90,1.90,1.0,1.91,1.91
m7,2025-06-07T18:00:00Z,Nu,Xi,2,2,1840,1830,1,2.50,3.20,2.90,,,,,
```

- [ ] **Step 2: 写失败测试**

`tests/test_backtest.py` 顶部补充 import：

```python
from pathlib import Path

SAMPLE_CSV = Path(__file__).resolve().parent / "data" / "backtest_sample.csv"
```

追加测试：

```python
def test_load_matches_parses_sample():
    from worldcup.backtest import load_matches

    matches = load_matches(SAMPLE_CSV)
    assert len(matches) == 7
    first = matches[0]
    assert first.match_id == "m1"
    assert first.home_score == 2
    assert first.odds_1x2 == {"home": 1.60, "draw": 3.90, "away": 6.00}
    assert first.odds_ou == {"over": 1.85, "under": 1.95}
    assert first.ah_line == -1.0
    assert matches[2].neutral is False
    last = matches[-1]
    assert last.odds_ou is None and last.odds_ah is None and last.ah_line is None


def test_load_matches_missing_required_column_raises():
    import tempfile

    from worldcup.backtest import load_matches

    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write("match_id,home_team\nm1,Alpha\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "missing required columns" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_matches_missing_required_value_raises():
    import tempfile

    from worldcup.backtest import load_matches

    header = (
        "match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,"
        "home_elo_before,away_elo_before\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + "m1,2025-06-01T18:00:00Z,Alpha,Beta,2,,1900,1700\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "row 2" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 3: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 3 个新测试 FAIL（`load_matches` 不存在）。

- [ ] **Step 4: 实现**

`worldcup/backtest.py` 追加：

```python
REQUIRED_COLUMNS = (
    "match_id",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_elo_before",
    "away_elo_before",
)


@dataclass(frozen=True)
class BacktestMatch:
    match_id: str
    kickoff_at_utc: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    home_elo_before: float
    away_elo_before: float
    neutral: bool = True
    odds_1x2: dict[str, float] | None = None
    odds_ou: dict[str, float] | None = None
    ah_line: float | None = None
    odds_ah: dict[str, float] | None = None


def _opt_float(row: dict, key: str) -> float | None:
    value = (row.get(key) or "").strip()
    return float(value) if value else None


def _market_dict(row: dict, keys: dict[str, str]) -> dict[str, float] | None:
    values = {name: _opt_float(row, column) for name, column in keys.items()}
    if any(v is None for v in values.values()):
        return None
    return values


def load_matches(path: str | Path) -> list[BacktestMatch]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"missing required columns: {missing}")
        matches: list[BacktestMatch] = []
        for line_no, row in enumerate(reader, start=2):
            for column in REQUIRED_COLUMNS:
                if not (row.get(column) or "").strip():
                    raise ValueError(f"row {line_no}: missing value for {column}")
            ah_line = _opt_float(row, "ah_line")
            odds_ah = _market_dict(row, {"home": "odds_ah_home", "away": "odds_ah_away"})
            matches.append(
                BacktestMatch(
                    match_id=row["match_id"].strip(),
                    kickoff_at_utc=row["kickoff_at_utc"].strip(),
                    home_team=row["home_team"].strip(),
                    away_team=row["away_team"].strip(),
                    home_score=int(row["home_score"]),
                    away_score=int(row["away_score"]),
                    home_elo_before=float(row["home_elo_before"]),
                    away_elo_before=float(row["away_elo_before"]),
                    neutral=(row.get("neutral") or "1").strip() != "0",
                    odds_1x2=_market_dict(
                        row, {"home": "odds_home", "draw": "odds_draw", "away": "odds_away"}
                    ),
                    odds_ou=_market_dict(row, {"over": "odds_over", "under": "odds_under"}),
                    ah_line=ah_line if odds_ah is not None else None,
                    odds_ah=odds_ah if ah_line is not None else None,
                )
            )
    return matches
```

- [ ] **Step 5: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py tests/data/backtest_sample.csv
git commit -m "feat: add backtest csv loader with synthetic sample"
```

### Task 7: 单场重放（模型 + 市场概率）

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_replay_match_produces_model_and_market_probs():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    matches = load_matches(SAMPLE_CSV)
    result = replay_match(matches[0], cfg)
    assert math.isclose(sum(result["model_1x2"].values()), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(result["market_1x2"].values()), 1.0, abs_tol=1e-9)
    assert result["model_1x2"]["home"] > result["model_1x2"]["away"]
    assert 0.0 < result["model_ou"]["over"] < 1.0
    assert result["mu_used"] > 0


def test_replay_match_without_odds_keeps_model_only():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    last = load_matches(SAMPLE_CSV)[-1]
    result = replay_match(last, cfg)
    assert result["market_ou"] is None
    assert math.isclose(result["mu_used"], cfg["poisson"]["mu_total"], abs_tol=1e-9)


def test_replay_match_home_advantage_applied_when_not_neutral():
    from worldcup.backtest import load_matches, replay_match
    from worldcup.config import load_config

    cfg = load_config()
    matches = load_matches(SAMPLE_CSV)
    non_neutral = matches[2]
    assert non_neutral.neutral is False
    result = replay_match(non_neutral, cfg)
    base_dr = non_neutral.home_elo_before - non_neutral.away_elo_before
    assert math.isclose(result["dr"], base_dr + cfg["elo"]["home_adv"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`replay_match` 不存在）。

- [ ] **Step 3: 实现**

`worldcup/backtest.py` 追加：

```python
def replay_match(match: BacktestMatch, cfg: dict) -> dict:
    dr = match.home_elo_before - match.away_elo_before
    if not match.neutral:
        dr += cfg["elo"]["home_adv"]
    market_1x2 = devig(match.odds_1x2) if match.odds_1x2 else None
    market_ou = devig(match.odds_ou) if match.odds_ou else None
    mu_used = poisson.blended_mu(
        market_ou["over"] if market_ou else None, OU_LINE, cfg["poisson"]
    )
    lh, la = poisson.lambdas(dr, cfg["poisson"], mu_total=mu_used)
    matrix, _ = poisson.score_matrix(lh, la, cfg["poisson"])
    poisson_1x2 = poisson.probs_1x2(matrix)
    elo_1x2 = elo.win_draw_loss(
        match.home_elo_before,
        match.away_elo_before,
        neutral=match.neutral,
        cfg=cfg["elo"],
    )
    combined_1x2 = ensemble.combine_1x2(
        elo_1x2, poisson_1x2, cfg["ensemble"]["w_elo"], cfg["ensemble"]["w_poisson"]
    )
    p_over = poisson.prob_over(matrix, OU_LINE)
    return {
        "dr": dr,
        "mu_used": mu_used,
        "model_1x2": combined_1x2,
        "market_1x2": market_1x2,
        "model_ou": {"over": p_over, "under": 1.0 - p_over},
        "market_ou": market_ou,
        "diff_dist": handicap.diff_distribution(matrix),
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add backtest per-match probability replay"
```

### Task 8: 报告组装 `run_backtest`

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_run_backtest_report_structure_and_small_sample_flag():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    cfg = load_config()
    report = run_backtest(load_matches(SAMPLE_CSV), cfg, min_sample=200)
    sample = report["sample"]
    assert sample["n_matches"] == 7
    assert sample["n_1x2"] == 7
    assert sample["n_ou"] == 6
    assert sample["n_ah"] == 6
    assert sample["sample_too_small"] is True

    metrics = report["markets"]["1x2"]
    for source in ("model", "market", "uniform"):
        assert metrics[source]["n"] == 7
        assert 0.0 <= metrics[source]["brier"] <= 2.0
        assert metrics[source]["log_loss"] > 0.0
    assert report["markets"]["ou_2_5"]["model"]["n"] == 7
    assert report["markets"]["ou_2_5"]["market"]["n"] == 6

    assert report["calibration_1x2"]
    assert sum(b["n"] for b in report["ev_buckets_1x2"]) == 21
    assert sum(b["n"] for b in report["odds_buckets_1x2"]) == 21
    assert sum(b["n"] for b in report["ah_ev_buckets"]) == 6
    assert report["totals_by_abs_dr"]
    assert "no staking advice" in report["notes"]


def test_run_backtest_not_small_when_min_sample_met():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    report = run_backtest(load_matches(SAMPLE_CSV), load_config(), min_sample=5)
    assert report["sample"]["sample_too_small"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`run_backtest` 不存在）。

- [ ] **Step 3: 实现**

`worldcup/backtest.py` 追加：

```python
EV_BUCKETS = ((-9.0, 0.0), (0.0, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 9.0))
ODDS_BUCKETS = ((1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 1000.0))
DR_BUCKETS = ((0.0, 100.0), (100.0, 200.0), (200.0, 300.0), (300.0, 10000.0))


def _mean_metrics(rows: list[tuple[dict[str, float], str]]) -> dict:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None}
    return {
        "n": len(rows),
        "brier": sum(brier_multiclass(p, o) for p, o in rows) / len(rows),
        "log_loss": sum(log_loss(p, o) for p, o in rows) / len(rows),
    }


def _bucket_rows(buckets: tuple, value: float) -> int | None:
    for idx, (lo, hi) in enumerate(buckets):
        if lo <= value < hi:
            return idx
    return None


def run_backtest(matches: list[BacktestMatch], cfg: dict, min_sample: int = 200) -> dict:
    model_1x2_rows: list[tuple[dict, str]] = []
    market_1x2_rows: list[tuple[dict, str]] = []
    uniform_1x2_rows: list[tuple[dict, str]] = []
    model_ou_rows: list[tuple[dict, str]] = []
    market_ou_rows: list[tuple[dict, str]] = []
    uniform_ou_rows: list[tuple[dict, str]] = []
    calibration_records: list[tuple[float, bool]] = []
    ev_buckets = [{"n": 0, "return_sum": 0.0} for _ in EV_BUCKETS]
    odds_buckets = [{"n": 0, "hits": 0, "implied_sum": 0.0} for _ in ODDS_BUCKETS]
    ah_buckets = [{"n": 0, "return_sum": 0.0} for _ in EV_BUCKETS]
    dr_buckets = [{"n": 0, "total_sum": 0, "mu_sum": 0.0} for _ in DR_BUCKETS]
    n_1x2 = n_ou = n_ah = 0

    uniform_1x2 = {k: 1 / 3 for k in OUTCOMES}
    uniform_ou = {"over": 0.5, "under": 0.5}

    for match in matches:
        replay = replay_match(match, cfg)
        result = outcome_1x2(match.home_score, match.away_score)
        total_goals = match.home_score + match.away_score
        ou_result = "over" if total_goals > OU_LINE else "under"

        model_1x2_rows.append((replay["model_1x2"], result))
        uniform_1x2_rows.append((uniform_1x2, result))
        model_ou_rows.append((replay["model_ou"], ou_result))
        uniform_ou_rows.append((uniform_ou, ou_result))
        for selection in OUTCOMES:
            calibration_records.append(
                (replay["model_1x2"][selection], selection == result)
            )

        if match.odds_1x2:
            n_1x2 += 1
            market_1x2_rows.append((replay["market_1x2"], result))
            for selection in OUTCOMES:
                odds_value = match.odds_1x2[selection]
                ev_value = replay["model_1x2"][selection] * odds_value - 1.0
                realized = (odds_value - 1.0) if selection == result else -1.0
                ev_idx = _bucket_rows(EV_BUCKETS, ev_value)
                if ev_idx is not None:
                    ev_buckets[ev_idx]["n"] += 1
                    ev_buckets[ev_idx]["return_sum"] += realized
                odds_idx = _bucket_rows(ODDS_BUCKETS, odds_value)
                if odds_idx is not None:
                    odds_buckets[odds_idx]["n"] += 1
                    odds_buckets[odds_idx]["hits"] += int(selection == result)
                    odds_buckets[odds_idx]["implied_sum"] += 1.0 / odds_value

        if match.odds_ou:
            n_ou += 1
            market_ou_rows.append((replay["market_ou"], ou_result))

        if match.odds_ah and match.ah_line is not None:
            n_ah += 1
            predicted = handicap.ev_handicap(
                replay["diff_dist"], match.ah_line, match.odds_ah["home"]
            )
            realized = ah_realized_return(
                match.home_score - match.away_score, match.ah_line, match.odds_ah["home"]
            )
            idx = _bucket_rows(EV_BUCKETS, predicted)
            if idx is not None:
                ah_buckets[idx]["n"] += 1
                ah_buckets[idx]["return_sum"] += realized

        dr_idx = _bucket_rows(DR_BUCKETS, abs(replay["dr"]))
        if dr_idx is not None:
            dr_buckets[dr_idx]["n"] += 1
            dr_buckets[dr_idx]["total_sum"] += total_goals
            dr_buckets[dr_idx]["mu_sum"] += replay["mu_used"]

    return {
        "sample": {
            "n_matches": len(matches),
            "n_1x2": n_1x2,
            "n_ou": n_ou,
            "n_ah": n_ah,
            "min_sample": min_sample,
            "sample_too_small": len(matches) < min_sample,
        },
        "markets": {
            "1x2": {
                "model": _mean_metrics(model_1x2_rows),
                "market": _mean_metrics(market_1x2_rows),
                "uniform": _mean_metrics(uniform_1x2_rows),
            },
            "ou_2_5": {
                "model": _mean_metrics(model_ou_rows),
                "market": _mean_metrics(market_ou_rows),
                "uniform": _mean_metrics(uniform_ou_rows),
            },
        },
        "calibration_1x2": calibration_bins(calibration_records),
        "ev_buckets_1x2": [
            {
                "range": list(EV_BUCKETS[i]),
                "n": b["n"],
                "mean_return": (b["return_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(ev_buckets)
        ],
        "odds_buckets_1x2": [
            {
                "range": list(ODDS_BUCKETS[i]),
                "n": b["n"],
                "hit_rate": (b["hits"] / b["n"]) if b["n"] else None,
                "implied_mean": (b["implied_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(odds_buckets)
        ],
        "ah_ev_buckets": [
            {
                "range": list(EV_BUCKETS[i]),
                "n": b["n"],
                "mean_return": (b["return_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(ah_buckets)
        ],
        "totals_by_abs_dr": [
            {
                "range": list(DR_BUCKETS[i]),
                "n": b["n"],
                "mean_total_goals": (b["total_sum"] / b["n"]) if b["n"] else None,
                "mean_mu_used": (b["mu_sum"] / b["n"]) if b["n"] else None,
            }
            for i, b in enumerate(dr_buckets)
        ],
        "notes": "research metrics only; no staking advice",
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add backtest report assembly with baselines"
```

### Task 9: CLI 入口

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_cli_writes_report_json():
    import json
    import tempfile

    from worldcup.backtest import main

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.json"
        code = main(["--csv", str(SAMPLE_CSV), "--out", str(out_path), "--min-sample", "5"])
        assert code == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert report["sample"]["n_matches"] == 7
        assert report["sample"]["sample_too_small"] is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`main` 不存在）。

- [ ] **Step 3: 实现**

`worldcup/backtest.py` 末尾追加：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline backtest for the worldcup engine")
    parser.add_argument("--csv", required=True, help="historical matches csv path")
    parser.add_argument("--config", default=None, help="settings.yaml path override")
    parser.add_argument("--out", default="data/local/backtest/report.json")
    parser.add_argument("--min-sample", type=int, default=200)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    report = run_backtest(load_matches(args.csv), cfg, min_sample=args.min_sample)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["sample"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过，并手动跑一次 CLI**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv tests/data/backtest_sample.csv --min-sample 5`
Expected: 打印 sample 摘要 JSON；`data/local/backtest/report.json` 生成（该目录已被 gitignore）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: add backtest cli entrypoint"
```

---

## Part D：文档同步

### Task 10: README 与 RECENT_WORK

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: README 增加「离线回测」小节**

在 README 验证/命令相关章节附近追加：

```markdown
## 离线回测

回测框架只读本地历史 CSV，不联网，输出研究指标（Brier / Log Loss / 校准分箱 / EV 与赔率分层 / 总进球诊断），不含任何资金建议。

```bash
python3 -m worldcup.backtest --csv data/local/backtest/history.csv --min-sample 200
```

- CSV 列契约见 `tests/data/backtest_sample.csv`（合成样例，仅演示格式，不得用于正式结论）。
- 真实历史数据（赛前 Elo、收盘赔率）来源需单独确认后再接入。
- 报告默认写入被忽略的 `data/local/backtest/report.json`。
- 样本量低于 `--min-sample` 时报告带 `sample_too_small: true`，不能据此下强结论。

另外：OU 大小球模型的逐场 `mu_total` 现在由「OU 市场去水概率反推的总进球」与配置先验 `poisson.mu_total` 按 `poisson.mu_market_weight` 混合得出；无 OU 市场时回退先验。snapshot 的 `model.mu_total` 字段记录实际使用值。
```

- [ ] **Step 2: 覆盖更新 RECENT_WORK.md**

按既有格式覆盖写入：更新时间、完成事项（OU 市场锚定 + 回测框架）、涉及文件、验证结果（`tests/run_tests.py` 全绿 + CLI 演示命令）、下次继续事项（确认真实历史数据来源；用真实数据跑回测后再调 `mu_market_weight`、ensemble 权重和 EV 阈值）、注意风险（OU 信号在市场锚定后会显著趋于保守，属预期行为）。

- [ ] **Step 3: 全量验证**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record backtest framework and ou market anchoring"
```

---

## 风险与说明

1. **OU 信号会变保守（预期行为）**：`mu_market_weight: 0.7` 把模型总进球拉向市场，OU 的模型概率与市场概率差距缩小，S/A 级 OU 信号将明显减少。这正是修复目的——当前 OU 信号是纯噪声。把权重设为 0 可完全回到旧行为。
2. **1X2 与 AH 同步受益**：比分矩阵的 `mu` 更真实后，`poisson_1x2` 与 `handicap_dist` 也随之改善；`model_disagreement` 守卫逻辑不变。
3. **快照字段为纯增量**：`model.mu_total` 是新增字段，不改动现有字段和 ingest 契约；线上发布流程不变。
4. **回测结论暂不可用**：在真实历史数据来源确认前，只有框架和合成样例；任何参数调整必须等真实数据回测结果。
5. **本计划不改**：scheduler、ingest、HMAC、SQLite/Postgres schema、预览页路由。
