# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

历史归档：[docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md](docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md)（164 条）

较早记录压缩摘要（2026-07-10 至 2026-07-19）：完成 MatchPick v3、首选鲜度、中超俱乐部评级门槛、已开赛待赛果展示、延期状态、quota 槽位切换和世界杯赛后同步等阶段；当前槽位数以最新记录和 README 为准。保留的关键约束已同步到 README、AGENTS/CLAUDE 与 Git 历史。2026-07-19 首次赛后 live 因当时外部配置受阻，未产生业务写入。

## 2026-08-24 The Odds API 五 Key 轮换

- 显式 Key 槽位从 primary / secondary / tertiary 扩展为 primary / secondary / tertiary / quaternary / quinary，保持现有顺序轮换和低额度策略：当前槽位剩余额度 <=30 时优先选下一个未探测或 >30 的槽位，全部无新鲜额度时才选仍有余额的最早槽位，全耗尽则暂停。
- 同步 `worldcup.theoddsapi_keys`、readiness 必需变量名、scheduler/publish 安全提示、`.env.example`、README 和 AGENTS/CLAUDE；真实 Key 未写入文件，聊天中暴露的 Key 仍应撤销后重生。
- TDD 分别验证第四、第五槽 RED→GREEN，readiness 缺槽变量名 RED→GREEN；聚焦 Key 测试 `13/13`，完整回归 `1129/1129 tests passed, 1 optional fastapi module skipped`，`py_compile` 与 `git diff --check` 通过。未读取/修改 `.env`、未联网、未消耗 quota、未 commit/push/deploy。

## 2026-08-24 六联赛单场分析离线闭环

- 意甲、巴甲、西甲、英超、德甲、法甲新增正式但默认关闭的 `league_v1` profile、市场共识 snapshot、单场页面固定入口与状态、严格赛前 closing、90 分钟赛果验证门、结算、分联赛统计和六联赛汇总。
- batch runner 默认零写入 dry-run；live/write 明确阻断为 `live_acceptance_not_enabled`。未读取 `.env`、未联网、未消耗 quota、未生成正式 closing/统计、未发布或部署。
- TDD 聚焦验证通过；共享 `load_config()` 污染由测试内 deep copy 修复并复现验证；批处理补充单联赛失败隔离与 `partial` 状态回归；最终完整回归 `1148/1148 tests passed, 1 optional fastapi module skipped`，六个新模块 `py_compile` 与 `git diff --check` 通过。
- 后续 live 激活设计已确认：六联赛同时参加赛程发现，按最近 kickoff 动态优先，逐联赛独立通过 odds/球队身份/90 分钟 scores 门禁后启用；真实请求逐 key 预估成本并在每次响应后重算 quota，已开赛场不得补造首选。设计阶段未联网、未读 `.env`、未消耗 quota、未解除 live 门或部署。

## 2026-08-20 中超赛后样本 Sentinel

- 已确认独立 `worldcup.csl_postmatch_sentinel` 方向：复用“双源赛果接受 → postmatch shadow”触发，只监控数据链路异常、恢复和正式样本首次达到 50；不根据命中率或盘口方向报警，不自动调参或解除 `club_rating_pending`。
- 当前真实 ignored shadow 只读基线为 174 场已验证结果、46 场 closing、38 个正式 decision，`20 hit / 18 miss`，`sample_too_small=true`；现有 128 个 closing 缺口和 8 个 decision 缺口只作为通知基线，coverage 仍完整保留，不声称已修复。
- 已实现 `worldcup/csl_postmatch_sentinel.py`（严格输入契约、纯 evaluator、锁/原子 state/outbox、脱敏 CLI）与 `worldcup/csl_scheduled_publish.py`（accepted result → shadow success → sentinel → odds 的非阻断接线、`--no-notify`）；对应测试为 `tests/test_csl_postmatch_sentinel.py` 与 `tests/test_csl_scheduled_publish.py`。sentinel 摘要仅存在 scheduler-local 返回值，最终 publish-body 捕获测试保证其不进入公开 snapshot 或 HMAC body。
- Fix Round1：合法 standalone dry-run / CLI 从同一次已验证 projection 输出严格 `decision_count` 与 `sample_too_small`，不二次读取报告；错误/输入不可读时省略。scheduler 白名单继续丢弃这两个字段，既不扩大其 local result，也不进入公开 snapshot/HMAC body。
- 最终验证：`py_compile` 通过；聚焦 sentinel `31/31`、scheduler `45/45`；指定 runtime 完整回归为 `1126/1126 tests passed, 1 module skipped (optional fastapi)`。实现已通过 PR #5 squash merge 到远端 `main` commit `8a500bec65bc50a26d2043ec9edd5817bfcea70d`；本地主工作区已对齐该提交，旧本地 `main` 完整备份为 `codex/local-main-before-sync-20260820`。
- 经单独批准后，真实 ignored baseline 已用同步后的 `main` 显式 `--write` 激活且未传 `--notify`：首次返回 `status=stored`，第二次返回 `status=unchanged`，两次均为 `event_count=0`、`notification_status=not_attempted`、`decision_count=38`、`sample_too_small=true`。state 固定记录 128 个 closing 缺口、8 个 decision 缺口、38 个正式样本，`threshold_notified=false`、active/outbox 均为空；第二次运行 state SHA256 保持 `b8f3d8064d09dad6ca6a9485243f904027bfbda48f8b8bd86bedcb86e6f491e1`，敏感字段扫描为空。
- 现有 `xin.celab.football.csl-scheduled-publish` LaunchAgent 继续每 900 秒唤醒 `worldcup.csl_scheduled_publish --live`，最近退出码为 0，不新增 timer；只读 `worldcup.ops_check` 返回 `ok=true`、0 errors。设计文档：`docs/superpowers/specs/2026-08-20-csl-postmatch-sentinel-design.md`；实施计划：`docs/superpowers/plans/2026-08-20-csl-postmatch-sentinel.md`。未创建 `ARCHITECTURE.md`，未发送真实 WxPusher、未调用 provider、未新增 quota 消耗、未部署。

