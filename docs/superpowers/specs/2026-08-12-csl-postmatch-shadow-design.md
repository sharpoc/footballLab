# 中超赛后 Shadow 结算与评估闭环设计

日期：2026-08-12

状态：已确认设计，待实施计划

适用赛事：`csl_2026`

## 1. 背景

当前中超赛前链路已经能够：

- 从 The Odds API 获取中超主盘口；
- 使用中足联官方公开接口与 7M 对完赛赛果进行双源一致性校验；
- 将成功构建的赛前 snapshot 自动归档到 `data/local/diagnostics/csl_history/`；
- 为每场存在有效主盘口的比赛生成唯一 `match_decision`；
- 在 `club_rating_pending` 阶段使用赔率去水后的市场共识兜底，不让占位评级改变方向。

但赛后评估闭环没有跟随赛前链路持续运行。2026-08-12 只读核验发现：

- 本地中超 history 已能还原 35 场当前策略 closing decision；
- 结算为 17 场命中、18 场未中；
- 2026-08-09 三场均为“大 2.5”，结算为 2 中 1 失；
- 现有 `data/local/backtest/csl_2026_eval.csv` 和 `csl_2026_report.json` 仍停留在 2026-07-10 的 8 场样本；
- 公开 `/api/finished` 只包含世界杯，没有中超完赛记录。

因此当前优先问题不是调整模型参数，而是让中超赛果、closing、结算和评估稳定对齐，为后续策略校准提供可信证据。

## 2. 目标

第一阶段建立只在本地运行的中超赛后 shadow 闭环：

1. 使用已通过双源验证的中超赛果；
2. 为每场完赛比赛选择开赛前最后一份合法 closing snapshot；
3. 使用统一的 `settle_match_decision()` 结算当前 `match_decision`；
4. 生成可重复、可审计的 decision-only shadow 报告；
5. 同步更新已有中超 eval CSV、backtest report 和 pending gate 报告；
6. 在失败时保留上一份成功产物，不影响赛前刷新与线上发布；
7. 累积足够样本后，再单独评审是否公开中超战绩或调整策略。

本阶段不以提高短期表面命中率为验收标准，也不根据当前 35 场小样本调参。

## 3. 非目标

本阶段明确不做：

- 不修改 `match_pick_v3` 的选择方向、权重、阈值或风险扣分；
- 不偏置大球、小球、主胜、客胜或任一盘口类型；
- 不删除低置信比赛来提高表面命中率；
- 不解除 `club_rating_pending`；
- 不修改公开 `/api/finished`、`/api/matches`、preview 或静态站点；
- 不发布中超赛后 snapshot 到 ECS；
- 不新增 The Odds API 请求或消耗 quota；
- 不新增独立 LaunchAgent；
- 不把 legacy S/A/B/C 决策混入当前策略统计；
- 不进行多联赛通用结算平台重构。

## 4. 方案选择

### 4.1 方案 A：手工运行已有评估 CLI

优点是改动最小。缺点是无法保证每个比赛日都更新，容易继续出现生产 snapshot 新鲜但评估报告陈旧的问题，因此不采用。

### 4.2 方案 B：在现有中超刷新链路后运行非阻断 shadow 闭环

复用现有双源赛果、history、closing join、统一 settlement 和 backtest 模块；只有双源验证通过并成功接受赛果后才尝试生成 shadow 产物。shadow 失败只记录安全诊断，不阻断当前中超赛前 snapshot 构建或发布。

这是本设计采用的方案。

### 4.3 方案 C：直接建设通用多赛事公开赛后引擎

长期复用性更强，但会同时改变多赛事结果模型、公开 API、持久化和发布边界，范围与风险明显超过当前目标，因此暂不采用。

## 5. 架构与职责

### 5.1 触发边界

shadow 闭环接在现有 `csl_scheduled_publish` 的本地赛果更新之后：

1. 现有链路抓取中足联官方与 7M 数据；
2. 现有双源比较器验证日期、主客队和比分全量一致；
3. 现有防回退规则确认新结果没有删除或改写已接受赛果；
4. 成功接受新的结果 cache 后，调用 shadow runner；
5. shadow runner 读取本地结果与本地 snapshot history；
6. shadow 成功或跳过后，赛前 snapshot 构建和发布按原流程继续。

