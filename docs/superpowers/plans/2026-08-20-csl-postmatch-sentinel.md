# CSL Postmatch Sentinel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有中超双源赛果与 postmatch shadow 链路后增加本地、非阻断、可去重的样本与数据质量 sentinel，并只在新异常、恢复和正式样本首次达到 50 时通过 WxPusher 提醒。

**Architecture:** 新建 `worldcup.csl_postmatch_sentinel`，把严格输入校验与事件判定保持为纯函数，把锁、原子 state、outbox 和通知放在独立 runner 中。`worldcup.csl_scheduled_publish` 只在 accepted results 且 shadow `stored/unchanged` 后调用 runner；sentinel 摘要只存在于 scheduler-local 返回值，不进入 snapshot 或 HMAC publish body。

**Tech Stack:** Python 3.12 标准库、JSON、`fcntl.flock`、`tempfile.mkstemp`、`os.replace`、现有 `worldcup.notifications.send_wxpusher_notification`、项目自带 `tests/run_tests.py`。

**Spec:** `docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md`

## Global Constraints

- 只支持 `competition_id="csl_2026"`、`season="2026"`、report schema v1。
- standalone 默认 dry-run：不得创建目录、锁、state，不得通知。
- `notify=True` 必须同时 `write=True`；测试与普通本地验证不得调用真实 WxPusher。
- 当前 128 个 `missing_closing` 与 8 个 `missing_decision` 是首次启用基线，不发旧账通知，但不得从 coverage 隐藏。
- 只监控数据链路；hit rate、连胜连败、盘口方向和 `can_lift_club_rating_pending=false` 不产生事件。
- 样本达到 50 只提醒人工复盘，不自动调参、不解除 `club_rating_pending`。
- sentinel 失败不阻断 results、shadow、odds refresh、snapshot build 或 publish。
- 不调用 The Odds API、不读取赔率 key、不修改 quota ledger、不新增 LaunchAgent/定时器/数据库/endpoint。
- 不把 sentinel 字段写入 `decision`、`run.policy`、`data_quality`、snapshot、HMAC body、API、preview 或数据库。
- 本阶段只更新现有 `README.md`；不创建 `ARCHITECTURE.md`。
- `.env`、token、UID、secret、原始 provider/bookmaker payload、绝对私有路径和 traceback 不得进入 state、通知或安全返回。
- 每个任务的 commit 命令只有在用户另行给出当前阶段提交授权后执行；仅有“确认实现”时保留工作区修改，不 commit/push。

## File Map

- Create `worldcup/csl_postmatch_sentinel.py`: 输入契约、纯 evaluator、事件 ID、state/outbox、文件锁、原子写、CLI。
- Create `tests/test_csl_postmatch_sentinel.py`: evaluator、runner、并发、outbox、脱敏和 dry-run 测试。
- Modify `worldcup/csl_scheduled_publish.py`: 非阻断接入、safe summary、`--no-notify`。
- Modify `tests/test_csl_scheduled_publish.py`: 调用顺序、失败隔离、pending/source 阻断、CLI 静音、公开 payload 隔离。
- Modify `README.md`: 模块目录、触发边界、CLI、state 与研究限制。
- Modify `docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md`: 保持已确认的“不创建 ARCHITECTURE”范围。
- Modify `RECENT_WORK.md`: 在现有 2026-08-20 条目中补充实现与验证结果，不新增第 21 个标题。

---

### Task 1: Strict Input Contract and Pure Event Evaluator

**Files:**
- Create: `worldcup/csl_postmatch_sentinel.py`
- Create: `tests/test_csl_postmatch_sentinel.py`

**Interfaces:**
- Consumes: schema v1 `csl_postmatch_shadow.json` 与 `csl_closing_coverage.json` 的 Python dict。
- Produces: `SentinelValidationError`, `validate_postmatch_inputs(shadow_report, coverage_report)`, `evaluate_postmatch_sentinel(shadow_report=..., coverage_report=..., previous_state=..., observed_at=...)`；Task 2 的 runner 只通过这些接口取得规范化输入和下一状态。

- [ ] **Step 1: Write the report fixture and failing bootstrap/contract tests**

在 `tests/test_csl_postmatch_sentinel.py` 写入完整最小 fixture，不从真实 ignored 文件读取：