## 2026-08-13 中超 closing coverage foundation 文档与真实本地验收

- 同步 `README.md`、`docs/superpowers/data-contract.md`、`AGENTS.md` 与 `CLAUDE.md`：初始 128 个 match ID 是固定重建资格 membership，`2026-06-29` 仅为 bootstrap cutoff；canonical report 做全量 finished/history reconciliation，pending 只承接恢复；正式 headline 仅结算 observed schema v2 `match_pick_v3` 的 `MATCH_PICK`，reconstructed 保持独立且不得混算。本轮 implementation/documentation 已通过 PR #2 squash merge 到远程 `main` commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`，CI `tests` 通过；已部署到 `/opt/worldcup/releases/5d006be240fd42ef320e0e5ec1aee69992f0e9c9`，内部 health/readiness 与公网 `/healthz`、`/api/matches`、`/preview` 均验证通过。本条记录随 SSH 部署加固分支纳入本地版本历史。
- 最终完整项目验证：指定 runtime `1062/1062 tests passed`，`test_fastapi_app.py` 因 optional `fastapi` 不可用显式跳过 1 个 module。真实 `--initial-manifest` dry-run 返回 `matches=128`、`observed_cutoff=2026-06-29`，且运行前后 manifest/report/pending 均不存在，确认零写入。最终对抗性审查后，report strict validator 还强制 `initial_missing_count=128` 与固定 membership SHA256，重算 derived fields / fingerprint 后的 127-ID 子集或替换 128-ID 均 fail closed；真实 report 只读校验通过且文件 SHA256 不变。
- 显式 ignored 写入后，`initial_missing_manifest.json` 有 128 个唯一 ID，固定 membership hash 为 `530acaa872d753c911861e2cab1e1bf6a2a0a87c595028d9c5e369523a7f6a40`，日期范围 `2026-03-06..2026-06-28`，每行均通过精确 UTC kickoff 与 `cfl_official` / `sevenm` 双 source ID 复验。canonical report 覆盖 171 个 accepted `csl_2026` / `2026` results：43 observed closing、35 observed current decisions、8 observed missing-current-decision、128 missing，正式 observed tally 为 `17 hit / 18 miss / 0 push / 0 no_pick`，`sample_too_small=true`；没有 reconstructed tally 或 combined rate，不能据此声称重建提高胜率。
- 幂等与安全：第二次 manifest/report 写入均返回 `unchanged`；manifest SHA256 `9698786a6f5eed01d8a0cc7990c29a6fa63f6b33cafe93145b08cb6c9a6707da`、report SHA256 `7357d524770d89fb49f9a334b30c6684b2adf27236a2b10dc9234eab0f8e2211` 前后不变，pending 成功清除。两份 ignored artifact 对 `Authorization`、`Cookie`、`api_key`、`secret`、`.env` 和 request headers 的扫描为空。`csl_scheduled_publish` 原样命令返回 `status=dry_run`、`refresh=null`、`publish=null`，manifest/report/quota hash 与 mtime 不变，未读取 `.env`、未调用 provider、未消耗 quota、未发布或写 DB。
- 后续边界：历史 reconstruction 仍须先确认具体 source，完成 terms/robots/retention/reuse/rate-limit 审批，再单独确认小样本联网并把真实 raw samples 保存到 `data/probe/csl_historical_odds/<source_id>/`；只有之后另起 source-specific plan，才能实现离线 parser、quote-time interval、immutable bundle 与 reconstructed-only report，不得改变本轮 observed report 语义。

