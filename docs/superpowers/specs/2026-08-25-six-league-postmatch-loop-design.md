# 六联赛赛后结算与信号评估闭环设计

## 目标

为意甲、巴甲、西甲、英超、德甲、法甲建立独立的赛后闭环：用免费 FotMob 公开数据捕获严格 90 分钟赛果，与开赛前最后一份合法 closing 关联，按联赛独立结算并生成六联赛汇总。有新结算时发送每日摘要，累计有效 decided 样本达到 20 / 50 / 100 时发送一次性阶段提醒。

该链路只为后续离线评估提供可信样本，不自动调参，不自动上线新策略，不构成投注建议。

## 范围与非目标

范围：

- 只处理 FORMAL_SINGLE_MATCH_IDS 中的六个联赛。
- 只接受通过真实样例验收的 FotMob 完赛契约。
- 只结算开赛前最后一份 observed schema v2 closing。
- 按联赛隔离 results、closing、postmatch、statistics 和 state，再生成只读汇总。
- 每天北京时间 10:30 和 16:30 唤醒；没有 due 比赛时不请求 FotMob。
- 只在新增正式结算时发送 WxPusher 摘要，并对 20 / 50 / 100 门槛去重提醒。

非目标：

- 不购买或默认消耗 The Odds API scores 额度。
- 不把世界杯、中超、legacy、reconstructed 或手工结果混入正式统计。
- 不从开球时间推断完赛，不用即时比分、加时或点球数据冒充 90 分钟结果。
- 不事后补造 closing 或修改已发布的赛前推荐。
- 不在该 runner 内调参、重训、切换策略或部署模型。

## 方案选择

推荐使用 FotMob 免费公开 snapshot 加严格契约门禁。它可复用已有 FotMob competition / event identity 与球队 registry，不与临场赔率争抢免费 quota；代价是没有 SLA，schema 变更时必须 fail closed。

不采用 The Odds API scores 作为自动 fallback，因为六联赛按日请求会持续消耗赔率刷新的免费额度。也不在当前阶段维护六套官方联赛适配器，避免过早扩大故障面。

## 数据契约

FotMob collector 只把保存的响应解析为安全候选，不读 closing、不结算、不写统计。每条候选必须包含：

- competition identity 与 provider event ID。
- 主客队 provider name 与 strict canonical identity。
- timezone-aware kickoff_at_utc。
- 明确 terminal FINISHED 状态。
- 90 分钟主客队非负整数比分。
- provider 结果更新时间和源指纹。

身份不明、重复 event ID、球队不匹配、比分结构不完整、状态不明、开球时间超容差或无法证明 90 分钟语义时，只输出脱敏 rejection，不产生正式赛果。

live 开启前必须对每个联赛保存至少一份真实完赛样例，并用 competition、event、strict team identity、terminal status 和 90 分钟比分契约共同生成证据指纹。德甲未 active 时可完成离线契约，但不得进入 live 结算集合。

## Closing 与结算

每次赛前 snapshot 成功提交时，必须在分区 history 保留不可变的完整 snapshot。赛后 runner 用已有 select_league_closings 在同一 competition + event + kickoff + canonical teams 下选择开赛前最后一份合法 schema v2 decision。

如果 history 缺失、身份冲突或没有赛前合法 decision，该场记为 missing_closing，不能使用 current snapshot、开赛后 snapshot 或重建决策代替。

复用 build_league_postmatch 和 build_league_statistics，完善其外层 runner/store。正式 scope 固定为 observed_schema_v2_match_pick_only，产出 decision_tally、decision_sample、decision_coverage 和 skipped_no_closing。六联赛 aggregate 只汇总通过正式 scope 检查的分区，且报告必须同时展示逐联赛样本。

## Runner 与幂等

worldcup.league_postmatch_runner 默认 dry-run：只读 acceptance、fixture/snapshot history 和 state，不读 .env、不联网、不写盘、不通知。live 必须显式传入 --live --write，通知还需 --notify。

live 使用独立非阻塞文件锁，在锁内重读 acceptance、state 和 due 列表。单轮顺序：

1. 优先重试已持久化的通知 pending，不重新抓取或结算。
2. 只对当前 acceptance active 且已开赛、未有 terminal receipt 的 event 构建 due 集合。
3. 按 competition/day 合并 FotMob 请求，解析并严格绑定。
4. 先原子写 results evidence，再构建 closing/postmatch/statistics。
5. 结算指纹相同时返回 unchanged；新 event 可追加。
6. 已接受比分不得删除或静默改写。比分修订、finished 回退或身份变更将 event 置为 conflict，保留旧记录并要求人工复核。
7. 完成本地 state commit 后才构建通知 intent。通知失败不回滚结算，通过 outbox 下次重试。

单联赛的源或数据失败不得阻断其他联赛，但联赛内任何身份/比分冲突不得部分覆盖已接受记录。

## 文件边界

所有运行产物位于 ignored 路径：

