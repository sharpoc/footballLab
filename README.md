# 世界杯足彩分析站

这是一个 2026 世界杯研究/分析站项目。当前 MVP 目标是先跑通：

1. 数据源探测
2. Elo + Poisson + 赔率去水分析
3. 每场只输出一个“本场首选”；低置信只降概率和证据分，不删除整场
4. 后续上传到阿里云网站展示

项目定位是**研究/分析工具**，不构成投注建议，不显示下注金额，不做追损、重注或喊单。

## 当前状态

- Git 仓库已初始化。
- Plan 1 引擎核心已完成第一版。
- 当前离线回归通过；标准测试入口仍会因本机未安装可选 `fastapi` 依赖在对应适配层测试处中断，排除该可选文件后的结果见 `RECENT_WORK.md`。
- Plan 0 核心数据源探测已完成第一轮：openfootball 赛程、eloratings Elo、The Odds API 赔率可用；API-Football Free plan 不能访问 2026 season。
- Plan 2 当前产品链路已切到 MatchPick v3：本地/联赛 runner 只生成每场唯一 `match_decision`，不再生成或序列化 S/A/B/C；公开 API、静态导出、预览页、变化通知、完赛战绩和日报也只使用“本场首选 / 无法计算”。采集、概率模型、quota、调度、首发、HMAC ingest、SQLite/PostgreSQL 适配和多赛事合并能力继续保留。
- Plan 3A FastAPI 本地适配层已实现并完成测试。
- Plan 3B PostgreSQL store 适配器已在 `SnapshotStore` 边界后实现；测试只使用 fake connection，未连接真实数据库。
- Plan 3C store 选择接线已完成：本地 CLI 默认 SQLite，也可以通过 `WORLDCUP_STORE=postgres` 加 `DATABASE_URL` 显式选择 PostgreSQL；本轮未连接真实数据库。
- Plan 3D PostgreSQL smoke dry-run guard 已完成：只验证 PostgreSQL smoke 前置条件并输出脱敏请求元数据，不发 HTTP、不连数据库。
- Plan 4 继续使用原研究台账布局与交互；公开预览通过 `ledger_html` 的 decision-only 模式只展示本场首选和首选战绩，实际部署记录见 `RECENT_WORK.md`。
- Plan 5 Gate C HTTPS 已完成：`football.celab.xin` 通过 Nginx 将公网 HTTPS 流量反代到 `127.0.0.1:8788` 上的 `worldcup.http_app`；`/api/snapshot/latest` 返回 404；Let's Encrypt 证书续期已配置；公网读取和 ingest smoke 已通过。

## 当前产品契约：只保留本场首选

- `worldcup.match_decision` 的 v3 输出标签只有 `MATCH_PICK` 和 `NO_CLEAN_MARKET`；不读取 Grade/Signal，也不区分强价值、候选、高/低置信等产品类别。
- 每场只比较新鲜、可结算的 1X2、OU 主线和 AH 主线；同一 bookmaker 去重，优先使用完整对边。世界杯用市场 0.80 / 模型 0.20 生成安全概率；安全概率相近（默认 2 个百分点内）时继续比较书商覆盖、盘口质量、模型/市场一致性、不亏概率和预期损失。
- 原 `p_hit_safe >= 0.58`、`p_no_loss_safe >= 0.62`、3 家书商、离散度 1.18 和赔率 1.30–2.20 只保留为观察与风险扣分参考，不再作为删除整场的统一硬门槛。书商偏少、离散度偏高、模型分歧、四分之一盘、极深让球或概率偏低会降低 `p_hit_safe` / 证据分；四分之一盘和极深盘只在没有普通可比主盘时作为备用，但不再因它们删除整场。
- 只有赔率全部无效/过期、比赛已开始或没有任何可结算盘口时才输出 `NO_CLEAN_MARKET`。俱乐部评级 pending/missing/invalid 时不得让占位 1500 参与方向选择，改用赔率去水后的市场共识兜底并附加内部风险扣分。
- 新 snapshot 不含 `signals`；公开 `/api/snapshot/latest`、`/api/matches`、`/api/finished` 和静态 JSON 使用 schema v2 白名单，不公开 `grade`、`top_grade`、`signal_count`、`closing_signals` 或内部排序字段。
- 完赛只冻结赛前 `closing_match_decision`，统一结算为 `hit / miss / push / no_pick`；缺失或损坏的历史 decision 单列 coverage，不能冒充主动放弃。旧 store 不做破坏性迁移，历史 `closing_signals` 只留在原始存储兼容读取，不进入新 snapshot、API、页面、日报或战绩。
- `pipeline_signals.py`、旧 `ledger_html.py` 及历史等级诊断代码暂保留为 legacy 兼容/离线研究资产，但已从产品 runner、公开投影和页面入口断开；不得重新接回产品链路。

## 技术栈

