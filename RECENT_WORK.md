# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

## 2026-06-29 P9.19 HTTP ingest hardening

- 新增 implementation plan：`docs/superpowers/plans/2026-06-29-http-ingest-hardening.md`。
- 加固 `worldcup.http_app` 的 `/api/ingest/snapshot` 边界：非 `application/json` 请求返回 415；声明或实际 body 超过默认 1,000,000 bytes 返回 413；非法 `Content-Length` 与非法 UTF-8 在标准库 handler 进入业务逻辑前被拒绝。
- Ingest 响应新增安全 request id：可信 `X-Request-Id` 会被校验后回传，缺失或不安全时生成本地 uuid；成功和错误响应都带 `X-Request-Id` 与 `Cache-Control: no-store`。HMAC/验签失败改为结构化错误体 `error.code` / `error.request_id`，不回显 raw body、签名、secret、payload 或 header 原文。
- `worldcup.fastapi_app` 保持薄适配器，只透传 `handle_request` 的非 content-type 响应头，不重复实现 HMAC/幂等逻辑。
- 本轮不改模型、不拆 `pipeline.py`、不解除 `club_rating_pending`、不联网、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布、不部署、不改 LaunchAgent。
- 验证：新增红灯先使项目测试为 `611/616 tests passed`；实现后项目标准 `tests/run_tests.py` 返回 `616/616 tests passed`。

## 2026-06-29 P9.18 engineering guardrails

- 新增 implementation plan：`docs/superpowers/plans/2026-06-29-engineering-hardening-guardrails.md`，把外部工程评审转成低风险后续顺序：CI/依赖边界先行，再做 HTTP ingest、source fetch、config/profile 和 pipeline split。
- 调整 `pyproject.toml`：核心包默认无第三方依赖；FastAPI/Uvicorn/HTTPX 移到 `web` extra；`dev` extra 安装 Web 测试依赖和 pytest；`postgres` extra 保持 psycopg。
- 新增 `.github/workflows/ci.yml`：GitHub Actions 使用 Python 3.12，安装 `.[dev]`，运行现有 `tests/run_tests.py`。
- README 同步当前测试状态和可选依赖安装方式。本轮不改模型、不拆 `pipeline.py`、不解除 `club_rating_pending`、不联网、不读取 `.env`、不调用 The Odds API、不消耗 quota、不发布、不部署、不改 LaunchAgent。
- 验证：`git diff --check` 通过；项目标准 `tests/run_tests.py` 返回 `612/612 tests passed`。

## 2026-06-29 P9.17 CSL postmatch runner

- 新增 implementation plan：`docs/superpowers/plans/2026-06-29-csl-postmatch-runner.md`。
- 新增 `worldcup.csl_postmatch_runner`：一条本地-only 命令串起 CSL snapshot history + 本地完赛结果生成 eval CSV、运行 `worldcup.backtest`、再把 market baseline 接入 `csl_pending_gate`。
- Runner 默认写入 ignored 本地输出：`data/local/backtest/csl_2026_eval.csv`、`data/local/backtest/csl_2026_report.json` 和 `data/local/diagnostics/csl_pending_gate_<UTC>.json`；输出摘要只包含计数、sample 和 pending decision，不暴露 raw odds、bookmaker、API key 或 secret。
- 该链路仍然不解除 `club_rating_pending`，只用于赛后研究观察；本轮未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署、未改 LaunchAgent、未 push。
- 验证：TDD 红灯先因 `worldcup.csl_postmatch_runner` 缺失失败；实现后 `tests/test_csl_postmatch_runner.py` 聚焦测试 `2/2` 通过；`git diff --check` 通过；项目标准 `tests/run_tests.py` 返回 `612/612 tests passed`。

## 2026-06-29 P9.16 CSL snapshot archive

- 新增 `worldcup.csl_snapshot_archive`：把已生成的本地 `data/local/diagnostics/csl_live_league_snapshot.json` 校验后归档到 ignored `data/local/diagnostics/csl_history/`，文件名使用 snapshot 自身 `snapshot_at` 生成 UTC stamp，避免手工复制时钟偏差。
- 归档器只读本地 snapshot 并写本地 history；校验 `competition.id=csl_2026`、`snapshot_at` 和非空 `matches`；同内容重复归档返回 `duplicate`，不同内容同名冲突拒绝覆盖。
- CLI 支持 `--dry-run`，输出安全摘要；README 的 CSL Postmatch Eval Loop 已从手工 `mkdir/cp` 改为归档器命令。
- 本轮未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署、未改 LaunchAgent、未 push。
- 验证：新增 TDD 红灯先因 `worldcup.csl_snapshot_archive` 缺失失败；实现后 `tests/test_csl_snapshot_archive.py` 聚焦测试 `7/7` 通过；`git diff --check` 通过；项目标准 `tests/run_tests.py` 返回 `610/610 tests passed`。

## 2026-06-29 P9.15 CSL postmatch eval loop

- 新增 `worldcup.csl_eval_data`：只读本地 CSL snapshot history 与 `data/cache/club_results_csl_2026.csv`，用开球前最后一份 snapshot join 完赛赛果，输出现有 `worldcup.backtest` 可读取的 `data/local/backtest/csl_2026_eval.csv`。
- `worldcup.csl_pending_gate` 新增可选 `--market-report`，从本地 CSL backtest JSON 读取 `markets.1x2.market` 作为市场 baseline；即使 baseline 可用，也保持 `can_lift_club_rating_pending=false`，只进入观察/keep pending。
- README 新增 CSL 赛后评估闭环：归档本地 CSL snapshot、生成 eval CSV、跑 `worldcup.backtest`、把 report 接入 pending gate；准确率判断强调 Brier / Log Loss / calibration / model_matched vs market，而不是只看命中率。
- 本轮未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署、未改 LaunchAgent、未 push。
- 验证：TDD 红灯覆盖 `worldcup.csl_eval_data` 缺失和 `market_report` 未实现；实现后项目标准 `tests/run_tests.py` 返回 `603/603 tests passed`。

## 2026-06-29 P9.14 CSL observation report 与 pending gate

- 新增 `worldcup.csl_observation_report`：从本地 CSL live league snapshot 生成已脱敏观察报告，保留研究免责声明，过滤 raw odds、bookmaker、provider、API key、secret、资金和执行建议等不应暴露内容。
- 新增 `worldcup.csl_pending_gate`：只读本地 `club_results_<competition>.csv`，按日期批量 walk-forward replay club ratings；同一天无开球时间时，rating 与 home-prior baseline 都只使用评估日期之前的信息，避免同日泄漏。
- Pending gate report 固定单一 schema：`sample.total_results`、`decision.can_lift_club_rating_pending` 和顶层 `can_lift_club_rating_pending=false`；历史市场赔率缺失时保持观察/keep pending，不解除 `club_rating_pending`。
- 本轮未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署、未改 LaunchAgent、未提交、未 push。
- 验证：聚焦 `tests.test_csl_pending_gate` + `tests.test_csl_observation_report` 19 个测试通过；项目标准 `tests/run_tests.py` 返回 `598/598 tests passed`；最终 subagent 规格复审通过。

## 2026-06-29 P9.14 CSL observation report and pending gate 计划

- 新增 implementation plan：`docs/superpowers/plans/2026-06-29-csl-observation-report-and-pending-gate.md`。
- 计划覆盖两块本地-only 能力：`worldcup.csl_observation_report` 从最新 CSL league snapshot 生成观察报告；`worldcup.csl_pending_gate` 用 `data/cache/club_results_csl_2026.csv` 做 walk-forward club-rating 评估。
- 计划明确当前历史数据缺少 closing odds / market baseline，因此 pending gate 即使模型健康也必须保持 `can_lift_club_rating_pending=false`，最多支持 `observe_only_no_lift`，不能解除中超正式强信号压制。
- 本轮只写计划和近期记录；未改业务代码、未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布、未部署、未改 LaunchAgent、未提交、未 push。

## 2026-06-29 P9.13 CSL live odds 本地受控刷新

- 经用户确认，执行一次受控 `worldcup.league_odds_refresh --competition csl_2026 --sport-key soccer_china_superleague --live --replace-existing`；读取本地 `.env`，调用 The Odds API，覆盖 ignored cache：`data/cache/theoddsapi_csl_2026_odds.json`。
- 本轮使用 `theoddsapi_secondary`，返回 `events=8`、`quota_last=3`、`quota_remaining=34`、`used=466`，刷新时间为 `2026-06-29T02:32:31.106142+00:00`；未打印 API key、`.env` 值、raw odds、bookmaker 或 price。
- 已用新 cache 跑本地 `worldcup.league_runner`，写入 ignored snapshot / diagnostics：`data/local/diagnostics/csl_live_league_snapshot.json`、`data/local/diagnostics/csl_live_odds_refresh.json`、`data/local/diagnostics/csl_live_league_runner_check.json`。
- Runner 结果：`matches=8`、`club_rating.mode=sample_replay`、`matches_replayed=840`、`teams_rated=22`、`club_alias_unmatched=[]`、`invalid_odds_count=0`、`strong_grades=[]`；`rating_policy=club_rating_pending` 未解除，warnings 保留 `club_rating_pending` / `odds_event_only`。
- 验证：`python3 -m worldcup.ops_check --no-public --no-remote --format summary` 返回 `errors=0`、CSL live odds `ok events=8 fixtures=8 odds_events=8`；本轮未发布线上 snapshot、未部署、未改 LaunchAgent、未 push。

## 2026-06-29 P9.12 推送部署与 live publish

