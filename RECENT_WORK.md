# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

## 2026-08-04 每日精选 sidecar production adapter

- 在基线 `648b042` 上新增 `worldcup.daily_sidecar` production CLI：默认 dry-run 零 provider 调用；显式 `--live` 才读取三槽 key、按 quota 选择 provider，并接入 `/sports`、`/events`、`/odds` 到既有 `run_daily_odds_refresh` / `refresh_daily_odds`。daily budget 上限 85，provider/凭证/校验失败 fail-closed，输出只报槽位与 present/absent，不输出 secret。
- 新增显式 `--data-dir` / `WORLDCUP_DAILY_ODDS_DATA_DIR`：生产使用 `/var/lib/worldcup/daily_odds`，HTTP reader 与 writer 同一路径；保留 atomic snapshot/state、单实例 lock 与旧 `analysis_snapshot.json` / `/api/daily-picks` 链路隔离。
- 新增 Git 管理的 `deploy/systemd/worldcup-daily-sidecar.service` / `.timer`；SSH release 部署安装 unit、创建持久目录、`daemon-reload` 并明确 `disable` timer，不自动启用。live 成功、公网验收后才 enable。
- TDD 红灯先验证缺失 production module；实现后 sidecar/deploy 聚焦回归通过，待完整 runner、commit 与授权 ECS live 验收。

历史归档：[docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md](docs/history/RECENT_WORK_ARCHIVE_2026-07-20.md)（164 条）

## 2026-08-01 生产 Nginx 每日精选路由纳入 Git

- 新增版本化模板 `deploy/nginx/worldcup-daily-picks.conf`，只声明 `/api/daily-picks`、`/daily-picks`、`/api/daily-picks-sidecar`、`/daily-picks-sidecar` 四个 `location =`，统一代理到 `127.0.0.1:8788`；不扩大 `location /`，不含证书、secret、token、账号或 `.env`。
- 新增 `worldcup.nginx_routes`：只在目标 `server_name football.celab.xin` 中移除这四个旧 exact location 并加入一个 managed include；snippet/site 原子替换，先备份到 `/root/nginx-backups`，幂等时无副作用，`nginx -t` 失败不 reload 并恢复旧文件，reload 失败也恢复旧文件并尝试旧配置恢复 reload。
- `worldcup.ssh_deploy` live 远端流程接入 release 内模板和安装器；保持既有 bind-address、release/current 原子切换、旧路由、service restart、readyz warmup、公网 smoke 与 rollback 语义不变。dry-run 仍只读 Git，不 archive、SSH、写远端 Nginx 或 reload。
- TDD 新增 `tests/test_nginx_routes.py`，覆盖四个 exact route、模板安全、规范化幂等、备份/原子安装、`nginx -t` 失败恢复且不 reload、reload 失败恢复、ssh deploy 接线和 dry-run 无副作用。
- 验证：定向 Nginx 测试 `9/9` 通过；项目 runtime `951/951 passed, 1 optional fastapi skipped`；未访问生产、未 live deploy、未调用 provider、未读取 `.env`、未消耗 credits。


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

## 2026-07-19 世界杯赛后 live 首次执行受阻

