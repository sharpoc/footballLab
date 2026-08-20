# 中超赛后样本 Sentinel 与异常通知设计

日期：2026-08-20

状态：已确认设计，待实施计划

适用赛事：`csl_2026`

## 1. 背景

中超现有本地赛后链路已经能够在 7M 与中足联官方公开接口的日期、主客队和比分全量一致后，接受 2026 赛季赛果，选择开赛前最后一份合法 closing，结算当前 schema v2 `MATCH_PICK`，并更新 postmatch shadow、eval、backtest 和 pending gate 产物。

2026-08-20 只读检查时，最新 `csl_postmatch_shadow.json` 生成于 `2026-08-15T10:36:37.940466+00:00`，包含 174 场已验证结果、46 场 closing、38 个正式 decision 样本，结算为 20 hit、18 miss，`min_sample=50` 且 `sample_too_small=true`。coverage 同时保留 128 场历史 closing 缺口和 8 场 decision 缺口。

当前风险不是缺少新的选球规则，而是赛后样本或数据质量发生变化时，需要人工打开多个本地产物才能发现。若直接依据 38 场样本调整模型，容易被小样本和阶段性赛果拉偏；若不监控，closing 缺失、计数倒退或结算不一致又可能长期无人发现。

因此本阶段增加一个只服务于本地赛后研究链路的 sentinel：只在现有双源赛果触发后检查数据质量，异常或恢复时发送一次 WxPusher 通知，并在正式样本首次达到 50 时提醒启动人工复盘。

## 2. 目标

1. 复用现有“双源赛果接受 → postmatch shadow”触发，不新增定时器或 LaunchAgent。
2. 以当前已知缺口作为启用基线，只提醒之后的新异常、异常扩大和恢复。
3. 检测报告不可读、契约不一致、质量计数新增、正式样本倒退和 tally 不自洽。
4. 同一异常不重复轰炸；异常扩大视为新事件，恢复只通知一次。
5. 正式 `decision_count` 首次达到 50 时只通知一次“可以开始正式复盘”。
6. 通知或 sentinel 失败不得阻断赛果、赔率刷新、有效首选构建或发布。
7. 保证 sentinel 不进入公开 snapshot、HMAC body、API、preview 或数据库。

## 3. 非目标

本阶段明确不做：

- 不修改 `match_pick_v3` 的市场/模型权重、阈值、方向或风险扣分；
- 不根据命中率下降、连败、盘口类型或单轮结果发送策略报警；
- 不删除低置信比赛以提高表面命中率；
- 不自动调参，不自动解除 `club_rating_pending`；
- 不把样本达到 50 解释为模型可靠或达到生产调整标准；
- 不修复或回填现有 128 场 closing 缺口和 8 场 decision 缺口；
- 不新增 The Odds API 请求，不读取赔率 key，不修改 quota ledger；
- 不新增 scheduler、LaunchAgent、消息队列、数据库表或线上 endpoint；
- 不改变公开 snapshot、HMAC body、API、preview、静态站点或 ECS 数据；
- 不把 sentinel 失败升级为赛前链路的阻断条件。

## 4. 方案选择

### 4.1 方案 A：独立 sentinel 模块接入现有赛后链路

新增纯判定器、本地状态/outbox 和通知适配边界，由现有中超 scheduled publish 在成功运行 postmatch shadow 后调用。

优点是统计判断、文件状态和外部通知职责分离，易于单测和 fail-safe；不会把通知逻辑塞进 shadow 结算模块。这是采用方案。

### 4.2 方案 B：直接把通知写入 postmatch shadow

文件较少，但会把结算、报告提交、去重状态和外部进程调用耦合在一起。通知失败路径更容易影响 shadow 的原子提交语义，因此不采用。

### 4.3 方案 C：扩展 `ops_check` 或新增独立定时巡检

可以脱离赛果链路重复检查，但会扩大巡检范围或新增计时器，与“不额外调度、不额外消耗资源”的确认边界不符，因此不采用。

## 5. 架构与职责

### 5.1 触发顺序

live 中超链路顺序调整为：

