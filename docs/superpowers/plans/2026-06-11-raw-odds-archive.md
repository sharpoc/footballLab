# 原始赔率响应归档（odds_raw 逐轮留存）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次 live refresh 成功拿到新赔率后，把 The Odds API 原始响应（逐家 bookmaker 报价）gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`，为赛后赔率异动（line movement）研究保留细粒度数据；归档失败不阻断刷新/发布主链路。

**Architecture:** 只改 `worldcup/refresh_runner.py`：在既有 snapshot 归档块之后增加一个同模式的容错归档块，从本轮已写入的 `data/cache/theoddsapi_wc_odds.json` gzip 复制到 history 目录；`RefreshResult` 增加 `odds_raw_archive_path` 字段。本轮赔率抓取失败走缓存兜底时（`"theoddsapi" in stale_sources`）**不归档**，避免把上一轮的旧数据重复存成新 run。

**Tech Stack:** Python 标准库（`gzip`），自带测试 runner（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

红/绿都跑全量，用 `FAIL <file>::<test>` 行确认。基线为当前全绿（若 Plan A 调度计划已先落地为 `291/291`，未落地为 `292/292`；本计划与 Plan A 互不依赖，先后均可）。

**背景（实现者必读）：**

- snapshot 归档（`data/local/history/snapshot_<run_id>.json`）只保存**聚合后**赔率（均价/去水概率/家数/离散度），逐家报价在 `data/cache/theoddsapi_wc_odds.json` 每轮被覆盖，不归档就永久丢失。
- 磁盘成本：原始响应约 1.4MB，gzip 后数百 KB，按 Plan A 节奏全小组赛 ~40MB，目录已被 git ignore。
- `worldcup.eval_data` / `refresh_audit` / `ops_check` 都按 `snapshot_*.json` 前缀 glob history 目录，新增 `odds_raw_*.json.gz` 文件不影响它们。
- 本归档只为后续离线研究积累数据；**本计划不做任何 movement 特征、不改信号、不改模型**。
- 全程离线：不 push、不部署、不触发 live refresh、不调用 The Odds API。

---

### Task 1: refresh_runner 原始赔率归档

**Files:**
- Modify: `worldcup/refresh_runner.py`（import 区、`RefreshResult` ~L29-38、归档块 ~L156-174）
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: 扩展两个既有测试（失败先行）**

1a. `test_refresh_cache_and_build_snapshot_with_injected_transports` 末尾（`assert json.loads(archive.read_text())...` 之后）追加：

```python
        odds_raw = root / "history" / "odds_raw_20260608T000000Z-live.json.gz"
        assert result.odds_raw_archive_path == odds_raw
        assert odds_raw.exists()
        import gzip

        archived_events = json.loads(gzip.open(odds_raw, "rb").read().decode("utf-8"))
        assert archived_events[0]["id"] == "event-1"
        assert archived_events[0]["bookmakers"][0]["key"] == "bk1"
```

1b. `test_refresh_uses_stale_odds_cache_when_theoddsapi_times_out` 末尾追加（兜底轮不归档旧数据）：

```python
        assert result.odds_raw_archive_path is None
        assert list((root / "history").glob("odds_raw_*.json.gz")) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_refresh_runner.py::test_refresh_cache_and_build_snapshot_with_injected_transports`（`RefreshResult` 无 `odds_raw_archive_path` 属性）；stale 测试同样 FAIL。

- [ ] **Step 3: 最小实现**

3a. `worldcup/refresh_runner.py` 顶部 import 区加：

```python
import gzip
```

3b. `RefreshResult` 数据类末尾加字段：

```python
    odds_raw_archive_path: Path | None = None
```

3c. 在 snapshot 归档块（`print(f"warning: snapshot archive failed: ...")` 之后、`return RefreshResult(` 之前）插入：

```python
    odds_raw_archive_path: Path | None = None
    if "theoddsapi" not in stale_sources and odds_cache.exists():
        try:
            archive_dir = Path(history_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            odds_raw_archive_path = archive_dir / f"odds_raw_{run_metadata['run_id']}.json.gz"
            with gzip.open(odds_raw_archive_path, "wb") as fh:
                fh.write(odds_cache.read_bytes())
        except OSError as exc:
            print(f"warning: raw odds archive failed: {exc}", file=sys.stderr)
            odds_raw_archive_path = None
```

3d. `return RefreshResult(` 的参数列表加：

```python
        odds_raw_archive_path=odds_raw_archive_path,
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿（计数与基线一致，本任务只扩展既有测试不加新函数）。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/refresh_runner.py tests/test_refresh_runner.py
git commit -m "feat: archive raw odds responses per refresh"
```

---

### Task 2: 文档与收尾

**Files:**
- Modify: `README.md`（赛果回填/评估命令附近 ~L198-204）
- Modify: `RECENT_WORK.md`（顶部追加一节）

- [ ] **Step 1: README 增加一行说明**

在赛果回填/评估链路说明附近（`已知局限：…` 之前）加一行：

> - 每次 live refresh 成功获取新赔率后，原始逐家报价会 gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`（兜底缓存轮不归档），用于赛后赔率异动研究；该目录不进 git。

- [ ] **Step 2: 最终全量验证**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: 全绿；`git diff --check` 无输出。

- [ ] **Step 3: 更新 RECENT_WORK.md 并 commit（不 push）**

按既有格式在顶部追加"2026-06-11 原始赔率响应逐轮归档"一节：归档路径与命名、gzip、兜底轮不归档、失败不阻断主链路、用途（赛后 movement 研究，本计划不改信号/模型）、验证结果，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record raw odds archive"
```

---

## 范围外（明确不做）

- 不做 movement 特征提取、不加 `late_steam` 信号护栏、不改模型/信号——等小组赛积累 1-2 轮数据后另案回测决策。
- 不做归档清理/保留策略（量级 ~40MB，无需治理）。
- 不归档 openfootball / eloratings 原始响应（它们本身不随轮次高频变化，snapshot 已够用）。
- 不部署、不 push。
