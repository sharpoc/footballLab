# 数据源探测契约（Plan 0 初稿）

- 日期：2026-06-08
- 范围：2026 世界杯 MVP 数据源探测
- 结论状态：部分完成；赛程与 Elo 可用，API-Football 免费档不能访问 2026 season，赔率备源待 key 后继续探测

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

## 4. 赔率备源

### 当前状态

尚未探测。原因：当前只拿到 API-Football key，未拿到 The Odds API / odds-api.io / OddsPapi key。

### 下一步至少注册一个

| 服务 | 官网 | 变量名 |
|---|---|---|
| The Odds API | https://the-odds-api.com/ | `THE_ODDS_API_KEY` |
| odds-api.io | https://odds-api.io/ | `ODDS_API_IO_KEY` |
| OddsPapi | https://oddspapi.io/ | `ODDSPAPI_KEY` |

### 选择标准

优先选择同时满足以下条件的源：

1. 覆盖 `soccer_fifa_world_cup`
2. 有 `h2h / totals / spreads` 或等价三盘口
3. 返回 bookmaker、market、line、selection、price
4. 免费额度能支撑赛前 3 小时级别刷新
5. 能提供响应头或接口字段辅助 quota ledger

## 5. 状态判定落地

当前根据 API-Football 探测结果，状态逻辑调整为：

| 状态 | 含义 |
|---|---|
| `NO_MARKET_YET` | 距开赛 >14 天且无赔率，正常 |
| `ODDS_PENDING` | 赛前 14 天内暂未拿到主盘口，先不判异常 |
| `D` | 临近比赛仍无主盘口、盘口异常、报价不足或数据源权限/覆盖确认失败 |

由于 API-Football Free plan 对 2026 season 无权限，不能用它的 1-14 天窗口判断 2026 出盘状态；实际状态应以后续赔率备源探测结果为准。

## 6. Plan 2 输入要求

Plan 2 collectors 开工前，还缺：

1. 至少一个赔率备源的真实响应样例。
2. 三盘口字段映射：
   - `1X2_90min`
   - `OverUnder_90min`
   - `AsianHandicap_90min`
3. bookmaker 数量与 line 表达方式。
4. quota / credit 消耗方式。
5. team alias 初稿。

在这些缺口补齐前，不应写真 collectors。
