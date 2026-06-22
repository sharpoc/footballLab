# 世界杯足彩分析站

这是一个 2026 世界杯研究/分析站项目。当前 MVP 目标是先跑通：

1. 数据源探测
2. Elo + Poisson + 赔率去水分析
3. 价值信号分级
4. 后续上传到阿里云网站展示

项目定位是**研究/分析工具**，不构成投注建议，不显示下注金额，不做追损、重注或喊单。

## 当前状态

- Git 仓库已初始化。
- Plan 1 引擎核心已完成第一版。
- 本地测试执行器通过：`460/460 tests passed`。
- Plan 0 核心数据源探测已完成第一轮：openfootball 赛程、eloratings Elo、The Odds API 赔率可用；API-Football Free plan 不能访问 2026 season。
- Plan 2 已启动：当前完成纯离线解析层、单场价值信号、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、按每场比赛独立计算的刷新计划、post-information odds 定向刷新调度判定、post-lineup refresh guard、run metadata、调度执行包装、显著变化手机通知、完赛战绩定格、赔率走势富化、AH 验证 shadow、研究候选池 `candidate_grade`、首发/球员影响 `lineup_shadow` schema、`manual_json` 首发/球员本地入口、FIFA public API 官方首发抓取 CLI、赛前首发轮询编排 runner、赛前 LaunchAgent plist 生成器、lineups-only 赛前 LaunchAgent、官方首发链路审计与一次性通知、独立 OU total `ou_total_shadow` schema、AH candidate 正式激活规则、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照，本地 runner 生成的快照也包含 ingest 所需 run metadata。
- Plan 3A FastAPI 本地适配层已实现并完成测试。
- Plan 3B PostgreSQL store 适配器已在 `SnapshotStore` 边界后实现；测试只使用 fake connection，未连接真实数据库。
- Plan 3C store 选择接线已完成：本地 CLI 默认 SQLite，也可以通过 `WORLDCUP_STORE=postgres` 加 `DATABASE_URL` 显式选择 PostgreSQL；本轮未连接真实数据库。
- Plan 3D PostgreSQL smoke dry-run guard 已完成：只验证 PostgreSQL smoke 前置条件并输出脱敏请求元数据，不发 HTTP、不连数据库。
- Plan 4 研究台账 UI 已实现并部署到 `football.celab.xin`；公开页面已中文化，桌面和移动端浏览器 QA 已通过。
- Plan 5 Gate C HTTPS 已完成：`football.celab.xin` 通过 Nginx 将公网 HTTPS 流量反代到 `127.0.0.1:8788` 上的 `worldcup.http_app`；`/api/snapshot/latest` 返回 404；Let's Encrypt 证书续期已配置；公网读取和 ingest smoke 已通过。

## 技术栈

