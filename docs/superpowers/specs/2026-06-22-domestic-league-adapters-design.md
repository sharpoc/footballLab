# 俱乐部联赛多联赛接入设计

- 日期：2026-06-22
- 状态：已确认，进入 implementation plan
- 范围：多联赛架构底座；中超作为首个落地联赛；英超作为第二个 adapter 验证；西甲、德甲、意甲、法甲作为后续平滑扩展约束
- 定位：研究/分析工具，不构成投注建议，不显示下注金额，不做追损、重注、串关喊单或任何无风控建议

## 背景

当前项目以 2026 世界杯为首个 competition adapter，已经具备赛程/赔率采集、Elo + Poisson + 赔率去水、价值信号、刷新调度、本地 snapshot、SQLite/HTTP 展示、HMAC ingest 和研究台账页面。

下一阶段要开始俱乐部联赛开发。用户已确认首个联赛为中超，同时要求后续开发英超时必须考虑五大联赛可顺利接入。因此本设计不能把中超做成一次性特例，而应把中超作为通用俱乐部联赛 adapter 的第一个实例。

## 目标

1. 在不破坏世界杯现有链路的前提下，新增多 competition / 多联赛底座。
2. 中超先跑通未来 7-14 天本地研究台账 MVP。
3. 英超作为第二个 adapter 验证跨年赛季、主客场、俱乐部队名别名和数据源配置边界。
4. 英超验证后，西甲、德甲、意甲、法甲应主要通过新增配置和 alias 接入，而不是重写 pipeline。
5. 继续复用现有纯函数引擎、赔率去水、EV/Edge、调度、snapshot、页面和测试入口。
6. 显式区分世界杯杯赛语义与俱乐部联赛语义，避免把 `stage` / `group` 等世界杯字段继续扩散为新能力的核心模型。

## 非目标

- 不一次性上线中超 + 五大联赛全量。
- 不在本设计阶段修改线上发布、ECS、LaunchAgent、HMAC secret、SQLite/PostgreSQL 或云资源。
- 不安装新依赖。
- 不自动消耗 The Odds API quota；任何 live 探测都必须单独确认。
- 不把国家队 Elo 套用到俱乐部联赛。
- 不输出下注金额、仓位、凯利、执行建议或喊单文案。
- 不把中超初期无充分强度模型时的输出包装成高置信 S/A 结论。

## 推荐方案

采用“多联赛底座 + 中超首个 adapter”的方案。

新增通用 competition registry、联赛配置、俱乐部 alias 和 source key 探测能力。中超作为第一个 `domestic_league` adapter 接入；世界杯补齐 `competition=fifa_world_cup_2026` 元数据以验证兼容。后续英超、西甲、德甲、意甲、法甲复用同一 registry 和 adapter 接口。

不采用“只硬接中超”，因为会把 sport key、赛季、队名别名、页面筛选和数据质量状态散落到代码里，后续接英超和五大联赛会返工。

不采用“一次性全量接五大联赛”，因为数据源覆盖、赔率 quota、赛果口径、俱乐部强度模型和页面分层会同时扩张，容易影响已经稳定的世界杯链路。

## 架构边界

### Competition registry

新增通用 registry，负责声明每个 competition 的稳定元数据：

```text
id
name
kind
country
season
season_start
season_end
timezone
source_keys
markets
fixture_policy
rating_policy
refresh_policy
```

首批目标：

```text
fifa_world_cup_2026
csl_2026
epl_2026_27
laliga_2026_27
bundesliga_2026_27
serie_a_2026_27
ligue_1_2026_27
```

其中只有 `fifa_world_cup_2026` 和 `csl_2026` 进入第一轮实际 snapshot；英超和五大联赛配置可以先作为 dry-run / probe 约束存在。

### Snapshot competition block

每场 match 增加通用赛事元数据：

```text
competition:
  id
  name
  kind
  country
  season
  source
  fixture_source
  rating_policy
```

世界杯补齐：

```text
id = fifa_world_cup_2026
name = 2026 世界杯
kind = tournament
country = international
season = 2026
source = openfootball + theoddsapi
fixture_source = openfootball
rating_policy = national_team_elo
```

中超第一版：

```text
id = csl_2026
name = 中超 2026
kind = domestic_league
country = CN
season = 2026
source = theoddsapi / discovered fixture source
fixture_source = explicit_fixture_source 或 odds_event_only
rating_policy = club_rating_pending 或 club_rating
```

已有 `stage`、`group`、`home_team`、`away_team`、`signals` 保持兼容，不删除、不改名。