- Python 3
- 标准库优先
- 核心包默认不安装 Web / PostgreSQL 适配依赖；本地 Web 适配使用 `.[web]`，开发和 CI 使用 `.[dev]`，PostgreSQL 适配使用 `.[postgres]`
- 当前引擎不联网、不连数据库、不依赖云资源
- 当前 collector 解析层不联网；后续真实请求层可再引入 HTTP 客户端
- 当前 refresh runner 默认 dry-run；只有显式 `--live` 才会读取 `.env` 并联网消耗 The Odds API 额度
- 当前 The Odds API odds/scores fetch 使用统一 `SourceFetchError` 边界：只对 transient network / 5xx 做有限重试；credential、quota、4xx、invalid JSON 和 invalid UTF-8 不重试；只有拿到有效 JSON 后才写 cache / quota ledger；错误诊断会脱敏 `apiKey`，只暴露 `reason`、`retryable`、`attempts` 和可选 HTTP status
- 当前 scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策，不会联网或写入状态；全局 due 由所有比赛 `refresh_plan.next_update_at` 的最早值决定；单场计划会携带 `competition_id`、`refresh_priority`、`quota_budget` 和 `refresh_policy`，顶层 `competition_refresh` 汇总各赛事的待刷新窗口，便于后续分赛事限频；若某场 `lineup_shadow` 显示首发已确认但 odds 早于首发信息，则单场计划会给出 `post_information_odds_required`，在额度未耗尽时把下一次刷新提前到当前 dry-run 时刻
- 当前 scheduled refresh 默认 dry-run，dry-run 不读取 `.env`；只有显式 `--live` 且调度 due，或同时传 `--force`，才会读取 env 并调用 refresh runner
- 当前 scheduled publish 默认 dry-run，dry-run 不读取 `.env`、不刷新、不发布；只有显式 `--live` 且调度 due，或同时传 `--force`，才会刷新数据并向 HTTPS ingest endpoint 发送签名 snapshot。正常额度时，世界杯和中超都会把 `match_decision.valid_until - 20 分钟` 加入刷新候选，避免调度空窗让有效首选先过期。发布 HTTP 会对瞬时 TLS/网络/5xx 做有限重试；仍失败时写入同目录 `*.publish_pending.json` 脱敏状态，下一次 LaunchAgent 唤醒只重试发布已生成 snapshot，不重复刷新或消耗 quota。发布成功后会对比上一轮 snapshot，只有显著变化时才通过全局 WxPusher 工具发送手机通知，可用 `--no-notify` 关闭。
- 当前 scores capture 默认 dry-run，dry-run 不读取 `.env`、不联网、不写 results；淘汰赛开始后（`2026-06-28T00:00:00Z` 起）即使显式 `--live` 也会默认阻断并返回 `knockout_score_manual_review_required`，避免把可能含加时/点球的比分写入 90 分钟结算链路；只有人工确认 90 分钟口径后显式传 `--allow-knockout-scores` 才会放行。
- 当前 ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求
- 当前 ingest server 是纯本地验签/幂等模块；FastAPI adapter 已复用它，ECS 部署另行确认
- 当前 HTTP ingest 入口会拒绝非 JSON 请求、超限 body、非法 Content-Length 和非法 UTF-8；ingest 响应统一携带 `X-Request-Id`，错误体只暴露结构化 `error.code` / `error.request_id`，不回显 raw body、签名、secret 或 payload
- 当前 SQLite store / preview 都是本地低风险链路；默认输出在已忽略的 `data/local/` 或 `data/cache/`；公开 `/api/snapshot/latest`、`/api/matches`、`/api/finished` 和 `/preview` 只返回 decision-only 投影，并按 `competition_id` 合并各赛事最新 snapshot 形成只读展示视图；线上 HTTP 进程会缓存 public view 和 `/preview` 渲染 HTML，并用 DB 同目录的 `*.preview.html` / `*.preview.html.meta.json` 做重启后可复用的签名校验磁盘缓存，签名 ingest 成功后清空进程缓存且磁盘缓存会因 snapshot 签名变化自动失效
- 当前 PostgreSQL store adapter 可用于后续 ECS/RDS 接入；`psycopg` 只作为可选依赖声明，本轮未安装、未连接真实数据库
- 当前 store selection 默认 `sqlite`；单服务器 MVP 首发推荐 SQLite，只有显式 `--store postgres` 或 `.env` 中 `WORLDCUP_STORE=postgres` 时才要求 `DATABASE_URL`
- 当前 PostgreSQL smoke guard 默认只做 dry-run；SQLite 首发路线下返回 `blocked / expected_postgres` 是安全结果，且不打印 DSN、secret、签名或请求 body
- 当前 HTTP 适配层已用于 ECS 正式公网入口；服务只监听服务器本机 `127.0.0.1:8788`，由 Nginx 对 `football.celab.xin` 提供 HTTPS 反代
- 当前 ASGI 适配层无外部依赖，只包装本地 HTTP 路由契约；正式 ASGI server / ECS 部署需单独确认
- 当前 FastAPI app 仍作为可选适配层；Gate B 服务器 smoke 采用无额外依赖的标准库 HTTP app
- 当前 `/healthz` 不读 DB、不依赖 secret，只用于本地和后续云端健康检查契约；应用内部 `/readyz` 会读取最新 public view 并返回轻量 ready 摘要，用于部署/重启后的本机 warmup，不输出 secret、quota 或完整 snapshot；线上 Nginx 默认不公开 `/readyz`
- 当前静态导出默认写入已忽略的 `data/cache/site/`
- 当前 refresh runner 在写盘和 history 归档前做本地富化：每场 match 可附加 `odds_trend` 走势点，顶层可附加 `finished` 完赛定格块；富化失败只输出 warning，不阻断 snapshot 生成或发布
- 当前 `worldcup.lineups_refresh` 可用 FIFA public API 抓取官方首发；默认 dry-run，不联网写盘，只有显式 `--live` 才请求 FIFA 公网，只有再传 `--write` 才写入被忽略的 `data/cache/lineups_wc2026.json`。当临赛窗口内 FIFA 仍未返回两队 11 人首发时，可显式 `--notify` 通过 WxPusher 发一次缺失通知，去重状态写入被忽略的 `data/local/lineups_missing_notifications.json`。`worldcup.pre_match_runner` 可编排“首发轮询 → 新 confirmed lineup → post-lineup refresh guard → 首发后 odds refresh”，默认仍是 dry-run；`--refresh-guard` 只调用 scheduled refresh 的 dry-run 决策并返回 quota / policy 摘要，不刷新 odds、不消耗 The Odds API quota；如果同时打开 `--refresh-after-lineups --live-refresh`，guard 在 quota 未知或低于 `--min-refresh-quota` 时会阻断 live odds refresh。只有显式打开 `--live-lineups` / `--write-lineups` / `--refresh-after-lineups` / `--live-refresh` 才会逐步触发公网抓取、写本地 cache 和 The Odds API 刷新。`xin.celab.football.pre-match` LaunchAgent 已安装为 lineups-only + audit-notify 模式，每 300 秒运行 `worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --notify-audit`，不带 `--live-refresh`，所以不会自动消耗 The Odds API 刷 odds；生成未来 live-refresh plist 草案时会自动包含 `--refresh-guard`。本地 runner 会可选读取同一输入目录下的 `lineups_wc2026.json`，把已确认首发、替补、缺阵、阵型和球员影响 delta 接入 `lineup_context`；绑定首发上下文时，`source_match_no` 只能作为候选，必须同时校验双方 canonical team 和 UTC 开球时间，避免 FIFA 编号与本地赛程编号不一致时错挂；当前未接入付费首发 API。
- 当前 `odds_trend` / `odds_movement` 仍可从 history 归档生成只读诊断，但不再晋升等级或改写首选；`match_decision` 在 runner 生成时一次确定，后续富化不得改变方向。`lineup_shadow` / `ou_total_shadow` / probability families 继续作为模型审计字段，不进入公开页面。
- 当前静态预览/导出页保留原研究台账的导航、日期条、左右工作台、搜索、赛事筛选和历史视图；其中业务内容为 decision-only：待开赛每场恰好显示“本场首选”或“暂无可靠首选”，摘要与历史区只统计首选。页面保留脱敏数据质量状态和免责声明，不显示 S/A/B/C、价值分歧、下注金额或资金字段。
- 当前 readiness check 只读本地文件和变量名，会解析 snapshot/quota、检查预览免责声明，并确认 `.env.example` 只含空值模板，不联网、不打印 secret
- 当前 HMAC secret helper 只打印 `INGEST_HMAC_SECRET=<value>`，不会写 `.env`
- 当前公网 MVP 使用 HTTP app + SQLite + Nginx HTTPS；FastAPI、PostgreSQL/RDS、OSS/CDN 都是可选升级，不是单用户 MVP 首发必需项

## 目录结构