- 已推送 `f978ec9 Harden knockout signal strategy` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/f978ec9`；`/opt/worldcup/current` 已从 `/opt/worldcup/releases/6dc5751` 切到新 release，`worldcup.service` 与 `nginx` 均为 active。
- 已执行一次受控 `worldcup.scheduled_publish --live --force --no-notify --endpoint https://football.celab.xin/api/ingest/snapshot`：新 run 为 `20260629T021939Z-live`，ECS ingest 返回 HTTP 200 / `ingest_status=stored`，snapshot_id 为 `698b8a63e231e288047867fbe7c687e267d69e5c758c04ac45046734afce3f45`。
- 本轮 live refresh 使用 `theoddsapi_secondary`，The Odds API ledger 显示 secondary 剩余 37、used 463；primary 仍为 0。刷新后本地 snapshot 为 15 场，正式强信号仅保留 2 条 S：`Ivory Coast vs Norway` 客胜、`Colombia vs Ghana` 主胜；5 条长赔率 `1X2` raw 强信号已被 `x12_long_odds_candidate_only` 压到 B。
- 部署/发布后公网 smoke：`/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；公网 `/api/matches` 返回 15 场；首页和 `/preview` 保留研究免责声明，`stake` / `下注金额` / `资金` 禁词扫描为空。
- `python3 -m worldcup.ops_check --format summary` 返回 `errors=0`、`warnings=5`；warning 属于既有巡检类提示，不阻断本次上线。本次未改 LaunchAgent、未发送 WxPusher 通知、未 push secret 或 cache。

## 2026-06-29 P9.12 淘汰赛策略降噪与 scores guard

- 新增 implementation plan：`docs/superpowers/plans/2026-06-29-knockout-strategy-hardening.md`。
- 世界杯 `1X2_90min` 正式强信号新增候选化护栏：平局强信号默认降到 B 并记录 `x12_draw_candidate_only`；赔率高于 `quality.x12_official_odds_max`（当前 2.2）的 1X2 强信号降到 B 并记录 `x12_long_odds_candidate_only`。该护栏限定 `soccer_fifa_world_cup`，不影响 CSL league runner。
- 研究台账风险提示已补充上述两个新 reason 的中文说明，dry-run 预览页可读地展示“平局强信号暂列研究候选”和“赔率高于正式强信号上限”。
- 首发链路保持 shadow-only：`lineup_shadow` 不改 active 概率、EV/Edge 或正式等级，继续作为 AH candidate 晋级和 post-information odds 的门槛。
- `worldcup.scores_capture` 在 `2026-06-28T00:00:00Z` 起默认阻断 live scores 捕获，返回 `knockout_score_manual_review_required`，不调用 transport、不写 results；人工确认 90 分钟比分口径后可显式 `--allow-knockout-scores` 放行。`worldcup.daily_eval --live-scores` 同步支持该 opt-in。
- README 已补充淘汰赛 scores 人工确认规则和 1X2 强信号候选化护栏。实现和验证阶段未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未提交、未 push。
- 验证：新增 TDD 红灯覆盖后实现；当前 `tests/run_tests.py` 摘要返回 `579/579 tests passed`（同时输出既有 FastAPI/TestClient deprecation warning 和 ops_check 临时目录 warning）；`git diff --check` 通过。

## 2026-06-24 P9.11 ops_check 本地日报 dry-run 实现

- 新增 implementation plan：`docs/superpowers/plans/2026-06-24-ops-daily-report-dry-run.md`。
- 新增 `worldcup.ops_daily_report`：默认只跑本地 `run_ops_check(public_base_url=None, remote_host=None)`，从已脱敏的 `report` 摘要生成 `local_dry_run` 日报，写入 ignored `data/cache/ops_daily_report_<UTC>.md`；支持 `--format json`。
- 日报 payload 包含 `scope` 全 false、`delivery=skipped/dry_run_no_notification`、研究免责声明、`ops_summary` 和 `csl_live_odds` 摘要；不读取 full raw `local` payload，不输出 raw odds、bookmaker、market、price、URL、API key、HMAC、`.env` 值或原始响应。
- `generated_at` 统一规范化为 UTC `Z`；invalid `--generated-at` 在执行 `ops_check` 前报错；missing/malformed/inconsistent `report`、非法 status、status/count mismatch 或空 CSL report 都降级为 `status=error` 且非零退出，避免把坏巡检伪装成成功日报。
- README 已补充本地 Markdown/JSON 日报 dry-run 用法；本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发通知、未部署、未改 LaunchAgent。
- 验证：`git diff --check` 通过；`tests/test_ops_daily_report.py` direct tests `14/14` 通过；full `tests/run_tests.py` 返回 `572/572 tests passed`；本地 dry-run `python3 -m worldcup.ops_daily_report --generated-at 2026-06-24T08:00:00Z` 写入 ignored `data/cache/ops_daily_report_20260624T080000Z.md`，返回 `status=warn errors=0 warnings=1`，CSL live odds 摘要为 `ok events=8`，敏感/原始赔率关键词扫描无命中。

## 2026-06-24 P9.10 CSL live odds 巡检报告化实现

- `worldcup.ops_check` 新增顶层 `report` 摘要和 `--format summary`，从既有只读 `local.csl_live_odds` 安全字段生成 CSL live odds 日常巡检短报告。
- 短报告展示 cache status、events/fixtures/odds_events、provider/quota 摘要、synthetic/alias/非法赔率 guard、runner status、runner warnings、club rating replay 状态和 runner 强等级残留异常；不输出 raw odds、bookmaker、market、price、URL、API key、HMAC 或 `.env` 值。
- P9.10 不改变 P9.9 issue 计数语义：缺少 live cache 仍为 warning；synthetic、alias drift、非法赔率、runner blocker、runner error、runner strong grades 仍为 error。
- README 已补充日常用法：默认 JSON 含 `report.csl_live_odds`，人工快速巡检可跑 `python3 -m worldcup.ops_check --format summary`；该命令不触发 refresh、不发布、不调用 The Odds API、不消耗 The Odds API quota、不读取或打印 secret；纯本地离线巡检可跑 `python3 -m worldcup.ops_check --no-public --no-remote --format summary`。
- 经用户确认，已对 bundled Python 3.12 执行 `python3 -m pip install -e .`，补齐 `pyproject.toml` 声明的 FastAPI / HTTPX / Uvicorn 测试依赖。
- 本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot、未提交、未 push。
- 验证：新增 report/summary 聚焦测试通过；`tests/test_ops_check.py` `25/25` 通过；只读 `python3 -m worldcup.ops_check --no-public --no-remote --format summary` 输出 CSL live odds `ok events=8`；项目标准 full `tests/run_tests.py` 返回 `558/558 tests passed`；`git diff --check` 通过。

## 2026-06-24 P9.10 CSL live odds 巡检报告化计划

- 新增 implementation plan：`docs/superpowers/plans/2026-06-24-csl-live-odds-ops-reporting.md`。
- 计划目标是在 P9.9 已有只读 `local.csl_live_odds` 检查之上，增加顶层 `report.csl_live_odds` 和 `--format summary`，让日常 `worldcup.ops_check` 一眼看到 CSL live odds cache、alias、synthetic、非法赔率、runner warning/error、club rating replay 和 strong grades 状态。
- 计划保持 P9.9 安全边界：只读本地已脱敏摘要，不读取 `.env`，不调用 The Odds API，不 live，不写 cache，不部署，不改 LaunchAgent，不解除 `club_rating_pending`，不发布 CSL 预览或信号。
- 本轮实际只写计划和近期记录；只读跑过 `python3 -m worldcup.ops_check --no-public --no-remote`，当前 CSL live odds 为 `ok`、`events=8`、`alias_unmatched=0`、`invalid_odds=0`、`synthetic=false`、runner `strong_grades=[]`。

## 2026-06-24 P9.9 CSL live odds 巡检实现

- `worldcup.ops_check` 新增只读 `local.csl_live_odds`，读取 ignored live odds cache / quota / diagnostics：`data/cache/theoddsapi_csl_2026_odds.json`、`data/cache/quota.json`、`data/local/diagnostics/csl_live_odds_refresh.json`、`data/local/diagnostics/csl_live_league_runner_check.json`。
- 巡检复用 `parse_league_odds_events()` 检测 CSL live alias 漂移和非法 decimal odds；缺少 live cache 只计 warning，synthetic marker、alias drift、非法赔率、runner error/blocking warning/strong grades 均计 error。
- 输出改为安全摘要：provider、sport key、诊断 code、grade、path、club alias 等字段使用白名单/固定路径约束；raw odds、bookmaker、market、price、URL、API key、HMAC、`.env` 值和疑似 opaque secret 不进入结果，相关原始问题通过 count 保留。
- 本机只读 `python3 -m worldcup.ops_check --no-public --no-remote` 返回 `ok=true`、`errors=0`；`local.csl_live_odds` 为 `events=8`、`club_alias_unmatched_count=0`、`invalid_odds_count=0`、`has_synthetic_marker=false`、runner `strong_grades=[]`，warnings 仅来自既有本地日志计数。
- 本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot、未提交、未 push。
- 验证：CSL focused ops tests `12/12` 通过；`tests/test_ops_check.py` `20/20` 通过；项目标准 full `tests/run_tests.py` 返回 `553/553 tests passed`；`git diff --check` 通过；subagent spec/code review 最终通过。

## 2026-06-24 S 信号 AH candidate 收紧推送与部署

- 已提交并推送 `6dc5751 fix: tighten AH S signal promotion guards` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/6dc5751`；`/opt/worldcup/current` 已从 `/opt/worldcup/releases/72e9540` 切到新 release，`worldcup.service` 与 `nginx` 均为 active。
- 部署前验证：`git diff --check` 通过；`tests/test_odds_trend.py` 直跑 14 个测试通过；pipeline 聚焦 3 个护栏测试通过；bundled Python 一次性 runner 跑过除本机缺 `fastapi` 的 `test_fastapi_app.py` 外 393 个无 fixture 测试，`failures=0`。标准 `tests/run_tests.py` 仍在导入 `test_fastapi_app.py` 时因当前 runtime 缺少 `fastapi` 中断。
- 部署后公网 smoke：`/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；`/api/matches` 当前返回 25 场，公网 `/api/finished` 汇总 `match_count=44`、`sample_too_small=false`；页面保留研究免责声明，资金/下注禁词扫描为 0。
- `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`、`warnings=4`；warning 来自既有本地日志和远端 Nginx 历史计数，不阻塞本次上线。远端最近 10 分钟 `worldcup.service` 关键词扫描无 error 或 secret-like 命中。
- 本次部署只切换 S 信号晋级护栏代码并重启服务，未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布新 snapshot、未改 LaunchAgent。研究边界不变：不构成投注建议，不输出资金或执行建议。

## 2026-06-24 S 信号 AH candidate 晋级收紧

- 复盘 Portugal vs Uzbekistan 的正式 S 信号失效：问题信号为 `AsianHandicap_90min away_+2.5`，不是葡萄牙胜方向；盘口方向显示热门葡萄牙增强，但原 AH movement 逻辑允许仅凭受让方赔率缩短把候选升为正式 S。
- 收紧 `worldcup.odds_trend`：AH movement 若盘口方向明确反向，则赔率缩短不能单独构成 `supports_signal=true`；带 `extreme_favorite_handicap` 的 AH candidate 不再允许晋级正式 S/A。
- 收紧 `worldcup.pipeline`：`extreme_favorite_handicap` 进入 candidate hard veto，源头不再生成 `S-candidate`。
- 新增回归测试覆盖“盘口向热门增强但受让方赔率缩短不晋级”和“极端热门逆向 AH 候选不晋级”；保留正常同向 AH candidate 可晋级的既有测试。
- 验证：新增红灯先失败；实施后 `tests/test_odds_trend.py` 14 个测试通过，pipeline 聚焦用例通过；bundled Python 一次性 runner 跑过除本机缺 `fastapi` 的 `test_fastapi_app.py` 外 528 个测试，`failures=0`；标准 bundled `tests/run_tests.py` 当前被环境缺少 `fastapi` 阻塞，系统 Python 3.9 又不支持项目 `dict | None` 语法。
- 本轮未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot。研究边界不变：不构成投注建议，不输出资金或执行建议。

## 2026-06-24 P9.9 CSL live odds 巡检计划

- 新增 implementation plan：`docs/superpowers/plans/2026-06-24-csl-live-odds-ops-check.md`。
- 计划目标是在 `worldcup.ops_check` 增加只读 `local.csl_live_odds` 检查，读取 ignored live odds cache 与 diagnostics，复用 `parse_league_odds_events()` 检测 CSL live alias 漂移、synthetic marker、非法 decimal odds 和 runner 诊断 blocker。
- 计划明确缺少 live odds cache 只计 warning，避免干净机器或尚未执行 live 的环境失败；真实 alias drift、synthetic cache、脏赔率、`club_rating_missing` 等状态才计 error。
- 本轮只写计划和近期记录，未改业务代码、未执行 live refresh、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上 snapshot。

## 2026-06-24 P9.8 CSL live odds fetch 与 live alias 补丁

- 经用户明确确认，执行 `worldcup.league_odds_refresh --competition csl_2026 --sport-key soccer_china_superleague --cache-dir data/cache --quota-path data/cache/quota.json --live`；读取本地 `.env`，调用 The Odds API，写入 ignored cache：`data/cache/theoddsapi_csl_2026_odds.json`。
- Live fetch 返回 `status=fetched`、`events=8`、`theoddsapi_provider=theoddsapi_secondary`、`quota_remaining=248`、`quota_last=3`；安全摘要写入 ignored diagnostics：`data/local/diagnostics/csl_live_odds_refresh.json`，确认 `has_synthetic_marker=false`。
- 首次 live runner 发现真实 The Odds API 队名存在未覆盖别名，例如 `Shanghai SIPG FC`、`Beijing FC`、`Shandong Luneng Taishan FC`；按 TDD 补充 `csl_2026` live alias 测试和最小 alias 表，不放宽 unknown club fallback。
- 重跑 live runner 写入 ignored diagnostics：`data/local/diagnostics/csl_live_league_snapshot.json` 和 `data/local/diagnostics/csl_live_league_runner_check.json`；结果为 `counts.matches=8`、`club_alias_unmatched=[]`、`invalid_odds_count=0`、`club_rating.mode=sample_replay`、`matches_replayed=840`、`teams_rated=22`、`sample_too_small=false`、`errors=[]`。
- `rating_policy=club_rating_pending` 未改变；warnings 保留 `club_rating_pending` / `odds_event_only`，无 `club_rating_missing`，56 个 signals 没有 S/A final grades。本轮未部署、未改 LaunchAgent、未发布线上 snapshot、未打印 API key 或 `.env` 值。
- 验证：`tests/collectors/test_club_aliases.py` 单文件 `8/8` 通过；项目标准 full `tests/run_tests.py` 返回 `538/538 tests passed`；`git diff --check` 通过。

## 2026-06-23 P9.8 CSL live odds refresh 计划与 code-only 实现

- 新增 implementation plan：`docs/superpowers/plans/2026-06-23-csl-live-odds-refresh.md`。
- `worldcup.sources.theoddsapi` 已新增通用 `build_odds_url()` / `fetch_odds_for_sport()`，原 `build_worldcup_odds_url()` / `fetch_worldcup_odds()` 保持兼容并委托到通用实现；非 legacy key slot 仍同步写 `theoddsapi` legacy quota alias。
- 新增 guarded CLI：`worldcup.league_odds_refresh`。默认 dry-run 不读取 `.env`、不调用 transport、不写 odds cache；CSL 仍要求显式 `--sport-key soccer_china_superleague`，未修改 `worldcup/competitions.py`，未解除 `club_rating_pending`。
- Dry-run 命令返回 `status=dry_run`、`cache_exists=false`、`target_cache_path=data/cache/theoddsapi_csl_2026_odds.json`；确认默认 cache 仍不存在，synthetic backup 仍只保留在 ignored diagnostics：`data/local/diagnostics/theoddsapi_csl_2026_odds.synthetic_smoke.json`。
- Live fetch 尚未执行，仍需单独确认后才会读取 `.env`、调用 The Odds API、消耗 quota、写 `data/cache/theoddsapi_csl_2026_odds.json` 和 live diagnostics；本轮未部署、未改 LaunchAgent、未 push、未打印 API key 或 `.env` 值。
- 验证：新增 source 单文件测试 `4/4` 通过，新增 league refresh 单文件测试 `5/5` 通过；项目标准 full `tests/run_tests.py` 返回 `537/537 tests passed`；`git diff --check` 通过。

## 2026-06-23 P9.7 synthetic odds cache 清理

- 已将 P9.7 synthetic smoke odds 从 runner 默认路径 `data/cache/theoddsapi_csl_2026_odds.json` 移出，备份到 ignored diagnostics：`data/local/diagnostics/theoddsapi_csl_2026_odds.synthetic_smoke.json`。
- 当前默认 CSL odds cache 路径为空，后续真实 CSL runner 不会误读 synthetic odds；保留 diagnostics 备份仅用于追溯 P9.7 wiring smoke，不是真实市场数据。
- 本轮未改业务代码、未联网、未调用 The Odds API、未读取 `.env`、未消耗 quota、未部署、未改 LaunchAgent、未 push。

## 2026-06-23 P9.7 CSL league runner synthetic odds smoke

- 新增 implementation plan：`docs/superpowers/plans/2026-06-23-csl-league-runner-synthetic-odds-smoke.md`；按计划写入 ignored synthetic odds cache：`data/cache/theoddsapi_csl_2026_odds.json`，并明确标记 `_synthetic_smoke=true` / `Local wiring smoke only; not real odds.`。
- 本地 runner smoke 成功：`python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/local/diagnostics/csl_league_analysis_snapshot_synthetic_smoke.json --club-rating-min-matches 300 --snapshot-at 2026-06-23T12:00:00+00:00` 写出 1 场 ignored diagnostics snapshot。
- Smoke 诊断写入 ignored 文件：`data/local/diagnostics/csl_league_runner_synthetic_smoke.json`；结果为 `club_rating.mode=sample_replay`、`matches_replayed=840`、`teams_rated=22`、`skipped_rows=0`、`sample_too_small=false`、`errors=[]`，`club_alias_unmatched=[]`、`invalid_odds_count=0`、`rating_policy=club_rating_pending`，fixture 为 Shanghai Port vs Shandong Taishan，Elo 为 home 1664 / away 1631。
- `data_quality.warnings` 保留 `club_rating_pending` 和 `odds_event_only`；snapshot 中 7 个 signals 没有 S/A final grades，只有 wiring smoke 意义，不代表真实市场数据、不证明 value signal。
- 本轮未改业务代码、未联网、未调用 The Odds API、未读取 `.env`、未消耗 quota、未部署、未改 LaunchAgent、未 push。

## 2026-06-23 P9.6 中超 replay candidate 本地安装

- 新增 implementation plan：`docs/superpowers/plans/2026-06-23-csl-replay-candidate-local-install.md`；按计划把 P9.4 已验证的 ignored replay candidate：`data/local/diagnostics/csl_results_replay_candidate.csv`，安装到本地 ignored cache：`data/cache/club_results_csl_2026.csv`。
- 安装前 preflight 通过：candidate 840 行，2023/2024/2025 各 240 行、2026 120 行，`neutral=12`，重复 key 数为 0；直接 replay 返回 `parsed_results=840`、`matches_replayed=840`、`teams_rated=22`。
- 正式 cache contract 验证通过：`club_rating.mode=sample_replay`、`matches_replayed=840`、`teams_rated=22`、`skipped_rows=0`、`sample_too_small=false`、`errors=[]`；诊断写入 ignored 文件 `data/local/diagnostics/csl_club_rating_install_check.json`。
- `league_runner` 本地 smoke 按计划跳过：当前不存在 `data/cache/theoddsapi_csl_2026_odds.json`，未联网补抓、未调用 The Odds API；`club_rating_pending` 未解除，`worldcup/competitions.py` 未修改。
- 本轮未改业务代码、未读取 `.env`、未消耗 quota、未部署、未改 LaunchAgent、未 push。验证：full `tests/run_tests.py` with bundled Python 3.12 plus existing user-site FastAPI path returned `530/530 tests passed`；`git diff --check` passed。

## 2026-06-23 P9.4 中超赛果 full local sample 验收

- 按 P9.4 计划在用户确认后只读公开源，扩展 `sevenm` primary 与 `cfl-official` check 的 CSL 2023-2026 finished rows；样例与诊断只写入 ignored 本地路径：`data/probe/`、`data/local/diagnostics/`。
- Full probe 返回 `probe_status=0`，`valid_finished_matches=840`，`manual_review_required=0`，`team_alias_unmatched=[]`，`score_mismatches=0`，`missing_in_primary=0`，`degraded_candidates=0`。
- `pending_gate.can_enter_replay=true`，已生成 ignored replay candidate：`data/local/diagnostics/csl_results_replay_candidate.csv`；`pending_gate.can_lift_club_rating_pending=false`，未安装 `data/cache/club_results_csl_2026.csv`。
- 本轮未改业务代码、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未发布线上、未 push。
- 验证：full `tests/run_tests.py` with bundled Python 3.12 plus existing user-site FastAPI path returned `530/530 tests passed`; `git diff --check` passed.

## 2026-06-23 P9.5 CSL alias gate expansion

- Expanded strict `csl_2026` alias coverage for verified 2023-2026 CFL official / 7M source names, including historical clubs and source Chinese names that blocked P9.4 full-sample parsing.
- Preserved `match_known_club_alias()` strict behavior: unknown clubs still do not fall back to slugified names and remain blocked from replay.
- P9.4 proof probe remains clean: `valid_finished_matches=8`, `manual_review_required=0`, `team_alias_unmatched=[]`, `score_mismatches=0`, `degraded_candidates=0`, `can_enter_replay=false`, `can_lift_club_rating_pending=false`, `pending_reasons=['valid_finished_matches_below_300']`.
- This did not create `data/local/diagnostics/csl_results_replay_candidate.csv`, did not install `data/cache/club_results_csl_2026.csv`, did not call The Odds API, did not read `.env`, did not deploy, did not update LaunchAgent, and did not lift `club_rating_pending`.
- Verification: focused collector tests passed; full `tests/run_tests.py` with bundled Python 3.12 plus existing user-site FastAPI path returned `530/530 tests passed`; `git diff --check` passed. The raw bundled-Python command remains blocked in this local environment by missing `fastapi`.

## 2026-06-23 P9.4 中超赛果 proof sample 与 alias blocker

- 新增 implementation plan：`docs/superpowers/plans/2026-06-22-csl-results-sample-acquisition.md`；选定 `sevenm` 为 primary、`cfl-official` 为 check，样例与诊断只写入 ignored 本地路径：`data/probe/`、`data/local/diagnostics/`。
- 已保存 2023-2026 每季 2 场 finished proof sample：primary/check 各 8 行；`worldcup.csl_results_probe` 返回 `probe_status=0`、`valid_finished_matches=8`、`manual_review_required=0`、`score_mismatches=0`、`degraded_candidates=0`。
- 质量门槛保持关闭：`pending_gate.can_enter_replay=false`，原因是 `valid_finished_matches_below_300`；`pending_gate.can_lift_club_rating_pending=false`；未生成 `data/local/diagnostics/csl_results_replay_candidate.csv`，也未安装 `data/cache/club_results_csl_2026.csv`。
- full sample expansion 已按计划暂停：CFL 全量只读扫描确认当前 `csl_2026` alias 表缺少历史/现役若干名称，例如 `Cangzhou Mighty Lions`、`Dalian Pro`、`Nantong Zhiyun`、`Shenzhen`、`Henan`、`Zhejiang`、`Chongqing Tonglianglong`、`Liaoning Tieren`；下一步需要单独 alias-update plan，不能在 P9.4 偷改 alias。
- 本轮未改业务代码、未读取 `.env`、未调用 The Odds API、未消耗 quota、未部署、未改 LaunchAgent、未写线上、未提交/推送。验证：`tests/run_tests.py` 返回 `528/528 tests passed`。

## 2026-06-22 P9.3 中超历史赛果来源与清洗实现

- 新增严格 CSL alias gate：`match_known_club_alias()` 只接受 competition-scoped 已知别名，未知俱乐部不再静默 slugify 进入清洗链路。
- 新增 `worldcup.collectors.csl_results`：解析本地 2023-2026 CSL 样例、阻断未知 alias/非法比分/日期/状态/重复场次，按 `match_key` 双源校验并输出质量门槛诊断与 replay candidate CSV。
- 新增 `worldcup.csl_results_probe`：只读本地 CSV/JSON 样例，写 `data/local/diagnostics/csl_results_source_probe.json`，只有本地 gate 允许时才可选写 replay candidate。
- final review 后补强 duplicate blocking gate：primary/check 任一来源出现同 `match_key` 重复/冲突行时进入 `manual_review_required`，即使其他四季双源覆盖通过，也不能写 replay candidate。
- 本轮不接 `league_runner`，不解除 `club_rating_pending`，不联网、不读取 `.env`、不消耗 The Odds API quota、不部署、不改 LaunchAgent。
- 关键提交：`0b598ac`、`4b22de8`、`5ffe210`、`4c02d07`、`d0c9639`、`ed7cd43`、`5d2905b`、`2a670b5`、`500cb35`；文档收尾和 final review 修复在本分支后续本地提交中。
- 目标验证：`tests/collectors/test_club_aliases.py` 6/6、`tests/collectors/test_csl_results.py` 20/20、`tests/test_csl_results_probe.py` 7/7 均通过；合入 lineups baseline repair 后，隔离 worktree 标准入口 `tests/run_tests.py` 全量通过。

## 2026-06-22 Lineups baseline repair

- 将主工作区已验证但未提交的首发链路纳入隔离分支：`worldcup.collectors.lineups`、`worldcup.collectors.fifa_lineups`、`worldcup.lineups_refresh`、`worldcup.pre_match_runner`、`worldcup.pre_match_launch_agent`、`worldcup.lineup_audit`、`worldcup.shadow_backfill_diagnostics` 与对应 tests。
- 补齐已跟踪代码依赖的 lineup / post-information odds / shadow backfill 类型与逻辑，解决 clean checkout 下 `worldcup.collectors.lineups` 缺失导致标准测试无法启动的问题。
- 本轮只整理本地代码与测试基线，未执行 live lineups、未执行 live refresh、未消耗 The Odds API quota、未发通知、未改已安装 LaunchAgent、未部署、未推送。
- 验证：标准入口 `PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `498/498 tests passed`。

