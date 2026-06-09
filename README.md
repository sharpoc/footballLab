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
- 本地测试执行器通过：`156/156 tests passed`。
- Plan 0 核心数据源探测已完成第一轮：openfootball 赛程、eloratings Elo、The Odds API 赔率可用；API-Football Free plan 不能访问 2026 season。
- Plan 2 已启动：当前完成纯离线解析层、单场价值信号、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、低频调度策略、run metadata、调度执行包装、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照，本地 runner 生成的快照也包含 ingest 所需 run metadata。
- Plan 3A FastAPI local adapter is implemented and tested.
- Plan 3B PostgreSQL store adapter is implemented behind `SnapshotStore`; tests use fake connections only, with no real database connection.
- Plan 3C store selection wiring is implemented: local CLI defaults to SQLite and can explicitly select PostgreSQL with `WORLDCUP_STORE=postgres` plus `DATABASE_URL`, but no real database connection was made.
- Plan 3D PostgreSQL smoke dry-run guard is implemented: it validates PostgreSQL smoke prerequisites and emits redacted request metadata only, without HTTP or database connections.
- Plan 4 Research Ledger UI is implemented as a local static/exportable research page over the existing snapshot data; desktop/mobile browser QA passed, and no deployment, push, live API call, or online write was performed.

## 技术栈

- Python 3
- 标准库优先
- 当前引擎不联网、不连数据库、不依赖云资源
- 当前 collector 解析层不联网；后续真实请求层可再引入 HTTP 客户端
- 当前 refresh runner 默认 dry-run；只有显式 `--live` 才会读取 `.env` 并联网消耗 The Odds API 额度
- 当前 scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策，不会联网或写入状态
- 当前 scheduled refresh 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会调用 refresh runner
- 当前 ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求
- 当前 ingest server 是纯本地验签/幂等模块；FastAPI adapter 已复用它，ECS 部署另行确认
- 当前 SQLite store / preview 都是本地低风险链路；默认输出在已忽略的 `data/local/` 或 `data/cache/`
- 当前 PostgreSQL store adapter 可用于后续 ECS/RDS 接入；`psycopg` 只作为可选依赖声明，本轮未安装、未连接真实数据库
- 当前 store selection 默认 `sqlite`；只有显式 `--store postgres` 或 `.env` 中 `WORLDCUP_STORE=postgres` 时才要求 `DATABASE_URL`
- 当前 PostgreSQL smoke guard 默认只做 dry-run，要求 `WORLDCUP_STORE=postgres`、`DATABASE_URL` 和 `INGEST_HMAC_SECRET`，且不打印 DSN、secret、签名或请求 body
- 当前 HTTP 适配层只用于本地预览和路由契约测试；正式 FastAPI/ECS 部署需单独确认
- 当前 ASGI 适配层无外部依赖，只包装本地 HTTP 路由契约；正式 ASGI server / ECS 部署需单独确认
- 当前 FastAPI app 只作为本地适配层，复用既有路由契约；ECS 部署明确确认前保持 local-only
- 当前 `/healthz` 不读 DB、不依赖 secret，只用于本地和后续云端健康检查契约
- 当前静态导出默认写入已忽略的 `data/cache/site/`
- 当前静态预览/导出页为 Research Ledger UI：只展示研究信号、方法说明、脱敏数据质量状态和免责声明，不显示下注金额或资金相关字段
- 当前 readiness check 只读本地文件和变量名，会解析 snapshot/quota、检查预览免责声明，并确认 `.env.example` 只含空值模板，不联网、不打印 secret
- 当前 HMAC secret helper 只打印 `INGEST_HMAC_SECRET=<value>`，不会写 `.env`
- 后续云端计划使用 FastAPI + PostgreSQL + OSS

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
  differ.py                     # 两轮变化检测
  pipeline.py                   # collector 输出对齐 + 单场分析编排
  local_runner.py               # 本地样例/缓存 → 分析快照 JSON
  refresh_runner.py             # source refresh → cache → analysis snapshot
  scheduler.py                  # 免费额度调度策略与 run metadata
  scheduled_refresh.py          # 调度判断 → 条件执行 refresh
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
  ledger.py                     # Research Ledger UI 安全投影与格式化
  ledger_html.py                # Research Ledger HTML/CSS/vanilla JS renderer
  preview.py                    # 静态 HTML 预览页入口，委托 Research Ledger renderer
  http_app.py                   # 标准库 HTTP 适配层和路由契约
  asgi_app.py                   # 无依赖 ASGI 适配层
  export.py                     # 静态站点/API 导出
  readiness.py                  # 本地上线前 readiness check
  secrets.py                    # 本地 HMAC secret 生成助手，不写 .env
  quota.py                      # 本地 API quota ledger
  sources/
    openfootball.py             # openfootball 请求与缓存
    theoddsapi.py               # The Odds API 请求、缓存与 quota 记录
    eloratings.py               # Elo TSV 请求与缓存
  collectors/
    openfootball.py             # openfootball 赛程样例解析
    theoddsapi.py               # The Odds API 赔率样例解析
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