- Python 3
- 标准库优先
- 当前引擎不联网、不连数据库、不依赖云资源
- 当前 collector 解析层不联网；后续真实请求层可再引入 HTTP 客户端
- 当前 refresh runner 默认 dry-run；只有显式 `--live` 才会读取 `.env` 并联网消耗 The Odds API 额度
- 当前 scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策，不会联网或写入状态；全局 due 由所有比赛 `refresh_plan.next_update_at` 的最早值决定；若某场 `lineup_shadow` 显示首发已确认但 odds 早于首发信息，则单场计划会给出 `post_information_odds_required`，在额度未耗尽时把下一次刷新提前到当前 dry-run 时刻
- 当前 scheduled refresh 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会调用 refresh runner
- 当前 scheduled publish 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会刷新数据并向 HTTPS ingest endpoint 发送签名 snapshot；发布成功后会对比上一轮 snapshot，只有显著变化时才通过全局 WxPusher 工具发送手机通知，可用 `--no-notify` 关闭
- 当前 ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求
- 当前 ingest server 是纯本地验签/幂等模块；FastAPI adapter 已复用它，ECS 部署另行确认
- 当前 SQLite store / preview 都是本地低风险链路；默认输出在已忽略的 `data/local/` 或 `data/cache/`
- 当前 PostgreSQL store adapter 可用于后续 ECS/RDS 接入；`psycopg` 只作为可选依赖声明，本轮未安装、未连接真实数据库
- 当前 store selection 默认 `sqlite`；单服务器 MVP 首发推荐 SQLite，只有显式 `--store postgres` 或 `.env` 中 `WORLDCUP_STORE=postgres` 时才要求 `DATABASE_URL`
- 当前 PostgreSQL smoke guard 默认只做 dry-run；SQLite 首发路线下返回 `blocked / expected_postgres` 是安全结果，且不打印 DSN、secret、签名或请求 body
- 当前 HTTP 适配层已用于 ECS 正式公网入口；服务只监听服务器本机 `127.0.0.1:8788`，由 Nginx 对 `football.celab.xin` 提供 HTTPS 反代
- 当前 ASGI 适配层无外部依赖，只包装本地 HTTP 路由契约；正式 ASGI server / ECS 部署需单独确认
- 当前 FastAPI app 仍作为可选适配层；Gate B 服务器 smoke 采用无额外依赖的标准库 HTTP app
- 当前 `/healthz` 不读 DB、不依赖 secret，只用于本地和后续云端健康检查契约
- 当前静态导出默认写入已忽略的 `data/cache/site/`
- 当前 refresh runner 在写盘和 history 归档前做本地富化：每场 match 可附加 `odds_trend` 走势点，顶层可附加 `finished` 完赛定格块；富化失败只输出 warning，不阻断 snapshot 生成或发布
- 当前 `worldcup.lineups_refresh` 可用 FIFA public API 抓取官方首发；默认 dry-run，不联网写盘，只有显式 `--live` 才请求 FIFA 公网，只有再传 `--write` 才写入被忽略的 `data/cache/lineups_wc2026.json`。当临赛窗口内 FIFA 仍未返回两队 11 人首发时，可显式 `--notify` 通过 WxPusher 发一次缺失通知，去重状态写入被忽略的 `data/local/lineups_missing_notifications.json`。`worldcup.pre_match_runner` 可编排“首发轮询 → 新 confirmed lineup → post-lineup refresh guard → 首发后 odds refresh”，默认仍是 dry-run；`--refresh-guard` 只调用 scheduled refresh 的 dry-run 决策并返回 quota / policy 摘要，不刷新 odds、不消耗 The Odds API quota；如果同时打开 `--refresh-after-lineups --live-refresh`，guard 在 quota 未知或低于 `--min-refresh-quota` 时会阻断 live odds refresh。只有显式打开 `--live-lineups` / `--write-lineups` / `--refresh-after-lineups` / `--live-refresh` 才会逐步触发公网抓取、写本地 cache 和 The Odds API 刷新。`xin.celab.football.pre-match` LaunchAgent 已安装为 lineups-only + audit-notify 模式，每 300 秒运行 `worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --notify-audit`，不带 `--live-refresh`，所以不会自动消耗 The Odds API 刷 odds；生成未来 live-refresh plist 草案时会自动包含 `--refresh-guard`。本地 runner 会可选读取同一输入目录下的 `lineups_wc2026.json`，把已确认首发、替补、缺阵、阵型和球员影响 delta 接入 `lineup_context`；绑定首发上下文时，`source_match_no` 只能作为候选，必须同时校验双方 canonical team 和 UTC 开球时间，避免 FIFA 编号与本地赛程编号不一致时错挂；当前未接入付费首发 API。
- 当前 `odds_trend` / `odds_movement` 只读最近 10 天 history 归档并按文件名时间窗过滤；`odds_movement` 仅作为赔率/盘口移动 diagnostic，不参与模型、EV 或信号等级裁决；信号级 `movement_shadow` 标注赔率/盘口移动是否支持该条信号，并作为 AH candidate 正式激活的必要门槛之一；`candidate_grade` 默认只用于研究候选池，不计入正式 S/A 战绩，只有 AH candidate 同时通过 AH shadow、movement shadow、硬质量 veto、已确认首发和 post-information odds 门槛时，才会由 `attach_trends()` 激活为正式 `grade=raw_grade`；`lineup_shadow` 仅在输入 `lineup_context` 时输出首发/球员影响前后对比，不改变 active 概率、EV/Edge 或正式等级，但若首发未确认，或首发已确认但 odds 尚未 post-information，都会阻止 AH candidate 正式激活；`ou_total_shadow` 仅对比 active market-total OU 与 independent/raw OU total，不改变正式 OU 信号等级；`finished` 使用被忽略的 `data/local/finished_record_store.json` 增量缓存，已定格比赛不随每 15 分钟刷新重算
- 当前静态预览/导出页为研究台账 UI：只展示研究信号、每场下次更新时间、本届 S/A 信号战绩、已完赛战绩区、赔率走势、方法说明、脱敏数据质量状态和免责声明，不显示下注金额或资金相关字段；老 snapshot 缺少 `finished` 或 `odds_trend` 时页面会容忍缺键
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
  elo_local.py                  # 本地 Elo 基线冻结与本届赛果重放
  elo_replay.py                 # 国际比赛历史 Elo replay 与官方榜对照
  backtest_data.py              # 国际比赛历史结果转换为回测 CSV
  backtest.py                   # 离线回测、指标报告与参数扫描
  oddsportal_wc2022.py          # 2022 世界杯 OddsPortal 抓取产物标准化与回测 CSV join
  line_move_report.py           # 赔率/让球线移动分桶报告
  daily_eval.py                 # 赛后每日 results/eval/backtest 编排与日报
  postmatch_diagnostics.py      # 完赛 S/A 信号本地诊断报告
  lineup_audit.py               # 官方首发抓取 × snapshot/post-information odds 本地审计
  scores_capture.py             # The Odds API scores → 本地 results CSV（默认 dry-run）
  lineups_refresh.py            # FIFA public API 官方首发 → 本地 lineup cache（默认 dry-run）
  pre_match_runner.py           # 首发轮询 → 新 confirmed lineup → post-lineup refresh guard → 首发后 odds refresh 编排（默认 dry-run）
  pre_match_launch_agent.py     # 赛前首发轮询 LaunchAgent plist 生成器（不加载 launchd）
  odds_trend.py                 # 从 history 归档提取每场赔率走势点
  finished_record.py            # closing 信号 × 赛果定格，维护本地增量完赛 store
  differ.py                     # 两轮变化检测
  pipeline.py                   # collector 输出对齐 + 单场分析编排
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
  ledger.py                     # 研究台账 UI 安全投影与格式化
  ledger_html.py                # 研究台账 HTML/CSS/vanilla JS 渲染器
  preview.py                    # 静态 HTML 预览页入口，委托研究台账渲染器
  http_app.py                   # 标准库 HTTP 适配层和路由契约
  asgi_app.py                   # 无依赖 ASGI 适配层
  export.py                     # 静态站点/API 导出
  readiness.py                  # 本地上线前 readiness check
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

