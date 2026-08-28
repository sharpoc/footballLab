# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

历史归档：[docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md](docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md)（169 条）

较早记录压缩摘要（2026-07-10 至 2026-07-19）：完成 MatchPick v3、首选鲜度、中超俱乐部评级门槛、已开赛待赛果展示、延期状态、quota 槽位切换和世界杯赛后同步等阶段；当前槽位数以最新记录和 README 为准。保留的关键约束已同步到 README、AGENTS/CLAUDE 与 Git 历史。2026-07-19 首次赛后 live 因当时外部配置受阻，未产生业务写入。

## 2026-08-26 FotMob 赛果合同修复与离线 Gate A 复核

- 修复后的本地合同使用 `/api/data/matches` / `/api/data/matchDetails`、Brasileirão provider ID `268`、detail `general.matchTimeUTCDate`、calendar/detail FT+比分一致与无加时/点球/aggregate 的复合 90 分钟证明；404 单独投影为 `provider_contract_drift`。Task 4 新增从同一 hardened fd 读取、hash 并评估保存 bundle 的完全离线 CLI，不写正式 evidence/acceptance 或激活 Gate。Final review fix 又将同一 competition 的所有 due UTC 日期改为内存 staging 后一次 merge：typed drift 丢弃本轮全部新 receipt，ordinary calendar/detail failure 保留健康 staging 并显式 partial，任何当前 provider failure 都不再阻断旧 committed receipt 派生结算。`observed_clock` 在每个日期的 calendar/details 响应完整捕获后单独采样 `captured_at`，naive 时间 fail closed。
- 使用 first Gate A 六个 exact 保存路径与 timezone-aware `2026-08-26T00:00:00+00:00` 复核，首次发现 evaluator 错把 provider-native wrapper `calendar_date=YYYYMMDD` 限定为 ISO；修复为严格 8 位 ASCII 真实日期后，不改样例并原子重跑同一路径，最终为 `status=partial`、`4 verified / 2 blocked`。意甲/西甲/英超/法甲各接受唯一 event；Brazil 为 `sample_detail_missing`，Bundesliga 为 `no_current_season_finished_match`。ignored audit SHA-256 为 `91602330757587553da7bc64bd88c3cb79043e2d2511e9bb0ad1496eb2796cce`。这不是 operational Gate A 完成或激活；两项真实 re-probe 与任何 evidence/acceptance 写入仍须另行确认。
- README 与项目 AGENTS/CLAUDE 已同步 routes、ID、kickoff、复合证明、404、offline evaluator 和剩余 blockers；按获准 retention 将最旧 5 条完整迁移到既有 archive，当前 recent 恰好 20 条、archive 169 条。
- Final fix 严格 TDD 新增 4 项回归：当前实现先为 `0/4` RED（ordinary failure 持续搁置旧 receipt、typed drift 留下部分新 receipt、缺 `observed_clock` 接口），最小修复后为 `4/4` GREEN；整个 runner 模块 `38/38`，指定 runtime 完整回归 `1502/1502 tests passed, 1 optional fastapi module skipped`，相关 production/test 模块 `py_compile`、`git diff --check` 和 tracked diff 敏感值扫描通过。默认 runner dry-run 返回 `status=dry_run`，前后 `data/local` + `data/cache` manifest SHA-256 均为空集摘要 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`，且 `read_env/called_fotmob/wrote/notified=false`。实现与离线验收阶段未联网、未改真实 acceptance/evidence、未激活 Gate、未消耗 quota、未通知或操作 timer。
- PR #13 已 squash merge 到远端 `main` commit `1b43fd48ea0a0f790df1d788e4a02dbac079d87e`。部署使用该精确提交创建 `/opt/worldcup/releases/1b43fd48ea0a0f790df1d788e4a02dbac079d87e`，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/d82bd98e4f31cd968ffdc5656972c63b058e41d2` 原子切换到新 release；`worldcup.service` 与 Nginx 均为 `active`，ECS 本机 `/readyz` 预热成功，公网 `/healthz`、`/api/matches`、`/preview` 均返回 200，免责声明与公开禁词检查通过。部署未读取 `.env`、未调用 The Odds API、未改 quota、未安装 LaunchAgent、未发布 snapshot，也未激活 operational Gate A；自动回滚未触发。
- 巴甲 re-probe 经单独确认后仅请求 FotMob calendar/detail 各 1 次，The Odds API 请求与 quota 变化均为 0；保存样例 `20260824-5103595-reprobe.json`（SHA-256 `d6dce663704e151bfba779d1e5c83838437d9102f70ab3354605fdab468d4620`）完整证明 event `5103595` 的 FT、比分、ISO 开球和无加时/点球/aggregate。首次严格评估因 registry 缺少 provider 原名 `Botafogo RJ` / `Athletico Paranaense` 而 `unmatched_team`；TDD 增加 competition-scoped alias 后同一样例 `1/1 verified`。本轮仍未写正式 provider evidence/acceptance，aggregate Gate A 继续 partial，Bundesliga 当前赛季 FT blocker 未解除。

## 2026-08-25 六联赛赛后闭环离线实现