## 2026-08-12 中超本地 postmatch shadow 闭环

- 新增 `worldcup.csl_postmatch_shadow`：只结算 2026 赛季已验证赛果，严格选择开球前最后一份合法 closing，复用 `settle_match_decision()` / `summarize_decision_records()` 产出 decision-only tally、coverage、分组和 `p_hit_safe` Brier 校准；2023–2025 replay 仍只供评级/pending gate，不误记为当季 closing 缺口。
- CLI 默认 dry-run 零写入；显式 `--write` 先 staging 生成 eval/backtest/gate/shadow，交叉校验后逐文件原子提升，report 倒数第二、state 最后提交，并用 report hash + input fingerprint 绑定完整成功。相同输入返回 `unchanged`，提升失败回滚旧产物并只保留安全错误码。
- `csl_scheduled_publish` 在双源赛果接受后、odds refresh 前触发 shadow；源阻断时不运行，shadow 异常只记 `csl_postmatch_shadow_failed` warning，不阻断赛前 snapshot 生成/发布，不把 shadow 摘要持久化进常规发布 snapshot。
- 真实 ignored 数据验收：2026 赛季 171 场已验证赛果，43 场有 closing，35 场当前策略可结算，`17 hit / 18 miss / 0 push / 0 no_pick`，命中率 48.57%，`sample_too_small=true`；8 月 9 日三场均为 `OU over 2.5`，结算 `2 hit / 1 miss`。重复写入返回 `unchanged`，report/state hash 一致，敏感/原始 provider 字段扫描为空。
- 验证：聚焦回归 `61/61`，项目 runtime `968/968 passed, 1 optional fastapi skipped`，`py_compile` 和 `git diff --check` 通过。本阶段未读取 `.env`、未联网、未调用 The Odds API、未消耗 quota、未修改公开 API/preview、未发布、未部署、未改 LaunchAgent、未 commit/push。

## 2026-08-01 生产 Nginx 每日精选路由纳入 Git

- 新增版本化模板 `deploy/nginx/worldcup-daily-picks.conf`，只声明 `/api/daily-picks`、`/daily-picks`、`/api/daily-picks-sidecar`、`/daily-picks-sidecar` 四个 `location =`，统一代理到 `127.0.0.1:8788`；不扩大 `location /`，不含证书、secret、token、账号或 `.env`。
- 新增 `worldcup.nginx_routes`：只在目标 `server_name football.celab.xin` 中移除这四个旧 exact location 并加入一个 managed include；snippet/site 原子替换，先备份到 `/root/nginx-backups`，幂等时无副作用，`nginx -t` 失败不 reload 并恢复旧文件，reload 失败也恢复旧文件并尝试旧配置恢复 reload。
- `worldcup.ssh_deploy` live 远端流程接入 release 内模板和安装器；保持既有 bind-address、release/current 原子切换、旧路由、service restart、readyz warmup、公网 smoke 与 rollback 语义不变。dry-run 仍只读 Git，不 archive、SSH、写远端 Nginx 或 reload。
- TDD 新增 `tests/test_nginx_routes.py`，覆盖四个 exact route、模板安全、规范化幂等、备份/原子安装、`nginx -t` 失败恢复且不 reload、reload 失败恢复、ssh deploy 接线和 dry-run 无副作用。
- 验证：定向 Nginx 测试通过；项目 runtime `950/950 passed, 1 optional fastapi skipped`；未访问生产、未 live deploy、未调用 provider、未读取 `.env`、未消耗 credits。

## 2026-08-01 每日赔率 sidecar live 刷新闭环