## 2026-06-22 P9.3 中超历史赛果来源与清洗设计

- 新增设计文档：`docs/superpowers/specs/2026-06-22-csl-results-source-cleaning-design.md`。
- 已确认首版覆盖 `2023-2026` 中超联赛赛果，采用“双源交叉校验”：开源结构化主源 + 官方/权威校验源。
- 设计只允许 Phase 0 公开源只读探测，不使用密钥、不接生产链路、不抓真实数据入库、不写线上、不部署、不更新 LaunchAgent。
- 设计定义进入 `club_rating` replay 前的质量门槛，并明确 P9.3 不解除 `club_rating_pending`，不输出下注金额或执行建议。

## 2026-06-22 P9.2 中超 Club Rating 本地基线

- 新增 `worldcup.club_rating`：只读本地 `data/cache/club_results_<competition_id>.csv`，按 competition 独立解析、canonicalize 俱乐部名称并重放 Elo-style rating。
- `worldcup.league_runner` 现在会尝试加载 club rating；缺文件、样本不足、CSV 无效或 fixture 球队缺 rating 时回退到 1500 占位，并在 `data_quality.club_rating` / `data_quality.warnings` 标记。
- P9.2 不接真实中超历史数据源、不联网、不消耗 The Odds API quota、不部署、不更新 LaunchAgent、不改 `csl_2026.rating_policy`，强信号仍按 `club_rating_pending` 降级。
- 验证：最终标准入口 `PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `497/497 tests passed`；`git diff --check` 通过；空 cache runner 按预期因缺少本地 odds cache 失败且未联网；临时 odds + club results cache smoke 输出 `club_rating.mode=sample_replay`、Elo home 1516 / away 1484。

## 2026-06-22 P9.1 俱乐部联赛本地 MVP 工作流

- 新增俱乐部联赛 implementation plan：`docs/superpowers/plans/2026-06-22-domestic-league-adapters.md`。
- 已实现 Phase 0/1/2 本地基础：competition registry、sports catalog probe、club aliases、league odds adapter、World Cup competition block、CSL local league runner、ledger competition labels/filter。
- final review 后补齐 CSL league snapshot 的 `invalid_odds_count` / `invalid_odds_examples` 审计，避免脏赔率只被隔离但不进入 `data_quality`；`worldcup.league_runner` CLI 同时支持 `--competition` 与 `--competition-id`。
- 当前未联网、未消耗 The Odds API quota、未上线、未部署、未提交/推送；任何 live odds 探测、scheduled publish、ECS ingest 或 LaunchAgent 更新都需要单独确认。
- 验证：最终标准入口 `PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `485/485 tests passed`；`git diff --check` 通过；空缓存 `python3 -m worldcup.league_runner --competition csl_2026` 按预期因缺少 `theoddsapi_csl_2026_odds.json` 本地失败，未联网。

## 2026-06-22 P9 俱乐部联赛多联赛接入设计

- 新增 `docs/superpowers/specs/2026-06-22-domestic-league-adapters-design.md`，明确中超作为首个俱乐部联赛 adapter，英超作为第二个 adapter 验证，西甲/德甲/意甲/法甲作为后续平滑扩展约束。
- 用户已确认该设计；新增 implementation plan：`docs/superpowers/plans/2026-06-22-domestic-league-adapters.md`，覆盖 Phase 0 sports key 只读探测、Phase 1 多联赛底座、Phase 2 中超本地 MVP。
- 设计建议先做 competition registry、snapshot `competition` block、The Odds API sports key 只读探测、俱乐部 alias 和中超未来 7-14 天本地 snapshot。
- 设计明确俱乐部联赛不能复用国家队 Elo；中超初期若缺少 `club_rating`，强信号必须降级或仅作为观察/候选，后续再用历史赛果重放 Elo 建立 `club_rating`。
- 本轮只写设计文档和近期记录；未改代码、未联网探测、未执行 live refresh、未消耗 The Odds API quota、未改 LaunchAgent、未发布线上 snapshot、未部署、未提交/推送。

## 2026-06-22 P8.14 ops_check 增加 refresh guard 巡检

- `worldcup.ops_check` 的 `local.pre_match.wiring` 新增 `has_refresh_guard`，用于显示 pre-match LaunchAgent 是否带 `--refresh-guard`。
- error 规则改为只拦截裸 `--live-refresh`：如果 `has_live_refresh=true` 且 `has_refresh_guard!=true`，巡检计入 error；带 guard 的未来 live-refresh 配置不因该项误报。
- 本轮只改本地巡检代码、测试和文档，未改已安装 LaunchAgent，未 reload/kickstart launchd，未执行 live refresh，未消耗 The Odds API quota，未发通知，未发布线上 snapshot。
- TDD 覆盖：先新增裸 live-refresh 报错、带 guard live-refresh 通过、wiring 输出 `has_refresh_guard` 的红灯测试，实施后标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `464/464 tests passed`。

## 2026-06-22 P8.13 增加 post-lineup refresh guard

- `worldcup.pre_match_runner` 新增 `--refresh-guard` 与 `--min-refresh-quota`：只有出现 `newly_confirmed > 0` 时，才会调用 scheduled refresh 的 dry-run 决策，返回 `guard.status`、quota、policy reason 和 `next_due_at`。
- guard 默认不刷新 odds、不消耗 The Odds API quota；如果同时启用 `--refresh-after-lineups --live-refresh`，且 quota 未知或低于 `--min-refresh-quota`，runner 返回 `post_information_refresh_blocked`，不会调用 live refresh。
- `worldcup.pre_match_launch_agent --allow-live-refresh` 生成的 plist 草案现在会自动带 `--refresh-guard`；本轮未改已安装的 `~/Library/LaunchAgents/xin.celab.football.pre-match.plist`，未 reload/kickstart launchd。
- dry-run smoke：`python3 -m worldcup.pre_match_launch_agent --allow-live-refresh` 输出 ProgramArguments 包含 `--refresh-guard --refresh-after-lineups --live-refresh`，`status=dry_run`、`loaded=false`；`python3 -m worldcup.pre_match_runner --refresh-guard` 在无新 confirmed lineup 时返回 `post_information_refresh.status=skipped`。
- 本轮未执行 live refresh、未消耗 The Odds API quota、未发送通知、未发布线上 snapshot、未部署、未提交/推送。
- TDD 覆盖：先新增 refresh guard dry-run、quota 耗尽阻断 live refresh、live-refresh plist 生成带 guard 的红灯测试，实施后标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `463/463 tests passed`。

