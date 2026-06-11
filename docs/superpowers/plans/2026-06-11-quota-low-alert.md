# The Odds API 额度告警推送实现计划（v2，适配 key 槽位轮换）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> v2 说明：v1 写于 key 轮换（`theoddsapi_keys.py`，commit `90164d9`..`1082232`）落地之前，按单一 `theoddsapi` 台账设计，已作废。本版按槽位（primary/secondary）监控，并把"槽位耗尽（即将自动切换）"作为告警事件。

**Goal:** 任一 key 槽位的剩余额度向下跨过阈值（100 / 30 / 10 / 0）时，随当轮发布自动发一条 WxPusher 告警；跨过 0 即"该槽位耗尽、调度将自动切换/暂停"，提醒用户给耗尽槽位补新 key。每个槽位每个阈值只发一次，不刷屏。

**Architecture:** 只改 `worldcup/scheduled_publish.py`：refresh 前后各读一次 quota ledger 的**槽位视图**（由 `.env` 配置的 slot 决定监控 `theoddsapi_primary` / `theoddsapi_secondary`，未配置 slot 时回退监控 legacy `theoddsapi`），发布成功后对每个槽位做"向下跨阈值"判定，汇总成一条告警经既有 `send_wxpusher_notification` 发送；结果字典新增 `quota_alert`。复用 `--no-notify` 总开关与 `notify_fn` 注入，零新依赖。

**Tech Stack:** Python 标准库，自带测试 runner（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

**背景（实现者必读）：**

- key 轮换已上线：`choose_key_slot` 在 primary 台账 `remaining <= 0` 时自动切 secondary，两槽都耗尽时调度报 `quota_exhausted` 暂停。轮换是**静默**的——没有告警的话用户不知道备胎已启用、也不知道该补 key 了。
- "跨过 0"天然就是切换/暂停事件：primary 从正数跌到 0 的那一轮发"已耗尽、下一轮自动切 secondary"；secondary 跨 0 那一轮发"全部耗尽、自动刷新暂停"。无需记录状态。
- 防刷屏靠跨阈值语义：`before > t >= after`。下一轮 before 已在阈值下方，不会重复发。
- 替换耗尽槽位的 key 后，台账该槽位仍是 0，调度不会自动用它；README 已写明需经确认执行一次 `worldcup.scheduled_publish --live --force` 写回新额度。告警文案直接引用该步骤。
- 告警走 `notify` 总开关：`--no-notify` 静音；dry-run / not_due / blocked / 发布失败路径不发。
- **不打印 key 值**：告警和结果里只出现槽位名（PRIMARY / SECONDARY / LEGACY）和数字。
- 测试必须自带临时 `.env`（写入假的 `THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY`）并传 `env_path`，不依赖仓库真实 `.env` 的槽位配置。
- 全程离线（fake notify_fn / refresh_fn / publish_fn）；不 push、不部署、不触发 live refresh、不调用 The Odds API。

---

### Task 1: scheduled_publish 槽位跨阈值告警

**Files:**
- Modify: `worldcup/scheduled_publish.py`
- Test: `tests/test_scheduled_publish.py`

- [ ] **Step 1: 写失败测试**

`tests/test_scheduled_publish.py` 末尾追加（复用文件内既有 `FakeRefreshResult` / `_write_not_due_snapshot`）：

