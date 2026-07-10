# 项目协作说明

本文件是 Codex 在本项目内的本地说明入口。Claude Code 对应读取 `CLAUDE.md`；两份文件应保持同步。

## 默认语言

- 默认使用简体中文沟通。
- 代码、命令、配置键名、字段名和报错信息保留英文原文。

## 项目定位

- 这是 2026 世界杯研究/分析站。
- 目标是做数据采集、量化分析和每场唯一“本场首选”展示；只要存在开赛前有效、可结算的主盘口，每场必须输出一个首选，不能靠删除低置信场次提高表面命中率。
- 不构成投注建议。
- 不显示下注金额。
- 不做追损、重注、串关喊单或任何无风控建议。

## 当前阶段

- Plan 1 引擎核心已完成第一版。
- Plan 0 核心数据源探测已完成第一轮。
- Plan 2 已启动，当前完成纯离线解析层、单场模型/市场输出、覆盖率与市场证据优先的每场唯一 `match_decision` 输出、本地快照 runner、可注入请求层、quota ledger、refresh runner、source fallback policy、低频调度策略、run metadata、调度执行包装、云端 ingest HMAC dry-run、本地服务端验签/幂等、SQLite 持久化、只读查询、静态预览页、标准库 HTTP/ASGI 适配层、`/healthz`、静态站点导出、本地 readiness check、`.env.example` 安全检查和 HMAC secret helper；首次 live refresh 已成功生成 72 场本地分析快照。
- Plan 2 collectors 必须基于 `docs/superpowers/data-contract.md` 和 `data/probe/` 保存样例写离线解析测试，不能按假接口写。
- Plan 3 云端与调度等阿里云资源确认后再细化。

## 开发规则

- 优先最小可行实现，不提前上 ML。
- 本届 MVP 只做 Elo + Poisson + 赔率去水 + 市场证据优先的每场唯一首选；公开产品只允许 `MATCH_PICK`（本场首选）或 `NO_CLEAN_MARKET`（无法计算）。
- S/A/B/C、EV/Edge 价值信号及旧 decision label 只允许作为 legacy compatibility 读取旧 snapshot/store；不得参与新 `match_decision` 选择，不得出现在公开 API、预览页、通知或新完赛统计中。
- 新 snapshot 每场最多一个 `match_decision`；概率偏低、书商偏少、离散度偏高、模型分歧或俱乐部评级 pending/missing 时改为风险扣分和市场共识兜底，不得删除整场。只有赔率全部无效/过期、比赛已开始或不存在任何可结算盘口时才允许 `NO_CLEAN_MARKET`。
- 当前公开策略版本为 `match_pick_v3`：世界杯市场/模型权重为 0.80/0.20；安全概率相近（默认 2 个百分点内）的候选继续比较书商覆盖、盘口质量和模型/市场一致性。中超俱乐部评级未完成时不得使用占位 1500 影响方向，改用赔率去水后的市场共识并附加内部风险扣分。
- 新完赛契约以 `closing_match_decision` 结算，统计使用 `decision_tally`（`hit/miss/push/no_pick`）、`decision_sample` 和 `decision_coverage`；旧等级 tally 不再是正式契约。
- 当前实现以 2026 世界杯为首个 competition adapter，但新增通用数据结构、snapshot 字段、概率族、赔率/盘口移动诊断和回测接口时，应尽量使用可迁移到联赛的命名与边界，避免继续把新能力写死为世界杯专用语义；已有 `stage` / `group` 等世界杯字段保持兼容，不为未来联赛提前大重构。
- 引擎层必须保持纯函数，不联网、不连数据库、不依赖云。
- 采集层使用保存的样例响应做离线解析测试。
- source refresh 失败但本地缓存存在时，可以继续用上一轮缓存生成快照；必须在 `data_quality.source_errors` 和 `data_quality.stale_sources` 标记，不能静默当作新鲜数据。
- Elo 来源为本地基线重放：`data/cache/elo_baseline_*.tsv` + openfootball 完赛比分按 eloratings 公式（K=60、中立场）增量重放生成 `elo_world.tsv`；eloratings 抓取仅用于重新锚定基线，抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不因此单独强制取消本场首选。重放计算失败时回退沿用现有 `elo_world.tsv` 并记 `elo_local` 错误。常量与实现见 `worldcup/elo_local.py`。
- scheduler 默认 dry-run，只读取本地 snapshot / quota 并输出 JSON 决策；The Odds API 按免费额度使用，低额度时必须降频。
- scheduled refresh 默认 dry-run；只有显式 `--live` 且调度 due，或同时传 `--force`，才会调用 refresh runner。
- 正常额度时，世界杯与中超调度都必须把 `match_decision.valid_until - 20 分钟` 作为刷新候选，避免有效首选先过期再等下一个赛前锚点；quota 低于等于 30 时允许按既有低额度锚点降级。
- scheduled publish 对瞬时 TLS/网络/5xx 做有限重试；仍失败时必须保留不含 secret 的 `*.publish_pending.json` 状态，下次唤醒只重试发布现有 snapshot，不重复刷新或消耗 quota。
- ingest 默认 dry-run；只构造请求体、HMAC 签名头和 body hash，不发送线上请求，不能打印 HMAC secret。
- 云端 ingest 必须使用 HMAC + timestamp + run_id/snapshot_id，并做幂等与防重放；当前默认防重放窗口为 300 秒。
- 本地 SQLite / preview 输出必须写入被忽略的 `data/local/` 或 `data/cache/`；预览页必须保留研究免责声明，不显示资金相关字段。
- HTTP / ASGI 适配层只用于本地预览、路由契约测试和后续 FastAPI 包装参考；正式依赖安装、ECS 部署、上线和云端写入必须单独确认。
- `/healthz` 只能报告服务存活，不读 DB、不依赖 secret，不输出环境变量、密钥、quota 或 snapshot 内容。
- 静态站点导出默认写入被忽略的 `data/cache/site/`，只作为本地预览/上线包草案，不代表已部署。
- readiness check 只读本地文件和变量名，不联网、不打印 secret；缺少真实 `INGEST_HMAC_SECRET` 时应报错，不要自动生成并写入 `.env`；`.env.example` 必须只含变量名和空值。
- HMAC secret helper 只允许打印新 secret 给本地人工写入 `.env`，不得自动改 `.env` 或把 secret 写入文档。