- 用户确认真实赛果抓取、HMAC 发布与部署/定时任务后，先完成零副作用 dry-run，再执行 `worldcup.postmatch_publish --live`。openfootball 返回 104 场 fixture、103 场严格 `score.ft` 完赛比分，本地新增英格兰 1–2 阿根廷与法国 4–6 英格兰两场；更新了 ignored `data/cache/openfootball_2026.json`、`data/local/results/wc2026_results.csv` 和 `data/local/finished_record_store.json`，未调用 The Odds API、未消耗 quota。
- 英格兰 vs 阿根廷存在赛前 closing decision，可以形成第 102 条 finished；法国 vs 英格兰没有任何赛前 snapshot/closing，staged finished 明确记录 `finished_result_count=103`、`closing_available_count=102`、`missing_closing_count=1`。runner 按现有 all-or-nothing 契约返回 `new_result_missing_closing`，在构造 HMAC 请求和写 ECS 之前阻断，因此线上 finished 仍为 101，英格兰 vs 阿根廷仍未移出公开比赛列表。
- 公网只读复核 `/api/matches` 仍有 10 场，包含英格兰 vs 阿根廷和 4 场延期中超；`/api/finished` 仍为 101 场。ECS `worldcup.service` / Nginx active，SSH 绑定当前 Wi-Fi `192.168.31.46` 可用。
- 部署未执行：标准 `worldcup.ssh_deploy` 只允许归档可追踪 Git commit，当前工作区包含本轮未提交实现并会以 `dirty_worktree` 阻断。未绕过保护直接覆盖 active release，未安装/修改 LaunchAgent，未 commit/push；后续需明确确认“缺 closing 时透明记录并部分发布已有 closing 的完赛场”这一业务语义，以及允许本地 commit 后再走可回滚部署。
- 用户随后明确确认部分发布、本地 commit + 可回滚 ECS 部署，以及每天北京时间 16:40 的独立赛后 LaunchAgent。`postmatch_publish` 现只把 closing 缺失降为透明覆盖缺口，仍阻断源回退/重复、比分修订、finished 冲突和比分不一致；缺 closing 的比赛不补造首选，其他已有 closing 的完赛场继续发布，并同步写 `decision_coverage.missing_closing_count`、`skipped_no_closing`、`run.postmatch.missing_closing_count` / `partial_publish`。
- 新增 `worldcup.postmatch_launch_agent` 生成器，默认每天 16:40 执行独立 live runner，显式使用项目绝对路径和 HTTPS endpoint；生成器本身只输出/写 plist，不自行加载 launchd。TDD 聚焦回归 `31/31`，配置 runtime 完整回归（排除未安装的可选 FastAPI）`778/778`，系统 Python FastAPI `13/13`，合计 `791/791`；`compileall` 和 `git diff --check` 通过。
- 使用真实 104 场 openfootball cache、103 条 results、101 场 base finished 和本地 closing history 的临时副本完成无网络/假 publish 演练：成功生成 102 场 finished，`missing_closing_count=1`、`partial_publish=true`，英格兰 vs 阿根廷进入 finished 且公开实时列表为 0；所有产物只写临时目录。实际 commit、部署、HMAC 发布和 LaunchAgent 加载结果待后续步骤追加。
- 实现已本地提交为 `4af0eb7 fix: publish verified postmatch results`，未 push。标准部署 dry-run 为 `dry_run_ready`；首次 live 部署在 SSH banner 前超时并明确返回 `deployed=false`，远端 release/current/service 未切换。随后 TCP 22/443 仍可建立连接，但绑定 `192.168.31.46` / `en1` 的 SSH banner 与直连 HTTPS `/healthz` 均超时，因此未绕过标准部署或改本机路由。
- 真实 `postmatch_publish --live` 已构造 102 场 finished 完整候选，`missing_closing_count=1`、`partial_publish=true`；HTTPS ingest 未确认返回，outbox 安全保留绑定 endpoint/run/hash 的 `publish_pending` 与不可变 prepared snapshot，canonical postmatch snapshot/state 尚未落盘。未重复抓取、未调用 The Odds API、未消耗 quota。
- 经确认已写入并加载 `/Users/eagod/Library/LaunchAgents/xin.celab.football.postmatch-publish.plist`；`plutil` 通过，launchd 注册为 `gui/501/xin.celab.football.postmatch-publish`，每天北京时间 16:40 运行，`RunAtLoad=false`、`runs=0`，未 kickstart。首次定时唤醒会优先重试现有 pending，不重新抓取。
- 用户关闭 TUN 后确认路由/DNS 已恢复 `en1 -> 39.102.50.205`，但 ECS 仍无 SSH banner 与 HTTPS 响应；用户在阿里云控制台重启实例后，SSH 恢复且旧 release/service/Nginx 均可读。随后以标准工具和自动回滚部署 `ffd0cad8f931621df0f540b47b5dca364480c2d6`，previous release 为 `ee100768...`，current/service/Nginx/内部 warmup 与公网 `/healthz`、`/api/matches`、`/preview` smoke 全部通过，未触发回滚。
- 部署后只重试原 pending，复用同一 prepared snapshot，没有重新抓取；第二次传输返回 HTTP 200 / ingest `stored`，本地 canonical postmatch snapshot/state 已落盘，pending/prepared 已清理。ECS SQLite 最新记录为 `20260719T133440Z-postmatch`，未调用 The Odds API、未消耗 quota、未打印 secret。
- 公网最终验收：`/api/matches` 为 5 场，`POSTPONED=0`、英格兰 vs 阿根廷实时条目为 0；`/api/finished` 为 102 场，英格兰 1–2 阿根廷已结算，coverage 为 `finished_result_count=103`、`closing_available_count=102`、`missing_closing_count=1`、`skipped_no_closing=1`；`/preview` 目标行显示 `1 - 2` 与赛果结论，不再显示“赛果待确认”。研究免责声明和公开字段安全检查均通过。
- 最终巡检发现 `ops_check` 仍拿旧 analysis finished=101 与 results=103 对账，属于新 postmatch 边界接入遗漏；现改为只优先使用 state/hash 验证通过的 postmatch snapshot，否则回退 analysis snapshot。TDD ops 回归 `28/28`，配置 runtime 全量 `779/779`、系统 FastAPI `13/13`，合计 `792/792`；真实 `ops_check` 为 `errors=0`、5 个既有 warning，远端日志敏感命中 0。
- 巡检修复提交为 `dd972e6 fix: audit published postmatch snapshots`，已通过标准工具部署到 ECS `/opt/worldcup/releases/dd972e6e1626b8999ccee47ec3a837135180a288`，previous release 为 `ffd0cad8...`；current/service/Nginx/内部 warmup 与公网 smoke 全部通过，未触发回滚。最终复核仍为 matches=5、postponed=0、英格兰 vs 阿根廷实时条目=0、finished=102、目标比分 `1 - 2`、missing closing=1；LaunchAgent 保持 16:40、`runs=0`。全程未 push、未调用 The Odds API、未消耗 quota、未泄露 secret。
- 用户随后反馈世界杯当前列表为空；只读排查确认 openfootball 仍有西班牙 vs 阿根廷决赛（北京时间 7 月 20 日 03:00），根因是世界杯 analysis snapshot 停在英格兰 vs 阿根廷，赛后投影移除该完赛场后没有新决赛可展示。经确认执行一次受控 `worldcup.scheduled_publish --live --force --no-notify`，新 run `20260719T141624Z-live` 刷新出 1 场世界杯决赛并 HMAC 发布成功，ECS 返回 HTTP 200 / `stored`，snapshot id 为 `21836b5f85491915b032cde88f5ea2b929ee0c6ec77f7833d0435ed18c7a7657`。tertiary quota 由 332 降至 329；公网 `/api/matches` 为 6 场（世界杯 1 + 中超 5），preview 显示“比赛列表 6 场”、西班牙 vs 阿根廷和“历史比赛 102 场”；`ops_check` 为 errors=0 / 既有 warnings=5。本轮未改代码、未 commit/push、未部署服务。

