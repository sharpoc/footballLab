# AH 评估覆盖（snapshot AH 主盘 + eval_data AH 列）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让赛后验证链路覆盖亚洲让球：snapshot 保存 AH 主盘聚合赔率，`eval_data` 输出 `ah_line` / `odds_ah_home` / `odds_ah_away`，使现有 `worldcup.backtest` 的 AH 实盈评估（`ah_ev_buckets`）能吃到真实世界杯数据。

**Architecture:** 三段最小改动，复用既有结构：(1) `pipeline.analyze_match_input` 用已有的 `_main_home_ah_line` + `odds.aggregate` 生成 `market_ah_main` 块挂到 `MatchAnalysis`；(2) `local_runner._analysis_to_dict` 把它增量序列化到 snapshot 的 `market["ah_main"]`（无 AH 报价时不写该键，老快照兼容）；(3) `eval_data` 从 closing snapshot 读 `ah_main` 输出三个新 CSV 列，列名与 `backtest.load_matches` 已支持的 `ah_line` / `odds_ah_home` / `odds_ah_away` 完全对齐。`backtest.py` 不需要改。

**Tech Stack:** Python 标准库（无第三方依赖），自带测试 runner `tests/run_tests.py`（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

runner 不支持单测过滤；"验证失败/通过"都跑全量，用输出里的 `FAIL <file>::<test>` 行确认目标测试状态。当前基线为 `269/269 tests passed`。

**关键背景（实现者必读）：**

- `worldcup/backtest.py:47-48,74-97` 已支持 CSV 列 `ah_line` / `odds_ah_home` / `odds_ah_away`：`ah_line` 是主队让球线（如 `-0.5`），两列赔率必须同时存在才会进入 AH 评估；缺列或空串会安全退化为 `None`，不报错。所以本计划只需把数据喂进 CSV，评估端零改动。
- `worldcup/pipeline.py:358-365` 的 `_main_home_ah_line` 已实现主盘选线（按报价家数最多、|line| 最小取主盘）；`worldcup/pipeline.py:417-494` 的 `_ah_signals` 用 `odds.aggregate` 按 home_line / -home_line 分别聚合。本计划在 `analyze_match_input` 里复用同样的聚合方式生成 market 块；`_ah_signals` 保持原样不动（信号逻辑临近开赛不碰）。
- AH 的 home 和 away 两边 line 不同（home_line 与 -home_line），所以**不能**直接用 `odds.aggregate_market`（它只接受单一 line），必须像 `_ah_signals` 一样调两次 `odds.aggregate`。
- snapshot 是增量兼容契约：此前 `model.mu_total`、`odds_updated_at`、`result` 都是这样加的，ingest 服务端只验 HMAC/body hash/snapshot_id，不校验 schema；ledger/preview 只按已知键读取，新增 `market["ah_main"]` 不影响线上页面。
- 时效约束：`eval_data` 用"开球前最后一份"归档 snapshot 做 closing；已归档的旧 snapshot 不会有 `ah_main`（AH 列为空，属预期）。本改动需要在下一次 live refresh 前合入本机 `main`，开赛前的 closing snapshot 才会带上 AH 赔率。
- 项目规则：本地 commit 允许；**不 push、不部署、不触发 live refresh、不调用 The Odds API**。本计划全部离线。

---

### Task 1: pipeline 生成 `market_ah_main` 块

**Files:**
- Modify: `worldcup/pipeline.py`（`MatchAnalysis` 数据类 ~L51-63、`analyze_match_input` ~L187-245、`_main_home_ah_line` 之后 ~L365）
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_pipeline.py` 末尾追加（`replace`、`MarketType`、`load_config`、`analyze_match_input` 均已 import）：

```python
def test_analyze_match_input_aggregates_main_ah_market():
    analysis = analyze_match_input(_sample_match_input_with_three_markets(), load_config())

    assert analysis.market_ah_main == {
        "line_home": -0.5,
        "odds": {"home": 1.9, "away": 1.9},
        "n_books_by_selection": {"home": 1, "away": 1},
    }


def test_analyze_match_input_without_ah_quotes_has_no_ah_market():
    match_input = _sample_match_input_with_three_markets()
    match_input = replace(
        match_input,
        quotes=[q for q in match_input.quotes if q.market_type != MarketType.AH],
    )

    analysis = analyze_match_input(match_input, load_config())

    assert analysis.market_ah_main is None
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 输出含 `FAIL test_pipeline.py::test_analyze_match_input_aggregates_main_ah_market`（报 `MatchAnalysis` 无 `market_ah_main` 属性或缺少构造参数），其余既有测试保持 PASS。

- [ ] **Step 3: 最小实现**

3a. `worldcup/pipeline.py` 的 `MatchAnalysis` 数据类末尾加一个字段（在 `market_ou_2_5: dict` 之后）：

```python
    market_ah_main: dict | None
```

3b. 在 `_main_home_ah_line` 函数定义之后（~L366）新增聚合 helper：

