# 国际 A 级友谊赛本地接入设计

- 日期：2026-06-10
- 状态：等待用户审阅后进入实现计划
- 范围：最近 7 天滚动窗口内的国家队 A 级友谊赛，本地 snapshot 与预览页接入
- 已选方案：B，探测成功后接入本地 snapshot 和预览页，不接入线上 scheduled publish

## 目标

在不影响当前 2026 世界杯公网台账的前提下，先把最近 7 天滚动窗口内的国家队 A 级友谊赛接入本地分析链路。

成功标准：

1. 能用 dry-run 探测确认未来 7 天是否存在可用的国家队 A 级友谊赛赛程与赔率。
2. 探测成功后，本地 snapshot 可以同时包含世界杯比赛和友谊赛比赛。
3. 本地预览页能显示赛事标签，并可区分 `国际A级友谊赛` 与 `2026 世界杯`。
4. 友谊赛链路每天最多刷新一次，默认本地生成，不自动发布线上。
5. 不显示下注金额、资金建议、喊单文案或任何投注执行引导。

## 范围定义

### 时间窗口

使用滚动未来 7 天窗口：

- 窗口开始：每次运行时的 `observed_at`。
- 窗口结束：`observed_at + 7 days`。
- 时间比较统一用 UTC 存储；页面继续显示北京时间。
- 初始上线时，北京时间 2026-06-10 的运行会覆盖 2026-06-10 到 2026-06-17 的未来比赛。

已经结束的比赛不进入本阶段分析 snapshot；如果后续需要回看过去 7 天，需要另做结果/比分链路，不和本阶段混在一起。

### A 级友谊赛

本阶段把 `A 级友谊赛` 定义为成年男子国家队正式国际友谊赛：

- 包含：成年男子国家队之间的国际友谊赛。
- 排除：U21/U23/青年队、女足、俱乐部、预备队、训练赛、封闭热身赛。
- 排除：世预赛、洲际杯、欧国联、世界杯、俱乐部世界杯等正式赛事。

如果数据源只给出模糊的 `Friendlies` 或 `World Friendlies`，实现必须通过队名与国家队 Elo alias 匹配来二次过滤；无法确认 A 级身份的比赛进入 `data_quality`，不静默当作可分析赛事。

## 方案选择

### 推荐方案：本地探测 + 本地接入

先新增一个友谊赛探测与本地接入层：

- 探测层只写 `data/probe/` 或 `data/cache/` 中被忽略的样例和摘要。
- 探测通过后，解析为现有 `Fixture`、`ParsedOddsEvent`、`EloRating` 可消费的数据。
- 本地 runner 生成合并 snapshot。
- 预览页显示赛事标签、赛事筛选和友谊赛不确定性提示。
- 不改线上发布调度；不向公网 ingest 发送友谊赛 snapshot。

推荐原因：

- 当前 The Odds API 保存样例与公开 sports 列表中没有明确的国际友谊赛 sport key，不能直接按世界杯方式替换 `sport_key`。
- API-Football 免费档有 season 权限限制，必须先用真实样例验证友谊赛是否可访问。
- 友谊赛战意、轮换、阵容信息波动更大，先本地验证能避免污染已上线的世界杯台账。

### 备选方案 A：只探测不接入

只做数据源探测和样例保存，不进入 snapshot。

优点是最稳，完全不影响模型与页面；缺点是用户看不到本地台账效果。

### 备选方案 C：直接接入线上日更

直接把友谊赛放入线上 scheduled publish。

本阶段不采用。原因是数据源覆盖、赔率额度、赛事身份过滤和展示语义都还没有验证，直接上线会降低当前世界杯页面可信度。

## 数据源策略

### API-Football 探测

API-Football 作为友谊赛探测主候选源：

- 先用只读探测发现友谊赛相关 league 或 competition 标识。
- 再按 2026 season 与 7 天日期窗口拉 fixtures。
- 对候选 fixture 拉取 odds 或按 league/season 拉取 odds，再按 fixture 对齐。
- 保存原始样例到 `data/probe/` 或 `data/cache/`，不进 git。
- 样例解析测试必须基于保存样例，不按假接口写。

如果 API-Football 返回 Free plan 无权限、无赔率或无法确认 A 级身份，探测结果必须标记为 blocked，不生成看似完整的友谊赛分析。

### The Odds API 探测

The Odds API 作为辅助候选：

- 先刷新 `/sports/?all=true` 的本地样例，确认是否出现新的友谊赛 sport key。
- 如果没有明确 sport key，不调用 `/odds` 详情接口消耗额度。
- 如果后续出现明确友谊赛 key，再按 `h2h,spreads,totals` 做小窗口赔率探测。

当前设计不假设 The Odds API 已支持国际友谊赛。

### Elo 源

沿用现有国家队 Elo：