```python
def _run_publish_with_quota(root, before, after, notify=True):
    """before/after 为 {provider: remaining} 字典，模拟本轮刷新前后的台账。"""
    snapshot_path, quota_path = _write_not_due_snapshot(root)
    env_path = root / ".env"
    env_path.write_text(
        "THE_ODDS_API_KEY_PRIMARY=fake-primary\nTHE_ODDS_API_KEY_SECONDARY=fake-secondary\n",
        encoding="utf-8",
    )
    quota_path.write_text(
        json.dumps({"providers": {p: {"remaining": r, "last": 3} for p, r in before.items()}}),
        encoding="utf-8",
    )
    notify_calls = []

    def refresh_fn(**kwargs):
        Path(kwargs["quota_path"]).write_text(
            json.dumps({"providers": {p: {"remaining": r, "last": 3} for p, r in after.items()}}),
            encoding="utf-8",
        )
        return FakeRefreshResult(
            snapshot_path=Path(kwargs["snapshot_path"]),
            snapshot={"counts": {"matches": 72}},
            run_metadata={"run_id": "20260609T000000Z-live"},
        )

    def publish_fn(**kwargs):
        return {
            "status": "sent",
            "http_status": 200,
            "ingest_status": "stored",
            "request": {"run_id": "20260609T000000Z-live"},
        }

    def notify_fn(content, *, summary):
        notify_calls.append({"content": content, "summary": summary})
        return {"status": "sent", "exit_code": 0}

    result = run_scheduled_publish(
        now="2026-06-09T00:00:00+00:00",
        live=True,
        env_path=env_path,
        cache_dir=root / "cache",
        snapshot_path=snapshot_path,
        quota_path=quota_path,
        endpoint="https://football.celab.xin/api/ingest/snapshot",
        api_key="fake-key",
        secret="fake-secret",
        notify=notify,
        refresh_fn=refresh_fn,
        publish_fn=publish_fn,
        notify_fn=notify_fn,
    )
    return result, notify_calls


def test_quota_alert_sent_when_primary_crosses_threshold():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 102, "theoddsapi": 102},
            after={"theoddsapi_primary": 99, "theoddsapi": 99},
        )

    assert result["status"] == "published"
    assert result["quota_alert"]["status"] == "sent"
    assert result["quota_alert"]["slots"] == [
        {"slot": "PRIMARY", "remaining": 99, "thresholds_crossed": [100]}
    ]
    alert_calls = [c for c in notify_calls if "额度告警" in c["summary"]]
    assert len(alert_calls) == 1
    assert "PRIMARY" in alert_calls[0]["content"]
    assert "99" in alert_calls[0]["content"]


def test_quota_alert_reports_primary_exhaustion_as_switchover():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 2, "theoddsapi": 2},
            after={"theoddsapi_primary": 0, "theoddsapi": 0},
        )

    assert result["quota_alert"]["slots"] == [
        {"slot": "PRIMARY", "remaining": 0, "thresholds_crossed": [0]}
    ]
    alert_calls = [c for c in notify_calls if "额度告警" in c["summary"]]
    assert len(alert_calls) == 1
    assert "耗尽" in alert_calls[0]["content"]
    assert "THE_ODDS_API_KEY" in alert_calls[0]["content"]


def test_quota_alert_not_sent_without_crossing():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 200, "theoddsapi": 200},
            after={"theoddsapi_primary": 197, "theoddsapi": 197},
        )

    assert result["status"] == "published"
    assert result["quota_alert"] is None
    assert [c for c in notify_calls if "额度告警" in c["summary"]] == []


def test_quota_alert_respects_no_notify():
    with TemporaryDirectory() as tmp:
        result, notify_calls = _run_publish_with_quota(
            Path(tmp),
            before={"theoddsapi_primary": 102, "theoddsapi": 102},
            after={"theoddsapi_primary": 99, "theoddsapi": 99},
            notify=False,
        )

    assert result["status"] == "published"
    assert result["quota_alert"] is None
    assert notify_calls == []
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 四个新测试 FAIL（`result` 无 `quota_alert` 键，KeyError）。

- [ ] **Step 3: 最小实现**

3a. `worldcup/scheduled_publish.py` import 区追加：

```python
from worldcup.quota import load_quota_ledger
from worldcup.theoddsapi_keys import (
    LEGACY_PROVIDER,
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    configured_key_slots,
)
```

模块常量区追加：

```python
QUOTA_ALERT_THRESHOLDS = (100, 30, 10, 0)
_SLOT_LABELS = {
    PRIMARY_PROVIDER: "PRIMARY",
    SECONDARY_PROVIDER: "SECONDARY",
    LEGACY_PROVIDER: "LEGACY",
}
```

3b. 新增模块级函数：

```python
def _watched_providers(env: dict[str, str]) -> list[str]:
    slots = configured_key_slots(env)
    if slots:
        return [slot.provider for slot in slots]
    return [LEGACY_PROVIDER]


def _quota_by_provider(quota_path: str | Path, providers: list[str]) -> dict[str, int | None]:
    try:
        ledger = load_quota_ledger(quota_path).get("providers", {})
    except (OSError, ValueError):
        ledger = {}
    out: dict[str, int | None] = {}
    for provider in providers:
        value = (ledger.get(provider) or {}).get("remaining")
        out[provider] = value if isinstance(value, int) else None
    return out