## 2026-06-22 P8.12 ops_check 增加 pre-match / lineup audit 巡检

- `worldcup.ops_check` 新增 `local.pre_match`：读取 `xin.celab.football.pre-match` LaunchAgent、扫描 `pre-match.out.log` / `pre-match.err.log`、读取 `data/local/diagnostics/lineup_audit.json` 摘要。
- `local.pre_match.wiring` 显示 `--live-lineups`、`--write-lineups`、`--notify-missing`、`--notify-audit`、`--refresh-after-lineups`、`--live-refresh` 是否存在；若 pre-match plist 误带 `--live-refresh`，巡检会计入 error。
- CLI 新增 `--pre-match-launch-agent`、`--pre-match-log`、`--lineup-audit` 参数；默认仍只读本机文件，不触发 FIFA 抓取、不发通知、不刷新 odds、不消耗 The Odds API quota。
- 本机只读 smoke：`python3 -m worldcup.ops_check --no-public --no-remote` 返回 `ok=true`、`errors=0`、`warnings=3`；`local.pre_match.wiring.has_notify_audit=true`、`has_live_refresh=false`，lineup audit 摘要为 `confirmed_lineups=10`、`captured_before_kickoff=0`、`entered_snapshot=5`、`post_information_odds_available=5`。
- TDD 覆盖：先新增 pre-match 巡检输出和误带 `--live-refresh` 报错测试，确认失败后实现；标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `461/461 tests passed`。

## 2026-06-22 P8.11 更新 pre-match LaunchAgent 启用审计通知

- 经用户确认，已重写并重新加载 `~/Library/LaunchAgents/xin.celab.football.pre-match.plist`。
- 当前 launchd 实际参数为 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing --notify-audit`。
- 当前 plist 与 launchd 均不包含 `--refresh-after-lineups` / `--live-refresh`，所以不会自动触发 The Odds API odds refresh，不会消耗 odds quota。
- 重新加载步骤：`launchctl bootout gui/501 ...` 成功，`launchctl bootstrap gui/501 ...` 成功；reload 后 `launchctl print` 显示 `run interval = 300 seconds`、`runs = 0`。
- 未手动 `kickstart`，避免在当前时刻额外触发真实通知；新版任务会在下一次 300 秒轮询自然生效。
- 本地非联网 dry-run 验证：`python3 -m worldcup.pre_match_runner --notify-audit` 输出 `lineup_audit.notification.status=skipped`、`reason=no_actionable_lineup_audit_issues`，审计摘要为 `confirmed_lineups=10`、`captured_before_kickoff=0`、`entered_snapshot=5`、`post_information_odds_available=5`。
- 本轮未执行 live refresh、未消耗 The Odds API quota、未发布线上 snapshot、未部署、未提交/推送。

## 2026-06-22 P8.10 官方首发链路审计通知

- `worldcup.lineup_audit` 新增 `send_lineup_audit_notification()` 与 CLI `--notify`：只对开赛前仍存在的 `captured_without_snapshot_input` / `captured_without_post_information_odds` 发一次性 WxPusher 通知。
- 通知去重复用 `data/local/lineups_missing_notifications.json`，新增 `audit_sent` 命名空间；不会覆盖既有缺首发通知的 `sent` 状态。
- `worldcup.pre_match_runner` 新增显式 `--notify-audit`：可在首发轮询后跑本地审计通知，但不触发 The Odds API refresh，不消耗 quota。
- `worldcup.pre_match_launch_agent` 生成的新 plist 默认包含 `--notify-audit`；本轮未重写、未 reload、未 kickstart 当前已安装的 `~/Library/LaunchAgents/xin.celab.football.pre-match.plist`。
- 最新非通知审计：`confirmed_lineups=10`、`captured_before_kickoff=0`、`entered_snapshot=5`、`post_information_odds_available=5`；新增首发仍是开赛后抓到，不能当作赛前增强信号样本。
- 本轮未发送真实通知、未执行 live refresh、未消耗 The Odds API quota、未发布线上 snapshot、未部署、未提交/推送。
- 验证：新增通知 RED 测试先因缺少 `send_lineup_audit_notification` 失败；实施后标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `460/460 tests passed`。

## 2026-06-21 P8.9 官方首发链路只读审计

- 新增 `worldcup.lineup_audit`：只读本地 `data/cache/lineups_wc2026.json`、最新 `analysis_snapshot.json`、`data/local/history/snapshot_*.json` 和 `data/local/lineups_missing_notifications.json`。
- 报告逐场输出：官方首发是否确认、确认时间距离开赛分钟数、是否开赛前抓到、是否进入 snapshot、是否已有 post-information odds、强信号数量和问题 flags。
- 汇总输出：confirmed lineups、开赛前抓到数、进入 snapshot 数、post-information odds 数、抓到但未进 snapshot 数、抓到但未有 post-information odds 数。
- CLI 默认输出 `data/local/diagnostics/lineup_audit.json`；本工具不联网、不刷新 odds、不消耗 The Odds API quota、不改 LaunchAgent、不发布线上 snapshot。
- 当时本地审计结果：`confirmed_lineups=6`、`captured_before_kickoff=0`、`entered_snapshot=3`、`post_information_odds_available=3`；说明当时首发缓存均为开赛后抓到，不能当作赛前增强信号样本。
- 验证：新增审计测试先因缺少 `worldcup.lineup_audit` 红灯，实施后标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `457/457 tests passed`；`python3 -m worldcup.lineup_audit` 输出 `status=ok`。

## 2026-06-21 P8.8 增加 T-35 首发确认锚点

- 根据赛前首发通常在开赛前约 60 分钟公布、T-30 更稳的判断，`worldcup.scheduler` 的 `policy_version` 升为 `free-tier-v3`。
- 临赛赔率刷新锚点从 T-12h / T-6h / T-90 / T-55 / T-25 扩展为 T-12h / T-6h / T-90 / T-55 / T-35 / T-25；T-35 的 `policy_reason=pre_35m_lineup_confirm`，用于首发确认主检查。
- 低额度（≤30）保留关键锚点时同步保留 T-35：T-90 / T-55 / T-35 / T-25。
- `worldcup.lineups_refresh` 默认缺首发通知窗口从 90 分钟缩短为 35 分钟；lineups-only LaunchAgent 仍每 300 秒轮询，但只有开赛前 35 分钟内仍未抓到官方首发时才发缺失通知。
- 页面更新规则说明同步显示 T-35；本轮只改本地调度/通知默认值、测试和文档，未执行 live refresh，未消耗 The Odds API quota，未改 LaunchAgent，未发布线上 snapshot，未部署，未提交/推送。
- 验证：新增调度 RED 测试和缺首发通知窗口 RED 测试均先失败后通过；标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `455/455 tests passed`。

## 2026-06-20 P8.7 修正首发上下文跨源编号匹配

- 发现 FIFA public API 的 `source_match_no` 与当前 openfootball / snapshot 的 `source_match_no` 不是同一套编号：本地 confirmed cache 中 `29` 是 Brazil vs Haiti，而当前 snapshot 中 `29` 是 Curaçao vs Ivory Coast。
- 修正 `worldcup.pipeline.build_match_inputs()` 的首发绑定规则：`source_match_no` 只作为候选；只有同时匹配双方 canonical team 和 UTC kickoff 时才可使用，否则回退到 team + kickoff key，仍不匹配则不挂 `lineup_context`。
- 新增回归测试覆盖“编号相同但球队/时间不同不能挂首发；编号不同但球队/时间相同可以挂首发”的场景。
- 当前缓存内存重建验证：`lineup_contexts=2`、`matches=40`、`lineup_matches=0`，`source_match_no=29` 的 Curaçao vs Ivory Coast 没有被错挂 Brazil vs Haiti 首发。
- 验证：经用户确认后执行 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip install -e .` 恢复 bundled Python 测试依赖；标准 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `453/453 tests passed`，`git diff --check` 通过。
- 本轮只改本地匹配逻辑、测试和文档；未联网、未刷新 odds、未改 LaunchAgent、未发布线上 snapshot，未部署，未提交/推送。

## 2026-06-20 P8.6 安装 lineups-only 赛前首发 LaunchAgent