- Task 1–6 已在本地实现严格 FotMob 90 分钟解析、路径/内容/registry 绑定的 acceptance、单调分区 results/closing store、纯 due planner、observed schema v2 累计结算、逐联赛/六联赛统计、可恢复通知 outbox、dry-run-first runner 和只生成 plist 的 LaunchAgent generator。缺 closing 显式记 `missing_closing` / `skipped_no_closing`，比分修订/finished 回退/身份冲突保留旧值并 fail closed。
- 默认 `python3 -m worldcup.league_postmatch_runner --root <repo>` 只读 acceptance/evidence/history/result receipt，不读 runner/notification state，不读 `.env`、不联网、不写盘、不通知，不调用 The Odds API scores 或改动 quota。live/write 必须同时显式给出；不带 `--notify` 的新 transition 会被静默消费，不留待以后补发的摘要。已有 notification pending 则先于 provider 检查：带 `--notify` 时重试，不带时返回 `notification_pending` 并阻断 provider。WxPusher 接受成功到 sent receipt 持久化之间仍有 at-least-once 重复发送窗口。
- 赛后 generator 的独立 label 为 `xin.celab.football.league-postmatch`，计划每天北京时间 10:30 / 16:30、`RunAtLoad=false`，与赛前 `xin.celab.football.league-pre-match` 的每 300 秒 confirmed-lineup observer 无关。当前未安装/加载赛后 plist，未验收真实 FotMob 赛后样例，未执行 live/write，未发送手机通知，未 push/merge/deploy。
- 后续必须分开确认 Gate A 真实 FotMob probe、Gate B 首次静默 live/write 与立即幂等重跑、Gate C 赛后 LaunchAgent 安装（`RunAtLoad=false`、不 kickstart）、Gate D 首次真实 WxPusher。Gate A 无法证明 terminal 90 分钟语义时必须保持 blocked；20/50/100 仅是健康检查/离线候选/正式优化审查门，不自动调参或上线。本闭环只用于研究与离线评估，不构成投注建议，不输出下注金额或执行建议。
- Task 7 新鲜验证：指定 runtime 两次完整回归均为 `1446/1446 tests passed, 1 optional fastapi module skipped`；六个赛后生产模块 `py_compile`、`git diff --check` 和变更 diff 敏感值扫描通过。真实文件系统 dry-run 前后 quota/leagues/results/state/notification manifest SHA-256 均为 `d4b7297def72fad76ec1cc62e9797c3ea6b7f9608e5fda5bb12c6c1ca2e933d4`，输出 `mode=dry_run` 且 `read_env/called_fotmob/wrote/notified=false`；观察/full-live plist 预览均未生成文件。
- 最终分支审查的 4 个 Important 和 1 个 Minor 已按 TDD 修复：The Odds parser 精确绑定 `theoddsapi_scores_v1`，legacy lifecycle 通过 Task 2 committed receipt 适配器继续供当前 scheduled publisher 使用；legacy postmatch/statistics 已隔离到 `data/local/leagues/legacy_theoddsapi/`，不读写 FotMob 正式 aggregate/state/notification。共享 `closing.json` 改为同一 `flock` 内读取、校验、单调 merge 和原子写入，拒绝删除、身份变化、时间倒退和同时间 decision 冲突。
- FotMob 正式汇总新增 `postmatch_components.json` 持久化逐联赛 last-known-good 验证统计；单个分区不可读、结构无效或为 legacy shape 时显式标 `stale/blocked`并保留旧计数，健康联赛仍可推进 statistics/state/通知。README 已更正 closing schema v2 口径：`MATCH_PICK` 进入 `hit/miss/push`，`NO_CLEAN_MARKET` 只进入 `no_pick`/coverage。本轮未宣称 FotMob live 已激活。
- 最终新鲜验证：跨模块聚焦回归 `77/77`，指定 runtime 完整回归 `1453/1453 tests passed, 1 optional fastapi module skipped`，相关模块/测试 `py_compile`、`git diff --check` 与变更 diff 敏感值扫描通过。默认 dry-run 前后 manifest SHA-256 均为 `48bd7359a6bfbc2489f703ca41c69c165fc5baccc82d4618b73e760c7e634e99`，输出 `mode=dry_run` 且 `read_env/called_fotmob/wrote/notified=false`。未联网、未执行 live/write/notify，未安装调度器，未 push/PR/merge/deploy；Gates A–D 仍需分别确认。
- 最终审查 round 2 已将 provider evidence 物理隔离：FotMob 正式 runner 只读 `data/local/leagues/<competition_id>/providers/fotmob/result_contract_evidence.json`，legacy lifecycle 只读 `data/local/leagues/legacy_theoddsapi/<competition_id>/result_contract_evidence.json`。旧通用 evidence 仅在精确验证为 `theoddsapi_scores_v1` 后于 write 轮次按原字节、持锁原子复制到 legacy 路径；dry-run 不写，已有 legacy/FotMob evidence 不覆盖。缺失或 schema 错配会显式 `legacy_result_contract_evidence_missing/invalid`，不再以冻结统计假装 `ready/stored`。
- LKG 选择已从“仅结构验证”改为“结构 + 逐分区单调验证”：component 持久化 competition/provider/postmatch schema、tally/sample/coverage 和 settled/result membership。合法空分区、tally/coverage 回退或 event membership 替换均标 `postmatch_partition_regression`并保留 LKG，健康联赛仍可结算、推进 aggregate/state 并通知；身份/schema/membership 不兼容的 LKG entry 不会覆盖验证过的 previous statistics。新鲜聚焦回归 `82/82`、完整回归 `1458/1458 tests passed, 1 optional fastapi module skipped`，`py_compile`、`git diff --check`、敏感值扫描与默认 dry-run manifest 验证通过；manifest 前后均为 `48bd7359a6bfbc2489f703ca41c69c165fc5baccc82d4618b73e760c7e634e99`。未联网或执行真实 live/write/notify/timer/push/PR/merge/deploy，Gates A–D 仍未激活。

## 2026-08-25 六联赛确认首发功能部署