- 保留上一阶段全部 dirty 改动，未 reset/checkout/clean，未 commit/push/PR；先恢复 `tests/test_daily_odds_refresh.py` 中被吞并的同 key 测试边界，再补 timezone、自然日、一次请求、多 event、quota、完整 h2h、标准化快照和 writer 失败状态断言。
- `worldcup.daily_odds_refresh` 现以单次 planner 结果驱动 live：同 `sport_key + anchor` 一次 odds fetch，T-6/T-90=`h2h`、T-25=`h2h,spreads,totals`；只保留北京时间当日未开赛 event，排除次日/已开赛/重复 ID/改期/不完整 h2h/过期赔率；一次 response 同时生成安全 event rows、全局 Top4、2串1、3串1 和 snapshot payload。
- 新增 `worldcup.daily_odds_state.DailyOddsState`，原子保存 `sport_key|anchor` committed keys；只有 `DailyOddsSnapshotWriter` 成功后才提交，重启可复用 state，writer 失败不会伪造成功。新增日预算与 provider remaining 硬 guard，部分失败只重跑失败 key。
- `worldcup.daily_odds_store` 扩展标准化白名单和跨运行 event identity/kickoff 校验，继续原子写入独立 `data/cache/daily_odds/`，不保存 raw provider/bookmaker/API key/secret。新增 `query.load_daily_sidecar_snapshot()` / `project_daily_sidecar()`。
- 新增只读 `/api/daily-picks-sidecar` 与 `/daily-picks-sidecar`；旧 `/api/daily-picks`、`/daily-picks`、旧单场 API/预览仍走原 snapshot projection，不会因刷新联网。FastAPI 路由同步声明。
- 文档已同步到 `README.md`；未创建 `ARCHITECTURE.md`。项目 runtime 完整回归 `942/942 passed, 1 optional fastapi skipped`；focused daily sidecar/旧菜单回归通过；`py_compile` 与 `git diff --check` 通过。未读取 `.env`、未调用真实 provider、未消耗 odds credits、未启动服务、未部署。
- 当前最终状态：代码本地完成；`worldcup.ssh_deploy --root /Users/eagod/ai-dev/足彩 --ref HEAD` dry-run 已执行并按设计返回 `status=blocked, reason=dirty_worktree, dirty_files=31`，未 SSH、未 archive、未重启服务、未切换线上 current。服务尚未部署，自动 daily odds 抓取保持关闭；没有 commit/push/PR、没有读取 `.env`、没有真实 provider/odds/credits、没有线上写入。

## 2026-08-01 真实 provider 验证后的 17 联赛接入

- 将已通过真实 The Odds API `/sports` + `/events` 只读验证的 17 个联赛接入现有 `worldcup.competitions` profile 与 `worldcup.daily_competitions` catalog：中超、英超、英冠、德甲、德乙、法甲、意甲、西甲、瑞典超、挪超、丹超、芬超、墨西哥超、J 联赛、K 联赛、巴西甲、美职联；墨西哥甲、澳超、阿根廷超仍无 profile/`sport_key`，保持 `code_reserved` / fail-closed。
- 英超消歧：排除俄超、非足球和 outright/winner 类候选后，`soccer_epl` 为唯一主赛事 key；`/events` HTTP 200，返回 10 场完整 event identity。德甲消歧：排除奥地利、女子、手球和其他非主赛事候选后，`soccer_germany_bundesliga` 为唯一主赛事 key；`/events` HTTP 200，返回 11 场完整 event identity。两者均无重复 event、无 identity 异常。
- `/sports` 返回 174 个 sport；本阶段只调用 `/sports` 与每个确认 key 一次 `/events`，响应 quota headers 保持 `remaining=500 / used=0 / last=0`，未调用 `/odds`、`/scores` 或历史赔率，未消耗赔率 credits、未写 raw payload。
- 保留旧 catalog 顺序和中超 `resolve_sport_key` 的双 candidate/显式 key 兼容；daily sidecar 使用已验证 exact key。`daily_odds_refresh` 默认仍 disabled，只有显式 `enabled=True, live=True` 才进入 odds fetcher；未改 `local_runner.py`、`match_decision.py`、旧单场查询或 snapshot 契约。
- TDD 先出现 3 个兼容红灯，最小修正后项目 runtime 完整回归 `938/938 passed, 1 module skipped (optional fastapi)`；新增 17 联赛 resolved-catalog、3 联赛 fail-closed 和 exact profile/key 测试。未启动服务、未部署、未 commit/push。

## 2026-07-31 每日赔率 sidecar 与动态 provider 状态

- 在不改旧单场 `match_decision` / `local_runner` 主路径的前提下，新增 `worldcup/daily_odds_refresh.py` 注入式 sidecar：依赖注入 `sports/events/odds fetcher` 与 snapshot writer，默认不自动运行。
- 20 联赛白名单保留：中超仍为已知配置；英超/德甲/法甲/意甲/西甲保留候选 `sport_key`；其余 14 个仅保留名称和 `code_reserved`，不猜 `competition_id` 或 provider key。运行时只接受 `/sports` exact active key，缺失/inactive 显示 `provider_unavailable`。
- 同一 `sport_key` 的未来赛程在同一波只发一次 odds 请求；不同 key 分别请求；T-6h/T-90m 为 `h2h`，T-25m 为 `h2h,spreads,totals`。已开赛、无未来赛程、重复锚点、quota 不足和同 event 多 kickoff 均 fail-closed。
- 扩展 `worldcup.sources.theoddsapi` 的 `/events` URL 与注入式 `fetch_events_for_sport`，继续复用现有 `fetch_json_from_url` 和 `quota.update_quota_from_headers`；旧世界杯 odds 封装保持兼容。
- `odds_movement` 只记录 `shadow_only` / risk metadata，不参与 Top4、2串1、3串1 排序或 tie-break，不进入公开 raw bookmaker/odds 字段；每日精选继续复用既有 snapshot/query/routes/menu。
- TDD 先捕获 provider 目录、同 key 批量、跨 key、三锚点 markets、改期/quota/幂等和 `/events` source 边界失败，再修复实现。定向与完整验证：`928/928 passed, 1 optional fastapi skipped`；未联网、未读 `.env`、未启动服务、未调用真实 provider、未消耗 quota、未 commit。