本地只读 sports key 探测使用保存样例，不消耗 The Odds API quota：

```bash
python3 -m worldcup.sources.theoddsapi_sports --sample data/probe/theoddsapi_sports.json
```

中超本地 snapshot 从缓存 odds event 构建，默认不联网：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

`--competition` 与 `--competition-id` 等价，默认 competition id 为 `csl_2026`。

中超初期 `rating_policy=club_rating_pending` 时，强信号会降级或仅作为观察；不得把国家队 Elo 套用于俱乐部联赛。任何 live odds 探测、scheduled publish、ECS ingest 或 LaunchAgent 更新都需要单独确认。

## 本地验证

当前机器没有安装 `pytest` 时，用：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

如果以后安装了 `pytest`，也可以用：

```bash
python3 -m pytest -v
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

日常运维推荐使用一键只读检查命令；它会汇总本机 snapshot/history/quota/LaunchAgent、本机 scheduled-publish 日志、pre-match LaunchAgent 参数、pre-match 日志、最新 lineup audit 摘要、公网 `/healthz` / `/api/matches` / 页面更新时间、ECS 服务/SQLite/latest snapshot 和日志安全计数。pre-match wiring 会显示 `--refresh-guard`，且如果检测到 `--live-refresh` 但没有 `--refresh-guard` 会计入 error。该命令不触发 refresh、不发布、不读取或打印 secret。

```bash
python3 -m worldcup.ops_check
```

Elo 基线与本地重放可用只读命令检查；该命令只读 `data/cache/elo_baseline_*` 与 openfootball 缓存，不联网、不打印 secret：

```bash
python3 -m worldcup.elo_local --check
```

`worldcup.scheduled_publish --live` 发布成功后会复用研究台账的“本轮变化”规则：比较刷新前后的本地 snapshot，只有等级、EV、Edge、模型概率、市场概率或赔率超过展示阈值时，才调用 `/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind` 发送手机通知。通知结果只记录发送状态、摘要和条数，不记录 WxPusher UID、URL、token 或原始响应；临时禁用可加 `--no-notify`。

当 openfootball 缓存里已有完赛比分时，snapshot 会给对应比赛附加 `result`，研究信号台账会在“信号原因”栏显示赛后验证：胜平负 / 大小球显示“命中”或“未中”，亚洲让球显示“命中 / 未中 / 走水”。

最新 refresh 富化后的 snapshot 还会包含顶层 `finished` 块：用开球前最后一轮 closing snapshot 的信号与本地赛果定格完赛场，`tally` 只统计 S/A 级信号；走水计入 `push`，但不进入命中率分母。新定格记录的 `closing_signals` 会以 `diagnostic_schema_version=2` 冻结 reason、raw grade、EV/Edge、概率族差异、盘口移动质量和 diagnostic flags，供后续本地复盘诊断使用；旧记录缺少这些字段时继续兼容。页面会新增“本届信号战绩”卡和“已完赛战绩”区，完赛区按北京日期分组，展开明细展示 closing 盘口、赛果判定和 SVG 赔率走势。每场最新 match 也可带 `odds_trend` 字段，供主台账展开详情展示迷你折线和首末点文本；同时可带 `odds_movement`，记录 1X2、AH 主盘、OU 主线的首末赔率、相对移动、盘口线移动和质量标记，暂只供研究诊断；`model.lineup_shadow` 可记录首发确认后的球员影响调整、post-information odds 是否可用、调整前后 1X2/OU 概率和 edge 对比，当前 `activation=shadow_only`；`model.ou_total_shadow` 可记录 active market-total OU 与 independent/raw OU total 的概率和 edge 对比，当前 `activation=shadow_only`，不解除 `market_informed_total` 降级；信号字典可带 `ah_validation_shadow`，记录 AH fair-line delta、盘口报价一致性和候选验证结果。若 AH raw S/A 被 `ah_market_edge_missing` soft cap 压到 B，且 `candidate_grade` 存在、AH shadow 验证通过、`movement_shadow.supports_signal=true`、没有硬质量 veto，并且首发确认后已有 post-information odds，`attach_trends()` 会把该 AH candidate 激活为正式 `grade=raw_grade`，写入 `promotion` 审计块并移除候选字段；OU candidate 仍不激活为正式等级。

赛后链路已由 LaunchAgent `xin.celab.football.daily-eval` 每天北京时间 16:30 自动执行 `python3 -m worldcup.daily_eval --notify --live-scores`：先调用 The Odds API scores 端点补抓赛果（每天约 2 credits，同 key 槽位轮换），再依次 `results_capture` → `eval_data` → `backtest` 并推送研究日报（完赛数、评估样本、模型 vs 市场指标、S/A 级信号命中统计）；无新增赛果不推送。手动补跑同一命令即可，幂等。

比赛日之后跑：

```bash
# 1) 从已缓存的 openfootball 数据提取完赛比分（幂等，可重复跑）
python3 -m worldcup.results_capture

