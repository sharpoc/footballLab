# 项目协作说明

本文件是 Codex 在本项目内的本地说明入口。Claude Code 对应读取 `CLAUDE.md`；两份文件应保持同步。

## 默认语言

- 默认使用简体中文沟通。
- 代码、命令、配置键名、字段名和报错信息保留英文原文。

## 项目定位

- 这是 2026 世界杯研究/分析站。
- 目标是做数据采集、量化分析和每场唯一“本场首选”展示；只要存在开赛前有效、可结算的主盘口，每场必须输出一个首选，不能靠删除低置信场次提高表面命中率。
- 不构成投注建议。
- 不显示下注金额。
- 不做追损、重注、串关喊单或任何无风控建议。

## 当前阶段

- Plan 1 引擎核心已完成第一版。
- Plan 0 核心数据源探测已完成第一轮。
- Plan 2 已启动，当前完成纯离线解析层、单场模型/市场输出、覆盖率与市场证据优先的每场唯一 `match_decision` 输出、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、低频调度策略、run metadata、调度执行包装、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照。
- Plan 2 collectors 必须基于 `docs/superpowers/data-contract.md` 和 `data/probe/` 保存样例写离线解析测试，不能按假接口写。
- Plan 3 云端与调度等阿里云资源确认后再细化。

## 开发规则