1. 中足联官方与 7M 双源赛果刷新；
2. 只有结果刷新安全状态为 `updated` 或 `verified` 时运行 postmatch shadow；
3. postmatch shadow 返回受支持的成功状态后运行 sentinel；
4. 原有 The Odds API refresh、snapshot 构建和 publish 继续。

sentinel 不在以下路径运行：

- scheduler dry-run；
- 双源赛果 blocked、冲突、回退或抓取失败；
- pending publish retry；
- postmatch shadow 抛异常或返回错误；
- 单独的公开查询、preview 或静态导出。

postmatch shadow 返回 `stored` 或 `unchanged` 时均允许 sentinel 检查。`unchanged` 检查可以重试 outbox 中上次发送失败的事件，但不能重新创建已经确认过的事件。

### 5.2 独立模块

新增 `worldcup.csl_postmatch_sentinel`，提供两个边界：

- 纯判定器：输入当前 shadow、coverage、上次有效状态和当前时间，返回事件、下一状态及安全摘要；不读写文件、不联网。
- runner：负责加载和严格校验本地 JSON、排他锁、原子提交状态、调用注入式通知函数并返回脱敏结果。

公开接口保持依赖可注入：

```python
evaluate_postmatch_sentinel(
    *,
    shadow_report: dict,
    coverage_report: dict,
    previous_state: dict | None,
    observed_at: str,
) -> dict

run_csl_postmatch_sentinel(
    *,
    root: str | Path = ".",
    shadow_path: str | Path | None = None,
    coverage_path: str | Path | None = None,
    state_path: str | Path | None = None,
    observed_at: str | None = None,
    write: bool = False,
    notify: bool = False,
    notify_fn: Callable = send_wxpusher_notification,
) -> dict
```

CLI 默认 `write=False`、`notify=False`，只输出候选事件摘要且零写入。`notify=True` 必须要求 `write=True`，避免通知已发送但没有持久化去重状态。

### 5.3 Scheduler 接入

`worldcup.csl_scheduled_publish.run_csl_scheduled_publish()` 增加注入式 `postmatch_sentinel_fn` 和通知控制参数。CLI 新增 `--no-notify`：

- live 默认允许 sentinel 发送通知；
- `--no-notify` 只令 sentinel 静音；
- 不改变赛果刷新、shadow、odds refresh、quota、snapshot 或 publish；
- 静音事件记录为 `suppressed`，以后不补发同一旧事件；异常扩大或出现新事件仍可在恢复通知能力后的新一轮触发。

sentinel 的安全摘要只允许放在 scheduler 本地返回对象中，不得加入 `decision`、`run.policy`、`data_quality`、snapshot 或 publish body。公开 payload 测试必须捕获实际传给 HMAC publish 的 body，证明没有 sentinel 字段。

## 6. 输入契约

### 6.1 Shadow 报告

必须满足：

- 顶层为 JSON object；
- `schema_version` 为当前受支持版本；
- `competition_id == "csl_2026"`；
- `season == "2026"`；
- `status == "ok"`；
- `generated_at` 为可解析 UTC 时间；
- `decision_sample`、`decision_tally` 和 `decision_coverage` 为 object；
- 所有计数字段为非负整数且不得接受 `bool`；
- `min_sample` 为正整数；
- `sample_too_small` 与 `decision_count < min_sample` 一致。

### 6.2 Coverage 报告

必须满足：

- 顶层为 JSON object；
- schema、赛事和赛季与 shadow 一致；
- `generated_at` 可解析且与同轮 shadow 的 `generated_at` 完全一致；
- `summary.finished_result_count == shadow.decision_coverage.finished_result_count`；
- `summary.observed_closing_count == shadow.decision_coverage.closing_available_count`；
- `summary.observed_current_decision_count == shadow.decision_coverage.decision_available_count`；
- `summary.observed_missing_current_decision_count == shadow.decision_coverage.missing_decision_count`；
- `summary.missing_count == shadow.decision_coverage.missing_closing_count`；
- `performance.observed.decision_sample` 与 shadow 的 `decision_sample` 规范化后完全一致；
- `performance.observed.decision_tally` 与 shadow 的 `decision_tally` 规范化后完全一致；
- `performance.observed.official_headline_scope == "observed_schema_v2_match_pick_only"`；
- 文件不可读、JSON 损坏或契约不符时返回稳定错误码，不把空对象解释为“没有异常”。

