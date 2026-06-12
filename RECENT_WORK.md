# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

## 2026-06-12 台账筛选区与历史回顾入口

- 首页/预览页台账顶部新增一级视图切换：`实时信号` / `历史回顾`；历史回顾面板默认隐藏，切换后显示已完赛战绩或空状态。
- 筛选区改为日期、赛事、等级、搜索四类：日期按钮由当前实时信号日期动态生成；赛事下拉先以 `世界杯` 为真实数据来源，并预留英超/西甲/意甲/德甲/法甲/中超选项，暂不伪造联赛数据。
- 实时信号行与历史行都写入 `data-date` / `data-league` / `data-grade` / `data-search`，前端同一套过滤状态可叠加视图、日期、赛事、等级和搜索。
- 日期按钮较多时限制在控件内部横向滚动；桌面与 390px 移动视口检查无页面级横向溢出，历史切换能正确隐藏实时表格。
- 本地验证：先看到新增预览 DOM 契约测试红灯，再实现转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `347/347 tests passed`。
- 本次只改本地静态预览渲染层与测试，未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-12 已完赛战绩区与赔率走势

- 背景：The Odds API 完赛数小时后会下架赔率事件，完赛场会从最新 snapshot 整场消失，页面"预测结果"无法等到 result 与 signals 同框；本轮改为本地富化 snapshot，把完赛数据按 closing 口径定格后随站点展示。
- 新增 `worldcup.odds_trend`：从 `data/local/history/snapshot_*.json` 提取每场 `odds_trend`，只保留首点、变化点和最新点，每 selection 上限 30 点；按文件名先过滤最近 10 天窗口，不打开窗口外归档。
- 新增 `worldcup.finished_record`：读取本地 results CSV 与 kickoff 前 3 天 history 归档，复用 `eval_data.closing_match_entry` 和 `ledger._prediction_result` 定格 closing 信号、比分、赔率、走势；`data/local/finished_record_store.json` 为被忽略的增量 store，已定格比赛不重算。
- `refresh_runner` 已在写 `analysis_snapshot.json` 与 history 归档前注入 `odds_trend` 和顶层 `finished` 块；富化失败只向 stderr 输出 warning，不阻断主刷新、发布件或归档件。
- `ledger.py` / `ledger_html.py` 已新增 S/A 战绩指标、主台账完赛去重、每行走势点、按北京日分组的已完赛战绩区、SVG sparkline 与文本兜底；老 snapshot 缺少 `finished` / `odds_trend` 时页面不渲染新增区块并保持兼容。
- 离线 smoke：生成 `data/local/backtest/p3_smoke_snapshot.json` 与 `data/cache/preview.html`，输出 `matches=71`、`with_trend=71`、`finished=1`、`skipped_no_closing=0`、`tally.S.hit=2`（揭幕战 closing 中有 2 条 S 级命中信号）。
- 浏览器 QA：通过本地只读静态服务打开 `data/cache/preview.html`；桌面视口确认战绩卡、完赛区、展开明细、sparkline 和 console 无 error/warn；390px 视口确认页面级 `scrollWidth == clientWidth`，主台账与完赛表格横向滚动限制在各自容器内，完赛行可展开且 sparkline 可见。
- 本地验证：按 TDD 逐任务看到预期红灯并转绿，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `347/347 tests passed`。
- 本次已按任务本地 commit，不 push、不部署、不触发 live refresh、不调用 The Odds API；ECS 部署与"更新规则"卡文案上线仍需用户单独确认。

## 2026-06-12 The Odds API 赛果源接入

- 背景：openfootball 开赛后仍未及时录入比分，本地赛后链路自身行为正确但被上游结果源阻塞；The Odds API `/scores` 首验样例已保存到 `data/probe/theoddsapi_scores_sample.json`，确认 Mexico 2-0 South Africa `completed=true`，实测 `/scores` 带 `daysFrom=2` 消耗 2 credits。
- 已新增 `worldcup.collectors.theoddsapi_scores` 纯离线解析、`worldcup.sources.theoddsapi_scores` 抓取/缓存/quota 槽位双写、`worldcup.scores_capture` CLI（默认 dry-run，`--live` 才联网），并把 results CSV upsert 主键迁移为 `(kickoff 日期, home, away)`，吸收跨源 kickoff 分钟差。
- `worldcup.daily_eval --live-scores` 已在赛后链路前调用 scores capture；当 openfootball 仍滞后但 scores 新增/更新时，会继续驱动 `eval_data` / `backtest`，并在 digest 中保留 `scores` 明细。
- LaunchAgent `xin.celab.football.daily-eval` 已从北京时间 12:30 改为 16:30，并加 `--live-scores`；原因是 scores feed 赛后约 1-4 小时结算，当日最晚场约 04:00 UTC 完赛，16:30 北京更能一次覆盖完整上一比赛日。
- 真实 smoke：`python3 -m worldcup.daily_eval --live-scores --notify` 返回 `status=ok`，scores 捕获 `events=72`、`completed=1`、`added=1`、`slot=primary`，results 入库 Mexico 2-0 South Africa，eval `joined=1`，backtest `n_ah=1` 且 `sample_too_small=true`，WxPusher `notification.status=sent`；`data/local/backtest/wc2026_eval.csv` 的 `ah_line=-1.0`、AH 主/客赔率均有值。
- Quota ledger 已同步：`theoddsapi` 与 `theoddsapi_primary` 均记录 `last=2`、`remaining=424`；未打印 key、secret 或 token。
- 已知风险：淘汰赛阶段 scores 可能含加时/点球比分，与 1X2 的 90 分钟结算口径冲突；6-27 前必须回评，必要时淘汰赛停用 scores 自动入库或改人工核对。Elo 重放与页面赛果显示仍以 openfootball 为准，openfootball 滞后期间页面“预测结果”可能晚于日报。
- 本地验证：按 TDD 逐任务看到预期红灯并转绿，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `330/330 tests passed`。
- 本次已本地 commit，不 push、不部署；live smoke 消耗约 2 credits，并发送了一条真实赛后日报。

## 2026-06-11 赛后链路每日自动化

- 新增 `worldcup.daily_eval`：进程内复用 `results_capture` → `eval_data` → `backtest` 三个既有 CLI 契约，生成 digest（完赛数、评估样本、1X2/OU 模型 vs 市场指标、AH 样本数、S/A 级信号命中统计）。
- `signal_tally` 复用 `ledger._prediction_result`，口径与页面“预测结果”列一致；当前只统计当前 snapshot 中已完赛比赛的 S/A 信号。
- CLI 支持 `python3 -m worldcup.daily_eval --notify`；仅当本轮有新增/更新赛果且 digest `status=ok` 时才通过 WxPusher 推送研究日报，无新增赛果返回 `no_new_results` 且不推送，重复跑幂等。
- 已安装本机 LaunchAgent `~/Library/LaunchAgents/xin.celab.football.daily-eval.plist`，每天北京时间 12:30 在 `/Users/eagod/ai-dev/足彩` 执行 bundled Python 的 `worldcup.daily_eval --notify`，日志写入 `~/Library/Logs/worldcup/daily-eval.*.log`。
- 真实 smoke：手动运行与 kickstart 均输出 `status=no_new_results`、`notification=null`；stdout/stderr 日志敏感词扫描 `api[_-]?key|secret|token|signature|cookie` 计数均为 0。
- 本地验证：先按 TDD 看到缺少 `worldcup.daily_eval` 与 `main` 的红灯，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `320/320 tests passed`。
- 本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 赛后链路每日自动化实现计划（待 Codex 执行）

