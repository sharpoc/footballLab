# Local to Cloud Checklist

本清单只描述上线步骤和验证点，不执行部署、不写云资源。

## Plan 5 Gate Model

| Gate | 含义 | 允许动作 | 禁止动作 |
|---|---|---|---|
| Gate A: local dry-run | 本地证明测试、静态导出、readiness、secret redaction、PostgreSQL smoke guard 都可控 | 本地测试、被忽略目录输出、文档更新 | 部署、联网写入、连接 RDS、push |
| Gate B: test environment smoke | 明确确认后，对测试 ECS/RDS 做最小真实 smoke | 测试 ECS/RDS、signed ingest smoke、测试域名 | 生产 DNS 切换、生产写入 |
| Gate C: production cutover | 明确确认后，把公开流量切到生产环境 | 生产 ECS/RDS/域名/HTTPS、监控和回滚 | 未确认的自动发布或高频刷新 |

当前 Plan 5 只完成 Gate A。Gate B / Gate C 必须单独确认。

## 当前本地状态

- 数据源：openfootball 赛程、eloratings Elo、The Odds API 小组赛赔率。
- 本地缓存：`data/cache/analysis_snapshot.json`，当前含 72 场已确定对阵分析。
- 本地接口契约：`POST /api/ingest/snapshot`、`GET /api/snapshot/latest`、`GET /api/matches`、`GET /preview`、`GET /healthz`。
- 持久化边界：默认 SQLite，本地已提供 `PostgresSnapshotStore` adapter；当前只用 fake connection 测试，未连接真实 RDS。
- PostgreSQL smoke guard：`worldcup.postgres_smoke` 只做 dry-run，验证 `WORLDCUP_STORE=postgres`、`DATABASE_URL`、`INGEST_HMAC_SECRET` 和本地 snapshot，并只输出脱敏请求摘要。
- 本地预览包：`data/cache/site/`，该目录被 git ignore。
- readiness：本地或云端环境必须配置 `THE_ODDS_API_KEY` 和 `INGEST_HMAC_SECRET`；选择 PostgreSQL 时还必须配置 `DATABASE_URL`；检查过程不得输出真实值。
- `.env.example`：只含变量名和空值，可提交；真实 `.env` 不提交。

## 上线前人工配置

1. 运行 `python3 -m worldcup.secrets` 生成 `INGEST_HMAC_SECRET=<value>`。
2. 测试 PostgreSQL 时设置 `WORLDCUP_STORE=postgres`，并只把 `DATABASE_URL` 写入本地 `.env` 或云端 secret manager。
3. 不把 secret、DSN、密码写进文档、代码、提交信息或聊天。
4. 运行 `python3 -m worldcup.readiness --root .`，确认 readiness 全绿。
5. 真实 PostgreSQL smoke 前先运行 `python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://test.example.invalid/api/ingest/snapshot`，确认返回 `dry_run_ready` 且输出不含敏感值。

## ECS Dry-Run Checklist

1. 确认 Alibaba Cloud region。
2. 确认 ECS instance type、OS image、disk size 和 security group。
3. 确认运行方式：`systemd` service 或 container runtime。
4. 确认 app 启动命令示例：

```bash
python3 -m worldcup.fastapi_app --host 0.0.0.0 --port 8788 --env /etc/worldcup/.env --store postgres
```

5. 确认 `/healthz` 通过反向代理或负载均衡暴露。
6. 只公开必要端口，通常是 `80` / `443`；app port 留在内网。
7. 日志不得包含 `.env`、`DATABASE_URL`、API key、HMAC secret、`X-Worldcup-Signature` 或请求 body。
8. 确认部署 artifact 来源：Git checkout、release tarball 或 container image。
9. 部署前记录 Git commit SHA。

## RDS/PostgreSQL Dry-Run Checklist

1. 确认测试 smoke 使用 RDS 还是一次性 PostgreSQL test DB。
2. 确认 `WORLDCUP_STORE=postgres`。
3. 确认 `DATABASE_URL` 只存在于 `.env` 或云端 secret manager。
4. 使用 least-privilege app user，不用 root/admin。
5. ECS 可以访问 RDS；macmini 不直连 RDS。
6. schema 初始化由 `PostgresSnapshotStore` 执行。
7. `idempotency_key` 唯一约束保留。
8. 同一 signed payload 两次写入应返回 `stored` 后 `duplicate`。
9. 生产写入前确认 RDS backup/snapshot 策略。

## Domain And HTTPS Dry-Run Checklist