- PR #7 已 squash merge 到远端 `main` commit `3c3db4101092f265804871f3650dbe2b8565890b`；合并前指定 runtime 完整回归 `1347/1347 tests passed, 1 optional fastapi module skipped`，CI `tests` 通过。
- 经独立部署确认后，以 `origin/main` 精确提交创建 `/opt/worldcup/releases/3c3db4101092f265804871f3650dbe2b8565890b`，原子切换 `/opt/worldcup/current`；`worldcup.service` 与 Nginx 均为 `active`，ECS 本机 `/readyz` 预热成功，公网 `/healthz`、`/api/matches`、`/preview` 均返回 200，预览免责声明与公开字段安全检查通过。
- 本次部署未读取/修改 `.env` 或密钥，未调用 The Odds API，未发布业务 snapshot，未安装或修改 LaunchAgent，未发送 WxPusher 通知；确认首发链路仍需后续独立完成定时器安装与真实 provider/通知门禁。
- 本地旧 `main` 已完整备份为 `codex/local-main-before-sync-20260825`（`5d58051826ed6ca8dae4f7f21945fb528a313208`），随后精确对齐远端 squash commit。观察模式 LaunchAgent `xin.celab.football.league-pre-match` 已写入并 bootstrap，每 300 秒运行 `--live-lineups --write-lineups`；实际注册参数不含 `--live-refresh`、`--refresh-after-lineups`、`--publish` 或 `--notify`，`RunAtLoad=false`，未主动 kickstart。
- 经后续独立确认，publisher readiness `4/4` 通过，正式 endpoint 由现有生产 LaunchAgent、README 和历史成功 ingest 记录交叉确认为 `https://football.celab.xin/api/ingest/snapshot`；tertiary quota 只读基线为 remaining 465。该 LaunchAgent 已升级并重新 bootstrap 为完整 live 参数：confirmed lineup 后才运行 quota guard、联赛赔率刷新、聚合发布与去重通知；德甲仍因 `identity_verified` 而非 `active` 被排除。`RunAtLoad=false`，未主动 kickstart，安装时未调用 FotMob/The Odds API、未发布或发送通知；观察模式 plist 备份保存在 `~/Library/Logs/worldcup/`，不参与登录自动加载。
- 只读根因排查确认公网 `/api/matches` 仍只有 7 场 `csl_2026`：五联赛本地 87 场均为验收用 `offline_prediction`，正式 `data/cache/leagues/<competition>/snapshot.json` 尚未生成；首发 runner 仅在 `newly_confirmed` 后增量刷新，不承担首次生产发布。新增 `worldcup.league_bootstrap_publish` 首次 bootstrap CLI：默认零副作用 dry-run，真实执行须 `--live --write --force-initial`，绑定 acceptance fingerprint、全部 active 联赛未来 event IDs、严格 identity、逐响应 quota 和完整聚合 HMAC 发布；partial/receipt 缺口不发布，只有 ingest `stored/duplicate` 后写完成 state，发布失败留下的分区可安全重试。独立审查发现并修复 live 并发重复消耗/发布风险：非阻塞单实例锁覆盖 completion 检查、刷新、发布与 state commit，锁竞争在 env/provider 前阻断。真实本地 dry-run 选中英超/西甲/法甲/意甲/巴甲 5 个 active 联赛、63 场未来事件、预计 15 credits，德甲排除，quota 与联赛文件集合前后不变；TDD bootstrap `9/9`、完整回归 `1356/1356 tests passed, 1 optional fastapi module skipped`，`py_compile` 与 `git diff --check` 通过。实现阶段尚未调用真实 provider、写正式分区或发布。
- 首次 bootstrap 实现已通过 PR #8 squash merge 到远端 `main` commit `88b011690331ebeaa09026d2f561144084369d29`，CI 通过；经独立部署确认后创建并切换 `/opt/worldcup/releases/88b011690331ebeaa09026d2f561144084369d29`，上一 release 为 `3c3db4101092f265804871f3650dbe2b8565890b`。`worldcup.service`、Nginx 均为 `active`，ECS 本机 `/readyz` 预热成功，公网 `/healthz`、`/api/matches`、`/preview` 均返回 200。部署阶段未读取 `.env`、未调用 The Odds API、未发布五联赛 snapshot、未改 LaunchAgent；真实首次 bootstrap 仍需独立确认执行。
- 经独立确认执行真实五联赛首次 bootstrap：preflight 选中 5 个 active 联赛、63 个绑定未来 event、预计 15 credits，tertiary remaining 465；live 最终提交英超 20、西甲 14、法甲 9、意甲 20、巴甲 10 场共 73 个 schema v2 `MATCH_PICK`，identity mismatch 为 0，聚合 snapshot `league-aggregate-ba837d138c76921bdfe9` ingest 返回 `stored`，完成 state 已绑定 5 个 component 和 acceptance fingerprint，重复 dry-run 返回 `bootstrap_already_complete`。tertiary/legacy quota 均更新为 remaining 450 / used 50，实际消耗 15。公网仍只显示 7 场中超；只读追踪确认 SQLite 已存入约 536KB 聚合 snapshot，但 `query._active_competition_ids()` 按 `fixture_policy != dry_run_probe` 只检索世界杯/中超，漏掉固定六联赛，故 latest view 无法选中 `multi_league`。
- 经独立确认已在 `codex/league-public-query` 修复上述公网查询缺口：新增真实 SQLite 回归，先复现“中超 snapshot + 更新的 multi_league 聚合仍只返回中超”，再让 `_active_competition_ids()` 在原世界杯/中超基础上追加固定 `FORMAL_SINGLE_MATCH_IDS` 检索候选；聚合中的比赛仍按 match 自身 competition 投影，acceptance 中无比赛的德甲不会生成公开行。回归 RED→GREEN，`tests/test_query.py` 为 `21/21`，完整回归 `1357/1357 tests passed, 1 optional fastapi module skipped`，`py_compile` 与 `git diff --check` 通过；实现阶段未调用 provider、未消耗 quota、未写 ECS 或重新发布。
- PR #9 已 squash merge 到 `main` commit `a2775d37b728100003657ac6020d116e402e425b`，CI 通过。首次部署的 release 切换、service/Nginx 和 ECS 本机 `/readyz` 最终成功，但公网 `/preview` 在 15 秒 smoke 窗口超时，脚本自动回滚到 `88b011690331ebeaa09026d2f561144084369d29`。只读根因证据显示新版本 `/readyz` 首轮约 19 秒，两代公开视图 SQLite 查询 77.194 秒、HTML 渲染仅 0.037 秒；根因是六个联赛 ID 分别对无索引 `snapshot_json LIKE` 执行全表检索，不是 Nginx 或公网链路。
- 经独立确认已按 TDD 实现公网查询性能修复：真实 SQLite trace 回归先以 `8` 次 competition JSON 查询 RED，再将六个内部 ID 收敛为单一顶层 `multi_league` 聚合 ID，GREEN 为世界杯/中超/聚合共 `3` 次并继续验证中超、英超、西甲真实行与德甲无伪造行。生产 SQLite 等价三候选/两代只读 SQL 实测 9.424 秒（世界杯 2、中超 2、`multi_league` 1 条），进入 15 秒 smoke 窗口。定向 query `21/21`、完整回归 `1357/1357 tests passed, 1 optional fastapi module skipped`，`py_compile` 和 `git diff --check` 通过。本阶段未重新部署、未写生产 DB、未调用 provider 或消耗 quota。
- 查询性能修复已通过 PR #10 squash merge 到 `main` commit `db0b1aab76d5af6186993b586086be09deb6d419`，CI 通过。经独立部署确认，该 release 的 ECS `/readyz` 和公网 `/healthz`、`/api/matches`、`/preview` 全部在窗口内返回 200，性能超时已消失；但 smoke 将英超真实球队 `Newcastle United` 中的 `unit` 子串误判为禁词，再次自动回滚到 `88b011690331ebeaa09026d2f561144084369d29`。按 TDD 新增边界回归：`Newcastle United` / `united_states` 允许，独立 `unit` 仍阻断；实现只对 `unit` 使用 ASCII identifier 边界，其他禁词不放宽。边界回归 `2/2`、完整回归 `1359/1359 tests passed, 1 optional fastapi module skipped`，`py_compile` 和 `git diff --check` 通过。本阶段未重新部署、未调用 provider、未消耗 quota、未写生产 DB。
- smoke 误报修复已通过 PR #11 squash merge 到 `main` commit `384c90c3d5842c9fb706a739bc7e2234aadac27b`，CI 通过，并已成功部署为 `/opt/worldcup/releases/384c90c3d5842c9fb706a739bc7e2234aadac27b`。ECS `/readyz` 成功，service/Nginx 均为 `active`，公网 `/healthz`、`/api/matches`、`/preview` 均返回 200，smoke 禁词命中为 0 且免责声明存在。独立公网验收共 80 场：中超 7、英超 20、西甲 14、法甲 9、意甲 20、巴甲 10；德甲仍因无已验收真实比赛而不伪造公开行。部署未读取/修改 secret，未调用 provider、未消耗 quota、未发布新 snapshot、未修改 LaunchAgent 或 DB schema。
- 经独立确认已在 `codex/chinese-club-labels` 按 TDD 实现单场分析中文俱乐部展示与下拉清理：六联赛 116 支已验收 canonical 全部配置 competition-scoped 中文名，公网当前 98 个去重俱乐部身份只读覆盖为 `98/98`；页面显示中文但 `/api/matches` 仍保留英文队名，未知队回退英文。下拉列表不再从 finished-only 记录生成世界杯入口，但世界杯历史数据/API 兼容不删除，固定六联赛入口保留。三组验收回归 RED→GREEN，identity `5/5`、preview `23/23`、完整回归 `1362/1362 tests passed, 1 optional fastapi module skipped`，`py_compile` 与 `git diff --check` 通过；未调用 provider、未消耗 quota、未写生产 DB或部署。
- 合并前独立审查发现六联赛 scoped 查询未命中时会误落入国家队全局中文表；已用 `Brazil` 在巴甲 scope、`Arsenal` 在西甲 scope 的真实格式化反例复现，并改为正式六联赛 scoped miss 直接保留原文；世界杯、中超与无 scope 的既有翻译不变。
- 中文俱乐部展示与下拉清理已通过 PR #12 squash merge 到 `main` commit `d82bd98e4f31cd968ffdc5656972c63b058e41d2`，CI 与合并前完整回归 `1363/1363` 通过。经独立部署确认，已创建并原子切换 `/opt/worldcup/releases/d82bd98e4f31cd968ffdc5656972c63b058e41d2`，`worldcup.service` 与 Nginx 均为 `active`，readyz 及公网 `/healthz`、`/api/matches`、`/preview` 均返回 200。功能验收确认 80 场 API 仍保留英文 canonical，页面已显示六联赛中文队名，finished-only 世界杯下拉入口不存在。部署未读取/修改 secret，未调用 provider、未消耗 quota、未发布新 snapshot、未修改 LaunchAgent 或 DB schema。
- 已确认六联赛赛后结算与信号评估闭环设计：推荐免费 FotMob 公开 snapshot + 严格 90 分钟契约门禁，不使用 The Odds API scores 争抢赔率 quota；逐联赛 closing/results/postmatch/statistics 隔离，只结算赛前最后一份 observed schema v2 closing，每日 10:30/16:30 计划唤醒，有新结算才发日摘要，20/50/100 decided 样本分别触发健康检查/离线候选/正式优化审查提醒。当前仅写设计，未联网 probe、未实现 runner、未安装调度器或发送通知。
- 已基于上述设计编写六联赛赛后闭环实施计划：拆分 FotMob 赛果契约、单调结果/closing store、纯 due planner 与累计结算、可恢复通知 outbox、dry-run-first live runner、LaunchAgent generator 和文档/完整验证 7 个 TDD 任务；真实 FotMob probe、首次 live/write、定时器安装和首次真实通知保留为 4 个独立运维门。计划阶段未实现代码、未联网、未写运行 state、未安装定时器或发送通知。