没有新增已验证赛果时，runner 通过输入指纹判定为幂等跳过，不重复生成相同报告。

当赛事没有未来比赛、现有中超调度进入 discovery cadence 时，仍沿用现有调度触发结果刷新，不另建长期常驻进程。

手工 CLI 默认只输出 dry-run 摘要，不写任何产物。只有显式本地 `--write`，或现有 live 中超刷新链确认已接受双源结果后的受控调用，才允许提交 shadow 产物。

### 5.2 Shadow runner

新增边界建议命名为 `worldcup.csl_postmatch_shadow`，只负责编排：

- 加载 `csl_2026` 已验证结果；
- 加载中超 snapshot history；
- 调用 closing 匹配与 decision settlement；
- 聚合 coverage、命中与校准指标；
- 调用现有 `csl_postmatch_runner` 更新 eval/backtest/pending gate；
- 原子写入 shadow 报告和状态；
- 返回不含原始赔率、密钥或线上请求体的安全摘要。

该模块不直接抓取网络数据、不读取 `.env`、不发布 snapshot、不调用通知工具。

### 5.3 Closing 匹配

closing 匹配复用 `csl_eval_data.closing_match_entry()` 的既有约束，并补齐 shadow 需要的审计信息：

- `competition_id` 必须为 `csl_2026`；
- 主客队必须使用已解析的 canonical identity 精确匹配；
- 结果日期必须与 snapshot 中实际 kickoff 的 UTC 日期一致；
- snapshot 时间必须严格早于 kickoff；
- 明确 `POSTPONED` 的记录不得作为 closing；
- 同一比赛取 kickoff 前时间最晚的合法 snapshot；
- 找不到合法 closing 时记录 `missing_closing`，不得用开赛后 snapshot、其他日期或相似队名补造。

closing 记录保留以下审计字段：

- `competition_id`；
- `kickoff_at_utc`；
- canonical 主客队；
- `closing_snapshot_at`；
- 原始 `closing_match_decision` 的公开安全字段；
- 严格 90 分钟比分；
- settlement 结果。

### 5.4 结算

所有 1X2、OU、AH、DNB 结算必须调用 `worldcup.decision_settlement.settle_match_decision()`，不得在 shadow 模块内复制盘口结算规则。

只把 `schema_version=2` 且 `label=MATCH_PICK` 的当前策略决策计入正式 `hit/miss/push`。其他情况分别记录为：

- `no_pick`；
- `missing_decision`；
- `invalid_decision`；
- `missing_closing`；
- `identity_mismatch`；
- `result_source_blocked`。

legacy decision 只计入 coverage 诊断，不得混入当前策略命中率。

## 6. 本地产物

所有新增或更新产物必须位于已忽略目录，不进入 Git：

### 6.1 Canonical shadow 报告

路径：`data/local/diagnostics/csl_postmatch_shadow.json`

建议结构：

```json
{
  "schema_version": 1,
  "competition_id": "csl_2026",
  "generated_at": "...",
  "input_fingerprint": "...",
  "status": "ok",
  "sample": {},
  "decision_tally": {},
  "decision_coverage": {},
  "breakdowns": {},
  "calibration": {},
  "matches": [],
  "warnings": [],
  "research_notice": "仅用于研究分析，不构成投注建议。"
}
```

报告不得保存 API key、secret、原始 provider payload、完整 bookmaker 明细或资金字段。

### 6.2 Shadow 状态

路径：`data/local/diagnostics/csl_postmatch_shadow_state.json`

状态仅保存：

- 上次成功输入指纹；
- 上次成功时间；
- 上次成功样本数；
- canonical 报告内容 hash；
- 最近一次尝试的安全状态与错误码。

不得把异常堆栈、原始响应或敏感值写入状态。

### 6.3 既有评估产物

成功运行时继续更新：