# 1a) openfootball 录入滞后时，用 The Odds API scores 手动补抓赛果（约 2 credits）
python3 -m worldcup.scores_capture --live

# 2) 用"开球前最后一份"归档 snapshot 的赔率 join 赛果，生成带赔率的回测 CSV
python3 -m worldcup.eval_data

# 3) 用现有回测评估真实表现（EV 分层、model_matched vs market 此时有意义）
python3 -m worldcup.backtest --csv data/local/backtest/wc2026_eval.csv --min-sample 30 --out data/local/backtest/wc2026_report.json

# 4) 生成本地完赛 S/A 信号诊断报告（只读，不调参、不联网）
python3 -m worldcup.postmatch_diagnostics

# 5) 回填历史 closing snapshot 的 shadow 诊断（只读，不调参、不联网）
python3 -m worldcup.shadow_backfill_diagnostics

# 6) 审计官方首发是否进入 snapshot / post-information odds 链路（只读，不联网）
python3 -m worldcup.lineup_audit

# 6a) 对开赛前仍存在的首发链路缺口发一次性通知（不联网抓数据、不刷新 odds）
python3 -m worldcup.lineup_audit --notify
```

- 每次 live refresh 成功获取新赔率后，原始逐家报价会 gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`（兜底缓存轮不归档），用于赛后赔率异动研究；该目录不进 git。
- `worldcup.postmatch_diagnostics` 只读本地 snapshot/history/finished 数据，输出 `data/local/diagnostics/postmatch_diagnostics.json`，用于按市场、等级、原因、盘口移动和概率族差异解释 S/A 信号命中/未中；样本不足时只能作为观察，不能据此调参。
- `worldcup.shadow_backfill_diagnostics` 只读本地 snapshot/history/finished 数据，输出 `data/local/diagnostics/shadow_backfill_diagnostics.json`，用于给历史 closing 信号回算 `ah_validation_shadow` 和 `movement_shadow` 并按赛果分桶；该报告不改模型、不改信号等级、不回填线上数据，样本较少时只能作为观察。
- `worldcup.lineup_audit` 只读本地 `lineups_wc2026.json`、最新 snapshot、history snapshot 和缺首发通知状态，输出 `data/local/diagnostics/lineup_audit.json`，用于确认官方首发是否在开赛前抓到、是否进入 snapshot、是否已有 post-information odds；该报告不联网、不刷新赔率、不发布线上数据。显式 `--notify` 时，只对开赛前仍存在的 `captured_without_snapshot_input` / `captured_without_post_information_odds` 发一次性 WxPusher 通知，去重状态写入同一个被忽略的通知状态文件。

