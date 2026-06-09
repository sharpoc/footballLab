# 项目协作说明

本文件是 Claude Code 在本项目内的本地说明入口。Codex 对应读取 `AGENTS.md`；两份文件应保持同步。

## 默认语言

- 默认使用简体中文沟通。
- 代码、命令、配置键名、字段名和报错信息保留英文原文。

## 项目定位

- 这是 2026 世界杯研究/分析站。
- 目标是做数据采集、量化分析、价值信号展示。
- 不构成投注建议。
- 不显示下注金额。
- 不做追损、重注、串关喊单或任何无风控建议。

## 当前阶段

- Plan 1 引擎核心已完成第一版。
- Plan 0 核心数据源探测已完成第一轮。
- Plan 2 已启动，当前完成纯离线解析层、单场模型/市场输出、价值信号输出、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、低频调度策略、run metadata、调度执行包装、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照。
- Plan 2 collectors 必须基于 `docs/superpowers/data-contract.md` 和 `data/probe/` 保存样例写离线解析测试，不能按假接口写。
- Plan 3 云端与调度等阿里云资源确认后再细化。

## 开发规则

- 优先最小可行实现，不提前上 ML。
- 本届 MVP 只做 Elo + Poisson + 赔率去水 + EV/Edge + 等级状态。
- 引擎层必须保持纯函数，不联网、不连数据库、不依赖云。
- 采集层使用保存的样例响应做离线解析测试。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策；The Odds API 按免费额度使用，低额度时必须降频。
- scheduled refresh 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会调用 refresh runner。
- ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求，不能打印 HMAC secret。
- 云端 ingest 必须使用 HMAC + timestamp + run_id/snapshot_id，并做幂等与防重放；当前默认防重放窗口为 300 秒。
- 本地 SQLite / preview 输出必须写入被忽略的 `data/local/` 或 `data/cache/`；预览页必须保留研究免责声明，不显示资金相关字段。
- HTTP / ASGI 适配层只用于本地预览、路由契约测试和后续 FastAPI 包装参考；正式依赖安装、ECS 部署、上线和云端写入必须单独确认。
- `/healthz` 只能报告服务存活，不读 DB、不依赖 secret，不输出环境变量、密钥、quota 或 snapshot 内容。
- 静态站点导出默认写入被忽略的 `data/cache/site/`，只作为本地预览/上线包草案，不代表已部署。
- readiness check 只读本地文件和变量名，不联网、不打印 secret；缺少真实 `INGEST_HMAC_SECRET` 时应报错，不要自动生成并写入 `.env`；`.env.example` 必须只含变量名和空值。
- HMAC secret helper 只允许打印新 secret 给本地人工写入 `.env`，不得自动改 `.env` 或把 secret 写入文档。

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

## 近期重点

1. The Odds API key 已在聊天截图暴露过；用户已确认不充值，后续按免费额度和缓存兜底设计。
2. 用 `python3 -m worldcup.secrets` 生成 `INGEST_HMAC_SECRET`，手动写入 `.env` 后 readiness check 才能全绿。
3. 将 `worldcup.http_app` / `worldcup.asgi_app` 的路由契约迁移/包装为 FastAPI，实现正式 ECS ingest/read API。
4. 把 SQLite store 替换/适配为 PostgreSQL 持久化，保留幂等唯一键。
5. 保持 collector 解析测试使用 `data/probe/` 保存样例，不联网。
6. 后续再把 scheduled refresh 接到 macmini cron / launchd。