两份报告的 `input_fingerprint` 来自不同输入投影，不要求相等；只分别校验为 64 位 lowercase SHA-256，并把二者共同纳入 sentinel 输入指纹。

## 7. 基线、水位线与异常规则

### 7.1 首次基线

第一次成功 write：

- 保存当前质量计数、单调计数、正式样本数和报告指纹；
- 当前 `missing_closing_count=128` 与 `missing_decision_count=8` 作为已知历史基线；
- 不为已有缺口补发启动通知；
- 若首次启用时 `decision_count >= min_sample`，仍生成一次样本门槛事件。

历史缺口继续保留在 coverage 报告中。sentinel 的“不提醒旧账”不得改写为“旧问题已经解决”。

### 7.2 数据质量事件

下列质量计数相对最近有效水位新增时产生事件：

- `missing_closing_count`；
- `missing_decision_count`；
- `identity_mismatch_count`；
- `invalid_decision_count`；
- `result_source_blocked_count`；
- `unresolved_count`。

同一事件指纹不变时不重复发送。若新增受影响比赛、计数继续增加或详情摘要变化，视为异常扩大，生成新的事件版本。计数恢复到异常前水位时，生成一次恢复事件。

若报告能提供稳定 match identity，事件详情优先使用排序后的安全 match IDs 计算摘要；不得在通知状态中保存原始 provider payload、赔率或完整比赛对象。若只能得到聚合计数，则使用字段名、基线值和当前值计算稳定事件指纹。

### 7.3 单调倒退事件

以下数值不得低于上次成功确认的高水位：

- `finished_result_count`；
- `closing_available_count`；
- `decision_available_count`；
- `decision_sample.decision_count`。

发生倒退时报警；恢复到原高水位时通知恢复。异常期间不得降低保存的高水位，否则下一轮会把数据丢失误当成新基线。

### 7.4 内部一致性事件

至少校验：

- `hit + miss + push + no_pick == decision_count`；
- `decision_available_count == decision_count`，除非现有正式契约有明确、已测试的 push/no-pick差异；
- `closing_available_count >= decision_available_count`；
- `finished_result_count >= closing_available_count`；
- `actionable`、`decided`、`decision_count` 和 `pick_rate` 的关系符合现有 report builder 契约；
- `sample_too_small == (decision_count < min_sample)`。

实现前必须用现有报告生成器和测试夹具确认精确等式；若现有契约允许例外，应在实现计划中写成显式规则并补反例测试，不能为了让测试通过静默放宽。

### 7.5 报告质量事件

以下情况生成稳定的本地错误结果，并在状态可安全读取时进入通知 outbox：

- shadow 或 coverage 文件缺失；
- JSON 不可读；
- schema、competition、season 或 status 不合法；
- 必需字段缺失或类型错误；
- 两份报告时间/指纹关系违反现有生成链路契约。

不得用空列表或零计数替代不可读输入，否则会制造错误恢复或覆盖历史基线。

### 7.6 样本门槛事件

当 `previous_decision_count < min_sample` 且 `current_decision_count >= min_sample` 时，发送一次“中超正式样本达到 50，可以开始人工复盘”。状态保存门槛已通知标记，后续计数继续增长不重复发送。

该事件不改变 pending gate，不触发参数修改。若样本随后倒退，单独按“单调倒退”处理，不重新打开门槛通知资格。

### 7.7 明确不监控的结果信号

sentinel 不因以下情况产生通知：

- hit rate 上升或下降；
- 连续命中或连续失误；
- 某轮全部选择大球、小球、主队或客队；
- 任一盘口类型短期表现；
- `can_lift_club_rating_pending=false` 本身。

这些只能在达到最小样本后进入独立人工复盘，避免把小样本噪声变成自动策略动作。

## 8. 状态、去重与 Outbox

状态路径：

`data/local/diagnostics/csl_postmatch_sentinel_state.json`

状态只保存：