def _build_quota_alert(
    before: dict[str, int | None],
    after: dict[str, int | None],
) -> dict | None:
    slot_reports = []
    for provider, remaining_after in after.items():
        remaining_before = before.get(provider)
        if remaining_before is None or remaining_after is None:
            continue
        crossed = sorted(
            t for t in QUOTA_ALERT_THRESHOLDS if remaining_before > t >= remaining_after
        )
        if crossed:
            slot_reports.append(
                {
                    "slot": _SLOT_LABELS.get(provider, provider),
                    "remaining": remaining_after,
                    "thresholds_crossed": crossed,
                }
            )
    if not slot_reports:
        return None
    lines = ["The Odds API 额度告警"]
    for report in slot_reports:
        if 0 in report["thresholds_crossed"]:
            lines.append(
                f"{report['slot']} 槽位已耗尽（剩余 {report['remaining']}），"
                "调度将自动切换备用槽位；全部耗尽时自动刷新暂停。"
            )
        else:
            lines.append(
                f"{report['slot']} 槽位剩余 {report['remaining']}"
                f"（已跌破 {max(report['thresholds_crossed'])}）。"
            )
    lines += [
        "处理：申请新免费 key 替换 .env 中耗尽槽位的",
        "THE_ODDS_API_KEY_PRIMARY / THE_ODDS_API_KEY_SECONDARY，",
        "再经确认执行一次 python3 -m worldcup.scheduled_publish --live --force",
        "让新额度写回 quota 台账（耗尽状态下调度不会自行恢复该槽位）。",
    ]
    lowest = min(report["remaining"] for report in slot_reports)
    return {
        "summary": f"The Odds API 额度告警：最低槽位剩余 {lowest}",
        "content": "\n".join(lines),
        "slots": slot_reports,
    }
```

3c. `run_scheduled_publish` 内：`env = _load_env(env_path)` 之后加：

```python
    watched_providers = _watched_providers(env) if notify else []
    quota_before = _quota_by_provider(quota_path, watched_providers) if notify else {}
```

两个提前 return（`skipped` 与 `blocked / empty_refreshed_snapshot`）的结果字典各加一项：

```python
            "quota_alert": None,
```

在发布成功后的通知段（`notification_result` 计算完成之后、最终 return 之前）加：

```python
    quota_alert_result = None
    if notify:
        alert = _build_quota_alert(
            quota_before, _quota_by_provider(quota_path, watched_providers)
        )
        if alert is not None:
            sent = notify_fn(alert["content"], summary=alert["summary"])
            quota_alert_result = {**sent, "slots": alert["slots"]}
```

最终 return 字典加：

```python
        "quota_alert": quota_alert_result,
```

- [ ] **Step 4: 运行确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全绿（净增 4 个测试）。既有 skipped / blocked / published / key-rotation 测试只断言特定键，新增 `quota_alert` 键不影响。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/scheduled_publish.py tests/test_scheduled_publish.py
git commit -m "feat: alert when key slot quota crosses thresholds"
```

---

### Task 2: 文档与收尾

**Files:**
- Modify: `README.md`（key 轮换运维要点同一条）
- Modify: `RECENT_WORK.md`（顶部追加一节）

- [ ] **Step 1: README 补充告警说明**

在"调度会按本地 quota ledger 保守轮换 …"那条运维要点末尾补一句：

> 任一槽位剩余额度跌破 100 / 30 / 10 / 0 时会随当轮发布自动发 WxPusher 额度告警（每个槽位每个阈值只发一次，跨 0 即槽位耗尽/自动切换提示；`--no-notify` 可静音）。

- [ ] **Step 2: 最终全量验证**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

Expected: 全绿；无空白错误。

- [ ] **Step 3: 更新 RECENT_WORK.md 并 commit（不 push）**

按既有格式在顶部追加"2026-06-11 槽位额度跨阈值告警"一节：监控口径（按 `.env` 配置的槽位，未配置回退 legacy）、阈值与跨 0 即切换提示、防刷屏语义、走 notify 总开关、测试计数，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record slot quota threshold alerts"
```

---

## 范围外（明确不做）

- 不自动换 key、不读写 `.env`、不打印 key 值。
- 不改 `choose_key_slot` 轮换逻辑本身。
- 不做每日额度消耗报表（`ops_check` 已能看当前值）。
- 不在请求失败时做同轮 fallback（轮换设计的既有 Non-Goal）。
- 不部署、不 push。
