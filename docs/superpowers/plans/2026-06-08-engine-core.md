# 世界杯分析引擎核心 实现计划（Plan 1：Engine Core）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **项目规则提醒（强约束）**：本计划涉及 `git init` 与 `git commit` 的步骤，**均需用户明确同意后才能执行**。执行者在第一次 `git init`/`git commit` 前必须暂停征求用户确认；未经验证的代码不得提交。若用户暂不初始化 git，跳过所有 commit 步骤、改为口头汇报阶段完成即可。

**Goal:** 实现一个纯 Python、零外部依赖（不联网、不连云）的世界杯分析引擎：输入 Elo / 赔率报价 → 输出 1X2 / 大小球 / 亚洲让球的模型概率、去水市场概率、EV/Edge/等级/状态，以及两轮之间的变化事件；全程 pytest 单测。

**Architecture:** 纯函数 + dataclass 数据模型，常数集中在 `config/settings.yaml`。Poisson 用 `scipy.stats`。每个引擎子模块单一职责、可独立测试。下游（采集/云端）只消费本引擎的纯函数与数据结构。

**Tech Stack:** Python 3.11、numpy、scipy、PyYAML、pytest。

---

## 文件结构（先锁定边界）

```
足彩/
├── pyproject.toml                # 包与 pytest 配置（最小）
├── config/settings.yaml          # 所有常数：λ、平局、阈值、权重、差异检测阈值
├── worldcup/
│   ├── __init__.py
│   ├── config.py                 # 读取 settings.yaml → dict
│   ├── models.py                 # dataclass：OddsQuote / MarketProbs / Signal 等 + 枚举
│   └── engine/
│       ├── __init__.py
│       ├── odds.py               # 隐含概率 / 去水 / 按 line 聚合 / 异常值过滤
│       ├── elo.py                # 期望胜率 / 平局公式 / 1X2
│       ├── poisson.py            # λ / 比分矩阵(0~10) / 归一化 / 尾部质量 / 1X2·OU
│       ├── handicap.py           # 让球结算表 EV（整数/半盘/四分之一盘）
│       ├── ensemble.py           # 1X2 = Elo 与 Poisson 加权
│       └── value.py              # EV / Edge / 等级 / 状态 / 降级硬规则
│   └── differ.py                 # 两轮对比 → 变化事件
└── tests/
    ├── test_config.py
    ├── engine/
    │   ├── test_odds.py
    │   ├── test_elo.py
    │   ├── test_poisson.py
    │   ├── test_handicap.py
    │   ├── test_ensemble.py
    │   └── test_value.py
    └── test_differ.py
```

仅本计划范围内的文件；采集/云端文件在后续计划创建。

---

## Task 1：项目骨架 + 配置加载 + 数据模型

**Files:**
- Create: `pyproject.toml`
- Create: `config/settings.yaml`
- Create: `worldcup/__init__.py`（空）
- Create: `worldcup/config.py`
- Create: `worldcup/models.py`
- Create: `worldcup/engine/__init__.py`（空）
- Test: `tests/test_config.py`

- [ ] **Step 1: 写 `pyproject.toml`**

```toml
[project]
name = "worldcup"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["numpy", "scipy", "PyYAML"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"

[tool.setuptools.packages.find]
include = ["worldcup*"]
```

- [ ] **Step 2: 写 `config/settings.yaml`（所有常数集中于此）**

```yaml
poisson:
  mu_total: 2.6          # 大赛平均总进球
  gd_div: 250            # Elo 差 → 净胜球除数
  gd_clamp: 2.5          # 净胜球绝对值上限
  lambda_min: 0.15
  lambda_max: 4.5
  max_goals: 10          # 比分矩阵 0..max_goals
  tail_mass_max: 0.01    # 截断尾部质量阈值（1%）

elo:
  home_adv: 100          # 非中立场主队 Elo 加成
  base_draw: 0.28
  draw_k: 0.0003
  draw_min: 0.18
  draw_max: 0.32

ensemble:
  w_elo: 0.5
  w_poisson: 0.5

odds:
  min_books: 3           # 有效报价公司下限，少于则不出 S/A
  outlier_ratio: 2.0     # 与中位数偏离超过该倍数/分之一视为离群

value:
  # 1X2 / OU 用 EV+Edge 双阈值；AH 只用 EV
  s_ev: 0.08
  s_edge: 0.04
  a_ev: 0.05
  a_edge: 0.02
  b_ev: 0.03
  b_edge: 0.01
  odds_max_age_seconds: 12600   # 3.5 小时

differ:
  ev_change: 0.03        # EV 变化阈值 ±3%
  odds_move: 0.05        # 赔率异动阈值 5%

ou_main_line: 2.5
```

