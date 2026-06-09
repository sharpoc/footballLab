# 数据源探测契约（Plan 0 初稿）

- 日期：2026-06-08
- 范围：2026 世界杯 MVP 数据源探测
- 结论状态：核心源已探测；赛程与 Elo 可用，API-Football 免费档不能访问 2026 season，The Odds API 可作为当前 MVP 赔率源；Plan 2 本地采集/分析/预览链路已按该契约落地第一版

## 1. 赛程源：openfootball/worldcup.json

### 结论

- 可用。
- 无需 API key。
- 已保存样例：`data/probe/openfootball_2026.json`（不进 git）。
- 2026 数据返回 104 场，符合 MVP 预期。

### 真实 URL

```text
https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json
```

### 顶层结构

```text
{
  "name": "World Cup 2026",
  "matches": [...]
}
```

### match 字段

```text
date
time
team1
team2
group
round
ground
num
```

### 内部字段映射

| openfootball 字段 | 内部字段 | 说明 |
|---|---|---|
| `num` | `source_match_no` | 原始比赛编号，可能缺失时用顺序补 |
| `date` | `kickoff_date` | 日期字符串，例如 `2026-06-11` |
| `time` | `kickoff_time_raw` | 含 UTC offset，例如 `13:00 UTC-6` |
| `team1` | `home_team_name` | 小组赛真实队名；淘汰赛可能是占位符 |
| `team2` | `away_team_name` | 小组赛真实队名；淘汰赛可能是占位符 |
| `group` | `group` | 淘汰赛可能为空 |
| `round` | `stage` | 例如 `Matchday 1` / `Final` |
| `ground` | `venue_name` | 城市或场地描述 |

### 时间处理

- openfootball 的 `time` 已带 offset，例如 `UTC-6`。
- 内部应解析为 timezone-aware `kickoff_at_utc`。
- 前端默认显示 Asia/Shanghai，并可附 venue local time。

### 已知坑

- 淘汰赛队名会出现 `W101` 等占位符，不能当真实国家队名。
- `ground` 是场地/城市描述，不一定是标准球场 ID。
- 多源拼接必须维护 team alias 表。

## 2. API-Football

### 结论

- API key 有效，账号是 Free plan。
- `/leagues?search=world cup` 可用，并确认：
  - World Cup `league.id = 1`
  - seasons 包含 `2026`
- 但该 Free plan **不能访问 2026 season 的 fixtures / odds**。
- 因此本 key 当前不能作为 2026 fixtures / odds 主源。

### 已保存样例

```text
data/probe/apifootball_leagues.json
data/probe/apifootball_fixtures.json
data/probe/apifootball_odds.json
data/probe/apifootball_status.json
data/probe/apifootball_status_headers.txt
```

以上文件不进 git。

### 端点探测结果

| 端点 | 结果 |
|---|---|
| `/leagues?search=world%20cup` | 可用；返回 World Cup league id |
| `/fixtures?league=1&season=2026` | Free plan 拒绝访问 |
| `/odds?league=1&season=2026` | Free plan 拒绝访问 |
| `/status` | 可用；返回 plan 与请求额度 |

### 错误信息摘要

```text
Free plans do not have access to this season, try from 2022 to 2024.
```

### 配额行为

- 响应头包含：
  - `x-ratelimit-limit`
  - `x-ratelimit-remaining`
  - `x-ratelimit-requests-limit`
  - `x-ratelimit-requests-remaining`
- `/status` 响应中包含日请求额度结构。
- 后续 quota ledger 可以优先读远端额度；读不到时使用本地估算。

### 决策

- API-Football 免费档不能作为 2026 主源。
- 赛程主源改用 openfootball。
- 赔率必须走备源：The Odds API / odds-api.io / OddsPapi 至少择一。
- API-Football 可以保留为以后付费或 season 权限变化后的候选源。

## 3. Elo 源：eloratings.net

### 结论

- 可用。
- 无需 API key。
- 可解析国家队 Elo。

### 已保存样例

```text
data/probe/elo_world.tsv
data/probe/elo_teams.tsv
data/probe/elo_home.html
```