- 新增实现计划 `docs/superpowers/plans/2026-06-11-daily-eval-automation.md`：新增 `worldcup.daily_eval` 编排器，进程内串联 `results_capture` → `eval_data` → `backtest` 三个既有 CLI 契约，生成 digest（完赛数 / 评估样本 / 模型 vs 市场指标 / S 与 A 级信号命中统计，口径与页面"预测结果"列一致）。
- 推送规则：`--notify` 且 `status=ok`（有新增/更新赛果）才发 WxPusher 日报；无新增赛果跳过推送，重复运行幂等不重发。文案保留研究免责声明，不含资金字段。
- Task 3 安装每日 LaunchAgent `xin.celab.football.daily-eval`（北京时间 12:30，复用现有 plist 的 Python 路径），kickstart smoke 赛前应为 `no_new_results` 不发推送；这是计划中唯一系统状态变更，用户已确认要定时执行。
- 注意：6-12 首批完赛后建议先人工跑一遍 `python3 -m worldcup.daily_eval`（不带 `--notify`）核对 `_extract_score` 真实比分格式解析，再交给定时任务。
- 全链路只读 `data/cache/`、读写 `data/local/`，不联网、不消耗 The Odds API 额度、不写线上；验证命令仍为 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`，当前基线 `316/316`，完成后预计 `322/322`。
- 本次只写计划与近期记录，未改业务代码、未 commit、未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 槽位额度跨阈值告警

- 已在 `worldcup.scheduled_publish` 接入 The Odds API 槽位额度告警：刷新前后按 `.env` 配置监控 `theoddsapi_primary` / `theoddsapi_secondary`；未配置 slot 时回退 legacy `theoddsapi`。
- 告警阈值为 100 / 30 / 10 / 0，判定口径是 `before > threshold >= after`，因此每个槽位每个阈值天然只触发一次，不需要额外状态文件。
- 跨过 0 会作为槽位耗尽事件提示：primary 耗尽后下一轮调度会自动切 secondary；secondary 也耗尽时自动刷新继续暂停并报告 `quota_exhausted`。
- 告警复用 scheduled publish 的 WxPusher 通知通道和 `--no-notify` 总开关；返回结果新增 `quota_alert`，只包含槽位名、剩余额度、跨过阈值和发送状态，不记录真实 key。
- 告警文案提醒替换 `.env` 中耗尽槽位的 `THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY`，再经确认执行一次 `worldcup.scheduled_publish --live --force` 写回新额度。
- 本地验证：先按 TDD 看到新增测试因缺少 `quota_alert` 红灯（`312/316 tests passed`），实现后 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `316/316 tests passed`。
- 本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 Elo 改为本地基线重放

- 背景：eloratings 自 2026-06-11 起返回 JS 挑战页，旧 48h Elo 缓存宽限期会在北京时间 2026-06-13 09:33 到期；已退役该宽限机制，避免到期后信号被 `unconfirmed_backup` 统一压级。
- 新增 `worldcup.elo_local`：冻结可信官方 Elo 缓存为 `data/cache/elo_baseline_world.tsv` / `elo_baseline_teams.tsv` / `elo_baseline_meta.json`，每轮从基线 + openfootball 完赛比分按 eloratings 公式全量重算 `elo_world.tsv`；口径为 K=60、全按中立场，东道主优势仍由 pipeline 单独处理。
- 重放是幂等全量计算，不做状态累积；产物必须可被 `parse_elo_ratings` 解析，且行数不少于基线行数，否则保留旧 `elo_world.tsv` 并记录 `elo_local` 错误。
- eloratings 抓取成功时仅用于重新锚定基线；抓取失败只记 `data_quality.source_errors`，不标 `stale_sources`、不触发信号降级。
- 已离线冻结当前基线：`baseline_at=2026-06-11T01:33:56.792283+00:00`，`baseline_teams=244`，`computed_rows=244`；离线 smoke 生成 `data/local/backtest/elo_smoke_snapshot.json`，72 场，样例 Elo 为 `home=1875`、`away=1518`。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `312/312 tests passed`。
- 本次未 push、未部署、未触发 live refresh、未真实访问 eloratings、未调用 The Odds API。

## 2026-06-11 The Odds API key 自动轮换

- 已新增保守 key slot 选择：`THE_ODDS_API_KEY_PRIMARY` / `THE_ODDS_API_KEY_SECONDARY` 都配置时优先 primary，primary 在 quota ledger 中剩余额度为 0 时自动切到 secondary；两个配置槽都耗尽时继续报告 `quota_exhausted` 并暂停刷新。
- `THE_ODDS_API_KEY` 保持旧入口兼容，可作为 primary fallback；显式传 `api_key` 的测试/手工调用仍按 legacy provider 处理。
- scheduled refresh / publish 已改为从调度报告和本地 `.env` 选择可用 slot，并把 `theoddsapi_provider` 传到 refresh runner；run summary 会记录安全的 `odds_api_key_slot` 和 provider alias，不记录真实 key。
- The Odds API source 成功刷新后会把 quota 写入选中 provider（如 `theoddsapi_secondary`），并同步镜像到旧 `theoddsapi` provider，兼容现有页面和运维检查。
- 已同步 `.env.example` 空变量名、readiness 安全检查和 README 运维说明；本次未把真实 key 写入 git、文档或回复。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `304/304 tests passed`。
- 本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 自算 Elo 与额度告警实现计划（待 Codex 执行）

- 探测确认 eloratings WAF 为 JS 挑战页，加浏览器请求头无法绕过（Python UA 415；浏览器 UA 200 但返回 "One moment, please..." 挑战 HTML；cookie+Referer 仍 415）；方案结论与对比见 `docs/superpowers/specs/2026-06-11-elo-source-resilience-design.md`。
- 待执行计划一 `docs/superpowers/plans/2026-06-11-self-computed-elo.md`：新增 `worldcup/elo_local.py`（冻结基线 / 增量重放 / 行数护栏 / CLI），`refresh_runner` 改为基线+openfootball 赛果自算 Elo，eloratings 抓取降级为可选重新锚定，48h 宽限期退役；含 CLAUDE/AGENTS/README 同步。**截止压力：Elo 缓存宽限期 2026-06-13 09:33（北京时间）到期，须在此前合入。**
- 待执行计划二 `docs/superpowers/plans/2026-06-11-quota-low-alert.md`（已改 v2 适配 key 槽位轮换）：按 `.env` 配置的槽位分别监控 `theoddsapi_primary` / `theoddsapi_secondary`（未配置槽位回退 legacy），任一槽位向下跨过 100/30/10/0 阈值时随当轮发布发 WxPusher 告警；跨 0 即"槽位耗尽、自动切换/暂停"提示，提醒给耗尽槽位补新 key 并 force publish 复位台账。每槽位每阈值只发一次，走 `--no-notify` 总开关，不打印 key 值。
- 执行顺序：先 Elo（有截止时间），后额度告警；两计划互不依赖。验证命令仍为 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`。
- 顺带事项：`docs/superpowers/plans/2026-06-11-plan-a-cadence-simplification.md`、`2026-06-11-raw-odds-archive.md` 与本轮新增 spec/plan 文档目前未跟踪，可随本轮 docs commit 一并入库。
- 本次只写探测结论、设计与计划，未改业务代码、未 commit、未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 原始赔率响应逐轮归档

- 已在 live refresh 成功获取新赔率后，把 The Odds API 原始逐家报价 gzip 归档到 `data/local/history/odds_raw_<run_id>.json.gz`；对应 `RefreshResult.odds_raw_archive_path` 会记录归档路径。
- The Odds API 请求失败并使用本地赔率缓存兜底时不归档，避免把上一轮旧响应重复记成新 run；归档失败只输出 warning，不阻断 snapshot 生成、history snapshot 归档或后续发布链路。
- 用途仅限赛后 odds movement / line movement 研究；本计划未提取 movement 特征、未增加 `late_steam` 信号护栏、未改模型或信号逻辑。
- 已扩展 `tests/test_refresh_runner.py` 覆盖成功归档 gzip 内容和兜底轮不归档；本地全量验证通过。
- 本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 刷新节奏简化为 Plan A

- 已移除 7d / 3d / 1d / 6h 窗口分级，常规刷新改为每天 1 次；每场临赛锚点从 6 个改为 5 个：去掉 T-3h30 / T-70 / T-40，新增 T-12h / T-6h，并保留 T-90 / T-55 / T-25。
- `policy_version` 已升为 `free-tier-v2`；低额度（≤30）只保 T-90 / T-55 / T-25，额度耗尽暂停的既有保护不变。
- 预期消耗口径维持小组赛约 828 credits；用户已确认额度耗尽后更换免费 `THE_ODDS_API_KEY` 的策略。换 key 后需经确认执行一次 `worldcup.scheduled_publish --live --force`，让新额度写回 quota ledger。
- 已同步预览页“更新规则”卡片、scheduler / snapshot / refresh / scheduled publish 测试期望和 README 运维要点。
- 本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 调度触发时间与页面显示对齐

- 排查确认 `20260611T051346Z-live` 是北京时间 13:13:46 自动刷新：The Odds API 整包刷新 72 场，quota `458 -> 455`，并因 14 条显著变化发出手机通知；14:00 前后的 LaunchAgent 唤醒只是 skipped。
- 根因：`build_match_refresh_plan` 的普通 cadence 只有在 `last_refresh_at + interval` 尚未到达时才对齐到开赛时钟；一旦 raw interval 已经过了，就把 `next_update_at` 设成当前唤醒时间，导致真实触发早于页面显示。
- 已修复为普通 cadence 始终先对齐到该场开赛时间的分钟/秒，只有对齐后的时间也已过去才 due；临赛固定锚点仍保留“错过即补刷”行为。
- 新增回归测试覆盖 `03:08:26` 上次刷新、`05:13:46` 唤醒时仍等待 `06:00:00` 的单场计划和全局 decision。
- 本地验证：先看到新增测试红灯（`290/292 tests passed`），修复后 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `292/292 tests passed`。
- 只读模拟验证：用 `snapshot_20260611T030826Z-live.json` 在 `2026-06-11T05:13:46+00:00` 计算，结果 `should_refresh=False`、`next_due_at=2026-06-11T06:00:00+00:00`；当前真实 dry-run 为 `status=dry_run`、`next_due_at=2026-06-11T08:00:00+00:00`，未触发刷新、发布或通知。

## 2026-06-11 手机推送通知链路已上线

- 已接入 `worldcup.notifications`：复用研究台账“本轮变化”规则，对比发布前后的 snapshot，只在等级、EV、Edge、模型概率、市场概率或赔率出现显著变化时生成通知。
- `worldcup.scheduled_publish --live` 在刷新并成功发布后自动发送手机通知；未到点、dry-run、空快照、发布失败或无显著变化都不发送；临时关闭可加 `--no-notify`。
- 通知通道使用全局 WxPusher 工具 `/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind`；代码捕获并丢弃原始 stdout/stderr，返回结果只保留 `status`、`exit_code`、summary 和条数，避免把 UID、URL、token 或原始响应写入 scheduled-publish 日志。
- 新增 `tests/test_notifications.py` 与 `tests/test_scheduled_publish.py` 覆盖通知内容、无变化跳过、WxPusher 原始输出脱敏、发布成功后触发通知；TDD 红灯为缺少 `worldcup.notifications`。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `290/290 tests passed`；`python3 -m worldcup.scheduled_publish ...` dry-run 返回 `notification=null`，未触发刷新、发布或通知。
- 已按“直接上线”确认提交并推送 `f6708a0 feat: notify significant match updates`，部署到 ECS `/opt/worldcup/releases/f6708a0`；公网 `/healthz`、`/api/matches`、首页和 `/preview` 验证通过，远端最近日志敏感词/error 命中 0。
- 本次实现不会主动消耗 The Odds API quota；当前 dry-run 为 `status=dry_run`、`notification=None`、`should_refresh=False`、下一轮 due `2026-06-11T06:00:00+00:00`、quota remaining `458`。下一次 scheduler due 且发布成功后才会根据变化发通知。

## 2026-06-11 下次更新时间整点对齐已上线