- 优先最小可行实现，不提前上 ML。
- 本届 MVP 只做 Elo + Poisson + 赔率去水 + 市场证据优先的每场唯一首选；公开产品只允许 `MATCH_PICK`（本场首选）或 `NO_CLEAN_MARKET`（无法计算）。
- S/A/B/C、EV/Edge 价值信号及旧 decision label 只允许作为 legacy compatibility 读取旧 snapshot/store；不得参与新 `match_decision` 选择，不得出现在公开 API、预览页、通知或新完赛统计中。
- 新 snapshot 每场最多一个 `match_decision`；概率偏低、书商偏少、离散度偏高、模型分歧或俱乐部评级 pending/missing 时改为风险扣分和市场共识兜底，不得删除整场。只有赔率全部无效/过期、比赛已开始或不存在任何可结算盘口时才允许 `NO_CLEAN_MARKET`。
- 当前公开策略版本为 `match_pick_v3`：世界杯市场/模型权重为 0.80/0.20；安全概率相近（默认 2 个百分点内）的候选继续比较书商覆盖、盘口质量和模型/市场一致性。中超俱乐部评级未完成时不得使用占位 1500 影响方向，改用赔率去水后的市场共识并附加内部风险扣分。
- 新完赛契约以 `closing_match_decision` 结算，统计使用 `decision_tally`（`hit/miss/push/no_pick`）、`decision_sample` 和 `decision_coverage`；旧等级 tally 不再是正式契约。
- 当前实现以 2026 世界杯为首个 competition adapter，但新增通用数据结构、snapshot 字段、概率族、赔率/盘口移动诊断和回测接口时，应尽量使用可迁移到联赛的命名与边界，避免继续把新能力写死为世界杯专用语义；已有 `stage` / `group` 等世界杯字段保持兼容，不为未来联赛提前大重构。
- 引擎层必须保持纯函数，不联网、不连数据库、不依赖云。
- 采集层使用保存的样例响应做离线解析测试。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- Elo 来源为本地基线重放：`data/cache/elo_baseline_*.tsv` + openfootball 完赛比分按 eloratings 公式（K=60、中立场）增量重放生成 `elo_world.tsv`；eloratings 抓取仅用于重新锚定基线，抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不因此单独强制取消本场首选。重放计算失败时回退沿用现有 `elo_world.tsv` 并记 `elo_local` 错误。常量与实现见 `worldcup/elo_local.py`。
- 中超俱乐部评级使用独立 `csl_model` 配置边界，当前仍为 `shadow_only` / `club_rating_pending`。除全局 replay 样本外，每场双方都必须达到逐队最小样本（默认 30 场）；未达标时必须使用 1500 结构占位并走市场兜底，不得让小样本 rating 影响首选方向。
- 中超 replay 赛果 live 更新必须同时通过 7M + 中足联官方公开接口的日期/主客队/比分全量一致校验，并且不得删除或改写已接受的赛果；否则沿用旧 cache 并记质量错误。解除 pending 还必须同时通过最新赛季主场先验和同样本市场基准门槛，不能只看全历史聚合 Brier。
- 中超 scheduled publish 每次成功构建赛前 snapshot 后必须自动归档到 ignored `data/local/diagnostics/csl_history/`，用于 closing join 和市场基准积累；归档失败记 `snapshot_archive_failed`，但不阻断当场有效首选发布。
- 中超 closing coverage 使用固定初始 128 个 match id + 全量 finished/history reconciliation；`csl_closing_coverage.json` 只把 observed schema v2 `MATCH_PICK` 计入正式战绩，reconstructed 必须独立统计且不得混算。audit 默认 dry-run、不得联网或调用 provider；scheduled publish 中 audit/pending 失败只记安全 warning，不得绕过 due/quota/live 边界或阻断已有有效首选。
- 中超已接受的双源赛果刷新后运行本地非阻断 postmatch shadow；只用 2026 赛季赛果、开赛前最后合法 closing 和当前 schema v2 首选结算，通过指纹幂等更新 ignored shadow/eval/backtest/gate 产物。shadow 失败只记 warning，不阻断赛前刷新/发布，不消耗 The Odds API quota，不进公开 API，不自动调参或解除 `club_rating_pending`。
- 明确 `fixture_status=POSTPONED` 的场次只从公开 `project_match_rows` / API / preview / static export 隐藏，内部 snapshot/cache/history 必须保留；已进入 `finished.matches` 的比赛同样从公开实时列表移除。不得仅按开球时间推断完赛，已开赛但未确认赛果的场次仍展示“赛果待确认”。
- 世界杯赛后公开同步必须使用独立 `postmatch_publish` 产物和 state/outbox，不得覆盖 `analysis_snapshot.json` 或影响 odds scheduler/quota；live 必须显式传入非占位 endpoint 并持有单实例文件锁，公开结算严格只接受 openfootball `score.ft` 的 90 分钟非负整数比分，忽略 `score.et`、`score.p` 和 legacy `score1/score2`。源回退/重复、比分修订、finished 回退/冲突或比分不一致必须阻断发布；单场 closing 缺失不得补造首选，也不得拖住其他已有 closing 的完赛场，必须在 `decision_coverage.missing_closing_count`、`skipped_no_closing` 和 `run.postmatch.partial_publish` 中透明记录。pending 必须绑定 endpoint，只有 ingest 返回 `stored` / `duplicate` 才算成功，之后先落 state、再清 pending。
- scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策；The Odds API 按免费额度使用，低额度时必须降频。
- 意甲、巴甲、西甲、英超、德甲、法甲使用独立 `league_v1` 离线闭环；俱乐部评级 pending 时只用市场共识，占位 1500 不得影响方向。正式统计只接受 observed schema v2，不混入世界杯、中超、legacy 或 reconstructed。六联赛 batch 的 live/write 在真实赔率与 90 分钟 scores 样例验收前必须保持 `live_acceptance_not_enabled`。
- 六联赛 live 只能由 acceptance report 中 sport catalog、odds sample、严格球队 identity、90 分钟 result contract 四类证据指纹共同激活；`state=active` 字符串本身无效。正式 live pipeline 必须传赛事级严格 identity registry，禁止回退 slug。真实 probe、active 写入、LaunchAgent 安装、推送和部署分别确认。
- 六联赛 FotMob 赛果固定使用 `/api/data/matches?date=YYYYMMDD` 与 `/api/data/matchDetails?matchId=<event_id>`，保存 bundle 的 `calendar_date` 同样只接受组成真实日期的 8 位 ASCII `YYYYMMDD`；Brasileirão provider league ID 为 `268`。404 必须分类为 `provider_contract_drift` 并在 receipt 前阻断受影响分区。detail 开球只接受 timezone-aware ISO `general.matchTimeUTCDate`，90 分钟结果必须同时通过 calendar/detail FT+比分一致、无加时、无点球、无 aggregate 的复合证明；缺失或畸形类型一律 fail closed。保存 bundle 只能由 `worldcup.league_fotmob_result_probe` 完全离线审核，不得写 provider evidence/acceptance 或激活 Gate。2026-08-26 exact six 离线复核为 `4 verified / 2 blocked`；Brazil detail 与 Bundesliga 本赛季 FT 仍待另行确认的真实 re-probe，不能据此宣称 operational Gate A 完成或进入 Gate B。
- 六联赛赛后闭环当前只完成本地离线 / dry-run-first 实现；四份保存的真实 FotMob 赛后样例已 offline parser-verified，但尚未写入正式 provider evidence/acceptance，operational Gate A 仍为 partial；未执行 `--live --write`，未安装 `xin.celab.football.league-postmatch`，未发送真实 WxPusher。真实 FotMob re-probe、首次静默 live/write、LaunchAgent 安装、首次真实通知是独立 Gates A–D，push/merge/deploy 也不在其中。
- 赛后 acceptance 必须绑定同联赛 `providers/fotmob/result_contract_evidence.json`、规范化相对 `data/probe/...` 样例路径、实际样例字节 SHA-256 和当前完整严格球队 registry 指纹；不得读 legacy 或旧通用 evidence，样例目录/文件任一 symlink、路径逃逸、指纹不同或 due event identity 不同都必须在 provider 前 fail closed。
- 赛后结果 store 必须保持单调：已接受比分不删除/不静默改写，修订、finished 回退或身份冲突隔离人工审计；缺 closing 只记 `missing_closing` / `skipped_no_closing`，不补造推荐。已持久化通知 pending 必须先于 provider 检查：带 `--notify` 时先重试，不带时返回 `notification_pending` 并阻断 provider；通知失败不回滚结算。WxPusher 接受成功但 sent receipt 持久化前崩溃存在 at-least-once 重复发送窗口，不得声称 exactly-once。
- 赛后 closing schema v2 同时接受 `MATCH_PICK` 和 `NO_CLEAN_MARKET`；只有 `MATCH_PICK` 进入 `hit/miss/push`，`NO_CLEAN_MARKET` 只进入 `no_pick` 和 coverage，不进入命中率分母。
- 当前 scheduled publisher 仍保留 The Odds API scores legacy lifecycle；parser 必须绑定 `legacy_theoddsapi/<competition_id>/result_contract_evidence.json` 中精确 `theoddsapi_scores_v1` 并转 Task 2 committed receipt。旧通用 evidence 只可在精确验证后于 write 轮次按原字节复制到 legacy 路径，不覆盖已有 legacy 或 FotMob evidence。legacy evidence/postmatch/statistics 不得进入 FotMob provider evidence / `postmatch_statistics.json` / state / notification；FotMob Gates A–D 完成前不得静默退役该兼容链路。
- legacy 与 FotMob runner 共享的 `closing.json` 必须在同一 `flock` 内完成读取、验证、单调 merge 和原子写入；删除、身份变化、时间倒退、同时间 decision 冲突必须拒绝，并发不同 event 不得 lost update。
- FotMob 正式汇总使用 `postmatch_components.json` 保留逐联赛 last-known-good 验证统计、provider/schema 身份和 event membership。即使 fresh `postmatch.json` 结构合法，tally/sample/核心 coverage 或 membership 回退也必须显式 `postmatch_partition_regression` 并保留 LKG；不可读、结构无效、legacy shape 或 LKG 身份/schema 不兼容同样不得污染新 manifest。坏分区显式 `stale` / `blocked`，健康分区仍可推进 statistics/state/通知，不得将坏分区当空集合导致 aggregate 回退。
- 六联赛赛后 timer 固定为北京时间 10:30 / 16:30、`RunAtLoad=false`；赛前 confirmed-lineup observer 是独立 `xin.celab.football.league-pre-match` 五分钟唤醒链路，不得把五分钟频率写成赛后调度。
- 六联赛 FotMob 首发真实契约允许两类可用证据：provider 明示 confirmed 11+11；或 `lineupType=standard`、双方各 11 个唯一球员 ID、`general.started/finished=false`、`header.status.started/finished/cancelled=false`，且响应完成于开球前。后一类内部规范化为可用首发，但必须保留 `provider_lineup_type=standard` 与 `confirmation_basis=fotmob_standard_pregame_11v11`，不得声称 FotMob 明示 confirmed。predicted、缺状态、已开赛及身份/开球不一致仍 fail closed。
- The Odds API 使用 `THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY` / `THE_ODDS_API_KEY_TERTIARY` / `THE_ODDS_API_KEY_QUATERNARY` / `THE_ODDS_API_KEY_QUINARY` 五个显式槽位依次轮换；当前槽位剩余额度降到 30 或以下时，优先切换到仍未探测或剩余大于 30 的下一槽位并保留低额度应急余额。只有五个槽位都没有新鲜额度时才按低额度锚点降频，全部耗尽时暂停刷新。真实 token 只允许写入 ignored `.env`，不得进入代码、文档、日志或回复。
- scheduled refresh 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会调用 refresh runner。
- 正常额度时，世界杯与中超调度都必须把 `match_decision.valid_until - 20 分钟` 作为刷新候选，避免有效首选先过期再等下一个赛前锚点；quota 低于等于 30 时允许按既有低额度锚点降级。
- scheduled publish 对瞬时 TLS/网络/5xx 做有限重试；仍失败时必须保留不含 secret 的 `*.publish_pending.json` 状态，下次唤醒只重试发布现有 snapshot，不重复刷新或消耗 quota。
- ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求，不能打印 HMAC secret。
- 云端 ingest 必须使用 HMAC + timestamp + run_id/snapshot_id，并做幂等与防重放；当前默认防重放窗口为 300 秒。
- 本地 SQLite / preview 输出必须写入被忽略的 `data/local/` 或 `data/cache/`；预览页必须保留研究免责声明，不显示资金相关字段。
- HTTP / ASGI 适配层只用于本地预览、路由契约测试和后续 FastAPI 包装参考；正式依赖安装、ECS 部署、上线和云端写入必须单独确认。
- `/healthz` 只能报告服务存活，不读 DB、不依赖 secret，不输出环境变量、密钥、quota 或 snapshot 内容。
- 静态站点导出默认写入被忽略的 `data/cache/site/`，只作为本地预览/上线包草案，不代表已部署。
- readiness check 只读本地文件和变量名，不联网、不打印 secret；缺少真实 `INGEST_HMAC_SECRET` 时应报错，不要自动生成并写入 `.env`；`.env.example` 必须只含变量名和空值。
- HMAC secret helper 只允许打印新 secret 给本地人工写入 `.env`，不得自动改 `.env` 或把 secret 写入文档。