- schema、competition、season；
- 最近成功输入指纹和处理时间；
- 首次基线与单调高水位；
- 当前活动异常的稳定事件 ID、首次/最后出现时间和安全摘要；
- 已恢复事件的必要去重标记；
- 样本门槛已通知/已静音状态；
- pending、sent、failed 或 suppressed outbox 元数据；
- 最近安全运行状态和稳定错误码。

不得保存 token、UID、`.env`、命令输出、原始响应、traceback、绝对私有路径、原始 odds/provider payload 或完整比赛对象。

### 8.1 提交顺序

1. 获取 sentinel 专用排他文件锁；
2. 重读并验证 state 与报告；
3. 纯判定器生成下一状态和 outbox 项；
4. `write=False` 时只返回候选摘要，不创建目录、锁文件或 state；
5. `write=True` 时先用同目录临时文件、flush/fsync、重开校验和 `os.replace` 原子提交 pending state；
6. `notify=True` 时发送 pending 事件；
7. 发送成功后原子标记 `sent`，失败则保留 `pending/failed`；
8. 释放锁并返回脱敏摘要。

只在 `write=True` 时获取会落盘的锁，保证 standalone dry-run 零写入。

### 8.2 交付语义

本地 outbox 提供至少一次尝试语义。WxPusher 工具没有可用的业务幂等键；若进程在“远端发送成功、sent 状态落盘前”崩溃，下次可能重复一次。若改成发送前标记成功，则会在真实发送失败时永久丢通知，因此不采用。

这是一项明确接受的剩余风险。通知内容应带稳定的短事件 ID，便于用户识别极小概率的重复消息。

### 8.3 状态损坏

state 不可读或校验失败时：

- 不覆盖原文件；
- 不创建新的历史基线；
- 不把所有异常视为恢复；
- 返回 `sentinel_state_unreadable`；
- 主中超链路继续；
- 本轮不调用 WxPusher，因为去重依据不可验证；只在 scheduler-local 返回中保留脱敏 warning；
- 必须由人工修复或显式确认重建 state，不得通过删除损坏文件或静默重建来规避。

## 9. 通知内容

通知分三类：

- 异常：事件代码、当前/基线计数、短事件 ID、发现时间；
- 恢复：原事件代码、恢复到的计数、短事件 ID、恢复时间；
- 样本门槛：正式样本数、最小样本门槛和“仅启动人工复盘”的说明。

每条通知都包含“仅用于研究分析，不构成投注建议”。不包含下注金额、执行建议、原始赔率、密钥、UID、绝对路径、堆栈或 provider 响应。

多个事件在同一轮产生时合并成一条有限长度摘要，按严重度和事件代码稳定排序；超出展示上限只给计数，完整安全详情留在本地 state。

## 10. 错误处理与公开边界

- sentinel 任何异常都转换为稳定、脱敏的本地状态；
- 不捕获 `KeyboardInterrupt` / `SystemExit` 等进程控制异常；
- sentinel error 不写入公开 snapshot 的 `data_quality`，避免改变 API 契约；
- scheduler 本地返回对象可含 `postmatch_sentinel={status, reason, event_count, notification_status}`，不得含事件原始详情；
- sentinel 不参与 `_attach_run_metadata()`，不加入 `decision` 或 `LOCAL_POLICY_FIELDS`；
- publish fake 必须捕获最终 body，断言递归不存在 `postmatch_sentinel`、sentinel state 或通知内容；
- 通知失败不得改变 postmatch shadow 的成功状态，也不得阻止 odds refresh；
- provider/quota mocks 必须证明 sentinel 不调用 The Odds API、不修改 quota ledger。

## 11. 测试与验收

### 11.1 纯判定测试

- 当前 `128/8` 首次建基线零异常通知；
- 新 closing/decision 缺口生成事件；
- 相同异常幂等去重；
- 新 match 或计数增加生成异常扩大事件；
- 恢复只生成一次；
- 单调计数倒退时报警，且不降低高水位；
- tally、coverage 和 sample 契约不一致时报警；
- 49 → 50 生成一次门槛事件，50 → 51 不重复；
- 首次启用已达到 50 时补发一次；
- hit rate、连胜连败和盘口方向变化不产生事件；
- 输入顺序不同但语义相同时事件指纹稳定。