```text
config/
  settings.yaml                 # 模型常数、阈值、刷新参数

docs/superpowers/specs/
  2026-06-08-worldcup-prediction-mvp-design.md

docs/superpowers/plans/
  2026-06-08-engine-core.md
  2026-06-08-plan0-data-source-probe.md
  2026-06-08-plan2-collectors.md
  2026-06-08-autonomous-local-mvp.md
  2026-06-09-commute-local-hardening.md

worldcup/
  config.py                     # 配置读取
  models.py                     # 数据模型与枚举
  team_identity.py              # 多赛事 team identity registry，包住 season-aware alias / provider id
  elo_local.py                  # 本地 Elo 基线冻结与本届赛果重放
  elo_replay.py                 # 国际比赛历史 Elo replay 与官方榜对照
  backtest_data.py              # 国际比赛历史结果转换为回测 CSV
  backtest.py                   # 离线回测、指标报告与参数扫描
  oddsportal_wc2022.py          # 2022 世界杯 OddsPortal 抓取产物标准化与回测 CSV join
  line_move_report.py           # 赔率/让球线移动分桶报告
  daily_eval.py                 # 赛后每日 results/eval/backtest 编排与本场首选日报
  decision_settlement.py        # 本场首选统一结算与 decision-only 战绩汇总
  postmatch_diagnostics.py      # legacy 完赛等级诊断；不进入当前产品链路
  csl_results_probe.py          # 中超历史赛果本地样例清洗与双源诊断 CLI
  csl_eval_data.py              # 中超本地 snapshot × 完赛赛果 join 成回测 CSV
  csl_ops_runner.py             # 中超本地实战闭环：dry-run、snapshot、归档、观察报告、postmatch
  lineup_audit.py               # 官方首发抓取 × snapshot/post-information odds 本地审计
  scores_capture.py             # The Odds API scores → 本地 results CSV（默认 dry-run）
  lineups_refresh.py            # FIFA public API 官方首发 → 本地 lineup cache（默认 dry-run）
  lineup_source_probe.py        # FIFA/FotMob 首发源可用性只读探测（默认 dry-run，不进模型）
  pre_match_runner.py           # 首发轮询 → 新 confirmed lineup → post-lineup refresh guard → 首发后 odds refresh 编排（默认 dry-run）
  pre_match_launch_agent.py     # 赛前首发轮询 LaunchAgent plist 生成器（不加载 launchd）
  odds_trend.py                 # 从 history 归档提取每场赔率走势点
  finished_record.py            # closing match_decision × 赛果定格，维护本地增量完赛 store
  differ.py                     # legacy 等级变化检测；当前通知不再调用
  pipeline.py                   # collector 输出对齐 + 兼容 facade
  pipeline_analysis.py          # 单场概率族、OU total shadow、lineup shadow 与分析输出
  pipeline_signals.py           # legacy 等级研究模块；当前 runner 不再调用
  match_decision.py             # MatchPick v3：覆盖率 + 市场证据优先的每场唯一首选
  local_runner.py               # 本地样例/缓存 → 分析快照 JSON
  refresh_runner.py             # source refresh → cache → analysis snapshot
  scheduler.py                  # 免费额度调度策略与 run metadata
  scheduled_refresh.py          # 调度判断 → 条件执行 refresh
  notifications.py              # 显著变化摘要与 WxPusher 通知
  ingest.py                     # 云端 ingest payload 与 HMAC dry-run
  ingest_server.py              # ingest 验签、防重放与本地幂等模拟
  ingest_app.py                 # 本地 ingest 应用层：验签 → SnapshotStore
  fastapi_app.py                # 本地 FastAPI route adapter
  store.py                      # SQLite snapshot 持久化
  store_contract.py             # SnapshotStore 协议边界
  store_factory.py              # SQLite/PostgreSQL store 选择
  postgres_store.py             # PostgreSQL snapshot 持久化适配器
  postgres_smoke.py             # PostgreSQL smoke dry-run guard
  query.py                      # 最新快照读取与比赛行投影
  ledger.py                     # 公共格式化兼容层；旧等级 view-model 仅供 legacy 读取
  ledger_html.py                # 研究台账渲染器；公开入口使用 decision-only 模式
  match_decision_html.py        # 旧的独立首选卡片页实现；公开入口不再调用
  preview.py                    # 静态 HTML 预览入口，委托研究台账 decision-only 渲染
  http_app.py                   # 标准库 HTTP 适配层和路由契约
  asgi_app.py                   # 无依赖 ASGI 适配层
  export.py                     # 静态站点/API 导出
  readiness.py                  # 本地上线前 readiness check
  ssh_deploy.py                 # git archive + SSH 一键代码部署（默认 dry-run）
  secrets.py                    # 本地 HMAC secret 生成助手，不写 .env
  quota.py                      # 本地 API quota ledger
  theoddsapi_keys.py            # The Odds API key slot 选择与 quota 轮换辅助
  sources/
    openfootball.py             # openfootball 请求与缓存
    fifa_lineups.py             # FIFA public API 日程/官方首发请求与缓存
    theoddsapi.py               # The Odds API 请求、缓存与 quota 记录
    theoddsapi_scores.py        # The Odds API scores 请求、缓存与 quota 记录
    eloratings.py               # Elo TSV 请求与缓存
  collectors/
    fifa_lineups.py             # FIFA live football JSON 解析为首发/替补上下文
    openfootball.py             # openfootball 赛程样例解析
    lineups.py                  # manual_json 首发/球员上下文离线解析
    theoddsapi.py               # The Odds API 赔率样例解析
    theoddsapi_scores.py        # The Odds API scores 离线解析为 MatchResult
    eloratings.py               # eloratings TSV 解析
    team_aliases.py             # 队名规范化与别名
    club_aliases.py             # 俱乐部联赛队名规范化与别名
    csl_results.py              # 中超历史赛果本地样例解析、双源校验与 replay candidate 输出
  engine/
    odds.py                     # 赔率去水、聚合
    elo.py                      # Elo 1X2 概率
    poisson.py                  # Poisson 比分矩阵
    handicap.py                 # 亚洲让球 EV
    ensemble.py                 # Elo + Poisson 集成
    value.py                    # EV / Edge / 等级 / 状态

tests/
  run_tests.py                  # 无 pytest 环境下的本地测试执行器
  collectors/                   # collector 离线解析测试
```

## 俱乐部联赛本地 MVP

俱乐部联赛以 competition adapter 接入。当前先做中超 `csl_2026` 本地 MVP，并保留英超/西甲/德甲/意甲/法甲后续平滑接入的 registry 约束。

### 多赛事平台化护栏

`worldcup.competitions` 的每个 competition profile 除基础元数据外，还声明：

```text
settlement_rule
identity_policy
model_family
refresh_priority
quota_budget
market_quality_profile
```

这些字段会进入 snapshot 的 `competition` block 和 scheduler dry-run，用来避免把世界杯默认假设静默套到俱乐部联赛。当前 `fifa_world_cup_2026` 使用 `national_team_alias` / `national_team_elo` / `worldcup_elo_poisson_v1`；`csl_2026` 使用 `club_identity_registry` / `club_rating_pending` / `club_elo_poisson_pending_v1`。

`worldcup.team_identity` 提供第一版 season-aware team identity registry。当前先覆盖 CSL 2026 的核心 live odds / 历史赛果别名，记录 `team_id`、`competition_id`、`season_id`、`canonical_key`、`aliases`、`provider_team_ids`、`active_from` 和 `active_to`。未知俱乐部不会被当成已知 identity，只能进入 unmatched 诊断。

Closing / finished / CSL eval 匹配按 `competition_id` 隔离；旧世界杯 history 缺少 competition block 时按 `fifa_world_cup_2026` 兼容。新 `finished` schema v2 只提供 `decision_tally`、`decision_sample`、`decision_coverage` 和每场 `closing_match_decision`；旧等级 tally 不进入新快照或公开投影。

本地只读 sports key 探测使用保存样例，不消耗 The Odds API quota：

```bash
python3 -m worldcup.sources.theoddsapi_sports --sample data/probe/theoddsapi_sports.json
```