- 线上截图发现 03:00 开赛比赛在 10:58 手动刷新后显示“下次更新 12:58”；根因是单场普通 cadence 直接使用 `last_refresh_at + interval`，把手动刷新时的分钟秒带进了下一轮计划。
- 已在 `worldcup/scheduler.py` 将普通 cadence 改为先满足原间隔，再对齐到该场开赛时间的分钟/秒；03:00 开赛场次会显示整点，:30 开赛场次会显示半点，临赛固定锚点不变。
- 新增回归测试 `test_match_plan_aligns_cadence_to_kickoff_clock_after_manual_refresh`，覆盖 `10:58:55 -> 13:00`；全量测试 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `284/284 tests passed`。
- 已提交并推送 `1205e2a fix: align match refresh cadence to kickoff clock` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/1205e2a`；`worldcup.service` 与 `nginx` 均为 active。
- 已按确认再次强制刷新/发布 run `20260611T030826Z-live`，线上 ingest 返回 `stored`，72 场；当前最新 snapshot 的墨西哥 vs 南非下次更新为 `2026-06-11T06:00:00+00:00`（北京时间 14:00），页面不再出现 `12:58`。
- The Odds API quota 当前 `remaining=458`、`used=42`，本次强制刷新消耗 3。
- 公网 `/healthz`、`/api/matches`、首页和 `/preview` 验证通过，首页/preview 最后更新时间为北京时间 `2026 年 6 月 11 日 星期四 11:08`；资金/下注词扫描未命中，最近 10 分钟服务日志敏感词/error 命中 0。
- `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`、`warnings=2`；warning 来自 Nginx 历史 5xx/upstream 计数，非本次发布后的新错误。

## 2026-06-11 首页下次更新时间列已上线

- 已把单场 `refresh_plan` 接入本地/实时 snapshot：每场比赛写入安全的 `next_update_at`、`policy_reason`、`label`、`description`、`interval_seconds`、`should_refresh`；`run.policy.match_plans` 保留完整调度决策。
- `scheduler` dry-run 已改为用 72 场比赛的最早 `next_update_at` 决定全局 due，LaunchAgent 仍只需按全局任务唤醒。
- 首页研究台账在“更新”后新增“下次更新”列，展开详情补充“下次更新”；右侧“更新规则”卡片显示“按每场比赛独立调度”和最近一次计划。
- `/api/matches` / 静态导出行新增脱敏的 `next_update_at`、`next_update_label`、`next_update_description`，不输出 quota、secret 或资金相关字段。
- TDD 先看到 `refresh_plan`、ledger/query 投影和首页表头断言失败，再实现转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `283/283 tests passed`。
- 已提交并推送 `233b083 feat: show match-specific next update times` 到 `origin/main`，随后部署到 ECS；`/opt/worldcup/current` 已切到 `/opt/worldcup/releases/233b083`，`worldcup.service` 与 `nginx` 均为 active。
- 公网 `/healthz` 返回 200，`/api/matches` 返回 72 场，首页和 `/preview` 均显示“下次更新”列与“按每场比赛独立调度”；资金/下注相关词扫描未命中。
- 本次上线只切换代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot；最近 10 分钟 `worldcup.service` 与 `nginx` journal 敏感词/error 扫描为 0。

## 2026-06-11 Elo 缓存 48h 宽限期

- 背景：`www.eloratings.net` 自 2026-06-11 起对非浏览器请求返回 HTTP 415，导致 live refresh 可走本地 Elo 缓存但被统一标为 `stale_sources=["eloratings"]`，信号被 `unconfirmed_backup` 封顶到 B。
- 已在 `worldcup/refresh_runner.py` 增加 `ELO_CACHE_GRACE_SECONDS = 48 * 3600`：Elo 抓取失败且 `elo_world.tsv` / `elo_teams.tsv` mtime 仍在宽限期内时，只记录 `data_quality.source_errors`，不标 `stale_sources`、不触发信号降级；超过宽限期仍保持旧降级行为。
- 新增离线回归测试覆盖宽限期内不降级、49 小时超期仍降级；故障注入验证把宽限期临时放大到 999 小时时，超期测试会变红。
- 已同步 `CLAUDE.md` / `AGENTS.md` / `README.md` 的 source refresh stale 规则，说明 Elo 例外和常量位置。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `279/279 tests passed`。
- 遗留事项：eloratings 抓取本身仍被 WAF 拦截，另案处理；本次未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-11 线上空数据恢复与 Elo 缓存防线

- 排查确认线上服务、Nginx、SQLite 均正常；空数据根因是 `20260610T191323Z-live` 自动刷新时 eloratings 返回 HTML 挑战页，`elo_world.tsv` / `elo_teams.tsv` 被当作 TSV 覆盖，导致 Elo 解析为 0 条、72 场全部进入 `missing_elo`，最终发布了 0 场 snapshot。
- 已用上一份正常 72 场快照生成恢复 run `20260610T161313Z-live-restore` 并发布到 `https://football.celab.xin/api/ingest/snapshot`；ECS 返回 HTTP 200 / `ingest_status=stored`，公网 `/api/matches` 已恢复 72 场。
- 已恢复本机 `data/cache/elo_world.tsv`、`data/cache/elo_teams.tsv` 和 `data/cache/analysis_snapshot.json` 到可用状态；Elo 缓存当前可解析 244 条 rating、502 条 alias，本机 snapshot 为 72 场。
- 代码防线：`worldcup.sources.eloratings` 在写缓存前校验 HTTP 状态和 TSV 可解析性，HTML/空响应会抛错并保留旧缓存；`worldcup.scheduled_publish` 对刷新后 0 场 snapshot 返回 `blocked / empty_refreshed_snapshot`，不调用 publish。
- 新增回归测试覆盖 Elo HTML 挑战页不覆盖缓存、0 场刷新结果不发布；本地验证 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `277/277 tests passed`。
- 本次未调用 The Odds API、未刷新外部赔率；执行了一次已确认的线上恢复写入，未 push、未部署 release。

## 2026-06-10 AH 进入赛后评估链路

- 已按 `docs/superpowers/plans/2026-06-10-ah-eval-coverage.md` 逐任务 TDD 执行，并按任务做本地 commit；未 push、未部署、未触发 live refresh、未调用 The Odds API。
- snapshot 新增增量兼容块 `market.ah_main`：记录 AH 主盘 `line_home`、home/away 聚合赔率和双边报价家数；无 AH 报价时不写该键，老快照兼容。
- `eval_data` 输出新增 `ah_line` / `odds_ah_home` / `odds_ah_away` 三列；两边赔率或盘口线不完整时三列全空，避免半缺失数据进入评估。
- `backtest.py` 零改动；现有 `backtest.load_matches` 已能读取新增三列并进入 `ah_ev_buckets`。
- 本地验证：先按 TDD 看到新增测试红灯，再实现转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `275/275 tests passed`。
- 离线 smoke：`python3 -m worldcup.local_runner --input-dir data/cache --out data/local/backtest/ah_smoke_snapshot.json` 生成 72 场临时 snapshot，统计 `{"matches": 72, "with_ah_main": 72}`；输出位于被忽略的 `data/local/backtest/`。
- 时效注意：本改动合入本机 `main` 后，下一次 live refresh 生成的 closing snapshot 才会带 `ah_main`；已归档旧 snapshot 不回补，对应 AH 列为空属预期。

## 2026-06-10 AH 评估覆盖实现计划（待 Codex 执行）

- 新增实现计划 `docs/superpowers/plans/2026-06-10-ah-eval-coverage.md`：让赛后验证链路覆盖亚洲让球。
- 摸底结论：`backtest.py` 的 AH 评估（CSV 列 `ah_line` / `odds_ah_home` / `odds_ah_away`、`ah_ev_buckets`）早已就绪，本计划不改 backtest；缺口只在 snapshot `market` 块无 AH 主盘聚合赔率、`eval_data` 无 AH 列。
- 计划共 4 个任务、全 TDD、全离线：pipeline 生成 `market_ah_main`（复用 `_main_home_ah_line` + `odds.aggregate` 双边聚合，不碰 `_ah_signals` 信号逻辑）；`local_runner` 增量序列化 `market["ah_main"]`（无 AH 报价不写键，老快照兼容）；`eval_data` 输出三个 AH 列并与 `backtest.load_matches` roundtrip；离线 smoke + README/RECENT_WORK 文档同步。
- 用户决定由 Codex 按计划执行；验证命令仍为 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`，当前基线 `269/269 tests passed`，完成后预计 `275/275`。
- 时效注意：需在下一次 live refresh 前合入本机 `main`，开赛前 closing snapshot 才会带 `ah_main`；已归档旧快照不补字段，对应 AH 列为空属预期。
- 本次只写计划与近期记录，未改业务代码、未 commit、未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-10 一键只读运维检查

- 新增 `python3 -m worldcup.ops_check`，用于一键汇总本机 snapshot/history/quota/LaunchAgent、本机 scheduled-publish 日志、公网健康与页面更新时间、ECS 服务/SQLite/latest snapshot 和日志安全计数。
- 该命令只读执行：不触发 refresh、不调用 The Odds API、不发送 ingest、不重启服务、不部署、不读取或打印 `.env` secret。
- 输出会保留元数据摘要，例如 run_id、场次数、quota 数字、HTTP 状态、SQLite 最新 snapshot 元数据和敏感词/error 计数；远端 payload/snapshot 正文会被过滤。
- 新增 `tests/test_ops_check.py`，覆盖本机+公网摘要、远端元数据过滤、敏感词计数不泄露值；TDD 先确认缺少 `worldcup.ops_check` 的红测试，再实现。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `269/269 tests passed`；真实只读 smoke `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`、`warnings=0`。
- 本次仅新增只读运维工具、测试和 README 用法；未 push、未部署、未触发 live refresh、未写线上。

## 2026-06-10 强制发布追平线上快照

- 经用户确认，已执行一次 `worldcup.scheduled_publish --live --force`，新 run 为 `20260610T100725Z-live`，向 `https://football.celab.xin/api/ingest/snapshot` 发布成功；ECS 返回 HTTP 200 / `ingest_status=stored`。
- 本地 `data/cache/analysis_snapshot.json` 与 `data/local/history/snapshot_20260610T100725Z-live.json` 均为 72 场，`source_errors=[]`、`stale_sources=[]`。
- The Odds API quota 记录更新为 `remaining=473`、`used=27`，本次强制刷新消耗 3。
- 公网页面 `/` 和 `/preview` 已显示“最后更新 2026 年 6 月 10 日 星期三 18:07”；`/api/matches` 返回 72 场；公网 `/api/snapshot/latest` 仍按 Nginx 规则返回 404。
- ECS `worldcup.service` 与 `nginx` 均为 active；服务器 SQLite snapshot 行数增至 7，最新记录为 `20260610T100725Z-live`。
- 本机 scheduled-publish 日志、ECS journal、Nginx error/access log 检查未发现项目 secret/header 泄露；公开页面和 `/api/matches` 未命中资金/下注类禁止词。
- 本次未改代码、未重启服务、未部署 release、未 push。