### 11.2 文件与 Outbox 测试

- dry-run 不创建目录、锁、state 或通知；
- write 原子提交并重开校验；
- 并发 runner 不重复创建同一事件；
- 通知失败保留 pending，下一次成功后标记 sent；
- `notify=False` 把新事件标为 suppressed，不在未来补发旧消息；
- state 损坏不覆盖、不重建基线；
- 输出与 state 扫描不含 secret、token、UID、`.env`、绝对私有路径、原始 provider payload；
- writer/notify fake 抛出允许异常时返回稳定错误码。

### 11.3 Scheduler 集成测试

- 调用顺序为 results → coverage/shadow → sentinel → odds；
- blocked result source 不调用 shadow 或 sentinel；
- shadow error 不调用 sentinel；
- pending publish retry 不调用 sentinel；
- sentinel error 后 odds refresh 与 publish 仍继续；
- `--no-notify` 只静音 sentinel，其他调用和返回保持原语义；
- publish fake 捕获的 snapshot/HMAC body 不含 sentinel 字段；
- provider/quota spies 证明 sentinel 没有额外请求或 ledger 写入。

### 11.4 本地验收

1. 用当前真实 ignored 报告运行 standalone dry-run，确认识别 38 个正式样本但零写入、零通知；
2. 显式本地 write + notify=false 建立当前基线，确认 128/8 不产生旧账通知；
3. 重复同输入返回 unchanged，state hash/mtime 按实现契约保持幂等；
4. 所有真实 WxPusher 发送必须单独确认，测试只使用 fake；
5. 运行指定完整测试：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

6. 运行 `git diff --check`、`py_compile` 和敏感信息扫描。

验收不以命中率提高为标准；验收标准是异常发现、去重、恢复、样本门槛提醒和主链路隔离均可由测试证明。

## 12. 对抗性自审

### 12.1 小样本误导

38 场低于 50 场门槛，当前 20/18 只属于暂时观察。sentinel 不使用 hit rate 作为报警条件，也不会把达到 50 自动解释为可调参。

### 12.2 历史基线掩盖旧问题

把 128/8 设为通知基线会减少启动噪声，但不解决旧缺口。coverage 报告必须继续完整展示旧缺口，文档和通知不得声称 coverage 已完整。

### 12.3 触发稀疏

不新增定时器意味着没有新 accepted result 时，pending 通知不会主动重试，恢复也不会被主动检测。这是用户选择复用现有触发的明确代价；若以后需要固定重试，应另起设计，不在本阶段暗加 LaunchAgent。

### 12.4 通知幂等限制

文件 outbox 无法消除发送成功后、确认落盘前崩溃造成的极小重复窗口。设计选择宁可极小概率重复，不在失败时永久丢通知。

### 12.5 公开契约污染

sentinel 摘要若加入 scheduler decision，可能经 `run.policy` 进入公开 snapshot。设计要求它只能留在 scheduler-local 返回对象，并用最终 publish body 捕获测试证明隔离。

### 12.6 Secret、额度与线上风险

sentinel 只读本地报告，通知复用全局 WxPusher 工具；不得读取或记录其真实配置。它不调用 The Odds API、不修改 quota、不写线上数据库。设计文档与实施测试均不得触发真实通知、真实 provider 或部署。

### 12.7 范围控制

本阶段只新增 sentinel、最小 scheduler 接线、测试和架构文档。现有 128/8 缺口修复、模型复盘、参数调整、pending gate 解除、公开战绩和部署均是后续独立任务。

## 13. 实施顺序建议

1. 先写纯输入校验、事件模型和 evaluator 的失败测试；
2. 实现纯判定器；
3. 写 state/outbox/dry-run/并发失败测试并实现本地 runner；
4. 写 scheduler 接线与公开 payload 隔离测试；
5. 实现 `--no-notify` 和非阻断接入；
6. 更新现有 README 与近期记录；本阶段不新建 `ARCHITECTURE.md`；
7. 执行聚焦测试、完整回归、安全扫描和真实本地 dry-run；
8. 另行确认后才可进入 commit/push、合并或部署阶段。