以上文件不进 git。

### 可用 URL

```text
https://www.eloratings.net/World.tsv
https://www.eloratings.net/en.teams.tsv
```

### 字段映射

`World.tsv` 当前可按以下方式解析 MVP 所需字段：

| TSV 位置 | 内部字段 | 示例 |
|---|---|---|
| 第 1 列 | `elo_rank` | `1` |
| 第 3 列 | `team_code` | `ES` |
| 第 4 列 | `elo_rating` | `2155` |

`en.teams.tsv` 用于 `team_code -> team_name` 映射。

### 示例解析结果

```text
Spain      ES  2155
Argentina  AR  2114
France     FR  2062
England    EN  2021
Brazil     BR  1991
Mexico     MX  1875
```

### 已知坑

- openfootball 使用英文国家队名，Elo 使用国家代码 + 映射表，仍需 team alias 表。
- 部分历史/地区队名存在多个别名，collector 需要保留无法匹配列表。

## 4. 赔率源：The Odds API

### 结论

- 可用。
- 认证使用 `.env` 中的 `THE_ODDS_API_KEY`，不要写入代码、文档或 git。
- `soccer_fifa_world_cup` 当前为 active，描述为 `FIFA World Cup 2026`。
- `h2h / spreads / totals` 三类盘口都可返回，字段足够支撑 MVP。
- 2026-06-08 探测时返回 72 场，均能与 openfootball 的已确定对阵按队名对齐；未返回的 32 场主要是淘汰赛占位对阵，当前不判源失败。

### 已保存样例

```text
data/probe/theoddsapi_sports.json
data/probe/theoddsapi_sports_headers.txt
data/probe/theoddsapi_wc_odds.json
data/probe/theoddsapi_wc_odds_headers.txt
data/probe/theoddsapi_summary.json
```

以上文件不进 git。

### 端点

```text
GET https://api.the-odds-api.com/v4/sports/?all=true
GET https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds/?regions=eu&markets=h2h,spreads,totals&oddsFormat=decimal&dateFormat=iso
```

### sport key

| key | title | active | has_outrights | 用途 |
|---|---|---:|---:|---|
| `soccer_fifa_world_cup` | FIFA World Cup | true | false | 单场 1X2 / spreads / totals |
| `soccer_fifa_world_cup_winner` | FIFA World Cup Winner | true | true | 冠军 outright，MVP 暂不需要 |

### 响应结构

```text
event
  id
  sport_key
  sport_title
  commence_time
  home_team
  away_team
  bookmakers[]
    key
    title
    last_update
    markets[]
      key
      last_update
      outcomes[]
        name
        price
        point
```

### 字段映射

| The Odds API 字段 | 内部字段 | 说明 |
|---|---|---|
| `event.id` | `source_event_id` | The Odds API 事件 ID |
| `event.commence_time` | `kickoff_at_utc` | ISO8601 UTC，例如 `2026-06-11T19:00:00Z` |
| `event.home_team` | `home_team_name` | 与 openfootball 当前 72 场精确对齐 |
| `event.away_team` | `away_team_name` | 与 openfootball 当前 72 场精确对齐 |
| `bookmakers[].key` | `bookmaker_key` | 稳定 bookmaker 标识 |
| `bookmakers[].title` | `bookmaker_name` | 展示名 |
| `bookmakers[].last_update` | `odds_updated_at` | bookmaker 或 market 更新时间 |
| `markets[].key` | `source_market_key` | `h2h` / `spreads` / `totals` |
| `outcomes[].name` | `selection_name` | 队名、`Draw`、`Over`、`Under` |
| `outcomes[].price` | `decimal_odds` | 十进制赔率 |
| `outcomes[].point` | `line` | 让球或大小球线；`h2h` 无此字段 |

### 内部盘口映射

| 内部盘口 | The Odds API market | selection / line 规则 |
|---|---|---|
| `1X2_90min` | `h2h` | `name` 为主队、客队或 `Draw`；`price` 为 decimal odds |
| `AsianHandicap_90min` | `spreads` | `name` 为队名；`point` 为该队让球线，可出现 `-1.25`、`0.75` 等分位 |
| `OverUnder_90min` | `totals` | `name` 为 `Over` / `Under`；`point` 为进球线，可出现 `2.25`、`2.75` 等分位 |