## 2026-06-10 台账右栏下移

- 已将研究台账右侧说明栏改为台账下方的全宽卡片区，移除桌面端 `1fr + 340px` 两列布局，避免“信号原因”列被右栏挤压只显示半截。
- `right-rail` 下移后改用自适应卡片网格；移动端仍保持表格容器内横向滚动，不产生页面级横向溢出。
- 新增 preview 布局回归测试，先验证旧布局失败，再实现单列布局；本地 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `266/266 tests passed`。
- 本地渲染验证：桌面 `1536x900` 下右栏位于台账之后、`信号原因` 单元格在视口内、无页面级横向溢出；移动 `390x844` 下横向滚动限制在表格容器内；展开详情交互正常，控制台无 error/warn。
- 本次仅改展示层与测试，不主动触发 live refresh、不调用 The Odds API、不改 LaunchAgent。

## 2026-06-10 预测结果常驻列

- 研究信号台账新增常驻“预测结果”列：赛前/未完赛显示“待赛”，完赛后显示“命中 / 未中 / 走水”。
- 原“信号原因”栏继续保留赛后比分、方向和结算说明，避免只看标签时缺少上下文。
- 表格日期行和详情行 `colspan` 调整为 11，台账最小宽度调整为 1160px，窄屏继续在表格容器内横向滚动。
- 本地 TDD 先看到 preview 测试因缺少表头和状态徽章失败，再实现；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `265/265 tests passed`。
- 本次仅改展示层与测试，不主动触发 live refresh、不调用 The Odds API、不改 LaunchAgent。

## 2026-06-10 台账赛后预测结果显示

- 已提交并推送 `2243951 feat: show post-match prediction results` 到 `origin/main`，并部署到 ECS。
- 已将 release `2243951` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/6ffa888` 切到 `/opt/worldcup/releases/2243951`。
- `worldcup.service` 与 `nginx` 均为 active；服务器本机与公网 `/healthz` 返回 ok，公网 `/api/matches` 返回 72 场，`/api/snapshot/latest` 仍返回 404。
- 公网页面包含新增 `prediction-result` 渲染样式、保留“仅用于研究分析，不构成投注建议”免责声明，资金/下注相关词扫描未命中。
- 服务器 SQLite 当前 6 条 snapshot；本次部署只切换代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot。
- `worldcup.service` 最近 10 分钟 journal 敏感关键词扫描对 API key、HMAC secret、DATABASE_URL、signature、token、cookie、private-key 标记返回 0。
- 新增赛后验证显示：当 openfootball 缓存已有完赛比分时，snapshot 中对应 match 会附加 `result.status=finished`、`home_score`、`away_score`。
- 研究信号台账会在“信号原因”栏显示“预测结果：命中 / 未中 / 走水”，并展示比分和方向；展开“分析详情”会新增“赛后验证”行。
- 判定规则：胜平负按主胜/平/客胜；大小球按总进球与盘口线；亚洲让球按所选球队让球线结算，走盘显示为“走水”。
- 赛果缺失或比赛未完赛时页面保持现状，不显示赛后验证块；不读取 secret，不显示下注金额或资金相关字段。
- 本地验证先看到新增测试因缺少 `result` / `prediction_result` / HTML 渲染失败，再实现；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `265/265 tests passed`。
- 本次未触发 live refresh、未调用 The Odds API、未改 LaunchAgent。

## 2026-06-10 自动刷新归档验收工具

- 新增只读 CLI `python3 -m worldcup.refresh_audit`，用于检查 `data/local/history/` 最新归档 snapshot 摘要和 LaunchAgent 当前指向；不会联网、不会触发 refresh、不会读取 secret。
- CLI 输出最近归档的 `run_id`、`snapshot_at`、场次数、`source_errors_count`、`stale_sources`，以及 LaunchAgent 的 label、Python、module、工作目录、日志路径和 interval。
- 当前只读检查结果：历史归档已有 1 份 `snapshot_20260610T090754Z-live.json`，`matches=72`、`source_errors_count=0`、`stale_sources=[]`。
- 当前 LaunchAgent 指向 `/Users/eagod/Library/LaunchAgents/xin.celab.football.scheduled-publish.plist`，每 900 秒运行 `worldcup.scheduled_publish --live`，工作目录为 `/Users/eagod/ai-dev/足彩`。
- 本地验证先看到新增测试因缺少 `worldcup.refresh_audit` 失败，再实现；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `260/260 tests passed`。
- 已随 `2243951` 一并推送并部署；本工具本身只读，未触发 live refresh、未调用 The Odds API、未改 LaunchAgent。

## 2026-06-10 赛果回填与自有赔率评估链路

- 已完成计划 B 并按任务本地提交；未 push、未部署、未触发 live refresh、未调用 The Odds API、未改 LaunchAgent。
- `refresh_runner` 在写完 `analysis_snapshot.json` 后会尝试把 snapshot 归档到 `data/local/history/snapshot_<run_id>.json`；归档失败只向 stderr 打 warning，不阻断刷新/发布主链路。
- 归档功能合并进本机 `main` 后，从下一次 live refresh 开始自动积累；此前 08:27 这一轮不会被 retroactive 补归档。
- 新增 `parse_openfootball_results`，兼容 openfootball 顶层 `score1/score2` 和 `score.ft` 两种完赛比分格式，跳过未完赛和占位队。
- 新增 `worldcup.results_capture`：从本地 `data/cache/openfootball_2026.json` 幂等 upsert 到 `data/local/results/wc2026_results.csv`。
- 新增 `worldcup.eval_data`：用“开球前最后一份”归档 snapshot 的 1X2/OU 聚合赔率 join 赛果，输出 `data/local/backtest/wc2026_eval.csv`，可交给现有 `worldcup.backtest`。
- 开赛后日常命令：`python3 -m worldcup.results_capture`，`python3 -m worldcup.eval_data`，`python3 -m worldcup.backtest --csv data/local/backtest/wc2026_eval.csv --min-sample 30 --out data/local/backtest/wc2026_report.json`。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `257/257 tests passed`；赛前 smoke `python3 -m worldcup.results_capture` 输出 `{"finished": 0, "added": 0, "updated": 0, "total": 0}` 属正常。
- 已知局限：评估 CSV 的 `neutral` 一律为 1；AH 仅在 closing snapshot 含 `market.ah_main` 时进入评估，旧归档快照对应 AH 列为空；openfootball 首个比赛日若真实比分字段格式不同，需要回来调整 `_extract_score`。

## 2026-06-10 mu-dr 总进球先验证据

- 新建分支 `codex/mu-dr-prior-results-capture`，已完成计划 A 前 5 个任务并按任务本地提交；未 push、未部署。
- 新增 `poisson.prior_mu(dr, cfg)`，并将 pipeline/backtest 的 `dr` 接入 `blended_mu`；`config/settings.yaml` 只新增 `poisson.mu_dr_slope: 0.0`，`mu_total` 保持 `2.6`，默认生产行为关闭。
- 已跑 3x6 网格（base `2.2/2.3/2.4` x slope `0/0.001/0.0015/0.002/0.0025/0.003`），原始产物写入被忽略的 `data/local/backtest/mu_fit_*`。
- 绝对 OU Log Loss 最优为 `mu_total=2.2, mu_dr_slope=0.002`，但相对 18 格中 1X2 最优劣化 `0.002783108866`，超过 `0.002` 护栏。
- 通过护栏的推荐候选为 `mu_total=2.2, mu_dr_slope=0.0015`：OU Log Loss `0.679537590346`，相对现状基线改善 `0.011705408636`；1X2 Log Loss `0.893132261182`，相对 18 格中 1X2 最优劣化 `0.001790110834`，在护栏内。
- 证据报告见 `docs/research/2026-06-10-mu-dr-fit.md`；该候选只供与 `dc_rho` 一起决策，当前未启用。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `248/248 tests passed`。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未部署、未 push；只生成本地 ignored 回测产物。

## 2026-06-10 Elo replay + 真实历史回测基线

- 新建分支 `codex/elo-replay-real-backtest`，按 `docs/superpowers/plans/2026-06-10-elo-replay-real-backtest.md` 分任务 TDD 执行并做本地 commit；未 push、未部署。
- 新增 `worldcup.elo_replay`：按 eloratings 公开公式实现 K 因子、净胜球指数、零和更新，并从 1872 年国际比赛结果重放推演赛前 Elo。
- replay vs 官方榜对照 CLI 已跑通：`matches_replayed=49378`、`teams_rated=336`、`teams_mapped_to_codes=236`、官方 top-10 在 replay top-30 中 `overlap_hits=10/10`，未触发低 overlap 停止条件。
- 新增 `worldcup.backtest_data`：将 `data/probe/intl_results_martj42.csv` 转为回测 CSV，过滤 2010-01-01 起且双方可映射 Elo alias 的比赛；生成本地样本 `source_rows=49378`、`output_rows=14901`。
- `worldcup.backtest` 新增 `--sweep` 参数扫描；已跑 `poisson.dc_rho=0,-0.05,-0.1,-0.15`，未修改 `config/settings.yaml`。
- 首份真实历史回测报告已写入 `docs/research/2026-06-10-intl-backtest-baseline.md`：基线 1X2 model Brier `0.5252676489648002`、Log Loss `0.8933331617985814`；uniform Brier `0.6666666666666667`、Log Loss `1.0986122886681098`。
- 扫描结果中 `dc_rho=-0.15` 的 1X2 Log Loss 最低为 `0.891783627002631`，相对 `dc_rho=0` 的 Log Loss 改善 `0.0015495347959503247`；这只是证据，不自动改生产配置。
- 无赔率历史样本时，`model_matched` / `market` 样本数均为 0，不能校准 EV/Edge 阈值或 `poisson.mu_market_weight`；后续要等世界杯期间自有赔率快照和赛果回填。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `240/240 tests passed`。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未部署、未 push；本地产物仅写入被忽略的 `data/local/backtest/`。

## 2026-06-10 回测加固与 OU/DC 算法代码已上线

- 已将本地 `main` 推送到 `origin/main`，推送范围包含两批改动：`codex/backtest-ou-market-total` 和 `codex/backtest-hardening-dixon-coles`。
- 已将 release `6ffa888` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/e06536a` 切到 `/opt/worldcup/releases/6ffa888`。
- `worldcup.service` 已重启并保持 active；`nginx` 保持 active。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `228/228 tests passed`。
- 服务器本机与公网 `/healthz` 均返回 ok；公网 `/` 保留“仅用于研究分析，不构成投注建议”免责声明和“研究台账”页面。
- 公网 `/api/matches` 与服务器本机 `/api/matches` 均返回 72 场；敏感/资金相关字段扫描未命中。
- 服务器 SQLite 当前 5 条 snapshot；本次部署只切换代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。