## 2026-07-17 延期公开隐藏与世界杯赛后同步

- 定位截图中的两个问题：延期记录被内部 snapshot 直接投影到公开列表；世界杯赔率调度在开球后停止，日报链又只更新本地完赛产物而不发布新 snapshot，因此英格兰 vs 阿根廷有 closing 证据仍长时间显示“赛果待确认”。
- 公开 `project_match_rows` 现整场排除 `POSTPONED` 和已进入 `finished.matches` 的比赛，但内部 snapshot/cache/history 保留原数据。`/api/snapshot/latest`、`/api/matches`、preview、静态导出和 `/readyz.match_count` 统一使用过滤后口径，公开 counts 不再携带延期计数；已开赛但没有确认赛果的场次仍保留“赛果待确认”，不按时间猜测完赛。跨赛事同队同时的 identity 已按 competition 隔离。
- 新增独立 `worldcup.postmatch_publish`：默认零副作用 dry-run；live 只接受 openfootball `score.ft` 的 90 分钟非负整数比分，不调 The Odds API、不读写 quota ledger、不覆盖 `analysis_snapshot.json`。必须发布“完整世界杯 base + 累计 finished”，源回退/重复、比分修订、base/previous/store 冲突、closing 缺失或身份不明时阻断公开发布。
- 发布可靠性使用全共享写路径独占锁、内容 hash 命名的不可变 prepared snapshot、绑定 owner/endpoint/hash 的 pending、`stored` / `duplicate` 业务成功判定，以及“canonical output → state → 清 pending”顺序。并发、HTTP 200 但业务拒绝、state 落盘失败、pending endpoint/路径篡改、孤儿 prepared、重复 identity 和 state/output hash 不一致均有故障注入回归。
- 配置运行时排除可选 FastAPI 的完整回归 `775/775`，系统 Python FastAPI `13/13`，合计 `788/788`；`compileall`、`git diff --check` 和默认 dry-run 通过。使用当前 101 场本地基线与真实 closing history 在临时目录注入“英格兰 1–2 阿根廷”严格 `score.ft`，成功生成 102 场 finished 完整 snapshot，原始 analysis/openfootball/results/store 未改动。
- 本轮没有读取 `.env`、没有联网、没有真实刷新或 HMAC 发布赛果、没有部署、没有安装/修改 LaunchAgent、没有 commit/push。浏览器调试 skill 按路由优先使用本地 CLI/静态 HTML 验证，未启动应用内浏览器或本地服务。

## 2026-07-15 单场最后/下次更新时间

