# Plan A 刷新节奏简化（每日基线 + T-12h/T-6h/T-90/T-55/T-25 锚点）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把刷新调度从"多级窗口 cadence + 6 锚点"简化为用户确认的 Plan A：常规每天 1 次，每场比赛只保留 T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-25分钟 五个临赛锚点；低额度（≤30）和额度耗尽的既有保护不变。

**Architecture:** 只改 `worldcup/scheduler.py` 的常量与 `_select_interval`（删掉 7d/3d/1d/6h 窗口分级，cadence 恒为 24h），`MATCH_ANCHORS` 从 6 个改 5 个（去掉 T-3h30 / T-70 / T-40，新增 T-12h / T-6h）；同步 `ledger_html` 的"更新规则"卡片和 reason 中文映射。单场计划、开赛时钟对齐、低额度关键锚点、quota_exhausted、全局 decision 聚合等机制全部复用，不动。

**Tech Stack:** Python 标准库，自带测试 runner（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

runner 不支持单测过滤，红/绿都跑全量，用 `FAIL <file>::<test>` 行确认目标测试。当前基线 `292/292 tests passed`。

**背景与决策记录（实现者必读）：**

- 用户已确认 Plan A 并明确**不需要省额度**（额度烧完会换新的免费 key，只改 `.env` 的 `THE_ODDS_API_KEY`）。模拟口径：小组赛 17 天约 276 次刷新 ≈ 828 credits，按当前余额约 6-17~6-20 需要换 key，属预期，不是 bug。
- T-90 / T-55 / T-25 是用户点名必保的锚点；低额度（`0 < remaining <= 30`）时仍只保这三个（`CRITICAL_LOW_QUOTA_ANCHORS` 常量不变）。
- **不要加"合并窗口"类优化**：已验证真实赛程同日开球间隔 ≥2.5 小时，跨场锚点 20 分钟内近邻为 0 对；而 ≥20 分钟的合并窗口会因 LaunchAgent 15 分钟唤醒粒度误吞同场 T-55→T-25 必保锚点。
- 完全同时开球的场次共享一次刷新，靠既有"上次刷新 ≥ 锚点即跳过"规则，无需新代码。
- `policy_version` 从 `free-tier-v1` 升到 `free-tier-v2`，让 run metadata 能区分新旧策略生成的快照。
- 全程离线：不 push、不部署、不触发 live refresh、不调用 The Odds API。每个任务本地 commit。

---

### Task 1: scheduler 核心——锚点与间隔简化

**Files:**
- Modify: `worldcup/scheduler.py`（常量区 ~L12-32、`_select_interval` ~L60-84、`_cadence_label` ~L103-115）
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: 改写/新增失败测试**

1a. `tests/test_scheduler.py` 中 `test_scheduler_refreshes_when_no_previous_run` 的断言改为：

```python
    assert decision.should_refresh is True
    assert decision.reason == "no_previous_refresh"
    assert decision.policy_reason == "default"
    assert decision.interval_seconds == 86400
    assert decision.next_due_at is None
```

1b. 删除三个窗口测试 `test_scheduler_uses_twelve_hours_inside_seven_day_window`、`test_scheduler_uses_six_hours_inside_three_day_window`、`test_scheduler_uses_two_hours_inside_one_day_window`，原位置替换为一个每日间隔测试：

```python
def test_scheduler_uses_daily_interval_regardless_of_kickoff_window():
    decision = build_refresh_decision(
        now="2026-06-08T11:00:00+00:00",
        last_refresh_at="2026-06-08T00:00:00+00:00",
        next_kickoff_at="2026-06-11T19:00:00+00:00",
        quota_remaining=494,
    )

    assert decision.should_refresh is False
    assert decision.reason == "not_due"
    assert decision.policy_reason == "default"
    assert decision.interval_seconds == 86400
    assert decision.next_due_at == "2026-06-09T00:00:00+00:00"
```

1c. `test_match_plan_aligns_cadence_to_kickoff_clock_after_manual_refresh` 把 kickoff 改到 3 天后（避免被 T-12h 锚点抢先，保住"对齐回归"语义），断言改为每日 cadence：