## 对抗性自审

- 赛后复盘必须区分：数据事实、暂时观察、可确认结论、工程问题。
- 当 `sample_too_small=true` 或样本数低于 `min_sample` 时，只能给观察结论，不能建议调参。
- 赛后复盘必须检查：是否被小样本或单场极端结果拉偏、模型是否弱于市场基准、当前首选策略的已结算样本是否不足、`daily_eval.decision_tally` 与 `finished.decision_tally` 是否一致、`decision_coverage` 中是否存在缺失/无效/未结算决策、是否存在 `skipped_no_closing`、closing snapshot 是否完整、是否将 legacy 决策与当前策略绩效混算、是否混淆 90 分钟/加时/点球或比分来源、The Odds API scores 与 openfootball 是否可能不同步、东道主/准主场/中立场/Elo 口径是否可能影响判断。
- 自审发现问题时，必须把结论降级为“观察”或“需修数据链路”，不得硬给模型结论。
- 写实现计划、架构方案、调度/部署方案、数据链路方案或模型调整方案时，必须加入“对抗性自审”段落。
- 项目计划自审重点检查：是否解决根因、是否范围膨胀、是否改变业务语义/接口契约/结算口径、是否触发联网/额度/密钥/线上写入/部署风险、是否可 dry-run、是否有验证和回滚方式。
- 涉及 live refresh、The Odds API quota、HMAC secret、LaunchAgent、ECS、SQLite/PostgreSQL、`data/local/`、`data/cache/`、日报推送或公网展示时，必须显式写出风险和确认点。
- 复盘和计划输出仍必须保留研究边界：不构成投注建议，不输出下注金额或执行建议。

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

## ECS SSH / 部署注意事项

- 本机可能启用 TUN / fake-ip 代理，导致 `39.102.50.205` 默认路由走 `utun4 -> 198.18.0.1`，从而出现 SSH `Connection timed out during banner exchange` 或 `Connection closed by remote host`。
- 连接 ECS 或执行部署时，优先绑定本机 Wi-Fi 源地址：`ssh -b 192.168.31.152 strategy-lab-ecs ...`；使用一键部署工具时加 `--bind-address 192.168.31.152`。
- 如果本机网络变化，先用 `ipconfig getifaddr en1` 或 `route -n get 39.102.50.205` 确认当前 Wi-Fi 地址和路由，再更新 `--bind-address` 参数。
- 线上旧 release 曾因 `/api/matches` 慢查询拖住服务；重启 ECS 后不要先压测 `/api/matches`，应先抢 SSH 窗口部署已验证热修或重启服务，再做公网 smoke。

## 近期重点

1. The Odds API key 已在聊天截图暴露过；用户已确认不充值，后续按免费额度和缓存兜底设计；赛期留意 quota，低额度时降频。
2. 保持 collector 解析测试使用 `data/probe/` 保存样例，不联网。
3. 线上 ECS 当前使用 SQLite；切换 PostgreSQL/RDS 需单独确认（`postgres_store` / `store_factory` 代码层已就绪）。
4. 赛期内完赛后用 `results_capture` / `eval_data` 积累真实赛果，再决定是否采纳 `mu_total=2.2, mu_dr_slope=0.0015`（见 `docs/research/2026-06-10-mu-dr-fit.md`）；积累足够样本前不改模型参数。
5. 日常巡检用只读命令 `python3 -m worldcup.ops_check`。
