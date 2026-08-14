# 中超 Ops Check 实时数据源优先级修复设计

日期：2026-08-14

状态：已确认设计，待用户复核后编写实施计划

适用模块：`worldcup.ops_check`

## 1. 背景与已核实事实

部署后只读巡检曾显示：中超赔率 `observed_at=2026-06-29`、`quota_remaining=34`。进一步沿调度、缓存、额度和公网投影逐层核验后确认：

- `worldcup.csl_scheduled_publish` 的 LaunchAgent 已加载，每 15 分钟正常唤醒，最近退出码为 0；
- 真实赔率缓存 `data/cache/theoddsapi_csl_2026_odds.json` 于 2026-08-13 更新，包含 8 个 2026-08-14 至 2026-08-15 的赛事，最新 bookmaker 时间为 2026-08-13；
- 当前 quota ledger 的主、次、第三槽 remaining 分别为 0、29、29，调度器已按现有策略进入低额度模式，只保留 `T-25` 刷新锚点；
- 公网 8 场过期首选已全部安全投影为 `NO_CLEAN_MARKET`，没有继续展示旧首选；
- `data/local/diagnostics/csl_live_odds_refresh.json` 仍停留在 2026-06-29，且日常 `csl_scheduled_publish` 不会更新该文件；
- `ops_check` 的简报却只从这份旧诊断取 `observed_at`、provider 和 quota，所以显示了已经失效的运维事实。

根因是巡检数据源优先级错误，不是 scheduler 未运行，也不是真实赔率缓存从 6 月起未更新。

## 2. 目标

本次只修正 `ops_check` 的中超实时赔率摘要：

1. `observed_at` 优先反映当前合法 odds cache 的本地提交时间；
2. provider、`quota_remaining`、`quota_last` 优先反映当前 quota ledger 中调度器会采用的槽位；
3. 旧 refresh diagnostic 只在当前 cache 或 quota 信息不可用时逐字段 fallback；
4. 保留现有敏感字段过滤、退出码和 `status/errors/warnings` 语义；
5. 用回归测试证明旧诊断不能覆盖更新的 cache/quota。

## 3. 非目标

本阶段明确不做：

- 不修改 `csl_scheduled_publish`、LaunchAgent、刷新间隔或 due 计算；
- 不执行 live refresh，不调用 The Odds API，不消耗 quota；
- 不新增缓存年龄阈值或 stale warning；
- 不修改公开 API、preview、snapshot 或线上数据库；
- 不改 `match_pick_v3`、首选方向、模型参数或结算口径；
- 不删除或重写旧 `csl_live_odds_refresh.json`；
- 不读取 `.env`，不根据真实 key 内容推断槽位；
- 不把 bookmaker、price、raw market 或请求信息加入巡检输出；
- 不借机重构通用 quota 或 scheduler 模块。

## 4. 方案选择

### 4.1 方案 A：当前 cache/quota 优先，旧诊断 fallback

从已经通过解析校验的 odds cache 获取文件提交时间，从脱敏 quota ledger 按现有中超 scheduler 的顺序和阈值选择当前槽位。旧诊断只在相应当前字段缺失时 fallback。

该方案直接修复误报，不改变调度或告警语义，是本设计采用的方案。

### 4.2 方案 B：同时增加缓存年龄告警

可以更早暴露真正的陈旧缓存，但必须定义“距离 kickoff 多久算陈旧”并与低额度 `T-25` 策略协调，否则正常节流也会长期显示 warning。这属于新的运维策略，不纳入本次修复。

### 4.3 方案 C：删除时间和额度字段

能避免错误数字，却会失去判断调度与 quota 状态所需的可见性，因此不采用。

## 5. 数据流与优先级契约

### 5.1 Local 只读摘要

`_csl_live_odds_summary()` 继续完成现有 odds cache 结构与内容解析。只有 cache 存在、是合法事件列表且 `parse_league_odds_events()` 成功后，才允许读取并输出：

- `cache_updated_at`：cache 文件 `mtime` 转换后的 timezone-aware UTC ISO 时间。

`cache_updated_at` 表示“本地合法 cache 最后一次被提交到该路径的时间”，不声称所有 bookmaker 在这一刻同时更新。完整 JSON 继续保留原有 `refresh_diagnostic`，便于看出旧诊断本身是否存在和包含什么安全字段。

cache 缺失、形状错误、解析失败或 `stat` 失败时，不生成 `cache_updated_at`；既有 cache error 状态保持不变。

### 5.2 当前 quota 槽位选择

只读取 `_safe_quota_providers()` 已允许的 provider 名和数值字段。槽位顺序与当前中超 scheduler 保持一致：

1. `theoddsapi_primary`；
2. `theoddsapi_secondary`；
3. `theoddsapi_tertiary`；
4. legacy `theoddsapi`。

选择规则固定为：

1. 取第一个 `remaining > 30` 的槽位；
2. 若不存在，取第一个 `remaining > 0` 的槽位；
3. 若所有已知槽位都没有正额度，但至少有一个合法的 0 额度记录，取顺序中的第一个 0 额度槽位；
4. 若没有任何合法数值记录，则当前 quota 摘要不可用。

`remaining` 只接受非负整数，布尔值不得当作整数。未知 provider、字符串额度、负数以及额外字段不参与选择，也不得进入输出。legacy alias 排在显式槽位之后，避免同一次写入同时更新显式槽位和兼容 alias 时错误显示 alias。

