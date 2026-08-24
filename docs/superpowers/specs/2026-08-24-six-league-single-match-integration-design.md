# 六联赛单场分析完整闭环设计

日期：2026-08-24

状态：设计已确认，待编写实施计划；尚未实施、联网、消耗 The Odds API quota、生成正式 closing、写入统计或部署。

适用赛事：

- `serie_a_2026_27`：意甲 2026/27
- `serie_a_brazil_2026`：巴西甲 2026
- `laliga_2026_27`：西甲 2026/27
- `epl_2026_27`：英超 2026/27
- `bundesliga_2026_27`：德甲 2026/27
- `ligue_1_2026_27`：法甲 2026/27

## 1. 背景与问题

项目已有六个联赛的 competition profile、经过真实 `/sports` 与 `/events` 验证的 The Odds API `sport_key`，以及每日精选 sidecar 的部分标准化能力。现有单场分析正式链路只稳定覆盖世界杯和中超；六联赛配置仍是 `dry_run_probe` / `daily_odds_sidecar` 边界，不能生成正式单场 snapshot、赛前 closing、赛果结算或独立战绩。

因此，仅在 `/preview` 的赛事筛选中增加六个名称会制造假接入：页面有入口，但没有可审计的数据生产、封盘和结算链路。本设计采用通用联赛闭环，每个联赛是同一组严格接口下的独立配置实例，并把每日精选 sidecar 与正式单场链路保持隔离。

## 2. 目标

1. 六个联赛完整接入赔率刷新、MatchPick v3 单场预测、赛前封盘、90 分钟赛果结算和独立胜率统计。
2. 第一可见交付进入现有“单场分析”页面，而不是另建临时预览页。
3. 每场存在开赛前有效、可结算主盘口时输出唯一 `MATCH_PICK`；只有赔率无效、过期、比赛已开始或没有可结算盘口时输出 `NO_CLEAN_MARKET`。
4. 俱乐部 Elo 尚未形成已验证数据基线时使用赔率去水后的市场共识，标记 `club_rating_pending`；占位 1500 不得影响方向。
5. 六联赛 snapshot、history、closing、results、statistics 和失败状态按 `competition_id` 隔离。
6. 单联赛失败不得阻断其他联赛生成、结算和展示。
7. 开发与测试阶段只使用保存样例和依赖注入，默认 dry-run，不联网、不读取 `.env`、不消耗 quota、不污染正式 closing 或统计。
8. 延续研究边界：不构成投注建议，不显示金额，不提供下注或执行建议。

## 3. 非目标

本阶段明确不做：

- 不把每日精选 sidecar 快照升级或复制成正式单场 snapshot；
- 不把中超双源赛果、逐队评级门槛、pending gate、closing coverage、postmatch shadow 或 sentinel 泛化给海外联赛；
- 不抓取未经确认条款、口径和稳定性的第三方俱乐部 Elo；
- 不用结构占位 1500 产生模型方向或伪装模型覆盖；
- 不修改 MatchPick v3 的公共标签、资金边界或 legacy 兼容策略；
- 不把世界杯、中超、legacy decision 或 reconstructed closing 混入六联赛正式统计；
- 不新增生产 scheduler、LaunchAgent、ECS timer、数据库 schema 或公网写入；这些动作须在离线实现验收后单独设计和确认；
- 不在本设计阶段调用真实 `/odds` 或 `/scores`，不修改 quota ledger、`.env`、secret 或线上数据；
- 不因暂时没有合法比赛而生成模拟 fixture、模拟赔率或假胜率。

## 4. 方案选择

### 4.1 采用：通用联赛闭环 + 六个配置实例

新增通用 league competition pipeline，六个联赛只声明赛季、时区、provider key、刷新策略、结果口径和统计命名空间。赔率解析、预测、closing、结算和统计共用严格契约。

该方案使职责清晰，能够按单联赛独立测试、失败和回放；后续增加其他联赛时可复用相同边界，不复制整套业务流程。

### 4.2 不采用：直接参数化中超链路

中超包含双源赛果全量一致校验、俱乐部评级 pending gate、固定 closing coverage、postmatch shadow 和 sentinel 等特定业务约束。强行泛化会形成大量 competition 条件分支，并可能削弱中超现有 fail-closed 语义。

### 4.3 不采用：扩展每日精选 sidecar

sidecar 目前服务于周期 Top 4 和组合研究，没有正式 closing、90 分钟结算和分联赛统计契约。直接接入 `/preview` 虽然更快，但只能形成临时演示，不能满足完整闭环要求。