- 经用户明确“确认安装”，已写入并加载本机 LaunchAgent：`~/Library/LaunchAgents/xin.celab.football.pre-match.plist`。
- LaunchAgent 每 300 秒运行 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing`，工作目录为 `/Users/eagod/ai-dev/足彩`。
- 当前 plist 不包含 `--refresh-after-lineups` / `--live-refresh`，因此不会自动触发 The Odds API odds refresh，不会发布线上 snapshot。
- 日志路径：`~/Library/Logs/worldcup/pre-match.out.log` 和 `~/Library/Logs/worldcup/pre-match.err.log`。
- `launchctl bootstrap gui/501 ...` 成功；`launchctl print gui/501/xin.celab.football.pre-match` 显示 `run interval = 300 seconds`、`runs = 1`、`last exit code = 0`。
- 手动 `launchctl kickstart` smoke 成功：FIFA public API 检查 `matches_checked=5`、`confirmed=2`、`newly_confirmed=2`、`missing=3`、`missing_alerts=0`、`source_errors=[]`，写入 `data/cache/lineups_wc2026.json`；输出 `post_information_refresh_required`，未自动刷新 odds。
- 本轮新增 confirmed cache 摘要：Brazil vs Haiti（match 29）和 Türkiye vs Paraguay（match 31）写入 confirmed starting XI。
- plist 与 pre-match stdout/stderr 敏感词扫描对 `api_key|secret|token|signature|cookie|private` 返回 0。
- 本轮只安装本机 lineups-only LaunchAgent 和更新文档；未启用自动 live-refresh，未调用 The Odds API odds refresh，未发布线上 snapshot，未部署，未提交/推送。

## 2026-06-20 P8.5 赛前首发轮询 LaunchAgent 配置生成

- 新增 `worldcup.pre_match_launch_agent`：可生成 `xin.celab.football.pre-match` 的 LaunchAgent plist；默认每 300 秒运行 `worldcup.pre_match_runner --live-lineups --write-lineups --notify-missing`。
- 默认 plist 不包含 `--refresh-after-lineups` / `--live-refresh`，因此只做 FIFA 官方首发抓取、cache 写入和缺失通知，不触发 The Odds API odds refresh。
- 只有显式传 `--allow-live-refresh` 时，生成的 plist 才会加入首发后强制 odds refresh 参数；该模式会消耗 The Odds API quota，真实启用前必须单独确认。
- CLI 默认只输出 JSON 预览；传 `--out` 只写指定 plist 文件，不执行 `launchctl`、不加载、不 kickstart。
- 本轮只做本地生成器、测试和 README/RECENT_WORK 文档；未写入 `~/Library/LaunchAgents`，未启用新 LaunchAgent，未执行 live odds refresh，未发布线上 snapshot，未部署，未提交/推送。
- TDD 覆盖：默认 lineups-only 参数、显式 opt-in 才加入 post-lineup refresh 参数、写出的 plist 可被既有 `refresh_audit.inspect_launch_agent()` 正确识别。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `452/452 tests passed`。

## 2026-06-20 P8 赛前首发轮询编排 runner

- 新增 `worldcup.pre_match_runner`：默认 dry-run，不联网、不写盘、不发通知、不刷新 odds；可显式编排 `lineups_refresh`，并在出现 `newly_confirmed > 0` 时标记或触发首发后 odds refresh。
- `lineups_refresh` 现在会读取既有 confirmed cache，返回 `newly_confirmed`，避免同一场已确认首发在后续轮询中反复触发 post-information refresh。
- 缺失首发路径保持轻量：抓不到官方首发时仍只走 `lineups_refresh --notify` 的 WxPusher 缺失提醒，不触发 The Odds API。
- 新 confirmed lineup 路径支持两阶段：不传 `--refresh-after-lineups` 时只返回 `post_information_refresh_required`；同时传 `--refresh-after-lineups --live-refresh` 时才会强制调用 `scheduled_refresh(force=True)`，这一步会消耗 The Odds API quota。
- 本轮只做本地 runner、测试和 README/RECENT_WORK 文档；未启用 LaunchAgent，未执行 live odds refresh，未发布线上 snapshot，未部署，未提交/推送。
- TDD 覆盖：默认 dry-run 不刷新 odds、缺失首发只通知不耗 quota、新 confirmed lineup 只标记 required、显式打开后才 force scheduled refresh、同一 confirmed cache 不重复计入 newly confirmed。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `449/449 tests passed`。

## 2026-06-18 P7 FIFA public API 官方首发抓取与缺失通知

- 新增 `worldcup.sources.fifa_lineups`：封装 FIFA public API 的 `calendar/matches` 和 `live/football/{competition}/{season}/{stage}/{match}` 请求，支持可注入 transport 和可选 JSON cache。
- 新增 `worldcup.collectors.fifa_lineups`：把 FIFA live football JSON 解析为 `ParsedLineupContext`；`Status=1` 作为首发、`Status=2` 作为替补，保留 FIFA player id、球员名、位置粗分类和阵型 `Tactics`。
- 新增 `worldcup.lineups_refresh` CLI：默认 dry-run；`--live` 只读请求 FIFA 公网；`--write` 写入 `data/cache/lineups_wc2026.json`；写入时只保存 confirmed lineup，并合并保留旧 confirmed cache，避免未公布轮询清空已有首发。
- 缺失通知已接入：临赛窗口内 FIFA 仍未返回两队各 11 人首发时，`--notify` 会通过 WxPusher 发“官方首发未抓到”通知；去重状态写入 `data/local/lineups_missing_notifications.json`，同一场只提醒一次。
- `lineups_wc2026.json` 继续被现有 `local_runner` 自动读取；首发确认后若 odds 早于 `lineup_confirmed_at`，既有 scheduler 会触发 `post_information_odds_required`，AH candidate 仍必须等 confirmed lineup + post-information odds 才能升级正式 S/A。
- 只读公网 smoke：`python3 -m worldcup.lineups_refresh --live --now 2026-06-18T08:55:00+00:00` 返回 `matches_checked=4`、`confirmed=1`、`missing=3`、`source_errors=[]`，未写文件、未发通知。
- 本轮未接入付费 API、未调用 The Odds API、未触发 live refresh/publish/quota、未部署、未提交/推送。
- TDD 覆盖：FIFA source URL/cache、confirmed/unconfirmed live JSON 解析、CLI 写 confirmed cache、临赛缺失只通知一次、dry-run 不联网不写、未公布轮询不覆盖旧 confirmed cache。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `445/445 tests passed`。

## 2026-06-18 P6 manual_json 首发/球员本地入口与严格 S/A gate

- 新增 `worldcup.collectors.lineups` 与 collector model：可解析本地 `lineups_wc2026.json`，把已确认首发、替补、缺阵、球员影响 delta 标准化成 pipeline 可用的 `lineup_context`。
- `build_match_inputs()` 现在可按 `source_match_no` 或 `kickoff_at_utc + canonical home/away` 匹配首发上下文，并在 snapshot 的 `model.lineup_shadow` 中输出 provider、source、source_match_no、lineups 和首发后 odds 判定。
- `local_runner` 会自动读取输入目录下的 `lineups_wc2026.json`；文件存在时，snapshot `data_quality.lineups` 和 `counts.lineup_contexts` 会记录已解析/已确认数量。该入口默认适合放在被忽略的 `data/cache/` 或 `data/local/`，不提交真实人工数据。
- 若首发已确认但最新 odds 早于 `lineup_confirmed_at`，既有 scheduler 会给出 `post_information_odds_required`，用于赛前自动补一轮首发后赔率。
- AH candidate 正式激活门槛收紧：必须同时满足 AH shadow、movement shadow、无硬质量 veto、`confirmed_starting_xi=true` 和 `post_information_odds_available=true`；否则继续停留在候选池，不计入正式 S/A。OU 仍保持 shadow-only。
- 本轮只做本地自动化框架、测试和文档记录；未接入付费/实时首发 API，未触发 live refresh/publish/quota，不联网，不部署，不提交/推送。
- TDD 覆盖：manual_json 解析、缺少可用队名跳过、pipeline 首发匹配、local_runner 读取首发文件并触发 post-information odds 需求、AH candidate 无已确认首发时不升级。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `437/437 tests passed`。

## 2026-06-18 P5 AH candidate 正式激活规则

- `attach_trends()` 在附加 `movement_shadow` 后，会对 AH `candidate_grade` 做正式激活判断：仅当 raw grade 为 S/A、当前 grade 尚非 S/A、`ah_validation_shadow.candidate_validated=true`、`movement_shadow.supports_signal=true`、没有硬质量 veto，且首发已确认时 post-information odds 已可用，才会升级为正式 `grade=raw_grade`。
- 激活后的信号会写入 `promotion` 审计块，记录 `method=ah_candidate_v1`、原 grade、目标 grade、原 candidate grade、验证来源和候选依据；同时移除 `candidate_grade` / `candidate_reasons`，移除 `ah_market_edge_missing`，追加 `ah_candidate_promoted`，并标记 `ah_market_validated=true`。
- OU 仍保持 shadow-only：即使存在 `candidate_grade` 或 `ou_total_shadow`，也不会被本轮逻辑提升为正式 S/A。
- 本轮只做本地规则、测试和文档记录；不触发 live refresh/publish/quota，不联网，不部署，不提交/推送。
- TDD 覆盖：符合 AH shadow + movement shadow 门槛时 candidate 会升级为正式 S；OU candidate 不升级；首发已确认但 odds 尚未 post-information 时 AH candidate 不升级。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `432/432 tests passed`。

## 2026-06-18 P4 独立 OU total shadow schema

- 新增 `MatchAnalysis.ou_total_shadow`：对比 active `model_market_total` OU 概率与 independent/raw `model_raw` OU total 概率，并输出两套 edge 与 edge delta。
- `ou_total_shadow.activation=shadow_only`：不改变 active `mu_total`、OU 概率、EV/Edge、正式 `grade`、`candidate_grade`、finished tally 或日报 S/A 战绩。
- snapshot `model.ou_total_shadow` 会记录 `mu_active`、`mu_independent`、`mu_market`、`mu_market_weight`、`same_market_total_anchor`、active/independent/market probability 和 edge 对比。
- 本轮只接 schema / shadow 计算和本地 snapshot 序列化；不解除 `market_informed_total` 降级、不接真实新数据源、不联网、不触发 refresh/publish/quota、不部署、不提交/推送。
- TDD 覆盖：independent OU total shadow 不改变 active OU 信号等级，snapshot 可序列化 `model.ou_total_shadow`。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `430/430 tests passed`。

## 2026-06-18 P3 post-information odds 调度判定

- `worldcup.scheduler` 新增 `post_information_odds_required` 单场调度原因：当 `model.lineup_shadow` 显示首发已确认、最新 odds 早于首发信息且比赛未开赛时，单场 `next_update_at` 会提前到当前 dry-run 时刻。
- 调度输出会附带 `post_information_odds` 摘要，包含 `lineup_confirmed_at`、`odds_observed_at` 和 `post_information_odds_available=false`，用于审计为什么需要首发后赔率刷新。
- 额度耗尽、比赛已开赛、没有首发确认或 odds 已经是 post-information 时，不会触发该原因；正式 live refresh 仍必须由 `--live` 和调度 due 控制。
- 本轮只做本地调度/schema 层，不接真实首发数据源、不主动调用 The Odds API、不触发 refresh/publish/quota、不部署、不提交/推送。
- TDD 覆盖：首发后赔率缺失会立即 due，已有 post-information odds 不会额外刷新。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `428/428 tests passed`。

## 2026-06-18 P2 首发/球员影响 lineup_shadow schema

- 新增 `MatchAnalysisInput.lineup_context` 与 `MatchAnalysis.lineup_shadow`：输入结构化首发/球员影响 delta 后，输出调整前后 lambda、1X2/OU 概率和 edge 对比。
- `lineup_shadow.activation=shadow_only`：不改变 active `lambdas`、`combined_1x2`、EV/Edge、正式 `grade`、`candidate_grade`、finished tally 或日报 S/A 战绩。
- `lineup_shadow` 会记录 `lineup_confirmed_at`、最新 odds `fetched_at` 和 `post_information_odds_available`，用于区分赔率是否已经吸收首发信息。
- 本轮只接 schema / shadow 计算和本地 snapshot 序列化；不接真实首发数据源、不联网、不触发 refresh/publish/quota、不部署、不提交/推送。
- TDD 覆盖：首发 shadow 不改变 active 概率、post-information odds 判定、snapshot `model.lineup_shadow` 序列化。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `426/426 tests passed`。

## 2026-06-18 P1 候选池 candidate_grade

- 新增 `candidate_grade` / `candidate_reasons` 信号字段：当前只给 AH raw S/A 被 `ah_market_edge_missing` 压到 B、且 `ah_validation_shadow.candidate_validated=true`、没有硬质量 veto 的信号打研究候选标记。
- `candidate_grade` 不改变正式 `grade`，不改变 EV/Edge/模型概率，不计入 finished tally、日报 S/A 战绩或强信号统计；研究台账只在展开详情中显示“候选等级 / 候选依据”。
- 当前 OU 因 `same_market_total_anchor=true` 被压到 C 的 raw S/A 不进入 candidate；需等后续独立 OU total shadow 后再讨论。
- TDD 覆盖：AH shadow 验证通过时输出 `S-candidate`，OU 市场锚定 raw S/A 不输出 candidate，snapshot 序列化候选字段，台账展示候选但不计入正式强信号。
- 本轮不联网、不触发 refresh/publish/quota、不部署、不提交/推送。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `423/423 tests passed`。

## 2026-06-18 历史完赛 shadow 回填诊断

- 新增 `worldcup.shadow_backfill_diagnostics`：只读本地 `analysis_snapshot.json` / `finished_record_store.json` 与 `data/local/history/`，给历史 closing 信号回算 `ah_validation_shadow` 和 `movement_shadow`。
- 报告默认输出 `data/local/diagnostics/shadow_backfill_diagnostics.json`；该路径在本地忽略数据区，只用于研究诊断，不回填线上 snapshot、不改模型参数、不改信号等级、不触发 refresh/publish/quota、不联网、不部署。
- TDD 覆盖：旧 closing snapshot 缺 shadow 时可回算 AH fair-line / movement shadow；CLI 可写出本地 JSON 报告。
- 已运行真实本地报告：`match_count=20`、`raw_strong_signal_count=32`、`decided_raw_strong_signal_count=31`、`sample_too_small=false`、`missing_closing_entry=0`；source coverage 为 `closing_entry=32`、`ah_shadow=16`、`movement_shadow=32`。
- 当前观察：raw S/A 总体 `hit=9`、`miss=22`、`push=1`；AH `candidate_validated=true` 为 `hit=5`、`miss=7`；`movement_shadow.supports_signal=true` 为 `hit=5`、`miss=12`；`both_shadow_support=true` 为 `hit=4`、`miss=5`。暂不支持直接用 shadow 升级 S/A，只能继续作为诊断观察。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `420/420 tests passed`。

## 2026-06-18 AH 验证与赔率移动信号级 shadow

- AH 信号新增 `ah_validation_shadow`：基于模型净胜球分布计算标准赔率下的 `model_fair_line`，并记录 `market_line`、`fair_line_delta`、`line_consensus`、`dispersion_ok` 和 `candidate_validated`。
- 当前 AH 验证仍为 `activation=shadow_only`：即使候选验证通过，也不改变 `ah_market_validated=false`、不解除 `ah_market_edge_missing`、不增加 S/A 数量。
- `attach_trends()` 现在会在每条信号上附加 `movement_shadow`，用既有 `odds_movement` 判断赔率方向和盘口方向是否支持该信号；该字段只做诊断，不参与模型概率、EV、Edge、排序或等级裁决。
- TDD 覆盖：AH shadow 不升级等级、信号级 movement shadow 不改等级、snapshot 序列化保留 `ah_validation_shadow`。
- 本轮不改模型参数、不改 S/A 阈值、不改赔率源、不触发 refresh/publish/quota、不联网、不部署。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `418/418 tests passed`。

## 2026-06-17 finished 定格 schema v2 诊断字段

- 已增强 `worldcup.finished_record`：新定格的 `closing_signals` 在保留旧字段的基础上写入 `diagnostic_schema_version=2`，并冻结 `raw_grade`、`ev`、`edge`、`reasons`、`probability_family_probs`、`probability_family_deltas`、`odds_movement_quality` 和 `diagnostic_flags`。
- `worldcup.postmatch_diagnostics` 已改为优先读取 frozen v2 字段；即使后续 history 不可用或不再包含完整 match 诊断，也能从 finished store 直接统计 reason、概率族和盘口移动覆盖率。
- 旧 finished 记录继续兼容，不做后验回填；当前已有 22 条强信号仍会保持旧记录覆盖状态，后续新增完赛记录才会自然获得 v2 诊断上下文。
- TDD 覆盖：finished block 定格 v2 字段、概率族差异、盘口移动 flags；postmatch diagnostics 可在没有 history 的情况下使用 frozen v2 字段生成 source coverage。
- 本轮不改模型参数、不改信号等级、不改 refresh/publish/quota 逻辑、不联网、不部署。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `416/416 tests passed`。

## 2026-06-17 完赛 S/A 信号本地诊断报告

- 新增 `worldcup.postmatch_diagnostics`：只读本地 `analysis_snapshot.json`、`data/local/history/` 和 finished block，输出 `data/local/diagnostics/postmatch_diagnostics.json`。
- 报告按 S/A closing 信号生成逐条诊断行，并汇总 outcome、grade、market、reason、source coverage；逐条保留 closing snapshot、结果、EV/Edge、probability family 概率差异、odds movement quality 和 diagnostic flags。
- 诊断工具只解释已完赛信号，不改模型参数、不改信号等级、不触发 refresh/publish、不联网、不消耗 The Odds API quota、不部署。
- TDD 覆盖：finished 简版信号可从 closing history 补齐完整 signal reason、概率族差异和 odds movement；CLI 可写出 JSON 报告并保留研究免责声明。
- 真实本地报告已生成：`match_count=16`、`strong_signal_count=22`、`decided_strong_signal_count=21`、`sample_too_small=false`、`skipped_no_closing=0`。
- 当前真实报告分桶：`hit=7`、`miss=14`、`push=1`；`S=5/12/0`、`A=2/2/1`；市场分布为 `1X2_90min=1/6/0`、`AsianHandicap_90min=5/6/1`、`OverUnder_90min=1/2/0`。
- 当前历史归档限制已显式暴露：22 条强信号均可匹配 closing entry/full signal，但 `reason=0`、`probability_family=0`、`odds_movement=0`，说明较早 closing snapshot 缺少这些后续诊断字段；因此现阶段不能按 reason/概率族/盘口移动对早期 miss 下结论。
- 验证：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `414/414 tests passed`；`python3 -m worldcup.postmatch_diagnostics` 返回 `status=ok`。

## 2026-06-17 Phase 2B ECS 部署与受控 live refresh

- 已部署 `72e9540 feat: add odds movement diagnostics` 到 ECS `/opt/worldcup/releases/72e9540`；`/opt/worldcup/current` 已从 `/opt/worldcup/releases/71c4d68` 切换到新 release，`worldcup.service` 与 `nginx` 均为 active。
- 部署前完整测试入口通过：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `412/412 tests passed`。
- 部署后公网 smoke：`/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；页面保留研究免责声明，资金/下注禁词扫描为空。
- 已执行一次受控 `worldcup.scheduled_publish --live --force --no-notify`：新 run 为 `20260617T054825Z-live`，ECS ingest 返回 HTTP 200 / `ingest_status=stored`，snapshot_id 为 `18b00589bc9b49f1b1c378abcc1866a11158c904ca14c3798af0a1c5d994bb01`。
- 新 snapshot 共 53 场，`source_errors=[]`、`stale_sources=[]`、`invalid_odds_count=0`；The Odds API primary quota 从 162 变为 159，本轮消耗 3。
- `odds_movement` 已写入全部 53 场，`line_changed=14`、`sparse=0`；信号等级分布为 `S=3`、`A=2`、`B=90`、`C=276`。
- P0 fail-safe 不变量继续成立：`same_market_total_anchor=true` 的 OU S/A 为 0，`ah_market_validated=false` 的 AH S/A 为 0。
- `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`、`warnings=3`；warning 仍为既有日志类告警，不阻塞本次上线。远端最近 10 分钟 `worldcup.service` journal 未扫到敏感词或 error。