## 2026-08-24 The Odds API 五 Key 轮换

- 显式 Key 槽位从 primary / secondary / tertiary 扩展为 primary / secondary / tertiary / quaternary / quinary，保持现有顺序轮换和低额度策略：当前槽位剩余额度 <=30 时优先选下一个未探测或 >30 的槽位，全部无新鲜额度时才选仍有余额的最早槽位，全耗尽则暂停。
- 同步 `worldcup.theoddsapi_keys`、readiness 必需变量名、scheduler/publish 安全提示、`.env.example`、README 和 AGENTS/CLAUDE；真实 Key 未写入文件，聊天中暴露的 Key 仍应撤销后重生。
- TDD 分别验证第四、第五槽 RED→GREEN，readiness 缺槽变量名 RED→GREEN；聚焦 Key 测试 `13/13`，完整回归 `1129/1129 tests passed, 1 optional fastapi module skipped`，`py_compile` 与 `git diff --check` 通过。未读取/修改 `.env`、未联网、未消耗 quota、未 commit/push/deploy。

## 2026-08-24 六联赛单场分析离线闭环

- 意甲、巴甲、西甲、英超、德甲、法甲新增正式但默认关闭的 `league_v1` profile、市场共识 snapshot、单场页面固定入口与状态、严格赛前 closing、90 分钟赛果验证门、结算、分联赛统计和六联赛汇总。
- batch runner 默认零写入 dry-run；live/write 明确阻断为 `live_acceptance_not_enabled`。未读取 `.env`、未联网、未消耗 quota、未生成正式 closing/统计、未发布或部署。
- TDD 聚焦验证通过；共享 `load_config()` 污染由测试内 deep copy 修复并复现验证；批处理补充单联赛失败隔离与 `partial` 状态回归；最终完整回归 `1148/1148 tests passed, 1 optional fastapi module skipped`，六个新模块 `py_compile` 与 `git diff --check` 通过。
- 后续 live 激活设计已确认：六联赛同时参加赛程发现，按最近 kickoff 动态优先，逐联赛独立通过 odds/球队身份/90 分钟 scores 门禁后启用；真实请求逐 key 预估成本并在每次响应后重算 quota，已开赛场不得补造首选。设计阶段未联网、未读 `.env`、未消耗 quota、未解除 live 门或部署。
- Live 激活实施计划已拆为动态 planner、验收状态机、严格球队身份、scores 语义证据、probe bundle、active refresh、closing/postmatch/statistics、单一 scheduler 八个可独立验收任务；真实 probe、active 写入、LaunchAgent、推送和部署继续作为计划外独立确认门。
- Live 激活本地实现新增动态 planner、证据绑定 acceptance、严格 identity、通用 scores source、90 分钟 result evidence、脱敏 probe、分区 snapshot/history store 和 pending-first scheduler；live 缺任何四类证据或严格 registry 都在 env/写盘前阻断，正式赛果同样禁止 slug identity。TDD 新增/扩展 28 项测试，完整回归 `1176/1176 tests passed, 1 optional fastapi module skipped`，`py_compile`、`git diff --check` 和敏感字段扫描通过。实现阶段未联网、未读取 `.env`、未消耗 quota、未安装 timer、未推送或部署。
- 意甲真实接受门已完成：20 支 The Odds API 样本球队进入显式 competition-scoped registry，未知球队和跨联赛名称仍 fail-closed；12 场 h2h 样本全部生成唯一 `MATCH_PICK`，identity 缺口为 0，8 场 scores 与意甲官方赛报一致。经单独确认后，ignored `data/local/leagues/acceptance.json` 已仅将意甲写为正式 `active`；scheduler dry-run 在北京时间约 16:00 返回 12 场均 `not_due`、请求数 0、预估 credits 0，最早 T-6h 刷新点为 18:30。TDD RED→GREEN 后完整回归 `1177/1177 tests passed, 1 optional fastapi module skipped`，`py_compile`、`git diff --check` 和敏感扫描通过；本阶段未联网、未消耗 quota、未安装 timer、未推送/部署。
- 剩余五联赛真实接受门已按联赛隔离推进：英超/西甲/法甲/巴甲/德甲分别完成一次 `eu/h2h` 和 `scores?daysFrom=3` 探测，实际消耗 15 credits，tertiary 剩余 480；球队 identity 新增覆盖 20/20/18/18/20 支真实样本队。英超、西甲、法甲、巴甲赛果已经官方联赛或球会赛报交叉核验并写为正式 `active`；德甲因 2026/27 尚无完赛样本仅写为 `identity_verified`，scheduler 明确以 `acceptance_blocked` 排除。五联赛离线预测共 75 场、全部为唯一 `MATCH_PICK`、identity 缺口 0；六联赛 scheduler dry-run 当前请求数 0、预估 credits 0。完整回归 `1178/1178 tests passed, 1 optional fastapi module skipped`，`py_compile`、`git diff --check` 和敏感扫描通过；未安装 timer、未生成 live snapshot、未发布/推送/部署。
- 阶段 A 本地生产编排已补齐：单命令读取正式 acceptance/quota/events 生成 due plan，仅让 `active` 联赛进入 lifecycle；封盘、严格赛果、postmatch 和独立统计按联赛失败隔离。公开链路改为一次发布单份聚合 snapshot，未刷新的 active 联赛从分区缓存补齐，德甲仍排除。完整回归 `1184/1184 tests passed, 1 optional fastapi module skipped`；本阶段未联网、未读 `.env`、未消耗 quota、未安装 timer、未推送或部署。
- Confirmed lineup Task 1–7 最终 review 的 5 个 Important 已全部按 TDD 修复：live CLI 禁止用 `--now` 覆盖安全时钟，Task 4/5/7 用注入式单调 UTC clock 并在 provider 响应后与 commit/publish 前重新判断 kickoff；未 claim Task 4 pending 与同轮新 receipt 合并，同 competition/sport key 不二次 fetch；已开球、terminal/inactive 和缺 context receipt 逐条 `blocked/retryable` 隔离，quota warning 不再误清 staging context；Task 4 稳定产出严格逐场 `source_events`，Task 7 对 membership/count fail closed，真实 Task 4→Task 7 已验证连续失败门槛、恢复、同联赛单点和跨联赛不误恢复；所有 `stored/duplicate` 成功 publication（不再仅 ACK 特例）都在 bind/通知/Task 7 清理前验证 aggregate 精确覆盖 acceptance active 集合且 snapshot ID/联赛声明匹配 committed cache。最终新鲜验证：Task 1–7 focused `167/167`，完整回归 `1337/1337 tests passed, 1 optional fastapi module skipped`；compile/diff/sensitive/CLI 零写入验证通过。本阶段未联网、未读真实 `.env`、未消耗 quota、未真实 publish/notify、未安装 timer、未 push/merge/deploy。
- Final residual review 的 3 个 load-bearing Important 已继续按 TDD 收口：Task 5/7 对 staged、pending、committed 与 success-notify receipt 在 provider、publish、通知前重读逐场 context 并再次采样单调时钟，`POSTPONED/CANCELLED/FINISHED` 或已开赛场逐 receipt 隔离；同 competition 共享 provider 响应跨最早 kickoff 时重建合法 attempt membership、过滤过期 event，并拒绝 snapshot builder 重新带回禁用 event，使较晚合法场在一次 provider fetch 内完成；FotMob predicted/schema/identity/parser rejection 统一为安全 `rejected`，仅严格 confirmed event-scoped evidence 可 `succeeded` 并恢复 source episode。最终新鲜验证：Task 1–7 focused `177/177`，完整回归 `1347/1347 tests passed, 1 optional fastapi module skipped`；changed-file compile、`git diff --check`、tracked diff sensitive scan 与 CLI 零写入 dry-run 均通过。本阶段未联网、未读真实 `.env`、未消耗 quota、未真实 publish/notify、未安装 timer、未 push/merge/deploy。
- 经单独确认执行 FotMob 真实 probe 门禁检查：当时本地 active 赛程在未来 90 分钟内候选为 0，runner 返回 `lineups_checked / no_due`、退出码 0；`calendar_fetch_count=0`、`details_fetch_count=0`、`request_count=0`、首发 cache/state 写入均为 0，没有读 `.env`、调用 The Odds API、消耗 quota、publish 或 WxPusher。仅生成 ignored 单实例锁 `data/local/leagues/league_pre_match.lock`，证明“无比赛零 FotMob 请求”门禁；因无候选场次，尚未验证 FotMob 当前真实 lineup schema。