## 5. 架构与组件边界

### 5.1 端到端数据流

```text
saved provider odds sample / injected fetch result
  -> league odds parser
  -> competition + event + team identity validation
  -> market normalization and de-vig probabilities
  -> MatchPick v3 with club_rating_pending fallback
  -> competition-partitioned pre-match snapshot
  -> existing store ingest and multi-competition public projection
  -> /preview single-match analysis

pre-match snapshot history
  -> last legal snapshot strictly before kickoff
  -> immutable closing_match_decision

saved score sample / injected score result
  -> strict 90-minute result validation
  -> competition/event/team/kickoff join
  -> closing settlement
  -> competition-partitioned finished records and statistics
  -> per-league history and six-league aggregate projection
```

### 5.2 新组件

#### `worldcup/league_competition_pipeline.py`

单联赛纯编排层。输入 competition profile、标准化赔率 payload、观察时间和配置；输出正式 schema v2 snapshot。它不联网、不读取数据库、不写文件。

职责：

- 拒绝非正式允许的 competition profile；
- 调用既有 `parse_league_odds_events()` 和球队 identity registry；
- 为每场构建分析输入并调用现有引擎与 `decide_match()`；
- 在 `club_rating_pending` 时只允许市场共识参与方向，输出内部风险原因；
- 保证每场最多一个 `match_decision`；
- 输出 competition、run、counts、data_quality、matches 等既有安全结构。

#### `worldcup/league_closing.py`

从单赛事 immutable history 中选择开球前最后一份合法 snapshot，产生 closing index。纯选择器与文件 runner 分离。

职责：

- 使用 `competition_id + provider_event_id` 作为主身份；
- 同时校验 canonical home/away 与 UTC kickoff；
- 拒绝开球时刻及其后的 snapshot；
- 拒绝 event ID 复用、双方变化、kickoff 冲突和非法 decision；
- 保存 schema v2 `MATCH_PICK` 或明确 `NO_CLEAN_MARKET`；
- 相同输入幂等，既有合法 closing 不被较差或较晚数据回写。

#### `worldcup/league_results.py`

提供结果 source adapter 的纯解析与严格校验边界。首版可复用现有 The Odds API scores transport/collector，但必须先保存六联赛真实样例并证明比分字段的 90 分钟含义。

若源证据不能证明结果口径，输出 `result_pending_review`，不得交给结算器。不得只根据 kickoff 已过推断完赛。

#### `worldcup/league_postmatch.py`

按赛事将 verified result 与 closing join，调用 `settle_match_decision()` 和 `summarize_decision_records()`，生成 finished block 与统计。单场缺 closing 时记入 coverage，不补造首选，也不阻断其他比赛。

#### `worldcup/league_batch_runner.py`

依次编排六个 competition。默认 dry-run；每个联赛产生独立状态，某一联赛解析或构建失败时继续处理其他联赛。该模块不吞掉异常细节，但对公开/日志返回稳定脱敏错误码。

#### `worldcup/league_statistics.py`

只接受当前 schema v2 observed closing record，按 competition 分组，并生成六联赛聚合。聚合必须由分联赛正式 tally 相加得到，不能从混合历史重新推断。

### 5.3 既有组件的最小扩展

- `worldcup/competitions.py`：为六联赛声明正式但默认关闭的 `league_v1` pipeline 能力；保留 exact verified `sport_key`。
- `worldcup/league_odds_refresh.py`：继续提供默认 dry-run、显式 live 的联网边界；批量调度不得绕过 key rotation、quota 和 stale 标记。
- `worldcup/league_runner.py`：保留中超现有行为；只抽取真正通用的纯函数或改由新 pipeline 调用，不搬迁中超特有流程。
- `worldcup/query.py`：安全投影增加分联赛运行状态和统计；继续合并各 competition 最新 snapshot。
- `worldcup/ledger_html.py`：单场分析固定提供六联赛筛选、可信空状态和筛选后的独立历史/统计。
- `worldcup/http_app.py`、`worldcup/fastapi_app.py`：优先复用现有 `/api/matches`、`/api/finished` 和 `/preview`，不新增不必要路由。

## 6. Competition profile 与赛季边界

六个 profile 继续使用现有 ID 和 provider key：