- 只有能匹配 `eloratings` 国家队 alias 的比赛才进入分析。
- 缺失 Elo 的队伍写入 `data_quality.missing_elo`。
- 无法匹配为国家队的候选比赛写入 `data_quality.unclassified_friendlies`，不进入正式分析输入。

## 数据模型

本阶段在 snapshot match 行上新增赛事元数据：

```text
competition
  id
  name
  kind
  tier
  source
  uncertainty
```

友谊赛固定映射：

```text
id = international_friendlies
name = 国际A级友谊赛
kind = friendly
tier = A
source = api_football 或 theoddsapi
uncertainty = friendly
```

世界杯比赛补齐：

```text
id = fifa_world_cup_2026
name = 2026 世界杯
kind = tournament
tier = major
source = openfootball + theoddsapi
uncertainty = standard
```

为了保持兼容，已有顶层字段如 `stage`、`group`、`home_team`、`away_team`、`signals` 不删除、不改名。

## 分析规则

友谊赛继续使用现有 MVP 模型：

- 国家队 Elo。
- Poisson 进球矩阵。
- Elo + Poisson 集成。
- 赔率去水。
- EV / Edge / 等级。

友谊赛额外增加保守标记：

- 每个友谊赛信号的 `reasons` 增加 `friendly_uncertainty`。
- S/A 信号默认降一级，除非后续有单独回测或用户明确要求关闭降级。
- 页面解释文案提示友谊赛存在轮换、战意和名单不确定性。

这不是临时掩盖模型问题，而是对友谊赛业务语义的显式风险控制。

## 日更策略

本阶段实现本地日更，不接线上发布：

- 每天最多刷新一次友谊赛 7 天窗口。
- 刷新结果写入本地 `data/cache/`。
- 本地 snapshot 和预览页可以每天更新。
- 不调用 `worldcup.scheduled_publish`。
- 不向 `football.celab.xin` 的 ingest endpoint 发送友谊赛数据。
- 如果当天刷新失败但本地有上一轮友谊赛缓存，可以生成降级快照，并在 `data_quality.source_errors` 与 `data_quality.stale_sources` 标记。
- 如果没有可用缓存，友谊赛部分失败，不应阻断世界杯本地 snapshot 生成。

调度策略使用独立 policy reason：

```text
friendlies_daily
```

它不能覆盖现有世界杯 `free-tier-v1` 策略；实现时可以在 run metadata 中记录友谊赛子策略。

## 页面展示

本地预览页做最小增强：

- 表格增加赛事标签或赛事列。
- 控件增加赛事筛选：`全部`、`2026 世界杯`、`国际A级友谊赛`。
- 友谊赛行显示 `友谊赛 / A级`。
- 友谊赛解释中加入不确定性提示。
- 继续保留研究免责声明。
- 不新增投注 CTA、不显示资金字段。

如果没有友谊赛数据，页面不报错；只显示世界杯数据，并在源健康中提示友谊赛探测无可用赛事或未启用。

## 错误处理

必须显式区分以下状态：

- `no_friendly_key`：The Odds API 未提供友谊赛 sport key。
- `source_permission_denied`：API-Football 权限不足。
- `no_fixtures_in_window`：7 天窗口内没有候选友谊赛。
- `no_odds_for_fixture`：有赛程但无赔率。
- `unclassified_friendly`：无法确认是否成年男子国家队 A 级友谊赛。
- `missing_elo`：国家队 Elo 无法匹配。
- `stale_friendlies`：本轮刷新失败，使用旧缓存。

这些状态进入 `data_quality`，页面显示摘要，不把缺数据伪装成正常无信号。

## 测试计划

实现前先写测试：

1. API-Football 友谊赛样例解析测试。
2. 7 天滚动窗口过滤测试。
3. A 级身份过滤测试：排除 U21、女足、俱乐部和正式赛事。
4. 缺失 odds / Elo / 权限失败的数据质量测试。
5. 合并 snapshot 测试：世界杯和友谊赛同时存在，原有字段兼容。
6. 友谊赛信号 `friendly_uncertainty` 与 S/A 降级测试。
7. 预览页赛事标签和筛选文案测试。
8. 本地日更策略测试：同一天不重复刷新，下一天允许刷新。

验证命令沿用项目当前无 pytest 命令：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

## 非目标

- 不上线友谊赛数据。
- 不改公网 `scheduled_publish`。
- 不部署云资源。
- 不引入付费 API plan。
- 不做俱乐部赛事。
- 不接青年队、女足或非 A 级比赛。
- 不展示下注金额或任何投注执行建议。
- 不做历史回测。
- 不做比分赛果页。

## 实施边界

下一阶段实现计划应保持以下边界：

- 先做离线样例解析和 dry-run 探测。
- 所有真实联网动作必须显式 `--live`。
- 真实 API key 只从 `.env` 读取，不写入文档、日志或 git。
- 新增缓存与探测样例写入已忽略目录。
- 线上发布、部署、push、改 launchd 或改云资源都需要单独确认。