- 定位临近开赛仍显示“按24小时时间间隔刷新”的根因：最后一个赛前锚点执行后，调度器把通用 24 小时 cadence 当成下次更新；该时间已晚于开球，页面又直接展示策略说明，造成赛前还会等待 24 小时的误解。
- `/api/matches` 为每场新增 `last_update_at` / `last_update_label` 安全投影；最后更新时间优先使用该场赔率时间，其次为分析计算时间和 snapshot 时间。原有 `next_update_at` / `next_update_label` 保持兼容。
- 首选详情新增“最后更新”和“下次更新”，统一显示北京时间；有明确计划时显示具体日期时间，最后一个赛前刷新完成后显示“临场更新已完成”，延期或已开赛场次继续显示对应状态，不再回退为 24 小时文案。
- 调度器只保留开球前的 cadence / 首选鲜度候选；所有赛前候选均完成后返回终态 `pre_match_refresh_complete`，且 `next_update_at=null`、`should_refresh=false`。同步更新 README、数据契约和回归测试，不改首选方向、模型、赔率额度或 snapshot 结构。
- TDD 先以 5 个聚焦用例确认旧逻辑失败，完成后聚焦回归 `5/5`、配置运行时排除可选 FastAPI 的完整回归 `737/737`、系统 Python FastAPI `13/13`，合计 `750/750`；`compileall` 和 `git diff --check` 通过。实现和验证未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未刷新或发布 snapshot、未部署、未修改 LaunchAgent、未 commit/push。
- 应用内浏览器运行时初始化出现 `Cannot redefine property: process`，因此没有用未获确认的外部浏览器替代；已通过生成的静态 HTML 检查移动端相关结构与具体时间文案。
- 功能提交为 `ee10076 feat: show per-match update times`，已推送到 `origin/codex/csl-scheduled-publish`，并绑定 Wi-Fi 地址 `192.168.31.46` 部署到 ECS `/opt/worldcup/releases/ee100768b16eabcea1b91e5db96a07dd4eea4f09`。`worldcup.service` / Nginx active，内部 `/readyz` warmup 与公网 `/healthz`、`/api/matches`、`/preview` smoke 均通过，自动回滚未触发。
- 公网 `/api/matches` 当前 13/13 场均包含 `last_update_at`、`last_update_label` 和既有 `next_update_*` 字段；`/preview` 渲染 13 组“最后更新 / 下次更新”，旧“按24 小时时间间隔刷新”文案为 0，研究免责声明保留。部署未读取 `.env`、未调用 The Odds API、未消耗 quota、未刷新或发布 snapshot，也未修改 LaunchAgent。

## 2026-07-14 The Odds API 三槽低额度切换

- 经用户确认，在现有 Primary / Secondary 基础上新增 Tertiary 独立槽位；真实 token 仅写入 ignored `.env`，`.env.example`、README 和项目协作规则只记录正式变量名与轮换契约，不包含真实值。实现阶段先以待激活变量保存，获得第二次 live 确认后才切为正式变量。
- 轮换从“完全耗尽才切换”改为“当前槽位剩余大于 30 时继续使用，降到 30 或以下时优先切到仍未探测或剩余大于 30 的下一槽位”。低额度旧槽保留作应急；若三个槽位都只剩低额度则按顺序继续使用尚有余额的槽位并启用低额度锚点，全部为 0 才暂停。
- 世界杯 scheduled refresh、The Odds API scores、中超 odds refresh 共用统一选择器；中超 scheduled publish / ops runner、quota 告警、readiness 和脱敏 ops check 同步识别 `theoddsapi_tertiary`。脱敏模拟验证在 Primary=0、Secondary=26、Tertiary 尚无 ledger 记录时返回 `tertiary`，未读取或输出 token 值。
- TDD 先确认缺少 Tertiary provider 的红灯，再覆盖阈值切换、未知新槽 bootstrap、全低额度应急回退、中超 quota 汇总和脱敏巡检。配置运行时排除可选 FastAPI 文件的完整回归 `735/735`、系统 Python FastAPI `13/13`，合计 `748/748`；`compileall`、`git diff --check` 和 readiness 通过。实现与离线验证阶段未联网、未调用 The Odds API、未消耗 quota、未刷新或发布 snapshot、未部署、未修改 LaunchAgent、未 commit/push。
- 经用户第二次确认，正式激活 Tertiary，并以 `--no-notify` 执行一次世界杯受控 `--live --force`。刷新明确选择 `odds_api_key_slot=tertiary`，生成 `run_id=20260714T102722Z-live` 的 2 场 snapshot，HTTPS ingest 首次返回 HTTP 200 / `stored`，无 pending、无 WxPusher 通知。Tertiary 首次真实额度为 used 3 / remaining 497；Primary 为 0，Secondary 在激活前被既有 LaunchAgent 两次自然刷新从 26 消耗到 20，不是本次 Tertiary force 消耗。
- 线上验收 `/healthz` 正常，`/api/matches` 共 11 场，世界杯 2/2、中超 9/9 均为 `MATCH_PICK`，`NO_CLEAN_MARKET=0`。中超 dry-run 已读取 Tertiary remaining=497，恢复 T-90/T-25 正常锚点；真实 token 未进入 tracked files、文档、日志或输出。
- 三槽实现提交为 `6741bff feat: rotate three odds api keys`，与此前未推送的延期修复 `172c3c0` 一并推送到 `origin/codex/csl-scheduled-publish`。绑定 Wi-Fi 源地址 `192.168.31.46` 部署到 ECS `/opt/worldcup/releases/6741bff25d9a023404d946d2ab693687d8696d3e`，`worldcup.service` / Nginx active，内部 `/readyz` warmup 正常，公网 `/healthz`、`/api/matches`、`/preview` 均为 200，自动回滚未触发。部署未读取 `.env`、未调用 The Odds API、未发布 snapshot，也未修改或重载 LaunchAgent；现有两个 LaunchAgent 已通过相同 `.env` 路径自然使用三槽逻辑。