## 对抗性自审

- 赛后复盘必须区分：数据事实、暂时观察、可确认结论、工程问题。
- 当 `sample_too_small=true` 或样本数低于 `min_sample` 时，只能给观察结论，不能建议调参。
- 赛后复盘必须检查：是否被小样本或单场极端结果拉偏、模型是否弱于市场基准、当前首选策略的已结算样本是否不足、`daily_eval.decision_tally` 与 `finished.decision_tally` 是否一致、`decision_coverage` 中是否存在缺失/无效/未结算决策、是否存在 `skipped_no_closing`、closing snapshot 是否完整、是否将 legacy 决策与当前策略绩效混算、是否混淆 90 分钟/加时/点球或比分来源、The Odds API scores 与 openfootball 是否可能不同步、东道主/准主场/中立场/Elo 口径是否可能影响判断。
- 自审发现问题时，必须把结论降级为“观察”或“需修数据链路”，不得硬给模型结论。
- 写实现计划、架构方案、调度/部署方案、数据链路方案或模型调整方案时，必须加入“对抗性自审”段落。
- 项目计划自审重点检查：是否解决根因、是否范围膨胀、是否改变业务语义/接口契约/结算口径、是否触发联网/额度/密钥/线上写入/部署风险、是否可 dry-run、是否有验证和回滚方式。
- 涉及 live refresh、The Odds API quota、HMAC secret、LaunchAgent、ECS、SQLite/PostgreSQL、`data/local/`、`data/cache/`、日报推送或公网展示时，必须显式写出风险和确认点。
- 复盘和计划输出仍必须保留研究边界：不构成投注建议，不输出下注金额或执行建议。