| competition_id | provider sport_key | season | timezone |
| --- | --- | --- | --- |
| `serie_a_2026_27` | `soccer_italy_serie_a` | `2026/27` | `Europe/Rome` |
| `serie_a_brazil_2026` | `soccer_brazil_campeonato` | `2026` | `America/Sao_Paulo` |
| `laliga_2026_27` | `soccer_spain_la_liga` | `2026/27` | `Europe/Madrid` |
| `epl_2026_27` | `soccer_epl` | `2026/27` | `Europe/London` |
| `bundesliga_2026_27` | `soccer_germany_bundesliga` | `2026/27` | `Europe/Berlin` |
| `ligue_1_2026_27` | `soccer_france_ligue_one` | `2026/27` | `Europe/Paris` |

profile 需要新增或明确以下能力字段：

- `pipeline_family = "league_v1"`
- `prediction_policy = "market_consensus_until_club_rating_verified"`
- `result_policy = "verified_football_90min"`
- `statistics_scope = "observed_schema_v2_match_pick_only"`
- `runtime_status = "disabled_until_live_acceptance"`

这些字段表示能力与安全状态，不代表已启用真实刷新。

## 7. 预测契约

1. 输入只使用新鲜、可结算的 1X2、OU 主线和 AH 主线。
2. 同一 bookmaker 去重，并优先使用完整对边。
3. 俱乐部评级未验证时：
   - `rating_policy` 保持 `club_rating_pending`；
   - 不把 home/away 1500 传入会影响方向的模型路径；
   - 用赔率去水后的市场概率生成候选；
   - 对书商覆盖、离散度、盘口质量和数据缺口施加既有风险扣分；
   - 在内部数据质量中说明 `market_consensus_fallback`。
4. 新 snapshot 只允许 `MATCH_PICK` 或 `NO_CLEAN_MARKET`，不生成或公开 S/A/B/C、EV、Edge 或旧 signal label。
5. 每场最多一个 `match_decision`。低概率或模型缺失降低安全概率和证据分，不能通过删除整场提高表面命中率。

未来俱乐部 Elo 启用必须另有数据源证据、历史赛果 replay、逐队样本门槛、市场基准和独立设计，不能在本阶段自动解除 pending。

## 8. 刷新、缓存与失败语义

### 8.1 分区目录

所有运行产物位于 ignored 路径：

```text
data/cache/leagues/<competition_id>/odds.json
data/cache/leagues/<competition_id>/snapshots/
data/local/leagues/<competition_id>/closing.json
data/local/leagues/<competition_id>/results.json
data/local/leagues/<competition_id>/statistics.json
```

不得写入每日精选 `data/cache/daily_odds/`，也不得覆盖世界杯或中超专属产物。

### 8.2 dry-run 与 live 边界

- 默认 dry-run 不读取 `.env`，不调用 transport，不写 quota、cache、history、closing、results、statistics 或 DB。
- 离线实现只使用 `data/probe/` 保存样例和测试内注入 payload。
- future live 必须显式启用目标 competition，并通过统一 key rotation、quota ledger、有限 transient retry 和脱敏错误边界。
- live 刷新、生产 scheduler、LaunchAgent、ECS 发布和真实 DB 写入均须单独确认。

### 8.3 失败与 stale

- 单赛事 source 失败但存在可接受 cache 时可使用上一轮数据构建，但必须在该 competition 的 `data_quality.source_errors` 和 `stale_sources` 标记。
- cache 已过合法鲜度时不得生成新的有效首选。
- batch runner 返回每个 competition 的 `built`、`degraded`、`empty`、`blocked` 或 `error`，不得把局部失败改成全局成功。

## 9. Closing 契约

closing identity 至少包含：

```text
competition_id
provider_event_id
kickoff_at_utc
home_canonical
away_canonical
```

规则：

1. closing 只能来自 `snapshot_at < kickoff_at_utc` 的最后合法 snapshot。
2. `snapshot_at == kickoff_at_utc` 视为已开赛，不可封盘。
3. decision 必须为当前 schema v2，且 label 为 `MATCH_PICK` 或 `NO_CLEAN_MARKET`。
4. 改期比赛以新的 kickoff 重新进入赛前链路；旧 kickoff closing 保留审计但不得自动迁移。
5. provider event ID 对应不同球队、相同球队对应冲突 event ID、kickoff 非法回退或重复 closing 内容不一致时 fail-closed。
6. closing 写入使用锁、临时文件、`fsync` 和原子替换；相同输入幂等。
7. 缺 closing 在赛后统计中透明记为 `missing_closing_count`，不能补造或用赛后赔率替代。

## 10. 赛果与结算契约

### 10.1 接受条件

首版结果 source 候选为 The Odds API scores，但必须在实现前取得并保存六联赛真实样例，逐项验证：