- `data/local/backtest/csl_2026_eval.csv`；
- `data/local/backtest/csl_2026_report.json`；
- `data/local/diagnostics/csl_pending_gate_latest.json`；
- 必要的安全运行摘要。

历史时间戳报告不得在每 15 分钟空跑时无限增长；只有输入指纹变化或显式人工运行时才生成新历史记录。

## 7. 报告指标

### 7.1 正式统计

- `decision_tally.hit`；
- `decision_tally.miss`；
- `decision_tally.push`；
- `decision_tally.no_pick`；
- `decision_sample.decided`；
- `decision_sample.hit_rate`；
- `decision_sample.pick_rate`；
- `decision_sample.min_sample`；
- `decision_sample.sample_too_small`。

### 7.2 Coverage

- 已验证完赛结果数；
- closing 可用数；
- closing 缺失数；
- 当前策略 decision 可用数；
- decision 缺失数；
- decision 无效数；
- legacy decision 数；
- identity mismatch 数；
- source blocked 数。

### 7.3 分组观察

仅作为观察，不自动触发调参：

- 按 `1X2 / OU / AH / DNB`；
- 按 `home / draw / away / over / under`；
- 按盘口 line；
- 按参考赔率区间；
- 按 `p_hit_safe` 区间；
- 按证据分区间；
- 按书商覆盖和离散度风险标记。

每个分组必须同时显示样本数。样本低于门槛时只标记“观察”，不得输出“应调高/调低权重”等自动结论。

### 7.4 校准指标

对当前 decision 的二元命中结果计算：

- `p_hit_safe` 分桶的均值与实际命中率；
- 二元 Brier score；
- 每桶样本数；
- 全局 `sample_too_small`。

AH 半赢按 `hit`、半输按 `miss` 与现有公开 tally 保持一致；若后续需要单位收益校准，应另立设计，不在本阶段混入口径。

## 8. 幂等、原子性与失败处理

### 8.1 输入指纹

输入指纹至少覆盖：

- 已接受中超结果的稳定序列；
- 每个已结算结果实际选中的 closing identity、`closing_snapshot_at` 与 decision 内容摘要；
- settlement schema/version；
- competition id。

未被任何已验证完赛结果选中的赛前 history 文件不得改变指纹，避免未来场次快照增长触发无意义重算。相同指纹重复运行必须返回 `unchanged`，不得重复计数或生成重复历史报告。

### 8.2 原子写入

每个文件均使用同目录临时文件加 `os.replace`。runner 必须先在 staging 路径完整生成并交叉验证 eval CSV、backtest report、pending gate 和 shadow 报告，全部成功后再依次提升；canonical shadow 报告最后提升，成功状态与输入指纹在其后提交。该顺序不宣称跨文件系统事务，但保证消费者只把最后提交且 hash 一致的 canonical 报告视为新一轮成功结果。任一步骤失败时：

- 上一份 canonical 成功报告保持完整；
- 不提交新的成功指纹；
- 即使某个辅助文件已提升，消费者也不得在 canonical 报告与状态 hash 未共同确认前将其视为新一轮完整结果；
- 写入或返回安全失败码；
- 不影响赛前 snapshot 构建和发布。

### 8.3 非阻断原则

shadow 是诊断链，不是赛前产品链的依赖。以下问题只增加 `data_quality` warning，不得撤销有效赛前首选：

- shadow 报告生成失败；
- eval/backtest 写入失败；
- pending gate 报告失败；
- 单场 closing 缺失；
- 单场 decision 无效。

双源赛果不一致仍由现有结果刷新边界阻止接受新结果，shadow 不得绕过。

## 9. 验证策略

### 9.1 单元测试

- 1X2、OU、AH、DNB 均复用 canonical settlement；
- 当前策略与 legacy decision 分离；
- kickoff 前最后 snapshot 正确选择；
- kickoff 后 snapshot 不得进入 closing；
- `POSTPONED`、改期、跨赛事和主客对调不得误匹配；
- 缺 closing、缺 decision、非法 line 分别计数；
- 相同指纹幂等跳过；
- 原子替换失败时旧报告保持完整；
- shadow 异常不阻断 `csl_scheduled_publish`。