中超本地 snapshot 从缓存 odds event 构建，默认不联网：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

`--competition` 与 `--competition-id` 等价，默认 competition id 为 `csl_2026`。

HTTP 预览/公开查询支持多赛事 latest 合并视图：同一个 store 里同时存在世界杯和中超 snapshot 时，`/preview` 与 `/api/matches` 会展示各赛事最新一份，并复用页面赛事筛选显示“中超 2026”。该查询只读本地/线上 SQLite 或 PostgreSQL store，不刷新赔率、不读取 `.env`、不调用 The Odds API。线上标准库 HTTP 进程会缓存 public view 和 `/preview` 渲染 HTML，避免每次请求重复扫描、解析历史大 snapshot 和重建大页面；`/preview` 还会把已渲染 HTML 写入 DB 同目录的签名校验磁盘缓存，服务重启后如果 snapshot 未变化可直接复用；签名 ingest 成功后会自动清空进程缓存，磁盘缓存会因 snapshot 签名变化自动失效。

中超当前 `rating_policy=club_rating_pending`：MatchPick v3 使用完整主盘口的去水市场共识兜底，不让占位 1500 或未过门槛的俱乐部 Elo 影响方向。`csl_model` 已与世界杯参数拆开：全局 replay 至少 300 场，且单队至少 30 场才允许该场使用真实评级；否则该场明确记为 `club_rating_team_sample_too_small` 并继续市场兜底。评级仍是 `shadow_only`，只有最新赛季、主场先验和同样本市场基准都达标后才能另行解除 pending。

### CSL Scheduled Publish

`worldcup.csl_scheduled_publish` 是中超自动刷新/发布入口。默认 dry-run 只读取本地 snapshot / quota 并输出决策，不读取 `.env`、不联网、不调用 The Odds API、不发布；只有显式 `--live` 且决策 due，或同时传 `--force`，才会读取 `.env`、刷新中超 odds、生成预测 snapshot 并 HMAC ingest 到线上。live due 时会先用 7M 赛程数组与中足联官方公开接口双源校验已完赛比分；只有日期、主客队和比分全部一致才原子更新本地 replay CSV。这两个公开源不消耗 The Odds API quota；抓取或校验失败时沿用旧 cache 并写质量警告，不阻断赔率刷新。

中超自动刷新采用“单场 due 触发、全赛事统一刷新”的省额度策略：调度器扫描当前 `csl_2026` snapshot 里的所有未来比赛，任一比赛命中锚点或首选鲜度保底就刷新整个 `soccer_china_superleague` sport key 一次，避免按每场单独调用 The Odds API。默认锚点为 `T-90` 和 `T-25`，正常额度时另保证在 `valid_until` 前 20 分钟刷新；全局最短刷新间隔为 30 分钟；当 quota remaining 低于等于 30 时只保留 `T-25`；没有未来比赛时最多每 24 小时做一次 discovery refresh，用于发现新的 odds event 赛程。

```bash
# dry-run：只看是否 due
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_scheduled_publish

# live：仅 due 时刷新并发布
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_scheduled_publish \
  --live \
  --cache-dir data/cache \
  --quota-path data/cache/quota.json \
  --snapshot-path data/cache/csl_publish_snapshot.json \
  --diagnostics-snapshot-path data/local/diagnostics/csl_live_league_snapshot.json \
  --env .env \
  --endpoint https://football.celab.xin/api/ingest/snapshot
```

`worldcup.csl_scheduled_launch_agent` 可生成本机 LaunchAgent plist，默认每 900 秒唤醒一次 runner，但 runner 自身仍会先做 due / quota / 30 分钟节流判断，所以唤醒频率提高不会直接变成每 15 分钟调用一次 The Odds API。生成或更新 plist 不会自动加载 launchd；实际写入 `~/Library/LaunchAgents/` 和 `launchctl bootstrap` 前必须单独确认。

### CSL Ops Runner

P9.23 新增一条中超本地实战命令，把本地状态检查、cache snapshot、赛前归档、观察报告和可选 postmatch 复盘收束到 `worldcup.csl_ops_runner`。默认 dry-run 只读本地文件，不写入、不读取 `.env`、不联网、不调用 The Odds API、不消耗 quota、不发布、不部署、不更新 LaunchAgent。

```bash
# 只读检查当前 CSL cache/results/history/quota 状态
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner

# 使用现有本地 cache 写 ignored 本地研究产物
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local

# 在本地赛果已确认后，同时跑赛后 eval/backtest/pending gate
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner \
  --run-local \
  --postmatch \
  --postmatch-min-sample 30 \
  --postmatch-warmup-matches 300 \
  --postmatch-min-eval-matches 200
```

`--run-local` 只允许写入 ignored 的 `data/local/` 或 `data/cache/` 产物：`data/local/diagnostics/csl_live_league_snapshot.json`、`data/local/diagnostics/csl_history/`、`data/cache/csl_observation_report_*.md|json`、`data/local/diagnostics/csl_ops_runner_*.json`，以及 postmatch 的 `data/local/backtest/` / pending gate 输出。摘要只包含安全计数、路径、质量警告和 safety flags，不输出 raw bookmaker rows、per-book prices、API key、secret、provider payload、资金或执行建议。

受控 live odds 刷新仍必须单独确认后才运行：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --live-odds --run-local
```

该模式会读取 `.env` 并消耗 The Odds API quota；默认 dry-run 和普通 `--run-local` 不会触发。

### 中超 Club Rating 本地基线

P9.2 新增本地 `club_rating` 基线能力，当前仍保持 `csl_2026.rating_policy=club_rating_pending`。样例或本地历史赛果可以进入诊断，但评级未达到可用标准前，公开首选只使用市场共识兜底，不让占位评级影响方向。

本地历史赛果 CSV 默认路径：

```bash
data/cache/club_results_csl_2026.csv
```

字段契约：

```text
competition_id,season,date,home_team,away_team,home_score,away_score,neutral
```

规则：

- `competition_id` 必须等于 `csl_2026`。
- `date` 使用 `YYYY-MM-DD`。
- `home_team` / `away_team` 通过俱乐部 alias 映射到 competition-scoped canonical key。
- `home_score` / `away_score` 必须是非负整数；无效行会跳过并进入 `data_quality.club_rating.skipped_rows`。
- `neutral` 支持 `0/1`、`true/false`、`yes/no`；中超常规主客场默认 `0`。

`league_runner` 只读取本地 cache，不联网：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

缺少 CSV、全局样本不足、CSV 无效、fixture 球队缺 rating，或该队少于 30 场 replay 时，snapshot 会在 `data_quality.club_rating` / `data_quality.warnings` 标记原因，并保留 1500 仅供结构兼容；MatchPick v3 不让该占位值参与方向选择。当前双源已校验并写入 2023–2026 共 856 场，最新到 2026-07-05；重庆铜梁龙与辽宁铁人各 17 场，所以它们的当前对阵仍在逐队门槛下。

### CSL Historical Results Probe

P9.3 新增本地-only 中超历史赛果 probe，用于从人工保存的 2023-2026 公开源样例中做严格 alias、双源比分/日期校验、质量门槛诊断和可选 replay candidate CSV。样例默认放在被忽略路径，例如 `data/probe/`；诊断默认写入 `data/local/diagnostics/`。

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_results_probe \
  --competition csl_2026 \
  --primary-source-id <primary_id> \
  --primary-sample data/probe/csl_results_primary_sample.csv \
  --check-source-id <check_id> \
  --check-sample data/probe/csl_results_check_sample.csv \
  --output data/local/diagnostics/csl_results_source_probe.json
```