已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 采用 closing snapshot 的主盘线与均价（本改动合入前的老归档快照无 `ah_main`，对应 AH 列为空）；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。Elo 重放与页面赛果显示仍以 openfootball 为准，openfootball 录入滞后期间页面“预测结果”可能晚于日报。淘汰赛（6-28 起）scores 可能含加时/点球比分，与 1X2 的 90 分钟结算口径冲突，6-27 前必须回评是否暂停 scores 自动入库或改人工核对。

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
2. 公网开放 `/`、`/preview`、`/api/matches`、`/healthz`、`/api/ingest/snapshot`；`/api/snapshot/latest` 返回 404。
3. 本机 `launchd` 已启用 `xin.celab.football.scheduled-publish`，每 15 分钟唤醒一次；真正刷新/发布仍由 scheduler due 判断控制。
4. 本机 `launchd` 已启用 `xin.celab.football.pre-match`，每 300 秒运行 lineups-only 赛前首发轮询和首发链路审计通知；不带 `--live-refresh`，不会自动刷新 odds。
5. 下一步观察首轮 due 后的刷新、线上 ingest、Nginx/systemd 日志、certbot 自动续期和赛前首发轮询日志。
6. RDS/PostgreSQL 暂不需要；等多用户、备份或查询压力变大再升级。

## 重要约束

