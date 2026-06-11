# Elo 缓存新鲜度宽限期（grace window）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** eloratings 抓取失败但本地缓存足够新（≤ 48 小时）时，不再把 `eloratings` 标进 `stale_sources`，从而不触发全量信号降级（S → B / `unconfirmed_backup`）；缓存超过宽限期则保持现有降级行为。

**Architecture:** 只改 `worldcup/refresh_runner.py` 的 eloratings 异常分支：失败时仍记录 `data_quality.source_errors`（保持可观测），但先看两个 Elo 缓存文件的 mtime，距现在 ≤ `ELO_CACHE_GRACE_SECONDS`（48h）就不追加 `stale_sources`。理由：Elo 评分只在完赛后变化，世界杯首场未踢前，≤48h 的缓存与真实值完全一致。引擎层（`pipeline.py` / `engine/value.py`）不动。

**Tech Stack:** Python 标准库（`pathlib`、`datetime`、`os.utime`）；测试为 `tests/run_tests.py` 自带的纯函数 runner（无 pytest）。

**背景（实现者无需再排查）：**
- 2026-06-11 起 `www.eloratings.net` 对非浏览器客户端返回 HTTP 415（WAF），抓取持续失败。
- 失败链路：`refresh_runner.py:97-103` 把 `eloratings` 追加进 `stale_sources` → `pipeline.py` `generate_value_signals` 里 `depends_on_backup = bool(stale_sources)` 对**所有**信号生效 → `engine/value.py` `grade_signal` 把等级封顶到 B 并加 reason `unconfirmed_backup`。
- 本计划不修复抓取本身（另有独立任务），只让"缓存够新"时不降级。

**项目约束（必须遵守）：**
- 验证命令：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
- 测试必须离线，不发真实网络请求（用注入的 transport / 预置缓存文件）。
- 本地 commit 可以做；**不要 push 远端、不要部署**。
- 不要改 `engine/` 纯函数层，不要顺手重构其他代码。

---

### Task 1: 宽限期内抓取失败 → 不标 stale（TDD）

**Files:**
- Modify: `worldcup/refresh_runner.py`（顶部常量 + `fetch_elo_files` 的 except 分支，现 97-103 行）
- Test: `tests/test_refresh_runner.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_refresh_runner.py` 末尾追加（复用文件里已有的 `FakeResponse`；`openfootball_body` / `odds_body` 与文件中 `test_refresh_uses_stale_odds_cache_when_theoddsapi_times_out` 的同名变量内容一致，这里完整重写一份，不要跨函数共享）：

```python
def _elo_grace_fixture(root: Path) -> Path:
    """预置全部缓存文件，返回 cache 目录。Elo 缓存 mtime 为当前时间（新鲜）。"""
    cache = root / "cache"
    cache.mkdir()
    openfootball_body = json.dumps(
        {
            "matches": [
                {
                    "round": "Matchday 1",
                    "date": "2026-06-11",
                    "time": "13:00 UTC-6",
                    "team1": "Mexico",
                    "team2": "South Africa",
                    "ground": "Mexico City",
                }
            ]
        }
    )
    odds_body = json.dumps(
        [
            {
                "id": "event-1",
                "sport_key": "soccer_fifa_world_cup",
                "commence_time": "2026-06-11T19:00:00Z",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "bookmakers": [
                    {
                        "key": "bk1",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Mexico", "price": 1.8},
                                    {"name": "South Africa", "price": 4.8},
                                    {"name": "Draw", "price": 3.6},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    )
    (cache / "openfootball_2026.json").write_text(openfootball_body)
    (cache / "theoddsapi_wc_odds.json").write_text(odds_body)
    (cache / "elo_world.tsv").write_text("1\t1\tMX\t1875\n2\t2\tZA\t1700\n")
    (cache / "elo_teams.tsv").write_text("MX\tMexico\nZA\tSouth Africa\n")
    return cache


def test_refresh_keeps_elo_fresh_within_grace_window():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_grace_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("invalid Elo ratings TSV: parsed 0 rows")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        # 失败仍要可观测：source_errors 必须记录
        assert result.snapshot["data_quality"]["source_errors"][0]["source"] == "eloratings"
        assert "parsed 0 rows" in result.snapshot["data_quality"]["source_errors"][0]["error"]
        # 但缓存新鲜（刚写入，age≈0），不标 stale、不降级
        assert result.snapshot["data_quality"]["stale_sources"] == []
        assert result.snapshot["run"]["stale_sources"] == []
        home_signal = next(
            signal
            for signal in result.snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert "unconfirmed_backup" not in home_signal["reasons"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py 2>&1 | grep -E "elo_fresh|tests passed"
```