该 probe 只读取本地 CSV/JSON 样例，不联网、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布 snapshot、不部署、不更新 LaunchAgent、不解除 `club_rating_pending`。只有显式传入 `--write-replay-candidate` 且本地质量门槛允许 `can_enter_replay=true` 时，才会写出 replay candidate CSV；该 candidate 不会自动安装到 `data/cache/club_results_csl_2026.csv`。

候选 CSV 仍沿用 P9.2 replay 契约：

```text
competition_id,season,date,home_team,away_team,home_score,away_score,neutral
```

双源冲突、未知 alias、主客队反转、比分冲突、日期冲突、主源缺校验源或校验源缺主源都会进入 diagnostics；`pending_gate.can_lift_club_rating_pending` 在 P9.3 中始终为 `false`。

当前赛季可用下面的受控入口校验/更新 replay cache；默认不联网，`--live` 只校验，`--live --write` 才写 ignored cache：

```bash
python3 -m worldcup.csl_results_refresh
python3 -m worldcup.csl_results_refresh --live
python3 -m worldcup.csl_results_refresh --live --write
```

### CSL Observation Report 与 Pending Gate

P9.14 新增两个中超本地诊断入口，用于中超开赛后从已保存的本地快照和本地历史赛果生成只读观察报告，不联网、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布 snapshot、不部署、不更新 LaunchAgent，也不解除 `club_rating_pending`。

本地观察报告读取已脱敏的 CSL runner snapshot，输出 Markdown 或 JSON 到 ignored 路径：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_observation_report \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json
```

该报告只保留研究所需的比赛、计数、警告和“本场首选 / 暂无可靠首选”摘要；会过滤 raw odds、bookmaker、provider、API key、secret、资金或执行建议等不应出现在报告里的内容，并保留“仅用于研究分析，不构成投注建议。”声明。

Pending gate 读取本地 `data/cache/club_results_csl_2026.csv`，做无同日泄漏的 walk-forward replay 诊断：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_pending_gate \
  --competition csl_2026 \
  --cache-dir data/cache \
  --warmup-matches 300 \
  --min-eval-matches 200
```

Gate report 除聚合样本外，还分赛季报告 model / uniform / home-prior，并要求同样本 `model_matched` 与 market 比较。当前有 8 场 opening/closing 快照与赛果可 join，该小样本 model 1X2 Brier 为 0.4678、market 为 0.5130；但 `8 < 200` 的市场基准门槛，只能记为暂时观察，不解除 `club_rating_pending`。Replay 按日期批量评估和批量更新 rating；同一天没有开球时间时，不会让当天早些比赛影响当天后续比赛的 rating 或 home-prior baseline。

### CSL Postmatch Eval Loop

P9.15 新增中超本地赛后评估闭环，用于把已归档的 CSL league snapshot 与本地完赛赛果 join 成现有 `worldcup.backtest` 可读取的 CSV，再计算模型 vs 市场的 Brier / Log Loss / 校准等研究指标。该链路只读本地 history/results，输出到 ignored `data/local/backtest/`，不联网、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布、不部署、不更新 LaunchAgent，也不解除 `club_rating_pending`。

要让 CSL 评估有效，必须先在赛前持续保留 opening/closing 候选 snapshot；没有开球前 snapshot 的完赛场会计入 `skipped_no_closing`，不能用来声称准确率。

```bash
# 0) 先 dry-run 校验当前 CSL snapshot 会归档到哪里；不写文件
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_snapshot_archive \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json \
  --history data/local/diagnostics/csl_history \
  --dry-run

# 1) 把人工确认过的当前 CSL snapshot 归档到本地 ignored history
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_snapshot_archive \
  --snapshot data/local/diagnostics/csl_live_league_snapshot.json \
  --history data/local/diagnostics/csl_history

# 2) 一条命令跑本地赛后复盘：eval CSV -> backtest -> pending gate
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_postmatch_runner \
  --history data/local/diagnostics/csl_history \
  --results data/cache/club_results_csl_2026.csv \
  --eval-out data/local/backtest/csl_2026_eval.csv \
  --report-out data/local/backtest/csl_2026_report.json \
  --min-sample 30 \
  --warmup-matches 300 \
  --min-eval-matches 200
```

判断“准确率”时必须同时看覆盖率和命中率：优先看 `csl_2026_report.json` 中 `sample.sample_too_small`、`markets.1x2.model_matched` vs `markets.1x2.market`、`markets.1x2.uniform`、校准分箱，以及 `csl_pending_gate` 的 `checks.market_baseline_sufficient`、`checks.latest_season_model_beats_home_prior_brier`。样本不足、closing snapshot 覆盖不足或模型弱于市场/最新赛季主场先验时，只能作为观察，不能调参或解除 `club_rating_pending`；公开首选继续使用明确标记风险的市场共识兜底。

## 本地验证

当前机器没有安装 `pytest` 时，用：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

如果以后安装了 `pytest`，也可以用：

```bash
python3 -m pytest -v
```

可选依赖安装：

```bash
python3 -m pip install -e ".[dev]"
```

本地 FastAPI 适配层可用以下命令启动：

```bash
python3 -m worldcup.fastapi_app --host 127.0.0.1 --port 8788 --db data/local/worldcup.db --env .env
```

FastAPI app 在明确确认 ECS 部署前只作为本地适配层使用。

PostgreSQL smoke dry-run guard 仅在明确选择 PostgreSQL/RDS 时先跑；SQLite 首发时返回 `blocked / expected_postgres` 是安全结果：

```bash
python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

该命令只验证前置条件并输出脱敏请求摘要，不连接数据库、不发送 HTTP。

官方首发抓取默认 dry-run；不传 `--live` 不联网，不传 `--write` 不写入 `lineups_wc2026.json`，不传 `--notify` 不发通知：

```bash
# 只读公网检查 FIFA 官方首发，输出 confirmed/missing 摘要
python3 -m worldcup.lineups_refresh --live

# 写入已确认首发，保留旧 confirmed cache，不用空轮询覆盖已有数据
python3 -m worldcup.lineups_refresh --live --write

# 临赛窗口内官方首发仍缺失时，通过 WxPusher 只提醒一次
python3 -m worldcup.lineups_refresh --live --write --notify

# 首发源候选探测 dry-run：不联网、不写盘、不进入模型
python3 -m worldcup.lineup_source_probe --dry-run

# 只读探测 FIFA/FotMob 是否在观察时刻给出 confirmed/predicted/missing 11 人，写本地诊断
python3 -m worldcup.lineup_source_probe --live --write

# 追加 ignored 历史观测并生成 Markdown 报告；不进入模型、不刷新赔率
python3 -m worldcup.lineup_source_probe --live --write --append-history --write-report

# 赛前编排 dry-run：不联网、不写盘、不发通知、不刷新 odds
python3 -m worldcup.pre_match_runner

