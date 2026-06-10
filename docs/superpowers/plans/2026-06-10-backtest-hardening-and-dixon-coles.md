# 回测加固 + Dixon-Coles 低比分修正 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复上一轮 review 发现的三个回测/锚定弱点（赔率值校验、模型-市场同样本对比、mu 锚定加 `min_books` 守卫），并以配置门控方式（默认 no-op）加入 Dixon-Coles 低比分相关性修正和回测 CLI 参数覆盖能力，为真实历史数据到位后的参数调优做好基础。

**Architecture:** 全部改动保持引擎纯函数边界。Dixon-Coles 以 `poisson.dc_rho`（默认 `0.0` 即与现状逐位相同）作用于 `score_matrix` 的 (0,0)/(0,1)/(1,0)/(1,1) 四格后重归一化；mu 市场锚定在 pipeline 层用 OU 盘口 `n_books` 守卫；回测新增 `apply_overrides` 纯函数支持 `--set section.key=value` 配置覆盖，使 `dc_rho` / `mu_market_weight` 等参数可以在真实数据上做对比实验而不改 `settings.yaml`。

**Tech Stack:** 纯标准库 Python，无新依赖；测试用 `tests/run_tests.py` 纯函数断言风格。

---

## 背景与约束

- 前置：本计划基于分支 `codex/backtest-ou-market-total` 的代码状态（回测框架 + OU 市场锚定已完成，`218/218 tests passed`）。请在该分支（或其合并进 `main` 之后的 `main`）上新建分支执行。
- 上一轮 review 的三个非阻塞发现，本计划逐一修复：
  1. 回测 CSV 加载器不校验赔率 > 1.0，坏数据会在 `devig` 处抛错且无行号上下文。
  2. 回测里模型指标在全部行上算、市场指标只在有赔率的行上算，真实数据赔率覆盖不全时两者不可直接对比。
  3. mu 市场锚定不看 `n_books`，单一 bookmaker 报价也会锚定总进球。实测当前缓存 72 场中 68 场 OU ≥ 3 books，加守卫后只有 4 场单 book 的回退先验，属保守正确。
- Dixon-Coles 修正：独立双 Poisson 系统性低估 0-0/1-1 等低比分。标准修法是对低比分四格乘 tau 因子（参数 rho，典型拟合值为小负数）。**本计划只加结构，`dc_rho` 默认 `0.0`，生产行为不变**；rho 的具体取值必须等真实历史数据回测证据，不允许拍脑袋启用。
- 已知近似（写入 README，不在本计划处理）：`dc_rho != 0` 时比分矩阵的 P(over 2.5) 与 mu 锚定所用的纯 Poisson 反推之间有微小偏差；rho 为小负数时偏差可忽略，待回测确定 rho 后再评估是否需要联动。
- 明确不做（等真实数据回测证据）：power/Shin 去水、逐 book 去水后平均概率、模型向市场收缩、ensemble 权重调整。
- 验证命令（无 pytest 环境，一次跑全部测试）：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- 本项目允许本地 commit，不允许 push。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `worldcup/backtest.py` | 修改 | 加载器赔率校验；`model_matched` 同样本指标；`apply_overrides` + CLI `--set` |
| `worldcup/engine/poisson.py` | 修改 | `_clamped_rho` + `score_matrix` 的 Dixon-Coles tau 调整 |
| `worldcup/pipeline.py` | 修改 | mu 锚定增加 OU `n_books >= odds.min_books` 守卫 |
| `config/settings.yaml` | 修改 | `poisson` 块新增 `dc_rho: 0.0` |
| `tests/test_backtest.py` | 修改 | 新增加载器校验、matched 指标、overrides 测试 |
| `tests/engine/test_poisson.py` | 修改 | 新增 Dixon-Coles 测试 |
| `tests/test_pipeline.py` | 修改 | 新增 min_books 守卫测试（需扩展 `_ou_match_input` helper） |
| `README.md`、`RECENT_WORK.md` | 修改 | 文档同步 |

---