- API key、RDS 连接串、HMAC 密钥、Cookie、token 不得写入 git、文档或回复。
- macmini 不直连 RDS/OSS，后续只调用 ECS ingest API。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- Elo 来源为本地基线重放：`data/cache/elo_baseline_*.tsv` + openfootball 完赛比分按 eloratings 公式（K=60、中立场）增量重放生成 `elo_world.tsv`；eloratings 抓取仅用于重新锚定基线，抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不降级信号。重放计算失败时回退沿用现有 `elo_world.tsv` 并记 `elo_local` 错误。实现见 `worldcup/elo_local.py`。
- The Odds API 按免费额度使用：常规每天 1 次，每场保留 T-12小时 / T-6小时 / T-90 / T-55 / T-35 / T-25 临赛锚点；低额度（≤30）只保 T-90 / T-55 / T-35 / T-25。调度会按本地 quota ledger 保守轮换 `THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY`：primary 未耗尽时优先用 primary，primary 剩余额度为 0 时切到 secondary；两个配置槽都耗尽时继续暂停并报告 `quota_exhausted`。任一槽位剩余额度跌破 100 / 30 / 10 / 0 时会随当轮发布自动发 WxPusher 额度告警（每个槽位每个阈值只发一次，跨 0 即槽位耗尽/自动切换提示；`--no-notify` 可静音）。更换或新增 key 后，需经确认执行一次 `worldcup.scheduled_publish --live --force` 让新额度写回 quota ledger（耗尽状态下调度不会自行恢复）。
- FIFA public API 首发抓取不消耗 The Odds API quota，也不需要 key；它是公开源，不是付费 SLA 数据源。`worldcup.lineups_refresh` 抓不到官方首发时只能记录 missing 或发缺失通知，不能伪造 confirmed；默认只在开赛前 35 分钟内发缺首发通知，避免过早提醒。`--write` 合并保留旧 confirmed cache，避免未公布轮询清空已确认首发。FIFA `source_match_no` 与 openfootball/本地 snapshot 编号可能不是同一套编号，首发绑定不得只依赖编号，必须通过双方 canonical team + UTC kickoff 校验。`worldcup.pre_match_runner` 只有在 `newly_confirmed > 0` 时才会允许触发首发后 odds refresh；`--refresh-guard` 会先 dry-run 检查调度决策和 quota，`--live-refresh` 会消耗 The Odds API quota，当前已安装的 `xin.celab.football.pre-match` 不包含该参数。如需改为自动首发后 odds refresh，必须单独确认后更新 plist 并重新加载 launchd。
- ingest 必须绑定 `timestamp`、`run_id`、`snapshot_id` 和 body hash 做 HMAC；dry-run 不发送请求，也不能打印 secret。
- ingest server 默认防重放窗口为 300 秒；服务端必须用 `X-Worldcup-Idempotency-Key` 做幂等。
- `/healthz` 只能报告服务存活，不得输出环境变量、密钥、quota 或 snapshot 内容。
- 本地预览页必须保留研究免责声明，不显示资金相关字段。
- readiness check 只报告变量名、文件状态和内容完整性，不能输出密钥值；`.env.example` 必须只含变量名和空值。
- 所有公开输出都必须保留免责声明。
- 当前 EV/Edge 阈值未经历史赔率回测验证，公开页只能显示研究价值信号。
- S/A 强信号有只降级置信度护栏，不改模型概率、不升级 B/C：`1X2` 主/客方向若逆 closing 市场主方向、缺少主亚盘同向支持，或主办国本土场地市场确认不足，会封顶到 B；主办国 AH 强信号若主亚盘让步确认不足，也会封顶到 B；极强热门/大让步场景下，受让方 AH 强信号和低大小球线 Under 强信号会封顶到 B；AH 0 盘强信号也会封顶到 B。阈值见 `config/settings.yaml` 的 `quality.*` 护栏配置。未激活的 `candidate_grade` 仍只是候选池标记，不计入排序强弱口径、finished tally 或日报 S/A 战绩；只有 AH candidate 通过 AH shadow、movement shadow、硬质量 veto、已确认首发和 post-information odds 门槛后，才会由 `promotion` 审计块激活为正式 `grade=raw_grade`；`lineup_shadow` 是首发/球员影响诊断，不改变 active 概率；`ou_total_shadow` 是 independent/raw OU total 诊断，不改变正式 OU 等级。