### 9.2 离线真实数据回归

使用现有 ignored history 与已验证中超结果，必须还原：

- 当前策略 closing decision：35 场；
- `hit=17`；
- `miss=18`；
- 2026-08-09 三场为 `over 2.5`；
- 2026-08-09 结算为 `2 hit / 1 miss`。

如果实际历史在实施期间新增赛果，测试 fixture 应冻结实施前的最小脱敏样例；真实回归报告则允许样本继续增长，不把 35 场硬编码为永久生产断言。

### 9.3 完整回归

运行：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

并验证：

- 全量测试无新增失败；
- `compileall` 通过；
- `git diff --check` 通过；
- 默认 dry-run 不联网、不读取 `.env`、不写 shadow 产物；
- 注入式本地运行不调用 The Odds API；
- 公共 API 和 preview 输出不变。

## 10. 分阶段交付

### 阶段 1：离线 shadow runner

实现 closing、settlement、报告、幂等和原子写入，使用现有 35 场本地数据完成回归。该阶段不接生产调度。

成功标准：真实离线数据可稳定生成可信报告，重复运行结果一致。

### 阶段 2：接入现有中超刷新链

在双源结果成功接受后调用 shadow runner。所有 shadow 错误均降级为安全 warning，不影响赛前产品链。

成功标准：连续自然运行到至少一个新比赛日，报告样本只增不减，closing/结果无冲突，赔率 quota 不因 shadow 增加。

### 阶段 3：人工复审

至少观察 7 天并覆盖 2026-08-14 至 2026-08-15 中超比赛日后，检查：

- tally 与逐场 settlement 一致；
- coverage 无未解释缺口；
- 没有开赛后 closing 污染；
- 没有因 source mismatch 接受错误比分；
- shadow 没有影响赛前刷新、发布或 quota。

通过后再单独决定是否把中超 finished 合并到公开 API。公开阶段必须另行确认和设计，不能由 shadow 自动晋升。

## 11. 对抗性自审

### 小样本与过度结论

35 场只能作为暂时观察。特别是 `over=6/15`、`under=6/9` 不足以证明长期方向差异，不能据此偏向小球或限制同向推荐。本设计只补数据闭环，不调参。

### Closing 污染

最大统计风险是使用开赛后 snapshot、补赛旧事件或相似队名误接 closing。设计通过严格 competition/canonical identity、结果日期、kickoff 时间和 `snapshot_at < kickoff` 防止污染；无法唯一匹配时宁可记缺失。

### 结果口径

只接受现有双源一致且已写入 cache 的 90 分钟比分。shadow 不自行抓取、不使用加时/点球、不绕过防回退规则。

### 生产耦合

shadow 接入已有 runner 可能把诊断失败传导到赛前发布。设计要求调用点捕获 shadow 边界异常、只追加 warning，并用测试证明赛前 publish 结果不受影响。

### 存储增长

中超 history 当前约 24MB，但无变化的周期运行若持续写时间戳报告会造成无意义增长。设计以输入指纹去重，只在数据变化时保存新结果。

### 范围膨胀

本阶段不顺手拆分大型 UI 文件、不建设多联赛 finished、不修改公开 schema、不启用俱乐部模型。发现的相邻问题记录为后续任务，不能进入本实施范围。

### 回滚

本阶段新增产物均位于 ignored `data/local/`，代码接入使用非阻断调用。回滚只需移除 shadow 调用与新增模块；既有结果 cache、history、公开 snapshot 和线上数据库不做迁移或破坏性修改。

## 12. 后续决策门槛

只有同时满足以下条件，才进入公开中超战绩设计：

- shadow 至少覆盖一个新增比赛日；
- tally 与逐场人工抽样一致；
- coverage 缺口均可解释；
- 没有 closing 时间污染；
- 双源赛果与 shadow 输出一致；
- shadow 运行没有增加 The Odds API 消耗；
- 用户单独确认公开 API、页面和发布范围。

是否调整 `match_pick_v3` 或解除 `club_rating_pending` 仍分别遵守各自样本与基准门槛，不因 shadow 链路可用而自动改变。