```python
from __future__ import annotations

from copy import deepcopy

from worldcup.csl_postmatch_sentinel import (
    SentinelValidationError,
    evaluate_postmatch_sentinel,
    validate_postmatch_inputs,
)


def _reports(*, decision_count: int = 38, missing_closing: int = 128,
             missing_decision: int = 8, finished_result_count: int | None = None,
             closing_available_count: int | None = None,
             generated_at: str = "2026-08-15T10:36:37Z"):
    tally = {"hit": decision_count, "miss": 0, "push": 0, "no_pick": 0}
    sample = {
        "actionable": decision_count,
        "decided": decision_count,
        "decision_count": decision_count,
        "hit_rate": 1.0 if decision_count else None,
        "min_sample": 50,
        "pick_rate": 1.0 if decision_count else 0.0,
        "sample_too_small": decision_count < 50,
    }
    closing_available = (
        decision_count + missing_decision
        if closing_available_count is None
        else closing_available_count
    )
    finished_results = (
        closing_available + missing_closing
        if finished_result_count is None
        else finished_result_count
    )
    coverage_block = {
        "finished_result_count": finished_results,
        "closing_available_count": closing_available,
        "decision_available_count": decision_count,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "legacy_decision_count": 0,
        "missing_closing_count": missing_closing,
        "missing_decision_count": missing_decision,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    matches = [
        {
            "match_id": f"csl_2026:missing:{index}",
            "settlement": {"status": "missing_closing"},
        }
        for index in range(missing_closing)
    ] + [
        {
            "match_id": f"csl_2026:no-decision:{index}",
            "settlement": {"status": "missing_decision"},
        }
        for index in range(missing_decision)
    ]
    shadow = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "a" * 64,
        "status": "ok",
        "decision_sample": sample,
        "decision_tally": tally,
        "decision_coverage": coverage_block,
        "matches": matches,
    }
    coverage = {
        "schema_version": 1,
        "competition_id": "csl_2026",
        "season": "2026",
        "generated_at": generated_at,
        "input_fingerprint": "b" * 64,
        "summary": {
            "finished_result_count": coverage_block["finished_result_count"],
            "missing_count": missing_closing,
            "observed_closing_count": coverage_block["closing_available_count"],
            "observed_current_decision_count": decision_count,
            "observed_missing_current_decision_count": missing_decision,
        },
        "performance": {
            "observed": {
                "decision_sample": deepcopy(sample),
                "decision_tally": deepcopy(tally),
                "official_headline_scope": "observed_schema_v2_match_pick_only",
            }
        },
        "matches": [],
    }
    return shadow, coverage


def test_validate_inputs_rejects_cross_report_mismatch():
    shadow, coverage = _reports()
    coverage["summary"]["observed_current_decision_count"] = 37
    try:
        validate_postmatch_inputs(shadow, coverage)
    except SentinelValidationError as exc:
        assert exc.code == "coverage_shadow_mismatch"
    else:
        raise AssertionError("mismatched reports must fail closed")


def test_first_evaluation_baselines_existing_128_and_8_without_alerting():
    shadow, coverage = _reports()
    result = evaluate_postmatch_sentinel(
        shadow_report=shadow,
        coverage_report=coverage,
        previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )
    assert result["events"] == []
    assert result["state"]["baseline_quality"] == {
        "missing_closing_count": 128,
        "missing_decision_count": 8,
        "identity_mismatch_count": 0,
        "invalid_decision_count": 0,
        "result_source_blocked_count": 0,
        "unresolved_count": 0,
    }
    assert result["state"]["high_water"]["decision_count"] == 38
```

- [ ] **Step 2: Run the focused tests and confirm the RED failure**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); ns['test_validate_inputs_rejects_cross_report_mismatch'](); ns['test_first_evaluation_baselines_existing_128_and_8_without_alerting']()"
```

Expected: FAIL while importing because `worldcup.csl_postmatch_sentinel` does not exist.

- [ ] **Step 3: Implement strict normalization and validation**

在新模块中定义明确常量、稳定错误类型和严格 getter；不得用 `int(value)` 接受字符串或 `bool`：

```python
REPORT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
DEFAULT_COMPETITION_ID = "csl_2026"
DEFAULT_SEASON = "2026"
DEFAULT_MIN_SAMPLE = 50
RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"

QUALITY_FIELDS = (
    "missing_closing_count",
    "missing_decision_count",
    "identity_mismatch_count",
    "invalid_decision_count",
    "result_source_blocked_count",
    "unresolved_count",
)
MONOTONIC_FIELDS = (
    "finished_result_count",
    "closing_available_count",
    "decision_available_count",
    "decision_count",
)


class SentinelValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _strict_count(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise SentinelValidationError(code)
    return value


def validate_postmatch_inputs(
    shadow_report: object,
    coverage_report: object,
) -> tuple[dict[str, object], dict[str, object]]:
    shadow = _validate_shadow_report(shadow_report)
    coverage = _validate_coverage_report(coverage_report)
    _validate_cross_report(shadow, coverage)
    return shadow, coverage
```

同一步实现 `_validate_shadow_report()`、`_validate_coverage_report()` 和 `_validate_cross_report()`：前两者返回只含规范字段的 deep copy，后一函数逐项比较 spec 6.2 的八个等式并在任一不等时抛 `SentinelValidationError("coverage_shadow_mismatch")`。字段缺失、类型错误、时间错误、hash 错误分别使用 `shadow_report_invalid`、`coverage_report_invalid`、`report_generated_at_invalid`、`report_fingerprint_invalid`，不把原异常文本放入 code。

用 `_parse_utc()` 统一转 UTC；SHA-256 必须匹配 `re.fullmatch(r"[0-9a-f]{64}", value)`。浮点字段必须是有限 `int/float` 且拒绝 `bool`。严格实现以下等式：

```python
decision_count == hit + miss + push + no_pick
decided == hit + miss
actionable == hit + miss + push
pick_rate == actionable / decision_count  # 0 denominator -> 0.0
hit_rate == hit / decided                 # 0 denominator -> None
sample_too_small == (decision_count < min_sample)
closing_available_count >= decision_available_count
finished_result_count >= closing_available_count
```

- [ ] **Step 4: Write failing transition, recovery, regression, threshold and outcome-neutral tests**

追加以下独立测试；质量扩大通过 count 和稳定 match ID 集合共同改变事件指纹：

```python
def test_new_gap_is_alerted_once_then_expansion_and_recovery_are_distinct():
    shadow, coverage = _reports()
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow, coverage_report=coverage, previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]

    expanded_shadow, expanded_coverage = _reports(
        missing_closing=129,
        finished_result_count=175,
        closing_available_count=46,
    )
    first = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow, coverage_report=expanded_coverage,
        previous_state=baseline, observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in first["events"]] == [
        "missing_closing_increased"
    ]

    unchanged = evaluate_postmatch_sentinel(
        shadow_report=expanded_shadow, coverage_report=expanded_coverage,
        previous_state=first["state"], observed_at="2026-08-20T02:00:00Z",
    )
    assert unchanged["events"] == []

    wider_shadow, wider_coverage = _reports(
        missing_closing=130,
        finished_result_count=176,
        closing_available_count=46,
    )
    wider = evaluate_postmatch_sentinel(
        shadow_report=wider_shadow, coverage_report=wider_coverage,
        previous_state=unchanged["state"], observed_at="2026-08-20T03:00:00Z",
    )
    assert [event["kind"] for event in wider["events"]] == ["anomaly"]

    recovered_shadow, recovered_coverage = _reports(
        decision_count=40,
        missing_closing=128,
        missing_decision=8,
        finished_result_count=176,
        closing_available_count=48,
    )
    recovered = evaluate_postmatch_sentinel(
        shadow_report=recovered_shadow, coverage_report=recovered_coverage,
        previous_state=wider["state"], observed_at="2026-08-20T04:00:00Z",
    )
    assert [event["kind"] for event in recovered["events"]] == ["recovery"]


def test_count_regression_keeps_high_water_until_recovery():
    shadow, coverage = _reports(decision_count=38)
    baseline = evaluate_postmatch_sentinel(
        shadow_report=shadow, coverage_report=coverage, previous_state=None,
        observed_at="2026-08-20T00:00:00Z",
    )["state"]
    lower_shadow, lower_coverage = _reports(decision_count=37)
    lower = evaluate_postmatch_sentinel(
        shadow_report=lower_shadow, coverage_report=lower_coverage,
        previous_state=baseline, observed_at="2026-08-20T01:00:00Z",
    )
    assert lower["state"]["high_water"]["decision_count"] == 38
    assert "decision_count_regressed" in {
        event["code"] for event in lower["events"]
    }


def test_threshold_crosses_once_and_hit_rate_change_does_not_alert():
    before_shadow, before_coverage = _reports(decision_count=49)
    state = evaluate_postmatch_sentinel(
        shadow_report=before_shadow, coverage_report=before_coverage,
        previous_state=None, observed_at="2026-08-20T00:00:00Z",
    )["state"]
    at_shadow, at_coverage = _reports(decision_count=50)
    crossed = evaluate_postmatch_sentinel(
        shadow_report=at_shadow, coverage_report=at_coverage,
        previous_state=state, observed_at="2026-08-20T01:00:00Z",
    )
    assert [event["code"] for event in crossed["events"]] == [
        "decision_sample_reached_minimum"
    ]
    changed = deepcopy(at_shadow)
    changed["decision_tally"] = {"hit": 25, "miss": 25, "push": 0, "no_pick": 0}
    changed["decision_sample"].update({"hit_rate": 0.5, "decided": 50})
    changed_coverage = deepcopy(at_coverage)
    changed_coverage["performance"]["observed"]["decision_tally"] = deepcopy(
        changed["decision_tally"]
    )
    changed_coverage["performance"]["observed"]["decision_sample"] = deepcopy(
        changed["decision_sample"]
    )
    second = evaluate_postmatch_sentinel(
        shadow_report=changed, coverage_report=changed_coverage,
        previous_state=crossed["state"], observed_at="2026-08-20T02:00:00Z",
    )
    assert second["events"] == []
```

- [ ] **Step 5: Run transition tests and confirm they fail for missing evaluator behavior**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); [ns[name]() for name in ('test_new_gap_is_alerted_once_then_expansion_and_recovery_are_distinct','test_count_regression_keeps_high_water_until_recovery','test_threshold_crosses_once_and_hit_rate_change_does_not_alert')]"
```

Expected: FAIL on missing event state/transition behavior; validation tests from Step 1 must already pass.

- [ ] **Step 6: Implement deterministic evaluator and event IDs**

Use canonical JSON (`sort_keys=True`, compact separators, UTF-8) and SHA-256. Event records contain only safe primitives:

```python
def _event_id(payload: dict[str, object]) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_postmatch_sentinel(
    *,
    shadow_report: dict[str, object],
    coverage_report: dict[str, object],
    previous_state: dict[str, object] | None,
    observed_at: str,
) -> dict[str, object]:
    shadow, coverage = validate_postmatch_inputs(shadow_report, coverage_report)
    current = _sentinel_projection(shadow, coverage)
    if previous_state is None:
        next_state, new_events = _initial_state(current, observed_at)
    else:
        previous = _validate_state(previous_state)
        next_state, new_events = _transition_state(previous, current, observed_at)
    return {"state": next_state, "events": new_events}
```

同一步实现 `_sentinel_projection()`、`_initial_state()`、`_validate_state()` 和 `_transition_state()`。`_initial_state()` 复制当前 quality 为 baseline、当前 monotonic 为 high-water；只有当前样本已达到 min_sample 才创建 threshold event。`_transition_state()` 对六个 quality condition 和四个 high-water condition 按本节已确认的扩大/幂等/恢复规则更新，最后单独处理 threshold；它不得读取 `hit_rate`、market 或 selection 来判断事件。

每个 active condition 用 condition key（例如 `quality:missing_closing_count`、`regression:decision_count`）索引；状态保存当前 event ID。相同 condition + 相同 count + 相同 match-ID digest 不创建新事件；扩大时替换 active event 并创建新 anomaly；回到 baseline/high-water 时创建一次 recovery 并移除 active condition。

- [ ] **Step 7: Run the whole focused sentinel test file**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import inspect, runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); [fn() for name, fn in sorted(ns.items()) if name.startswith('test_') and inspect.isfunction(fn)]"
```

Expected: all Task 1 tests return without exception.

- [ ] **Step 8: Commit Task 1 only when current-stage commit authorization exists**

```bash
git add worldcup/csl_postmatch_sentinel.py tests/test_csl_postmatch_sentinel.py
git commit -m "feat: evaluate csl postmatch sentinel events"
```

Without explicit commit authorization, record these exact files as the Task 1 checkpoint and continue without staging or committing.

---

### Task 2: Atomic State, Outbox, Notification, and Safe CLI

**Files:**
- Modify: `worldcup/csl_postmatch_sentinel.py`
- Modify: `tests/test_csl_postmatch_sentinel.py`

**Interfaces:**
- Consumes: Task 1 `validate_postmatch_inputs()` and `evaluate_postmatch_sentinel()`.
- Produces: `run_csl_postmatch_sentinel(...) -> dict[str, Any]`, `main(argv, runner=run_csl_postmatch_sentinel) -> int`; Task 3 injects the runner into the scheduler.

- [ ] **Step 1: Write failing dry-run, baseline persistence, suppression and retry tests**

Add JSON file helpers that write the `_reports()` fixture into a temporary root. Tests must inject notification fakes:

```python
def test_runner_dry_run_creates_no_state_lock_or_notification():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        before = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
        calls = []
        result = run_csl_postmatch_sentinel(
            root=root,
            observed_at="2026-08-20T00:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True),
        )
        after = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
    assert result["status"] == "dry_run_ready"
    assert result["event_count"] == 0
    assert before == after
    assert calls == []


def test_runner_suppresses_new_event_when_notify_false_and_never_backfills_it():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(root=root, write=True, notify=False,
                                   observed_at="2026-08-20T00:00:00Z")
        _write_reports(root, missing_closing=129)
        suppressed = run_csl_postmatch_sentinel(
            root=root, write=True, notify=False,
            observed_at="2026-08-20T01:00:00Z",
        )
        calls = []
        repeated = run_csl_postmatch_sentinel(
            root=root, write=True, notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True) or {"status": "sent"},
        )
    assert suppressed["notification_status"] == "suppressed"
    assert repeated["status"] == "unchanged"
    assert calls == []


def test_failed_notification_remains_pending_and_retries_on_unchanged_input():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_reports(root)
        run_csl_postmatch_sentinel(root=root, write=True, notify=False,
                                   observed_at="2026-08-20T00:00:00Z")
        _write_reports(root, missing_closing=129)
        failed = run_csl_postmatch_sentinel(
            root=root, write=True, notify=True,
            observed_at="2026-08-20T01:00:00Z",
            notify_fn=lambda *_args, **_kwargs: {"status": "failed", "exit_code": 1},
        )
        calls = []
        retried = run_csl_postmatch_sentinel(
            root=root, write=True, notify=True,
            observed_at="2026-08-20T02:00:00Z",
            notify_fn=lambda *_args, **_kwargs: calls.append(True) or {"status": "sent"},
        )
    assert failed["notification_status"] == "failed"
    assert retried["notification_status"] == "sent"
    assert calls == [True]