- [ ] **Step 3: 写失败测试 `tests/test_config.py`**

```python
from worldcup.config import load_config


def test_load_config_reads_known_keys():
    cfg = load_config()
    assert cfg["poisson"]["max_goals"] == 10
    assert cfg["elo"]["home_adv"] == 100
    assert cfg["odds"]["min_books"] == 3
```

- [ ] **Step 4: 运行测试确认失败**

Run: `pytest tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.config`）

- [ ] **Step 5: 写 `worldcup/config.py`**

```python
from functools import lru_cache
from pathlib import Path
import yaml

_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict:
    p = Path(path) if path else _DEFAULT
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
```

- [ ] **Step 6: 写 `worldcup/models.py`（数据模型 + 枚举）**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MarketType(str, Enum):
    X12 = "1X2_90min"
    OU = "OverUnder_90min"
    AH = "AsianHandicap_90min"


class Grade(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    NO_MARKET_YET = "NO_MARKET_YET"
    ODDS_PENDING = "ODDS_PENDING"


@dataclass(frozen=True)
class OddsQuote:
    bookmaker: str
    market_type: MarketType
    selection: str            # 例: "home"/"draw"/"away"/"over"/"under"/"home_-0.5"
    odds: float
    line: float | None = None # OU/AH 的盘口线；1X2 为 None
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.fetched_at is not None and self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")


@dataclass(frozen=True)
class MarketProbs:
    """某市场的概率分布：selection -> 概率，概率和=1。"""
    market_type: MarketType
    probs: dict[str, float]
    line: float | None = None


@dataclass(frozen=True)
class Signal:
    market_type: MarketType
    selection: str
    grade: Grade
    ev: float | None
    edge: float | None
    status: str               # "OK" / "NO_MARKET_YET" / "ODDS_PENDING" / "D"
    reasons: list[str] = field(default_factory=list)
    line: float | None = None
```

- [ ] **Step 7: 运行测试确认通过**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 8: 提交（需用户同意 git）**

```bash
git add pyproject.toml config/settings.yaml worldcup/ tests/test_config.py
git commit -m "feat(engine): project skeleton, config loader, data models"
```

---

## Task 2：赔率去水与聚合（odds.py）

**Files:**
- Create: `worldcup/engine/odds.py`
- Test: `tests/engine/test_odds.py`

- [ ] **Step 1: 写失败测试 `tests/engine/test_odds.py`**

```python
import math
import pytest
from worldcup.models import OddsQuote, MarketType
from worldcup.engine.odds import (
    implied_prob,
    devig,
    aggregate,
    aggregate_market,
    filter_outliers,
)


def test_implied_prob():
    assert implied_prob(2.0) == 0.5
    assert math.isclose(implied_prob(1.8), 1 / 1.8)


def test_devig_sums_to_one():
    raw = {"home": 1.8, "draw": 3.6, "away": 4.8}
    p = devig(raw)
    assert math.isclose(sum(p.values()), 1.0, abs_tol=1e-9)
    # 去水后仍保持相对大小：home 最大
    assert p["home"] > p["draw"] > p["away"]


def test_filter_outliers_drops_extreme():
    # 中位数约 2.0，10.0 为离群
    assert filter_outliers([1.9, 2.0, 2.1, 10.0], ratio=2.0) == [1.9, 2.0, 2.1]


def test_aggregate_same_line_average():
    quotes = [
        OddsQuote("bk1", MarketType.OU, "over", 1.9, line=2.5),
        OddsQuote("bk2", MarketType.OU, "over", 2.1, line=2.5),
        OddsQuote("bk3", MarketType.OU, "over", 2.0, line=2.25),  # 不同线，应被排除
    ]
    agg = aggregate(quotes, MarketType.OU, "over", line=2.5)
    assert agg["n_books"] == 2
    assert math.isclose(agg["odds"], 2.0)


def test_aggregate_market_devigs_all_selections_same_line():
    quotes = [
        OddsQuote("bk1", MarketType.OU, "over", 1.9, line=2.5),
        OddsQuote("bk2", MarketType.OU, "over", 2.1, line=2.5),
        OddsQuote("bk1", MarketType.OU, "under", 1.8, line=2.5),
        OddsQuote("bk2", MarketType.OU, "under", 2.0, line=2.5),
        OddsQuote("bk1", MarketType.OU, "over", 1.7, line=2.25),  # 不同线排除
    ]
    out = aggregate_market(quotes, MarketType.OU, line=2.5, selections=["over", "under"])
    assert out["n_books_by_selection"] == {"over": 2, "under": 2}
    assert set(out["odds"]) == {"over", "under"}
    assert math.isclose(sum(out["market_probs"].values()), 1.0, abs_tol=1e-9)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_odds.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.odds`）

- [ ] **Step 3: 写 `worldcup/engine/odds.py`**

```python
from __future__ import annotations
from statistics import median
from worldcup.models import OddsQuote, MarketType


def implied_prob(odds: float) -> float:
    return 1.0 / odds


def devig(raw_odds: dict[str, float]) -> dict[str, float]:
    """欧赔 dict → 去水归一化市场概率，和为 1。"""
    raw = {k: implied_prob(v) for k, v in raw_odds.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def filter_outliers(odds_list: list[float], ratio: float) -> list[float]:
    """剔除明显离群赔率：与中位数比值超过 ratio 或低于 1/ratio。"""
    if not odds_list:
        return []
    m = median(odds_list)
    return [o for o in odds_list if (1.0 / ratio) <= (o / m) <= ratio]


def aggregate(
    quotes: list[OddsQuote],
    market_type: MarketType,
    selection: str,
    line: float | None = None,
    ratio: float = 2.0,
) -> dict:
    """按 market_type + line + selection 聚合，仅同一线求平均（离群剔除后）。"""
    matched = [
        q.odds
        for q in quotes
        if q.market_type == market_type and q.selection == selection and q.line == line
    ]
    cleaned = filter_outliers(matched, ratio)
    n = len(cleaned)
    return {
        "odds": (sum(cleaned) / n) if n else None,
        "n_books": n,
    }


def aggregate_market(
    quotes: list[OddsQuote],
    market_type: MarketType,
    line: float | None,
    selections: list[str],
    ratio: float = 2.0,
) -> dict:
    """聚合同一 market+line 下所有 selection，并返回去水市场概率。"""
    odds: dict[str, float] = {}
    n_books: dict[str, int] = {}
    for selection in selections:
        agg = aggregate(quotes, market_type, selection, line=line, ratio=ratio)
        n_books[selection] = agg["n_books"]
        if agg["odds"] is not None:
            odds[selection] = agg["odds"]
    if set(odds) != set(selections):
        return {"odds": odds, "market_probs": {}, "n_books_by_selection": n_books}
    return {
        "odds": odds,
        "market_probs": devig(odds),
        "n_books_by_selection": n_books,
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_odds.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/odds.py tests/engine/test_odds.py
git commit -m "feat(engine): odds implied prob, de-vig, aggregation by line, outlier filter"
```

---

## Task 3：Elo 模型 → 1X2（elo.py）

**Files:**
- Create: `worldcup/engine/elo.py`
- Test: `tests/engine/test_elo.py`

- [ ] **Step 1: 写失败测试 `tests/engine/test_elo.py`**

```python
import math
from worldcup.engine.elo import expected_score, win_draw_loss

CFG = {"home_adv": 100, "base_draw": 0.28, "draw_k": 0.0003,
       "draw_min": 0.18, "draw_max": 0.32}


def test_expected_score_equal_is_half():
    assert math.isclose(expected_score(0), 0.5)


def test_expected_score_monotonic():
    assert expected_score(200) > expected_score(0) > expected_score(-200)


def test_win_draw_loss_sums_to_one():
    p = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    assert math.isclose(p["home"] + p["draw"] + p["away"], 1.0, abs_tol=1e-9)


def test_equal_neutral_home_equals_away():
    p = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    assert math.isclose(p["home"], p["away"], abs_tol=1e-9)


def test_draw_prob_clamped():
    # 巨大实力差 → 平局概率压到下限
    p = win_draw_loss(2400, 1400, neutral=True, cfg=CFG)
    assert math.isclose(p["draw"], CFG["draw_min"], abs_tol=1e-9)
    assert p["home"] > p["away"]


def test_home_advantage_applied_when_not_neutral():
    eq_neutral = win_draw_loss(1800, 1800, neutral=True, cfg=CFG)
    with_home = win_draw_loss(1800, 1800, neutral=False, cfg=CFG)
    assert with_home["home"] > eq_neutral["home"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_elo.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.elo`）

- [ ] **Step 3: 写 `worldcup/engine/elo.py`**

```python
from __future__ import annotations


def expected_score(dr: float) -> float:
    """Elo 期望胜率（含平局折算前的二元期望）。dr = 主队Elo − 客队Elo。"""
    return 1.0 / (10 ** (-dr / 400) + 1)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def win_draw_loss(elo_home: float, elo_away: float, neutral: bool, cfg: dict) -> dict:
    dr = elo_home - elo_away
    if not neutral:
        dr += cfg["home_adv"]
    we = expected_score(dr)
    p_draw = _clamp(
        cfg["base_draw"] - cfg["draw_k"] * abs(dr),
        cfg["draw_min"],
        cfg["draw_max"],
    )
    p_home = (1 - p_draw) * we
    p_away = (1 - p_draw) * (1 - we)
    return {"home": p_home, "draw": p_draw, "away": p_away}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_elo.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/elo.py tests/engine/test_elo.py
git commit -m "feat(engine): Elo expected score + draw formula -> 1X2"
```

---

## Task 4：Poisson 比分矩阵 → 1X2/OU（poisson.py）

**Files:**
- Create: `worldcup/engine/poisson.py`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试 `tests/engine/test_poisson.py`**

```python
import math
from worldcup.engine.poisson import lambdas, score_matrix, probs_1x2, prob_over

CFG = {"mu_total": 2.6, "gd_div": 250, "gd_clamp": 2.5,
       "lambda_min": 0.15, "lambda_max": 4.5, "max_goals": 10,
       "tail_mass_max": 0.01}


def test_lambdas_equal_strength_split_mu():
    lh, la = lambdas(0, CFG)
    assert math.isclose(lh, 1.3) and math.isclose(la, 1.3)


def test_lambdas_clamped():
    lh, la = lambdas(100000, CFG)  # 极端 dr
    assert lh == CFG["lambda_max"] or la == CFG["lambda_min"]


def test_matrix_normalized_sums_to_one():
    matrix, tail = score_matrix(1.3, 1.3, CFG)
    total = sum(sum(row) for row in matrix)
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert tail < CFG["tail_mass_max"]


def test_1x2_sums_to_one_and_symmetric():
    matrix, _ = score_matrix(1.3, 1.3, CFG)
    p = probs_1x2(matrix)
    assert math.isclose(p["home"] + p["draw"] + p["away"], 1.0, abs_tol=1e-9)
    assert math.isclose(p["home"], p["away"], abs_tol=1e-9)


def test_over_matches_manual():
    # λ=1.3,1.3 下 P(总进球<=2) 用独立泊松卷积手算，over = 1 - under
    matrix, _ = score_matrix(1.3, 1.3, CFG)
    over = prob_over(matrix, 2.5)
    assert 0.0 < over < 1.0
    # under(<=2) + over(>2) = 1
    under = 1 - over
    assert math.isclose(over + under, 1.0, abs_tol=1e-9)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_poisson.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.poisson`）

- [ ] **Step 3: 写 `worldcup/engine/poisson.py`**

```python
from __future__ import annotations
from scipy.stats import poisson


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lambdas(dr: float, cfg: dict) -> tuple[float, float]:
    gd = _clamp(dr / cfg["gd_div"], -cfg["gd_clamp"], cfg["gd_clamp"])
    half = cfg["mu_total"] / 2
    lh = _clamp(half + gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    la = _clamp(half - gd / 2, cfg["lambda_min"], cfg["lambda_max"])
    return lh, la


def score_matrix(lh: float, la: float, cfg: dict) -> tuple[list[list[float]], float]:
    """返回 (归一化矩阵, 截断尾部质量)。matrix[i][j] = P(主队 i 球, 客队 j 球)。"""
    n = cfg["max_goals"]
    ph = [poisson.pmf(i, lh) for i in range(n + 1)]
    pa = [poisson.pmf(j, la) for j in range(n + 1)]
    raw = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    total = sum(sum(row) for row in raw)
    tail = 1.0 - total
    matrix = [[raw[i][j] / total for j in range(n + 1)] for i in range(n + 1)]
    return matrix, tail


def probs_1x2(matrix: list[list[float]]) -> dict:
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return {"home": home, "draw": draw, "away": away}


def prob_over(matrix: list[list[float]], line: float) -> float:
    """总进球 > line 的概率（line 取 x.5，无 push）。"""
    over = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i + j > line:
                over += p
    return over
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_poisson.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/poisson.py tests/engine/test_poisson.py
git commit -m "feat(engine): Poisson lambdas, normalized score matrix, 1X2 & over/under"
```

---

## Task 5：亚洲让球结算表 EV（handicap.py）

**Files:**
- Create: `worldcup/engine/handicap.py`
- Test: `tests/engine/test_handicap.py`

- [ ] **Step 1: 写失败测试 `tests/engine/test_handicap.py`**

```python
import math
from worldcup.engine.handicap import diff_distribution, ev_handicap


def simple_matrix():
    # 构造确定性分布：50% 净胜球 +1，50% 净胜球 0（用 2x2 矩阵）
    # matrix[i][j]: (1,0)=0.5 -> diff +1; (0,0)=0.5 -> diff 0
    return [[0.5, 0.0], [0.5, 0.0]]


def test_diff_distribution():
    d = diff_distribution(simple_matrix())
    assert math.isclose(d[1], 0.5) and math.isclose(d[0], 0.5)


def test_integer_line_push():
    # 主队 0 盘，odds=2.0：diff +1 赢(净+1.0)，diff 0 push(净0)
    # EV = 0.5*(2-1) + 0.5*0 = 0.5
    d = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(d, line=0.0, odds=2.0), 0.5)


def test_half_line_no_push():
    # 主队 -0.5，odds=2.0：diff +1 赢，diff 0 输
    # EV = 0.5*(1) + 0.5*(-1) = 0.0
    d = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(d, line=-0.5, odds=2.0), 0.0)


def test_quarter_line_minus_025():
    # 主队 -0.25 = 半注 0 盘 + 半注 -0.5 盘，odds=2.0
    # 半注0盘: 0.5*(0.5*1 + 0.5*0)=... 用 ev_handicap 复用整数/半盘
    # 整数0盘 EV=0.5；半盘-0.5 EV=0.0；平均=0.25
    d = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(d, line=-0.25, odds=2.0), 0.25)


def test_quarter_line_minus_075():
    # -0.75 = 半注 -0.5 + 半注 -1.0
    # -0.5 EV=0.0；-1.0 盘: diff+1 push(净0), diff0 输(-1) → 0.5*0+0.5*(-1)=-0.5
    # 平均 = (0.0 + -0.5)/2 = -0.25
    d = diff_distribution(simple_matrix())
    assert math.isclose(ev_handicap(d, line=-0.75, odds=2.0), -0.25)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_handicap.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.handicap`）

- [ ] **Step 3: 写 `worldcup/engine/handicap.py`**

```python
from __future__ import annotations


def diff_distribution(matrix: list[list[float]]) -> dict[int, float]:
    """从比分矩阵得到主队净胜球分布：diff(i-j) -> 概率。"""
    dist: dict[int, float] = {}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            d = i - j
            dist[d] = dist.get(d, 0.0) + p
    return dist


def _ev_integer(dist: dict[int, float], line: float, odds: float) -> float:
    """整数盘（line 为整数）：adjusted = diff + line；>0 赢, ==0 push, <0 输。"""
    ev = 0.0
    for diff, p in dist.items():
        adj = diff + line
        if adj > 0:
            ev += p * (odds - 1)
        elif adj == 0:
            ev += 0.0          # push 退本，净收益 0
        else:
            ev += p * (-1)
    return ev


def _ev_half(dist: dict[int, float], line: float, odds: float) -> float:
    """半盘（line 为 x.5）：adjusted 永不为 0；>0 赢，<0 输。"""
    ev = 0.0
    for diff, p in dist.items():
        adj = diff + line
        if adj > 0:
            ev += p * (odds - 1)
        else:
            ev += p * (-1)
    return ev


def _line_kind(line: float) -> str:
    x2 = round(line * 2)
    if abs(line * 2 - x2) > 1e-9:
        return "quarter"          # 如 -0.25 / -0.75
    return "integer" if x2 % 2 == 0 else "half"


def ev_handicap(dist: dict[int, float], line: float, odds: float) -> float:
    """让球 EV：自动区分整数 / 半盘 / 四分之一盘。line 为施加在主队的让球（-1=主-1）。"""
    kind = _line_kind(line)
    if kind == "integer":
        return _ev_integer(dist, line, odds)
    if kind == "half":
        return _ev_half(dist, line, odds)
    # quarter：拆成相邻的整数盘与半盘各半注（间隔 0.5），平均
    lo, hi = line - 0.25, line + 0.25
    return 0.5 * ev_handicap(dist, lo, odds) + 0.5 * ev_handicap(dist, hi, odds)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_handicap.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/handicap.py tests/engine/test_handicap.py
git commit -m "feat(engine): Asian handicap settlement EV (integer/half/quarter, push)"
```

---

## Task 6：集成 1X2（ensemble.py）

**Files:**
- Create: `worldcup/engine/ensemble.py`
- Test: `tests/engine/test_ensemble.py`

- [ ] **Step 1: 写失败测试 `tests/engine/test_ensemble.py`**

```python
import math
from worldcup.engine.ensemble import combine_1x2


def test_combine_weighted_average_normalized():
    elo = {"home": 0.6, "draw": 0.25, "away": 0.15}
    poi = {"home": 0.4, "draw": 0.35, "away": 0.25}
    out = combine_1x2(elo, poi, w_elo=0.5, w_poisson=0.5)
    assert math.isclose(sum(out.values()), 1.0, abs_tol=1e-9)
    # home 应在两者之间
    assert 0.4 <= out["home"] <= 0.6


def test_combine_all_weight_elo():
    elo = {"home": 0.6, "draw": 0.25, "away": 0.15}
    poi = {"home": 0.1, "draw": 0.1, "away": 0.8}
    out = combine_1x2(elo, poi, w_elo=1.0, w_poisson=0.0)
    assert math.isclose(out["home"], 0.6, abs_tol=1e-9)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_ensemble.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.ensemble`）

- [ ] **Step 3: 写 `worldcup/engine/ensemble.py`**

```python
from __future__ import annotations


def combine_1x2(elo: dict, poisson: dict, w_elo: float, w_poisson: float) -> dict:
    keys = ("home", "draw", "away")
    mixed = {k: w_elo * elo[k] + w_poisson * poisson[k] for k in keys}
    total = sum(mixed.values())
    return {k: v / total for k, v in mixed.items()}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_ensemble.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/ensemble.py tests/engine/test_ensemble.py
git commit -m "feat(engine): ensemble weighted 1X2"
```

---

## Task 7：EV / Edge / 等级 / 状态 / 降级（value.py）

**Files:**
- Create: `worldcup/engine/value.py`
- Test: `tests/engine/test_value.py`

定义评级上下文与函数签名：

```python
# grade_signal(market_type, selection, p_model, p_market, odds, ctx, cfg) -> Signal
# ctx 字段：
#   status: "OK"|"NO_MARKET_YET"|"ODDS_PENDING"|"D"  （上游赔率状态）
#   odds_age_seconds: float|None
#   n_books: int
#   depends_on_backup: bool   # 该报价是否依赖未确认的高频备源
#   line_changed_unknown: bool
```

- [ ] **Step 1: 写失败测试 `tests/engine/test_value.py`**

```python
import math
import pytest
from worldcup.models import MarketType, Grade
from worldcup.engine.value import ev, edge, grade_signal

CFG = {"s_ev": 0.08, "s_edge": 0.04, "a_ev": 0.05, "a_edge": 0.02,
       "b_ev": 0.03, "b_edge": 0.01, "odds_max_age_seconds": 12600,
       "min_books": 3}


def ok_ctx(**kw):
    base = {"status": "OK", "odds_age_seconds": 60, "n_books": 5,
            "depends_on_backup": False, "line_changed_unknown": False}
    base.update(kw)
    return base


def test_ev_and_edge():
    assert math.isclose(ev(0.6, 1.75), 0.05)
    assert math.isclose(edge(0.55, 0.45), 0.10)


def test_1x2_grade_s_requires_ev_and_edge():
    # p_model=0.60, market=0.45 -> edge 0.15; odds=1.85 -> EV=0.11 -> S
    s = grade_signal(MarketType.X12, "home", 0.60, 0.45, 1.85, ok_ctx(), CFG)
    assert s.grade == Grade.S


def test_1x2_high_ev_but_low_edge_not_s():
    # 高 EV 但 edge 不足（market 接近 model）→ 不到 S
    s = grade_signal(MarketType.X12, "home", 0.50, 0.495, 2.3, ok_ctx(), CFG)
    assert s.grade in (Grade.B, Grade.C)


def test_ah_uses_ev_only():
    # AH：传 p_market=None，按 EV 定级
    s = grade_signal(MarketType.AH, "home_-0.5", 0.6, None, 1.85, ok_ctx(), CFG,
                     ah_ev=0.09)
    assert s.grade == Grade.S
    assert s.edge is None


def test_ah_requires_precomputed_settlement_ev():
    with pytest.raises(ValueError, match="AH requires ah_ev"):
        grade_signal(MarketType.AH, "home_-0.5", 0.6, None, 1.85, ok_ctx(), CFG)


def test_stale_odds_caps_at_b():
    s = grade_signal(MarketType.X12, "home", 0.60, 0.45, 1.85,
                     ok_ctx(odds_age_seconds=99999), CFG)
    assert s.grade == Grade.B


def test_few_books_caps_at_b():
    s = grade_signal(MarketType.X12, "home", 0.60, 0.45, 1.85,
                     ok_ctx(n_books=1), CFG)
    assert s.grade == Grade.B


def test_no_market_yet_status():
    s = grade_signal(MarketType.X12, "home", 0.60, None, None,
                     ok_ctx(status="NO_MARKET_YET"), CFG)
    assert s.grade == Grade.NO_MARKET_YET
    assert s.status == "NO_MARKET_YET"


def test_odds_pending_status():
    s = grade_signal(MarketType.X12, "home", 0.60, None, None,
                     ok_ctx(status="ODDS_PENDING"), CFG)
    assert s.grade == Grade.ODDS_PENDING
    assert s.status == "ODDS_PENDING"


def test_d_status():
    s = grade_signal(MarketType.X12, "home", 0.60, None, None,
                     ok_ctx(status="D"), CFG)
    assert s.grade == Grade.D
    assert s.status == "D"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/engine/test_value.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.engine.value`）

- [ ] **Step 3: 写 `worldcup/engine/value.py`**

```python
from __future__ import annotations
from worldcup.models import MarketType, Grade, Signal


def ev(p_model: float, odds: float) -> float:
    return p_model * odds - 1


def edge(p_model: float, p_market: float) -> float:
    return p_model - p_market


def _base_grade_1x2_ou(ev_val: float, edge_val: float, cfg: dict) -> Grade:
    if ev_val >= cfg["s_ev"] and edge_val >= cfg["s_edge"]:
        return Grade.S
    if ev_val >= cfg["a_ev"] and edge_val >= cfg["a_edge"]:
        return Grade.A
    if ev_val >= cfg["b_ev"] and edge_val >= cfg["b_edge"]:
        return Grade.B
    return Grade.C


def _base_grade_ah(ev_val: float, cfg: dict) -> Grade:
    if ev_val >= cfg["s_ev"]:
        return Grade.S
    if ev_val >= cfg["a_ev"]:
        return Grade.A
    if ev_val >= cfg["b_ev"]:
        return Grade.B
    return Grade.C


def _cap_at_b(grade: Grade) -> Grade:
    return Grade.B if grade in (Grade.S, Grade.A) else grade


def grade_signal(
    market_type: MarketType,
    selection: str,
    p_model: float,
    p_market: float | None,
    odds: float | None,
    ctx: dict,
    cfg: dict,
    ah_ev: float | None = None,
    line: float | None = None,
) -> Signal:
    # 1) 非 OK 状态直接返回对应状态信号
    status = ctx.get("status", "OK")
    if status in ("NO_MARKET_YET", "ODDS_PENDING", "D"):
        g = {
            "NO_MARKET_YET": Grade.NO_MARKET_YET,
            "ODDS_PENDING": Grade.ODDS_PENDING,
            "D": Grade.D,
        }[status]
        return Signal(market_type, selection, g, None, None, status,
                      [status], line)

    # 2) 计算 EV/Edge 与基础等级
    reasons: list[str] = []
    if market_type == MarketType.AH:
        if ah_ev is None:
            raise ValueError("AH requires ah_ev from settlement table")
        ev_val = ah_ev
        edge_val = None
        base = _base_grade_ah(ev_val, cfg)
    else:
        ev_val = ev(p_model, odds)
        edge_val = edge(p_model, p_market)
        base = _base_grade_1x2_ou(ev_val, edge_val, cfg)

    # 3) 降级硬规则（禁止 S/A → 最高 B）
    age = ctx.get("odds_age_seconds")
    if age is not None and age > cfg["odds_max_age_seconds"]:
        base = _cap_at_b(base); reasons.append("stale_odds")
    if ctx.get("n_books", 0) < cfg["min_books"]:
        base = _cap_at_b(base); reasons.append("few_books")
    if ctx.get("depends_on_backup"):
        base = _cap_at_b(base); reasons.append("unconfirmed_backup")
    if ctx.get("line_changed_unknown"):
        base = _cap_at_b(base); reasons.append("line_changed_unknown")

    return Signal(market_type, selection, base, ev_val, edge_val, "OK",
                  reasons, line)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/engine/test_value.py -v`
Expected: PASS

- [ ] **Step 5: 提交（需用户同意 git）**

```bash
git add worldcup/engine/value.py tests/engine/test_value.py
git commit -m "feat(engine): EV/Edge/grade with statuses and downgrade rules"
```

---

## Task 8：变化检测（differ.py）

**Files:**
- Create: `worldcup/differ.py`
- Test: `tests/test_differ.py`

变化事件结构（普通 dict，便于序列化）：
```python
# {"type": "grade_change"|"ev_change"|"odds_move"|"match_added"|"match_removed",
#  "match_id": str, "market": str, "selection": str|None,
#  "from": ..., "to": ..., }
```
两轮输入结构（每场每选项一条记录）：
```python
# round = {
#   match_id: {
#     "selections": {
#        "1X2_90min|home": {"grade": "A", "ev": 0.06, "odds": 1.9},
#        ...
#     }
#   }, ...
# }
```

- [ ] **Step 1: 写失败测试 `tests/test_differ.py`**

```python
from worldcup.differ import diff_rounds

CFG = {"ev_change": 0.03, "odds_move": 0.05}


def sel(grade, ev, odds):
    return {"grade": grade, "ev": ev, "odds": odds}


def test_grade_change_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("C", 0.01, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("S", 0.09, 1.9)}}}
    events = diff_rounds(prev, curr, CFG)
    types = {e["type"] for e in events}
    assert "grade_change" in types


