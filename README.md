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
- 本地测试执行器通过：`240/240 tests passed`。
- Plan 0 核心数据源探测已完成第一轮：openfootball 赛程、eloratings Elo、The Odds API 赔率可用；API-Football Free plan 不能访问 2026 season。
- Plan 2 已启动：当前完成纯离线解析层、单场价值信号、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、按每场比赛独立计算的刷新计划、run metadata、调度执行包装、显著变化手机通知、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照，本地 runner 生成的快照也包含 ingest 所需 run metadata。
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
- 当前 scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策，不会联网或写入状态；全局 due 由所有比赛 `refresh_plan.next_update_at` 的最早值决定
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
- 当前静态预览/导出页为研究台账 UI：只展示研究信号、每场下次更新时间、方法说明、脱敏数据质量状态和免责声明，不显示下注金额或资金相关字段
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
  elo_replay.py                 # 国际比赛历史 Elo replay 与官方榜对照
  backtest_data.py              # 国际比赛历史结果转换为回测 CSV
  backtest.py                   # 离线回测、指标报告与参数扫描
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

FastAPI app 在明确确认 ECS 部署前只作为本地适配层使用。

PostgreSQL smoke dry-run guard 仅在明确选择 PostgreSQL/RDS 时先跑；SQLite 首发时返回 `blocked / expected_postgres` 是安全结果：

```bash
python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

该命令只验证前置条件并输出脱敏请求摘要，不连接数据库、不发送 HTTP。

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

另外：OU 大小球模型的逐场 `mu_total` 现在由「OU 市场去水概率反推的总进球」与配置先验 `poisson.mu_total` 按 `poisson.mu_market_weight` 混合得出；无 OU 市场时回退先验。snapshot 的 `model.mu_total` 字段记录实际使用值。

模型还内置 Dixon-Coles 低比分修正开关 `poisson.dc_rho`（默认 `0.0` 即关闭，行为与历史版本一致）；rho 的取值必须由真实历史数据回测确定后再启用。mu 市场锚定仅在 OU 盘口 over/under 双边报价家数均达到 `odds.min_books` 时生效，否则回退先验 `poisson.mu_total`。注意：`dc_rho != 0` 时比分矩阵的大小球概率与 mu 锚定的纯 Poisson 反推存在微小近似偏差，rho 为小负数时可忽略。

总进球先验支持随 Elo 差上升：`poisson.mu_dr_slope`（默认 `0.0` 关闭；clamp 见 `mu_prior_min/max` 代码默认 1.5/4.0）；拟合证据见 `docs/research/2026-06-10-mu-dr-fit.md`。

## 世界杯期间评估数据（自有赔率 + 赛果）

每次 live refresh 会把 snapshot 归档到被忽略的 `data/local/history/`（merge 进本机 main 后自动生效，无需部署服务端）。自动刷新链路可用只读验收命令检查最近归档和 LaunchAgent 指向：

```bash
python3 -m worldcup.refresh_audit
```

日常运维推荐使用一键只读检查命令；它会汇总本机 snapshot/history/quota/LaunchAgent、本机 scheduled-publish 日志、公网 `/healthz` / `/api/matches` / 页面更新时间、ECS 服务/SQLite/latest snapshot 和日志安全计数。该命令不触发 refresh、不发布、不读取或打印 secret。

```bash
python3 -m worldcup.ops_check
```

`worldcup.scheduled_publish --live` 发布成功后会复用研究台账的“本轮变化”规则：比较刷新前后的本地 snapshot，只有等级、EV、Edge、模型概率、市场概率或赔率超过展示阈值时，才调用 `/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind` 发送手机通知。通知结果只记录发送状态、摘要和条数，不记录 WxPusher UID、URL、token 或原始响应；临时禁用可加 `--no-notify`。

当 openfootball 缓存里已有完赛比分时，snapshot 会给对应比赛附加 `result`，研究信号台账会在“信号原因”栏显示赛后验证：胜平负 / 大小球显示“命中”或“未中”，亚洲让球显示“命中 / 未中 / 走水”。

比赛日之后跑：

```bash
# 1) 从已缓存的 openfootball 数据提取完赛比分（幂等，可重复跑）
python3 -m worldcup.results_capture

# 2) 用"开球前最后一份"归档 snapshot 的赔率 join 赛果，生成带赔率的回测 CSV
python3 -m worldcup.eval_data

# 3) 用现有回测评估真实表现（EV 分层、model_matched vs market 此时有意义）
python3 -m worldcup.backtest --csv data/local/backtest/wc2026_eval.csv --min-sample 30 --out data/local/backtest/wc2026_report.json
```

- 每次 live refresh 成功获取新赔率后，原始逐家报价会 gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`（兜底缓存轮不归档），用于赛后赔率异动研究；该目录不进 git。

已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 采用 closing snapshot 的主盘线与均价（本改动合入前的老归档快照无 `ah_main`，对应 AH 列为空）；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。

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

1. Gate C HTTPS 已完成：`https://football.celab.xin/` 对外展示研究台账。
2. 公网开放 `/`、`/preview`、`/api/matches`、`/healthz`、`/api/ingest/snapshot`；`/api/snapshot/latest` 返回 404。
3. 本机 `launchd` 已启用 `xin.celab.football.scheduled-publish`，每 15 分钟唤醒一次；真正刷新/发布仍由 scheduler due 判断控制。
4. 下一步观察首轮 due 后的刷新、线上 ingest、Nginx/systemd 日志和 certbot 自动续期。
5. RDS/PostgreSQL 暂不需要；等多用户、备份或查询压力变大再升级。

## 重要约束

- API key、RDS 连接串、HMAC 密钥、Cookie、token 不得写入 git、文档或回复。
- macmini 不直连 RDS/OSS，后续只调用 ECS ingest API。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- 例外：eloratings 抓取失败但本地 Elo 缓存 mtime 在 48 小时宽限期内时，只记 `data_quality.source_errors`，不标 `stale_sources`、不触发信号降级（Elo 仅在完赛后变化，宽限期内缓存与真实值一致）；超过宽限期仍按上一条降级。常量为 `worldcup/refresh_runner.py` 的 `ELO_CACHE_GRACE_SECONDS`。
- The Odds API 按免费额度使用：常规每天 1 次，每场保留 T-12小时 / T-6小时 / T-90 / T-55 / T-25 临赛锚点；低额度（≤30）只保 T-90 / T-55 / T-25。额度耗尽后更换 `.env` 的 `THE_ODDS_API_KEY`，再经确认执行一次 `worldcup.scheduled_publish --live --force` 让新额度写回 quota ledger（耗尽状态下调度不会自行恢复）。
- ingest 必须绑定 `timestamp`、`run_id`、`snapshot_id` 和 body hash 做 HMAC；dry-run 不发送请求，也不能打印 secret。
- ingest server 默认防重放窗口为 300 秒；服务端必须用 `X-Worldcup-Idempotency-Key` 做幂等。
- `/healthz` 只能报告服务存活，不得输出环境变量、密钥、quota 或 snapshot 内容。
- 本地预览页必须保留研究免责声明，不显示资金相关字段。
- readiness check 只报告变量名、文件状态和内容完整性，不能输出密钥值；`.env.example` 必须只含变量名和空值。
- 所有公开输出都必须保留免责声明。
- 当前 EV/Edge 阈值未经历史赔率回测验证，公开页只能显示研究价值信号。