## 文件与安全

- 不要提交 `.env`、API key、token、Cookie、RDS 连接串、HMAC secret。
- `.env` 只放本地真实密钥。
- `.env.example` 只允许放变量名。
- `data/raw/`、`data/probe/`、`data/cache/`、`data/local/` 和 `.DS_Store` 不进 git。

## 验证命令

当前可用的无 pytest 验证命令：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

如果安装了 pytest：

```bash
python3 -m pytest -v
```

## Git 规则

- 本项目已初始化 git 仓库。
- 本地提交可以做；推送远端、部署、改云资源前必须单独确认。
- 不要使用破坏性 git 命令，例如 `git reset --hard` 或 `git checkout --` 覆盖用户改动。

## 阶段确认规则

以下四个口令是当前项目内对一次明确阶段的批量授权，不是永久或跨任务授权。每个阶段执行前必须汇报目标、影响文件/状态、验证方式；用户口令只授权已汇报范围。用户可随时撤销；发现业务语义、接口、数据、权限、密钥、依赖、迁移或范围变化必须重新确认。

### "确认实现"

- **授权**：在已说明且用户确认的当前任务范围内修改本地源码/测试/文档并运行本地验证，可连续完成，不逐文件重复确认。
- **不授权**：commit/push/PR/merge/deploy、secrets、数据库或生产写入、依赖安装、权限与账号变更。
- **失效**：任务完成、用户取消、需求/范围发生实质变化。