探测中还出现 `h2h_lay`，主要来自交易所类 bookmaker。Plan 2 collector 应允许未知 market 存在，但 MVP 聚合时先忽略 `h2h_lay`。

### 覆盖与 bookmaker

- 返回事件数：72。
- 每场 bookmaker 数：15 到 25 家，平均约 19.58 家。
- 每场至少有一个 `h2h`、`spreads`、`totals` market。
- 按 market 覆盖：
  - `h2h`：72 场，25 个 bookmaker。
  - `spreads`：72 场，7 个 bookmaker。
  - `totals`：72 场，14 个 bookmaker。
  - `h2h_lay`：50 场，2 个 bookmaker，MVP 暂不使用。
- `spreads` 和 `totals` 返回的 `point` 支持 0.25/0.75 这类亚洲盘常见分位。

### 配额行为

- `/sports/?all=true` 本次 `x-requests-last = 0`，不消耗额度。
- `/odds` 本次 `x-requests-last = 3`，对应 1 个 region x 3 个 markets。
- 响应头包含：
  - `x-requests-remaining`
  - `x-requests-used`
  - `x-requests-last`
- quota ledger 优先读取响应头；如果响应头缺失，再按 `regions * markets` 估算本次消耗。

### 决策

- The Odds API 进入 Plan 2，作为当前 MVP 的赔率源。
- API-Football Free plan 不进入 2026 赔率采集链路，除非后续升级 plan 或 season 权限变化。
- odds-api.io / OddsPapi 暂时保留为未来交叉校验或容灾候选；当前没有必要阻塞 Plan 2。

## 5. Team alias 初稿

### 结论

- The Odds API 的 48 个队名全部出现在 openfootball 已确定对阵中，当前 72 场可按队名 pair 对齐。
- The Odds API 的 48 个队名中，使用 `en.teams.tsv` 全部别名列可匹配 Elo 47 个；剩余 1 个需要手工 alias。
- collector 必须保留 `unmatched_teams` 输出，不能静默丢弃无法匹配的队。

### 必要 alias

| canonical_key | openfootball / The Odds API | Elo code | Elo name / alias | 处理 |
|---|---|---|---|---|
| `bosnia_herzegovina` | `Bosnia & Herzegovina` | `BA` | `Bosnia & Herzegovina` | Elo 已含 alias |
| `czech_republic` | `Czech Republic` | `CZ` | `Czechia` | 需要手工 alias |
| `united_states` | `USA` | `US` | `USA` / `United States` | Elo 已含 alias |

## 6. 状态判定落地

当前根据 API-Football 与 The Odds API 探测结果，状态逻辑调整为：

| 状态 | 含义 |
|---|---|
| `NO_MARKET_YET` | 距开赛 >14 天且无赔率，正常 |
| `ODDS_PENDING` | 赛前 14 天内暂未拿到主盘口，先不判异常 |
| `D` | 临近比赛仍无主盘口、盘口异常、报价不足或数据源权限/覆盖确认失败 |

由于 API-Football Free plan 对 2026 season 无权限，不能用它的 1-14 天窗口判断 2026 出盘状态。实际状态以 The Odds API 为准：

- 小组赛 72 场已经有 `h2h / spreads / totals`，可进入正常赔率采集。
- 淘汰赛占位对阵尚无真实队名时，不判 `D`；保持 fixture 占位状态，等待对阵确定后再匹配赔率。
- 已确定对阵在赛前 14 天内没有目标盘口时，标记 `ODDS_PENDING`。
- 已确定对阵临近比赛仍没有目标盘口、盘口结构异常、`point` 缺失或 bookmaker 数低于 `config/settings.yaml` 中的 `odds.min_books` 时，标记 `D`。

## 7. Refresh / cache policy

### 结论

