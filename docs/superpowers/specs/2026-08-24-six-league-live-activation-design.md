# 六联赛单场分析 Live 激活设计

日期：2026-08-24

状态：设计已确认，实施计划已编写并待执行确认。尚未联网、读取 `.env`、消耗 The Odds API quota、解除 live 门、安装调度或部署。

## 1. 目标

在现有六联赛离线闭环之上，接入真实赛程、赔率和严格 90 分钟赛果，使意甲、巴甲、西甲、英超、德甲、法甲从下一批尚未开赛且存在合法盘口的比赛开始，每场输出唯一“本场首选”研究结论，并完成赛前封盘、赛果结算和分联赛统计。

公开产品继续使用 `MATCH_PICK` / `NO_CLEAN_MARKET`，保留“不构成投注建议”，不显示金额或下注指令。已经开赛或结束的比赛不得补造赛前首选。

## 2. 当前事实与缺口

- 六个 `league_v1` profile、纯离线预测、closing、结果验证门、结算、独立统计和单场页面入口已经实现。
- `worldcup.league_batch_runner` 的 live/write 仍固定返回 `live_acceptance_not_enabled`。
- 六联赛没有正式 live runner、独立状态提交、自动调度或发布接线。
- 现有 `/sports` 与 `/events` 探测证明了 exact `sport_key`，但正式启用仍需当前赛季真实 odds 样例、球队身份验收和 scores 口径证据。
- 当前免费额度紧张；所有真实请求必须先估算成本，逐 key 执行并在额度不足时停止。

## 3. 方案选择

采用“共享 live 编排 + 六联赛独立验收/启用状态 + 最近开球动态优先级”。六个联赛同时参加赛程发现，不按固定联赛顺序等待；实际 odds 请求按下一场开球时间排序。某联赛独立通过验收即可启用，失败不拖住其他联赛。

不采用以下方案：

- 六联赛全部验收后一次性开放：会让已准备好的联赛被单一异常源长期拖住。
- 为每个联赛复制一套 runner：重复 quota、状态、closing 和失败处理，维护风险高。
- 只发布推荐、不做 closing/结算：无法形成可审计战绩，违反完整闭环目标。

## 4. 运行状态机

每个 `competition_id` 独立维护：

```text
disabled_until_live_acceptance
  -> probing
  -> odds_sample_verified
  -> identity_verified
  -> result_contract_verified
  -> active

任一硬门失败：blocked（保留 reason，可复验）
active 运行失败：degraded 或 stale；超过鲜度后回到 blocked，不发布新首选
```

只有 `active` 联赛可以进行 live/write。状态只能由保存样例的离线验收报告推进，不能靠配置手工跳级。单场页面公开状态由最近一次已提交运行状态投影，不伪造比赛行。

## 5. 数据源与验收门

### 5.1 赛程与赔率

每个联赛使用 profile 中 exact The Odds API `sport_key`：

- 意甲：`soccer_italy_serie_a`
- 巴甲：`soccer_brazil_campeonato`
- 西甲：`soccer_spain_la_liga`
- 英超：`soccer_epl`
- 德甲：`soccer_germany_bundesliga`
- 法甲：`soccer_france_ligue_one`

验收必须保存脱敏 raw 样例到 ignored `data/probe/leagues/<competition_id>/`，验证 active sport、event ID、UTC kickoff、主客队、完整 h2h、合法 decimal odds、bookmaker 覆盖和无重复身份。T-25 才请求 spreads/totals；早期锚点只请求 h2h 以节省额度。

### 5.2 球队身份

从真实 event/odds 样例生成候选球队清单，经显式 alias registry 映射到 canonical identity。未知球队不得使用宽松 slug fallback 进入正式首选；记录 `unmatched_team` 并只阻断相关比赛。event ID、球队或 kickoff 冲突时 fail-closed。

### 5.3 赛果

为 exact sport key 保存真实 completed scores 样例，并验证 provider 文档与 payload 能证明其为足球常规时间结算口径。只有 `completed=true`、比分为非负整数且 event/team/kickoff 唯一 join 才可进入正式结果。

若 90 分钟语义不能证明，联赛停在 `result_contract_verified` 之前；可以生成赛前 dry-run，但不得启用正式 closing 战绩或公开胜率。不得混入加时、点球、aggregate、半场或含义不明比分。

## 6. 额度与动态优先级

每轮先做零额度本地计划，候选按以下顺序排序：

1. 已进入刷新窗口且距离开球最近；
2. T-25 高于 T-90，T-90 高于 T-6h；
3. 已 active 联赛的首选鲜度保底；
4. 尚在 probing 的联赛样例请求。

同一 `sport_key + anchor` 一次请求覆盖该波次全部比赛。每次真实请求前重新读取安全 quota ledger；优先使用未探测或余额大于 30 的下一个 Key 槽位。没有新鲜槽位时只保留临场关键请求；全部耗尽则停止，不尝试绕过 provider 限制。

计划输出必须包含预计请求数、目标 sport key、markets、原因和停止点，但不得输出 API key、URL query、raw bookmaker 或 secret。

## 7. 刷新策略

每场采用以下赛前锚点：

- T-6h：首次正式赛前赔率和首选；只请求 h2h。
- T-90m：临场更新；只请求 h2h。
- T-25m：最终主刷新；请求 h2h、spreads、totals。
- `valid_until - 20m`：正常额度下的首选鲜度保底。