```python
def test_match_plan_aligns_cadence_to_kickoff_clock_after_manual_refresh():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T02:58:55+00:00",
        last_refresh_at="2026-06-11T02:58:55+00:00",
        match=_match("2026-06-13T19:00:00+00:00"),
        quota_remaining=461,
    )

    assert plan["next_update_at"] == "2026-06-12T03:00:00+00:00"
    assert plan["policy_reason"] == "default"
    assert plan["label"] == "常规"
    assert plan["should_refresh"] is False
```

1d. `test_match_plan_waits_for_aligned_cadence_when_raw_interval_has_elapsed` 重命名并改为验证比赛日被 T-12h 锚点接管（保留 `05:13:46` 不触发刷新的生产回归语义）：

```python
def test_match_plan_holds_for_pre_12h_anchor_on_matchday():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T05:13:46+00:00",
        last_refresh_at="2026-06-11T03:08:26+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=455,
    )

    assert plan["next_update_at"] == "2026-06-11T07:00:00+00:00"
    assert plan["policy_reason"] == "pre_12h_checkpoint"
    assert plan["label"] == "T-12小时"
    assert plan["should_refresh"] is False
```

1e. `test_match_refresh_decision_uses_aligned_cadence_due_time` 同步改期望：

```python
    assert decision.should_refresh is False
    assert decision.reason == "not_due"
    assert decision.next_due_at == "2026-06-11T07:00:00+00:00"
```

1f. 文件末尾新增两个测试（T-6h 锚点生效、已删锚点不再触发）：

```python
def test_match_plan_uses_pre_6h_checkpoint_anchor():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T08:00:00+00:00",
        last_refresh_at="2026-06-11T07:30:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=494,
    )

    assert plan["next_update_at"] == "2026-06-11T13:00:00+00:00"
    assert plan["policy_reason"] == "pre_6h_checkpoint"
    assert plan["label"] == "T-6小时"
    assert plan["should_refresh"] is False


def test_match_plan_skips_removed_t70_anchor_between_t90_and_t55():
    plan = scheduler.build_match_refresh_plan(
        now="2026-06-11T17:40:00+00:00",
        last_refresh_at="2026-06-11T17:35:00+00:00",
        match=_match("2026-06-11T19:00:00+00:00"),
        quota_remaining=494,
    )

    assert plan["next_update_at"] == "2026-06-11T18:05:00+00:00"
    assert plan["policy_reason"] == "pre_55m_lineup_main"
```

1g. `test_scheduler_report_reads_snapshot_and_quota_without_refreshing` 末尾断言改为：

```python
        assert report["decision"]["next_due_at"] == "2026-06-09T00:00:00+00:00"
```

其余测试（T-90 锚点、低额度保 T-55、quota_exhausted、最早场次聚合、quota_low 24h、run metadata）**不改**——它们在新策略下行为不变，如果跑完红灯阶段它们也红了，说明实现改错了。

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 上述改动过的测试全部 FAIL（如 `policy_reason` 仍为 `pre_7d_window`、`next_update_at` 仍为旧窗口时间、`pre_6h_checkpoint` 不存在、T-70 仍抢在 T-55 前）；未改的测试保持 PASS。

- [ ] **Step 3: 最小实现**

3a. `worldcup/scheduler.py` 常量区：`POLICY_VERSION = "free-tier-v1"` 改为 `"free-tier-v2"`；删除 `PRE_7D_WINDOW_SECONDS`、`PRE_7D_INTERVAL_SECONDS`、`PRE_3D_WINDOW_SECONDS`、`PRE_3D_INTERVAL_SECONDS`、`PRE_1D_WINDOW_SECONDS`、`PRE_1D_INTERVAL_SECONDS`、`PRE_6H_WINDOW_SECONDS`、`PRE_6H_INTERVAL_SECONDS` 八个常量；`MATCH_ANCHORS` 替换为：

```python
MATCH_ANCHORS = (
    (12 * 3600, "pre_12h_checkpoint", "T-12小时", "赛日早间检查"),
    (6 * 3600, "pre_6h_checkpoint", "T-6小时", "赛前状态检查"),
    (90 * 60, "pre_90m_lineup_warmup", "T-1小时30分", "阵容/伤停预热"),
    (55 * 60, "pre_55m_lineup_main", "T-55分钟", "首发主抓点"),
    (25 * 60, "pre_25m_final_check", "T-25分钟", "临场最终确认"),
)
```

