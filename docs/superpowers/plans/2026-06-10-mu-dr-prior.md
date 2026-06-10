# 总进球先验随 Elo 差变化（mu = f(|dr|)）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 OU 总进球先验从常数 `mu_total: 2.6` 升级为随 |dr|（Elo 差绝对值）线性上升的函数（配置门控，默认斜率 0 即与现状一致），并用 14,901 场真实历史数据网格搜索出候选参数，产出证据报告供用户决策。

**Architecture:** 引擎新增纯函数 `poisson.prior_mu(dr, cfg)`，`blended_mu` 增加 `dr` 参数并以 `prior_mu` 作为先验基准；pipeline 与回测重放把已有的 `dr` 传入。市场锚定逻辑不变：有 OU 市场时 `mu = w·mu_market + (1−w)·prior_mu(dr)`，无市场时回退 `prior_mu(dr)`——这让无市场/低 book 数比赛的总进球先验也有 Elo 信息。

**Tech Stack:** 纯标准库 Python，无新依赖；测试用 `tests/run_tests.py` 纯函数断言风格。

---

## 背景与约束

- 证据（`docs/research/2026-06-10-intl-backtest-baseline.md`，14,901 场）：场均总进球随 |dr| 单调上升——|dr| 0-100 为 2.325，100-200 为 2.519，200-300 为 2.590，300+ 为 3.246。常数先验系统性低估强弱悬殊场的总进球。
- 基于最新 `main` 新建分支执行（确认 `main` 已包含 `worldcup/elo_replay.py` 与 `--sweep` 能力；若 `codex/elo-replay-real-backtest` 尚未合并，先基于该分支）。
- **`mu_dr_slope` 默认 `0.0`（严格 no-op），生产行为不变**；网格搜索只产出证据，是否启用、取哪组参数由用户看报告后决定。
- 真实历史 CSV 无赔率 → 回测中 `mu = prior_mu(dr)` 直接生效（无市场混合），正好是先验的干净拟合环境。
- 验证命令（一次跑全部测试）：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- 本项目允许本地 commit，不允许 push。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `worldcup/engine/poisson.py` | 修改 | 新增 `prior_mu`；`blended_mu` 增加 `dr` 参数 |
| `worldcup/pipeline.py` | 修改 | `analyze_match_input` 把 `dr` 传给 `blended_mu` |
| `worldcup/backtest.py` | 修改 | `replay_match` 把 `dr` 传给 `blended_mu` |
| `config/settings.yaml` | 修改 | `poisson` 块新增 `mu_dr_slope: 0.0` |
| `tests/engine/test_poisson.py` | 修改 | `prior_mu` / 带 dr 的 `blended_mu` 测试 |
| `tests/test_pipeline.py`、`tests/test_backtest.py` | 修改 | dr 接线测试 |
| `docs/research/2026-06-10-mu-dr-fit.md` | 新建 | 网格搜索证据报告 |
| `README.md`、`RECENT_WORK.md` | 修改 | 文档同步 |

---

### Task 1: `poisson.prior_mu`

**Files:**
- Modify: `worldcup/engine/poisson.py`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试**

`tests/engine/test_poisson.py` 追加：

```python
def test_prior_mu_zero_slope_returns_base():
    from worldcup.engine.poisson import prior_mu

    assert prior_mu(0, CFG) == CFG["mu_total"]
    assert prior_mu(400, CFG) == CFG["mu_total"]


def test_prior_mu_rises_with_abs_dr_and_is_symmetric():
    from worldcup.engine.poisson import prior_mu

    cfg = dict(CFG)
    cfg["mu_total"] = 2.3
    cfg["mu_dr_slope"] = 0.002
    assert math.isclose(prior_mu(0, cfg), 2.3)
    assert math.isclose(prior_mu(300, cfg), 2.3 + 0.002 * 300)
    assert math.isclose(prior_mu(-300, cfg), prior_mu(300, cfg))


def test_prior_mu_clamped():
    from worldcup.engine.poisson import prior_mu

    cfg = dict(CFG)
    cfg["mu_dr_slope"] = 0.01
    assert prior_mu(10000, cfg) == 4.0
    cfg["mu_prior_max"] = 3.5
    assert prior_mu(10000, cfg) == 3.5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 3 个新测试 FAIL（`prior_mu` 不存在）。

- [ ] **Step 3: 实现**

`worldcup/engine/poisson.py` 中 `blended_mu` 之前追加：

```python
def prior_mu(dr: float, cfg: dict) -> float:
    """Total-goals prior; rises with |dr| when mu_dr_slope is enabled."""
    base = cfg["mu_total"]
    slope = cfg.get("mu_dr_slope", 0.0)
    if not slope:
        return base
    mu = base + slope * abs(dr)
    return _clamp(mu, cfg.get("mu_prior_min", 1.5), cfg.get("mu_prior_max", 4.0))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/engine/poisson.py tests/engine/test_poisson.py