The FastAPI app is local-only until ECS deployment is explicitly confirmed.

PostgreSQL smoke dry-run guard 可在测试环境变量准备好后先跑：

```bash
python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

该命令只验证前置条件并输出脱敏请求摘要，不连接数据库、不发送 HTTP。

## API 注册清单

API-Football 与 The Odds API 已完成第一轮探测；其它赔率源可作为后续容灾或交叉校验候选。

| 用途 | 服务 | 注册 / 官网 |
|---|---|---|
| 主数据源：赛程、结果、赔率探测 | API-Football | https://www.api-football.com/ |
| 赔率备源 | The Odds API | https://the-odds-api.com/ |
| 赔率备源 | odds-api.io | https://odds-api.io/ |
| 赔率低频交叉校验 | OddsPapi | https://oddspapi.io/ |
| 免费赛程源 | openfootball/worldcup.json | https://github.com/openfootball/worldcup.json |
| Elo 数据源 | World Football Elo Ratings | https://www.eloratings.net/ |

拿到 key 后，本地创建 `.env`，不要提交：

```bash
API_FOOTBALL_KEY=...
THE_ODDS_API_KEY=...
ODDS_API_IO_KEY=...
ODDSPAPI_KEY=...
INGEST_HMAC_SECRET=...
WORLDCUP_STORE=
DATABASE_URL=
```

`.env` 已被 `.gitignore` 忽略。

## 下一步

1. 上线前确认 `.env` 或云端 secret manager 已配置 `INGEST_HMAC_SECRET`，并重新跑 readiness check。
2. 明确确认 ECS/RDS 后，在测试环境配置 `WORLDCUP_STORE=postgres` 与 `DATABASE_URL`。
3. 先运行 PostgreSQL smoke dry-run guard，确认输出 `dry_run_ready` 且无敏感值。
4. 在测试环境做真实 PostgreSQL smoke，再考虑部署生产。
5. 后续再把 scheduled refresh 接到 macmini cron / launchd。

## 重要约束

- API key、RDS 连接串、HMAC 密钥、Cookie、token 不得写入 git、文档或回复。
- macmini 不直连 RDS/OSS，后续只调用 ECS ingest API。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- The Odds API 按免费额度使用：低额度时 scheduler 会降频；上线前不得默认高频刷新。
- ingest 必须绑定 `timestamp`、`run_id`、`snapshot_id` 和 body hash 做 HMAC；dry-run 不发送请求，也不能打印 secret。
- ingest server 默认防重放窗口为 300 秒；服务端必须用 `X-Worldcup-Idempotency-Key` 做幂等。
- `/healthz` 只能报告服务存活，不得输出环境变量、密钥、quota 或 snapshot 内容。
- 本地预览页必须保留研究免责声明，不显示资金相关字段。
- readiness check 只报告变量名、文件状态和内容完整性，不能输出密钥值；`.env.example` 必须只含变量名和空值。
- 所有公开输出都必须保留免责声明。
- 当前模型未经历史回测验证，公开页只能显示研究价值信号。