- `worldcup.refresh_runner` 默认 dry-run；只有显式 `--live` 才会读取 `.env` 并请求外部源。
- `worldcup.scheduler` 默认 dry-run；只读取本地 snapshot / quota 并输出 JSON 决策，不联网、不写状态。
- `worldcup.scheduled_refresh` 默认 dry-run；只有显式 `--live` 且 scheduler 判定 due，或同时传 `--force`，才会调用 refresh runner。
- source refresh 成功时写入 `data/cache/`，随后由 `worldcup.local_runner` 的同名缓存输入契约生成 `analysis_snapshot.json`。
- source refresh 失败但对应缓存文件已存在时，本轮可以继续用上一轮缓存生成快照，避免网络抖动导致整轮分析中断。
- source refresh 失败且对应缓存文件不存在时，必须失败退出，不能生成看似完整但来源缺失的快照。

### 快照质量字段

fallback 发生时，`analysis_snapshot.json` 必须在 `data_quality` 中写入：

```text
source_errors[]
  source
  error

stale_sources[]
```

当前 source name 约定：

| source | 缓存文件 | 说明 |
|---|---|---|
| `openfootball` | `data/cache/openfootball_2026.json` | 赛程源 |
| `eloratings` | `data/cache/elo_world.tsv` + `data/cache/elo_teams.tsv` | Elo 源 |
| `theoddsapi` | `data/cache/theoddsapi_wc_odds.json` | 赔率源 |

前端、云端 ingest 和后续调度不能把 `stale_sources` 非空的快照当作全新数据；可以展示，但需要保留数据质量标记。

### Run metadata

每次 `refresh_runner --live` 或 `local_runner` 从本地样例/缓存生成的 `analysis_snapshot.json` 都必须包含：

```text
run
  schema_version
  run_id
  mode
  observed_at
  policy_version
  policy
    should_refresh
    reason
    policy_reason
    interval_seconds
    now
    last_refresh_at
    next_due_at
    next_kickoff_at
    quota_remaining
    policy_version
  quota
  source_errors
  stale_sources
```

本地 runner 使用 `mode = local` 和 `run_id` 后缀 `-local`，`quota` 可为空，但 `run.run_id`、`run.observed_at` 和 `policy` 必须存在，保证同一份快照可以继续进入 HMAC dry-run、ingest_app 和 FastAPI smoke。

当前 `policy_version = free-tier-v1`。默认策略：

| 条件 | interval | policy_reason |
|---|---:|---|
| The Odds API `remaining <= 30` | 86400 秒 | `quota_low` |
| 下一场 kickoff 在 7 天内 | 21600 秒 | `tournament_window` |
| 其它情况 | 86400 秒 | `default` |

调度执行层应先运行 scheduler dry-run；只有 `decision.should_refresh = true` 时才进入 live refresh。低额度降频优先级高于赛前窗口。

### Scheduled refresh result

`worldcup.scheduled_refresh` 输出 JSON：

```text
status
force
report
refresh
```

状态约定：

| status | 含义 |
|---|---|
| `dry_run` | 默认行为，只输出 scheduler report，不调用 refresh runner |
| `skipped` | 已传 `--live`，但 scheduler 判定 not due，未调用 refresh runner |
| `refreshed` | 已传 `--live`，且 due 或 `--force`，已调用 refresh runner |

cron / launchd 应优先调用默认 dry-run 验证本地状态；真实刷新命令必须显式带 `--live`。

## 8. Cloud ingest dry-run

### 结论

- `worldcup.ingest` 只做本地 dry-run：构造请求体、HMAC 签名头、body hash 和幂等键，不发送线上请求。
- CLI 默认不展开完整 body，只输出 `body_sha256`、`body_bytes`、headers 和目标 URL；需要调试完整请求体时才显式传 `--include-body`。
- HMAC secret 从 `.env` 的 `INGEST_HMAC_SECRET` 读取；文档、日志和 dry-run 输出不得打印 secret。

### Payload

ingest request body 是 canonical JSON：

```text
schema_version
run_id
snapshot_id
snapshot_at
generated_at
snapshot
```

字段约定：