`CRITICAL_LOW_QUOTA_ANCHORS` 保持 `{"pre_90m_lineup_warmup", "pre_55m_lineup_main", "pre_25m_final_check"}` 不变。

3b. `_select_interval` 函数体替换为（保留签名兼容 `build_refresh_decision` 的调用）：

```python
def _select_interval(
    now: datetime,
    next_kickoff_at: datetime | None,
    quota_remaining: int | None,
) -> tuple[int, str]:
    if quota_remaining is not None and quota_remaining <= QUOTA_LOW_REMAINING:
        return QUOTA_LOW_INTERVAL_SECONDS, "quota_low"
    return DEFAULT_INTERVAL_SECONDS, "default"
```

3c. `_cadence_label` 的 `labels` 字典缩减为：

```python
    labels = {
        "default": "常规",
        "quota_low": "低额度",
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `tests/test_scheduler.py` 全绿。此时 `test_local_runner.py`、`test_refresh_runner.py`、`test_preview.py` 会有新的 FAIL（下游期望还没改），属预期，Task 2/3 处理；**不要**为了全绿在本任务改回 scheduler。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/scheduler.py tests/test_scheduler.py
git commit -m "feat: simplify refresh cadence to plan A anchors"
```

---

### Task 2: 下游 snapshot/refresh 测试期望对齐

**Files:**
- Test: `tests/test_local_runner.py`（~L91-92）
- Test: `tests/test_refresh_runner.py`（~L116）

这两个文件的被测代码（`local_runner` / `refresh_runner`）**不需要改**，它们只是透传 scheduler 的计划；要改的只是测试里写死的旧窗口期望值。fixture 场景均为 `observed/snapshot_at = 2026-06-08T00:00:00+00:00`、kickoff `2026-06-11T19:00:00+00:00`：新策略下每日 cadence 对齐开赛分钟（:00）得 `2026-06-09T00:00:00+00:00`，早于 T-12h 锚点（06-11 07:00）。

- [ ] **Step 1: 更新期望值**

`tests/test_local_runner.py` ~L91-92 改为：

```python
        assert snapshot["matches"][0]["refresh_plan"]["next_update_at"] == "2026-06-09T00:00:00+00:00"
        assert snapshot["matches"][0]["refresh_plan"]["label"] == "常规"
```

`tests/test_refresh_runner.py` ~L116 改为：

```python
        assert result.snapshot["matches"][0]["refresh_plan"]["next_update_at"] == "2026-06-09T00:00:00+00:00"
```

- [ ] **Step 2: 运行确认这两个文件转绿**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `test_local_runner.py`、`test_refresh_runner.py` 全 PASS；剩余 FAIL 只在 `test_preview.py`（Task 3 处理）。

- [ ] **Step 3: 本地 commit（不 push）**

```bash
git add tests/test_local_runner.py tests/test_refresh_runner.py
git commit -m "test: align snapshot plan expectations with plan A cadence"
```

---

### Task 3: 预览页"更新规则"卡片与 reason 映射

**Files:**
- Modify: `worldcup/ledger_html.py`（`_policy_reason_label` ~L357-366、更新规则卡片 ~L433-440）
- Test: `tests/test_preview.py`（fixture ~L15-17、断言 ~L99-104）

- [ ] **Step 1: 写失败测试**

1a. `tests/test_preview.py` 的 `_snapshot()` fixture 中 `run.policy` 改为新策略口径：

```python
            "policy": {
                "policy_reason": "default",
                "interval_seconds": 86400,
                "next_due_at": "2026-06-08T12:00:00+00:00",
            },
```

1b. 规则卡片断言（~L99-104）替换为：

```python
    assert "常规：每天 1 次" in html
    assert "临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-25分钟" in html
    assert "低额度：每天 1 次，并保留 T-90 / T-55 / T-25" in html
    assert "赛前 7 天内" not in html
    assert "当前规则：常规" in html
```

（"下次计划：2026 年 6 月 8 日 星期一 20:00" 的断言保留不动，`next_due_at` 未变。）

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_preview.py::...`（页面仍渲染旧窗口列表"赛前 7 天内：12 小时"）。

- [ ] **Step 3: 最小实现**

3a. `worldcup/ledger_html.py` 的 `_policy_reason_label` 字典替换为（锚点 reason 会成为全局 decision 的 `policy_reason`，需要中文映射；fallback 文案不变）：

```python
    labels = {
        "default": "常规",
        "quota_low": "低额度",
        "pre_12h_checkpoint": "T-12小时",
        "pre_6h_checkpoint": "T-6小时",
        "pre_90m_lineup_warmup": "T-1小时30分",
        "pre_55m_lineup_main": "T-55分钟",
        "pre_25m_final_check": "T-25分钟",
    }