def test_small_ev_change_ignored():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.030, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.031, 2.0)}}}
    events = diff_rounds(prev, curr, CFG)
    assert all(e["type"] != "ev_change" for e in events)


def test_large_ev_change_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.0)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("A", 0.07, 2.0)}}}
    events = diff_rounds(prev, curr, CFG)
    assert any(e["type"] == "ev_change" for e in events)


def test_odds_move_detected():
    prev = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.00)}}}
    curr = {"m1": {"selections": {"1X2_90min|home": sel("B", 0.03, 2.20)}}}
    events = diff_rounds(prev, curr, CFG)
    assert any(e["type"] == "odds_move" for e in events)


def test_match_added_and_removed():
    prev = {"m1": {"selections": {}}}
    curr = {"m2": {"selections": {}}}
    events = diff_rounds(prev, curr, CFG)
    types = {e["type"] for e in events}
    assert "match_added" in types and "match_removed" in types
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_differ.py -v`
Expected: FAIL（`ModuleNotFoundError: worldcup.differ`）

- [ ] **Step 3: 写 `worldcup/differ.py`**

```python
from __future__ import annotations


def diff_rounds(prev: dict, curr: dict, cfg: dict) -> list[dict]:
    events: list[dict] = []

    prev_ids = set(prev)
    curr_ids = set(curr)
    for mid in curr_ids - prev_ids:
        events.append({"type": "match_added", "match_id": mid})
    for mid in prev_ids - curr_ids:
        events.append({"type": "match_removed", "match_id": mid})

    for mid in prev_ids & curr_ids:
        psel = prev[mid].get("selections", {})
        csel = curr[mid].get("selections", {})
        for key in psel.keys() & csel.keys():
            a, b = psel[key], csel[key]
            market, _, selection = key.partition("|")
            if a.get("grade") != b.get("grade"):
                events.append({
                    "type": "grade_change", "match_id": mid, "market": market,
                    "selection": selection, "from": a.get("grade"),
                    "to": b.get("grade"),
                })
            if abs((b.get("ev") or 0) - (a.get("ev") or 0)) >= cfg["ev_change"]:
                events.append({
                    "type": "ev_change", "match_id": mid, "market": market,
                    "selection": selection, "from": a.get("ev"), "to": b.get("ev"),
                })
            oa, ob = a.get("odds"), b.get("odds")
            if oa and ob and abs(ob - oa) / oa >= cfg["odds_move"]:
                events.append({
                    "type": "odds_move", "match_id": mid, "market": market,
                    "selection": selection, "from": oa, "to": ob,
                })
    return events
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_differ.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归 + 提交（需用户同意 git）**