## 2026-06-10 回测加固 + Dixon-Coles 门控实现完成

- 新建分支 `codex/backtest-hardening-dixon-coles`，基于 `codex/backtest-ou-market-total` 按 `docs/superpowers/plans/2026-06-10-backtest-hardening-and-dixon-coles.md` 分任务 TDD 执行；每个任务已做本地 commit，未 push、未部署。
- 回测 CSV 加载器现在校验所有十进制赔率必须 > 1.0，并在错误中带 CSV 行号，避免无效赔率静默进入指标。
- 回测报告新增 `markets.*.model_matched`：模型在“有市场赔率的同样本行”上的指标，用于和 `market` 基线公平比较；`markets.*.model` 仍保留全样本模型指标。
- `worldcup.backtest` CLI 新增 `--set section.key=value` 单次配置覆盖，使用深拷贝避免污染共享配置；可用于 `poisson.dc_rho`、`poisson.mu_market_weight` 等参数实验。
- 收尾 review 补充校验：`--set poisson.=1` 这类空嵌套 key 现在会按无效覆盖报错，不再静默写入空配置键。
- OU 市场锚定新增 `odds.min_books` 守卫：只有 over/under 双边报价家数均达标才用市场反推 `mu_total`，否则回退 `poisson.mu_total`；当前本地缓存抽查为 `anchored: 68 / 72`，4 场单 book 回退属预期。
- Poisson 比分矩阵新增配置门控 Dixon-Coles 低比分修正，`poisson.dc_rho: 0.0` 默认关闭且行为与历史版本一致；rho 取值必须等真实历史数据回测后再启用。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 已通过 `228/228 tests passed`；CLI smoke `python3 -m worldcup.backtest --csv tests/data/backtest_sample.csv --min-sample 5 --out data/local/backtest/dc_probe.json --set poisson.dc_rho=-0.1` 成功。
- 后续建议：先确认真实历史数据来源，再用 `--set` 扫描 `dc_rho`、`poisson.mu_market_weight` 和 ensemble 权重；未经回测证据不要启用非零 `dc_rho`。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未改 scheduler / ingest / HMAC / 数据库 schema。

## 2026-06-10 回测框架 + OU 市场锚定实现完成

- 新建分支 `codex/backtest-ou-market-total`，按 `docs/superpowers/plans/2026-06-10-backtest-and-ou-market-total.md` 分任务 TDD 执行，并为每个任务做本地 commit；未 push、未部署。
- 修复 OU 大小球模型总进球恒定问题：新增 `prob_total_over`、`implied_total_mu`、`blended_mu`，`poisson.mu_market_weight: 0.7` 使逐场 `mu_total` 由 OU 市场去水概率与配置先验混合；无 OU 市场时回退先验，权重为 0 时可回到旧行为。
- `pipeline.analyze_match_input` 现在先聚合 OU 市场，再使用逐场 `mu_total_used` 计算比分矩阵；本地 snapshot `model.mu_total` 记录实际使用值，现有 API / ingest / store 契约为增量兼容。
- 新增 `worldcup.backtest` 离线回测框架：CSV 加载、单场概率重放、Brier / Log Loss、校准分箱、EV 与赔率分层、AH 实际回报、按 Elo 差分桶的总进球诊断、market / uniform 基线和 CLI。
- 新增合成样例 `tests/data/backtest_sample.csv`，仅用于测试和格式演示，不得用于正式结论；真实历史数据（赛前 Elo + 收盘赔率）来源仍需单独确认。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `218/218 tests passed`；CLI smoke `python3 -m worldcup.backtest --csv tests/data/backtest_sample.csv --min-sample 5` 成功生成被忽略的 `data/local/backtest/report.json`。
- 后续建议：先确认真实历史数据来源，用真实数据跑回测后再调整 `poisson.mu_market_weight`、Elo/Poisson ensemble 权重和 EV/Edge 阈值；OU 信号会因市场锚定明显趋于保守，属于预期行为。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未改 scheduler / ingest / HMAC / 数据库 schema。

## 2026-06-10 研究台账行内变化与 S/A 徽章已上线

- 已提交并推送 `e06536a feat: move signal changes into ledger rows` 到 `origin/main`。
- 已将 release `e06536a` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/0a926c8` 切到 `/opt/worldcup/releases/e06536a`。
- `worldcup.service` 已重启并保持 active；服务器本机 `127.0.0.1:8788/healthz` 与公网 `/healthz` 均返回 ok。
- 公网 `/` 和 `/preview` 返回 200，页面保留“仅用于研究分析，不构成投注建议”免责声明；独立 `change-summary` / “最近变化”标题已不再出现，`grade-priority` 样式已出现在页面中。
- 公网 `/api/matches` 返回 72 场，未发现资金相关字段；服务器 SQLite 当前 5 条快照。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 本次上线只发布代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-10 研究台账行内变化与 S/A 徽章

- 新建分支 `codex/ledger-row-changes-signal-badges`。
- 研究台账移除独立“最近变化”区块，把本轮等级、EV、Edge、概率和赔率变化挂到对应比赛/盘口信号行，并在展开详情中显示“本轮变化”。
- S/A 等级徽章改为更醒目的高对比块状样式，保留 B/C/D 的低干扰样式。
- 本地浏览器检查通过：桌面预览无独立 `change-summary`，变化 chip 出现在对应行；390px 移动视口下页面无整体横向溢出，宽表格仍在容器内滚动。
- 本次只改本地 UI/投影代码和测试，未提交、未 push、未部署、未触发 live refresh、未调用 The Odds API、未写入线上 snapshot。

## 2026-06-10 回测框架 + OU 市场锚定实现计划（待 Codex 执行）

- 算法评估发现关键缺陷：OU 模型 `lh + la = mu_total = 2.6` 恒成立，两独立 Poisson 之和仍是 Poisson(2.6)，已数值验证 dr 0~600 时 `P(over 2.5)` 全部为 0.4816（极端 clamp 时 0.5063），当前 OU 信号无信息量。
- 新增实现计划 `docs/superpowers/plans/2026-06-10-backtest-and-ou-market-total.md`，对应 spec `2026-06-10-signal-quality-backtest-design.md` 第二阶段。
- 计划共 10 个任务、全 TDD：引擎新增 `prob_total_over` / `implied_total_mu` / `blended_mu`（`mu_market_weight: 0.7` 市场锚定逐场总进球，无市场回退先验、权重 0 即旧行为）；pipeline 先聚合 OU 市场再算矩阵，snapshot 增量字段 `model.mu_total`；新建 `worldcup/backtest.py`（CSV 加载、概率重放、Brier / Log Loss / 校准分箱、EV 与赔率分层、AH 实盈、|dr| 总进球诊断、市场与 uniform 基线、`sample_too_small` 标记、CLI）。
- 已确认现有测试只断言 OU 输出结构、不断言具体数值，行为变更不会打破存量测试；`MatchAnalysis` 仅在 `pipeline.py` 一处按关键字构造，加字段安全。
- 用户决定由 Codex 按计划执行；验证命令仍为 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`。
- 注意：真实历史数据（赛前 Elo + 收盘赔率）来源需单独确认，本计划只交付框架与合成样例；OU 信号修复后会显著趋于保守，属预期行为。
- 本次只写计划与近期记录，未改业务代码、未触发 live refresh、未调用 The Odds API、未部署、未 push、未 commit。

## 2026-06-10 信号质量防抖代码已上线

- 已提交并推送 `0a926c8 feat: add signal quality guards` 到 `origin/main`。
- 已将 release `0a926c8` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/b597379` 切到 `/opt/worldcup/releases/0a926c8`。
- `worldcup.service` 已重启并保持 active；公网 `/healthz` 与服务器本机 `127.0.0.1:8788/healthz` 均返回 ok。
- 公网 `/api/matches` 返回 72 场，未发现资金相关字段；页面仍保留“仅用于研究分析，不构成投注建议”免责声明。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 本次上线只发布代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot；新信号质量原因会在后续定时发布生成新 snapshot 后体现。

## 2026-06-10 信号质量防抖第一阶段实现

- 新增 `model_disagreement` 降级：Elo 与 Poisson 在 1X2 上明显分歧时，S/A 信号最高压到 B，并记录原因。
- 新增 `market_dispersion` 降级：同一盘口下多家 bookmaker 报价离散过大时，S/A 信号最高压到 B，并记录原因；报价数量不足时优先记录 `few_books`，不额外标记市场离散。
- 赔率聚合保留原有均值、去水市场概率和报价数量，同时补充离散度摘要；现有 API/ingest/store 契约不变。
- 页面详情风险提示新增模型分歧和市场报价分歧中文解释。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `192/192 tests passed`，`git diff --check` 通过。
- 安全边界检查：新增公开文案不包含资金建议；安全词扫描只命中既有“不要显示下注金额”的约束/测试。
- 本次未触发 live refresh、未调用 The Odds API、未写入线上 snapshot、未部署、未 push、未 commit。

## 2026-06-10 信号质量防抖实现计划

- 新增实现计划 `docs/superpowers/plans/2026-06-10-signal-quality-guards-implementation.md`。
- 计划将第一阶段拆成 odds dispersion metadata、value grade caps、pipeline quality context、ledger reason 文案和最终验证五个任务。
- 实现范围限定为 `model_disagreement` 与 `market_dispersion` 保守降级，不包含回测框架、不引入新数据源、不改线上调度。
- 本次只写计划与近期记录，未改业务代码、未触发 live refresh、未调用 The Odds API、未部署、未 push、未 commit。

## 2026-06-10 更新规则与变化对比已上线

- 已提交并推送 `b597379 feat: show update cadence and signal changes` 到 `origin/main`。
- 已将 release `b597379` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/b472246` 切到 `/opt/worldcup/releases/b597379`。
- `worldcup.service` 已重启并保持 active；公网 `/healthz` 与服务器本机 `127.0.0.1:8788/healthz` 均返回 ok。
- 公网页面已显示“更新规则”“最近变化”“更新”列和 `S/A/B/C/D` 等级颜色样式；页面仍保留“仅用于研究分析，不构成投注建议”免责声明。
- 公网 `/api/matches` 返回 72 场，未发现资金相关字段；服务器 SQLite 当前 5 条快照，最新 run 为 `20260610T022748Z-live`。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 本次上线只发布代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot；后续新 snapshot 会携带赔率源更新时间字段。