## 2026-06-17 Phase 2B 赔率/盘口移动 diagnostic schema

- 新增 `odds_movement` diagnostic：从既有 `odds_trend` 历史点派生 1X2、AH 主盘、OU 主线的首末赔率、绝对/相对移动、盘口线移动和 quality 标记。
- `odds_movement` 随 `attach_trends()` 写入每场 match 顶层；schema 当前为 `schema_version=1`、`window=captured_history`，包含 `1x2`、`ah_main`、`ou`、`quality` 四块。
- 该字段只作为研究诊断，不参与模型概率、EV、S/A 阈值、信号等级或 fail-safe 裁决；公开 `/api/matches` 仍使用既有安全投影。
- TDD 覆盖：movement 摘要生成、`attach_trends()` 写入 diagnostic、refresh 富化路径保留字段。
- 当前缓存只读重算 sanity：所有重算比赛均可生成 `odds_movement`，OU 同市场锚定 S/A 和 AH 未验证 S/A 仍为 0。
- 本轮不改模型参数、信号阈值、赔率源、refresh/publish/quota 逻辑或 ECS；未触发 live refresh，未部署。

## 2026-06-17 联赛可迁移架构偏好

- 已将项目级长期偏好同步写入 `AGENTS.md` 和 `CLAUDE.md`：当前实现以 2026 世界杯为首个 competition adapter，但新增通用数据结构、snapshot 字段、概率族、赔率/盘口移动诊断和回测接口时，应尽量使用可迁移到联赛的命名与边界。
- 规则同时明确：已有 `stage` / `group` 等世界杯字段保持兼容，不为未来联赛提前大重构。
- 本轮只更新项目协作偏好和近期记录，不改代码、不触发 refresh、不部署、不提交、不推送。

## 2026-06-16 Phase 2A 概率族 shadow schema 实现

- 已按已确认方案在 `worldcup.pipeline` 输出 `model_raw`、`model_market_total`、`market_only` 三套 probability family，并为每套概率写入 provenance metadata。
- `model_raw` 使用当前 Elo + Poisson + ensemble 与 `mu_prior`，不使用同场市场概率反推总进球；`model_market_total` 保持当前 legacy active 行为，并显式记录 `same_market_total_anchor` 与同市场 OU 强信号限制；`market_only` 只作为 baseline / diagnostic，不允许生成 model value signal。
- snapshot `model.probability_families` 已序列化；旧有 `model.mu_total`、`model.combined_1x2`、`model.ou_2_5`、`market.*` 和 `signals` 保持兼容，公开 `/api/matches` 投影不新增大块诊断字段。
- TDD 覆盖：pipeline 概率族/provenance、shadow 输出不改变现有信号等级、snapshot 兼容序列化、公开比赛投影忽略 `probability_families`。
- 完整标准测试入口通过：新增相关断言随标准入口运行返回 `410/410 tests passed`。
- 本轮不切换最终信号生成口径，不改 `mu_total`、`mu_market_weight`、`mu_dr_slope`、`dc_rho`、Elo K、host advantage、ensemble 权重、S/A 阈值、赔率源、refresh、publish、quota 或 ECS；未触发 live refresh，未部署。

## 2026-06-16 Phase 2 概率族 schema 方案

- 已按 GPT 5.5 Pro 的第二阶段建议写成工程计划文档：`docs/superpowers/plans/2026-06-16-probability-families-schema.md`。
- 方案目标是 shadow 输出三套概率族：`model_raw`、`model_market_total`、`market_only`，并加入 provenance metadata、snapshot 兼容策略、API/前端策略、回测口径和测试清单。
- 计划明确 Phase 2A 只做 schema / shadow 输出，不切换最终信号生成口径；不启用 `mu_total=2.2`、`mu_dr_slope=0.0015`、`dc_rho=-0.15`，不改阈值、赔率源、refresh、publish、quota 或 ECS。
- 本轮只写方案文档和近期记录，未改模型代码，未触发 refresh，未部署。

## 2026-06-16 P0.5 脏赔率隔离与诊断

- 只读观察下一次 scheduled publish：当前 UTC `2026-06-16T03:25:05Z` 尚未到 `next_due_at=2026-06-16T07:00:00+00:00`；最新 history 仍为 `20260616T025759Z-local`，`ops_check` 仍为 `ok=true`、`errors=0`、`warnings=3`，公网和 ECS 本机 `/healthz`、`/api/matches`、`/api/finished` 均正常。
- 根因定位：历史 raw odds archive 中存在 The Odds API `price=1.0` 脏 quote，例如 `odds_raw_20260615T010915Z-live.json.gz` 内 Ivory Coast vs Ecuador 的 `betsson` / `nordicbet` / `betclic_fr`，会在 `aggregate_market()` 调用 `devig()` 时触发 `decimal odds must be > 1.0`。
- 新增 `InvalidOddsQuote` 诊断模型；`parse_theoddsapi_events()` 现在会在解析层隔离 decimal odds `<= 1.0` 的 quote，不让其进入 aggregation、devig、EV 或 signal generation，同时保留 bookmaker、market、selection/outcome、line、match id、球队、commence_time 和 last_update。
- snapshot `data_quality` 新增 `invalid_odds_count` 和最多 10 条 `invalid_odds_examples`，并在本地 snapshot 构建时补充 `raw_payload_path`；正常 odds `> 1.0` 行为不变。
- TDD 覆盖：`odds == 1.0`、`odds < 1.0`、valid odds 不变、脏 quote 不进入 market aggregation、warning/context 不丢。
- 历史事故临时复现验证：用 `odds_raw_20260615T010915Z-live.json.gz` 替换临时 cache odds 后可正常构建 snapshot，输出 `invalid_odds_count=5`；当前真实 `data/cache` 内存构建 `invalid_odds_count=0`。
- 完整标准测试入口通过：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 返回 `406/406 tests passed`；仍只有 Starlette/FastAPI TestClient deprecation warning，不影响本次 gate。
- 本轮不改模型参数、信号阈值、赔率源配置、刷新策略、发布策略、quota 或 ECS；未触发 live refresh，未调用 The Odds API，未部署。

## 2026-06-16 P0.5 fail-safe baseline 与测试 runtime 修复

- 已将 P0 signal fail-safe 上线后口径标记为 `signal-failsafe-v1`，记录到 `docs/research/2026-06-16-signal-failsafe-baseline.md`；后续信号分布、日报、回测和复盘应明确区分 `pre-failsafe` / `post-failsafe`。
- baseline 固定引用：code commit `71c4d68`、deployment doc commit `893110e`、publish run `20260616T025759Z-local`、snapshot_id `7f095fb7017c0acf588c017d11406f81ddabaccb2c81f509fd1e75f4090e0098`。
- 只读复查 `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`、`warnings=3`；warning 分类为：本地 `scheduled-publish.err.log` 历史 traceback 属于 `data_quality_warning`，远端 nginx access/error 5xx/upstream 计数属于公网扫描导致的 `expected_warning`。
- 本地 `scheduled-publish.err.log` 历史 traceback 早于本次 P0 部署，包含一次 HTTPS publish TLS EOF 和多次 `decimal odds must be > 1.0`；当前不阻塞 baseline，但应作为后续 P0.5 小修复候选，避免脏赔率让自动构建中断。
- 已按 `pyproject.toml` 声明依赖修复当前测试 runtime parity：执行 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip install -e .`，安装 `fastapi`、`httpx`、`uvicorn` 及其依赖到当前 bundled Python runtime。
- 完整标准测试入口已恢复可信 gate：`/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `402/402 tests passed`，包含 `tests/test_fastapi_app.py`；运行中出现 Starlette/FastAPI TestClient deprecation warning，暂不影响结果。
- 本轮未改模型参数、赔率源、刷新策略、发布策略、quota、ECS 部署或线上数据；未触发 live refresh，未调用 The Odds API。

## 2026-06-16 信号等级 fail-safe 口径修复

- 按外部审查结论完成第一阶段口径修复：本次是 fail-safe 补丁，不改模型参数、赔率源、刷新逻辑、发布逻辑或展示主框架。
- OU 信号新增同场市场 total 锚定标记：当本场 OU 市场参与反推 `mu_total` 时，OU 最终等级封顶为 `C`，追加 `market_informed_total`；保留 `raw_grade`、`EV`、`Edge`、`total_mu_source` 和 `same_market_total_anchor` 供审计。
- AH 信号新增临时市场验证标记：当前 AH 尚无 market edge / fair-line delta / line consensus 闭环，`ah_market_validated=false` 时 S/A 封顶为 `B`，追加 `ah_market_edge_missing`；保留 settlement EV 和 `raw_grade`，B/C 不被压成 C。
- `MatchAnalysis` 和 snapshot model 块新增 `mu_prior`、`mu_market`、`mu_market_weight`、`total_mu_source`、`same_market_total_anchor`，便于后续拆分 `model_raw` / `model_market_total` / `market_only`。
- 既有置信度护栏调整为即使信号已被 fail-safe 降级，也继续追加交叉验证 reason，避免审计信息被覆盖。
- TDD 覆盖：OU market-informed total S/A 封顶、prior total 不误杀、AH 缺市场验证封顶、已有 reason 保留，以及 pipeline 级信号元数据透传。
- 验证：标准命令 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 在加载 `tests/test_fastapi_app.py` 时因当前 runtime 缺少 `fastapi` 中断；排除该可选依赖文件后 `390/390` 通过。
- 只读 sanity check 用当前 `data/cache` 内存重算 57 场：`same_market_total_anchor=true` 的 OU S/A 为 `0`，`ah_market_validated=false` 的 AH S/A 为 `0`；最终等级分布为 `S=3`、`A=4`、`B=95`、`C=297`，强信号只剩 1X2。
- 实现阶段未联网、未触发 live refresh、未调用 The Odds API、未写入 snapshot；研究边界不变，不构成投注建议。
- 已提交并推送 `71c4d68 fix: cap market-leaked signal grades` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/71c4d68`；`worldcup.service` 与 `nginx` 均为 active。
- 已执行一次受控本地缓存重算发布：新 run 为 `20260616T025759Z-local`，本地生成并归档 57 场 snapshot，`source_errors=[]`、`stale_sources=[]`；未触发 live refresh、未调用 The Odds API、未消耗 quota。ECS ingest 返回 HTTP 200 / `ingest_status=stored`，snapshot_id 为 `7f095fb7017c0acf588c017d11406f81ddabaccb2c81f509fd1e75f4090e0098`。
- 发布后验证：远端 latest snapshot 中 `same_market_total_anchor=true` 的 OU S/A 为 `0`，`ah_market_validated=false` 的 AH S/A 为 `0`；fail-safe reason 计数为 `market_informed_total=114`、`ah_market_edge_missing=114`；等级分布为 `S=3`、`A=4`、`B=95`、`C=297`。
- `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`；公网 `/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200，公开页保留研究免责声明且未命中资金/下注禁词。

## 2026-06-15 OU 主盘口动态选择