- competition/sport key 精确匹配；
- `completed == true`；
- provider event ID、双方球队和 kickoff 可唯一对应；
- 主客队比分为非负整数；
- 文档和真实 payload 能证明比分是本项目所需的足球 90 分钟结算口径。

最后一项不能证明时，该比赛输出 `result_pending_review`。不得把加时、点球、aggregate、半场或含义不明的比分写入正式结果。

### 10.2 冲突处理

- 比分修订、finished 回退、同 event 多比分、球队或 kickoff 冲突阻断该场更新；
- 已接受结果不得被删除或静默改写；修订必须保留旧值、来源时间和人工审核状态；
- 单场阻断不拖住其他已有 closing 且结果有效的比赛。

### 10.3 结算

统一调用 `settle_match_decision()`，只结算 closing 中的 schema v2 decision。输出 `hit`、`miss`、`push` 或 `no_pick`；missing、invalid、unresolved 和 legacy 只进入 coverage，不进入正式命中率分母。

## 11. 独立统计契约

每个联赛输出：

```text
competition_statistics[competition_id]
  decision_tally
    hit
    miss
    push
    no_pick
  decision_sample
    min_sample
    decided
    actionable
    decision_count
    sample_too_small
    hit_rate
    pick_rate
  decision_coverage
    finished_result_count
    closing_available_count
    missing_closing_count
    decision_available_count
    missing_decision_count
    invalid_decision_count
    unresolved_count
    legacy_decision_count
```

统计规则：

- `hit_rate = hit / (hit + miss)`；push 与 no-pick 不进入命中率分母；
- `sample_too_small` 使用明确 `min_sample`，首版沿用既有统计纯函数默认值，实施计划必须把确切值固化到测试；
- 每个联赛独立计算，页面筛选后只展示该联赛；
- 六联赛汇总只相加六个同口径正式统计；
- 世界杯、中超、legacy decision、reconstructed closing 和每日精选记录不得混入；
- 小样本只标记“观察”，不得据此建议调参或宣称模型可靠。

## 12. 单场分析页面

现有 `/preview` 保持单场分析入口。赛事筛选固定包含世界杯、中超和本设计六联赛；其中六联赛入口不能依赖当前是否恰好有比赛行才出现。

每个联赛需要呈现可信状态：

- 有正式单场数据；
- 暂无未来赛程；
- 暂无合法赔率；
- 数据过期；
- 赛果待确认；
- pipeline 尚未 live 启用。

页面要求：

- 选择联赛后只展示对应 live rows、finished rows 和独立统计；
- “全部赛事”可展示所有比赛，但六联赛汇总统计必须明确范围；
- 开赛后未确认赛果的比赛保留冻结首选并显示“赛果待确认”；
- 明确延期和已确认完赛继续遵循现有公开隐藏规则；
- 动态文本做 HTML escaping；筛选保持键盘与无障碍标签契约；
- 页面保留研究免责声明，不显示资金、下注、执行建议、S/A/B/C、EV 或 Edge。

## 13. API 与存储兼容

优先扩展现有 public projection，不新增临时 API：

- `/api/matches`：继续返回各 competition 最新正式比赛；
- `/api/finished`：继续返回 closing decision 结算记录，并提供按 competition 过滤所需字段；
- `/preview`：消费相同安全投影；
- `/api/snapshot/latest`：保持 schema v2 白名单，不公开 raw provider payload、bookmaker 明细或内部排序字段。

SQLite/PostgreSQL store 继续以 snapshot 的 competition block 分区。实施不得要求破坏性 schema migration；若现有 store 无法承载新状态，应优先使用 snapshot 内兼容字段或独立 ignored state，并另行提出迁移设计。

## 14. 测试与验收

### 14.1 TDD 范围

通用行为写一次测试，六个 profile 使用参数化覆盖：

1. competition ID、season、timezone、exact provider key 和 disabled runtime 状态；
2. 保存赔率样例解析、event/team identity 和非法盘口 fail-closed；
3. `club_rating_pending` 下占位 1500 不影响方向；
4. 每场最多一个 `match_decision`，合法市场存在时不因低置信删除整场；
5. 开球前最后合法 snapshot 成为 closing；
6. 开球后赔率、改期、event ID 和球队冲突不能改写 closing；
7. 结果 schema、90 分钟口径、比分修订、finished 回退和缺 closing；
8. 六联赛 tally、sample 和 coverage 完全隔离；
9. 汇总不混入世界杯、中超、legacy 或 reconstructed；
10. 页面固定六联赛入口、可信空状态、筛选历史和独立统计；
11. HTML escaping、无障碍筛选和研究边界；
12. dry-run 不读 `.env`、不调用 transport、不消耗 quota、不写正式状态；
13. 单联赛失败不阻断其他联赛；
14. 既有世界杯、中超、每日精选和 public API 回归保持通过。