## 2026-06-10 研究台账更新规则与变化对比

- 调度规则已改为：常规 24 小时、赛前 7 天内 12 小时、赛前 3 天内 6 小时、赛前 1 天内 2 小时；低额度优先降频到 24 小时。
- 每场信号行新增“更新”列，优先展示赔率源 `last_update` 的北京时间；无赔率源时间时回退到分析快照更新时间。
- 快照生成会保存每场 `odds_updated_at`，1X2 / 大小球聚合市场会保存 `last_update_at`，后续页面可区分赔率源更新和分析更新。
- 研究台账新增“最近变化”区块，比较当前轮和上一轮同一场同一盘口方向的等级、EV、Edge、模型概率、市场概率和可用聚合赔率变化。
- 等级 `S/A/B/C/D` 已使用不同颜色展示，筛选和展开详情逻辑保持可用。
- SQLite 与 PostgreSQL store 均新增最近快照读取能力；`/preview` 会读取最近两轮，只有一轮时显示暂无上一轮数据。
- 本地验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `177/177 tests passed`，`git diff --check` 通过。
- 浏览器实测临时本地 `/preview`：桌面与移动宽度均渲染正常，无控制台 error/warn；点击信号行可展开详情。
- 本次仅修改本地代码和近期记录，未提交、未 push、未部署、未触发 live refresh、未调用 The Odds API、未写入线上 snapshot。

## 2026-06-10 北京时间与信号详情已上线

- 已提交并推送 `b472246 feat: add expandable signal analysis details` 到 `origin/main`。
- 已将 release `b472246` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/5fe2eef` 切到 `/opt/worldcup/releases/b472246`。
- `worldcup.service` 已重启并保持 active；公网 `/healthz` 与服务器本机 `127.0.0.1:8788/healthz` 均返回 ok。
- 公网页面已显示“分析详情”展开能力、更新时间已显示为北京时间中文格式，原始 UTC 更新时间字符串不再出现在页面。
- 浏览器实测公网第一条信号可点击展开，详情行显示核心判断、盘口方向、模型与市场、EV、等级状态和风险提示。
- 公网 `/api/matches` 返回 72 场，未发现资金相关字段；页面仍保留“仅用于研究分析，不构成投注建议”免责声明。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 本次上线只发布代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-10 信号质量防抖与最小回测设计

- 新增设计文档 `docs/superpowers/specs/2026-06-10-signal-quality-backtest-design.md`。
- 推荐先做 `model_disagreement` 与 `market_dispersion` 两个保守降级条件，再单独推进最小回测框架。
- 明确本阶段不引入 ML、不自动抓伤病新闻、不新增外部 API 调用、不显示下注金额、不改线上调度。
- 后续如进入实现，应先写第一阶段实现计划，优先覆盖模型分歧降级、盘口报价离散度降级、页面 reason 文案和本地测试。
- 本次只写设计与近期记录，未改业务代码、未触发 live refresh、未调用 The Odds API、未部署、未 push、未 commit。

## 2026-06-10 信号行可展开分析详情

- 公开研究台账支持点击单条信号行，在当前行下方展开“分析详情”，再次点击或按 Enter/空格可收起。
- 展开内容来自现有 snapshot 投影数据，包括核心判断、盘口方向、模型与市场、EV、等级状态和风险提示。
- 详情只解释研究信号来源，不新增外部数据源，不展示下注金额、资金、原始 secret、内部 run_id 或上游错误细节。
- 新增投影层与 HTML 交互测试，覆盖详情项、可点击行、详情行、键盘操作钩子。
- 本次仅修改本地代码和近期记录，未提交、未 push、未部署、未触发 live refresh、未调用 The Odds API、未写入线上 snapshot。

## 2026-06-10 最后更新时间改为北京时间显示

- 将公开研究台账顶部和右侧“最后更新”从原始 UTC ISO 字符串改为北京时间中文格式，例如 `2026 年 6 月 8 日 星期一 08:00`。
- 原始 snapshot/API 的 `snapshot_at` 数据契约不变，只调整 HTML 展示层。
- 新增预览页测试，覆盖顶部 meta 与右侧更新时间都显示北京时间，并不再暴露原始 UTC ISO。
- 本次仅修改本地代码和近期记录，未提交、未 push、未部署、未触发 live refresh、未调用 The Odds API、未写入线上 snapshot。

## 2026-06-10 国家队世界杯算法优化代码已上线

- 已提交并推送 `5fe2eef feat: tighten national team signal quality` 到 `origin/main`。
- 已将 release `5fe2eef` 部署到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/17c8c50` 切到 `/opt/worldcup/releases/5fe2eef`。
- `worldcup.service` 已重启并保持 active；公网 `/healthz` 与服务器本机 `127.0.0.1:8788/healthz` 均返回 ok。
- 公网 `/api/matches` 返回 72 场，未发现资金相关字段；首页仍保留“仅用于研究分析，不构成投注建议”免责声明。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 本次上线只发布代码并重启服务，未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot；线上页面会在下一次定时发布生成新 snapshot 后体现新算法评分。

## 2026-06-10 国家队世界杯算法赛前优化

- 收紧强弱悬殊场的平局下限：`elo.draw_min` 从 `0.18` 调整为 `0.12`，避免极端强弱场被硬托出过高平局概率。
- 新增低市场概率保护：当 1X2/大小球选项的去水市场概率低于 `value.longshot_market_prob_max = 0.12` 且原本达到 S/A 时，信号压到 B 并记录 `longshot_uncertainty`。
- 新增 2026 世界杯主办地优势识别：美国、墨西哥、加拿大在本土主办城市比赛时使用有符号 Elo 主场修正，支持主队或客队为东道主的情况。
- 保留现有纯函数模型和本地链路，不引入 ML、不接新数据源、不改线上发布流程。
- 本地验证：先观察新增测试失败，再实现；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `165/165 tests passed`。
- 本次未触发 live refresh、未调用外部 API、未写入线上 snapshot、未部署、未 push。

## 2026-06-10 国际 A 级赛事接入记为代办

- 已完成国际 A 级赛事本地接入设计，并明确当前阶段只覆盖成年男子国家队赛事；世界杯结束后再单独设计俱乐部赛事。
- 该功能已暂停进入 Backlog：不进入实现计划、不做数据源探测、不接本地 snapshot、不接线上发布。
- 由于 2026 世界杯将在北京时间 2026-06-11 开赛，当前优先级切换为国家队世界杯算法优化与赛前验证。
- 后续恢复时先复核数据源可用性、The Odds API / API-Football 额度与身份过滤，再决定是否实施。
- 本次只更新文档和近期记录，未改代码、未触发 live refresh、未调用外部 API、未写入线上 snapshot。

## 2026-06-09 信号时效和兜底缓存降级接线

- 接入 `odds_age_seconds`：研究信号生成时使用 `snapshot_at / observed_at` 与 `OddsQuote.fetched_at` 计算赔率年龄，超过 `odds_max_age_seconds` 时触发 `stale_odds` 并按既有规则压到 B。
- 接入 `stale_sources`：source refresh 失败并使用本地缓存兜底时，信号上下文会带 `depends_on_backup`，逐条信号记录 `unconfirmed_backup` 并按既有规则压到 B。
- `refresh_runner` 在生成 snapshot 前传入 stale source，避免页面只显示整体“过期”但信号仍按新鲜数据评级。
- 新增离线测试覆盖旧赔率压级和 The Odds API 兜底缓存信号原因。
- 本地验证：`161/161 tests passed`。
- 未提交、未 push、未部署、未触发 live refresh、未调用 The Odds API、未写入线上 snapshot。

## 2026-06-09 北京时间显示已部署到线上

- 已部署 release `17c8c50` 到 ECS，`/opt/worldcup/current` 已从 `/opt/worldcup/releases/127dc2b` 切到 `/opt/worldcup/releases/17c8c50`。
- `worldcup.service` 已重启并保持 active；公网 `/` 和 `/healthz` 均返回 HTTP 200。
- 公网页面和 `/preview` 已显示 `开赛 (北京时间)`，旧表头 `开赛 (UTC)` 不再出现；页面仍保留研究免责声明。
- `/api/matches` 返回 72 场；`/api/snapshot/latest` 仍返回 404。
- 服务器 SQLite snapshot 行数保持 2，最新 run 仍为 `20260609T082711Z-live`；本次部署未主动触发 source refresh、未调用 The Odds API、未写入新 snapshot。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- 未 push 远端、未改 Nginx/DNS/证书、未改数据库 schema。

## 2026-06-09 页面开赛时间改为北京时间

- 研究台账展示层将 `kickoff_at_utc` 转换为北京时间后生成日期分组和时间列。
- 原始 `kickoff_at_utc` 字段和排序依据保持 UTC，不改变 snapshot/API 数据契约。
- 表头从 `开赛 (UTC)` 改为 `开赛 (北京时间)`，搜索文本也包含北京时间日期和时间。
- 新增/更新测试覆盖 `2026-06-11T19:00:00+00:00` 显示为 `2026 年 6 月 12 日 星期五`、`03:00`。
- 本地验证：`160/160 tests passed`。
- 未部署、未重启服务、未 push、未调用 live API、未线上写入。