1. 确认域名和 ICP 备案状态。
2. 确认 DNS provider 和 record type。
3. 先使用测试域名，例如 `test.example.invalid`。
4. 确认 TLS 证书来源：Alibaba Cloud certificate、Let's Encrypt 或已有证书。
5. 确认 HTTP 到 HTTPS redirect。
6. 如果静态 Research Ledger 不由 FastAPI 直接服务，确认 CDN/OSS/static hosting 方案。
7. 比赛窗口期 HTML 和 JSON 缓存 TTL 应保持较短。

## Secret Dry-Run Checklist

必需变量名：

```text
THE_ODDS_API_KEY
INGEST_HMAC_SECRET
WORLDCUP_STORE
DATABASE_URL
```

1. `.env.example` 只保留变量名和空值。
2. 真实 `.env` 必须保持 git ignored。
3. `INGEST_HMAC_SECRET` 只在 payload producer 和 ECS ingest server 之间共享。
4. `THE_ODDS_API_KEY` 按免费额度低频使用。
5. `DATABASE_URL` 不得进入日志、提交、文档、截图、shell history 片段或聊天。
6. dry-run 输出不得包含请求 body 或 `X-Worldcup-Signature`。

## Rollback Dry-Run Checklist

1. 记录 last known good Git SHA。
2. 保留上一版 ECS service artifact 或 container image。
3. 保留上一版 `.env` / secret manager version。
4. 生产写入前保留数据库 backup/snapshot。
5. 优先回滚 app；只有 app 回滚失败时才回滚 DNS。
6. 如果写入了坏数据，先停 scheduled refresh，再 replay 或 restore。
7. 回滚验证：

```bash
curl -fsS https://test.example.invalid/healthz
curl -fsS https://test.example.invalid/api/matches
```

期望：health 返回 `status=ok`；matches 不含 stake、bet amount、bankroll、payout、wager、unit、下注金额、投注金额、本金、重注、追损、串关或喊单字段。

## Plan 5 Local Dry-Run Commands

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.export --snapshot data/cache/analysis_snapshot.json --out-dir data/cache/site
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

PostgreSQL smoke guard 的本地期望：

- 如果 `.env` 仍是 SQLite/local 模式，返回 `status=blocked` 和 `message=expected_postgres` 是安全结果。
- 如果 `.env` 已明确切到测试 PostgreSQL，返回 `status=dry_run_ready`，且只包含脱敏请求摘要。

上述两种结果都不连接 RDS、不发送 HTTP。

## 云端实现阶段

1. 复用已完成的本地 FastAPI adapter，接入 ECS 运行配置和正式 secret 管理。
2. 用 `WORLDCUP_STORE=postgres` + `DATABASE_URL` 选择 `PostgresSnapshotStore` 接入 RDS，保留 `idempotency_key` 唯一约束。
3. ECS 只开放必要 API；macmini 只调用 ECS ingest，不直连 RDS/OSS。
4. `/healthz` 只用于健康检查，不输出环境变量、quota、snapshot 或 secret。

## Local FastAPI Smoke

1. Start local FastAPI only after `.env` readiness is green.
2. Check `GET /healthz`.
3. Check `GET /api/matches` contains no stake or bet amount fields.
4. Check `POST /api/ingest/snapshot` with a signed local payload returns `stored` or `duplicate`.
5. Check `GET /preview` contains the research disclaimer.
6. Stop the local process before deploying or changing cloud resources.

## 调度阶段

1. macmini cron / launchd 只调用 `worldcup.scheduled_refresh --live`。
2. The Odds API 免费额度按 scheduler 降频策略执行。
3. 失败但缓存可用时允许 stale fallback，但必须在 snapshot `data_quality` 中标记。

## 部署验证

1. API smoke test：`GET /healthz` 返回 `status=ok`。
2. PostgreSQL smoke dry-run guard：本地返回 `dry_run_ready`，只展示脱敏 `method`、`url`、`path`、`header_names`、`run_id`、`snapshot_id`、`body_sha256`、`idempotency_key`、`body_bytes` 和 `expected_sequence`。
3. PostgreSQL smoke test：测试环境以 `--store postgres` 或 `WORLDCUP_STORE=postgres` 写入同一 signed payload 两次，应返回 `stored` 后 `duplicate`。
4. ingest dry-run：本地生成 HMAC header，先对测试环境验签。
5. read API：`GET /api/matches` 不包含 stake、bet amount 或下注金额字段。
6. 公开页面：保留研究免责声明。
7. 日志：不得出现 API key、HMAC secret、RDS 连接串、`X-Worldcup-Signature`、请求 body、Cookie 或 token。