### Task 1: 回测 CSV 加载器校验赔率 > 1.0

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_load_matches_rejects_odds_not_above_one():
    import tempfile

    from worldcup.backtest import load_matches

    header = (
        "match_id,kickoff_at_utc,home_team,away_team,home_score,away_score,"
        "home_elo_before,away_elo_before,odds_home,odds_draw,odds_away\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(header + "m1,2025-06-01T18:00:00Z,Alpha,Beta,2,0,1900,1700,1.00,3.90,6.00\n")
        path = fh.name
    try:
        load_matches(path)
    except ValueError as exc:
        assert "row 2" in str(exc) and "odds" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（当前加载器不报错，`expected ValueError`）。

- [ ] **Step 3: 实现**

修改 `worldcup/backtest.py` 的 `load_matches` 循环体：先把三个市场 dict 取成局部变量并校验，再构造 `BacktestMatch`（替换现有循环内从 `ah_line = ...` 到 `matches.append(...)` 结束的部分）：

```python
        for line_no, row in enumerate(reader, start=2):
            for column in REQUIRED_COLUMNS:
                if not (row.get(column) or "").strip():
                    raise ValueError(f"row {line_no}: missing value for {column}")
            ah_line = _opt_float(row, "ah_line")
            odds_ah = _market_dict(row, {"home": "odds_ah_home", "away": "odds_ah_away"})
            odds_1x2 = _market_dict(
                row, {"home": "odds_home", "draw": "odds_draw", "away": "odds_away"}
            )
            odds_ou = _market_dict(row, {"over": "odds_over", "under": "odds_under"})
            for market in (odds_1x2, odds_ou, odds_ah):
                if market and any(value <= 1.0 for value in market.values()):
                    raise ValueError(f"row {line_no}: decimal odds must be > 1.0")
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
                    odds_1x2=odds_1x2,
                    odds_ou=odds_ou,
                    ah_line=ah_line if odds_ah is not None else None,
                    odds_ah=odds_ah if ah_line is not None else None,
                )
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: validate decimal odds in backtest loader"
```

### Task 2: 模型-市场同样本对比指标 `model_matched`

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_run_backtest_reports_model_matched_subset():
    from worldcup.backtest import load_matches, run_backtest
    from worldcup.config import load_config

    report = run_backtest(load_matches(SAMPLE_CSV), load_config(), min_sample=5)
    assert report["markets"]["1x2"]["model_matched"]["n"] == 7
    assert report["markets"]["ou_2_5"]["model_matched"]["n"] == 6
    assert report["markets"]["ou_2_5"]["market"]["n"] == 6
    assert report["markets"]["ou_2_5"]["model"]["n"] == 7
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`KeyError: 'model_matched'`）。

- [ ] **Step 3: 实现**

修改 `worldcup/backtest.py` 的 `run_backtest`：

3a. 在 rows 列表声明区补两个列表：

```python
    model_matched_1x2_rows: list[tuple[dict, str]] = []
    model_matched_ou_rows: list[tuple[dict, str]] = []
```

3b. 在 `if match.odds_1x2:` 分支内（`market_1x2_rows.append(...)` 之后）加：

```python
            model_matched_1x2_rows.append((replay["model_1x2"], result))
```

3c. 在 `if match.odds_ou:` 分支内（`market_ou_rows.append(...)` 之后）加：

```python
            model_matched_ou_rows.append((replay["model_ou"], ou_result))
```

3d. 报告 `markets` 块加 `model_matched`（与市场同样本的模型指标，用于公平对比）：

```python
        "markets": {
            "1x2": {
                "model": _mean_metrics(model_1x2_rows),
                "model_matched": _mean_metrics(model_matched_1x2_rows),
                "market": _mean_metrics(market_1x2_rows),
                "uniform": _mean_metrics(uniform_1x2_rows),
            },
            "ou_2_5": {
                "model": _mean_metrics(model_ou_rows),
                "model_matched": _mean_metrics(model_matched_ou_rows),
                "market": _mean_metrics(market_ou_rows),
                "uniform": _mean_metrics(uniform_ou_rows),
            },
        },
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（既有 `test_run_backtest_report_structure_and_small_sample_flag` 只断言 `model/market/uniform` 键存在性，不会回退）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: report model metrics on market-matched subset"
```

### Task 3: 回测 CLI 配置覆盖 `--set`

**Files:**
- Modify: `worldcup/backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_backtest.py` 追加：

```python
def test_apply_overrides_returns_modified_copy():
    from worldcup.backtest import apply_overrides

    cfg = {"poisson": {"dc_rho": 0.0}, "ou_main_line": 2.5}
    out = apply_overrides(cfg, ["poisson.dc_rho=-0.1", "ou_main_line=3.5"])
    assert out["poisson"]["dc_rho"] == -0.1
    assert out["ou_main_line"] == 3.5
    assert cfg["poisson"]["dc_rho"] == 0.0
    assert cfg["ou_main_line"] == 2.5


def test_apply_overrides_parses_int_bool_string():
    from worldcup.backtest import apply_overrides

    cfg = {"odds": {"min_books": 3}}
    out = apply_overrides(cfg, ["odds.min_books=5", "odds.flag=true", "odds.name=abc"])
    assert out["odds"]["min_books"] == 5
    assert out["odds"]["flag"] is True
    assert out["odds"]["name"] == "abc"


def test_apply_overrides_invalid_format_raises():
    from worldcup.backtest import apply_overrides

    for bad in ("poisson.dc_rho", "=1", "unknown.key=1"):
        try:
            apply_overrides({"poisson": {}}, [bad])
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")


def test_cli_accepts_set_overrides():
    import json
    import tempfile

    from worldcup.backtest import main

    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "report.json"
        code = main(
            [
                "--csv", str(SAMPLE_CSV),
                "--out", str(out_path),
                "--min-sample", "5",
                "--set", "poisson.mu_market_weight=0",
            ]
        )
        assert code == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        mus = [b["mean_mu_used"] for b in report["totals_by_abs_dr"] if b["n"]]
        for mu in mus:
            assert math.isclose(mu, 2.6, abs_tol=1e-9)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`apply_overrides` 不存在；CLI 不认识 `--set`）。

- [ ] **Step 3: 实现**

3a. `worldcup/backtest.py` 顶部 import 区加 `import copy`。

3b. 在 `main` 之前追加：

```python
def _parse_override_value(value: str):
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def apply_overrides(cfg: dict, overrides: list[str]) -> dict:
    """Return a deep copy of cfg with `section.key=value` / `key=value` overrides applied."""
    out = copy.deepcopy(cfg)
    for item in overrides:
        key, sep, raw_value = item.partition("=")
        if not sep or not key or not raw_value.strip():
            raise ValueError(f"invalid override (expect section.key=value): {item!r}")
        section, dot, name = key.partition(".")
        parsed = _parse_override_value(raw_value.strip())
        if not dot:
            out[key] = parsed
            continue
        if section not in out or not isinstance(out[section], dict):
            raise ValueError(f"unknown config section: {section!r}")
        out[section][name] = parsed
    return out
```

3c. 修改 `main`：增加参数并经 `apply_overrides` 取配置（同时天然解决 `load_config` 是 `lru_cache` 共享 dict、不能原地改的问题）：

```python
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        dest="overrides",
        metavar="SECTION.KEY=VALUE",
        help="override a config value for this run, e.g. --set poisson.dc_rho=-0.1",
    )
    args = parser.parse_args(argv)

    cfg = apply_overrides(load_config(args.config), args.overrides)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/backtest.py tests/test_backtest.py
