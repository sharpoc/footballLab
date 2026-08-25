# 六联赛 Confirmed Lineup 刷新与通知设计

日期：2026-08-24

状态：设计已确认，待用户审核后编写实施计划。尚未实现、联网、读取 .env、消耗额度、推送、部署、安装定时器或发送真实手机通知。

## 1. 目标与边界

为意甲、巴甲、西甲、英超、德甲和法甲接入 FotMob confirmed starting XI。只有双方各 11 名且状态明确为 confirmed 才接受。接受后经 quota guard 刷新该联赛赔率，复用 MatchPick v3 重新生成每场唯一“本场首选”，发布单份六联赛聚合 snapshot，并通过 WxPusher 发送去重通知。

本阶段不购买正式数据 API。FotMob 属于免费非正式接口，没有 SLA。失败时必须保留原首发和原推荐，predicted、probable、expected、unknown 均不得影响推荐。

当前没有可验收的球员评级或首发强度模型，因此首发名单不直接修改概率。首发后推荐更新只来自“确认首发后重抓市场赔率，再运行既有 MatchPick v3”，不得宣传为球员级模型。

产品继续只公开 MATCH_PICK 或 NO_CLEAN_MARKET，保留“不构成投注建议”，不显示金额、串关、重注、追损、EV/Edge 或 legacy 等级。

## 2. 两级调度

独立 LaunchAgent 每 300 秒唤醒 runner，但 runner 先只读本地 active 联赛赛程：

    未来 90 分钟无未开赛比赛：零 FotMob 请求退出
    距开球 45–90 分钟且未 confirmed：每 15 分钟最多请求一次
    距开球 0–45 分钟且未 confirmed：每 5 分钟最多请求一次
    已 confirmed：停止该场首发轮询
    已开赛、POSTPONED、CANCELLED 或已完赛：停止轮询

同一日期的 calendar 请求合并，不能每场重复请求；只对进入窗口的 match ID 请求 details。状态节流必须跨进程重启保持幂等。

## 3. 首发接受契约

- 主客双方各有且仅有 11 名 starting players。
- payload 必须具有明确 confirmed 语义；22 人完整但语义 unknown 仍拒绝。
- 抓取时间必须早于 kickoff；开球后首次出现不得补造赛前推荐。
- 已接受 confirmed lineup 不能被后续 missing、predicted 或错误响应覆盖。
- lineup 指纹包含 competition、本地 event、FotMob match、kickoff 和双方 11 人 provider player ID。
- 只保存公开安全字段，不保存 Cookie、request headers、账号状态、广告追踪或完整原始响应。

## 4. 比赛身份绑定

FotMob match 与本地 event 必须同时满足：

1. competition 属于六个正式 profile 且 acceptance 为 active；
2. 主客队经 competition-scoped registry 解析为相同 canonical identity；
3. 主客方向一致，不自动对调；
4. UTC kickoff 差值不超过 5 分钟；
5. 双向候选唯一，不存在一对多或多对一。

FotMob ID 不能单独证明身份。未知球队、宽松 slug、跨联赛同名、主客对调、kickoff 超限或重复 match 必须 fail-closed。

## 5. 数据与状态

六联赛首发使用独立 ignored 产物，不复用世界杯 lineups_wc2026.json：

    data/cache/leagues/lineups/<competition_id>.json
    data/local/leagues/lineup_state.json
    data/local/leagues/lineup_notification_state.json
    data/local/leagues/diagnostics/lineup_runs/<run_id>.json

lineup cache 先原子写入，再提交 accepted fingerprint。写入失败不提交 state、不刷新赔率、不发送成功通知。confirmed cache 采用不可降级合并。

## 6. 首发后刷新与发布

每个新 accepted fingerprint 最多触发一次 post-lineup refresh 尝试：

1. 读取 quota ledger 和可用 Key 槽位；
2. quota 未知、耗尽或低于安全下限时阻断；
3. 允许时按 competition 合并，同一 sport key 每轮最多请求一次；
4. 原子保存分联赛 current/history snapshot；
5. 从全部 active 分区已提交缓存重读，构建单份 league-aggregate snapshot；
6. 缺任一 active 分区、snapshot ID 不一致或身份冲突时不发布；
7. HMAC ingest 成功后才允许发送“推荐已更新”通知。

同一轮多场新首发按联赛合并，不能每场重复消耗相同 sport key quota。开球后停止赛前刷新。publish 失败保留脱敏 pending，下轮优先重试 pending，不重复调用赔率 provider。

## 7. WxPusher 通知

每场每种事件按 fingerprint 去重，项目文件不得保存 WxPusher token 或 UID。

成功通知包含：

- 联赛、主队 vs 客队、北京时间开球时间；
- 双方首发已确认及确认时间；
- 原首选到新首选；
- 新安全概率与参考赔率；
- 首选未变时写“首发后复核：方向未变”；
- 研究免责声明。

降级通知：

- 默认距开球 20 分钟仍无 confirmed：一次“首发未确认，保留原推荐”；
- 首发已确认但 quota guard 阻断：一次“首发已保存，赔率刷新被额度保护阻断，保留原推荐”；
- FotMob 瞬时错误不通知；连续失败达到门槛且进入临赛窗口时发一次，恢复后最多发一次；
- publish 失败不发送“推荐已更新”；
- notification 失败不回滚 snapshot，写脱敏 outbox 重试且不再刷新赔率。

