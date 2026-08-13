# 中超历史 Closing 回补与未来覆盖审计设计

日期：2026-08-12

状态：已确认设计，待实施计划与用户文档复核

适用赛事：`csl_2026`

## 1. 背景与已核实事实

2026-08-12 的本地只读核验确认：

- 2026 赛季已有 171 场双源验证赛果；
- 43 场存在开赛前 closing snapshot；
- 其中 35 场包含当前 `schema_version=2`、`policy_version=match_pick_v3` 的可结算首选，结算为 17 场命中、18 场未中；
- 另有 8 场 closing 只有旧结构或缺少当前策略 decision，不计入当前策略命中率；
- 128 场缺少 closing，全部发生在 2026-03-06 至 2026-06-28；
- 本地 `csl_history` 从 2026-06-29 才开始归档，7—8 月未发现同类归档漏跑；
- 本机没有 3—6 月对应的中超历史赔率缓存，因此无法还原当时真实发布的首选；
- 当前正式 decision 样本仍为 `sample_too_small=true`。

这 128 场缺口的根因不是身份匹配 bug，而是归档能力启用前不存在赛前 snapshot。历史公开赔率即使能够取得，也只能构造离线反事实研究样本，不能冒充真实发布记录。

The Odds API 官方历史赔率接口覆盖 `soccer_china_superleague`，但只向付费计划开放，featured markets 的历史请求按 `10 × market × region` 计费。本项目已确认不充值，因此该接口不属于本设计的数据源。参考：[官方历史赔率接口说明](https://the-odds-api.com/liveapi/guides/v4/#get-historical-odds)、[中超历史覆盖说明](https://the-odds-api.com/historical-odds-data/)。

## 2. 目标

本阶段采用“公开源严格部分回补 + 未来防漏”的路线：

1. 为 128 场历史缺口生成完整、可审计的状态清单；
2. 从无需付费、无需登录的公开历史赔率候选源探测可核验数据；
3. 只用严格开赛前盘口离线重建 `match_pick_v3`，并永久标记为 `reconstructed`；
4. 保持真实归档 `observed` 与历史重建 `reconstructed` 的统计边界，禁止混算；
5. 对未来比赛增加 closing 覆盖审计，让每场完赛比赛都有明确覆盖状态或具体缺失原因；
6. 为后续策略诊断积累可信证据，但本阶段不调参、不解除 `club_rating_pending`。

“未来防漏”指消灭无解释的静默缺口，不承诺在 provider 无盘口、quota 不足或合法刷新失败时伪造 100% closing 覆盖。

## 3. 非目标

本阶段明确不做：

- 不把 `reconstructed` 写入现有 `data/local/diagnostics/csl_history/`；
- 不把历史重建首选计入正式 35 场 observed 战绩；
- 不把 8 场旧 schema closing 改写成当前策略 decision；
- 不根据赛果倒推首选，不使用赛后信息参与候选选择；
- 不修改 `match_pick_v3` 的权重、阈值、风险扣分或盘口偏好；
- 不删除低置信比赛来提高表面命中率；
- 不修改公开 API、preview、静态站点、通知或线上数据库；
- 不调用 The Odds API 历史付费接口，不充值，不消耗现有免费 odds quota；
- 不绕过验证码、登录墙、robots 限制、访问控制或站点反自动化措施；
- 不新增 LaunchAgent，不部署 ECS，不修改 `.env`、secret、权限或依赖；
- 不承诺历史 128 场全部可回补，也不把覆盖率提升等同于胜率提升。

## 4. 方案选择

### 4.1 方案 A：公开源严格部分回补 + 未来防漏

先对 7M、OddsPortal 等候选公开页面做小样本可行性探测；只有来源、比赛身份、盘口、赔率和赛前时间均能核验的记录才允许重建。无法达到门槛的场次保留明确原因。未来继续复用现有中超刷新与归档链路，增加覆盖审计而不新增并行调度器。

该方案不要求充值，不污染正式战绩，失败时仍能通过未来防漏持续增加真实样本，是本设计采用的方案。候选站点名称只表示探测对象，不代表已确认可自动采集、可稳定解析或具有足够数据质量。

### 4.2 方案 B：The Odds API 历史付费接口

官方结构与现有 collector 最接近，也能提供历史 snapshot 时间，但需要付费计划且单次成本较高，与当前“不充值”约束冲突，因此不采用。

### 4.3 方案 C：按赛果或当前模型倒推历史首选

覆盖率最高，但会引入严重后视偏差，无法证明真实赛前策略表现，因此禁止采用。

## 5. 数据分层与不可混算契约

报告使用两个正交字段，避免把“数据从哪里来”和“为什么不能结算”混为一谈：

- `provenance_class`：只允许 `observed`、`reconstructed`、`none`；
- `coverage_status`：每场有且只有一个，取值为 `observed_current_decision`、`observed_missing_current_decision`、`reconstructed`、`market_baseline_only`、`manual_review` 或 `missing`。

其中 `observed*` 的 provenance 固定为 `observed`，`reconstructed` 固定为 `reconstructed`，其余状态固定为 `none`。下面分别说明各层及其 coverage 映射。

### 5.1 `observed`

来自当时真实赛前 snapshot history，且 closing 严格早于开赛。正式当前策略战绩只统计其中包含合法 schema v2 `MATCH_PICK` 的 35 场，对应 `coverage_status=observed_current_decision`。

已有 closing 但缺少当前策略 decision 的 8 场对应 `coverage_status=observed_missing_current_decision`，只进入 coverage，不进入 `hit/miss`。

### 5.2 `reconstructed`

使用后来取得、但能证明 quote 时间严格早于开赛的公开历史盘口，按冻结的离线重建契约重新执行 `match_pick_v3`。它是反事实研究样本，不是当时真实展示或发布的首选。

每条记录必须同时保存：

- `provenance_class=reconstructed`；
- `competition_id=csl_2026`；
- canonical 主客队和 kickoff；
- source id、原始 source URL 或稳定页面标识；
- `quote_observed_at` 与本次 `retrieved_at`，两者不得混淆；
- 原始时间文本、来源时区、时间精度、字段语义、转换规则版本与页面证据定位；
- 原始样例内容 SHA-256；
- 标准化盘口内容 SHA-256；
- reconstruction code commit、policy version 和 config digest；
- 重建 decision 的安全字段与结算结果。

### 5.3 `market_baseline_only`

能够核验比赛身份和部分市场价格，但不满足重建首选门槛，例如只有单一书商、缺少完整主盘口、缺少可证明的赛前 quote 时间或只有聚合均价。该层只允许用于市场覆盖诊断或经单独设计的市场基准，不生成重建首选。

### 5.4 `manual_review`

日期、主客队、kickoff、赛事身份或来源间盘口存在冲突。默认 fail closed，不自动选择“更合理”或更有利的一边。

### 5.5 `missing`

没有可用公开记录，或来源不可访问、无法核验、违反访问边界。

### 5.6 唯一状态决策表

状态按以下固定优先级判定，命中后停止，不允许同一场落入多个状态：

| 优先级 | 条件 | `coverage_status` | 允许的 `reason_code` |
|---|---|---|---|
| 1 | 存在合法 observed closing 和当前 schema v2 decision | `observed_current_decision` | `observed_closing` |
| 2 | 存在合法 observed closing，但无当前 schema v2 decision | `observed_missing_current_decision` | `legacy_decision`、`no_current_decision` |
| 3 | 没有 observed，且身份、kickoff 或来源证据相互冲突 | `manual_review` | `identity_mismatch`、`kickoff_conflict`、`source_conflict`、`duplicate_event_conflict` |
| 4 | 没有 observed，历史证据满足全部重建门槛 | `reconstructed` | `reconstructed_eligible` |
| 5 | 身份可核验且存在部分赛前市场证据，但不足以生成首选 | `market_baseline_only` | `quote_time_unverifiable`、`insufficient_bookmakers`、`no_complete_main_market`、`aggregate_only` |
| 6 | 其余情况 | `missing` | `source_unavailable`、`source_access_blocked`、`source_unapproved`、`kickoff_unverifiable`、`no_market_record`、`post_kickoff_only` |

`reason_code` 必须属于该状态的白名单。若同时出现多个同优先级 reason，保留确定性排序后的 primary reason 和完整 `reason_codes` 数组；不能因此改变 coverage 状态。

各 `coverage_status` 不得通过空字段或推断互相转换；转换必须来自新的可审计证据并改变输入指纹。`provenance_class=observed` 不得被历史回补覆盖或降级为 reconstructed。

## 6. 历史数据准入门槛

历史记录只有同时满足以下条件才能进入 `reconstructed`：

1. 明确属于 `csl_2026`；
2. 日期、canonical 主客队与双源验证赛果一致；
3. kickoff 与已验证赛程一致；允许的时间容差必须在实现计划中固定，并且不得跨自然日静默匹配；
4. 来源时间具有明确字段语义、时区与精度，并且其可能时间区间的上界严格早于 kickoff；页面抓取时间 `retrieved_at` 不能替代 quote 时间；
5. 至少两个独立 bookmaker 对同一主盘口提供合法十进制赔率；
6. 至少存在一个完整、可结算的 1X2、OU 或 AH 主盘口；
7. 同一盘口的 outcomes 足以完成去水，不得补造缺失 selection；
8. 原始样例、解析结果、来源标识与内容 hash 可复核；
9. 首选重建过程只接收赛前允许字段，不得接收比分、赛果标签或赛后统计；
10. 中超历史重建固定 `model_input_policy=market_consensus_only` 与 `rating_policy=club_rating_pending`，不得读取 Elo、俱乐部评级、球队样本、当前积分榜或由历史赛果重放出的任何派生特征；
11. 使用冻结的 reconstruction code commit、`match_pick_v3` policy 和显式 config digest，后续代码变化不得静默改写旧结果。

单一聚合网站返回多个明确 bookmaker 可以满足“两个独立 bookmaker”门槛，但必须保留 bookmaker identity；只有网站自己的综合均价不算多个 bookmaker。

标准化时间记录必须包含 `quote_time_raw`、`quote_timezone`、`quote_time_precision`、`quote_time_semantics`、`quote_time_conversion_version` 和 `evidence_locator`。parser 应计算 `quote_time_earliest_possible` 与 `quote_time_latest_possible`；只有 latest possible 严格早于 kickoff 才可重建。时区不明、DST/偏移无法确定、只有日期或精度不足以证明严格赛前时，统一降级为 `market_baseline_only`。历史页面只标记“closing”时不得仅凭字段名称假定其时间语义。

## 7. 架构与组件职责

### 7.1 `csl_historical_odds_probe`

职责仅限来源可行性探测：

- 接收显式 source adapter 和代表性比赛清单；
- 保存原始 HTML/JSON/CSV 样例到 ignored `data/probe/csl_historical_odds/<source_id>/`；
- 保存 retrieval metadata、URL 或页面标识、HTTP 安全状态和内容 hash；
- 输出覆盖与访问边界摘要；
- 不生成首选、不读取 `.env`、不调用 The Odds API、不写正式 history。

网络探测必须单独获得用户确认，并且每个 source 先生成 source approval：记录站点条款页面与核验时间、robots 结论、自动访问许可、原始页面本地留存/研究再利用许可、批准的 URL 范围、速率上限和联系人/人工复核结论。公开可见不等于允许自动采集；任一许可不明确时返回 `source_unapproved`，不得自动访问。验证码、登录墙、付费墙、robots 限制或明确访问拒绝一律返回 `source_access_blocked`，不重试绕过。

collector 只能基于真实保存样例实现；不得先猜页面结构或用合成接口替代来源契约。

### 7.2 来源专用离线 parser

每个候选来源有独立 parser，输入为保存样例，输出统一标准化 quote：

```text
source_id
source_event_id
source_page_id
retrieved_at
quote_observed_at
quote_time_raw
quote_timezone
quote_time_precision
quote_time_semantics
quote_time_earliest_possible
quote_time_latest_possible
quote_time_conversion_version
evidence_locator
competition_id
kickoff_at_utc
home_team_raw / away_team_raw
home_canonical / away_canonical
bookmaker
market
selection
line
decimal_odds
is_main_market
raw_content_sha256
```

parser 必须是纯离线逻辑，不联网、不读数据库、不读取 secret。单个来源结构变化只影响该 adapter，不能改变其他来源或 observed closing。

### 7.3 `csl_closing_backfill`

职责为标准化数据的身份 join、质量门槛、重建与产物编排：

1. 加载 128 场缺口 manifest；
2. 加载已保存并解析的公开源 quotes；
3. 按 competition、canonical identity、kickoff 做严格匹配；
4. 为每场生成互斥 coverage status 和 reason codes；
5. 对达到门槛的场次调用现有赔率去水、主盘口和 `match_pick_v3` 纯函数；
6. 构造只含 immutable fixture identity、kickoff、标准化赛前 quotes 和冻结 policy/config 的 `PrematchReconstructionInput`；强制市场共识兜底，不加载 rating、赛果或其他赛后派生数据；
7. 先生成并 hash 重建 decision，再在独立结算阶段 join 赛果并复用 `settle_match_decision()`；结算结果不得反馈到首选选择；
8. 输出独立 reconstructed bundle 和研究报告。

该模块默认 dry-run、零写入。只有显式 `--write` 才能原子提交 ignored 产物；不得写入 `csl_history/`、正式 snapshot store 或公开 API 数据。

### 7.4 `csl_closing_coverage_audit`

职责为未来 coverage 诊断，不负责联网刷新：

- 赛前检查 upcoming fixture 是否已有合法、仍覆盖当前比赛身份的 snapshot 归档；
- 若缺失，将 `closing_archive_missing` 作为现有 scheduler 的候选原因，但不得绕过 due、quota、provider 可用性或 `--live` 边界；
- 成功构建 snapshot 后验证归档文件可读、competition/fixture identity 一致且 snapshot 时间早于 kickoff；
- 赛后按第 5.6 节决策表为每场输出唯一 `coverage_status` 和白名单 reason；
- 将静默漏档、归档失败、没有合法盘口、quota 阻断、provider 错误和身份冲突区分统计。
- 每次运行都从完整已接受 finished manifest 与 observed history 做全量幂等 reconciliation，不依赖单次触发事件；
- 赛果接受后先持久化 audit pending，再尝试审计；审计成功后清 pending，失败则保留安全失败码供下次重试。

该 audit 复用现有 `csl_scheduled_publish` 与 `csl_postmatch_shadow` 触发点，不新增独立调度器。审计失败只记 warning 且保留 pending，不阻断已有有效赛前首选或公开发布；即使进程在写 pending 前退出，下一次全量 reconciliation 也必须发现 finished manifest 中尚无 coverage 的比赛。

## 8. 数据流与阶段边界

### 8.1 来源探测阶段

1. 从 3、4、5、6 月分层选取代表性比赛；
2. 对候选来源完成条款、robots、自动访问、留存/再利用和限速审批；
3. 单独确认后只访问获批来源；
4. 保存原始样例和 retrieval metadata；
5. 用离线 parser 评估 identity、quote time、bookmaker 和主盘口覆盖；
6. 输出 source feasibility report。

来源探测的成功标准是证明至少一个来源能稳定提供达到准入门槛的真实样例。若没有来源达标，历史重建停止，保留 `missing`，但未来 coverage audit 仍继续实施。

### 8.2 128 场 dry-run manifest

在任何批量采集前，先基于已验证赛果生成 128 场 manifest。每场包括 match identity、kickoff、当前 coverage 状态、需要探测的 source 和预期请求范围。dry-run 不联网、不写 source 样例，只生成安全摘要；写 manifest 需要显式本地 `--write`。

### 8.3 批量回补阶段

只有来源样例通过离线解析测试且用户再次确认批量联网后才执行：

1. 按 source approval 规定的 URL 范围与速率访问已批准来源；
2. 新样例以内容 hash 幂等保存，不覆盖旧内容；
3. parser 生成标准化 quotes；
4. backfill runner 先 staging 全部产物并做交叉校验；
5. 原子提升 canonical bundle 与状态；
6. 相同输入指纹再次运行返回 `unchanged`。

批量回补失败不得删除上一份成功 bundle，也不得影响未来 observed closing 链路。

### 8.4 未来防漏阶段

现有 live 中超刷新继续负责 provider 调用和 snapshot 归档。coverage audit 只消费已有 scheduler 计划、snapshot 和归档结果：

```text
existing scheduler
  -> existing live refresh when allowed
  -> snapshot build
  -> observed archive
  -> archive validation
  -> postmatch coverage audit
  -> observed-only formal report
```

任何补偿刷新仍必须遵守现有免费 quota、三 key 轮换、低额度保护和显式 live 规则；audit 不能自行调用 provider。

## 9. 本地产物与安全边界

建议产物全部位于已忽略目录：

```text
data/probe/csl_historical_odds/<source_id>/
data/local/backfill/csl_2026/initial_missing_manifest.json
data/local/backfill/csl_2026/source_approvals/<source_id>.json
data/local/backfill/csl_2026/runs/<input_fingerprint>/normalized_quotes.json
data/local/backfill/csl_2026/runs/<input_fingerprint>/reconstructed_closing.json
data/local/backfill/csl_2026/runs/<input_fingerprint>/coverage.json
data/local/backfill/csl_2026/runs/<input_fingerprint>/manifest.json
data/local/backfill/csl_2026/current.json
data/local/diagnostics/csl_closing_coverage.json
data/local/diagnostics/csl_closing_coverage_pending.json
```

每个 `runs/<input_fingerprint>/` 是不可变版本目录；`current.json` 只保存当前成功 fingerprint 与版本内各文件 hash。`reconstructed_closing.json` 不得采用现有 observed snapshot 文件名或结构，不得被 `csl_eval_data.closing_match_entry()` 的 observed history loader 自动读取。任何合并研究视图必须通过显式 dual-input API 读取两个来源，而不是把文件放入同一目录后依赖隐式扫描。

原始页面可能含无关标识或动态参数。提交到报告前必须白名单投影；不得保存或输出 Cookie、Authorization、API key、token、用户账号、完整请求 header、资金字段或异常堆栈。URL 若带 query secret 必须在写盘前清除。

## 10. 报告与统计契约

### 10.1 Observed 正式战绩

正式 headline 仅统计：

- `provenance_class=observed`；
- closing 严格早于 kickoff；
- schema v2 `MATCH_PICK`；
- 当前 settlement contract 可结算。

现有 35 场、17 hit / 18 miss 是当前正式基线。历史回补不能改变这些计数。

### 10.2 Reconstructed 研究回测

单独显示：

- reconstructed eligible 数；
- hit/miss/push/no_pick；
- 按 market、selection、line、赔率、`p_hit_safe` 和 source 分组；
- source coverage 和降级 reason；
- reconstruction commit/policy/config digest；
- `sample_too_small`。

标题、字段和文件名必须明确包含 `reconstructed` 或“历史重建”，不得使用“正式战绩”措辞。

### 10.3 合并研究视图

允许在同一报告中并排展示 observed 与 reconstructed，但禁止生成单一合并命中率、单一合并 Brier 或把两类样本相加后宣称总战绩。coverage 总览可以按互斥状态相加，performance 指标必须分层。

### 10.4 Coverage

128 场历史缺口及所有未来完赛场都必须有且只有一个 coverage 状态。报告至少包含：

- finished result count；
- observed closing count；
- observed current decision count；
- reconstructed eligible count；
- market baseline only count；
- manual review count；
- missing count；
- 按 reason code 与月份分组；
- identity conflict 与 source conflict 明细数量。

## 11. 时间切分与调参边界

- 初始 128 个缺口 match id（已核实 kickoff 范围为 2026-03-06 至 2026-06-28）是唯一允许历史重建的固定集合；日期月份只用于展示，不能代替 match id membership；
- kickoff 自 2026-06-29 起的现有真实 observed 固定为 retrospective validation；6 月 29—30 日不得误归入 reconstructed；
- 由于当前 35 场 observed 表现已经被查看，它不是完全盲测集，任何基于它的优势结论都必须降级；
- 真正的确认必须来自策略冻结后新增的 unseen observed 场次；
- 在 observed 当前策略可结算样本达到至少 50 场前，不进入调参设计；
- reconstructed 样本不得计入这个 50 场 observed 门槛；
- 达到样本门槛也只代表允许评审，不代表自动调参或解除 `club_rating_pending`；仍需同时比较市场基准、主场先验、coverage 和校准稳定性。

## 12. 幂等、原子性与失败处理

### 12.1 输入指纹

canonical backfill 指纹至少覆盖：

- 128 场 manifest 的稳定内容；
- 每个原始样例内容 hash；
- 标准化 quote 内容；
- identity mapping 版本；
- reconstruction code commit、policy version 和 config digest；
- settlement contract version。

抓取时间、文件路径或排序变化不得单独改变结果指纹；实际来源内容、quote 时间、身份、盘口或策略契约变化必须改变指纹。

### 12.2 原子提交

raw samples 采用内容寻址或不可变文件名，不覆盖旧样例。runner 在同一文件系统的 staging 目录生成完整 bundle，验证内部 manifest 与所有文件 hash 后，将 staging 原子 rename 为尚不存在的不可变 `runs/<input_fingerprint>/` 目录；若该版本已存在，只允许 hash 完全一致的幂等复用。最后通过单文件 `os.replace` 原子切换 `current.json`。任何步骤失败都不会覆盖旧版本目录或旧 `current.json`；消费者只读取 current 指向且 hash 全部匹配的 bundle。

future audit 的 canonical 结果是单一自包含 `csl_closing_coverage.json`，通过临时文件加 `os.replace` 更新；pending 是独立恢复信号，不是 canonical report 的组成部分。report 成功但 pending 清理失败时，下次 full reconciliation 以 report input fingerprint 判定是否只需清理 stale pending，不重复 provider 请求或重复计数。

### 12.3 失败策略

- 来源不可访问：记 `source_access_blocked`，不绕过；
- HTML/JSON 结构变化：保留旧样例与旧成功结果，parser 返回安全错误码；
- 身份冲突：进入 `manual_review`；
- quote 时间不明或晚于 kickoff：不重建；
- 单书商或不完整盘口：降级 `market_baseline_only`；
- source conflict：不自动选择有利赔率；
- staging、校验或提升失败：保留上一份 canonical bundle，不提交新成功指纹；
- future audit 失败：保留 `csl_closing_coverage_pending.json` 和安全 warning，不阻断赛前发布；下一次运行从完整 finished manifest 全量对账并重试。

## 13. 测试与验收

### 13.1 离线 parser 测试

- 每个 source adapter 使用真实保存样例；
- 合法赔率、非法赔率、主盘口、替代盘口、时间字段和 bookmaker identity；
- 原始时间文本、时区、DST/偏移、精度区间和 evidence locator 可复核；
- 页面结构缺失或变化时 fail closed；
- parser 不联网、不读取 `.env`。

### 13.2 身份与时间测试

- competition、日期、canonical 主客队和 kickoff 精确匹配；
- 主客颠倒、未知 alias、跨自然日、补赛、重复 event 和冲突 kickoff 被阻断；
- `quote_observed_at >= kickoff` 被阻断；
- `retrieved_at` 不能充当 quote 时间；
- 时间区间上界不能证明严格赛前、时区不明或只有日期精度时不能重建；
- 固定 128 个缺口 match id 才允许重建，2026-06-29 起 observed 不得进入重建集合。

### 13.3 重建隔离测试

- observed loader 永远不会扫描 reconstructed 目录；
- observed 35 场 tally 在加入任意 reconstructed 数据后保持不变；
- 8 场 `observed_missing_current_decision` 不被补造成 schema v2 decision；
- 赛果字段注入 reconstruction input 时被拒绝或不可达；
- rating、Elo、积分榜、球队样本和赛后派生数据 provider 使用 exploding stub，证明重建路径不会调用；
- decision 先于赛果 join 生成并 hash，改变比分不得改变 decision hash；
- 单书商、冲突源、不完整市场不会生成首选；
- settlement 只调用统一 `settle_match_decision()`。

### 13.4 幂等与原子性测试

- dry-run 零写入；
- 相同输入重复 write 返回 `unchanged`；
- 内容变化改变指纹；
- 任一步骤失败时旧 `current.json` 和其指向的不可变 bundle 仍可完整读取；
- 已存在相同 fingerprint 但文件 hash 不一致时 fail closed；
- raw sample 不被静默覆盖；
- 报告敏感字段扫描为空。

### 13.5 Future coverage audit 测试

- 成功 snapshot 自动归档后状态为 `observed_current_decision` 或 `observed_missing_current_decision`；
- archive 不可读、身份不符、snapshot 晚于 kickoff、没有当前 decision 分别输出稳定 reason；
- audit 不调用 provider，不绕过 quota，不修改公开 snapshot；
- audit warning 不阻断 scheduled publish；
- audit 失败保留 pending，下一次运行不依赖事件触发而从完整 finished manifest 补齐；
- 模拟在 pending 写入前退出，下一次 full reconciliation 仍能发现 coverage 缺口；
- 每个 finished match 恰有一个 coverage 状态。

### 13.6 阶段验收

来源探测阶段：

- 分层代表样例和 source feasibility report 可复核；
- 每个自动探测来源都有有效 source approval；许可不明确的来源零请求；
- 至少一个来源达到重建门槛，才允许进入批量回补；
- 若没有来源达标，明确停止历史重建，不降低准入标准。

历史回补阶段：

- 128 场全部有明确互斥状态和 reason；
- 正式 observed tally 保持 17 hit / 18 miss，除非未来新增真实 observed 赛果；
- reconstructed 统计完全独立；
- dry-run、幂等、回滚与敏感扫描通过。

未来防漏阶段：

- 每场完赛比赛都有 closing coverage 状态；
- 静默缺口为零；
- provider/额度/盘口原因透明可审计；
- 项目完整测试通过。

## 14. 实施阶段与确认点

### 阶段 1：本地 manifest 与契约

实现 128 场缺口 manifest、互斥状态模型、报告 schema 和纯离线测试。该阶段不联网。

### 阶段 2：公开源小样本探测

在用户单独确认联网范围、候选来源和代表比赛后，保存少量真实样例。不得批量采集。

### 阶段 3：来源 parser 与重建 runner

仅基于阶段 2 保存样例实现 parser、质量门槛、重建隔离与独立报告。默认 dry-run。

### 阶段 4：未来 coverage audit

接入现有 scheduler/archive/postmatch 边界，不新增调度器，不改变公开产品语义。

### 阶段 5：历史批量回补

只有 source feasibility、离线测试、请求规模和访问边界都通过审查，并再次获得用户确认后，才允许批量联网与 ignored 数据写入。

每阶段完成后都需完整验证。commit、push、PR、merge、部署、LaunchAgent、secret、数据库或线上写入继续按项目阶段授权单独确认。

## 15. 对抗性自审

### 15.1 已发现的高风险

1. **后视偏差**：历史赔率是赛后取得，当前代码也晚于比赛发生。通过 `reconstructed` 永久标记、冻结 code/config、禁止赛果进入选择输入和不并入正式战绩降低风险，但无法把它变成真实发布记录。
2. **来源时间语义**：页面显示“closing”不等于能证明 quote 在开赛前。没有可核验 `quote_observed_at` 时必须降级，可能导致绝大多数 128 场仍无法重建。
3. **选择偏差**：公开源容易只覆盖热门比赛或特定盘口。报告必须展示月份、球队、市场和 missing reason 分布，不能用可取得子样本代表全赛季。
4. **来源相关性**：多个网站可能转载同一底层 bookmaker feed。准入门槛要求独立 bookmaker，而不是简单计算页面数量；仍无法完全消除底层相关性。
5. **验证集已被查看**：现有 35 场 observed 结果已知，不能宣称完全盲测。任何调参后的确认都必须依赖未来 unseen observed 样本。
6. **未来“零漏档”的错误理解**：provider 无盘口或 quota 保护可能产生合法 missing。目标是零静默缺口，不是用额外付费请求或伪造数据追求 100% closing。
7. **访问与维护风险**：公开页面可能有 robots、验证码、结构变更或使用限制。设计选择 fail closed，并允许历史路线停止，不能把绕过限制当工程任务。
8. **跨文件原子性**：逐文件替换会产生半新半旧 bundle。设计改用不可变版本目录并只原子切换 `current.json`，消费者验证全部 hash。
9. **派生输入泄漏**：即使不直接传比分，后来重放出的 rating 也会泄漏未来信息。历史重建因此强制市场共识模式，禁止读取 rating、Elo、积分榜和球队样本。
10. **审计触发丢失**：只依赖赛果接受事件会在崩溃后留下静默缺口。future audit 改为持久 pending 加每次全量 reconciliation。

### 15.2 计划修订结论

原始方向“历史回补 + 未来防漏”保留，但作以下收紧：

- 历史回补从“尽量补齐”改为“严格部分回补”；
- `retrieved_at` 与 `quote_observed_at` 明确分离；
- observed 与 reconstructed 不提供单一合并绩效；
- 当前 35 场只作已查看的 retrospective validation，未来新增 observed 才是确认样本；
- 初始缺口以固定 128 个 match id 定义，observed cutoff 明确为 2026-06-29，避免月份边界误分；
- quote 时间保存原文、时区、精度区间、语义和证据定位，只有 latest possible 严格赛前才可重建；
- canonical backfill 使用不可变版本目录和单一原子 pointer；
- future audit 使用持久 pending 与全量 reconciliation；
- 自动访问前必须明确核验条款、robots、留存/再利用许可和来源级限速；
- source 探测、批量联网、代码实现和部署分别设确认门；
- 若公开源无法满足时间与 bookmaker 门槛，历史重建停止，但未来 coverage audit 仍实施。

独立对抗性审查提出的状态重叠、跨文件原子性、派生输入后视泄漏、审计触发丢失、6 月 29 日切分和时间证据/访问许可问题均已在设计中收紧。复查后没有需要改变公开业务语义、接口契约、结算口径、数据库或线上状态的阻断项。剩余主要风险是公开历史数据可能不足；该风险通过 fail closed、透明 coverage 和未来真实样本积累接受，不以降低质量门槛换取表面覆盖率。

## 16. 研究边界

本设计只用于数据质量、策略研究和评估闭环，不构成投注建议，不输出下注金额、追损、重注或执行建议。历史覆盖提高不能直接证明未来胜率提高；任何策略变化必须经过另行确认的设计、足量 observed 样本和未来 unseen 验证。