同一联赛任一比赛 due 时只刷新整个 sport key 一次。开赛后停止赔率刷新；最后一个 `snapshot_at < kickoff_at` 的合法 snapshot 成为 closing。改期比赛重新计算锚点，旧 kickoff 记录仅保留审计。

## 8. Live 编排与持久化

新增独立六联赛 live runner，复用现有 transport、五 Key 轮换、quota ledger、有限瞬时重试和 `league_competition_pipeline`。职责分为：

- planner：纯函数生成动态优先队列和预计成本；
- probe runner：显式 live，逐 key 保存脱敏样例和 header quota 摘要；
- acceptance evaluator：只读保存样例，输出确定性门禁报告；
- refresh runner：仅允许 active competition，生成并原子保存 snapshot/history；
- closing/postmatch runner：严格封盘、结果 join、结算和统计；
- publisher：复用既有 HMAC ingest 语义，局部失败保留 pending，不重复消耗 quota。

产物继续按 competition 分区：

```text
data/cache/leagues/<competition_id>/snapshot.json
data/local/leagues/<competition_id>/history/
data/local/leagues/<competition_id>/closing.json
data/local/leagues/<competition_id>/results.json
data/local/leagues/<competition_id>/statistics.json
data/local/leagues/acceptance.json
data/local/leagues/scheduler_state.json
```

所有本地产物 ignored；写入使用文件锁、临时文件、`fsync`、`os.replace` 和输入指纹幂等。state 只在 snapshot/history 成功后提交。

## 9. 页面与统计

单场页面固定保留六联赛入口，并展示 `probing`、`active`、`degraded`、`quota_blocked`、`result_pending` 等可信状态。只有 active 且存在尚未开赛合法 snapshot 的比赛显示首选。

每个联赛独立输出 `decision_tally`、`decision_sample`、`decision_coverage` 和 `sample_too_small`。只有 observed schema v2 closing 可进入命中率；世界杯、中超、legacy、reconstructed 和无 closing 比赛不得混算。样本低于门槛时只展示观察，不据此调参。

## 10. 调度与发布边界

实现验证完成后再单独安装一个六联赛调度入口。建议 LaunchAgent 每 5 分钟唤醒 planner，但仅在存在 due 项时访问 provider；无比赛、未到锚点或 quota 不足时零请求退出。六联赛不得注册六个互相竞争的 timer。

调度上线、真实 odds/scores 请求、解除某联赛 live 门、HMAC 发布、ECS 部署均为分阶段状态变更，必须分别报告请求成本、目标联赛和回滚方式后确认。

## 11. 验收标准

每个联赛独立满足：

1. exact sport key active，存在未来比赛；
2. 保存的 odds 样例通过 schema、身份、完整盘口和新鲜度验证；
3. 全部样例球队已进入显式 canonical registry；
4. 同 payload 离线重放稳定生成每场最多一个 `MATCH_PICK` / `NO_CLEAN_MARKET`；
5. T-6h/T-90m/T-25m、有效期保底、改期和开赛停止均有测试；
6. closing 严格早于 kickoff，重复运行幂等；
7. scores 的 90 分钟语义有证据，结算与 coverage 正确；
8. 分联赛统计不混入其他赛事或 legacy；
9. 单联赛失败不影响其他联赛；
10. dry-run 不读 env、不联网、不写状态；敏感字段扫描为空；
11. 完整项目回归通过；
12. 真实启用后公网页面状态、比赛行、免责声明和更新时间 smoke 通过。

## 12. 分阶段实施

1. 实现纯 planner、验收报告、球队 registry 和 scores 通用 source 边界，全部使用测试样例。
2. 运行零额度计划，列出六联赛当前未来比赛与预计真实请求成本。
3. 经单独确认后，按最近开球顺序逐 key 采集最小真实样例；每次请求后更新 quota 状态，额度不足立即停止。
4. 离线验收样例；通过的联赛逐个解除门禁，未通过者保留明确原因。
5. 接通 snapshot/history/closing/postmatch/statistics 的本地 live 状态，先 dry-run 再显式 write。
6. 安装单一调度入口，观察至少一个完整刷新锚点周期。
7. 经单独确认后推送、部署和公网 smoke。

## 13. 对抗性自审

- **当前赛季已开始：** 固定联赛先后会错过临近比赛，因此改为最近 kickoff 动态优先；已开赛场不补造首选。
- **额度风险：** 六 key 一次性 odds/scores 可能耗尽免费额度；所有真实请求先计划、逐 key 执行并在每次响应后重算余额。
- **比分语义风险：** provider 的 completed score 不自动等于项目要求的 90 分钟比分；证据不足时阻断结算和胜率。
- **身份风险：** 宽松球队 slug 会把相似名称错配；正式路径只接受显式 alias 和完整 event/team/kickoff join。
- **数据污染风险：** probe、daily sidecar 和正式单场产物分区，验收失败样例不能写入 closing/statistics。
- **小样本风险：** 早期命中率只能作为观察，不调参、不宣传稳定胜率。
- **范围风险：** 本阶段不引入俱乐部 Elo、不修改 MatchPick v3 语义、不重构中超或世界杯链路。
- **运维风险：** 单一 timer 和按赛事锁避免并发重复扣额度；状态只在产物成功后提交，发布失败不得重复刷新。

审查结论：该设计覆盖真实推荐所需的赛程、赔率、身份、预测、封盘、结果、结算、统计、调度和发布，但 live 成功仍依赖 provider 当前可用性、剩余额度和 90 分钟比分证据。任何一项不能证明时必须保持对应联赛禁用，不能用模拟数据代替。