git commit -m "feat: support config overrides in backtest cli"
```

### Task 4: mu 市场锚定增加 `min_books` 守卫

**Files:**
- Modify: `worldcup/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: 扩展测试 helper 并写失败测试**

把 `tests/test_pipeline.py` 中 `_ou_match_input` 的签名和 OU 报价生成部分改为支持自定义 book 列表（其余 fixture/event 构造不变）：

```python
def _ou_match_input(
    over_odds: float,
    under_odds: float,
    with_ou: bool = True,
    books: tuple[str, ...] = ("book1", "book2", "book3"),
) -> MatchAnalysisInput:
    kickoff = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
    quotes = [
        OddsQuote("book1", MarketType.X12, "home", 2.5),
        OddsQuote("book1", MarketType.X12, "draw", 3.2),
        OddsQuote("book1", MarketType.X12, "away", 2.9),
    ]
    if with_ou:
        for book in books:
            quotes.append(OddsQuote(book, MarketType.OU, "over", over_odds, line=2.5))
            quotes.append(OddsQuote(book, MarketType.OU, "under", under_odds, line=2.5))
```

文件末尾追加测试：

```python
def test_ou_anchor_requires_min_books():
    cfg = load_config()
    analysis = analyze_match_input(_ou_match_input(1.55, 2.45, books=("book1",)), cfg)
    assert math.isclose(analysis.mu_total_used, cfg["poisson"]["mu_total"])
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `test_ou_anchor_requires_min_books` FAIL（单 book 也被锚定，`mu_total_used != 2.6`）；既有 `test_ou_probability_varies_with_market_total`（3 books）保持 PASS。

- [ ] **Step 3: 实现**

修改 `worldcup/pipeline.py` 的 `analyze_match_input`，把现在直接传 `market_ou_2_5["market_probs"].get("over")` 的两行替换为带守卫的版本：

```python
    p_over_market = market_ou_2_5["market_probs"].get("over")
    if p_over_market is not None:
        ou_books = market_ou_2_5["n_books_by_selection"]
        if min(ou_books.get("over", 0), ou_books.get("under", 0)) < cfg["odds"]["min_books"]:
            p_over_market = None
    mu_total_used = poisson.blended_mu(p_over_market, ou_line, cfg["poisson"])
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: 用真实缓存抽查守卫影响面**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "
from pathlib import Path
from worldcup.local_runner import build_snapshot_from_cache
snap = build_snapshot_from_cache(Path('data/cache'))
mus = [m['model']['mu_total'] for m in snap['matches']]
anchored = sum(1 for mu in mus if abs(mu - 2.6) > 1e-9)
print('anchored:', anchored, '/', len(mus))
"
```

Expected: `anchored: 68 / 72` 左右（当前缓存里 4 场 OU 只有 1 家 book，回退先验）。

- [ ] **Step 6: Commit**

```bash
git add worldcup/pipeline.py tests/test_pipeline.py
git commit -m "feat: gate mu market anchor by min books"
```

### Task 5: Dixon-Coles 低比分修正（配置门控，默认 no-op）

**Files:**
- Modify: `worldcup/engine/poisson.py`
- Modify: `config/settings.yaml`
- Test: `tests/engine/test_poisson.py`

- [ ] **Step 1: 写失败测试**

`tests/engine/test_poisson.py` 追加：

```python
def test_score_matrix_dc_rho_zero_is_noop():
    base, tail_base = score_matrix(1.5, 1.1, CFG)
    cfg = dict(CFG)
    cfg["dc_rho"] = 0.0
    same, tail_same = score_matrix(1.5, 1.1, cfg)
    assert same == base
    assert tail_same == tail_base