### "确认提交推送"

- **授权**：对当前任务的当前非 main PR 分支进行 commit + push；同一 PR 内仅为修复 CI 的非业务语义问题（test assertions/fixtures、CI 配置、import path、格式）可连续修改、测试、commit、push 直到 CI 通过，不逐次确认。
- **不授权**：`worldcup/` 业务行为、API 契约、数据库 schema、依赖、secret/权限处理的新增变化；push main、force push、merge、deploy、删分支。
- **遇到不允许项**：立即停下重新说明和确认。
- **失效**：PR 合并/关闭或需求变化。

### "确认合并"

- **授权**：仅把当前已通过要求 CI 的 PR squash merge 到 main。
- **不授权**：force push、删分支、部署。
- **失效**：合并完成即失效。

### "确认部署"

- **授权**：仅部署用户已确认的 main commit/artifact；允许预检、新建独立物理 release、上传/解压、原子 current 切换、既有服务重启、healthz/readyz/API/preview GET、失败自动回滚、部署记录。
- **不授权**：修改 .env/secrets/账号权限/数据库 schema、ingest/publish/admin/生产业务写入、git 操作、删除旧 release。
- **失效**：成功或回滚完成即失效。

## ECS SSH / 部署注意事项

- 本机可能启用 TUN / fake-ip 代理，导致 `39.102.50.205` 默认路由走 `utun4 -> 198.18.0.1`，从而出现 SSH `Connection timed out during banner exchange` 或 `Connection closed by remote host`。
- 连接 ECS 或执行部署时，优先绑定本机 Wi-Fi 源地址：`ssh -b 192.168.31.152 strategy-lab-ecs ...`；使用一键部署工具时加 `--bind-address 192.168.31.152`。
- 如果本机网络变化，先用 `ipconfig getifaddr en1` 或 `route -n get 39.102.50.205` 确认当前 Wi-Fi 地址和路由，再更新 `--bind-address` 参数。
- 线上旧 release 曾因 `/api/matches` 慢查询拖住服务；重启 ECS 后不要先压测 `/api/matches`，应先抢 SSH 窗口部署已验证热修或重启服务，再做公网 smoke。

## 近期重点

1. The Odds API key 已在聊天截图暴露过；用户已确认不充值，后续按免费额度和缓存兜底设计；赛期留意 quota，低额度时降频。
2. 保持 collector 解析测试使用 `data/probe/` 保存样例，不联网。
3. 线上 ECS 当前使用 SQLite；切换 PostgreSQL/RDS 需单独确认（`postgres_store` / `store_factory` 代码层已就绪）。
4. 赛期内完赛后用 `results_capture` / `eval_data` 积累真实赛果，再决定是否采纳 `mu_total=2.2, mu_dr_slope=0.0015`（见 `docs/research/2026-06-10-mu-dr-fit.md`）；积累足够样本前不改模型参数。
5. 日常巡检用只读命令 `python3 -m worldcup.ops_check`。