```

Also assert `run_csl_postmatch_sentinel(write=False, notify=True)` raises `ValueError("notify_requires_write")` before creating a lock.

- [ ] **Step 2: Run Task 2 runner tests and confirm RED**

Run the new functions with the Task 1 focused runner command.

Expected: FAIL because `run_csl_postmatch_sentinel` and persistent outbox behavior are absent.

- [ ] **Step 3: Implement paths, lock, atomic state validation, safe summaries and notification batches**

Add exact defaults:

```python
DEFAULT_SHADOW_REPORT = "data/local/diagnostics/csl_postmatch_shadow.json"
DEFAULT_COVERAGE_REPORT = "data/local/diagnostics/csl_closing_coverage.json"
DEFAULT_STATE = "data/local/diagnostics/csl_postmatch_sentinel_state.json"
DEFAULT_LOCK = "data/local/diagnostics/csl_postmatch_sentinel.lock"
```

Implementation rules:

```python
def run_csl_postmatch_sentinel(
    *,
    root: str | Path = ".",
    shadow_path: str | Path = DEFAULT_SHADOW_REPORT,
    coverage_path: str | Path = DEFAULT_COVERAGE_REPORT,
    state_path: str | Path = DEFAULT_STATE,
    lock_path: str | Path = DEFAULT_LOCK,
    observed_at: str | None = None,
    write: bool = False,
    notify: bool = False,
    notify_fn: Callable[..., dict[str, Any]] = send_wxpusher_notification,
) -> dict[str, Any]:
    if notify and not write:
        raise ValueError("notify_requires_write")
    paths = _resolve_paths(root, shadow_path, coverage_path, state_path, lock_path)
    if not write:
        return _run_dry(paths, observed_at=observed_at)
    with _exclusive_lock(paths["lock"]):
        return _run_locked(
            paths,
            observed_at=observed_at,
            notify=notify,
            notify_fn=notify_fn,
        )
```

同一步实现 `_resolve_paths()`、`_run_dry()`、`_exclusive_lock()` 和 `_run_locked()`。`_run_dry()` 只用 `Path.read_bytes()`、严格 JSON decode、Task 1 evaluator 和 safe-summary builder；不得调用 `mkdir` 或打开 lock。`_run_locked()` 在锁内重读全部输入，先提交 pending/suppressed state，再按 notify flag 发送单条 batch，最后提交 sent/failed 状态。

Use `fcntl.flock(LOCK_EX)` only when `write=True`. `_write_state_atomic()` must write same-directory temp, flush/fsync, reopen and call `_validate_state()`, then `os.replace`; on failure delete only its own temp. State unreadable returns:

```python
{
    "status": "error",
    "reason": "sentinel_state_unreadable",
    "error_type": "SentinelValidationError",
    "event_count": 0,
    "notification_status": "not_attempted",
}
```

It must preserve original bytes and never call `notify_fn`. Input report validation errors with a valid state create/reuse one safe `input:<reason>` anomaly in outbox; empty/malformed input must never be converted to zero counters or recovery. When a later run validates both reports, close that input-error condition and create one recovery event.

A recovery whose originating anomaly was `suppressed` must also be `suppressed`; do not send a recovery for an anomaly the user intentionally silenced. If the same condition expands after notifications are re-enabled, the new anomaly version is eligible to send and its later recovery is also eligible to send.

Notification content is built from safe event records, sorted by severity/code/event ID, capped at five detail lines, and ends with `仅用于研究分析，不构成投注建议。`. Call the existing adapter exactly as:

```python
notify_fn(content, summary="中超赛后数据监控提醒")
```

Accept only `{"status": "sent"}` as delivery success. Do not store adapter stdout/stderr, command, UID or response body.

- [ ] **Step 4: Write failing corruption, redaction, atomic failure and concurrent dedup tests**

Add tests that:

- corrupt an existing state file, preserve its exact bytes, and assert `notify_fn` is not called;
- inject a writer/replace failure and assert the previous state bytes remain unchanged;
- put `secret`, `api_key`, `bookmakers`, an absolute private path and traceback text in malformed input, then recursively assert none appear in result/state/notification;
- fork two processes against the same changed reports, collect notification calls through two result files, and assert only one process creates/sends the event;
- call CLI with no flags and assert exit 0, JSON `dry_run_ready`, no state/lock;
- call CLI `--write --notify` only as `main(["--write", "--notify"], runner=fake_runner)`，其中 fake runner 断言参数后返回安全 `stored` 摘要，保证不执行真实 WxPusher。

Concurrent expected state must contain one event ID and a valid JSON object; no `.tmp` file remains.

- [ ] **Step 5: Run new durability tests and confirm RED**

Name the tests exactly:

- `test_corrupt_state_is_preserved_and_notification_is_not_attempted`
- `test_atomic_state_failure_preserves_previous_bytes`
- `test_state_result_and_notification_redact_sensitive_input`
- `test_concurrent_runners_create_and_send_one_event`
- `test_cli_defaults_to_zero_write_dry_run`
- `test_cli_notify_uses_injected_runner`

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); [ns[name]() for name in ('test_corrupt_state_is_preserved_and_notification_is_not_attempted','test_atomic_state_failure_preserves_previous_bytes','test_state_result_and_notification_redact_sensitive_input','test_concurrent_runners_create_and_send_one_event','test_cli_defaults_to_zero_write_dry_run','test_cli_notify_uses_injected_runner')]"
```

Expected: FAIL on at least state preservation/concurrent dedup before durability implementation is complete.

- [ ] **Step 6: Complete durability and CLI implementation**

CLI flags:

```python
parser.add_argument("--root", default=".")
parser.add_argument("--shadow-path", default=DEFAULT_SHADOW_REPORT)
parser.add_argument("--coverage-path", default=DEFAULT_COVERAGE_REPORT)
parser.add_argument("--state-path", default=DEFAULT_STATE)
parser.add_argument("--lock-path", default=DEFAULT_LOCK)
parser.add_argument("--observed-at", default=None)
parser.add_argument("--write", action="store_true")
parser.add_argument("--notify", action="store_true")
```