# 抓官方首发并写 cache；缺首发和首发链路缺口可分别通知，不会刷新 odds
python3 -m worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --notify-audit

# 新 confirmed lineup 出现后，只做首发后 odds refresh guard；不刷新 odds、不消耗 The Odds API quota
python3 -m worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --refresh-guard

# 新 confirmed lineup 出现后，经 guard 允许再强制跑一次首发后 odds refresh；会消耗 The Odds API quota，启用前需单独确认
python3 -m worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --refresh-guard --refresh-after-lineups --live-refresh

# 生成赛前轮询 LaunchAgent 配置预览；只输出 JSON，不写系统文件、不加载 launchd
python3 -m worldcup.pre_match_launch_agent

# 写一份 plist 草案到本地 cache 供人工检查；真正写入 ~/Library/LaunchAgents 并加载需单独确认
python3 -m worldcup.pre_match_launch_agent --out data/cache/xin.celab.football.pre-match.plist
```

## 离线回测

回测框架只读本地历史 CSV，不联网，输出研究指标（Brier / Log Loss / 校准分箱 / EV 与赔率分层 / 总进球诊断），不含任何资金建议。

```bash
python3 -m worldcup.backtest --csv data/local/backtest/history.csv --min-sample 200
```

- CSV 列契约见 `tests/data/backtest_sample.csv`（合成样例，仅演示格式，不得用于正式结论）。
- 历史数据链路：`python3 -m worldcup.backtest_data` 把 `data/probe/` 的国际比赛结果样例（含 `worldcup.elo_replay` 推演的赛前 Elo）转换成回测 CSV；`python3 -m worldcup.elo_replay` 输出 replay 与官方 eloratings 榜单的对照。
- 参数扫描：`--sweep poisson.dc_rho=0,-0.05,-0.1,-0.15` 一次产出多取值对比报告；首份真实回测证据见 `docs/research/2026-06-10-intl-backtest-baseline.md`。
- 真实历史收盘赔率来源需单独确认后再接入。
- 报告默认写入被忽略的 `data/local/backtest/report.json`。
- 样本量低于 `--min-sample` 时报告带 `sample_too_small: true`，不能据此下强结论。
- 报告中 `markets.*.model` 是全样本模型指标，`model_matched` 是与市场基线同样本（有收盘赔率的行）的模型指标；对比模型 vs 市场请用 `model_matched` vs `market`。
- 可用 `--set section.key=value` 做单次参数实验（不改 `settings.yaml`），例如 `--set poisson.dc_rho=-0.1 --set poisson.mu_market_weight=0`。
- CSV 中任何十进制赔率必须 > 1.0，否则按行号报错。
- `worldcup.oddsportal_wc2022` 用于一次性把 2022 世界杯 OddsPortal / OddsHarvester 本地抓取产物 join 成回测 CSV；原始与 join 产物默认写入被忽略的 `data/local/backtest/`。
- `worldcup.line_move_report` 用于读取 `wc2022_history.csv`，按 1x2 主胜赔率漂移与 AH 线移动分桶输出研究报告；报告默认写入被忽略的 `data/local/backtest/line_move_report.json`。
- The Odds API live payload 中任何 decimal odds `<= 1.0` 的 quote 会在解析层隔离，不进入聚合、去水、EV 或信号生成；snapshot `data_quality.invalid_odds_count` 记录全量计数，`invalid_odds_examples` 最多保留 10 条可审计上下文。
- Phase 2A 起，每场 snapshot 的 `model.probability_families` 可 shadow 输出 `model_raw`、`model_market_total`、`market_only` 三套概率和 provenance；当前生产信号仍使用 fail-safe 保护下的 legacy `model_market_total` 路径，公开 API/页面继续读取旧字段。老 snapshot 缺少该 block 仍有效。

另外：OU 大小球模型会按每场 over/under 双边报价家数选择当前主流 half-goal 盘口线，再由该线的市场去水概率反推总进球，并与配置先验 `poisson.mu_total` 按 `poisson.mu_market_weight` 混合；无可用 OU 主线时回退 `ou_main_line` 配置。snapshot 的 `model.mu_total` 记录实际使用的总进球，`model.ou_line` 与 `market.ou_2_5.line` 记录实际大小球盘口线；`ou_2_5` key 暂时保留作兼容字段名，不代表永远固定 2.5。

模型还内置 Dixon-Coles 低比分修正开关 `poisson.dc_rho`（默认 `0.0` 即关闭，行为与历史版本一致）；rho 的取值必须由真实历史数据回测确定后再启用。mu 市场锚定仅在 OU 盘口 over/under 双边报价家数均达到 `odds.min_books` 时生效，否则回退先验 `poisson.mu_total`。注意：`dc_rho != 0` 时比分矩阵的大小球概率与 mu 锚定的纯 Poisson 反推存在微小近似偏差，rho 为小负数时可忽略。

总进球先验支持随 Elo 差上升：`poisson.mu_dr_slope`（默认 `0.0` 关闭；clamp 见 `mu_prior_min/max` 代码默认 1.5/4.0）；拟合证据见 `docs/research/2026-06-10-mu-dr-fit.md`。

## 世界杯期间评估数据（自有赔率 + 赛果）

每次 live refresh 会把 snapshot 归档到被忽略的 `data/local/history/`（merge 进本机 main 后自动生效，无需部署服务端）。自动刷新链路可用只读验收命令检查最近归档和 LaunchAgent 指向：

```bash
python3 -m worldcup.refresh_audit
```

日常运维推荐使用一键只读检查命令；它会汇总本机 snapshot/history/quota/LaunchAgent、本机 scheduled-publish 日志、pre-match LaunchAgent 参数、pre-match 日志、最新 lineup audit 摘要、公网 `/healthz` / `/api/matches` / 页面更新时间、ECS 服务/SQLite/latest snapshot 和日志安全计数。pre-match wiring 会显示 `--refresh-guard`，且如果检测到 `--live-refresh` 但没有 `--refresh-guard` 会计入 error。该命令不触发 refresh、不发布、不调用 The Odds API、不消耗 The Odds API quota、不读取或打印 secret；如需纯本地离线巡检，加 `--no-public --no-remote`。P9.10 起，默认 JSON 会包含顶层 `report.csl_live_odds` 日常摘要；需要人工快速巡检时可用 `python3 -m worldcup.ops_check --format summary` 输出短报告，展示 CSL live odds cache 状态、event/fixture 数、provider/quota 摘要、synthetic/alias/非法赔率 guard、runner 状态、`club_rating_pending`/`odds_event_only` warning 和 runner 强等级残留异常。

```bash
python3 -m worldcup.ops_check
```

```bash
python3 -m worldcup.ops_check --format summary
```

```bash
python3 -m worldcup.ops_check --no-public --no-remote --format summary
```

P9.11 起，可把上述本地巡检摘要写成本地 dry-run 日报文件：

```bash
python3 -m worldcup.ops_daily_report
```

默认输出到被忽略的 `data/cache/ops_daily_report_<UTC>.md`，只跑本地 `ops_check`，并强制跳过公网 HTTP、ECS remote、live refresh、通知发送和部署动作。该日报只使用 `ops_check` 已脱敏的 `report` 摘要，不输出 raw odds、bookmaker、market、price、URL、API key、HMAC、`.env` 值或原始响应。需要 JSON 产物时：

```bash
python3 -m worldcup.ops_daily_report --format json
```

代码部署可用 SSH 一键部署工具，默认只做 dry-run：检查 git ref、工作区是否干净，并输出将要发布的 release 路径，不连接 ECS、不切换服务、不发布 snapshot。

```bash
python3 -m worldcup.ssh_deploy
```

真实部署必须显式加 `--live`；部署使用本地 `git archive` 通过 SSH stdin 上传到 `/opt/worldcup/releases/<commit>`，远端 `py_compile` 关键 HTTP/query 文件后原子切换 `/opt/worldcup/current`，重启 `worldcup.service`，先在 ECS 本机请求 `http://127.0.0.1:8788/readyz` warmup 最新 public view，再公网 smoke `/healthz`、`/api/matches` 和 `/preview`；这样不需要把 `/readyz` 加到 Nginx 公网白名单，也能降低重启后第一波重页面请求风险。如需 smoke 失败自动回滚到上一 release：