## 2026-07-31 每日赔率本地接线（默认关闭）

- 新增独立 `worldcup.daily_odds_store.DailyOddsSnapshotWriter` 与 `data/cache/daily_odds/daily_odds_snapshot.json` 命名空间：原子临时文件 + `fsync` + `os.replace`，校验 `competition_id`、`event_id`、`commence_time`、fixture identity，改期 event fail-closed；禁止写入旧 `analysis_snapshot.json` / 联赛 snapshot 名称。
- writer 只保留 provider catalog、sport/competition、anchor、markets、event IDs、fixture identity 和 `odds_movement=shadow_only` 风险元数据；注入 payload 中的 raw bookmaker/provider odds 不进入文件，也不污染旧 cache/history。
- `worldcup.daily_odds_refresh.plan_daily_odds_refresh()` 只基于注入的 `/sports` 等价列表和 `/events` 等价 fixtures 生成 due anchor、markets、estimated credits、fixtures、skip reason；不创建 transport、不调用 odds、不写文件。
- `worldcup.scheduled_refresh.run_daily_odds_refresh()` 默认返回 `disabled`；显式 `enabled=True` 但 `live=False` 只做 planner dry-run；仅显式 `enabled=True, live=True` 才调用注入的 `odds_fetcher` 和 writer。旧 `run_scheduled_refresh()` 及其 CLI parser/返回契约保持不变，未注册 scheduler/LaunchAgent。
- TDD 先记录缺失 `daily_odds_store` 的红灯（项目 runtime `928/929`），实现后定向与完整回归均为 `935/935 passed, 1 module skipped (optional fastapi)`；`py_compile`、`git diff --check` 通过。全程未联网、未读 `.env`/密钥、未调用真实 provider、未启动服务、未消耗 quota、未 commit/push/deploy。

## 2026-07-31 每日精选 sidecar 初版

- 完成每日精选 sidecar：20 个联赛目录状态为中超 `enabled`、英超/德甲/法甲/意甲/西甲 `code_reserved`、其余 14 个 `unsupported`；未验证 provider ID / sport key 不猜测、不模拟。
- 新增动态 `/daily-picks` 与 `/api/daily-picks`，周期为北京时间 `18:00` 至次日 `18:00`，内部 UTC；全局 Top 4、同一 Top 4 派生 `2串1` / `3串1`，组合仅标记独立性近似研究分数并过滤同场/同队冲突。
- 复用现有 snapshot/public safe projection 和 scheduled refresh/publish；未新增 daily-picks scheduler、静态 export 或 LaunchAgent。旧单场算法、投影、路由和返回契约未改变。
- 验证：项目指定 runtime `tests/run_tests.py` 为 `921/921 passed, 1 optional fastapi skipped`；未启动服务、未联网、未消耗 quota、未部署、未 commit。

## 2026-07-20 19:30 UTC+8 阶段确认规则 + ssh_deploy 安全加固

- **阶段确认规则**：`CLAUDE.md` 和 `AGENTS.md` 各追加"阶段确认规则"段落（四个口令：确认实现/提交推送/合并/部署），内容同步。
- **ssh_deploy.py 加固**（TDD 红→绿）：
  - 远端脚本在 mv/ln 前拒绝 `[ -L "$release" ]`（防止部署到 symlink alias 导致共享目录污染）。
  - `previous` 解析改为 fail-closed：先判断 `$current` 是否作为文件系统条目存在（`[ -e ] || [ -L ]`）；只有真正不存在时才允许 `previous=""` 作为首次部署；current 存在但 `readlink -f` 返回空（broken/cyclic symlink）则打印 `current_unresolvable` 并 exit 1；解析非空后继续做 releases 前缀、-d、非 symlink 校验。
  - 使用 `flock -n` 非阻塞并发锁（`$releases_dir/.deploy.lock`），覆盖创建→切换→重启→验证全程。
- **测试**：新增 4 个 test_ssh_deploy 测试；全部 11/11 通过；完整 run_tests.py 897/897 + 1 skip (fastapi)。
- 未做：commit/push/PR/merge/deploy、依赖安装、CI/GitHub 设置、服务状态变更。

## 2026-07-20 17:50 UTC+8 部署修正：独立物理 release + symlink 污染修复

