# Local to Cloud Checklist

本清单记录上线步骤、验证点和当前生产状态；后续生产变更仍需单独确认。

## Plan 5 Gate Model

| Gate | 含义 | 允许动作 | 禁止动作 |
|---|---|---|---|
| Gate A: local dry-run | 本地证明测试、静态导出、readiness、secret redaction、PostgreSQL smoke guard 都可控 | 本地测试、被忽略目录输出、文档更新 | 部署、联网写入、连接 RDS、push |
| Gate B: production-server smoke | 明确确认后，在唯一生产服务器上做受控 smoke，不开定时刷新 | 同一台服务器、SQLite 默认、临时端口或临时 host、signed ingest smoke | 正式域名切换、自动刷新、无回滚点写入 |
| Gate C: production activation | Gate B 通过后，明确确认再启用正式域名/HTTPS 和定时刷新 | 正式域名/HTTPS、macmini scheduled refresh、监控和回滚 | 未确认的自动发布或高频刷新 |

当前 Plan 5 已完成 Gate C：`football.celab.xin` 通过 Nginx/HTTPS 对外开放，后续 scheduled refresh 仍需单独确认。

## 当前本地状态

- 数据源：openfootball 赛程、eloratings Elo、The Odds API 小组赛赔率。
- 本地缓存：`data/cache/analysis_snapshot.json`，当前含 72 场已确定对阵分析。
- 本地接口契约：`POST /api/ingest/snapshot`、`GET /api/snapshot/latest`、`GET /api/matches`、`GET /preview`、`GET /healthz`。
- 持久化边界：默认 SQLite，适合单用户 MVP 首发；本地已提供 `PostgresSnapshotStore` adapter，但 RDS/PostgreSQL 不是首发必需项。
- PostgreSQL smoke guard：`worldcup.postgres_smoke` 只做 dry-run；当前 SQLite 首发路线下返回 `blocked / expected_postgres` 是安全结果。
- 本地预览包：`data/cache/site/`，该目录被 git ignore。
- readiness：本地或云端环境必须配置 `THE_ODDS_API_KEY` 和 `INGEST_HMAC_SECRET`；选择 PostgreSQL 时才必须配置 `DATABASE_URL`；检查过程不得输出真实值。
- `.env.example`：只含变量名和空值，可提交；真实 `.env` 不提交。

## 当前生产状态

- 域名：`https://football.celab.xin/`
- 服务器 app：`worldcup.service`，监听 `127.0.0.1:8788`。
- 公网入口：Nginx 反代 HTTPS 到本机 app。
- 存储：SQLite，路径 `/var/lib/worldcup/worldcup.db`。
- 公开路径：`/`、`/preview`、`/api/matches`、`/healthz`、`/api/ingest/snapshot`。
- 阻断路径：`/api/snapshot/latest` 返回 404，`/api/snapshot/` 前缀也阻断。
- 证书：Let's Encrypt，`football.celab.xin`，到期日 2026-09-07，certbot timer 已存在，续期 dry-run 已通过。
- Nginx 回滚备份：`/root/nginx-backups/20260609153432-football-gatec` 和 `/root/nginx-backups/20260609153716-football-https`。
- 已启用本机 LaunchAgent：`xin.celab.football.scheduled-publish`，每 900 秒唤醒一次，实际刷新/发布由 scheduler due 判断控制。
- 尚未启用：RDS/PostgreSQL、OSS/CDN。

## 上线前人工配置

1. 运行 `python3 -m worldcup.secrets` 生成 `INGEST_HMAC_SECRET=<value>`。
2. 单服务器 MVP 默认设置 `WORLDCUP_STORE=sqlite`，SQLite DB 放在持久化路径，例如 `/var/lib/worldcup/worldcup.db`。
3. 不把 secret、DSN、密码写进文档、代码、提交信息或聊天。
4. 运行 `python3 -m worldcup.readiness --root .`，确认 readiness 全绿。
5. 只有明确决定升级 PostgreSQL/RDS 时，才设置 `WORLDCUP_STORE=postgres` 和 `DATABASE_URL`，并先运行 `python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://preprod.example.invalid/api/ingest/snapshot`，确认返回 `dry_run_ready` 且输出不含敏感值。

## ECS Dry-Run Checklist

1. 确认 Alibaba Cloud region。
2. 确认 ECS instance type、OS image、disk size 和 security group。
3. 确认运行方式：`systemd` service 或 container runtime。
4. 确认 SQLite MVP app 启动命令示例：

```bash
python3 -m worldcup.fastapi_app --host 127.0.0.1 --port 8788 --env /etc/worldcup/.env --store sqlite --db /var/lib/worldcup/worldcup.db
```

5. 确认 `/healthz` 通过反向代理或负载均衡暴露。
6. 只公开必要端口，通常是 `80` / `443`；app port 留在内网。
7. 日志不得包含 `.env`、`DATABASE_URL`、API key、HMAC secret、`X-Worldcup-Signature` 或请求 body。
8. 确认部署 artifact 来源：Git checkout、release tarball 或 container image。
9. 部署前记录 Git commit SHA。

## Storage Dry-Run Checklist