```

3b. 更新规则卡片 `<ul class="policy-list">` 内容替换为：

```html
      <ul class="policy-list">
        <li>常规：每天 1 次</li>
        <li>临赛锚点：T-12小时 / T-6小时 / T-90分钟 / T-55分钟 / T-25分钟</li>
        <li>低额度：每天 1 次，并保留 T-90 / T-55 / T-25</li>
        <li>额度耗尽：暂停自动刷新</li>
      </ul>
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全量绿（应为 `291/291 tests passed`：基线 292 减去删除的 3 个窗口测试，加上新增 2 个锚点测试）。若计数不同以全绿为准并核对原因。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/ledger_html.py tests/test_preview.py
git commit -m "feat: show plan A refresh rules on preview"
```

---

### Task 4: 只读 smoke、文档与收尾

**Files:**
- Modify: `README.md`（~L250 运维要点一行）
- Modify: `RECENT_WORK.md`（顶部追加一节）

- [ ] **Step 1: 只读 dry-run smoke（不联网、不消耗额度）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.scheduler | python3 -c "
import json, sys
report = json.load(sys.stdin)
d = report['decision']
reasons = {p['policy_reason'] for p in d.get('match_plans', [])}
legacy = reasons & {'pre_7d_window', 'pre_3d_window', 'pre_1d_window', 'pre_6h_window',
                    'pre_3h30m_matchday_first', 'pre_70m_lineup_probe', 'pre_40m_lineup_confirm'}
print(json.dumps({'should_refresh': d['should_refresh'], 'next_due_at': d['next_due_at'],
                  'policy_reason': d['policy_reason'], 'legacy_reasons_found': sorted(legacy)}, ensure_ascii=False))
"
```

Expected: `legacy_reasons_found` 为空列表；`policy_reason` 属于 `default / quota_low / pre_12h_checkpoint / pre_6h_checkpoint / pre_90m_lineup_warmup / pre_55m_lineup_main / pre_25m_final_check / no_previous_refresh` 之一。`should_refresh` 真假取决于运行时刻，不作断言。

- [ ] **Step 2: 更新 README 运维要点**

`README.md` ~L250 原句：

> - The Odds API 按免费额度使用：低额度时 scheduler 会降频，但保留 T-90 / T-55 / T-25 等关键临赛锚点；上线前不得默认高频刷新。

替换为：

> - The Odds API 按免费额度使用：常规每天 1 次，每场保留 T-12小时 / T-6小时 / T-90 / T-55 / T-25 临赛锚点；低额度（≤30）只保 T-90 / T-55 / T-25。额度耗尽后更换 `.env` 的 `THE_ODDS_API_KEY`，再经确认执行一次 `worldcup.scheduled_publish --live --force` 让新额度写回 quota ledger（耗尽状态下调度不会自行恢复）。

- [ ] **Step 3: 最终全量验证**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: 全绿；`git diff --check` 无输出。

- [ ] **Step 4: 更新 RECENT_WORK.md 并 commit（不 push）**

按既有格式在顶部追加"2026-06-11 刷新节奏简化为 Plan A"一节，记录：窗口分级移除、锚点 6→5（去 T-3h30/T-70/T-40，增 T-12h/T-6h）、`policy_version` 升 `free-tier-v2`、预期消耗口径（小组赛约 828 credits，用户已确认换 key 策略）、换 key 后需 force publish 恢复调度，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record plan A cadence simplification"
```

---

## 范围外（明确不做）

- 不做锚点合并窗口/去重优化（已论证无收益且会误吞必保锚点）。
- 不做按剩余额度分档降频（用户已确认不省额度；≤30 的既有保护保留）。
- 不改通知频率/最小间隔（如需另起任务）。
- 不动 `_align_cadence_due_to_kickoff_clock`、低额度过滤、`quota_exhausted`、全局 decision 聚合逻辑。
- 不部署 ECS、不 push（部署/推送由用户单独确认后进行，页面"更新规则"卡片要等部署后才在线上生效）。