- data/probe/leagues/results/ 保存真实诊断样例。
- data/local/leagues/<competition_id>/closing.json。
- data/local/leagues/<competition_id>/results.json。
- data/local/leagues/<competition_id>/postmatch.json。
- data/local/leagues/postmatch_statistics.json。
- data/local/leagues/postmatch_state.json。
- data/local/leagues/postmatch_notification_state.json。
- data/local/leagues/league_postmatch.lock。

不保存 Cookie、token、密钥、原始响应头或不必要的用户数据。本闭环首先产生本地可审计统计，不覆盖赛前 aggregate snapshot；公开 API/页面属于后续独立范围。

## 调度与通知

新 LaunchAgent 固定使用独立 label `xin.celab.football.league-postmatch`，不复用赛前首发 timer。计划北京时间 10:30 和 16:30 运行，RunAtLoad=false，标准输出/错误日志分别写入 `~/Library/Logs/worldcup/league-postmatch.out.log` 和 `league-postmatch.err.log`。安装/加载和首次真实 FotMob probe/live 分别是独立确认门。

只有本轮 newly_settled > 0 时才生成每日摘要，包含各联赛新结算数、hit/miss/push/no_pick、missing closing 和累计样本。不显示下注金额、EV/Edge 或执行建议。

门槛以六联赛正式 aggregate decided = hit + miss 计算，各通知一次：

- 20：只生成健康检查，禁止调参结论。
- 50：允许创建离线候选回测，但不更换公开策略。
- 100：只有在预先锁定的留出集上同时优于当前 match_pick_v3 和市场基准，且逐联赛样本没有严重失衡，才能提出正式优化方案。任何上线仍需单独确认。

## 错误处理

- FotMob 超时/5xx/schema 变更：记录脱敏 source error，保留旧 state，其他联赛继续。
- 比赛未 terminal：保持 pending，下次重试。
- 结果语义/身份不明：不写正式 results。
- closing 缺失：可接受赛果证据，但只计 missing_closing，不补造首选。
- 比分修订/完赛回退/身份冲突：冲突隔离，保留已接受值，人工复核。
- 原子写失败：不提交本轮 receipt，下次可安全重试。
- WxPusher 失败：结算不回滚，intent 留在 outbox，下次优先重试。
- 锁竞争：本轮零外部请求退出，不阻塞下一轮。

## 验证与运维门

- collector 用 data/probe 真实样例覆盖完赛、未完赛、延期、schema 缺失、重复 event、错联赛、身份冲突和非 90 分钟字段。
- closing 回归证明最后合法赛前 snapshot 被选中，开赛后 snapshot 被拒绝。
- 结算复用现有 1X2、大小球、亚洲让球含走水/半赢/半输矩阵。
- dry-run 证明不读 .env、不联网、不写盘、不通知。
- 真实文件回归覆盖非阻塞锁、单联赛故障隔离、跨重启幂等、比分修订 fail closed 和通知 outbox 恢复。
- 完整项目测试、py_compile、git diff --check 和敏感字段扫描必须通过。

真实运维门依次为：真实 FotMob probe 及契约证据；本地 dry-run/shadow；一次显式确认的 live/write 和通知 dry-run；单独确认 LaunchAgent 安装/加载；单独确认首次真实通知。

## 回滚

代码回滚只需停用新 runner，它不覆盖赛前 aggregate snapshot。LaunchAgent 回滚只 bootout 新的独立 label，不影响世界杯、中超、六联赛首发或赔率 timer。已接受赛果和结算不自动删除；如果契约有问题，停止新写入并人工审计。

## 对抗性自审

- 页面待赛数不是可评估样本；只有已完赛、有 observed closing 且可结算的 decided 才计入门槛。
- 聚合样本可能由单一联赛主导；正式优化前必须检查逐联赛覆盖与方向一致性。
- 命中率上升可能是盘口组成变化；候选必须与同样本市场概率基准比较。
- FotMob 免费源可能延迟、修订或改 schema；必须用 terminal 状态、指纹幂等和冲突门禁。
- 赛前 history 归档缺口会导致 missing_closing；不得删除这些场次提高表面命中率。
- 世界杯、中超、legacy 和 reconstructed 必须在 competition + schema + scope 三层排除。
- 20 场只是健康检查，50 场只允许离线候选，100 场也不是自动上线授权。
- 真实 probe、live/write、WxPusher、LaunchAgent、push/merge/deploy 均是独立确认门。

## 验收标准

- 默认 dry-run 对文件、网络、环境变量和通知零副作用。
- 只有通过证据、strict identity 和 90 分钟契约的 terminal FotMob 结果能进入正式 store。
- 每个结算都能追溯到开赛前最后一份合法 observed closing。
- 已接受比分不会被删除或静默修改，重复运行不重复结算/通知。
- 单联赛故障隔离，汇总不混入其他赛事或非 observed 数据。
- 有新结算才发日摘要；20 / 50 / 100 提醒各发一次，通知失败可恢复。
- 候选优化不能绕过留出集、市场基准、逐联赛样本审计和独立上线确认。