## 2026-06-09 公开界面中文化

- 将公开预览/导出页从英文 `Research Ledger` 改为中文“研究台账”。
- 中文化页面标题、免责声明、筛选控件、表头、空态、方法说明、数据源健康、注意事项、更新时间、信号解释、质量状态和摘要指标。
- 公开页面中的球队名、比赛阶段和分组已做中文展示；原始 snapshot 和 `/api/matches` JSON 契约仍保留源数据英文，避免破坏接口。
- readiness 免责声明检查同步为中文新文案：`仅用于研究分析，不构成投注建议`。
- 本地验证：`160/160 tests passed`。
- 已部署 release `127dc2b` 到 ECS，`/opt/worldcup/current` 指向 `/opt/worldcup/releases/127dc2b`，`worldcup.service` 重启后为 active。
- 公网 HTTPS smoke 通过：`/` 返回中文“研究台账”和中文免责声明，旧英文标题/免责声明不可见；`/preview` 显示中文球队名；`/healthz` 返回 ok；`/api/matches` 返回 72 场；`/api/snapshot/latest` 仍返回 404。
- 服务器 SQLite snapshot 行数保持 2，最新 run 仍为 `20260609T082711Z-live`；本次部署只切换代码并重启服务，未主动触发 source refresh、未写入新 snapshot、未 push 远端。
- systemd journal 最近 10 分钟敏感关键词扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。

## 2026-06-09 已观察到首次自动定时发布

- 观察到 scheduler due 后第一次自动 LaunchAgent 运行。
- `launchd` 运行次数增加到 2，最后一次退出码为 0。
- 任务刷新了 72 场比赛，并将 run `20260609T082711Z-live` 发布到 `https://football.celab.xin/api/ingest/snapshot`。
- The Odds API quota 从 remaining 494 / used 6 变为 remaining 491 / used 9，符合一次赔率刷新消耗 3 credits。
- 服务器 SQLite snapshot 行数从 1 增至 2；服务器最新 run 为 `20260609T082711Z-live`。
- Snapshot `data_quality.source_errors` 和 `data_quality.stale_sources` 为空。
- LaunchAgent 日志敏感扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。

## 2026-06-09 已启用 Launchd 定时发布

- 已安装并加载用户 LaunchAgent `xin.celab.football.scheduled-publish`。
- Plist 路径：`~/Library/LaunchAgents/xin.celab.football.scheduled-publish.plist`。
- 日志路径：`~/Library/Logs/worldcup/scheduled-publish.out.log` 和 `~/Library/Logs/worldcup/scheduled-publish.err.log`。
- LaunchAgent 每 900 秒运行一次，并调用 `worldcup.scheduled_publish --live --endpoint https://football.celab.xin/api/ingest/snapshot`。
- 手动 `launchctl kickstart` 退出码为 0，因 scheduler decision 为 `not_due` 返回 `status=skipped`；quota 和服务器 SQLite 行数未变化。
- plist 与 launchd 日志敏感扫描对 API key、HMAC secret、signature、token、cookie、private-key 标记返回 0。
- kickstart 期间未发生 live refresh、The Odds API 调用或线上写入，因为 scheduler 未到期。

## 2026-06-09 定时发布命令

- 新增 `worldcup.publish`：默认 dry-run 的 snapshot 发布器，可构造签名 ingest 请求，并在输出中脱敏 request body、HMAC secret 和 `X-Worldcup-Signature`。
- 新增 `worldcup.scheduled_publish`：复用现有 scheduler/refresh 流程，只有显式 `--live` 且 scheduler due，或传入 `--force` 时，才发布到 HTTPS ingest。
- 新增发布脱敏、live sender 注入、定时发布跳过、refresh 后发布等测试。
- 本地 dry-run 示例针对 `https://football.celab.xin/api/ingest/snapshot` 通过；当时 scheduler decision 为 `not_due`，因此未上传。
- 验证：`160/160 tests passed`。
- 本步骤未安装 launchd/cron 任务、未运行 live refresh、未调用 The Odds API、未执行线上写入。

## 2026-06-09 Plan 5 Gate C HTTPS 激活

- 已通过 Nginx 为 `football.celab.xin` 激活公网 HTTPS，并反代到 `127.0.0.1:8788` 上的 `worldcup.http_app`。
- 公网路径：`/`、`/preview`、`/api/matches`、`/healthz`、`/api/ingest/snapshot`。
- 原始 snapshot 路径已阻断：`/api/snapshot/latest` 返回 404，`/api/snapshot/` 前缀也被阻断。
- 已为 `football.celab.xin` 签发 Let's Encrypt 证书；证书到期日为 2026-09-07，certbot renewal timer 存在，续期 dry-run 在一次短暂 CAA 查询重试后成功。
- Nginx 备份写入 `/root/nginx-backups/20260609153432-football-gatec` 和 `/root/nginx-backups/20260609153716-football-https`。
- 公网 HTTPS smoke 通过：`/` 和 `/preview` 返回研究免责声明；`/api/matches` 返回 72 场比赛；`/healthz` 返回 `worldcup-analysis`；HTTPS ingest 返回 `duplicate`；HTTP 会跳转 HTTPS。
- 服务器检查：`worldcup.service` active、`nginx` active、SQLite snapshot 行数保持 1，journal/Nginx 敏感关键词扫描返回 0。
- 未执行 scheduled refresh、RDS/PostgreSQL 连接、live source refresh、The Odds API 调用、push 或服务器上的 git 操作。

## 2026-06-09 Plan 5 Gate B 服务器 Smoke

- 未在服务器使用 git，直接将 release `719c5ed` 部署到唯一 ECS 服务器。
- 服务器布局：`/opt/worldcup/releases/719c5ed`、`/opt/worldcup/current`、`/etc/worldcup/.env`、`/var/lib/worldcup/worldcup.db`。
- 新增 systemd `worldcup.service`，使用标准库 `worldcup.http_app`，只监听 `127.0.0.1:8788`。
- Gate B smoke 通过：`/healthz` 返回 ok；签名 ingest 先返回 `stored` 再返回 `duplicate`；`/api/matches` 返回 72 场比赛；`/preview` 返回研究免责声明。
- Smoke 后 SQLite DB 有 1 行 snapshot；journal 敏感关键词扫描未发现 `INGEST_HMAC_SECRET`、`THE_ODDS_API_KEY`、`DATABASE_URL`、signature、cookie、token 或 private-key 字符串。
- 未在服务器执行 git pull/clone、push、域名/DNS 变更、Nginx 公网路由、HTTPS 设置、RDS/PostgreSQL 连接、scheduled refresh、live source refresh 或 The Odds API 调用。

## 2026-06-09 Plan 5 部署 Dry-Run 清单

- 新增 `docs/superpowers/plans/2026-06-09-plan5-deployment-dry-run-checklist.md`。
- 扩展 `docs/ops/local-to-cloud-checklist.md`，加入 Gate A/B/C 拆分、ECS、存储、域名/HTTPS、secret、macmini refresh 和回滚 dry-run 清单。
- 更新 README 下一步：下一次真实动作应明确批准在唯一生产服务器做受控 smoke，而不是直接完整公网激活。
- 本地 dry-run 验证：`156/156 tests passed`；readiness 报告 12 项检查、0 errors、0 warnings；静态导出敏感/公开输出扫描无匹配；PostgreSQL smoke guard 在当前 SQLite 模式下安全返回 `blocked`。
- 按用户的一台服务器设置修正上线路径：Gate B 是同一台生产服务器上的受控 smoke，SQLite 是 MVP 默认存储，RDS/PostgreSQL 是后续可选升级。
- 未部署、未连接 RDS、未改域名/DNS、未改云资源、未调用 live API、未线上写入、未 push、未安装依赖。

## 2026-06-09 Plan 4 研究台账 UI 实现

- 基于本地 snapshot 数据实现首版公开研究台账 UI：`worldcup.ledger` 和 `worldcup.ledger_html`。
- `worldcup.preview` 现在渲染研究台账页面；`worldcup.export` 继承该页面用于 `data/cache/site/index.html`。
- 新增 ledger 投影、预览安全/可访问性、导出契约、移动端表格滚动容器等测试。
- 重新生成已忽略的本地预览产物：`data/cache/preview.html` 和 `data/cache/site/`。
- 桌面和移动端浏览器 QA 通过；移动端通过约束 ledger panel，确保宽表格在容器内横向滚动。
- 收紧静态导出安全：`api/snapshot/latest.json` 使用公开 snapshot 投影，`manifest.json` 不再暴露 `run_id`。
- 本地验证：`156/156 tests passed`。
- 未部署、未 push、未调用 live API、未线上写入、未连接数据库、未改云资源。

## 2026-06-09 Plan 4 UI 设计

- 使用 Product Design 确认首版公开 UI brief：用户应能快速扫描即将进行的世界杯价值信号。
- 选择视觉方向“研究台账”：面向公众的分析台账，包含摘要指标、信号表、方法/数据源健康侧栏和显式注意事项。
- 新增 `docs/superpowers/specs/2026-06-09-plan4-research-ledger-ui-design.md`；未写前端代码、未部署、未 push、未调用 live API、未线上写入。
- 确认研究台账设计进入实现计划，并新增 `docs/superpowers/plans/2026-06-09-plan4-research-ledger-ui-implementation.md`。

## 2026-06-09 Plan 3A / 3B / 3C / 3D