预期：`FAIL test_refresh_runner.py::test_refresh_keeps_elo_fresh_within_grace_window`，失败断言是 `stale_sources == []`（当前实现会标 `["eloratings"]`）。

- [ ] **Step 3: 最小实现**

`worldcup/refresh_runner.py` 顶部（`RefreshResult` dataclass 之前）加常量和 helper：

```python
ELO_CACHE_GRACE_SECONDS = 48 * 3600


def _cache_age_seconds(paths: list[Path]) -> float | None:
    try:
        oldest_mtime = min(path.stat().st_mtime for path in paths)
    except OSError:
        return None
    return datetime.now(timezone.utc).timestamp() - oldest_mtime
```

把现有的 eloratings except 分支（97-103 行）：

```python
    try:
        fetch_elo_files(cache_dir=cache, transport=elo_transport)
    except Exception as exc:
        if not (elo_world_cache.exists() and elo_teams_cache.exists()):
            raise
        source_errors.append({"source": "eloratings", "error": f"{type(exc).__name__}: {exc}"})
        stale_sources.append("eloratings")
```

改为：

```python
    try:
        fetch_elo_files(cache_dir=cache, transport=elo_transport)
    except Exception as exc:
        if not (elo_world_cache.exists() and elo_teams_cache.exists()):
            raise
        source_errors.append({"source": "eloratings", "error": f"{type(exc).__name__}: {exc}"})
        # Elo 只在完赛后变化：缓存仍在宽限期内时视为等效新鲜，不触发降级
        age = _cache_age_seconds([elo_world_cache, elo_teams_cache])
        if age is None or age > ELO_CACHE_GRACE_SECONDS:
            stale_sources.append("eloratings")
```

不要改 openfootball / theoddsapi 分支（赔率和赛程没有"完赛前不变"的性质，宽限期只适用于 Elo）。

- [ ] **Step 4: 运行测试确认通过**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py 2>&1 | grep -E "elo_fresh|FAIL|tests passed"
```

预期：`PASS ... test_refresh_keeps_elo_fresh_within_grace_window`，无 FAIL，总数全过（改动前基线是全过）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/refresh_runner.py tests/test_refresh_runner.py
git commit -m "feat: keep elo cache fresh within 48h grace window"
```

---

### Task 2: 超过宽限期 → 仍按旧行为降级（回归保护）

**Files:**
- Test: `tests/test_refresh_runner.py`（文件末尾追加）

- [ ] **Step 1: 写失败测试（先写测试，确认它在当前实现下直接通过也算完成红绿确认——见 Step 2 说明）**

在 `tests/test_refresh_runner.py` 末尾追加。需要在文件顶部 import 区加 `import os` 和 `import time`：