## 2026-07-13 中超延期状态闭环

- 定位第 18 轮延期场次仍显示首选的根因：中超赛前 snapshot 只跟随 The Odds API event / `commence_time`，没有消费中足联官方 `match_status`；赔率源未及时撤场时，延期场次会保留到原定开球时间，浙江 vs 青岛海牛还曾同时出现原日期与 7 月 14 日补赛两个事件。
- 新增中足联官方 `Fixture/Postponed/Played` 与 7M `Scores_Arr/Memo_Arr` 的离线赛程状态解析及全量双源校验。双方日期、主客队和状态全部一致且仍有未完赛场次时，live results refresh 原子写入 ignored `data/cache/csl_fixture_status_csl_2026.json`；状态 cache 缺失时 runner 可只读复用保存的双源 raw，任何缺行或状态冲突都不覆盖旧 cache。
- League runner 用双源状态覆盖赔率事件：`POSTPONED` 强制撤销首选并保留为明确延期记录；同一主客队已确认更晚新日期时压掉赔率源残留旧 event。公开 `/api/matches` 增加 `fixture_status`，页面显示“比赛延期 / 等待官方公布补赛时间”，延期不计入“暂无可靠首选”、不触发 T-90/T-25/鲜度保底，也不进入 closing、赛后回测或观察报告 no-pick；结果/状态刷新失败沿用 cache 并标 `stale_sources`。
- 使用 2026-07-12 保存的真实 raw 离线核对：中足联与 7M 各 240 场，101 场未完赛状态 101/101 一致，识别 4 场 `POSTPONED`；浙江 vs 青岛海牛只保留 7 月 14 日新 `SCHEDULED`。当前赔率 cache 离线构建为 7 场正常待赛 + 4 场延期，页面不再把 4 场延期识别为已开赛待赛果。
- TDD 聚焦回归 `94/94`；Python 3.12 可执行全项目回归 `729/729`，仅因该 runtime 未安装可选 FastAPI 跳过对应模块，系统 Python FastAPI 补跑 `13/13`，合计 `742/742`；`compileall` 和 `git diff --check` 通过。实现阶段未联网、未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署、未修改 LaunchAgent、未 commit/push。`RECENT_WORK.md` 按用户确认仅追加本条，不整理旧记录。

## 2026-07-10 筛选后比赛列表计数同步

- 定位赛事筛选后左栏仍显示“比赛列表 12场”的原因：前端 `applyFilters()` 只隐藏不匹配的行并切换详情，标题仍是服务端首次渲染的静态总数。
- 实时与历史工作台标题新增稳定计数契约；每次赛事、日期或搜索筛选后，都使用当前视图可见行数更新“比赛列表 / 历史比赛 N场”，不改数据、首选方向或页面布局。
- TDD 先补 HTML/JS 契约并确认红灯；预览层 `15/15`、配置运行时排除可选 FastAPI 的完整回归 `720/720`、系统 Python FastAPI `13/13`、`py_compile` 和 `git diff --check` 通过。本地浏览器验收确认搜索筛选计数 `3 → 1 → 0 → 3`，世界杯筛选显示 3 场；桌面端与 390px 移动端无 warning/error，临时本地服务已停止。
- 代码提交为 `fb0461e fix: restore published picks and filtered counts`，已发布到 ECS `/opt/worldcup/releases/fb0461e39233d4cfb17b340ceacd1bc137644c6f`；`worldcup.service` / Nginx active，内部 `/readyz` warmup 和公网 `/healthz`、`/api/matches`、`/preview` smoke 通过，自动回滚未触发。公网浏览器筛选“2026 世界杯”后标题为“比赛列表 3场”，实际 3 条世界杯、0 条中超，桌面端与 390px 移动端无 warning/error。部署未读取 `.env`、未调用 The Odds API、未发布 snapshot、未修改 LaunchAgent。

## 2026-07-10 世界杯首选发布回归修复

