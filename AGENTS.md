# 项目协作说明

本文件是 Codex 在本项目内的本地说明入口。Claude Code 对应读取 `CLAUDE.md`；两份文件应保持同步。

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
- 下一步是 Plan 0 数据源探测。
- Plan 2 collectors 必须等待 Plan 0 的真实字段契约，不能按假接口写。
- Plan 3 云端与调度等阿里云资源确认后再细化。

## 开发规则

- 优先最小可行实现，不提前上 ML。
- 本届 MVP 只做 Elo + Poisson + 赔率去水 + EV/Edge + 等级状态。
- 引擎层必须保持纯函数，不联网、不连数据库、不依赖云。
- 采集层使用保存的样例响应做离线解析测试。
- 云端 ingest 必须使用 HMAC + timestamp + run_id/snapshot_id，并做幂等与防重放。

## 文件与安全

- 不要提交 `.env`、API key、token、Cookie、RDS 连接串、HMAC secret。
- `.env` 只放本地真实密钥。
- `.env.example` 只允许放变量名。
- `data/raw/`、`data/probe/`、缓存和 `.DS_Store` 不进 git。

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

1. 注册 API-Football key。
2. 至少注册一个赔率备源 key。
3. 执行 Plan 0。
4. 生成 `docs/superpowers/data-contract.md`。
5. 再写 collectors。