def test_score_matrix_dc_negative_rho_boosts_low_score_draws():
    cfg = dict(CFG)
    cfg["dc_rho"] = -0.1
    adjusted, _ = score_matrix(1.5, 1.1, cfg)
    base, _ = score_matrix(1.5, 1.1, CFG)
    assert adjusted[0][0] > base[0][0]
    assert adjusted[1][1] > base[1][1]
    assert adjusted[0][1] < base[0][1]
    assert adjusted[1][0] < base[1][0]
    assert math.isclose(sum(sum(row) for row in adjusted), 1.0, abs_tol=1e-9)
    assert probs_1x2(adjusted)["draw"] > probs_1x2(base)["draw"]


def test_score_matrix_dc_extreme_rho_clamped_no_negative_cells():
    cfg = dict(CFG)
    cfg["dc_rho"] = -5.0
    adjusted, _ = score_matrix(1.5, 1.1, cfg)
    assert min(min(row) for row in adjusted) >= 0.0
    assert math.isclose(sum(sum(row) for row in adjusted), 1.0, abs_tol=1e-9)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 后两个测试 FAIL（当前 `score_matrix` 忽略 `dc_rho`，调整不生效）；no-op 测试天然 PASS。

- [ ] **Step 3: 实现**

修改 `worldcup/engine/poisson.py`：在 `score_matrix` 之前加 rho 合法域 clamp（保证四格 tau 因子非负）：

```python
def _clamped_rho(rho: float, lh: float, la: float) -> float:
    if not rho:
        return 0.0
    lo = max(-1.0 / lh, -1.0 / la)
    hi = min(1.0 / (lh * la), 1.0)
    return _clamp(rho, lo, hi)
```

`score_matrix` 改为（tail 仍按未调整矩阵计算，保持「截断损失」语义；tau 调整后重归一化）：