Run: `pytest -v`
Expected: 全部 PASS

```bash
git add worldcup/differ.py tests/test_differ.py
git commit -m "feat(engine): round differ -> change events"
```

---

## 完成标准（Definition of Done）

- [ ] `pytest -v` 全绿，覆盖：去水聚合、Elo 1X2、Poisson 矩阵/尾部、让球三类盘结算、集成、EV/等级/降级/状态、differ。
- [ ] 所有常数来自 `config/settings.yaml`，无散落魔法数字。
- [ ] 引擎不含任何网络/数据库/云调用（纯函数）。
- [ ] 数据模型（`models.py`）可被后续采集/云端计划直接复用。

## 与 spec 的对应（自检）

- spec 5.1 Elo → Task 3；5.2 Poisson（0~10、尾部 1%、1X2/OU）→ Task 4；5.4 去水+按 line 聚合+异常值 → Task 2；5.5 EV/Edge 双阈值、AH 仅 EV、降级硬规则、`NO_MARKET_YET`/`ODDS_PENDING`/`D` → Task 7；5.5 让球结算表（整数/半盘/四分之一盘 + push）→ Task 5；5.3 集成 → Task 6；6.4 变化检测 → Task 8；配置常数集中 → Task 1。
- **不在本计划**：采集（Plan 2）、云端 ingest/web/RDS/OSS、macmini publisher、调度、前端页面（Plan 3）、Task 0 数据源探测（Plan 0）。