`main()` prints only sorted safe JSON and returns 0 for `dry_run_ready/stored/unchanged`, 2 for `error/blocked`. Do not catch `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` or `BaseException` at the public boundary.

- [ ] **Step 7: Run all sentinel tests and compile the module**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import inspect, runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); [fn() for name, fn in sorted(ns.items()) if name.startswith('test_') and inspect.isfunction(fn)]"
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/csl_postmatch_sentinel.py tests/test_csl_postmatch_sentinel.py
```

Expected: every sentinel test passes; `py_compile` exits 0.

- [ ] **Step 8: Commit Task 2 only when current-stage commit authorization exists**

```bash
git add worldcup/csl_postmatch_sentinel.py tests/test_csl_postmatch_sentinel.py
git commit -m "feat: persist csl sentinel notifications"
```

Without commit authorization, leave the verified Task 2 changes uncommitted.

---

### Task 3: Non-Blocking CSL Scheduler Wiring and `--no-notify`

**Files:**
- Modify: `worldcup/csl_scheduled_publish.py`
- Modify: `tests/test_csl_scheduled_publish.py`

**Interfaces:**
- Consumes: `run_csl_postmatch_sentinel(root=<Path>, observed_at=<UTC str>, write=True, notify=<bool>)`.
- Produces: scheduler-local `postmatch_sentinel` safe summary and CLI `--no-notify`; public snapshot/publish contracts remain byte-shape compatible except their ordinary timestamps/run IDs.

- [ ] **Step 1: Extend existing live integration test with a failing sentinel order assertion**

In `test_live_force_refreshes_builds_snapshot_and_publishes`, add `sentinel` to counters/events and inject:

```python
def fake_sentinel(**kwargs):
    calls["sentinel"] += 1
    events.append("sentinel")
    assert Path(kwargs["root"]) == root
    assert kwargs["write"] is True
    assert kwargs["notify"] is True
    assert kwargs["observed_at"] == "2026-07-10T10:30:00+00:00"
    return {
        "status": "stored",
        "competition_id": "csl_2026",
        "event_count": 0,
        "notification_status": "not_attempted",
    }
```

Pass `postmatch_sentinel_fn=fake_sentinel`, `postmatch_sentinel_root=root`; expect `events[:5] == ["results", "coverage", "shadow", "sentinel", "odds"]` and `result["postmatch_sentinel"]["status"] == "stored"`.

- [ ] **Step 2: Run the live integration test and confirm RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import runpy; ns=runpy.run_path('tests/test_csl_scheduled_publish.py'); ns['test_live_force_refreshes_builds_snapshot_and_publishes']()"
```

Expected: FAIL because `run_csl_scheduled_publish` does not accept `postmatch_sentinel_fn`.

- [ ] **Step 3: Add typed dependency, safe projection and success-only invocation**

Add:

```python
from worldcup.csl_postmatch_sentinel import run_csl_postmatch_sentinel

PostmatchSentinelFn = Callable[..., dict[str, Any]]


def _safe_postmatch_sentinel(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_postmatch_sentinel_result"}
    safe = {"status": str(value.get("status") or "error")}
    for key in ("reason", "competition_id", "notification_status", "error_type"):
        if value.get(key) is not None:
            safe[key] = str(value[key])
    count = value.get("event_count")
    if type(count) is int and count >= 0:
        safe["event_count"] = count
    return safe
```

Extend runner parameters:

```python
postmatch_sentinel_fn: PostmatchSentinelFn = run_csl_postmatch_sentinel,
postmatch_sentinel_root: str | Path = ".",
notify: bool = True,
```

Only call after `_safe_postmatch_shadow(value)` returns status in `{"stored", "unchanged"}`. Catch only ordinary runtime/data exceptions (`OSError`, `ValueError`, `TimeoutError`, `RuntimeError`) and return `csl_postmatch_sentinel_failed`; do not leak `str(exc)`. For source blocked/shadow error, use a local `not_run` reason and never call the injected function.

Add `postmatch_sentinel` only to scheduler-local return dicts. Do not add it to `decision`, `_attach_run_metadata`, `_runner_diagnostic`, `data_quality` or `built`.

- [ ] **Step 4: Write failing isolation, retry, CLI and public-body tests**

Add or extend tests for all branches:

```python
def test_sentinel_failure_does_not_block_odds_publish_or_leak_message():
    def broken_sentinel(**_kwargs):
        raise RuntimeError("private sentinel path and token")

    case = _run_live_sentinel_case(postmatch_sentinel_fn=broken_sentinel)
    assert case["result"]["status"] == "published"
    assert case["calls"]["refresh"] == 1
    assert case["calls"]["publish"] == 1
    assert case["result"]["postmatch_sentinel"] == {
        "status": "error",
        "reason": "csl_postmatch_sentinel_failed",
        "error_type": "RuntimeError",
    }
    serialized = json.dumps(
        {"result": case["result"], "published": case["published"]}
    )
    assert "private sentinel path" not in serialized
    assert "postmatch_sentinel" not in json.dumps(case["published"])


def test_no_notify_cli_only_disables_sentinel():
    import io
    from contextlib import redirect_stdout
    from unittest.mock import patch
    from worldcup.csl_scheduled_publish import main as csl_main

    captured = {}
    with patch("worldcup.csl_scheduled_publish.run_csl_scheduled_publish") as run:
        run.side_effect = lambda **kwargs: captured.update(kwargs) or {"status": "dry_run"}
        with redirect_stdout(io.StringIO()):
            assert csl_main(["--no-notify"]) == 0
    assert captured["notify"] is False
```