```python
def _aggregate_ah_main(quotes: list[OddsQuote], ratio: float) -> dict | None:
    home_line = _main_home_ah_line(quotes)
    if home_line is None:
        return None
    block: dict = {"line_home": home_line, "odds": {}, "n_books_by_selection": {}}
    for selection, line in (("home", home_line), ("away", -home_line)):
        agg = odds.aggregate(quotes, MarketType.AH, selection, line=line, ratio=ratio)
        block["n_books_by_selection"][selection] = agg["n_books"]
        if agg["odds"] is not None:
            block["odds"][selection] = agg["odds"]
    return block
```

（`OddsQuote`、`MarketType`、`odds` 在 pipeline.py 顶部已 import；`_aggregate_ah_main` 定义在 `analyze_match_input` 之后没关系，调用发生在运行时。）

3c. `analyze_match_input` 返回的 `MatchAnalysis(...)` 构造里，在 `market_ou_2_5=market_ou_2_5,` 之后加：

```python
        market_ah_main=_aggregate_ah_main(match_input.quotes, ratio),
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿 `271/271 tests passed`（新增 2 个）。`MatchAnalysis` 只在 `pipeline.py` 一处按关键字构造，加字段不会打破别处。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/pipeline.py tests/test_pipeline.py
git commit -m "feat: aggregate main AH market in match analysis"
```

---

### Task 2: snapshot 序列化 `market["ah_main"]`

**Files:**
- Modify: `worldcup/local_runner.py`（`_analysis_to_dict` ~L67-113）
- Test: `tests/test_local_runner.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_local_runner.py` 末尾追加（`json`、`Path`、`TemporaryDirectory`、`build_snapshot_from_probe`、`_write_probe_files` 均已可用；probe 样例里 bk1 自带 `spreads` 市场 ±0.5 @1.9/1.9）：

```python
def test_build_snapshot_from_probe_includes_main_ah_market():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        ah_main = snapshot["matches"][0]["market"]["ah_main"]
        assert ah_main["line_home"] == -0.5
        assert ah_main["odds"] == {"home": 1.9, "away": 1.9}


def test_build_snapshot_from_probe_omits_ah_market_without_spreads():
    with TemporaryDirectory() as tmp:
        probe_dir = Path(tmp) / "probe"
        _write_probe_files(probe_dir)
        odds_path = probe_dir / "theoddsapi_wc_odds.json"
        events = json.loads(odds_path.read_text())
        events[0]["bookmakers"][0]["markets"] = [
            m for m in events[0]["bookmakers"][0]["markets"] if m["key"] != "spreads"
        ]
        odds_path.write_text(json.dumps(events))

        snapshot = build_snapshot_from_probe(probe_dir, snapshot_at="2026-06-08T00:00:00+00:00")

        assert "ah_main" not in snapshot["matches"][0]["market"]
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_local_runner.py::test_build_snapshot_from_probe_includes_main_ah_market`（KeyError: 'ah_main'）；`omits` 那条此时会先 PASS（键本来就不存在），属预期。

- [ ] **Step 3: 最小实现**

`worldcup/local_runner.py` 的 `_analysis_to_dict` 中，把 `match = {...}` 字面量里的：

```python
        "market": {
            "1x2": analysis.market_1x2,
            "ou_2_5": analysis.market_ou_2_5,
        },
```

改为先构造再条件挂键。在 `match = {` 之前加：

```python
    market: dict[str, Any] = {
        "1x2": analysis.market_1x2,
        "ou_2_5": analysis.market_ou_2_5,
    }
    if analysis.market_ah_main is not None:
        market["ah_main"] = analysis.market_ah_main
```

并把字面量里对应处替换为：

```python
        "market": market,
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿 `273/273 tests passed`。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/local_runner.py tests/test_local_runner.py
git commit -m "feat: serialize main AH market into snapshot"
```

---

### Task 3: eval_data 输出 AH 三列

**Files:**
- Modify: `worldcup/eval_data.py`（模块 docstring、`OUTPUT_COLUMNS`、`build_rows`）
- Test: `tests/test_eval_data.py`

- [ ] **Step 1: 写失败测试**

3 处改动 `tests/test_eval_data.py`：

1a. `_snapshot` helper 的 `"market"` 块加 `ah_main`（与 `1x2`、`ou_2_5` 平级）：

```python
                    "ah_main": {
                        "line_home": -0.5,
                        "odds": {"home": 1.96, "away": 1.88},
                    },
```

1b. 现有 `test_build_rows_joins_and_roundtrips_through_backtest_loader` 末尾追加两行断言：

```python
    assert loaded[0].ah_line == -0.5
    assert loaded[0].odds_ah == {"home": 1.96, "away": 1.88}
```

1c. 文件末尾追加两个新测试：