## 2026-08-20 中超赛后样本 Sentinel

- 已确认独立 `worldcup.csl_postmatch_sentinel` 方向：复用“双源赛果接受 → postmatch shadow”触发，只监控数据链路异常、恢复和正式样本首次达到 50；不根据命中率或盘口方向报警，不自动调参或解除 `club_rating_pending`。
- 当前真实 ignored shadow 只读基线为 174 场已验证结果、46 场 closing、38 个正式 decision，`20 hit / 18 miss`，`sample_too_small=true`；现有 128 个 closing 缺口和 8 个 decision 缺口只作为通知基线，coverage 仍完整保留，不声称已修复。
- 已实现 `worldcup/csl_postmatch_sentinel.py`（严格输入契约、纯 evaluator、锁/原子 state/outbox、脱敏 CLI）与 `worldcup/csl_scheduled_publish.py`（accepted result → shadow success → sentinel → odds 的非阻断接线、`--no-notify`）；对应测试为 `tests/test_csl_postmatch_sentinel.py` 与 `tests/test_csl_scheduled_publish.py`。sentinel 摘要仅存在 scheduler-local 返回值，最终 publish-body 捕获测试保证其不进入公开 snapshot 或 HMAC body。
- Fix Round1：合法 standalone dry-run / CLI 从同一次已验证 projection 输出严格 `decision_count` 与 `sample_too_small`，不二次读取报告；错误/输入不可读时省略。scheduler 白名单继续丢弃这两个字段，既不扩大其 local result，也不进入公开 snapshot/HMAC body。
- 最终验证：`py_compile` 通过；聚焦 sentinel `31/31`、scheduler `45/45`；指定 runtime 完整回归为 `1126/1126 tests passed, 1 module skipped (optional fastapi)`。实现已通过 PR #5 squash merge 到远端 `main` commit `8a500bec65bc50a26d2043ec9edd5817bfcea70d`；本地主工作区已对齐该提交，旧本地 `main` 完整备份为 `codex/local-main-before-sync-20260820`。
- 经单独批准后，真实 ignored baseline 已用同步后的 `main` 显式 `--write` 激活且未传 `--notify`：首次返回 `status=stored`，第二次返回 `status=unchanged`，两次均为 `event_count=0`、`notification_status=not_attempted`、`decision_count=38`、`sample_too_small=true`。state 固定记录 128 个 closing 缺口、8 个 decision 缺口、38 个正式样本，`threshold_notified=false`、active/outbox 均为空；第二次运行 state SHA256 保持 `b8f3d8064d09dad6ca6a9485243f904027bfbda48f8b8bd86bedcb86e6f491e1`，敏感字段扫描为空。
- 现有 `xin.celab.football.csl-scheduled-publish` LaunchAgent 继续每 900 秒唤醒 `worldcup.csl_scheduled_publish --live`，最近退出码为 0，不新增 timer；只读 `worldcup.ops_check` 返回 `ok=true`、0 errors。设计文档：`docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md`；实施计划：`docs/superpowers/plans/2026-08-20-csl-postmatch-sentinel.md`。未创建 `ARCHITECTURE.md`，未发送真实 WxPusher、未调用 provider、未新增 quota 消耗、未部署。

## 2026-08-13 中超 closing coverage foundation 文档与真实本地验收

- 同步 `README.md`、`docs/superpowers/data-contract.md`、`AGENTS.md` 与 `CLAUDE.md`：初始 128 个 match ID 是固定重建资格 membership，`2026-06-29` 仅为 bootstrap cutoff；canonical report 做全量 finished/history reconciliation，pending 只承接恢复；正式 headline 仅结算 observed schema v2 `match_pick_v3` 的 `MATCH_PICK`，reconstructed 保持独立且不得混算。本轮 implementation/documentation 已通过 PR #2 squash merge 到远程 `main` commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`，CI `tests` 通过；已部署到 `/opt/worldcup/releases/5d006be240fd42ef320e0e5ec1aee69992f0e9c9`，内部 health/readiness 与公网 `/healthz`、`/api/matches`、`/preview` 均验证通过。本条记录随 SSH 部署加固分支纳入本地版本历史。
- 最终完整项目验证：指定 runtime `1062/1062 tests passed`，`test_fastapi_app.py` 因 optional `fastapi` 不可用显式跳过 1 个 module。真实 `--initial-manifest` dry-run 返回 `matches=128`、`observed_cutoff=2026-06-29`，且运行前后 manifest/report/pending 均不存在，确认零写入。最终对抗性审查后，report strict validator 还强制 `initial_missing_count=128` 与固定 membership SHA256，重算 derived fields / fingerprint 后的 127-ID 子集或替换 128-ID 均 fail closed；真实 report 只读校验通过且文件 SHA256 不变。
- 显式 ignored 写入后，`initial_missing_manifest.json` 有 128 个唯一 ID，固定 membership hash 为 `530acaa872d753c911861e2cab1e1bf6a2a0a87c595028d9c5e369523a7f6a40`，日期范围 `2026-03-06..2026-06-28`，每行均通过精确 UTC kickoff 与 `cfl_official` / `sevenm` 双 source ID 复验。canonical report 覆盖 171 个 accepted `csl_2026` / `2026` results：43 observed closing、35 observed current decisions、8 observed missing-current-decision、128 missing，正式 observed tally 为 `17 hit / 18 miss / 0 push / 0 no_pick`，`sample_too_small=true`；没有 reconstructed tally 或 combined rate，不能据此声称重建提高胜率。
- 幂等与安全：第二次 manifest/report 写入均返回 `unchanged`；manifest SHA256 `9698786a6f5eed01d8a0cc7990c29a6fa63f6b33cafe93145b08cb6c9a6707da`、report SHA256 `7357d524770d89fb49f9a334b30c6684b2adf27236a2b10dc9234eab0f8e2211` 前后不变，pending 成功清除。两份 ignored artifact 对 `Authorization`、`Cookie`、`api_key`、`secret`、`.env` 和 request headers 的扫描为空。`csl_scheduled_publish` 原样命令返回 `status=dry_run`、`refresh=null`、`publish=null`，manifest/report/quota hash 与 mtime 不变，未读取 `.env`、未调用 provider、未消耗 quota、未发布或写 DB。
- 后续边界：历史 reconstruction 仍须先确认具体 source，完成 terms/robots/retention/reuse/rate-limit 审批，再单独确认小样本联网并把真实 raw samples 保存到 `data/probe/csl_historical_odds/<source_id>/`；只有之后另起 source-specific plan，才能实现离线 parser、quote-time interval、immutable bundle 与 reconstructed-only report，不得改变本轮 observed report 语义。