```bash
python3 -m worldcup.ssh_deploy --live --rollback-on-fail
```

如果本机 TUN/代理把 ECS IP 路由到 fake-ip 网段，使用本机 Wi-Fi 地址绑定 SSH 源地址：

```bash
python3 -m worldcup.ssh_deploy --live --rollback-on-fail --bind-address 192.168.31.152
```

该工具不读取 `.env`、不调用 The Odds API、不发布中超或世界杯 snapshot、不改 LaunchAgent、不推送 git；SSH 连接异常、工作区脏或 smoke 失败都会在 JSON 摘要中标记为 blocked/failed/rolled_back。

Elo 基线与本地重放可用只读命令检查；该命令只读 `data/cache/elo_baseline_*` 与 openfootball 缓存，不联网、不打印 secret：

```bash
python3 -m worldcup.elo_local --check
```

`worldcup.scheduled_publish --live` 发布成功后只比较刷新前后的本场首选：新增/撤销首选、方向改变、安全命中率变化至少 2 个百分点或参考赔率变化至少 0.05 时，才调用 `/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind` 发送手机通知。通知不再包含等级、EV 或 Edge；结果只记录发送状态、摘要和场次数，不记录 WxPusher UID、URL、token 或原始响应；临时禁用可加 `--no-notify`。

当 openfootball 缓存里已有完赛比分时，snapshot 会给对应比赛附加 `result`；完赛页只结算赛前冻结的 `closing_match_decision`，胜平负 / 大小球显示“命中”或“未中”，亚洲让球保留“命中 / 未中 / 走水”及半赢/半输 settlement class。

最新 refresh 富化后的 snapshot 包含顶层 `finished` schema v2：用开球前最后一轮 closing snapshot 的 `match_decision` 与本地 90 分钟赛果定格完赛场。`decision_tally` 统计 `hit / miss / push / no_pick`；走水和主动放弃都不进入命中率分母。`decision_coverage` 另列缺 closing、缺 decision、损坏 decision 和未解析赛果，避免把数据缺口包装成主动放弃。新记录不写 `closing_signals`；旧 store 中已有的等级数据保持原样但只读兼容，构建新 snapshot 时会被剥离。

赛后链路已由 LaunchAgent `xin.celab.football.daily-eval` 每天北京时间 16:30 自动执行 `python3 -m worldcup.daily_eval --notify --live-scores`：小组赛阶段可先调用 The Odds API scores 端点补抓赛果（每天约 2 credits，同 key 槽位轮换），再依次 `results_capture` → `eval_data` → `backtest` → `finished_record` 并推送研究日报（完赛数、评估样本、模型 vs 市场指标、本场首选命中/未中/走水/暂无首选）；无新增赛果不推送。淘汰赛阶段该 scores 补抓默认会被 `knockout_score_manual_review_required` 阻断，需人工确认 90 分钟比分口径后才可用 `--allow-knockout-scores` 放行。

比赛日之后跑：

```bash
# 1) 从已缓存的 openfootball 数据提取完赛比分（幂等，可重复跑）
python3 -m worldcup.results_capture

# 1a) openfootball 录入滞后时，用 The Odds API scores 手动补抓赛果（约 2 credits）
python3 -m worldcup.scores_capture --live

# 1b) 淘汰赛阶段仅在人工确认 scores 为 90 分钟口径后使用
python3 -m worldcup.scores_capture --live --allow-knockout-scores

# 2) 用"开球前最后一份"归档 snapshot 的赔率 join 赛果，生成带赔率的回测 CSV
python3 -m worldcup.eval_data

# 3) 用现有回测评估真实表现（EV 分层、model_matched vs market 此时有意义）
python3 -m worldcup.backtest --csv data/local/backtest/wc2026_eval.csv --min-sample 30 --out data/local/backtest/wc2026_report.json

# 4) legacy 等级诊断仅供读取旧历史，不进入当前本场首选产品链路
python3 -m worldcup.postmatch_diagnostics

# 5) 回填历史 closing snapshot 的 shadow 诊断（只读，不调参、不联网）
python3 -m worldcup.shadow_backfill_diagnostics

# 6) 审计官方首发是否进入 snapshot / post-information odds 链路（只读，不联网）
python3 -m worldcup.lineup_audit

# 6a) 对开赛前仍存在的首发链路缺口发一次性通知（不联网抓数据、不刷新 odds）
python3 -m worldcup.lineup_audit --notify
```