- 新增 `docs/superpowers/specs/2026-06-09-plan3a-fastapi-ecs-design.md`，明确下一阶段先做本地 FastAPI/ECS API 形态，不部署、不改云资源、不切 PostgreSQL。
- 推荐路线：FastAPI thin wrapper 复用现有 HMAC 验签、幂等、SQLite store、只读投影和 preview 逻辑；PostgreSQL 当时作为后续 Plan 3B 通过同一 store boundary 替换。
- 新增 `docs/superpowers/plans/2026-06-09-plan3a-fastapi-ecs-implementation.md`，把 Plan 3A 拆成依赖、FastAPI route tests、thin wrapper、ingest tests、store protocol、文档和最终验证任务。
- 完成 Plan 3A 本地 FastAPI 适配层，复用既有路由契约；未部署 ECS、未 push、未调用 live API、未线上写入。
- 新增 `SnapshotStore` 协议，保留 SQLite 行为，并为后续 PostgreSQL Plan 3B 做准备。
- 修复本地/cache snapshot 生成，使 `worldcup.local_runner` 包含 ingest 所需 run metadata；本地 FastAPI smoke 可 ingest 现有缓存生成的 snapshot。
- 在 `SnapshotStore` 后新增 Plan 3B PostgreSQL store 适配器；API/query/ingest 路径现在支持注入 store，SQLite 仍为默认本地 store。
- PostgreSQL 行为只用 fake connection 测试；未连接真实 RDS/PostgreSQL、未部署、未 push、未线上写入。
- 新增 Plan 3C store 选择接线：FastAPI 和 ingest CLI 默认 SQLite，支持 `--store postgres`，并可从 `.env` 读取 `WORLDCUP_STORE` / `DATABASE_URL`。
- readiness 现在会验证 store 选择且不打印 `DATABASE_URL`；PostgreSQL 模式要求变量名存在，SQLite 模式不要求。
- 新增 Plan 3D PostgreSQL smoke dry-run guard：`worldcup.postgres_smoke` 验证 postgres smoke 前置条件，并只输出脱敏请求元数据。
- smoke guard 不连接 RDS/PostgreSQL、不发送 HTTP、不打印 `DATABASE_URL`、不打印 HMAC secret、不打印 signature、不包含 request body。
- 本地验证：`138/138 tests passed`；`worldcup.readiness` 报告 12 项检查、0 errors、0 warnings。

## 2026-06-09

- 继续本地上线准备：新增 `/healthz` 路由，不读 DB、不依赖 secret，只返回服务存活契约；ASGI 适配层自动复用该路由。
- 新增 `worldcup/secrets.py`，可生成 `INGEST_HMAC_SECRET=<hex>` 供人工写入 `.env`，工具本身不写文件、不记录 secret；新增 `.env.example` 仅列变量名。
- readiness check 增加 `.env.example` 安全检查：模板必须包含必需变量名、值为空，并通过 `.gitignore` 例外进入仓库。
- 本地验证更新为：`104/104 tests passed`。
- 通勤窗口继续低风险本地加固：新增 `docs/superpowers/plans/2026-06-09-commute-local-hardening.md`，明确只做本地 ASGI / 静态导出 / readiness / 文档验证；不部署、不 push、不 commit、不安装依赖、不打真实 live API。
- 新增无依赖 ASGI 适配层 `worldcup/asgi_app.py`，复用 `worldcup.http_app` 路由契约，覆盖 `GET /api/matches` 和 `GET /preview` 的 ASGI 行为测试。
- 新增静态站点/API 导出器 `worldcup/export.py`，可从 `analysis_snapshot.json` 生成 `data/cache/site/index.html`、`api/matches.json`、`api/snapshot/latest.json` 和 `manifest.json`。
- 新增本地 readiness check `worldcup/readiness.py`，只读检查 `.env` 变量名、缓存快照内容、quota ledger、预览/静态导出免责声明和 git ignore 状态；当时真实检查仅缺 `INGEST_HMAC_SECRET`。
- 本地验证更新为：`99/99 tests passed`。

## 2026-06-08

- 完成标准库 HTTP 适配层：新增 `worldcup/http_app.py`，覆盖 `POST /api/ingest/snapshot`、`GET /api/snapshot/latest`、`GET /api/matches`、`GET /preview` 的本地路由契约；不启动服务、不部署。
- 本地验证更新为：`92/92 tests passed`。
- 完成本地上线预览闭环第一版：新增 `worldcup/store.py` SQLite 持久化、`worldcup/ingest_app.py` 本地验签入库、`worldcup/query.py` 只读投影、`worldcup/preview.py` 静态 HTML 预览；`data/local/` 已加入 git ignore。
- 已用当前缓存生成本地预览文件 `data/cache/preview.html`；该目录已被 git ignore。
- 本地验证更新为：`88/88 tests passed`。
- 完成本地服务端 ingest 验签/幂等：新增 `worldcup/ingest_server.py`，支持 HMAC 验签、body hash 校验、snapshot_id 校验、300 秒防重放窗口、内存幂等存储与 duplicate 检测。
- 本地验证更新为：`79/79 tests passed`。
- 完成云端 ingest HMAC dry-run：新增 `worldcup/ingest.py`，可从 snapshot 构造 payload、稳定 `snapshot_id`、body hash、HMAC 签名头和幂等键；默认 dry-run 不发送请求、不展开 body、不打印 secret。
- 本地验证更新为：`74/74 tests passed`。
- 完成调度执行包装：新增 `worldcup/scheduled_refresh.py`，默认 dry-run；`--live` 时先看 scheduler decision，只有 due 才调用 refresh runner，`--force` 可显式覆盖 not_due。
- 本地验证更新为：`71/71 tests passed`。
- 完成低频调度策略与只读 scheduler report：新增 `worldcup/scheduler.py`，支持按上一轮 snapshot、quota ledger 和下一场 kickoff 判断是否 due；默认 dry-run 输出 JSON，不联网、不写状态。
- refresh snapshot 新增 `run` metadata，记录 `run_id`、policy decision、quota、`source_errors`、`stale_sources`；`snapshot_at` 与本轮 `observed_at` 对齐。
- 本地验证更新为：`68/68 tests passed`。
- 完成 refresh fallback policy：source refresh 失败且已有本地缓存时，继续使用上一轮缓存生成快照，并在 `data_quality.source_errors` / `data_quality.stale_sources` 标记来源；新增 The Odds API TLS handshake timeout 离线单测。
- 本地验证更新为：`63/63 tests passed`。
- 执行首次真实 live refresh：openfootball、eloratings、The Odds API 写入 `data/cache/`；The Odds API 返回 72 场，quota ledger 记录 `last=3`、`remaining=494`、`used=6`；重新生成 `data/cache/analysis_snapshot.json`，输出 72 场 match analysis。
- live refresh 首次整链路运行时 The Odds API TLS handshake 超时；随后只重试 The Odds API 端点一次成功，避免重复刷新免费源。
- 新增 `worldcup/refresh_runner.py`：可串联 source refresh → `data/cache/` → analysis snapshot；CLI 默认 dry-run，只有显式 `--live` 才会读取 `.env` 并联网消耗额度。
- 本地验证更新为：`62/62 tests passed`。
- 新增可注入 source 请求层与 quota ledger：openfootball、eloratings、The Odds API 都能通过 fake transport 测试写入缓存；The Odds API 响应头会写入本地 quota ledger。
- `worldcup.local_runner` 新增 `--input-dir` / `build_snapshot_from_cache`，可直接读取同名缓存文件生成分析快照；本轮未实际联网请求 API。
- 本地验证更新为：`61/61 tests passed`。
- 新增本地快照 runner：`worldcup/local_runner.py` 可读取 `data/probe/` 样例并生成 `data/cache/analysis_snapshot.json`；真实样例输出 72 场 match analysis，包含 counts、data_quality、model、market、signals。
- 本地验证更新为：`55/55 tests passed`。
- 补齐单场价值信号输出：`generate_value_signals` 产出 1X2、OU 2.5、AH 主盘口信号；1X2/OU 使用 EV+Edge，AH 使用 settlement EV。
- 本地验证更新为：`53/53 tests passed`。
- 继续 Plan 2：新增 `worldcup/pipeline.py`，将 fixture / odds / Elo 对齐成 `MatchAnalysisInput`，并生成单场 Elo、Poisson、集成 1X2、OU 2.5、1X2/OU 市场聚合输出；真实样例可生成 72 场完整输入。
- 发现并处理样例源差异：`Brazil vs Haiti` 在 openfootball 与 The Odds API 的 kickoff 相差 30 分钟；pipeline 先按时间+队名精确匹配，失败时按唯一队名 pair 兜底，并记录 `time_mismatches`。
- 本地验证更新为：`52/52 tests passed`。
- 启动 Plan 2 collectors：新增纯离线解析层 `worldcup/collectors/`，覆盖 openfootball 赛程、The Odds API 赔率、eloratings Elo 和 team alias；新增 collector 单测与 `data/probe/` 样例 smoke test。
- 本地验证更新为：`49/49 tests passed`。
- 继续执行 Plan 0 赔率源探测：The Odds API key 可用，`soccer_fifa_world_cup` active；`h2h / spreads / totals` 返回 72 场小组赛赔率，单次消耗 3 credits，响应头可用于 quota ledger。
- 更新 `docs/superpowers/data-contract.md`：补齐 The Odds API 端点、字段映射、bookmaker/line 覆盖、quota 行为、team alias 初稿与 Plan 2 输入要求。
- 执行 Plan 0 第一轮数据源探测：openfootball 2026 赛程可用并返回 104 场；API-Football key 有效但 Free plan 不能访问 2026 season 的 fixtures/odds；eloratings `World.tsv` 与 `en.teams.tsv` 可解析国家队 Elo。
- 新增 `docs/superpowers/data-contract.md` 初稿，记录赛程、API-Football、Elo 与赔率备源待探测结论。
- 初始化 git 仓库，提交 `d52ba6c feat: initialize worldcup analysis engine`。
- 完成 Plan 1 引擎核心第一版：Elo、Poisson、赔率去水聚合、亚洲让球 EV、价值分级、变化检测。
- 修正 Plan 0 / Plan 1 中的关键问题：AH 不能走二元 EV、补 `D/ODDS_PENDING` 状态、补完整 market 聚合、修 API-Football 探测决策门。
- 本地验证通过：`41/41 tests passed`。
- 新增项目入口文档：`README.md`、`AGENTS.md`、`CLAUDE.md`、`RECENT_WORK.md`。

## 下一步

- 上线前补齐/确认 `INGEST_HMAC_SECRET`，重新跑 readiness check。
- 明确确认进入 Gate B 后，在唯一生产服务器上先用 SQLite 做受控 smoke，不接定时刷新。
- Gate B 通过后，再单独确认正式域名/HTTPS 和 macmini scheduled refresh。
- The Odds API key 已在聊天截图暴露过；用户已确认不充值，后续按免费额度和缓存兜底设计。