### 14.2 验证命令

聚焦测试按实施计划逐任务执行；完整回归使用：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

同时执行 Python 编译检查和 `git diff --check`。真实 live 验收是后续独立阶段，不能用离线测试通过替代。

## 15. 实施阶段拆分

为保证每阶段独立可验收，后续实施计划至少拆为：

1. 正式 competition profile 与通用 snapshot pipeline；
2. 单场分析固定联赛入口、状态和安全投影；
3. immutable history 与 closing；
4. 结果 adapter、90 分钟校验与 postmatch join；
5. 分联赛统计和六联赛汇总；
6. batch dry-run、失败隔离、文档和完整回归；
7. 另行确认后的真实样例采集与 live acceptance；
8. 另行设计并确认生产调度、发布和部署。

前六阶段不要求联网。第七阶段先做最小只读/低额度证据采集，确认 payload 和结果口径后才允许开启正式刷新；第八阶段不得与代码实现授权合并推定。

## 16. 对抗性自审

### 16.1 已排除的错误路径

- 目标不是“页面出现六个名字”，而是可追溯的赔率、预测、closing、结果和统计闭环。
- daily sidecar 与正式单场 snapshot 的数据契约和生命周期不同，不能互相冒充。
- 中超专属双源和 sentinel 不能被削弱或条件化成通用海外逻辑。
- club rating 缺失时若让两个 1500 进入模型，会制造虚假中性评级；本设计明确禁止。
- kickoff 已过不等于正式完赛，provider score 不明也不等于 90 分钟比分。
- 缺 closing 不能用赛后赔率、临近结果或 reconstructed decision 补造。
- 单场或单联赛失败必须局部化，不能把 batch 部分成功宣称为全部成功。
- 汇总胜率若混入其他赛事、legacy 或 reconstructed 会失去可比性，本设计使用固定 scope 白名单。
- 开发期真实联网会消耗免费额度并污染历史状态，本设计把 live acceptance 独立成需再次确认的阶段。

### 16.2 剩余风险与确认点

1. **The Odds API scores 口径**：当前尚未以六联赛真实样例证明所有比分均为项目要求的 90 分钟口径。证据不足时必须 fail-closed。
2. **quota 成本**：六个 sport key 的刷新会显著增加请求成本。生产刷新锚点、批量策略和低额度降级必须在 live acceptance 后单独设计。
3. **赛季与改期**：跨自然年联赛与巴甲单年赛季并存，identity 和 history 不得只用日期或队名。
4. **球队别名**：不同 provider 命名可能导致 event 与 result join 失败。必须扩展 season-aware club identity registry，并保留冲突诊断，不能模糊匹配后静默接受。
5. **页面状态与真实能力**：固定入口可能在 live 尚未启用时出现；必须明确展示 `pipeline 尚未 live 启用`，不能让用户误认为无比赛就是已接通。
6. **现有 dirty worktree**：实施前需保留并隔离当前五 Key 轮换相关改动，避免覆盖用户未提交内容。

审查结论：设计覆盖完整闭环和页面首交付目标，没有要求业务语义迁移、线上写入或即时部署。阻断实施计划的唯一外部证据是赛果 90 分钟口径，但该证据被明确安排在离线代码完成后的独立 live acceptance 阶段，因此不阻断前六个离线实施阶段。

## 17. 完成定义

离线实现只有同时满足以下条件才可声称完成：

- 六个正式 profile 均通过参数化契约测试；
- 六个联赛都能从保存赔率样例生成唯一 MatchPick v3 单场 snapshot；
- 单场分析固定显示六个入口和真实 pipeline 状态；
- closing 选择、结果验证、结算和统计具备完整离线反例测试；
- 六联赛统计独立且汇总范围可证明；
- 世界杯、中超、每日精选和公开 API 既有回归通过；
- dry-run 安全边界、敏感字段检查、编译检查和完整测试通过；
- 文档明确说明尚未进行真实联网、生产调度和部署。

真实数据闭环只有在后续 live acceptance 取得六联赛赔率/比分样例、证明 90 分钟口径、验证 quota 影响并经单独确认写入正式状态后，才可声称生产接入完成。