## 2026-08-12 中超本地 postmatch shadow 闭环

- 新增 `worldcup.csl_postmatch_shadow`：只结算 2026 赛季已验证赛果，严格选择开球前最后一份合法 closing，复用 `settle_match_decision()` / `summarize_decision_records()` 产出 decision-only tally、coverage、分组和 `p_hit_safe` Brier 校准；2023–2025 replay 仍只供评级/pending gate，不误记为当季 closing 缺口。
- CLI 默认 dry-run 零写入；显式 `--write` 先 staging 生成 eval/backtest/gate/shadow，交叉校验后逐文件原子提升，report 倒数第二、state 最后提交，并用 report hash + input fingerprint 绑定完整成功。相同输入返回 `unchanged`，提升失败回滚旧产物并只保留安全错误码。
- `csl_scheduled_publish` 在双源赛果接受后、odds refresh 前触发 shadow；源阻断时不运行，shadow 异常只记 `csl_postmatch_shadow_failed` warning，不阻断赛前 snapshot 生成/发布，不把 shadow 摘要持久化进常规发布 snapshot。
- 真实 ignored 数据验收：2026 赛季 171 场已验证赛果，43 场有 closing，35 场当前策略可结算，`17 hit / 18 miss / 0 push / 0 no_pick`，命中率 48.57%，`sample_too_small=true`；8 月 9 日三场均为 `OU over 2.5`，结算 `2 hit / 1 miss`。重复写入返回 `unchanged`，report/state hash 一致，敏感/原始 provider 字段扫描为空。
- 验证：聚焦回归 `61/61`，项目 runtime `968/968 passed, 1 optional fastapi skipped`，`py_compile` 和 `git diff --check` 通过。本阶段未读取 `.env`、未联网、未调用 The Odds API、未消耗 quota、未修改公开 API/preview、未发布、未部署、未改 LaunchAgent、未 commit/push。

## 2026-08-01 生产 Nginx 每日精选路由纳入 Git

- 新增版本化模板 `deploy/nginx/worldcup-daily-picks.conf`，只声明 `/api/daily-picks`、`/daily-picks`、`/api/daily-picks-sidecar`、`/daily-picks-sidecar` 四个 `location =`，统一代理到 `127.0.0.1:8788`；不扩大 `location /`，不含证书、secret、token、账号或 `.env`。
- 新增 `worldcup.nginx_routes`：只在目标 `server_name football.celab.xin` 中移除这四个旧 exact location 并加入一个 managed include；snippet/site 原子替换，先备份到 `/root/nginx-backups`，幂等时无副作用，`nginx -t` 失败不 reload 并恢复旧文件，reload 失败也恢复旧文件并尝试旧配置恢复 reload。
- `worldcup.ssh_deploy` live 远端流程接入 release 内模板和安装器；保持既有 bind-address、release/current 原子切换、旧路由、service restart、readyz warmup、公网 smoke 与 rollback 语义不变。dry-run 仍只读 Git，不 archive、SSH、写远端 Nginx 或 reload。
- TDD 新增 `tests/test_nginx_routes.py`，覆盖四个 exact route、模板安全、规范化幂等、备份/原子安装、`nginx -t` 失败恢复且不 reload、reload 失败恢复、ssh deploy 接线和 dry-run 无副作用。
- 验证：定向 Nginx 测试通过；项目 runtime `950/950 passed, 1 optional fastapi skipped`；未访问生产、未 live deploy、未调用 provider、未读取 `.env`、未消耗 credits。

## 2026-08-01 每日赔率 sidecar live 刷新闭环

- 保留上一阶段全部 dirty 改动，未 reset/checkout/clean，未 commit/push/PR；先恢复 `tests/test_daily_odds_refresh.py` 中被吞并的同 key 测试边界，再补 timezone、自然日、一次请求、多 event、quota、完整 h2h、标准化快照和 writer 失败状态断言。
- `worldcup.daily_odds_refresh` 现以单次 planner 结果驱动 live：同 `sport_key + anchor` 一次 odds fetch，T-6/T-90=`h2h`、T-25=`h2h,spreads,totals`；只保留北京时间当日未开赛 event，排除次日/已开赛/重复 ID/改期/不完整 h2h/过期赔率；一次 response 同时生成安全 event rows、全局 Top4、2串1、3串1 和 snapshot payload。
- 新增 `worldcup.daily_odds_state.DailyOddsState`，原子保存 `sport_key|anchor` committed keys；只有 `DailyOddsSnapshotWriter` 成功后才提交，重启可复用 state，writer 失败不会伪造成功。新增日预算与 provider remaining 硬 guard，部分失败只重跑失败 key。
- `worldcup.daily_odds_store` 扩展标准化白名单和跨运行 event identity/kickoff 校验，继续原子写入独立 `data/cache/daily_odds/`，不保存 raw provider/bookmaker/API key/secret。新增 `query.load_daily_sidecar_snapshot()` / `project_daily_sidecar()`。
- 新增只读 `/api/daily-picks-sidecar` 与 `/daily-picks-sidecar`；旧 `/api/daily-picks`、`/daily-picks`、旧单场 API/预览仍走原 snapshot projection，不会因刷新联网。FastAPI 路由同步声明。
- 文档已同步到 `README.md`；未创建 `ARCHITECTURE.md`。项目 runtime 完整回归 `942/942 passed, 1 optional fastapi skipped`；focused daily sidecar/旧菜单回归通过；`py_compile` 与 `git diff --check` 通过。未读取 `.env`、未调用真实 provider、未消耗 odds credits、未启动服务、未部署。
- 当前最终状态：代码本地完成；`worldcup.ssh_deploy --root /Users/eagod/ai-dev/足彩 --ref HEAD` dry-run 已执行并按设计返回 `status=blocked, reason=dirty_worktree, dirty_files=31`，未 SSH、未 archive、未重启服务、未切换线上 current。服务尚未部署，自动 daily odds 抓取保持关闭；没有 commit/push/PR、没有读取 `.env`、没有真实 provider/odds/credits、没有线上写入。

## 2026-08-01 真实 provider 验证后的 17 联赛接入