1. SQLite 是单用户 MVP 默认首发存储。
2. 确认 SQLite DB 路径在持久化磁盘，例如 `/var/lib/worldcup/worldcup.db`。
3. 确认 app 用户只拥有 app 目录、日志目录、`.env` 和 SQLite DB 路径的最小权限。
4. `idempotency_key` 幂等语义必须保留。
5. 同一 signed payload 两次写入应返回 `stored` 后 `duplicate`。
6. 生产服务器 smoke 通过前不启用 macmini scheduled refresh。
7. 如果后续升级 PostgreSQL/RDS，再确认 `WORLDCUP_STORE=postgres`、`DATABASE_URL`、least-privilege app user、ECS 到 RDS 网络和 backup/snapshot 策略。

## Domain And HTTPS Dry-Run Checklist

1. 确认域名和 ICP 备案状态。
2. 确认 DNS provider 和 record type。
3. 当前域名为 `football.celab.xin`，DNS 已指向当前 ECS。
4. TLS 证书来源为 Let's Encrypt。
5. HTTP 已跳转 HTTPS。
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
5. SQLite 首发时 `WORLDCUP_STORE=sqlite`，`DATABASE_URL` 留空。
6. 如果后续使用 PostgreSQL/RDS，`DATABASE_URL` 不得进入日志、提交、文档、截图、shell history 片段或聊天。
7. dry-run 输出不得包含请求 body 或 `X-Worldcup-Signature`。

## Rollback Dry-Run Checklist

1. 记录 last known good Git SHA。
2. 保留上一版 ECS service artifact 或 container image。
3. 保留上一版 `.env` / secret manager version。
4. 生产写入前保留数据库 backup/snapshot。
5. 优先回滚 app；只有 app 回滚失败时才回滚 DNS。
6. 如果写入了坏数据，先停 scheduled refresh，再 replay 或 restore。
7. 回滚验证：

```bash
curl -fsS https://preprod.example.invalid/healthz
curl -fsS https://preprod.example.invalid/api/matches
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

- SQLite MVP 首发路线下，返回 `status=blocked` 和 `message=expected_postgres` 是安全结果。
- 如果 `.env` 已明确切到 PostgreSQL，返回 `status=dry_run_ready`，且只包含脱敏请求摘要。

上述两种结果都不连接 RDS、不发送 HTTP。

## 云端实现阶段

1. 复用已完成的本地 FastAPI adapter，接入 ECS 运行配置和正式 secret 管理。
2. 单用户 MVP 默认 `WORLDCUP_STORE=sqlite`，用持久化 SQLite 文件首发。
3. 若后续升级 RDS，再用 `WORLDCUP_STORE=postgres` + `DATABASE_URL` 选择 `PostgresSnapshotStore`，保留 `idempotency_key` 唯一约束。
4. ECS 只开放必要 API；macmini 只调用 ECS ingest，不直连 RDS/OSS。
5. `/healthz` 只用于健康检查，不输出环境变量、quota、snapshot 或 secret。

## Local FastAPI Smoke

1. Start local FastAPI only after `.env` readiness is green.
2. Check `GET /healthz`.
3. Check `GET /api/matches` contains no stake or bet amount fields.
4. Check `POST /api/ingest/snapshot` with a signed local payload returns `stored` or `duplicate`.
5. Check `GET /preview` contains the research disclaimer.
6. Stop the local process before deploying or changing cloud resources.

## 调度阶段

1. macmini cron / launchd 推荐调用 `worldcup.scheduled_publish --live --endpoint https://football.celab.xin/api/ingest/snapshot`。
2. The Odds API 免费额度按 scheduler 降频策略执行。
3. scheduled publish 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会刷新并发布。
4. 失败但缓存可用时允许 stale fallback，但必须在 snapshot `data_quality` 中标记。
5. 发布输出不得包含 request body、HMAC secret 或 `X-Worldcup-Signature`。
6. 当前 LaunchAgent plist：`~/Library/LaunchAgents/xin.celab.football.scheduled-publish.plist`。
7. 当前日志：`~/Library/Logs/worldcup/scheduled-publish.out.log` 和 `~/Library/Logs/worldcup/scheduled-publish.err.log`。
8. 停用命令：`launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/xin.celab.football.scheduled-publish.plist`。

## 部署验证

1. API smoke test：`GET /healthz` 返回 `status=ok`。
2. SQLite smoke test：生产服务器以 `--store sqlite` 写入同一 signed payload 两次，应返回 `stored` 后 `duplicate`。
3. PostgreSQL smoke dry-run guard：仅在明确选择 RDS 时使用；本地返回 `dry_run_ready` 时只展示脱敏 `method`、`url`、`path`、`header_names`、`run_id`、`snapshot_id`、`body_sha256`、`idempotency_key`、`body_bytes` 和 `expected_sequence`。
4. ingest dry-run：本地生成 HMAC header，先对生产服务器受控 smoke endpoint 验签。
5. read API：`GET /api/matches` 不包含 stake、bet amount 或下注金额字段。
6. 公开页面：保留研究免责声明。
7. 日志：不得出现 API key、HMAC secret、RDS 连接串、`X-Worldcup-Signature`、请求 body、Cookie 或 token。
