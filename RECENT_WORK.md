# 近期工作

本文件只记录近期可操作进展，避免变成永久流水账。默认保留最近 20 条。

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