- 将已通过真实 The Odds API `/sports` + `/events` 只读验证的 17 个联赛接入现有 `worldcup.competitions` profile 与 `worldcup.daily_competitions` catalog：中超、英超、英冠、德甲、德乙、法甲、意甲、西甲、瑞典超、挪超、丹超、芬超、墨西哥超、J 联赛、K 联赛、巴西甲、美职联；墨西哥甲、澳超、阿根廷超仍无 profile/`sport_key`，保持 `code_reserved` / fail-closed。
- 英超消歧：排除俄超、非足球和 outright/winner 类候选后，`soccer_epl` 为唯一主赛事 key；`/events` HTTP 200，返回 10 场完整 event identity。德甲消歧：排除奥地利、女子、手球和其他非主赛事候选后，`soccer_germany_bundesliga` 为唯一主赛事 key；`/events` HTTP 200，返回 11 场完整 event identity。两者均无重复 event、无 identity 异常。
- `/sports` 返回 174 个 sport；本阶段只调用 `/sports` 与每个确认 key 一次 `/events`，响应 quota headers 保持 `remaining=500 / used=0 / last=0`，未调用 `/odds`、`/scores` 或历史赔率，未消耗赔率 credits、未写 raw payload。
- 保留旧 catalog 顺序和中超 `resolve_sport_key` 的双 candidate/显式 key 兼容；daily sidecar 使用已验证 exact key。`daily_odds_refresh` 默认仍 disabled，只有显式 `enabled=True, live=True` 才进入 odds fetcher；未改 `local_runner.py`、`match_decision.py`、旧单场查询或 snapshot 契约。
- TDD 先出现 3 个兼容红灯，最小修正后项目 runtime 完整回归 `938/938 passed, 1 module skipped (optional fastapi)`；新增 17 联赛 resolved-catalog、3 联赛 fail-closed 和 exact profile/key 测试。未启动服务、未部署、未 commit/push。

## 2026-07-31 每日赔率 sidecar 与动态 provider 状态

- 在不改旧单场 `match_decision` / `local_runner` 主路径的前提下，新增 `worldcup/daily_odds_refresh.py` 注入式 sidecar：依赖注入 `sports/events/odds fetcher` 与 snapshot writer，默认不自动运行。
- 20 联赛白名单保留：中超仍为已知配置；英超/德甲/法甲/意甲/西甲保留候选 `sport_key`；其余 14 个仅保留名称和 `code_reserved`，不猜 `competition_id` 或 provider key。运行时只接受 `/sports` exact active key，缺失/inactive 显示 `provider_unavailable`。
- 同一 `sport_key` 的未来赛程在同一波只发一次 odds 请求；不同 key 分别请求；T-6h/T-90m 为 `h2h`，T-25m 为 `h2h,spreads,totals`。已开赛、无未来赛程、重复锚点、quota 不足和同 event 多 kickoff 均 fail-closed。
- 扩展 `worldcup.sources.theoddsapi` 的 `/events` URL 与注入式 `fetch_events_for_sport`，继续复用现有 `fetch_json_from_url` 和 `quota.update_quota_from_headers`；旧世界杯 odds 封装保持兼容。
- `odds_movement` 只记录 `shadow_only` / risk metadata，不参与 Top4、2串1、3串1 排序或 tie-break，不进入公开 raw bookmaker/odds 字段；每日精选继续复用既有 snapshot/query/routes/menu。
- TDD 先捕获 provider 目录、同 key 批量、跨 key、三锚点 markets、改期/quota/幂等和 `/events` source 边界失败，再修复实现。定向与完整验证：`928/928 passed, 1 optional fastapi skipped`；未联网、未读 `.env`、未启动服务、未调用真实 provider、未消耗 quota、未 commit。

## 2026-07-31 每日赔率本地接线（默认关闭）

- 新增独立 `worldcup.daily_odds_store.DailyOddsSnapshotWriter` 与 `data/cache/daily_odds/daily_odds_snapshot.json` 命名空间：原子临时文件 + `fsync` + `os.replace`，校验 `competition_id`、`event_id`、`commence_time`、fixture identity，改期 event fail-closed；禁止写入旧 `analysis_snapshot.json` / 联赛 snapshot 名称。
- writer 只保留 provider catalog、sport/competition、anchor、markets、event IDs、fixture identity 和 `odds_movement=shadow_only` 风险元数据；注入 payload 中的 raw bookmaker/provider odds 不进入文件，也不污染旧 cache/history。
- `worldcup.daily_odds_refresh.plan_daily_odds_refresh()` 只基于注入的 `/sports` 等价列表和 `/events` 等价 fixtures 生成 due anchor、markets、estimated credits、fixtures、skip reason；不创建 transport、不调用 odds、不写文件。
- `worldcup.scheduled_refresh.run_daily_odds_refresh()` 默认返回 `disabled`；显式 `enabled=True` 但 `live=False` 只做 planner dry-run；仅显式 `enabled=True, live=True` 才调用注入的 `odds_fetcher` 和 writer。旧 `run_scheduled_refresh()` 及其 CLI parser/返回契约保持不变，未注册 scheduler/LaunchAgent。
- TDD 先记录缺失 `daily_odds_store` 的红灯（项目 runtime `928/929`），实现后定向与完整回归均为 `935/935 passed, 1 module skipped (optional fastapi)`；`py_compile`、`git diff --check` 通过。全程未联网、未读 `.env`/密钥、未调用真实 provider、未启动服务、未消耗 quota、未 commit/push/deploy。

## 2026-07-31 每日精选 sidecar 初版

- 完成每日精选 sidecar：20 个联赛目录状态为中超 `enabled`、英超/德甲/法甲/意甲/西甲 `code_reserved`、其余 14 个 `unsupported`；未验证 provider ID / sport key 不猜测、不模拟。
- 新增动态 `/daily-picks` 与 `/api/daily-picks`，周期为北京时间 `18:00` 至次日 `18:00`，内部 UTC；全局 Top 4、同一 Top 4 派生 `2串1` / `3串1`，组合仅标记独立性近似研究分数并过滤同场/同队冲突。
- 复用现有 snapshot/public safe projection 和 scheduled refresh/publish；未新增 daily-picks scheduler、静态 export 或 LaunchAgent。旧单场算法、投影、路由和返回契约未改变。
- 验证：项目指定 runtime `tests/run_tests.py` 为 `921/921 passed, 1 optional fastapi skipped`；未启动服务、未联网、未消耗 quota、未部署、未 commit。

## 2026-07-20 19:30 UTC+8 阶段确认规则 + ssh_deploy 安全加固

- **阶段确认规则**：`CLAUDE.md` 和 `AGENTS.md` 各追加"阶段确认规则"段落（四个口令：确认实现/提交推送/合并/部署），内容同步。
- **ssh_deploy.py 加固**（TDD 红→绿）：
  - 远端脚本在 mv/ln 前拒绝 `[ -L "$release" ]`（防止部署到 symlink alias 导致共享目录污染）。
  - `previous` 解析改为 fail-closed：先判断 `$current` 是否作为文件系统条目存在（`[ -e ] || [ -L ]`）；只有真正不存在时才允许 `previous=""` 作为首次部署；current 存在但 `readlink -f` 返回空（broken/cyclic symlink）则打印 `current_unresolvable` 并 exit 1；解析非空后继续做 releases 前缀、-d、非 symlink 校验。
  - 使用 `flock -n` 非阻塞并发锁（`$releases_dir/.deploy.lock`），覆盖创建→切换→重启→验证全程。
- **测试**：新增 4 个 test_ssh_deploy 测试；全部 11/11 通过；完整 run_tests.py 897/897 + 1 skip (fastapi)。
- 未做：commit/push/PR/merge/deploy、依赖安装、CI/GitHub 设置、服务状态变更。

## 2026-07-20 17:50 UTC+8 部署修正：独立物理 release + symlink 污染修复