```python
def test_refresh_marks_elo_stale_beyond_grace_window():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _elo_grace_fixture(root)
        openfootball_body = (cache / "openfootball_2026.json").read_text()
        odds_body = (cache / "theoddsapi_wc_odds.json").read_text()

        # 把 Elo 缓存 mtime 拨到 49 小时前，超过 48h 宽限期
        stale_ts = time.time() - 49 * 3600
        os.utime(cache / "elo_world.tsv", (stale_ts, stale_ts))
        os.utime(cache / "elo_teams.tsv", (stale_ts, stale_ts))

        def openfootball_transport(_url):
            return FakeResponse(openfootball_body.encode())

        def theoddsapi_transport(_url):
            return FakeResponse(odds_body.encode())

        def failing_elo_transport(_url):
            raise ValueError("invalid Elo ratings TSV: parsed 0 rows")

        result = refresh_cache_and_build_snapshot(
            api_key="fake-key",
            cache_dir=cache,
            snapshot_path=root / "out" / "snapshot.json",
            quota_path=cache / "quota.json",
            openfootball_transport=openfootball_transport,
            theoddsapi_transport=theoddsapi_transport,
            elo_transport=failing_elo_transport,
            history_dir=root / "history",
        )

        assert result.snapshot["data_quality"]["stale_sources"] == ["eloratings"]
        assert result.snapshot["run"]["stale_sources"] == ["eloratings"]
        home_signal = next(
            signal
            for signal in result.snapshot["matches"][0]["signals"]
            if signal["market_type"] == "1X2_90min" and signal["selection"] == "home"
        )
        assert "unconfirmed_backup" in home_signal["reasons"]
```

- [ ] **Step 2: 运行测试确认通过**

这是回归保护测试，Task 1 实现完成后它应当直接通过。如需验证测试本身有效，可临时把 `ELO_CACHE_GRACE_SECONDS` 改成 `999 * 3600` 看它变红，再改回 `48 * 3600`。

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py 2>&1 | grep -E "elo_stale_beyond|FAIL|tests passed"
```

预期：`PASS ... test_refresh_marks_elo_stale_beyond_grace_window`，无 FAIL。

- [ ] **Step 3: Commit**

```bash
git add tests/test_refresh_runner.py
git commit -m "test: cover elo stale downgrade beyond grace window"
```

---

### Task 3: 同步文档规则

**Files:**
- Modify: `CLAUDE.md:32`
- Modify: `AGENTS.md:32`
- Modify: `README.md:245`
- Modify: `RECENT_WORK.md`（覆盖更新近况）

三个文件里这一行（内容完全相同）：

```
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
```

- [ ] **Step 1: 在该行之后各新增一行（三个文件都加）**

```
- 例外：eloratings 抓取失败但本地 Elo 缓存 mtime 在 48 小时宽限期内时，只记 `data_quality.source_errors`，不标 `stale_sources`、不触发信号降级（Elo 仅在完赛后变化，宽限期内缓存与真实值一致）；超过宽限期仍按上一条降级。常量为 `worldcup/refresh_runner.py` 的 `ELO_CACHE_GRACE_SECONDS`。
```

- [ ] **Step 2: 更新 `RECENT_WORK.md`**

按该文件现有格式覆盖/追加一条：完成 Elo 缓存 48h 宽限期（背景：eloratings.net 自 2026-06-11 起对非浏览器请求返回 HTTP 415），涉及 `worldcup/refresh_runner.py`、`tests/test_refresh_runner.py`、三份规则文档；验证方式为 `tests/run_tests.py` 全过；遗留事项：eloratings 抓取本身仍被 WAF 拦截，另案处理。

- [ ] **Step 3: 全量测试 + Commit**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py 2>&1 | tail -3
git add CLAUDE.md AGENTS.md README.md RECENT_WORK.md
git commit -m "docs: record elo cache grace window rule"
```

预期：`N/N tests passed`（全过）。

---

## 验收标准（全部满足才算完成）

1. `tests/run_tests.py` 全过，新增 2 个测试（grace 内不降级、grace 外降级）都 PASS。
2. `refresh_cache_and_build_snapshot` 在 Elo 抓取失败 + 缓存 ≤48h 时：`source_errors` 含 eloratings、`stale_sources` 为空、信号 reasons 无 `unconfirmed_backup`。
3. 缓存 >48h 时行为与改动前完全一致。
4. openfootball / theoddsapi 的失败处理逻辑零改动。
5. 三份文档规则行已同步，`RECENT_WORK.md` 已更新。
6. 只有本地 commit，没有 push、没有部署动作。

## 不在本计划范围内

- 修复 eloratings 抓取被 WAF（HTTP 415）拦截的问题（独立任务）。
- 引擎层（`worldcup/engine/`）任何改动。
- ECS / 云端部署（本改动在本地 LaunchAgent 刷新链路生效，快照发布链路不变）。