- **异常根因**：先前部署使用 `cp -a` 复制 release symlink（而非目录内容），导致所有 release alias 指向同一物理目录 `dd972e6e1626b8999ccee47ec3a837135180a288`；后续 `scp` 实际覆写了共享物理目录，旧 release alias 作为回滚点已失效。
- **修正操作**：
  1. 创建独立回滚物理目录 `pre-4c68f00-rollback-20260720T101500Z`（从当前物理目录 cp -a + 旧 blob 覆盖 6 文件，inode 独立，nlink=1）。
  2. 创建独立 main release 物理目录 `4c68f00-main-20260720T101500Z`（从当前物理目录 cp -a，298/298 文件一致，inode 独立）。
  3. 原子切换 `current` → 新物理目录（直接指向，不经 release alias symlink 链）。
  4. 服务重启并验证：PID=59900、NRestarts=0、healthz ok、readyz ready (9 场)、/api/matches 200、/preview 200。
- **回滚目录** 6 文件旧 hash 验证通过：store.py=b195b2b5578e、quota.py=98c33da4ab7c、refresh_runner.py=74773f90e600、decision_settlement.py=f1189742e90f、match_decision.py=a9c0c32e915c、collectors/csl_result_sources.py=8773495d3c35。
- **main release** 6 文件新 hash：store.py=23e01afe742b、quota.py=96c8650d77f9、refresh_runner.py=74a046f739c3、decision_settlement.py=7a2e1cf89162、match_decision.py=bc8c78541871、collectors/csl_result_sources.py=9fd01bf44086。
- **当前 current**：`/opt/worldcup/releases/4c68f00-main-20260720T101500Z`（物理目录，非 symlink）。
- **回滚路径**：`ln -sfn /opt/worldcup/releases/pre-4c68f00-rollback-20260720T101500Z /opt/worldcup/current && systemctl restart worldcup`。
- **今后禁止**：不得使用 `cp -a` 复制 release symlink 作为新 release；必须用 `cp -a $(realpath source)` 或 `cp -rL` 创建独立物理目录。
- 未做：commit/push、修改 .env/DB/LaunchAgent/监控、删除旧 release、ingest/publish。

## 2026-07-20 15:36 UTC+8 readiness profile 拆分 + 部署 + RECENT_WORK 归档

- 本地功能：`worldcup/readiness.py` 新增 `--profile` (full/server/publisher) 和 `--env-path` 参数；893 tests passed，1 optional module skipped (fastapi)。
- 归档：RECENT_WORK.md 164 条旧记录无损迁入 `docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md`，当前文件保留最近 20 条。
- 远端新 release：`dd972e6-secretcheck-20260720T070131Z-readiness-20260720T073603Z`，仅上传 `worldcup/readiness.py`。
- 回滚点：`dd972e6-secretcheck-20260720T070131Z`（保留在 `/opt/worldcup/releases/`）。
- 验证结果：healthz ok、server preflight ok (2/2)、/api/matches HTTP 200 (9 场)、/preview HTTP 200。
- 未做：commit/push、修改 .env/DB/systemd/监控/LaunchAgent、ingest/publish、quota 消耗。
- CI 修复（同阶段追加）：`tests/test_fastapi_app.py` bad-signature 断言对齐 `authentication_failed`；`tests/test_runner_resilience.py` 3 个测试改用虚拟包名隔离 optional module 可用性，不再依赖宿主是否安装 fastapi。CI venv 906/906 + 本地 893/893 + 1 skip 全绿。

## 2026-07-20 15:02 UTC+8 部署：INGEST_HMAC_SECRET 启动强校验上线

- **release**: `dd972e6-secretcheck-20260720T070131Z`
- **base release**: `dd972e6e1626b8999ccee47ec3a837135180a288`
- **manifest（9 文件）**:
  - `worldcup/secrets.py`（validate_hmac_secret + --check）
  - `worldcup/http_app.py`（启动校验 + 401 统一 + sqlite3 503）
  - `worldcup/fastapi_app.py`（load_secret 校验）
  - `worldcup/ingest.py`（CLI 启动校验）
  - `worldcup/publish.py`（CLI 启动校验）
  - `worldcup/scheduled_publish.py`（live fail-fast）
  - `worldcup/csl_scheduled_publish.py`（live fail-fast）
  - `worldcup/postmatch_publish.py`（validate 调用）
  - `worldcup/readiness.py`（weak_secret error）
- **回滚点**: `/opt/worldcup/releases/dd972e6e1626b8999ccee47ec3a837135180a288`
- **验证结果**:
  - healthz: `status=ok`
  - HMAC readiness: `ok`
  - 进程稳定, 端口 8788 正常
  - 本地 dry-run: `status=dry_run`, 无 quota 消耗