## 8. Runner 与 LaunchAgent

新增独立入口：

    runner: worldcup.league_pre_match_runner
    label: xin.celab.football.league-pre-match
    interval: 300 seconds

不修改世界杯 xin.celab.football.pre-match。默认 CLI 必须 dry-run：不读 env、不联网、不写盘、不刷新赔率、不发布、不通知。live 使用分层开关：

    --live-lineups --write-lineups
    --refresh-after-lineups --live-refresh --refresh-guard
    --publish --notify

执行顺序：

    pending publish 重试
    -> 本地赛程门禁
    -> FotMob 轮询与身份验证
    -> lineup 原子提交
    -> quota guard
    -> odds refresh
    -> 分区 snapshot/history
    -> aggregate publish
    -> notification outbox/send
    -> state 提交

runner 使用单实例文件锁。LaunchAgent generator 只生成 plist 或 JSON，不自动 bootstrap。写入 LaunchAgents、bootstrap、kickstart 和真实通知均需独立确认。

## 9. 推送、部署与启用顺序

1. 本地 TDD 实现和完整回归；
2. 用保存 fixture 验证 confirmed、predicted、missing、unknown、身份和幂等；
3. 经确认运行最小 FotMob 真实 probe，仅保存脱敏样例和指纹；
4. 本地 commit；
5. 使用 codex/ PR 分支 push，CI 通过后单独确认 merge；
6. 仅部署已确认 main commit，执行 health、readiness、API、preview smoke，失败回滚；
7. 先安装不带 live refresh、publish、notify 的观察模式 LaunchAgent；
8. 验证赛程门禁与请求频率后，再显式启用完整 live 参数；
9. 手工 kickstart，核对请求数、quota、snapshot ID、公开 API 和 WxPusher 状态。

push、merge、deploy、LaunchAgent 写入/加载、真实 FotMob 请求、The Odds API quota 消耗和真实 WxPusher 通知是独立确认门。

## 10. 错误与回滚

- FotMob 不可用、schema 改变、限流或身份不唯一：保留旧 lineup 和旧推荐，不触发赔率刷新。
- lineup 写入失败：不提交 state，不刷新、不通知成功。
- quota guard 阻断：保留 confirmed lineup 和旧推荐，发送一次降级通知。
- odds refresh 失败：不发布新 aggregate，不发送更新成功通知。
- aggregate/publish 失败：保留线上最后正常 snapshot 和 pending，不重复刷新。
- notification 失败：不回滚已发布 snapshot，重试 outbox。
- ECS smoke 失败：原子回滚 current release。
- LaunchAgent 异常：bootout 新 label，不影响世界杯或中超 timer。

## 11. 验收标准

1. 未来 90 分钟无比赛时，多次唤醒均为 0 次 FotMob 请求。
2. 45–90 分钟最快 15 分钟一次，0–45 分钟最快 5 分钟一次。
3. 只有 confirmed 11+11 被接受，其余状态全部拒绝。
4. 跨联赛、主客对调、kickoff 超限、重复 ID、未知队全部 fail-closed。
5. accepted lineup 原子提交且不可被 missing 降级。
6. 同 fingerprint 最多触发一次刷新，同联赛多场合并为一次请求。
7. quota unknown、低额度或耗尽时不调用赔率 provider，降级通知去重。
8. 推荐变化只来自新赔率和 MatchPick v3，无球员硬编码权重。
9. aggregate 只从已提交分区重读，缺 active 联赛时不覆盖线上版本。
10. 成功、方向未变、缺首发、quota 阻断、publish 失败和通知失败均有幂等测试。
11. dry-run 不读 env、不联网、不写盘、不消耗 quota、不发布、不通知。
12. 只有一个独立六联赛 timer，不更改现有世界杯 timer。
13. 完整测试、py_compile、git diff --check、敏感扫描和 dry-run 零写校验通过。
14. 部署后 health、readiness、聚合 API、单场页和时间戳 smoke 通过，否则回滚。

## 12. 对抗性自审

- 免费接口风险：FotMob 无 SLA，解析必须严格并使用保存 fixture；失败保留原推荐。
- 空请求风险：5 分钟唤醒不能变成全天 5 分钟联网，本地 90 分钟门禁是硬约束。
- 首发语义风险：22 人完整不等于 confirmed，unknown 不猜测。
- 身份风险：必须用 competition、canonical home/away 和 kickoff 唯一 join。
- 模型误导风险：首发仅触发市场赔率复查，不宣称球员级预测。
- 额度风险：多场同联赛必须合并，所有刷新经过 quota guard。
- 通知风暴：事件指纹、连续错误门槛、outbox 和 sent receipt 必须可测。
- 状态一致性：发布成功前不能通知“推荐已更新”；pending 重试不能重复刷新。
- 运行位置：ECS 承载公开查询，本机 LaunchAgent 负责采集与 HMAC 发布；部署与 timer 启用是两件事。
- 回滚边界：ECS release rollback 与本机 LaunchAgent bootout 独立执行。

审查结论：该设计能以免费数据源接入 confirmed starting XI，但可靠性上限受 FotMob 影响。任何数据语义、身份或时间无法证明时，必须保留原推荐并降级通知，不能使用 predicted lineup 或猜测数据补齐。