| 字段 | 来源 / 规则 |
|---|---|
| `run_id` | `snapshot.run.run_id`，必填 |
| `snapshot_id` | 对完整 `snapshot` canonical JSON 做 SHA256 |
| `snapshot_at` | `snapshot.snapshot_at` |
| `generated_at` | 构造 ingest payload 的 UTC 时间 |
| `snapshot` | 完整 `analysis_snapshot.json` 内容 |

### HMAC signing

请求方法固定为 `POST`。签名消息为：

```text
timestamp
method
path
run_id
snapshot_id
body_sha256
```

各行用 `\n` 拼接，使用 `HMAC-SHA256(secret, message)`，header 中以 `sha256=<hex>` 传递。

请求头约定：

| Header | 说明 |
|---|---|
| `Content-Type` | 固定 `application/json` |
| `X-Worldcup-Timestamp` | 签名时间，服务端用于防重放窗口 |
| `X-Worldcup-Run-Id` | 本轮 run id |
| `X-Worldcup-Snapshot-Id` | snapshot body hash |
| `X-Worldcup-Body-SHA256` | ingest request body hash |
| `X-Worldcup-Signature` | `sha256=<hmac hex>` |
| `X-Worldcup-Idempotency-Key` | `<run_id>:<snapshot_id>` |

服务端必须用同样规则验签，并以 `X-Worldcup-Idempotency-Key` 做幂等。

### Server-side verification

本地服务端验签模块 `worldcup.ingest_server` 当前实现以下检查：

1. 必填 header 完整。
2. `X-Worldcup-Timestamp` 是 timezone-aware 时间，且与服务端当前时间相差不超过 300 秒。
3. 实际 body SHA256 等于 `X-Worldcup-Body-SHA256`。
4. request body 是 JSON，且包含 `run_id`、`snapshot_id`、`snapshot`。
5. body 内 `run_id` / `snapshot_id` 与 header 一致。
6. `X-Worldcup-Idempotency-Key` 等于 `<run_id>:<snapshot_id>`。
7. `snapshot_id` 等于完整 `snapshot` canonical JSON 的 SHA256。
8. `X-Worldcup-Signature` 等于服务端按同样签名串和 secret 计算出的 `sha256=<hex>`。

本地 `InMemoryIngestStore` 只用于测试幂等语义：

| status | 含义 |
|---|---|
| `stored` | 首次通过验签并写入 idempotency key |
| `duplicate` | 同一个 idempotency key 已存在，本次不重复写入 |
| `rejected` | 验签、body hash、timestamp 或字段一致性失败 |

FastAPI / PostgreSQL 版本必须保留上述验证顺序和状态语义，用数据库唯一键或等价幂等机制承接 `X-Worldcup-Idempotency-Key`。

## 9. Local persistence and preview

### SQLite store

`worldcup.store.SQLiteSnapshotStore` 是默认本地低风险持久化层，写入 `data/local/worldcup.db`。`data/local/` 必须被 git ignore。

`worldcup.store_contract.SnapshotStore` 定义 ingest / query 依赖的持久化边界。`SQLiteSnapshotStore` 和 `worldcup.postgres_store.PostgresSnapshotStore` 都满足该协议；切换持久化实现时不能改写 HMAC 验签、幂等语义或查询投影。

表 `snapshots` 字段：

| 字段 | 说明 |
|---|---|
| `idempotency_key` | 主键，等于 `<run_id>:<snapshot_id>` |
| `run_id` | 本轮 run id |
| `snapshot_id` | snapshot hash |
| `snapshot_at` | snapshot 时间 |
| `stored_at` | 本地写入时间 |
| `payload_json` | 完整 ingest payload |
| `snapshot_json` | 完整 analysis snapshot |

写入语义：

| status | 含义 |
|---|---|
| `stored` | 首次写入 idempotency key |
| `duplicate` | idempotency key 已存在，未重复写入 |

### PostgreSQL store adapter

`worldcup.postgres_store.PostgresSnapshotStore` 是后续 ECS/RDS 使用的持久化适配器。当前单测通过 fake connection 覆盖 schema 初始化、`ON CONFLICT (idempotency_key) DO NOTHING` 幂等写入、`latest_snapshot()` 读取和 JSON 解析；本轮没有连接真实 PostgreSQL 或 RDS。