- **基线对照**: 新旧 release 在相同环境运行 readiness，12 项检查结果完全一致（HMAC 均 ok，整体 7 errors/3 warnings 均为既有环境缺项）；新版未引入任何新 failure。
- **未发生回滚**
- **未做**: commit/push、数据库迁移、.env 修改、依赖安装

## 2026-07-20 INGEST_HMAC_SECRET 配置边界强校验

- `validate_hmac_secret(secret)` 在所有配置/启动入口 fail-fast：
  - `scheduled_publish`: live=True 时在 env 加载后、refresh/pending/publish 之前立即校验；live=False dry-run 不要求 secret。
  - `csl_scheduled_publish`: 同上架构，返回 `{"status":"blocked","reason":"weak_ingest_hmac_secret"}`。
  - `postmatch_publish`: 在 endpoint/path 校验后、pending/fetch/write 之前校验。
  - `http_app` / `fastapi_app` / `ingest` / `publish`: 启动前 SystemExit。
  - `readiness`: 报 `"weak_secret"` error，不泄露值/长度。
- 校验规则：`None`/空/UTF-8 字节 < 32 → `ValueError("weak_secret")`。
- 底层 `verify_ingest_request` 不强制长度，只在配置边界做。
- 每入口单次校验，`resolved_secret` 复用到后续 publish 路径。
- 测试 `tests/test_secret_validation.py`（24 个）：中央验证边界、各入口 live 拒绝/dry-run 通过、exploding stubs 证明无副作用、readiness 输出安全、底层兼容。
- 既有测试中短合成 secret 全部升级为 ≥32 字节值。
- 全量：880/880 passed, 1 skipped (fastapi)。
- 未联网、未部署、未 commit/push。

## 2026-07-20 secrets --check：非阻断 secret 安全核验

- `worldcup/secrets.py` 新增 `--check` 模式和 `check_secret()` 函数。
- 用法：`python3 -m worldcup.secrets --check --env-file .env`
- 输出固定 JSON：`{"configured": bool, "minimum_length_ok": bool, "generator_format_ok": bool}`
  - `minimum_length_ok`：UTF-8 字节长度 ≥ 32（不声称保证熵）
  - `generator_format_ok`：完全匹配 `token_hex(32)` 的 64 位 lowercase hex（信息项，不影响 pass/fail）
- 退出码：configured + minimum_length_ok → 0；否则 → 1。
- 不改变任何现有启动/运行行为；默认生成命令不受影响。
- 读取使用与项目 `_load_env` 一致的解析语义（去引号、最后出现的值优先）。
- 文件不可读/非 UTF-8 时安全返回 `configured=false`，无 traceback、不泄露内容。
- 新增 `tests/test_secrets_check.py`（18 个测试）：兼容性、check 各布尔组合、退出码、引号/重复键、缺失/不可读文件、输出不含 secret。
- 全量：856 passed, 0 failed, 1 skipped (fastapi)。
- 未改变业务启动链路、未联网、未读真实 .env、未 commit/push/deploy。

## 2026-07-20 测试 runner 容错修复

- `tests/run_tests.py` 旧实现中单个模块加载失败（如缺依赖）会中断全部后续模块执行。
- 修复：模块加载用 `try/except`，`ModuleNotFoundError` 匹配显式 allowlist `_OPTIONAL_DEPS = {"test_fastapi_app.py": {"fastapi"}}` → SKIP；其余任何加载异常 → FAIL 并继续。
- 最终摘要明确给出 passed/failed/skipped 计数和详情；只有真 FAIL 时退出非零。
- 新增 `tests/test_runner_resilience.py`（12 个测试）：正常模块 PASS、断言 FAIL、SyntaxError 排在正常模块前仍继续（排序假阳性已修正）、缺失非可选依赖 FAIL、允许的可选依赖 SKIP、allowlist 文件名匹配但依赖名不符 FAIL、缺少内部模块 FAIL、普通 ImportError FAIL 后继续、顶层 RuntimeError FAIL 后继续、摘要计数精确、多模块继续执行、退出码语义。
- 全量运行：838 passed, 0 failed, 1 skipped (test_fastapi_app.py: fastapi)。
- 未安装依赖、未联网、未 commit/push/deploy。

## 2026-07-20 Ingest 401 统一：对外不泄露签名验证阶段

- `http_app.py` rejection 路径：当 reason 在 `_AUTH_REJECTION_REASONS`（`signature_format_invalid`/`signature_mismatch`）时，对外统一返回 `"authentication_failed"` 而非原始内部 reason。
- 内部 `ingest_server.py` / `ingest_app.py` 验证结果继续保留原有详细 reason 不变。
- 新增 `tests/test_ingest_auth_unify.py`（6 个测试）：两种签名失败产生相同 error code、body 不含内部阶段名/路径/tracebacks、未来时间戳 ±300s 窗口语义锁定。
- 更新 `tests/test_http_app.py:1035` 断言从 `"signature_mismatch"` → `"authentication_failed"`。
- 全量测试 826 passed, 0 failed, 1 skipped (fastapi)。
- 未改变内部验证逻辑、未联网、未消耗 quota、未 commit/push/deploy。