- 修复大小球固定 `2.5` 的问题：`worldcup.pipeline` 现在按每场 over/under 双边报价家数选择当前主流 half-goal OU 盘口线，`ou_main_line` 只作为无可用主线时的 fallback / tie-break。
- snapshot 继续保留兼容字段 `ou_2_5`，但新增 `model.ou_line` 与 `market.ou_2_5.line` 记录真实盘口线；OU 信号 `line` 也改为真实主线。
- 赔率走势点现在记录 OU line，预览页走势文字会显示对应盘口，避免不同大小球线的赔率混在一条曲线里看不出来。
- 赛后 `eval_data` 导出新增 `ou_line`，`backtest` loader / replay / OU 赛果判定按每场 `ou_line` 计算；旧 CSV 或老 snapshot 缺 line 时兼容回退 `2.5`。
- 上线前用当前缓存只读重算验证：Spain vs Cape Verde 的 OU 主线从旧固定 `2.5` 改为 `3.5`，双边报价家数为 `11/11`。
- 本地验证：新增测试先红后绿；目标相关测试 `95/95` 通过；项目 3.12 runtime 下除 `test_fastapi_app.py` 外的测试 `381/381` 通过。标准 runtime 全量命令仍因当前环境缺少可选依赖 `fastapi` 在 FastAPI 测试导入处中断。
- 已提交并推送 `46bfee8 fix: select dynamic over-under main line` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/46bfee8`；`worldcup.service` 与 `nginx` 均为 active。
- 已执行一次 live force refresh（`--no-notify`）：新 run 为 `20260615T100519Z-live`，本地刷新生成 60 场 snapshot，`source_errors=[]`、`stale_sources=[]`，The Odds API quota 剩余 `263`。首次 `scheduled_publish` 在 HTTPS publish 阶段遇到一次 `SSL UNEXPECTED_EOF`，随后只用 `worldcup.publish` 重发同一份 snapshot，未重复刷新数据源；ECS 返回 HTTP 200 / `ingest_status=stored`，snapshot_id 为 `10e802e805c4e2945ea79545c12b9a9377ef3fdd5220a321f9e167fae35a51a8`。
- 发布后验证：远端 latest snapshot 中 Spain vs Cape Verde 的 `model.ou_line` 与 `market.ou_2_5.line` 均为 `3.5`，OU 信号行也为 `3.5`；最新 60 场扫描 `mismatches=0`，非 `2.5` 主线共 6 场（Brazil vs Haiti、Ecuador vs Curaçao、Egypt vs Iran、Spain vs Cape Verde、Spain vs Saudi Arabia、France vs Iraq）。
- `python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`；公网 `/healthz`、`/api/matches`、`/api/finished`、`/preview` 均返回 200，公开页保留研究免责声明且未命中资金/下注禁词。

## 2026-06-15 S/A 护栏缓存重算发布

- 按已上线的新 S/A 置信度护栏，用本地 `data/cache` 离线重算当前 snapshot；未触发 live refresh、未调用 The Odds API、未消耗 quota。
- 重算对比范围为 61 场、427 条信号：等级变化 17 条，其中 `S->B` 15 条、`A->B` 2 条；无 `B/C` 升级，`EV`、`Edge`、`status`、`line`、`selection` 均未变化。
- 发布前生成富化快照 `data/local/guard_compare/recomputed_enriched_snapshot.json`，保留 8 场 finished 块和 61 场 odds trend；等级统计为 `S=21`、`A=7`、`B=82`、`C=317`。
- 已通过 signed ingest 发布到线上，run_id 为 `20260615T031251Z-local`；远端 SQLite 最新 snapshot_id 为 `eed417b89de704ceedd2f6dfe655822cac662433a0f88066d7ada3ed5a0e231d`。
- 本地 `data/cache/analysis_snapshot.json` 已同步为同一份快照，并归档到 `data/local/history/snapshot_20260615T031251Z-local.json`；原快照备份在 `data/local/guard_compare/pre_guard_publish_analysis_snapshot.json`。
- 发布后验证：公网 `/healthz`、`/api/matches`、`/api/finished`、`/preview` 均返回 200；远端 latest snapshot 显示 `S=21/A=7/B=82/C=317`、`source_errors=[]`、`stale_sources=[]`；`python3 -m worldcup.ops_check` 返回 `ok=true`、`errors=0`。
- 研究边界不变，不构成投注建议；finished 仍为 8 场小样本，`sample_too_small=true`。

## 2026-06-15 S/A 强信号置信度护栏

- 新增只降级置信度护栏：`generate_value_signals()` 生成原始信号后统一检查，只有 `S/A` 会被封顶到 `B`，不改变模型概率、EV/Edge，不升级 `B/C`。
- `1X2` 主/客强信号新增三类风险 reason：`reverse_market`（逆 closing 市场主方向）、`ah_cross_check_missing`（主亚盘缺失或家数不足）、`ah_not_supporting_1x2`（主亚盘方向不支持该胜负方向）。
- 主办国本土场地新增确认护栏：主办国 `1X2` 强信号若市场概率低于 `quality.host_x12_market_prob_min_for_strong=0.6`，追加 `host_market_confirmation` 并封顶；主办国 AH 强信号若主亚盘让步绝对值低于 `quality.host_ah_abs_line_min_for_strong=1.0`，追加 `host_handicap_confirmation` 并封顶。
- 复盘 Germany vs Curaçao、Netherlands vs Japan、Ivory Coast vs Ecuador 后补充第二轮护栏：极强热门阈值 `quality.extreme_favorite_market_prob_min=0.85` 或主亚盘让步绝对值 `quality.extreme_favorite_ah_abs_line_min=2.5` 时，受让方 AH 强信号追加 `extreme_favorite_handicap` 并封顶；主亚盘大让步且 `Under 2.5` 强信号追加 `under_vs_big_handicap` 并封顶；AH 0 盘强信号追加 `ah_zero_line_confirmation` 并封顶。
- 反事实检查显示：Ivory Coast vs Ecuador 的 `Ecuador 1X2 S` 与 `Ecuador 0 盘 AH S` 会降到 B；Netherlands vs Japan 的 `Japan 1X2 S` 会降到 B；Germany vs Curaçao 的 `Under 2.5 S` 与 `Curaçao +3.5 AH S` 会降到 B。Ivory Coast vs Ecuador 的 `Over 2.5 A` 暂不硬杀，需等待更多 OU 样本再决定。
- TDD 红灯覆盖：USA vs Paraguay 型 `1X2 逆市场 + AH 不支持`、无 AH favorite 支持、Canada 型主办国市场确认不足、Canada 型主办国 AH 让步确认不足；同时覆盖 Mexico vs South Africa 型市场与亚盘均确认时不误杀。
- TDD 红灯追加覆盖：德国大让步下 `Under 2.5 S` 封顶、极强热门下受让方 AH S 封顶、AH 0 盘 S 封顶。
- 本地验证：单独 `tests/test_pipeline.py` 通过 `23/23`；除 `test_fastapi_app.py` 外的可执行测试通过 `374/374`。项目标准命令 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 当前在加载 `tests/test_fastapi_app.py` 时因本环境缺少可选依赖 `fastapi` 中断。
- 实现与本地验证阶段未联网、未触发 live refresh、未调用 The Odds API；研究边界不变，不构成投注建议。
- 已提交并推送 `66703f9 feat: add strong signal confidence guards` 到 `origin/main`；随后补记部署结果的 docs-only commit 也已推送，护栏代码包含在当前 `origin/main`。
- 已部署到 ECS，`/opt/worldcup/current` 指向当前 `origin/main` 的最新 release；`worldcup.service` 与 `nginx` 均为 active。
- 部署后 smoke：公网 `/healthz` 返回 `status=ok`，`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；服务日志只看到正常重启和请求记录。
- 本次上线只切换护栏代码并重启服务，未触发 live refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-14 ops_check 复盘巡检增强

- `worldcup.ops_check` 新增只读 `/api/finished` 公网检查，并在远端本机 HTTP smoke 中同步检查 `/api/finished`；失败会计入 errors。
- 本地巡检新增 `finished` 一致性块：重算 S/A closing signal tally，与 snapshot `finished.tally` 对比；读取 `data/local/results/wc2026_results.csv` 行数，与 finished coverage 的 `finished_result_count` 对齐检查。
- 日志敏感扫描区分 `api_key` 字段名和真实值泄露：安全字段名计入 `sensitive_field_name_hits`，不再把字段名误报为 `sensitive_hits`；真实 `api_key=value` 仍会按敏感命中处理。
- 本地 TDD 验证：先看到新增测试红灯（安全 `api_key` 字段名误报、缺少 `local.finished`），实现后 `tests/test_ops_check.py` 通过 `5/5`；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `378/378 tests passed`。
- 真实只读巡检：新版 `run_ops_check()` 返回 `ok=true`、`errors=0`、`warnings=3`；本地 finished `match_count=8`、tally 匹配、results CSV `8/8` 对齐，公网与远端本机 `/api/finished` 均返回 200；样本仍为 `8 < min_sample 20`，只能作为观察。
- 已提交并推送 `f1e29e0 chore: enhance finished ops check` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/f1e29e0`；`worldcup.service` 与 `nginx` 均为 active。
- 部署后 smoke：公网 `/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；`/api/finished` 返回 8 场、tally 为 `S 3/4/0`、`A 1/0/1`，禁词扫描为空。
- 本轮部署只切换巡检代码并重启服务，未触发 live refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-14 复盘接口安全投影推送与部署