### Adapter 输出契约

每个 adapter 只负责把各自数据源转成现有 pipeline 能消费的结构：

```text
Fixture
ParsedOddsEvent
MatchResult
ParsedLineupContext（未来可选）
```

引擎层继续保持纯函数，不联网、不连数据库、不依赖云。source 层仍负责请求和缓存；collector 层只解析保存样例；runner 层组合 adapter 输出并生成 snapshot。

## 数据源策略

### Phase 0 只读探测

先新增 The Odds API sports key 探测：

- 刷新 `/sports/?all=true` 的本地样例。
- 查找中超、英超、西甲、德甲、意甲、法甲是否存在 active sport key。
- 只保存 sport key、title、active、has_outrights、group/category 摘要。
- 不在未确认前调用多个 odds endpoint 消耗 quota。

中超 odds live 探测需要单独确认。确认后只做小窗口或一次性最小请求，保存原始样例到 `data/probe/` 或 `data/cache/`，不进 git，不打印 API key。

探测摘要至少区分：

```text
sport_key_found
sport_key_missing
sport_key_inactive
odds_available
no_odds_for_competition
markets_missing
quota_unknown
source_permission_denied
```

### 中超赛程策略

优先使用明确赛程源。如果第一版只有 The Odds API odds events 可用，则允许先从 odds event 反推未来比赛，但必须在 `competition.fixture_source` 和 `data_quality` 中标记：

```text
fixture_source = odds_event_only
```

该模式只能支撑“有赔率的未来比赛”台账，不能宣称覆盖完整中超赛程。

### 英超与五大联赛策略

英超作为第二个 adapter 用于验证：

- 跨年赛季 `2026_27`。
- 主客场固定存在，和世界杯中立/准主场不同。
- 俱乐部 alias 与国家队 alias 分离。
- 数据源 sport key 和赛程源配置可替换。
- 后续五大联赛只新增 registry 配置、alias 和保存样例解析测试。

## 俱乐部强度模型

俱乐部联赛不能复用国家队 Elo。

### 第一版保守策略

中超初始阶段若没有可靠 `club_rating`，使用保守模式：

- 不输出过度自信的强信号。
- `rating_policy=club_rating_pending` 进入 `data_quality`。
- 信号最多作为观察/候选，或受质量规则压制到 B/C。
- 页面明确展示强度模型尚未完成或样本不足。

这不是临时掩盖模型问题，而是避免用错误 rating pool 制造伪精确结论。

### 第二阶段 club rating

新增 `club_rating` 能力：

- 按联赛独立 rating pool。
- 使用历史联赛赛果重放 Elo。
- 主客场优势作为俱乐部联赛默认参数，不沿用世界杯中立场逻辑。
- 中超、英超、五大联赛共享接口，但各自可有独立 K 值、初始 rating 和赛季回归规则。

只有当历史赛果和回测证据足够时，才允许解除 `club_rating_pending` 的强信号压制。

## 刷新与 quota

中超和后续联赛必须按 competition 独立限频，不抢占世界杯临赛额度。

第一版建议：

- 中超未来 7-14 天窗口。
- 每个 competition 独立 `refresh_policy`。
- 低 quota 时优先保留已经上线或临近比赛的 competition。
- 多联赛批量刷新前必须先 dry-run 输出预计 credit 消耗。

任何 live odds refresh、scheduled publish、LaunchAgent 更新、ECS ingest 或公网发布都不在本设计默认执行范围内，需单独确认。

## 页面与 API 展示

研究台账最小增强：

- 增加 competition 标签。
- 增加 competition 筛选：全部、2026 世界杯、中超 2026，后续加入英超/五大联赛。
- 联赛行显示赛季和轮次；没有轮次时显示赛事标签和开赛时间。
- 数据质量区域展示 `fixture_source=odds_event_only`、`club_rating_pending`、`no_odds_for_competition` 等摘要。
- 保留免责声明。
- 不显示资金相关字段。

API 投影应保持旧消费者兼容。新增 competition 字段时，老 snapshot 缺少该字段仍应可渲染。

## 错误处理

必须显式区分：

```text
competition_not_configured
sport_key_missing
sport_key_inactive
no_fixtures_in_window
no_odds_for_competition
markets_missing
fixture_source_odds_event_only
club_alias_unmatched
club_rating_pending
club_rating_missing
quota_low
quota_exhausted
stale_competition_cache
```

这些状态进入 `data_quality`，页面显示摘要，不能把缺数据伪装成“无价值”或正常无信号。

## 实施分期

### Phase 0：只读探测