- 线上世界杯最新快照停在 `20260710T083215Z-live`，其首选在北京时间 20:02 过期；本机 19:50 和 21:05 已生成新快照，但 `916ddcf` 新增的 outbox 调用在 `now=None` 时引用未定义 `_now_utc_iso()`，LaunchAgent 两次均在发布前抛 `NameError`，导致公开投影将过期首选安全降为 `NO_CLEAN_MARKET`。
- 在 `worldcup.scheduled_publish` 补回 UTC 时钟函数；TDD 新增“新发布未传 `now`”和“pending 重试未传 `now`”两条回归，先复现两个 `NameError` 红灯后修复。配置运行时排除可选 FastAPI 的完整回归 `720/720`、系统 Python FastAPI `13/13`、`py_compile` 与 `git diff --check` 通过。
- 经用户确认，只重发已有 `20260710T130551Z-live` 快照，不重新刷新赔率；HTTPS 前两次握手瞬时失败，第三次返回 HTTP 200 / `stored`，pending 已清理。The Odds API secondary quota 保持 164 未变。
- 线上 `/api/matches` 现有世界杯 3/3 个 `MATCH_PICK`。公网浏览器筛选“2026 世界杯”后显示西班牙 vs 比利时、挪威 vs 英格兰、阿根廷 vs 瑞士 3 场本场首选，无“暂无可靠首选”，控制台无 warning/error。筛选后“比赛列表 12场”标题未随内容变为 3 场是既有小问题，本轮未顺手修改。

## 2026-07-10 已开赛待赛果场次保留

- 定位山东泰山 vs 云南玉昆开赛后从页面消失的原因：线上 `/api/matches` 仍有该场，但 decision-only 预览层在 `kickoff <= now` 时不看赛果状态直接过滤；完赛记录尚未生成时，该场会落入“待开赛已隐藏、历史尚未收录”的空档。
- 预览层现改为“待开赛 → 已开赛·赛果待确认 → 已完赛”；列表紧凑标记“待赛果”，详情明确显示“赛前首选（已封盘）”、“等待赛果确认”和“不是滚球建议”；赛果确认后仍按原逻辑转入历史。
- 展示的封盘首选优先从当前 snapshot 取值；如果开赛后刷新已将当前决策降为 `NO_CLEAN_MARKET`，则从同赛事前一份 snapshot 恢复开赛前最后一刻仍有效的 `match_decision` 公开字段。开赛前已过期、记录不完整或 legacy 内容不会被恢复。本轮不改模型、首选方向、公开 API、snapshot 契约、采集、调度或数据库。
- 首次代码部署后的线上验收发现两个真实链路问题：新进程会在同一 5 分钟时间桶内复用旧代码生成的磁盘 HTML 缓存，已等待自然换桶重渲染，未删除线上缓存；同时 21:26 的开赛后 snapshot 已不再携带赛前首选，因此补上前一 snapshot 的封盘回退。
- TDD 新增“当前 snapshot 直接保留封盘首选”和“开赛后 snapshot 从前一赛前 snapshot 恢复封盘首选”两个回归用例，均先确认旧逻辑红灯；实现后配置运行时完整回归 `718/718`、系统 Python FastAPI `13/13` 通过；`py_compile` 和 `git diff --check` 通过。
- 使用当前真实中超 snapshot 完成桌面端与 390px 移动端浏览器验收：山东泰山场次可见，点击详情切换正常，控制台无 warning/error。临时 `127.0.0.1` 预览服务已停止；本地验证阶段未读取 `.env`、未调用 The Odds API、未消耗 quota、未发布 snapshot、未部署或改 LaunchAgent。
- 实现分为 `c467d383ffe532d1e02b59deb60015deeeb3afa1` 和真实链路补齐 `43bafb7c4db1069a16c54c22112936854829112f` 两个提交；生产 current 已切到 `43bafb7`，`worldcup.service` / Nginx active，`/readyz` warmup 和 `/healthz`、`/api/matches`、`/preview` smoke 全部通过，自动回滚未触发。自然换过 5 分钟 HTML 缓存桶后，公网浏览器验收确认山东泰山 vs 云南玉昆显示“待赛果”和“赛前首选（已封盘）”，封盘方向为大小球 3.5 大球、安全概率 45.0%、参考赔率 1.77，点击交互正常且控制台无 warning/error。部署没有读取 `.env`、没有调用 The Odds API、没有消耗 quota、没有发布新 snapshot、没有修改 LaunchAgent。
- `RECENT_WORK.md` 已明显超过默认 20 条；经用户确认，本轮只追加记录，不顺手归档、压缩或删除旧记录。

## 2026-07-10 P1：中超俱乐部评级数据与启用门槛