```python
def score_matrix(lh: float, la: float, cfg: dict) -> tuple[list[list[float]], float]:
    n = cfg["max_goals"]
    ph = _pmf_series(lh, n)
    pa = _pmf_series(la, n)
    raw = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    tail = 1.0 - sum(sum(row) for row in raw)
    rho = _clamped_rho(cfg.get("dc_rho", 0.0), lh, la)
    if rho:
        raw[0][0] *= 1.0 - lh * la * rho
        raw[0][1] *= 1.0 + lh * rho
        raw[1][0] *= 1.0 + la * rho
        raw[1][1] *= 1.0 - rho
    total = sum(sum(row) for row in raw)
    matrix = [[raw[i][j] / total for j in range(n + 1)] for i in range(n + 1)]
    return matrix, tail
```

`config/settings.yaml` 的 `poisson` 块末尾加：

```yaml
  dc_rho: 0.0
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS（`dc_rho: 0.0` 时矩阵与旧实现逐位一致，存量测试不回退）。

- [ ] **Step 5: 验证回测可对比 rho 取值**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv tests/data/backtest_sample.csv --min-sample 5 --out data/local/backtest/dc_probe.json --set poisson.dc_rho=-0.1
```

Expected: 正常输出 sample 摘要（合成样例仅验证链路通，不解读指标）。

- [ ] **Step 6: Commit**

```bash
git add worldcup/engine/poisson.py config/settings.yaml tests/engine/test_poisson.py
git commit -m "feat: add config-gated dixon-coles low-score correction"
```

### Task 6: 文档同步

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: README「离线回测」小节补充**

在既有「离线回测」小节末尾追加：

```markdown
- 报告中 `markets.*.model` 是全样本模型指标，`model_matched` 是与市场基线同样本（有收盘赔率的行）的模型指标；对比模型 vs 市场请用 `model_matched` vs `market`。
- 可用 `--set section.key=value` 做单次参数实验（不改 `settings.yaml`），例如 `--set poisson.dc_rho=-0.1 --set poisson.mu_market_weight=0`。
- CSV 中任何十进制赔率必须 > 1.0，否则按行号报错。
```

并在「另外：OU 大小球模型……」段落后追加：

```markdown
模型还内置 Dixon-Coles 低比分修正开关 `poisson.dc_rho`（默认 `0.0` 即关闭，行为与历史版本一致）；rho 的取值必须由真实历史数据回测确定后再启用。mu 市场锚定仅在 OU 盘口 over/under 双边报价家数均达到 `odds.min_books` 时生效，否则回退先验 `poisson.mu_total`。注意：`dc_rho != 0` 时比分矩阵的大小球概率与 mu 锚定的纯 Poisson 反推存在微小近似偏差，rho 为小负数时可忽略。
```

- [ ] **Step 2: 更新 RECENT_WORK.md**

在文件顶部（标题说明行之后）插入新条目，按既有格式记录：完成事项（加载器赔率校验、`model_matched` 同样本指标、CLI `--set` 覆盖、mu 锚定 `min_books` 守卫、配置门控 Dixon-Coles）、涉及文件、验证结果（`tests/run_tests.py` 全绿、真实缓存抽查 anchored 68/72、CLI `--set` smoke）、下次继续事项（确认真实历史数据来源后用 `--set` 做 `dc_rho` / `mu_market_weight` 参数扫描）、注意风险（`dc_rho` 默认关闭，未经回测证据不得启用；4 场单 book OU 回退先验属预期）。

- [ ] **Step 3: 全量验证**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 4: Commit**

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record backtest hardening and dixon-coles gate"
```

---

## 风险与说明

1. **生产行为变化只有一处**：Task 4 的 `min_books` 守卫会让 OU 报价不足 3 家的比赛（当前缓存中 4/72 场）的 mu 回退先验 2.6，更保守，属预期；其余改动（`dc_rho: 0.0`、回测增强）对线上 snapshot 输出无影响。
2. **Dixon-Coles 默认关闭**：`dc_rho` 取值必须等真实历史数据回测；启用前 1X2/AH/OU 输出均不变。rho clamp 保证任何配置值都不会产生负概率格。
3. **`apply_overrides` 总是深拷贝**：顺带消除了 `load_config` 的 `lru_cache` 共享 dict 被原地修改的隐患。
4. **本计划不改**：scheduler、ingest、HMAC、store schema、预览页路由、去水方法、ensemble 权重。