```python
def test_build_rows_includes_main_ah_odds():
    snapshots = [_snapshot("2026-06-11T18:00:00+00:00", 1.8)]
    rows, _ = build_rows(snapshots, [RESULT_ROW])
    assert rows[0]["ah_line"] == -0.5
    assert rows[0]["odds_ah_home"] == 1.96
    assert rows[0]["odds_ah_away"] == 1.88


def test_build_rows_blank_ah_when_block_missing_or_incomplete():
    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    del snap["matches"][0]["market"]["ah_main"]
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["ah_line"] == ""
    assert rows[0]["odds_ah_home"] == "" and rows[0]["odds_ah_away"] == ""

    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    snap["matches"][0]["market"]["ah_main"]["odds"] = {"home": 1.96}
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["ah_line"] == ""
    assert rows[0]["odds_ah_home"] == "" and rows[0]["odds_ah_away"] == ""
```

（单边赔率缺失时三列全空：`backtest.load_matches` 本来就要求两边同时存在，提前置空让 CSV 自身语义一致。`del` 分支模拟老归档快照没有 `ah_main` 的兼容场景。）

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_eval_data.py::test_build_rows_includes_main_ah_odds`（KeyError: 'ah_line'）等；roundtrip 测试报 `loaded[0].ah_line` 为 `None` 不等于 `-0.5`。

- [ ] **Step 3: 最小实现**

3a. `worldcup/eval_data.py` 模块 docstring 第 4-5 行改为：

```python
已知局限：neutral 一律按 1 处理（snapshot 未保存东道主修正）。
AH 取 closing snapshot 的主盘 `market.ah_main`；老归档快照无该块时 AH 三列为空。
```

3b. `OUTPUT_COLUMNS` 末尾追加三列：

```python
    "ah_line",
    "odds_ah_home",
    "odds_ah_away",
```

3c. `_market_odds` 之后新增 helper：

```python
def _ah_main_fields(entry: dict) -> dict:
    block = ((entry.get("market") or {}).get("ah_main")) or {}
    line = block.get("line_home")
    ah_odds = block.get("odds") or {}
    if line is None or "home" not in ah_odds or "away" not in ah_odds:
        return {"ah_line": "", "odds_ah_home": "", "odds_ah_away": ""}
    return {
        "ah_line": line,
        "odds_ah_home": ah_odds["home"],
        "odds_ah_away": ah_odds["away"],
    }
```

3d. `build_rows` 里 `rows.append({...})` 的字典字面量末尾（`"odds_under": ...,` 之后）加：

```python
                **_ah_main_fields(entry),
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿 `275/275 tests passed`，roundtrip 测试证明 `eval_data` 写出的 CSV 能被 `backtest.load_matches` 解析出 `ah_line` / `odds_ah`，从而进入 `ah_ev_buckets` 评估。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/eval_data.py tests/test_eval_data.py
git commit -m "feat: add AH columns to eval csv"
```

---

### Task 4: 离线 smoke、文档同步与收尾

**Files:**
- Modify: `README.md`（~L204 已知局限一行）
- Modify: `RECENT_WORK.md`（追加一节）

- [ ] **Step 1: 用本地缓存做离线 smoke（不联网）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.local_runner --input-dir data/cache --out data/local/backtest/ah_smoke_snapshot.json
python3 - <<'EOF'
import json
snap = json.load(open("data/local/backtest/ah_smoke_snapshot.json"))
with_ah = sum(1 for m in snap["matches"] if "ah_main" in m["market"])
print({"matches": len(snap["matches"]), "with_ah_main": with_ah})
EOF
```

Expected: `matches` 为 72；`with_ah_main` 接近 72（部分场次无 spreads 报价时小于 72 属正常，对照此前 OU anchored 68/72 的量级）。输出写到被忽略的 `data/local/backtest/`，**不覆盖** `data/cache/analysis_snapshot.json`。

- [ ] **Step 2: 更新 README 已知局限**

`README.md` ~L204 的：

> 已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 不进评估；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。

改为：

> 已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 采用 closing snapshot 的主盘线与均价（本改动合入前的老归档快照无 `ah_main`，对应 AH 列为空）；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。

- [ ] **Step 3: 最终全量验证**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: 全绿 `275/275 tests passed`；`git diff --check` 无输出。

- [ ] **Step 4: 更新 RECENT_WORK.md 并 commit（不 push）**

在 `RECENT_WORK.md` 顶部按既有格式追加一节"2026-06-10 AH 进入赛后评估链路"，记录：snapshot 新增 `market.ah_main`（增量兼容）、eval CSV 新增 `ah_line` / `odds_ah_home` / `odds_ah_away`、backtest 端零改动、测试计数、smoke 结果，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。同时把"已知局限：AH 不进评估"的旧表述更正。

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record AH eval coverage"
```

---

## 范围外（明确不做）

- 不改 `_ah_signals` / 信号分级逻辑，不消除它与 `_aggregate_ah_main` 之间的小量重复聚合（开赛前不碰信号行为）。
- 不改 `backtest.py`（AH 评估已就绪）。
- 不做 `neutral` / 东道主修正进评估（已知局限保留）。
- 不部署 ECS、不 push、不触发 live refresh；改动合入本机 `main` 后由既有 LaunchAgent 在下一轮 live refresh 自然生效。