- 发现本地 `club_results_csl_2026.csv` 仍是 840 场、截止 2026-05-31；当前 7M 赛程数组与中足联官方公开接口均有 136 场 2026 已完赛比赛，双源日期/主客队/比分 136/136 一致。受控 `csl_results_refresh --live --write` 已原子更新 ignored replay cache 为 856 场（2023–2025 各 240，2026 为 136），最新到 2026-07-05；不读 `.env`、不调 The Odds API、不消耗 quota。
- 新增纯离线中足联/7M 解析器、双源 compare 和防回退写入链；任一源缺行、alias 未知、日期/主客/比分冲突，或新数据删除/改写旧赛果时阻断写入。中超 scheduled publish 在 live due 时先刷新这两个免费源；失败时沿用旧赛果 cache 并写质量警告，不阻断赔率链。
- 新增独立 `csl_model` 配置边界和逐队样本门槛：全局至少 300 场、单队至少 30 场。更新后重庆铜梁龙/辽宁铁人各 17 场，所以涉及它们的 2 场对阵使用 1500 结构占位并标 `club_rating_team_sample_too_small`；其余成熟样本球队更新真实评级。但整个评级仍 `shadow_only` / `club_rating_pending`，8 场现有市场基准小于 200 场门槛，不让俱乐部 Elo 直接改写首选方向。
- Pending gate 新增分赛季 model/uniform/home-prior 和同样本 model-vs-market 门槛。当前 8 场可 join 小样本中，model 1X2 Brier=0.4678、market=0.5130，但 `sample_too_small=true`，只记为观察、不宣称已优于市场。运维检查也改为区分“pending 下安全市场兜底首选”与“未经兜底使用 rating”，不再把 MatchPick v3 的合法兜底首选误报为 error。
- 对抗性收尾发现 scheduled publish 原来只覆盖最新诊断 snapshot、没有自动积累 closing 历史；已补为每次成功构建后自动归档到 ignored `csl_history/`。归档失败写 `snapshot_archive_failed` 质量警告但继续发布当前有效首选，避免观测链故障反过来造成线上无首选。
- 不改 UI 布局；只修正与 MatchPick v3 矛盾的规则文案，明确“有有效新鲜主盘就给一个首选，概率偏低保留观察风险”。P1 聚焦回归 `94/94`、配置 runtime 排除可选 FastAPI 的最终完整回归 `716/716`、系统 Python FastAPI `13/13` 均通过；`py_compile` 和 `git diff --check` 通过。
- P1 主实现提交 `8768f67034248097816ad843269730ff1569271c`，自动归档闭环提交 `6b00e5e49b6dca9e0b118eecd4b458869016fd5e`；生产 current 已切到后者，service/nginx/ready 与 `/healthz`、`/api/matches`、`/preview` smoke 全部通过，部署没有调用 The Odds API、没有修改 LaunchAgent。
- 使用仍新鲜的 odds cache 重建并发布 `run_id=20260710T111715Z-csl-live`，线上 ingest HTTP 200 / `stored`；双源当前赛季 136 场再次一致，8 场方向相对 P0 为 0 变化，8/8 均为安全 market fallback、`rating_unsafe_picks=0`。本地历史归档创建成功，The Odds API quota 保持 173 未变；线上合并 `/api/matches` 为 11/11 个 MatchPick v3，原 workbench 布局与研究免责声明保持不变，无 S/A/B/C 等级 UI。

## 2026-07-10 P0：首选鲜度与发布可靠性

- 世界杯与中超调度新增“首选鲜度保底”：正常免费额度下，把每场 `match_decision.valid_until - 20 分钟` 纳入下一次刷新候选，避免当前首选先过期、页面变成暂无可靠首选，再等旧赛前锚点。quota remaining 低于等于 30 时继续按低额度锚点降级，避免因保鲜耗尽数据源。
- HTTPS ingest 对瞬时 TLS/网络/5xx 做有限重试；仍失败时，在 ignored cache 同目录保留不含 secret 的 `*.publish_pending.json`。下次调度唤醒只重试发布同一 snapshot/run_id，成功后清理 pending，不重复刷新或消耗 The Odds API quota。
- 中超 LaunchAgent 生成器默认唤醒周期由 1800 秒缩短为 900 秒；runner 内部的 1800 秒刷新节流保持不变，所以不会把 API 调用频率翻倍。本轮不修改 UI 布局、MatchPick v3 选择方向或模型参数。
- TDD 新增过期前刷新、低额度保护、瞬时传输重试、pending 发布不二次刷新，以及 LaunchAgent 900 秒的回归用例。配置 runtime 排除可选 FastAPI 依赖的完整回归 `707/707` 通过，系统 Python 的 FastAPI 适配测试 `13/13` 通过，`py_compile` 与 `git diff --check` 通过。真实本地 dry-run 显示世界杯下一次为 `pick_expiry_guard`，中超当前为 `match_anchor_due`。
- 实现提交为 `916ddcf fix: keep match picks fresh and retry publishing`，已部署到 ECS `/opt/worldcup/releases/916ddcf2c83d1a55877c324f2ea4002750b99821`；`worldcup.service` / Nginx active，`/healthz`、`/api/matches`、`/preview` 都为 200，线上 11/11 场为 `match_pick_v3` / `MATCH_PICK`。本机中超 LaunchAgent 已重载为 900 秒唤醒、runner 仍为 1800 秒节流；由于当时已命中 T-90，首次唤醒正常刷新 8 场中超并 HTTP 200 `stored`，secondary quota 从 176 降到 173，未留下 pending 文件。