Create `_run_live_sentinel_case(*, postmatch_sentinel_fn, notify=True, results_status="updated", shadow_status="stored", pending=False)` in the same test file with this fixed contract: a temporary root containing a quota fixture; accepted results by default; safe coverage; stored shadow by default; fetched odds with injected quota metadata; one-match snapshot builder; and a publish fake that appends the fully decoded staged snapshot to `published`. Return `{"result": result, "calls": calls, "events": events, "published": published}` after the temporary files have been decoded. Every branch test changes only these parameters, so provider/publish behavior remains observable and deterministic.

Also assert:

- accepted source + shadow `unchanged` invokes sentinel so pending outbox can retry;
- source blocked does not invoke shadow or sentinel;
- shadow error does not invoke sentinel;
- pending publish retry does not invoke results/shadow/sentinel/odds;
- `notify=False` reaches sentinel but does not change refresh/builder/publish calls;
- captured final snapshot and `publish_fn` snapshot body recursively contain no key/value with `postmatch_sentinel`, event content, state path, notification summary or private marker;
- a sentinel fake that tries to return extra secret/path keys is reduced by `_safe_postmatch_sentinel`.

- [ ] **Step 5: Run scheduler sentinel tests and confirm RED**

Name the new tests exactly:

- `test_sentinel_failure_does_not_block_odds_publish_or_leak_message`
- `test_unchanged_shadow_runs_sentinel_for_pending_outbox_retry`
- `test_blocked_result_source_does_not_run_sentinel`
- `test_shadow_error_does_not_run_sentinel`
- `test_pending_publish_retry_does_not_run_sentinel`
- `test_no_notify_cli_only_disables_sentinel`
- `test_sentinel_summary_never_enters_published_snapshot`

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import runpy; ns=runpy.run_path('tests/test_csl_scheduled_publish.py'); [ns[name]() for name in ('test_live_force_refreshes_builds_snapshot_and_publishes','test_sentinel_failure_does_not_block_odds_publish_or_leak_message','test_unchanged_shadow_runs_sentinel_for_pending_outbox_retry','test_blocked_result_source_does_not_run_sentinel','test_shadow_error_does_not_run_sentinel','test_pending_publish_retry_does_not_run_sentinel','test_no_notify_cli_only_disables_sentinel','test_sentinel_summary_never_enters_published_snapshot')]"
```

Expected: FAIL until local-only return propagation, blocked branches and CLI flag are implemented.

- [ ] **Step 6: Complete all return branches and CLI flag**

Add:

```python
parser.add_argument(
    "--no-notify",
    action="store_true",
    help="Disable local CSL postmatch sentinel notifications only.",
)
```

Pass `notify=not args.no_notify` into `run_csl_scheduled_publish`. Every live return after result refresh carries the safe local `postmatch_sentinel`; dry-run and pending retry use explicit `not_run` summaries without invoking the sentinel. Ensure a sentinel failure never appends a public `data_quality` warning.

- [ ] **Step 7: Run focused scheduler and sentinel suites**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import inspect, runpy; ns=runpy.run_path('tests/test_csl_postmatch_sentinel.py'); [fn() for name, fn in sorted(ns.items()) if name.startswith('test_') and inspect.isfunction(fn)]"
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import inspect, runpy; ns=runpy.run_path('tests/test_csl_scheduled_publish.py'); [fn() for name, fn in sorted(ns.items()) if name.startswith('test_') and inspect.isfunction(fn)]"
```

Expected: both files complete without exception; no real provider/publish/notification function is called because tests inject fakes.

- [ ] **Step 8: Commit Task 3 only when current-stage commit authorization exists**

```bash
git add worldcup/csl_scheduled_publish.py tests/test_csl_scheduled_publish.py
git commit -m "feat: monitor csl postmatch samples"
```

Without commit authorization, leave the verified files uncommitted.

---

### Task 4: Documentation, Real Dry-Run, and Completion Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md`
- Modify: `RECENT_WORK.md`
- Verify: `worldcup/csl_postmatch_sentinel.py`
- Verify: `worldcup/csl_scheduled_publish.py`
- Verify: `tests/test_csl_postmatch_sentinel.py`
- Verify: `tests/test_csl_scheduled_publish.py`

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: operator-facing usage and fresh evidence that the implementation is local-only, zero-quota and public-payload safe.

- [ ] **Step 1: Update README with exact operator boundaries**

Update the module tree near `csl_postmatch_shadow.py` and the CSL postmatch section. Include these exact facts:

- `worldcup.csl_postmatch_sentinel` defaults to dry-run and reads local shadow/coverage only;
- automatic trigger is accepted dual-source result → shadow success → sentinel → odds;
- state path is `data/local/diagnostics/csl_postmatch_sentinel_state.json`;
- current historical 128/8 is baseline, not repaired coverage;
- only anomaly/expansion/recovery and first `decision_count >= 50` notify;
- hit rate and pick direction never trigger;
- `csl_scheduled_publish --no-notify` silences only sentinel;
- no new timer, provider call, quota, public payload, auto-tuning or pending lift;
- standalone commands:

```bash
# Zero-write inspection
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_sentinel

# Local state activation without phone notification; requires separate approval
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_sentinel --write
```

Do not create `ARCHITECTURE.md`.

- [ ] **Step 2: Update the existing RECENT_WORK entry without adding a heading**

Replace “尚未写实现代码” with actual file list and focused/full verification counts. Keep the entry explicit that no real notification, provider, `.env`, push or deploy occurred. Confirm `rg -c '^## ' RECENT_WORK.md` remains exactly 20.

- [ ] **Step 3: Run the real standalone dry-run and prove zero sentinel writes**

Use a safe hash/existence guard that works whether state already exists or not:

```bash
sentinel_state=data/local/diagnostics/csl_postmatch_sentinel_state.json
sentinel_lock=data/local/diagnostics/csl_postmatch_sentinel.lock
before_state=$(test -f "$sentinel_state" && shasum -a 256 "$sentinel_state" | awk '{print $1}' || printf absent)
before_lock=$(test -f "$sentinel_lock" && shasum -a 256 "$sentinel_lock" | awk '{print $1}' || printf absent)
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_sentinel
after_state=$(test -f "$sentinel_state" && shasum -a 256 "$sentinel_state" | awk '{print $1}' || printf absent)
after_lock=$(test -f "$sentinel_lock" && shasum -a 256 "$sentinel_lock" | awk '{print $1}' || printf absent)
test "$before_state" = "$after_state"
test "$before_lock" = "$after_lock"
```

Expected JSON: `status=dry_run_ready`, `decision_count` reflects the current local report, `sample_too_small=true` until 50. No WxPusher command is invoked.

- [ ] **Step 4: Stop for separate approval before activating the real ignored baseline**

Do not run the following command under “确认实现” alone because it writes `data/local/diagnostics/`:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_sentinel --write
```

After explicit approval, expect `status=stored`, historical 128/8 produces `event_count=0`, `notification_status=not_attempted`, and state contains no sensitive keys. Repeat once and expect `status=unchanged`. Never add `--notify` during local acceptance unless the user separately confirms a real WxPusher send.

- [ ] **Step 5: Run fresh focused and full verification**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile \
  worldcup/csl_postmatch_sentinel.py \
  worldcup/csl_scheduled_publish.py \
  tests/test_csl_postmatch_sentinel.py \
  tests/test_csl_scheduled_publish.py

/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  tests/run_tests.py

git diff --check
test "$(rg -c '^## ' RECENT_WORK.md)" -eq 20
! rg -n 'T[B]D|T[O]DO|implement[[:space:]]+later|fill[[:space:]]+in[[:space:]]+details' \
  docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md \
  docs/superpowers/plans/2026-08-20-csl-postmatch-sentinel.md
```

Expected: `py_compile` and full runner exit 0, zero failed tests, only the existing allowlisted optional FastAPI skip if that dependency remains unavailable, diff check clean, 20 RECENT_WORK headings, no placeholders.

- [ ] **Step 6: Run a final scope and sensitive-data audit**

```bash
git status --short
git diff --name-only
git diff -- \
  worldcup/csl_postmatch_sentinel.py \
  worldcup/csl_scheduled_publish.py \
  tests/test_csl_postmatch_sentinel.py \
  tests/test_csl_scheduled_publish.py \
  README.md \
  docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md \
  RECENT_WORK.md

! rg -n '(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)' \
  worldcup/csl_postmatch_sentinel.py \
  tests/test_csl_postmatch_sentinel.py \
  README.md RECENT_WORK.md
```

Verify no `ARCHITECTURE.md`, `.env`, cache/local artifact, LaunchAgent, dependency, database, deployment or unrelated file is changed. Re-read the captured publish-body tests before claiming the public contract is unchanged.

- [ ] **Step 7: Commit documentation/completion only when current-stage commit authorization exists**

```bash
git add README.md RECENT_WORK.md \
  docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md
git commit -m "docs: document csl postmatch sentinel"
```

If Tasks 1–3 were intentionally left uncommitted because authorization was absent, do not make a docs-only commit that strands the implementation; wait for the explicit commit stage and commit the reviewed change set with transparent boundaries.

## Execution Handoff Checks

Before starting Task 1:

- confirm the user has explicitly authorized implementation;
- use an isolated worktree at execution time if the chosen execution skill requires it;
- preserve unrelated dirty changes;
- do not treat plan approval as commit, push, merge, notification, local diagnostic write or deployment authorization;
- stop and re-confirm if the real report schema, scheduler trigger status, public payload boundary or notification semantics differs from this plan.
