# Recent Work

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

## 2026-06-08

- 初始化 git 仓库，提交 `d52ba6c feat: initialize worldcup analysis engine`。
- 完成 Plan 1 引擎核心第一版：Elo、Poisson、赔率去水聚合、亚洲让球 EV、价值分级、变化检测。
- 修正 Plan 0 / Plan 1 中的关键问题：AH 不能走二元 EV、补 `D/ODDS_PENDING` 状态、补完整 market 聚合、修 API-Football 探测决策门。
- 本地验证通过：`41/41 tests passed`。
- 新增项目入口文档：`README.md`、`AGENTS.md`、`CLAUDE.md`、`RECENT_WORK.md`。

## 下一步

- 注册 API-Football key。
- 至少注册一个赔率备源 key。
- 执行 `docs/superpowers/plans/2026-06-08-plan0-data-source-probe.md`。
- 产出 `docs/superpowers/data-contract.md`。