## 2026-07-10 MatchPick v3：有效主盘每场必选

- 经用户确认，将“为提高表面胜率删除低置信场次”改为“只要存在开赛前有效、新鲜、可结算的 1X2 / OU / AH 主盘，必须输出每场唯一首选”。只有赔率全部无效或过期、比赛已开始、没有可结算主盘时才允许 `NO_CLEAN_MARKET`。
- 策略升为 `match_pick_v3`：世界杯安全概率以市场 0.80 / 模型 0.20 融合；安全概率相近（默认 2 个百分点内）时，继续比较书商覆盖、盘口离散度、市场质量、模型/市场一致性、不亏概率和预期损失。书商偏少、离散度偏高、模型分歧、四分之一盘、极深让球和概率低改为风险扣分；四分之一盘/极深盘只在没有普通可比主盘时备用，不再删除整场。
- 中超 `club_rating_pending` / missing / invalid 不再阻断首选；方向改用去水后的市场共识兜底，占位 1500 不参与方向选择，评级问题保留为内部风险标记。公开投影已支持 v3，但不公开内部证据分或恢复 S/A/B/C。
- 使用当前被忽略的本地赔率缓存离线重算：世界杯 3/3 场产生首选，中超 8/8 场产生市场兜底首选，`NO_CLEAN_MARKET=0`。这些是现有 cache 的离线重算，不代表已发布新 snapshot。
- 历史聚合收盘价回放：2022 样本 v3 为 38/63（60.32%），2026 样本 v3 为 58/96（60.42%），合计 96/159（60.38%），覆盖率 159/159。真正纯市场基准为 100/159（62.89%）；两者只有 25 场方向不同，配对中 v3 多赢 5 场、市场多赢 9 场，差异不足以证明稳定优劣。回放只有聚合收盘价，并用 3 个相同合成书商补齐结构，无法验证真实书商离散度；因此只作观察，不宣称已提高胜率，不据此调整 Elo/Poisson 参数。
- TDD 先新增低概率、单书商/不完整市场、高离散度、中超评级 pending、只剩四分之一/极深盘时仍需给首选，以及过期 v3 首选保留 v3 策略版本的失败用例，实现后聚焦回归 `63/63` 通过；配置 runtime 排除可选 FastAPI 文件的完整回归 `700/700` 通过，系统 Python 的 FastAPI 适配测试 `13/13` 通过。
- 实现提交为 `d95aa85 feat: guarantee one match pick per valid market`；提交只包含决策引擎、公开投影、配置、测试和契约文档，没有修改 UI 文件、没有恢复 S/A/B/C，也没有改 LaunchAgent 或推送远端 Git。
- 经用户单独确认，绑定 Wi-Fi 地址 `192.168.31.46` 部署到 ECS `/opt/worldcup/releases/d95aa858a6a00ee20d94706ea6df7a5f78912121`。首次部署因公网 `/healthz` 瞬时 `URLError` 触发自动回滚到 `662715a`，复查三个入口为 200 后重试成功；当前 `worldcup.service` / Nginx 均为 active，内部 `/readyz` 为 ready。
- 强制刷新并发布世界杯与中超 v3 snapshot：世界杯 `run_id=20260710T083215Z-live`，3/3 场 `MATCH_PICK`；中超 `run_id=20260710T083454Z-csl-live`，8/8 场 `MATCH_PICK`且 8 场都明确使用评级 pending 的市场共识兜底。两个 ingest 均返回 HTTP 200 / `stored`；期间 TLS 瞬时中断时只重试已生成 snapshot 的发布，没有重复消耗赔率额度。The Odds API secondary 额度从 182 降到 176，两个赛事各消耗 3。
- 线上验收：`/healthz` 正常；`/api/matches` 共 11 场（世界杯 3 + 中超 8），`match_pick_v3=11`、`MATCH_PICK=11`、`NO_CLEAN_MARKET=0`，禁止的 `signals` / grade / 内部证据 / 资金字段扫描为空；`/preview` 仍是原“足球研究台账” UI，包含“本场首选”和研究免责声明，没有独立 `.match-card` UI 或资金词。
