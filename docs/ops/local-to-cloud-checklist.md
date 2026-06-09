# Local to Cloud Checklist

本清单只描述上线步骤和验证点，不执行部署、不写云资源。

## 当前本地状态

- 数据源：openfootball 赛程、eloratings Elo、The Odds API 小组赛赔率。
- 本地缓存：`data/cache/analysis_snapshot.json`，当前含 72 场已确定对阵分析。
- 本地接口契约：`POST /api/ingest/snapshot`、`GET /api/snapshot/latest`、`GET /api/matches`、`GET /preview`、`GET /healthz`。
- 本地预览包：`data/cache/site/`，该目录被 git ignore。
- readiness：除 `INGEST_HMAC_SECRET` 需要人工写入 `.env` 外，其余本地检查已可通过。
- `.env.example`：只含变量名和空值，可提交；真实 `.env` 不提交。

## 上线前人工配置

1. 运行 `python3 -m worldcup.secrets` 生成 `INGEST_HMAC_SECRET=<value>`。
2. 只把 value 写入本地 `.env` 或云端 secret manager，不写进文档、代码、提交信息或聊天。
3. 运行 `python3 -m worldcup.readiness --root .`，确认 readiness 全绿。

## 云端实现阶段

1. 用 FastAPI 包装 `worldcup.http_app` / `worldcup.asgi_app` 已验证的路由契约。
2. 用 PostgreSQL 替换 `SQLiteSnapshotStore`，保留 `idempotency_key` 唯一约束。
3. ECS 只开放必要 API；macmini 只调用 ECS ingest，不直连 RDS/OSS。
4. `/healthz` 只用于健康检查，不输出环境变量、quota、snapshot 或 secret。

## Local FastAPI Smoke

1. Start local FastAPI only after `.env` readiness is green.
2. Check `GET /healthz`.
3. Check `GET /api/matches` contains no stake or bet amount fields.
4. Check `GET /preview` contains the research disclaimer.
5. Stop the local process before deploying or changing cloud resources.

## 调度阶段

1. macmini cron / launchd 只调用 `worldcup.scheduled_refresh --live`。
2. The Odds API 免费额度按 scheduler 降频策略执行。
3. 失败但缓存可用时允许 stale fallback，但必须在 snapshot `data_quality` 中标记。

## 部署验证

1. API smoke test：`GET /healthz` 返回 `status=ok`。
2. ingest dry-run：本地生成 HMAC header，先对测试环境验签。
3. read API：`GET /api/matches` 不包含 stake、bet amount 或下注金额字段。
4. 公开页面：保留研究免责声明。
5. 日志：不得出现 API key、HMAC secret、RDS 连接串、Cookie 或 token。