### 5.3 最终 report 字段优先级

`report.csl_live_odds` 与 `--format summary` 使用相同的逐字段优先级：

| 输出字段 | 第一来源 | fallback |
|---|---|---|
| `observed_at` | `local.csl_live_odds.cache_updated_at` | 旧 `refresh_diagnostic.observed_at` |
| `provider` | 当前 quota 槽位 provider | 旧 `refresh_diagnostic.theoddsapi_provider` |
| `quota_remaining` | 当前 quota 槽位 `remaining` | 旧 `refresh_diagnostic.quota_remaining` |
| `quota_last` | 当前 quota 槽位 `last` | 旧 `refresh_diagnostic.quota_last` |

fallback 是逐字段的：例如 quota provider 存在但没有合法 `last` 时，只允许 `quota_last` 回退，不得让旧诊断重新覆盖已确认的 provider 或 remaining。

除上述字段外，`events`、`fixtures`、`odds_events`、synthetic guard、alias、非法赔率、runner 状态和 decision counts 均保持现有来源与语义。

## 6. 安全与错误处理

- 全程只读，不创建、触碰或替换 cache/quota/diagnostic 文件；
- 不读取 `.env`，不加载 API key，不连接 provider；
- 时间只接受本地文件系统返回的 timezone-aware UTC 转换结果或既有安全 ISO fallback；
- quota 只允许白名单 provider 和 `remaining/used/last` 数值字段；
- 不把 quota ledger 的其他字段加入 `local`、`report` 或 summary；
- 任一新来源不可用时 fail soft 到旧诊断，不把巡检变成 error；
- 既有 cache 缺失/损坏、synthetic、alias、非法赔率和 runner blocking 判定不变；
- 不改变 CLI 退出码：仍只有现有 error count 大于 0 时返回非零。

## 7. 测试设计

测试只修改 `tests/test_ops_check.py`，生产实现只修改 `worldcup/ops_check.py`。

TDD 至少覆盖：

1. **旧诊断不能覆盖新状态**：设置合法 cache 的固定 mtime、旧诊断的旧时间/34 额度、当前 ledger 的主 0/次 29/第三 29；断言 report 和 summary 显示 cache mtime、secondary、29 和当前 last；
2. **正常额度优先**：主 0、次 29、第三 50 时选择第三槽位 50；
3. **低额度顺序**：主 0、次 29、第三 29 时选择 secondary 29；
4. **逐字段 fallback**：cache mtime 或 quota 当前字段不可用时，只有缺失字段回退旧诊断；
5. **错误输入安全**：未知 provider、字符串 remaining、布尔数值、额外 secret/bookmaker/price 字段不进入结果；
6. **现有契约不回归**：cache 解析失败仍为 error，runner/公共接口/log checks 计数不变；
7. **CLI summary**：`--format summary` 使用与 JSON report 完全相同的有效字段。

先增加能稳定复现当前误报的失败测试，确认失败原因是旧诊断仍覆盖当前 cache/quota；再写最小实现使其转绿，最后运行聚焦测试和完整项目测试。

## 8. 验收标准

- 用 2026-08-14 的等价 fixture，summary 不再显示 2026-06-29 / 34，而显示当前 cache 提交时间和当前选中槽位额度 29；
- `local.csl_live_odds.refresh_diagnostic` 仍保留安全的旧诊断内容，证明没有静默重写历史文件；
- 没有新增 warning/error，也没有改变低额度 `T-25` 调度；
- 结果中不出现 bookmaker、price、API key、secret、`.env`、header 或请求体；
- 聚焦 `test_ops_check.py` 通过；
- 项目指定 runtime 完整测试通过；
- `git diff --check` 通过。

## 9. 回滚

该修复只改变只读巡检投影。若出现回归，回滚 `worldcup/ops_check.py` 与对应测试即可；不需要迁移 cache、quota、SQLite、LaunchAgent 或线上数据，也不需要恢复 provider 状态。

## 10. 对抗性自审

- **mtime 语义风险**：文件复制或人工 touch 可能改变 mtime，但不代表 bookmaker quote 更新。设计明确把它定义为“合法 cache 本地提交时间”，不把它包装成逐盘口采集时间；真正的 quote-age 告警留待独立设计。
- **调度口径漂移风险**：巡检必须固定 provider 顺序和 `>30 / >0` 规则，并用正常额度与低额度测试锁住当前语义；本次不改 scheduler，也不引入独立阈值。
- **legacy alias 重复风险**：显式槽位优先、legacy 最后，避免同一额度被错误报告为 legacy provider。
- **部分字段陈旧风险**：fallback 必须逐字段执行，不能因 `quota_last` 缺失而让旧诊断覆盖当前 remaining。
- **缓存损坏掩盖风险**：只有现有 parser 已确认 cache 合法时才产生 `cache_updated_at`；损坏 cache 继续沿用既有 error，不得被旧诊断伪装成健康。
- **范围膨胀风险**：不增加 stale threshold、不重构共享 quota、不修改调度、刷新、模型、API 或部署链路。
- **额度与线上风险**：设计和实现验证均只读本地文件；不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布、不部署。
- **过度结论风险**：修复只证明巡检展示当前事实，不证明赔率质量、模型胜率或样本表现得到改善。

自审结论：在保留 mtime 明确语义、逐字段 fallback 和现有 error 契约的前提下，没有需要改变业务语义、刷新策略或公开接口的阻断项，可以进入实施计划阶段。