- `worldcup/collectors/csl_result_sources.py:parse_sevenm_fixture_result_rows` 原实现只靠 `_SCORE_RE` 过滤 `Scores_Arr`，未检查 `Stat_Arr` 值；若 7M 返回进行中临时比分可能误接受。
- 加固：在 score 正则前先检查 `Stat_Arr[index]`，经 `int()` 规范化后严格等于 `_FINISHED_STAT = 4` 才继续；其他值（13/17/未知/空/无法转 int）全部 fail-closed 跳过。
- 数组长度一致性检查、downstream 双源 compare/verified gate、`parse_sevenm_fixture_rows` 逻辑均未改动。
- 新增 `tests/collectors/test_sevenm_stat_filter.py`（12 个测试）：Stat=4 接受、Stat=4 无比分拒绝、Stat=17/13/0/1/99/空字符串+有效比分全部拒绝、多行对齐验证、2026 probe 120 行回归。
- 全量测试 820 passed, 0 failed, 1 skipped (fastapi)。
- 本地 960+ 条样本中 Stat=4 与有效比分 100% 对应，加固对现有合法完场输出零影响。
- 未改变下游契约、未联网、未消耗 quota、未 commit/push/deploy。

## 2026-07-20 亚盘结算 _settlement_unit 去重

- `decision_settlement.py:55` 和 `match_decision.py:342` 存在完全相同的 `_settlement_unit` 实现。
- 将 `decision_settlement.py` 中的函数改为公共 `settlement_unit()`（权威实现），保留 `_settlement_unit = settlement_unit` 别名兼容内部调用点。
- `match_decision.py` 删除重复定义，改为 `from worldcup.decision_settlement import settlement_unit as _settlement_unit`。
- 新增 `tests/test_settlement_unit.py`（7 个参数化测试）：30 组精确矩阵覆盖整数/半球/四分之一盘、5 组非法 line ValueError、identity 断言（两模块引用同一函数对象）。
- 全量测试 808 passed, 0 failed, 1 skipped (fastapi 可选)。
- 未改变业务语义、盘口方向、浮点容差、返回类型或异常行为；未联网、未消耗 quota、未 commit/push/deploy。

## 2026-07-20 SQLite WAL + HTTP 503 结构化错误

- store.py：新增 `_connect()` 辅助方法统一设置 `busy_timeout=5000ms`；`initialize()` 增加 `PRAGMA journal_mode = WAL`，`_initialized` 标志避免重复执行；所有方法的 `sqlite3.connect` 替换为 `self._connect()`。
- http_app.py：将 `/healthz` 之外的全部路由提取为 `_handle_store_routes()`，外层 `handle_request` 用 `try/except sqlite3.OperationalError` 捕获并返回 `503 {"error":{"code":"service_unavailable"}}`，不泄露路径、SQL 或内部堆栈。
- 新增测试 `tests/test_store_wal.py`（9 个：WAL 持久化、busy_timeout=5000、503 返回、信息不泄漏、正常 200 回归、healthz 不受影响）。
- 全量测试 801 passed, 0 failed, 1 skipped (fastapi 可选依赖)。
- 未改变业务语义、未联网、未消耗 quota、未 commit/push/deploy。

## 2026-07-20 quota.py 原子写+排他锁 & elo_world.tsv 原子替换

- 只读审查发现 `worldcup/quota.py` 的 `save_quota_ledger` 使用 `Path.write_text` 直接覆盖，无锁无原子保护；`worldcup/refresh_runner.py` 的 `elo_world_cache.write_text` 同样非原子。
- quota.py 修改：`save_quota_ledger` 改为 `tempfile.mkstemp` + `os.write` + `os.fsync` + `os.replace`；`update_quota_from_headers` 整体 load→modify→save 包裹在独立 `.lock` 文件的 `fcntl.flock(LOCK_EX)` 内，锁粒度覆盖完整事务。
- refresh_runner.py 修改：新增 `_write_text_atomic` 辅助函数（同目录 tmp + `os.replace`），异常或中断时自动清理临时文件，原文件保持完整。
- 新增测试 `tests/test_quota_atomic.py`（8 个，含跨进程 subprocess 并发验证）和 `tests/test_refresh_runner_atomic.py`（5 个，含 `os.replace` 失败时旧文件完整性验证）。
- 全量测试 792 passed, 0 failed, 1 skipped (fastapi 可选依赖)。
- 未改变业务语义、未联网、未消耗 quota、未 commit/push/deploy。