部署时需要安装可选依赖：

```bash
python3 -m pip install '.[postgres]'
```

或等价安装：

```bash
python3 -m pip install 'psycopg[binary]>=3.2,<4'
```

真实 DSN、用户名、密码只能放在 `.env` 或云端 secret manager，不能写进 git、文档、日志或聊天。

### Store selection

`worldcup.store_factory.create_snapshot_store` 负责把配置映射到 `SnapshotStore`：

| 配置 | 行为 |
|---|---|
| 未设置 / `WORLDCUP_STORE=sqlite` | 使用 `SQLiteSnapshotStore` 和 `--db` 路径 |
| `WORLDCUP_STORE=postgres` 或 CLI `--store postgres` | 使用 `PostgresSnapshotStore`，必须提供 `DATABASE_URL` |

CLI 覆盖规则：

| 入口 | 参数 | 环境变量 |
|---|---|---|
| `python3 -m worldcup.fastapi_app` | `--store sqlite|postgres`、`--database-url-env DATABASE_URL` | `.env` 中的 `WORLDCUP_STORE` / `DATABASE_URL` |
| `python3 -m worldcup.ingest_app` | `--store sqlite|postgres`、`--database-url-env DATABASE_URL` | `.env` 中的 `WORLDCUP_STORE` / `DATABASE_URL` |

默认本地路径不需要 `DATABASE_URL`。只有选择 PostgreSQL 时才需要该变量名存在且有值；真实值不得写入文档、日志、提交信息或聊天。

### Local ingest app

`worldcup.ingest_app.process_local_ingest` 执行：

1. 调用 `worldcup.ingest_server.verify_ingest_request`。
2. 验签失败时返回 `rejected`，不写入 snapshot。
3. 验签成功时写入注入的 `SnapshotStore`；默认本地 CLI 使用 `SQLiteSnapshotStore`，返回 `stored` 或 `duplicate`。

CLI 只做本地验证和入库，不发送线上请求：

```bash
python3 -m worldcup.ingest_app --db data/local/worldcup.db --snapshot data/cache/analysis_snapshot.json --env .env
```

### Query projection

`worldcup.query` 提供只读投影：

| 函数 | 说明 |
|---|---|
| `load_latest_snapshot(db_path, store=None)` | 从默认 SQLite 或注入的 `SnapshotStore` 读取最新 snapshot |
| `project_match_rows(snapshot)` | 输出预览/API 可用的比赛行 |

比赛行字段：

```text
kickoff_at_utc
stage
group
home_team
away_team
match_label
signal_count
top_grade
stale
```

不得在投影中加入 stake、bet amount、下注金额或其它资金字段。

### Static preview

`worldcup.preview` 生成单文件 HTML：

```bash
python3 -m worldcup.preview --snapshot data/cache/analysis_snapshot.json --out data/cache/preview.html
```

预览页必须包含：

- `研究分析工具，不构成投注建议` 免责声明。
- counts 概览。
- data quality：`stale_sources`、`source_errors`、`missing_odds`、`missing_elo`、`time_mismatches`。
- 比赛表：UTC 开赛、阶段、小组、对阵、信号数、最高等级、缓存兜底。
- 不显示资金相关字段。

### Local HTTP route contract

`worldcup.http_app` 是标准库 HTTP 适配层，用于本地预览和路由契约测试；不是最终 ECS/FastAPI 部署形态。

当前路由：

| Method | Path | 行为 |
|---|---|---|
| `POST` | `/api/ingest/snapshot` | 调用本地 ingest app，验签后写入 `SnapshotStore` |
| `GET` | `/api/snapshot/latest` | 返回最新完整 snapshot |
| `GET` | `/api/matches` | 返回 `project_match_rows(snapshot)` |
| `GET` | `/preview` | 返回静态 HTML 预览页 |
| `GET` | `/healthz` | 返回服务存活状态；不读 DB、不依赖 secret |

### Local ASGI adapter