- 每次 live refresh 成功获取新赔率后，原始逐家报价会 gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`（兜底缓存轮不归档），用于赛后赔率异动研究；该目录不进 git。
- `worldcup.postmatch_diagnostics` 当前仅作为 legacy 历史诊断入口保留；它不参与新 snapshot、公开 API、页面、首选战绩或日报。新调优应基于 `decision_tally` 和版本化 closing decision 样本，不得再按等级结论调参。
- `worldcup.shadow_backfill_diagnostics` 只读本地 snapshot/history/finished 数据，输出 `data/local/diagnostics/shadow_backfill_diagnostics.json`，用于给历史 closing 信号回算 `ah_validation_shadow` 和 `movement_shadow` 并按赛果分桶；该报告不改模型、不改信号等级、不回填线上数据，样本较少时只能作为观察。
- `worldcup.lineup_audit` 只读本地 `lineups_wc2026.json`、最新 snapshot、history snapshot 和缺首发通知状态，输出 `data/local/diagnostics/lineup_audit.json`，用于确认官方首发是否在开赛前抓到、是否进入 snapshot、是否已有 post-information odds；该报告不联网、不刷新赔率、不发布线上数据。显式 `--notify` 时，只对开赛前仍存在的 `captured_without_snapshot_input` / `captured_without_post_information_odds` 发一次性 WxPusher 通知，去重状态写入同一个被忽略的通知状态文件。

已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 采用 closing snapshot 的主盘线与均价（本改动合入前的老归档快照无 `ah_main`，对应 AH 列为空）；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。Elo 重放与页面赛果显示仍以 openfootball 为准，openfootball 录入滞后期间页面“预测结果”可能晚于日报。淘汰赛（6-28 起）scores 可能含加时/点球比分，与 1X2 的 90 分钟结算口径冲突，因此 `worldcup.scores_capture` / `worldcup.daily_eval --live-scores` 默认阻断 scores 自动写入；人工确认 90 分钟比分口径后才可显式使用 `--allow-knockout-scores`。

## API 注册清单

API-Football 与 The Odds API 已完成第一轮探测；其它赔率源可作为后续容灾或交叉校验候选。

| 用途 | 服务 | 注册 / 官网 |
|---|---|---|
| 主数据源：赛程、结果、赔率探测 | API-Football | https://www.api-football.com/ |
| 赔率备源 | The Odds API | https://the-odds-api.com/ |
| 赛果及时源 | The Odds API scores | 同 key 轮换 |
| 赔率备源 | odds-api.io | https://odds-api.io/ |
| 赔率低频交叉校验 | OddsPapi | https://oddspapi.io/ |
| 免费赛程源 | openfootball/worldcup.json | https://github.com/openfootball/worldcup.json |
| Elo 基线重锚定 | World Football Elo Ratings | https://www.eloratings.net/ |

拿到 key 后，本地创建 `.env`，不要提交：

```bash
API_FOOTBALL_KEY=...
THE_ODDS_API_KEY=...
THE_ODDS_API_KEY_PRIMARY=...
THE_ODDS_API_KEY_SECONDARY=...
ODDS_API_IO_KEY=...
ODDSPAPI_KEY=...
INGEST_HMAC_SECRET=...
WORLDCUP_STORE=
DATABASE_URL=
```

`THE_ODDS_API_KEY` 保持旧入口兼容，也会作为 primary fallback；新赛期自动轮换建议同时配置 `THE_ODDS_API_KEY_PRIMARY` 和 `THE_ODDS_API_KEY_SECONDARY`。`.env` 已被 `.gitignore` 忽略，真实 key 不要写入文档或提交。

## 下一步

1. Gate C HTTPS 已完成：`https://football.celab.xin/` 对外展示研究台账。
2. 公网开放 `/`、`/preview`、`/api/matches`、`/healthz`、`/api/ingest/snapshot`；`/api/snapshot/latest` 返回 404；`/readyz` 是应用内部 warmup 路由，当前不经 Nginx 公网开放。
3. 本机 `launchd` 已启用 `xin.celab.football.scheduled-publish`，每 15 分钟唤醒一次；真正刷新/发布仍由 scheduler due 判断控制。
4. 本机 `launchd` 已启用 `xin.celab.football.pre-match`，每 300 秒运行 lineups-only 赛前首发轮询和首发链路审计通知；不带 `--live-refresh`，不会自动刷新 odds。
5. 下一步观察首轮 due 后的刷新、线上 ingest、Nginx/systemd 日志、certbot 自动续期和赛前首发轮询日志。
6. RDS/PostgreSQL 暂不需要；等多用户、备份或查询压力变大再升级。

## 重要约束

- API key、RDS 连接串、HMAC 密钥、Cookie、token 不得写入 git、文档或回复。
- macmini 不直连 RDS/OSS，后续只调用 ECS ingest API。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- Elo 来源为本地基线重放：`data/cache/elo_baseline_*.tsv` + openfootball 完赛比分按 eloratings 公式（K=60、中立场）增量重放生成 `elo_world.tsv`；eloratings 抓取仅用于重新锚定基线，抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不单独阻断本场首选。重放计算失败时回退沿用现有 `elo_world.tsv` 并记 `elo_local` 错误。实现见 `worldcup/elo_local.py`。
- The Odds API 按免费额度使用：常规每天 1 次，每场保留 T-12小时 / T-6小时 / T-90 / T-55 / T-35 / T-25 临赛锚点；低额度（≤30）只保 T-90 / T-55 / T-35 / T-25。调度会按本地 quota ledger 保守轮换 `THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY`：primary 未耗尽时优先用 primary，primary 剩余额度为 0 时切到 secondary；两个配置槽都耗尽时继续暂停并报告 `quota_exhausted`。任一槽位剩余额度跌破 100 / 30 / 10 / 0 时会随当轮发布自动发 WxPusher 额度告警（每个槽位每个阈值只发一次，跨 0 即槽位耗尽/自动切换提示；`--no-notify` 可静音）。更换或新增 key 后，需经确认执行一次 `worldcup.scheduled_publish --live --force` 让新额度写回 quota ledger（耗尽状态下调度不会自行恢复）。
- FIFA public API 首发抓取不消耗 The Odds API quota，也不需要 key；它是公开源，不是付费 SLA 数据源。`worldcup.lineups_refresh` 抓不到官方首发时只能记录 missing 或发缺失通知，不能伪造 confirmed；默认只在开赛前 35 分钟内发缺首发通知，避免过早提醒。`--write` 合并保留旧 confirmed cache，避免未公布轮询清空已确认首发。FIFA `source_match_no` 与 openfootball/本地 snapshot 编号可能不是同一套编号，首发绑定不得只依赖编号，必须通过双方 canonical team + UTC kickoff 校验。`worldcup.pre_match_runner` 只有在 `newly_confirmed > 0` 时才会允许触发首发后 odds refresh；`--refresh-guard` 会先 dry-run 检查调度决策和 quota，`--live-refresh` 会消耗 The Odds API quota，当前已安装的 `xin.celab.football.pre-match` 不包含该参数。如需改为自动首发后 odds refresh，必须单独确认后更新 plist 并重新加载 launchd。
- ingest 必须绑定 `timestamp`、`run_id`、`snapshot_id` 和 body hash 做 HMAC；dry-run 不发送请求，也不能打印 secret。
- ingest server 默认防重放窗口为 300 秒；服务端必须用 `X-Worldcup-Idempotency-Key` 做幂等。
- `/healthz` 只能报告服务存活，不得输出环境变量、密钥、quota 或 snapshot 内容；应用内部 `/readyz` 只能输出轻量 ready 状态和 match_count，不得输出完整 snapshot、secret、quota 或 provider 原始信息。
- 本地预览页必须保留研究免责声明，不显示资金相关字段。
- readiness check 只报告变量名、文件状态和内容完整性，不能输出密钥值；`.env.example` 必须只含变量名和空值。
- 所有公开输出都必须保留免责声明。
- 公开产品只能输出 `MATCH_PICK` 或 `NO_CLEAN_MARKET`；S/A/B/C、EV/Edge、旧 decision label 和 `signals` 只允许内部只读兼容历史数据，不得参与当前首选排序、公开 API、页面、通知、日报或 v3 战绩。
- 本场首选以覆盖率和安全命中率共同约束：只要存在开赛前有效、可结算的主盘口就必须给出一个首选；完整书商不足、离散度超限、模型内部严重分歧、四分之一/极深盘或俱乐部评级 pending/missing/invalid 只能触发风险扣分、备用候选或市场兜底。只有赔率全部无效/过期、比赛已开始或不存在任何可结算盘口时才允许 `NO_CLEAN_MARKET`。
- 任何回放必须同时报告覆盖率、命中/未中和市场基准，不能通过减少首选数量提高表面命中率。当前历史样本仍有限，只能报告观察结果，不据此调整 Elo/Poisson 参数或解除 `club_rating_pending`。
- 所有 1X2 edge-safe、OU independent total、AH push-aware fair odds 相关字段仍作为内部 diagnostic / shadow；它们可参与证据排序研究，但不能绕过开赛前数据边界、赔率时效和可结算性要求。