- 已将本地 `main` 推送到 `origin/main`，包含 `13dcc81 feat: add safe finished review API`、`e23e983 docs: add review self-audit rules`、`c3b932d docs: add wc2022 line movement backtest plan`。
- 部署 release `c3b932d` 到 ECS：`/opt/worldcup/current` 已从 `/opt/worldcup/releases/111d1d7` 切到 `/opt/worldcup/releases/c3b932d`；部署使用本地 `git archive` + SSH stdin 上传/解包，未在服务器使用 git。
- `worldcup.service` 已重启并保持 active；`nginx` 保持 active。公网 `/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200；公网 `/api/snapshot/latest` 仍按规则返回 404。
- 部署后发现 Nginx 仅白名单旧 `/api/matches`，已为 `/api/finished` 增加精确代理路由；`nginx -t` 通过后 reload，配置备份为 `/etc/nginx/sites-available/football.celab.xin.conf.bak.20260614T214815`。
- 公网 `/api/finished` 返回 8 场 finished 投影，`sample_too_small=true`，禁泄漏扫描未命中 `run_id`、quota、provider 原名、raw error、资金/下注字段；页面保留免责声明和小样本观察提示。
- 最近 10 分钟 `worldcup.service` journal 敏感词/error 命中 0，`football.celab.xin` 专属 Nginx access/error log 敏感词和 5xx/upstream 命中 0。
- 本次部署只切换代码和 Nginx 路由并重启/reload 服务，未触发 live refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-14 复盘接口第一阶段 review 收口

- 补充 ASGI `/api/finished` 契约测试，确认标准库 ASGI 适配层透传安全复盘投影且不泄露 `run_id`、quota 或 provider 原名。
- 将 `data_quality.enrichment_errors` 以脱敏计数形式暴露到静态 public snapshot 的 `data_quality.enrichment_error_count`，不输出 raw error。
- 预览页“数据源健康”补充 `富化异常` 计数，并把 enrichment error 计入数据质量预警；页面仍只显示数量，不显示原始错误细节。
- 本轮只改本地代码、测试和近期记录，未联网、未启动服务、未部署、未提交、未推送。
- 本地验证：先看到新增测试红灯（public snapshot 缺 `enrichment_error_count`、预览页缺 `富化异常`），实现后 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `376/376 tests passed`。

## 2026-06-13 复盘接口安全投影第一阶段

- 新增公开安全复盘投影 `project_finished_rows(snapshot)`，输出 `schema_version`、`summary`、coverage、小样本标记、closing 场次与复盘信号；测试覆盖不泄露 `run_id`、quota、provider 原名、原始 source error、资金/下注字段。
- 新增 `GET /api/finished`，标准库 HTTP 适配层与 FastAPI 包装层共用同一投影；静态导出新增 `api/finished.json`，`api/snapshot/latest.json` 也带脱敏 `finished` 投影，manifest 同步列出新文件。
- 历史回顾 workbench 顶部新增复盘质量提示：样本偏小时只标记为“仅作为观察”，`skipped_no_closing` 非 0 时显示缺少 closing 记录数量。
- `refresh_runner` 富化失败时继续不阻断刷新/发布，但会写入 `data_quality.enrichment_errors`，避免复盘富化异常只停留在 stderr。
- 数据契约文档已补 `GET /api/finished` 与 `api/finished.json`；本轮只改本地代码、测试和文档，未启动服务、未联网、未触发 live refresh、未调用 The Odds API、未部署、未提交、未推送。
- 本地验证：先看到新增契约测试红灯，再实现转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `374/374 tests passed`。

## 2026-06-13 对抗性自审偏好同步

- 已将通用对抗性自审方法论写入全局 `~/.codex/AGENTS.md` 和 `~/.claude/CLAUDE.md`：高不确定性数据分析、模型评估、复盘、策略判断、架构方案或实现计划默认检查小样本、口径不一致、基准不足、范围膨胀、线上状态、密钥/权限/成本与验证不足风险。
- 已将足彩项目专用规则同步写入项目 `AGENTS.md` 和 `CLAUDE.md`：赛后复盘必须区分事实/观察/结论/工程问题；样本不足时不得建议调参；必须检查 S/A 信号、`daily_eval.signal_tally` vs `finished.tally`、`skipped_no_closing`、closing snapshot、90 分钟/加时/点球/比分源、The Odds API scores vs openfootball、东道主/准主场/中立场/Elo 口径风险。
- 项目实现计划、架构方案、调度/部署方案、数据链路方案和模型调整方案以后必须包含“对抗性自审”段落，显式写出 live refresh、quota、HMAC secret、LaunchAgent、ECS、SQLite/PostgreSQL、`data/local/`、`data/cache/`、日报推送和公网展示等风险与确认点。
- 本次只更新偏好/协作文档，不改代码、不触发服务、不联网、不消耗 The Odds API、不提交、不推送。

## 2026-06-12 历史回顾工作台交互

- 将“历史回顾”从单独的 `finished-table` 表格改为与“实时信号”一致的 workbench：日期条、等级/搜索/赛事筛选、左侧历史比赛列表、右侧盘口分类 tabs 与信号明细。
- 历史详情使用 closing（开球前最后一轮）口径展示赛果、收盘快照、收盘赔率、命中/未中/走水与赔率走势；旧 `finished-row` 结构已从渲染契约中移除。
- 筛选状态按 `实时信号` / `历史回顾` 分视图保存，修复实时页选择未来日期后切到历史导致历史记录被隐藏的问题。
- `build_finished_view` 补充历史 workbench 所需展示字段：北京日期、源队名/中文队名、开球时间和 closing snapshot 时间。
- 本地验证：新增历史 workbench DOM 契约测试，先红灯后转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `370/370 tests passed`。
- Browser QA：用临时 `127.0.0.1:8765` 静态服务验证 `data/cache/preview.html`；实时页选 `2026-06-13` 后切历史仍显示 2 场历史比赛；历史盘口分类切到 `胜平负` 后显示 3 条信号，信号行可展开并显示赔率走势；390×844 视口无页面级横向溢出，console error/warn 为空。临时服务已停止。
- 功能代码已提交并推送 `b0e1255 feat: align history review workbench` 到 `origin/main`；随后将包含本条记录补记的最新 `origin/main` release 部署到 ECS。部署使用本地 `git archive` + SSH stdin 上传/解包，未在服务器使用 git。
- 部署后 `worldcup.service` 与 `nginx` 均为 active；公网 `/healthz`、`/api/matches`、首页和 `/preview` 均返回 200，`/api/matches` 返回 70 场。
- 线上 HTML 已验证包含 `history-workbench-shell` / `history-match-row`，旧 `class="finished-row"` 与 `class="finished-table"` 不存在，closing 复盘口径和研究免责声明保留。
- 线上 Chrome QA：历史页显示 2 场，`胜平负` 分类显示 3 条信号，首条信号可展开；390×844 视口无页面级横向溢出，二次 console 与 4xx/5xx response 采样为空。
- 线上 Chrome 复测时发现浏览器自动请求缺失的 `/favicon.ico` 会产生 console 404，已在页面 head 增加空 `data:,` favicon 声明并补充测试，避免无关资源缺失污染前端 QA。
- `worldcup.ops_check` 主链路显示当前 release、服务、公网接口均正常；总检查非零来自既有本地日志敏感词计数和 Nginx 历史 5xx/upstream 累计窗口。按 21:18 发布后窗口过滤，`worldcup.service` 关键词命中 0，Nginx 5xx 命中 0。
- 本次上线只切换页面渲染代码并重启服务，未触发 live refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-12 高级赛事情报台视觉重做

- 按 Product Design 生成的“高级赛事情报台 Premium Match Intelligence Workbench”方向重做研究台账视觉：顶部主导航、轻量日期 timeline、日期下方筛选行、左右 workbench、右侧 KPI 条、盘口 tabs 与信号表格。
- 修复等级列视觉：移除旧 `grade-priority` 大块样式，S/A/B/C 统一为小 badge；列表/表格内默认 28×22，KPI 区 30×24，文字居中；颜色改为 S=香槟金、A=靛紫、B=绿色、C/D=slate 灰。
- 左侧比赛队列补充国旗与中文队名别名，`Bosnia and Herzegovina` 显示为 `波黑`、`United States` 显示为 `美国`；对阵列固定 22% 宽度，表格 `table-layout: fixed`，左侧不再出现横向滚动条。
- 左侧比赛队列列顺序调整为 `开赛时间 → 对阵 → 最强等级 → 组别 → 信号数 → 最高 EDGE`，让等级紧跟在对阵信息后面。
- 右侧 workbench 信号行恢复可展开交互，每条信号可点击/键盘展开轻量明细，包含盘口方向、预测状态、模型/市场概率、EV/EDGE、等级、新鲜度和信号原因。
- 右侧信号表列顺序调整为 `市场/盘口 → 等级 → 预测 → 模型概率 → 市场概率 → EV/EDGE → 新鲜度 → 信号原因`，让等级成为第二视觉判断点。
- 新增过期/stale 展示层：过期信号行降权、显示 `过期` 小 badge，信号原因改为“盘口数据已过期，等待下一轮刷新。”，右侧明细底部显示轻量 warning strip。
- 旧 `按比赛/按信号` 模式栏继续保留 DOM 兼容但隐藏，首屏顺序调整为日期 timeline → 筛选 → workbench，更贴近设计目标。
- 本地验证：新增/调整预览契约测试，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `369/369 tests passed`。
- Browser QA：刷新 `/tmp/worldcup-grade-badge-preview.html` 后用 `127.0.0.1:18766` 静态服务检查 1440×1024、390×844 和当前浏览器视口；页面级无横向溢出，左侧队列表格无横向溢出，左侧等级列紧跟对阵列，右侧等级列保持第二列，右侧表格内部滚动，右侧首条信号可展开，console error/warn 为空。
- 已提交并推送 `a3f5c0b feat: refine premium workbench layout` 到 `origin/main`，并部署到 ECS `/opt/worldcup/releases/a3f5c0b`；部署使用本地 `git archive` + `scp/ssh` 解包，未在服务器使用 git。
- 部署后 `worldcup.service` 与 `nginx` 均为 active；公网 `/`、`/preview`、`/api/matches`、`/healthz` 均返回 200，`/api/matches` 返回 70 场，页面保留研究免责声明且禁词扫描为空。
- 线上 HTML 已验证左侧队列表头顺序为 `开赛时间 → 对阵 → 最强等级 → 组别 → 信号数 → 最高 EDGE`，首行对阵在等级前，波黑/美国未回退英文；最近 5 分钟 `worldcup.service` 日志错误/敏感词扫描为空。
- 本次未触发 live refresh、未调用 The Odds API、未写入新 snapshot；本地临时预览服务继续保持可访问。

## 2026-06-12 2022 世界杯 OddsPortal 赔率移动粗检

- 按修订后的漏斗式计划执行：不硬跑 AH 全盘口枚举；先用 OddsHarvester / OddsPortal 的 1x2 + OU 2.5 做 2022 世界杯粗检，AH 仅在粗检通过时再投入。
- Task 4 数据结果：results 翻页得到 64 个 match link；1x2 与 OU 2.5 最终各抓到 63 场，唯一缺失 `2022-12-09 Brazil-Croatia`（多次重试仍因 H2H fragment 匹配失败）；`wc2022_history.csv` join 结果为 `scraped=63`、`joined=63`、`unmatched=[]`。
- 数据 sanity：`rows=63`、`full_close=63`、`full_open=63`、`odds_home_moved_ge_2pct=56`。
- 新增 `worldcup.oddsportal_wc2022` 的反向中立场 join 修正：当 `intl_history.csv` 主客顺序与 OddsPortal 相反时，按 OddsPortal 主客口径翻转比分与赛前 Elo；测试覆盖 Netherlands/Qatar 一类风险。
- 新增 `worldcup.line_move_report`：输出 `by_1x2_move` 与 `by_abs_move` 两个维度；当前 AH 未抓，`by_abs_move=[]`。
- Task 5 Step 6 检查点数字：`<2%` 桶 `n=7`、`hit_rate=0.1429`、`mean_return=-0.7386`、`model_brier_1x2=0.388`；`2-5%` 桶 `n=10`、`hit_rate=0.3`、`mean_return=0.225`、`model_brier_1x2=0.609`；`5-10%` 桶 `n=18`、`hit_rate=0.2353`、`mean_return=1.4494`、`model_brier_1x2=0.5927`；`>=10%` 桶 `n=28`、`hit_rate=0.2222`、`mean_return=0.0807`、`model_brier_1x2=0.689`。
- 大移动桶合计 `n_matches=46`、`n_signals=44`、`hit_rate=0.2273`、`mean_return=0.6095`、`model_brier_1x2=0.6513`；相对 `<2%` 桶只有 Brier 更差，命中率与单位回报没有更差，因此不满足"至少两项方向性更差"，已跳过 Task 5.5，不继续 AH 限定抓取。
- 研究文档已写入 `docs/research/2026-06-12-wc2022-line-move.md`；既有回测器样本为 `n_matches=63`、`n_1x2=63`、`n_ou=63`、`n_ah=0`，1x2 `model_matched.brier=0.6153520445339987`、`market.brier=0.5760549007391181`，OU 2.5 `model_matched.brier=0.48168658556084926`、`market.brier=0.4781110268749646`。
- 本轮未付费、未调用 The Odds API、未 push、未部署；爬取原始产物和报告 JSON 均在被忽略的 `data/local/backtest/`。

## 2026-06-12 研究台账工作台布局上线

- 已将本轮研究台账工作台布局推送并部署到 ECS；发布使用本地 git archive + scp/ssh 解包，重启 `worldcup.service`，未在服务器使用 git。
- `worldcup.service` 与 `nginx` 部署后均保持 active；公网 `/healthz`、`/api/matches`、首页和 `/preview` 均返回 200，`/api/matches` 返回 70 场。
- 线上页面已包含新版 `.ledger-workbench` 和 `.date-strip`；in-app Browser 验证左侧比赛切换右侧明细、`让球` 分类只显示 `handicap` 信号，console 日志为空。
- 页面与 API 资金/下注禁词扫描未命中；`worldcup.service` 近 15 分钟 journal 中 error/secret-like 关键词命中 0。
- 本次部署只切换页面渲染代码并重启服务，不触发 live refresh、不调用 The Odds API、不写入新 snapshot。

## 2026-06-12 研究台账工作台布局改版

- 实时信号区改为参考图风格：顶部日期胶囊、筛选栏、左侧比赛列表、右侧选中比赛信号明细；默认仍按比赛聚合，按信号旧 DOM 作为兼容层保留。
- 右侧明细新增盘口分类 tabs：`全部 / 胜平负 / 让球 / 大小球`，点击左侧比赛会切换当前明细，点击分类只过滤当前比赛信号。
- 旧摘要指标移动到主工作台下方，首屏优先展示筛选和两栏台账；左侧比赛列表只保留信号数、最强等级和最高 EDGE，避免盘口 chips 挤压。
- 页面继续保留研究免责声明，不新增下注金额或资金相关字段；本次不改模型、数据、采集、云端 ingest 或业务文字。
- 本地验证：新增工作台布局 DOM 契约测试，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `349/349 tests passed`。
- Browser QA：临时 `127.0.0.1` 静态服务检查桌面和 390px 视口；工作台可见、点击比赛切换明细、盘口分类联动正常，console error/warn 为空；根级横向滚动隐藏，日期条和表格在各自容器内横向滚动。

## 2026-06-12 比赛分组台账与范围日期筛选

- 台账日期筛选从逐日按钮改为固定范围：`全部 / 今日 / 明日 / 未来3天 / 未来7天 / 选择日期`；具体日期进入 `#date-picker`，选项显示 `日期 · 场次 · 信号数`。
- 实时信号默认新增 `按比赛` 模式：每场比赛一条主行，展示信号数、盘口类型、最强等级、最高 EV/Edge，并可展开该场全部信号；原信号流水保留为 `按信号` 模式。
- 实时、历史行都补充北京日期 ISO，用于前端范围筛选；历史回顾继续共用日期/赛事/等级/搜索过滤。
- 本地验证：新增预览契约测试先红灯后转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `348/348 tests passed`。
- Browser QA：本地静态预览桌面默认 `按比赛`，71 场比赛 / 497 条信号，展开首场显示 7 条信号；切换 `按信号` 正常；`今日` 过滤显示 1 个日期分组 / 7 条信号；390px 视口页面级无横向溢出，日期和比赛表格都限制在内部滚动。
- 本轮代码将随本次确认推送并部署；部署只切换页面渲染代码并重启服务，不触发 live refresh、不调用 The Odds API、不写入新 snapshot。

## 2026-06-12 台账筛选上线与联赛筛选补强

- 已将台账日期筛选、赛事筛选、视图切换和历史回顾入口推送并部署；当前线上 release 为 `e2375fc`，路径 `/opt/worldcup/releases/e2375fc`。
- 部署后补强了联赛筛选脚本：过滤时直接读取 `#league-filter` 当前值，并同时监听 `input` / `change`，避免下拉值与列表状态不同步。
- 本地验证：新增回归断言先红灯后转绿，最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `347/347 tests passed`。
- 本次部署只切换页面渲染代码并重启 `worldcup.service`，未触发 live refresh、未调用 The Odds API、未写入新 snapshot。

## 2026-06-12 台账筛选区与历史回顾入口

- 首页/预览页台账顶部新增一级视图切换：`实时信号` / `历史回顾`；历史回顾面板默认隐藏，切换后显示已完赛战绩或空状态。
- 筛选区改为日期、赛事、等级、搜索四类：日期按钮由当前实时信号日期动态生成；赛事下拉先以 `世界杯` 为真实数据来源，并预留英超/西甲/意甲/德甲/法甲/中超选项，暂不伪造联赛数据。
- 实时信号行与历史行都写入 `data-date` / `data-league` / `data-grade` / `data-search`，前端同一套过滤状态可叠加视图、日期、赛事、等级和搜索。
- 日期按钮较多时限制在控件内部横向滚动；桌面与 390px 移动视口检查无页面级横向溢出，历史切换能正确隐藏实时表格。
- 本地验证：先看到新增预览 DOM 契约测试红灯，再实现转绿；最终 `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` 通过 `347/347 tests passed`。
- 本次只改本地静态预览渲染层与测试，未 push、未部署、未触发 live refresh、未调用 The Odds API。

## 2026-06-12 推送与 ECS 部署

- 已将本地 `main` 推送到 `origin/main`：`70c75f9 docs: add finished record and odds trend plan`。
- 已将 release `70c75f9` 部署到 ECS：`/opt/worldcup/current` 已从实际线上 release `/opt/worldcup/releases/90b7d94` 切到 `/opt/worldcup/releases/70c75f9`；部署使用本地 git archive + scp/ssh 解包，未在服务器使用 git。
- `worldcup.service` 已重启并保持 active；`nginx` 保持 active。公网 `/healthz` 返回 200，`/api/matches` 返回 71 场，首页和 `/preview` 返回 200 且保留免责声明；公网 `/api/snapshot/latest` 仍返回 404。
- 页面与 API 资金/下注禁词扫描未命中；`worldcup.service` 部署后 15 分钟 journal 敏感词/error 命中 0；`football.celab.xin` 专属 Nginx access/error 日志 tail 敏感词和 5xx/upstream 命中 0。
- in-app Browser 验收通过：桌面与 390px 视口均能渲染，标题为“2026 世界杯 | 研究台账”，免责声明存在，页面级无横向溢出，console error/warn 为空。
- 当前线上最新 snapshot 仍是部署前 run `20260612T014553Z-live`，`snapshot.finished` 不存在，因此页面暂时没有“本届信号战绩”卡和“已完赛战绩”区是预期；下一轮本机 live 发布生成并入库新 snapshot 后才会显示。
- 本次部署未触发 live refresh、未调用 The Odds API、未写入新 snapshot；P3 页面渲染代码、Plan A“更新规则”卡新文案，以及此前未部署的 key 轮换、自算 Elo、额度告警、scores 接入等代码已随 release 上线。

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