`worldcup.asgi_app.create_asgi_app` 是无外部依赖 ASGI 适配层，当前只包装 `worldcup.http_app.handle_request` 的路由契约，方便后续迁移到 FastAPI 或 ASGI server 时复用测试边界。

当前覆盖：

- `GET /api/matches`
- `GET /preview`
- `GET /healthz`

正式 ASGI server / FastAPI 依赖安装、启动常驻服务、ECS 部署和云端写入必须单独确认。

### FastAPI adapter

`worldcup.fastapi_app.create_fastapi_app` exposes the same local route contract as `worldcup.http_app` and delegates security-sensitive behavior to existing modules. It accepts an optional injected `SnapshotStore`, so later ECS/RDS wiring can use `PostgresSnapshotStore` without duplicating route logic. It must not reimplement HMAC verification, idempotency, query projection, or preview rendering.

The adapter is local-only until ECS deployment is separately confirmed.

### Local secret helper

`worldcup.secrets` 用于生成本地 HMAC secret 文本，方便人工写入 `.env`：

```bash
python3 -m worldcup.secrets
```

输出格式：

```text
INGEST_HMAC_SECRET=<hex value>
```

该工具不读取 `.env`、不写 `.env`、不写文档、不联网。真实 secret 只能留在 `.env` 或安全配置系统里，不能提交到 git。

### Static site/API export

`worldcup.export` 可从本地 snapshot 导出静态站点草案：

```bash
python3 -m worldcup.export --snapshot data/cache/analysis_snapshot.json --out-dir data/cache/site
```

导出文件：

| 文件 | 说明 |
|---|---|
| `index.html` | 研究预览页 |
| `api/snapshot/latest.json` | 完整最新 snapshot |
| `api/matches.json` | 只读比赛行投影 |
| `manifest.json` | 导出元数据 |

`data/cache/site/` 必须保持 git ignore；该输出只代表本地静态包草案，不代表已部署。

### Readiness check

`worldcup.readiness` 是只读上线前检查：

```bash
python3 -m worldcup.readiness --root .
```

检查项：

- `.env` 是否存在指定变量名：`THE_ODDS_API_KEY`、`INGEST_HMAC_SECRET`。
- `.env.example` 是否存在、包含必需变量名、值为空，并未被 `.env.*` 通配规则误忽略。
- store 配置是否有效：默认 `sqlite`；选择 `postgres` 时 `.env` 必须包含 `DATABASE_URL` 变量名，输出不能包含该值。
- `data/cache/analysis_snapshot.json` 是否存在且包含比赛。
- quota ledger 是否存在并可解析。
- `data/cache/preview.html` 与 `data/cache/site/index.html` 是否存在。
- 预览页和静态首页是否保留 `研究分析工具，不构成投注建议` 免责声明。
- `.env`、`data/cache/`、`data/local/`、`data/probe/` 是否被 git ignore。

readiness check 不联网、不打印 secret 值或 `DATABASE_URL` 值。当前如果 `.env` 缺少真实 `INGEST_HMAC_SECRET`，必须报错；不要自动生成并写入 `.env`。`.env.example` 只能保留变量名和空值，不能写入任何真实或示例 secret 值。

正式 FastAPI/ECS 版本应复用同一验签、幂等、查询投影和预览安全规则；上线、云端写入和部署必须单独确认。

## 10. Plan 2 输入要求

Plan 2 collectors 可以基于当前契约开工，最小输入如下：

1. 使用保存的 The Odds API 样例写离线解析测试。
2. 三盘口字段映射已确认：
   - `1X2_90min`
   - `OverUnder_90min`
   - `AsianHandicap_90min`
3. bookmaker 数量与 line 表达方式已确认。
4. quota / credit 消耗方式已确认。
5. team alias 初稿已确认，但 collector 仍必须输出无法匹配列表。

Plan 2 不应再按假接口写 collector；必须以 `data/probe/theoddsapi_wc_odds.json`、`data/probe/openfootball_2026.json`、`data/probe/elo_world.tsv`、`data/probe/elo_teams.tsv` 作为离线测试样例。