- 新增 sports key 探测命令。
- 保存中超、英超和五大联赛 sport key 摘要。
- 需要 live odds 探测时单独确认。

成功标准：

- 能输出中超是否可用、是否有 odds、是否有三类目标盘口。
- 能输出英超和五大联赛的 sport key 覆盖摘要。
- 不自动消耗 quota。

### Phase 1：多联赛底座

- 新增 `CompetitionConfig` / registry。
- 世界杯 match 补齐 competition block。
- runner 支持按 competition 构建本地 snapshot。
- 页面/API 支持 competition 字段兼容展示。

成功标准：

- 现有世界杯测试全过。
- 老 snapshot 仍可渲染。
- 新 snapshot 每场都有 competition block。

### Phase 2：中超 MVP

- 新增中超 adapter。
- 新增俱乐部 alias 表。
- 中超未来 7-14 天本地 snapshot。
- 无 club rating 时触发保守质量状态。

成功标准：

- 本地 snapshot 可以包含中超比赛。
- 中超数据质量状态透明。
- 不污染世界杯线上链路。

### Phase 3：英超验证与五大联赛扩展

- 新增英超 adapter dry-run。
- 验证跨年赛季、主客场和 alias 边界。
- 英超跑通后，西甲、德甲、意甲、法甲以配置和样例解析测试为主。

成功标准：

- 英超 adapter 不需要修改 pipeline 主流程。
- 五大联赛扩展不需要新增一套 runner。

## 建议文件结构

```text
worldcup/competitions.py
worldcup/league_runner.py
worldcup/sources/theoddsapi_sports.py
worldcup/collectors/club_aliases.py
worldcup/collectors/league_odds.py
worldcup/club_rating.py（后续阶段）
```

也可以选择先扩展 `worldcup/local_runner.py`，但如果联赛逻辑开始膨胀，应拆出 `league_runner.py`，避免继续把所有输入策略塞进世界杯本地 runner。

## 测试计划

实现前先写测试：

1. registry 测试：世界杯、中超、英超、五大联赛配置可枚举。
2. snapshot 兼容测试：新增 competition block 后，旧字段仍存在，老 snapshot 仍可渲染。
3. sports key 探测测试：从保存样例中解析中超和五大联赛 key 摘要。
4. 中超 adapter 测试：保存样例可解析为 `Fixture` 和 `ParsedOddsEvent`。
5. 俱乐部 alias 测试：中超俱乐部队名不污染国家队 alias。
6. 数据质量测试：无 sport key、无赔率、event-only 赛程、无 club rating 都进入 `data_quality`。
7. 页面测试：competition 筛选、赛事标签、免责声明仍在，不显示金额。
8. 调度测试：competition 独立限频，低 quota 不盲目刷新全部联赛。
9. 回归测试：现有世界杯测试继续全过。

验证命令沿用项目当前入口：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

## 对抗性自审

- 根因：本设计解决的是“联赛不能写死为中超特例”的架构问题，不直接解决俱乐部强度模型不足。强度模型通过 `club_rating_pending` 显式降级，后续再用历史赛果重放 Elo 根治。
- 范围：第一轮只做中超 MVP 和多联赛底座，不把五大联赛一次性上线，避免范围膨胀。
- 业务语义：世界杯中立场/杯赛阶段/淘汰赛语义不同于联赛主客场和跨年赛季，必须通过 competition metadata 分离。
- 接口契约：新增 competition block 必须向后兼容；旧字段不删除。
- 额度风险：多联赛会放大 The Odds API 消耗，所有 live 探测和刷新必须先 dry-run 并单独确认。
- 密钥风险：探测和文档不得打印 API key、HMAC secret、token、Cookie 或 `.env` 内容。
- 线上风险：本设计不改 ECS、不改 LaunchAgent、不发布线上 snapshot；上线另行确认。
- 数据风险：若中超无可靠赛程源或赔率覆盖，只能标记 blocked / degraded，不能生成看似完整的联赛结论。
- 结论边界：中超初期缺少 club rating 时只能给观察或降级信号，不构成投注建议，不输出下注金额或执行建议。

## 审阅确认点

用户审阅时重点确认：

1. 中超第一版是否采用未来 7-14 天窗口。
2. 是否接受中超初期无 `club_rating` 时强信号降级。
3. 是否接受 The Odds API live odds 探测需要单独确认并可能消耗 quota。
4. 英超是否作为第二个 adapter 验证抽象，再批量接入五大联赛。
5. 第一版是否仅做本地 snapshot / 本地预览，不改线上发布链路。