- **异常根因**：先前部署使用 `cp -a` 复制 release symlink（而非目录内容），导致所有 release alias 指向同一物理目录 `dd972e6e1626b8999ccee47ec3a837135180a288`；后续 `scp` 实际覆写了共享物理目录，旧 release alias 作为回滚点已失效。
- **修正操作**：
  1. 创建独立回滚物理目录 `pre-4c68f00-rollback-20260720T101500Z`（从当前物理目录 cp -a + 旧 blob 覆盖 6 文件，inode 独立，nlink=1）。
  2. 创建独立 main release 物理目录 `4c68f00-main-20260720T101500Z`（从当前物理目录 cp -a，298/298 文件一致，inode 独立）。
  3. 原子切换 `current` → 新物理目录（直接指向，不经 release alias symlink 链）。
  4. 服务重启并验证：PID=59900、NRestarts=0、healthz ok、readyz ready (9 场)、/api/matches 200、/preview 200。
- **回滚目录** 6 文件旧 hash 验证通过：store.py=b195b2b5578e、quota.py=98c33da4ab7c、refresh_runner.py=74773f90e600、decision_settlement.py=f1189742e90f、match_decision.py=a9c0c32e915c、collectors/csl_result_sources.py=8773495d3c35。
- **main release** 6 文件新 hash：store.py=23e01afe742b、quota.py=96c8650d77f9、refresh_runner.py=74a046f739c3、decision_settlement.py=7a2e1cf89162、match_decision.py=bc8c78541871、collectors/csl_result_sources.py=9fd01bf44086。
- **当前 current**：`/opt/worldcup/releases/4c68f00-main-20260720T101500Z`（物理目录，非 symlink）。
- **回滚路径**：`ln -sfn /opt/worldcup/releases/pre-4c68f00-rollback-20260720T101500Z /opt/worldcup/current && systemctl restart worldcup`。
- **今后禁止**：不得使用 `cp -a` 复制 release symlink 作为新 release；必须用 `cp -a $(realpath source)` 或 `cp -rL` 创建独立物理目录。
- 未做：commit/push、修改 .env/DB/LaunchAgent/监控、删除旧 release、ingest/publish。

## 2026-07-20 15:36 UTC+8 readiness profile 拆分 + 部署 + RECENT_WORK 归档

- 本地功能：`worldcup/readiness.py` 新增 `--profile` (full/server/publisher) 和 `--env-path` 参数；893 tests passed，1 optional module skipped (fastapi)。
- 归档：RECENT_WORK.md 164 条旧记录无损迁入 `docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md`，当前文件保留最近 20 条。
- 远端新 release：`dd972e6-secretcheck-20260720T070131Z-readiness-20260720T073603Z`，仅上传 `worldcup/readiness.py`。
- 回滚点：`dd972e6-secretcheck-20260720T070131Z`（保留在 `/opt/worldcup/releases/`）。
- 验证结果：healthz ok、server preflight ok (2/2)、/api/matches HTTP 200 (9 场)、/preview HTTP 200。
- 未做：commit/push、修改 .env/DB/systemd/监控/LaunchAgent、ingest/publish、quota 消耗。
- CI 修复（同阶段追加）：`tests/test_fastapi_app.py` bad-signature 断言对齐 `authentication_failed`；`tests/test_runner_resilience.py` 3 个测试改用虚拟包名隔离 optional module 可用性，不再依赖宿主是否安装 fastapi。CI venv 906/906 + 本地 893/893 + 1 skip 全绿。

## 2026-07-20 15:02 UTC+8 部署：INGEST_HMAC_SECRET 启动强校验上线

- **release**: `dd972e6-secretcheck-20260720T070131Z`
- **base release**: `dd972e6e1626b8999ccee47ec3a837135180a288`
- **manifest（9 文件）**:
  - `worldcup/secrets.py`（validate_hmac_secret + --check）
  - `worldcup/http_app.py`（启动校验 + 401 统一 + sqlite3 503）
  - `worldcup/fastapi_app.py`（load_secret 校验）
  - `worldcup/ingest.py`（CLI 启动校验）
  - `worldcup/publish.py`（CLI 启动校验）
  - `worldcup/scheduled_publish.py`（live fail-fast）
  - `worldcup/csl_scheduled_publish.py`（live fail-fast）
  - `worldcup/postmatch_publish.py`（validate 调用）
  - `worldcup/readiness.py`（weak_secret error）
- **回滚点**: `/opt/worldcup/releases/dd972e6e1626b8999ccee47ec3a837135180a288`
- **验证结果**:
  - healthz: `status=ok`
  - HMAC readiness: `ok`
  - 进程稳定, 端口 8788 正常
  - 本地 dry-run: `status=dry_run`, 无 quota 消耗
- **基线对照**: 新旧 release 在相同环境运行 readiness，12 项检查结果完全一致（HMAC 均 ok，整体 7 errors/3 warnings 均为既有环境缺项）；新版未引入任何新 failure。
- **未发生回滚**
- **未做**: commit/push、数据库迁移、.env 修改、依赖安装

## 2026-07-20 INGEST_HMAC_SECRET 配置边界强校验

- `validate_hmac_secret(secret)` 在所有配置/启动入口 fail-fast：
  - `scheduled_publish`: live=True 时在 env 加载后、refresh/pending/publish 之前立即校验；live=False dry-run 不要求 secret。
  - `csl_scheduled_publish`: 同上架构，返回 `{"status":"blocked","reason":"weak_ingest_hmac_secret"}`。
  - `postmatch_publish`: 在 endpoint/path 校验后、pending/fetch/write 之前校验。
  - `http_app` / `fastapi_app` / `ingest` / `publish`: 启动前 SystemExit。
  - `readiness`: 报 `"weak_secret"` error，不泄露值/长度。
- 校验规则：`None`/空/UTF-8 字节 < 32 → `ValueError("weak_secret")`。
- 底层 `verify_ingest_request` 不强制长度，只在配置边界做。
- 每入口单次校验，`resolved_secret` 复用到后续 publish 路径。
- 测试 `tests/test_secret_validation.py`（24 个）：中央验证边界、各入口 live 拒绝/dry-run 通过、exploding stubs 证明无副作用、readiness 输出安全、底层兼容。
- 既有测试中短合成 secret 全部升级为 ≥32 字节值。
- 全量：880/880 passed, 1 skipped (fastapi)。
- 未联网、未部署、未 commit/push。

## 2026-07-20 secrets --check：非阻断 secret 安全核验

- `worldcup/secrets.py` 新增 `--check` 模式和 `check_secret()` 函数。
- 用法：`python3 -m worldcup.secrets --check --env-file .env`
- 输出固定 JSON：`{"configured": bool, "minimum_length_ok": bool, "generator_format_ok": bool}`
  - `minimum_length_ok`：UTF-8 字节长度 ≥ 32（不声称保证熵）
  - `generator_format_ok`：完全匹配 `token_hex(32)` 的 64 位 lowercase hex（信息项，不影响 pass/fail）
- 退出码：configured + minimum_length_ok → 0；否则 → 1。
- 不改变任何现有启动/运行行为；默认生成命令不受影响。
- 读取使用与项目 `_load_env` 一致的解析语义（去引号、最后出现的值优先）。
- 文件不可读/非 UTF-8 时安全返回 `configured=false`，无 traceback、不泄露内容。
- 新增 `tests/test_secrets_check.py`（18 个测试）：兼容性、check 各布尔组合、退出码、引号/重复键、缺失/不可读文件、输出不含 secret。
- 全量：856 passed, 0 failed, 1 skipped (fastapi)。
- 未改变业务启动链路、未联网、未读真实 .env、未 commit/push/deploy。