git commit -m "feat: add dr-dependent total goals prior"
```

### Task 2: `blended_mu` 接入 `dr`

**Files:**
- Modify: `worldcup/engine/poisson.py`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试**

`tests/engine/test_poisson.py` 追加：

```python
def test_blended_mu_uses_dr_prior_without_market():
    from worldcup.engine.poisson import blended_mu, prior_mu

    cfg = dict(CFG)
    cfg["mu_dr_slope"] = 0.002
    cfg["mu_market_weight"] = 0.7
    assert math.isclose(blended_mu(None, 2.5, cfg, dr=300), prior_mu(300, cfg))


def test_blended_mu_blends_market_with_dr_prior():
    from worldcup.engine.poisson import blended_mu, implied_total_mu, prior_mu, prob_total_over

    cfg = dict(CFG)
    cfg["mu_dr_slope"] = 0.002
    cfg["mu_market_weight"] = 0.7
    p = prob_total_over(3.0, 2.5)
    expected = 0.7 * implied_total_mu(p, 2.5) + 0.3 * prior_mu(300, cfg)
    assert math.isclose(blended_mu(p, 2.5, cfg, dr=300), expected, abs_tol=1e-9)


def test_blended_mu_default_dr_keeps_old_behaviour():
    from worldcup.engine.poisson import blended_mu

    cfg = dict(CFG)
    cfg["mu_market_weight"] = 0.7
    assert math.isclose(blended_mu(None, 2.5, cfg), CFG["mu_total"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 前两个新测试 FAIL（`blended_mu` 不接受 `dr` 参数，`TypeError`）；第三个天然 PASS。

- [ ] **Step 3: 实现**

`worldcup/engine/poisson.py` 的 `blended_mu` 改为：

```python
def blended_mu(p_over_market: float | None, line: float, cfg: dict, dr: float = 0.0) -> float:
    """Blend market-implied total goals with the (possibly dr-dependent) prior.

    `mu_market_weight` 缺省为 0、`mu_dr_slope` 缺省为 0 时，
    行为与历史版本完全一致（恒用 mu_total 先验）。
    """
    base = prior_mu(dr, cfg)
    weight = cfg.get("mu_market_weight", 0.0)
    if p_over_market is None or weight <= 0:
        return base
    mu_market = implied_total_mu(p_over_market, line)
    return weight * mu_market + (1.0 - weight) * base
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（既有 `blended_mu` 测试因 `dr` 默认 0、slope 缺省 0 不回退）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/engine/poisson.py tests/engine/test_poisson.py
git commit -m "feat: blend market total with dr-dependent prior"
```

### Task 3: pipeline 与回测重放接线 `dr`

**Files:**
- Modify: `worldcup/pipeline.py`
- Modify: `worldcup/backtest.py`
- Modify: `config/settings.yaml`
- Test: `tests/test_pipeline.py`、`tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_pipeline.py` 追加（`_ou_match_input` 的 Elo 是 1800 vs 1800，需要一个大 Elo 差的输入；用 `apply_overrides` 取改写配置避免污染 `load_config` 缓存）：

```python
def test_mu_prior_uses_dr_when_no_ou_market():
    from worldcup.backtest import apply_overrides

    cfg = apply_overrides(load_config(), ["poisson.mu_dr_slope=0.002"])
    match_input = _ou_match_input(0.0, 0.0, with_ou=False)
    big_gap = MatchAnalysisInput(
        fixture=match_input.fixture,
        odds_event=match_input.odds_event,
        home_elo=EloRating("AA", 1, 2000),
        away_elo=EloRating("BB", 2, 1600),
        quotes=match_input.quotes,
    )
    analysis = analyze_match_input(big_gap, cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"] + 0.002 * 400)
```

`tests/test_backtest.py` 追加：

```python
def test_replay_match_uses_dr_prior_without_odds():
    from worldcup.backtest import BacktestMatch, apply_overrides, replay_match
    from worldcup.config import load_config

    cfg = apply_overrides(load_config(), ["poisson.mu_dr_slope=0.002"])
    match = BacktestMatch(
        match_id="m-dr",
        kickoff_at_utc="2024-01-01T12:00:00Z",
        home_team="Alpha",
        away_team="Beta",
        home_score=2,
        away_score=0,
        home_elo_before=2000.0,
        away_elo_before=1600.0,
    )
    result = replay_match(match, cfg)
    assert math.isclose(result["mu_used"], cfg["poisson"]["mu_total"] + 0.002 * 400)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 两个新测试 FAIL（`mu_total_used` / `mu_used` 仍为 2.6，因为 `dr` 未传入）。

- [ ] **Step 3: 实现**

3a. `worldcup/pipeline.py` 的 `analyze_match_input` 中，`blended_mu` 调用加 `dr`：

```python
    mu_total_used = poisson.blended_mu(
        p_over_market,
        ou_line,
        cfg["poisson"],
        dr=dr,
    )
```

3b. `worldcup/backtest.py` 的 `replay_match` 中同样：

```python
    mu_used = poisson.blended_mu(
        market_ou["over"] if market_ou else None, OU_LINE, cfg["poisson"], dr=dr
    )
```

3c. `config/settings.yaml` 的 `poisson` 块末尾加：

```yaml
  mu_dr_slope: 0.0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（slope 默认 0，所有存量行为不变）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/pipeline.py worldcup/backtest.py config/settings.yaml tests/test_pipeline.py tests/test_backtest.py
git commit -m "feat: wire dr into total goals prior"
```

### Task 4: 网格搜索（base × slope）

**Files:**
- 产物写入被忽略的 `data/local/backtest/`，不进 git。

- [ ] **Step 1: 确认历史 CSV 存在**

Run: `ls -lh data/local/backtest/intl_history.csv`
若不存在：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest_data --since 2010-01-01`

- [ ] **Step 2: 跑网格（3 个 base × 6 个 slope）**

对 base ∈ {2.2, 2.3, 2.4} 各跑一次：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest \
  --csv data/local/backtest/intl_history.csv \
  --out data/local/backtest/mu_fit_base2.2.json \
  --set poisson.mu_total=2.2 \
  --sweep "poisson.mu_dr_slope=0,0.001,0.0015,0.002,0.0025,0.003"
```

（base=2.3 / 2.4 时相应改 `--set` 和 `--out` 文件名。）

记录每个 (base, slope) 组合的 `ou_model` Brier / Log Loss 和 `1x2_model` Log Loss，整理成 18 行表格。

- [ ] **Step 3: 对照基线**

再跑一次现状基线供对照：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest \
  --csv data/local/backtest/intl_history.csv \
  --out data/local/backtest/mu_fit_baseline.json
```

（即 base=2.6、slope=0。）记录其 OU 与 1X2 指标。

- [ ] **Step 4: 选出候选并检查副作用**

选择标准：OU Log Loss 最低的 (base, slope) 组合；同时该组合的 1X2 Log Loss 不得比 18 格中最优值差超过 0.002（mu 影响 Poisson 平局形状，需确认没有为 OU 牺牲 1X2）。若曲面平坦，报告平坦区间而不是单点。

- [ ] **Step 5: 确认不改生产配置**

`git status` 确认 `config/settings.yaml` 除 Task 3 新增的 `mu_dr_slope: 0.0` 外无其他改动（`mu_total` 保持 2.6）。

### Task 5: 证据报告与文档

**Files:**
- Create: `docs/research/2026-06-10-mu-dr-fit.md`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: 写报告**

新建 `docs/research/2026-06-10-mu-dr-fit.md`，数字全部来自 Task 4 真实输出：

```markdown
# 总进球先验 mu = f(|dr|) 网格搜索（2026-06-10）

- 样本：data/local/backtest/intl_history.csv（14901 场，2010+，无赔率，先验直接生效）
- 模型：mu_prior = clamp(mu_total + mu_dr_slope·|dr|, 1.5, 4.0)
- 动机：基线报告显示场均总进球随 |dr| 从 2.325 升到 3.246，常数先验低估悬殊场
- 免责：仅用于研究分析，不构成投注建议

## 网格结果（18 组 + 现状基线）

（表格：base, slope, OU Brier, OU Log Loss, 1X2 Log Loss）

## 候选结论（待用户决策，不自动改生产配置）

（最优组合/平坦区间；vs 现状基线 base=2.6,slope=0 的 OU Log Loss 改善幅度；1X2 副作用检查结果；
 建议的生产配置候选值，例如 mu_total=X + mu_dr_slope=Y，并说明启用方式是改两行 settings.yaml）
```

- [ ] **Step 2: README 与 RECENT_WORK**

README「离线回测」小节 `dc_rho` 说明之后追加一句：

```markdown
总进球先验支持随 Elo 差上升：`poisson.mu_dr_slope`（默认 `0.0` 关闭；clamp 见 `mu_prior_min/max` 代码默认 1.5/4.0）；拟合证据见 `docs/research/2026-06-10-mu-dr-fit.md`。
```

RECENT_WORK.md 顶部插入条目：完成事项、网格最优组合与改善幅度、验证结果（全量测试通过）、待用户决策事项（`mu_dr_slope` 与 `dc_rho` 可一并决策启用）、风险（默认关闭，生产无变化）。

- [ ] **Step 3: 全量验证 + Commit**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

```bash
git add docs/research/2026-06-10-mu-dr-fit.md README.md RECENT_WORK.md
git commit -m "docs: record mu-dr prior grid search evidence"
```

---

## 风险与说明

1. **生产行为零变化**：`mu_dr_slope: 0.0` 严格 no-op（`prior_mu` 在 slope 为 0 时原样返回 `mu_total`，连 clamp 都不做）。
2. **与市场锚定的关系**：有 OU 市场时先验只占 30% 权重，启用 slope 的影响温和；真正受益的是无市场/低 book 数比赛和回测环境。
3. **1X2 副作用已纳入选择标准**：mu 变化会改变 Poisson 平局形状，Task 4 强制检查 1X2 Log Loss 不明显劣化。
4. **启用决策属于用户**：报告给出候选 (base, slope)，与 `dc_rho` 候选值可以一次性决策、一次改配置、一次部署。
