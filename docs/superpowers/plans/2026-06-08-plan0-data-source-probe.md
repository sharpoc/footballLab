# 数据源探测 实现计划（Plan 0：Task 0，实施第一步）

> **For agentic workers:** 本计划是**探测/调研**，不是 TDD 编码。目标是用真实 key 验证每个数据源，产出"数据契约"文档，供 Plan 2（采集层）按真实字段写 collector。**不通过则切备源**。

> **项目规则提醒**：探测脚本只读取外部 API、写本地 `data/probe/` 样例与 `docs/.../data-contract.md`，不 `git commit`、不开通云资源（除非用户同意）。所有 key 放本地 `.env`，绝不进 git/文档。

**Goal:** 确认 openfootball / API-Football / 高频赔率备源 / eloratings 对 2026 世界杯的真实可用性与字段结构，产出 `docs/superpowers/data-contract.md` 作为 Plan 2 的契约。

**前置（需用户先完成）—— 注册 key 清单：**
| 源 | 注册地址 | 免费档 | 拿到什么 | `.env` 变量 |
|----|---------|-------|---------|------------|
| API-Football | https://www.api-football.com/ | 100 次/天 | API key | `API_FOOTBALL_KEY` |
| The Odds API | https://the-odds-api.com/ | 500 credits/月 | API key | `THE_ODDS_API_KEY` |
| odds-api.io | https://odds-api.io/ | 100 次/小时 | API key | `ODDS_API_IO_KEY` |
| OddsPapi | https://oddspapi.io/ | 250 次/月 | API key | `ODDSPAPI_KEY` |
| openfootball | 无需注册 | 无限 | 直接 raw URL | — |
| eloratings | 无需注册 | 抓网页 | — | — |

> 备源（The Odds API / odds-api.io / OddsPapi）**至少注册一个**即可开始；三个都注册能更全面对比、最终择一。

---

## Task 0.1：openfootball 赛程探测（无需 key）

**产出：** `data/probe/openfootball_2026.json` + 字段映射记录。

- [ ] **Step 1: 拉取 2026 世界杯 JSON**

Run:
```bash
mkdir -p data/probe
curl -sL "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json" \
  -o data/probe/openfootball_2026.json
```
Expected: 文件非空（数百 KB 量级）。若 404，尝试仓库内其它路径（`2026--canada-mexico-usa/` 等），并记录真实路径。

- [ ] **Step 2: 检查结构与字段**

Run:
```bash
python -c "import json;d=json.load(open('data/probe/openfootball_2026.json'));print(type(d), list(d)[:10] if isinstance(d,dict) else len(d))"
```
记录：顶层结构、比赛字段名（date/time/team1/team2/group/round/stadium/city）、队名格式（全称/缩写/是否含国旗 emoji）、是否含淘汰赛阶段。

- [ ] **Step 3: 写入数据契约**

在 `docs/superpowers/data-contract.md` 的「赛程」章节记录：真实 raw URL、字段 → 内部 `Fixture` 字段映射、kickoff 时间的时区（UTC？当地？）、队名与其它源如何对齐（建立 team 名称别名表的必要性）。

---

## Task 0.2：API-Football 探测（核心，需 `API_FOOTBALL_KEY`）

**产出：** `data/probe/apifootball_*.json` 多个 + 契约记录 + 配额行为结论。

- [ ] **Step 1: 找到 2026 世界杯 league id**

Run:
```bash
source .env
curl -s "https://v3.football.api-sports.io/leagues?search=world%20cup" \
  -H "x-apisports-key: $API_FOOTBALL_KEY" -o data/probe/apifootball_leagues.json
python -c "import json;d=json.load(open('data/probe/apifootball_leagues.json'));[print(x['league']['id'],x['league']['name'],[s['year'] for s in x['seasons']][-3:]) for x in d['response']]"
```
记录：World Cup 的 `league.id`、2026 season 是否存在。

- [ ] **Step 2: 拉 fixtures（用上一步 league id 与 season 2026）**

Run:
```bash
LEAGUE_ID="$(python - <<'PY'
import json
d=json.load(open('data/probe/apifootball_leagues.json'))
for item in d.get('response', []):
    if item.get('league', {}).get('name') == 'World Cup':
        years = {s.get('year') for s in item.get('seasons', [])}
        if 2026 in years:
            print(item['league']['id'])
            break
PY
)"
test -n "$LEAGUE_ID"
curl -s "https://v3.football.api-sports.io/fixtures?league=$LEAGUE_ID&season=2026" \
  -H "x-apisports-key: $API_FOOTBALL_KEY" -o data/probe/apifootball_fixtures.json
python -c "import json;d=json.load(open('data/probe/apifootball_fixtures.json'));print('count',d['results']);print(d['response'][0] if d['response'] else 'EMPTY')"
```
记录：是否返回 104 场、`fixture.id` 结构、`fixture.date`（确认是否 ISO8601 带时区）、teams 命名、venue。

- [ ] **Step 3: 探 odds / bookmakers / bets**

Run:
```bash
LEAGUE_ID="$(python - <<'PY'
import json
d=json.load(open('data/probe/apifootball_leagues.json'))
for item in d.get('response', []):
    if item.get('league', {}).get('name') == 'World Cup':
        years = {s.get('year') for s in item.get('seasons', [])}
        if 2026 in years:
            print(item['league']['id'])
            break
PY
)"
test -n "$LEAGUE_ID"
curl -s "https://v3.football.api-sports.io/odds?league=$LEAGUE_ID&season=2026" \
  -H "x-apisports-key: $API_FOOTBALL_KEY" -o data/probe/apifootball_odds.json
python -c "import json;d=json.load(open('data/probe/apifootball_odds.json'));print('results',d['results']);r=d['response'][0] if d['response'] else None;print([b['name'] for b in r['bookmakers']][:10] if r else 'NO ODDS')"
```
记录：是否返回赔率、哪些 bookmaker、`bets` 里有哪些 market（Match Winner / Goals Over-Under / Asian Handicap 的真实名称与 id）、盘口线如何表示（`value` 字段格式）。**这是 Plan 2 让球/大小球解析的关键。**

- [ ] **Step 4: 探 lineups 可用性 + 配额头**

Run:
```bash
curl -sD - "https://v3.football.api-sports.io/status" \
  -H "x-apisports-key: $API_FOOTBALL_KEY" -o data/probe/apifootball_status.json | grep -i "x-ratelimit\|x-requests"
python -c "import json;d=json.load(open('data/probe/apifootball_status.json'));print(d['response']['requests'])"
```
记录：返回头里是否有剩余配额字段（供 quota ledger 用）、当日用量结构；lineups 端点是否在免费档可用（赛前才有数据，记录端点形态即可）。

- [ ] **Step 5: 决策门**

- 若 league id + fixtures 可用，且对**赛前 1–14 天窗口内的具体 fixture**能返回目标盘口 → **API-Football 作一站式主源**，记录全部字段映射。
- 若远期比赛（>14 天）无 odds → 记录为 `NO_MARKET_YET`，**不判主源失败**。
- 若 14 天内 fixture 暂无主盘口 → 记录为 `ODDS_PENDING`，继续交叉验证备源，**不立即判主源失败**。
- 只有当临近比赛（赛前 24h/2h）仍无主盘口、分页/fixture 参数确认无误且盘口长期不全时，才标记"赔率走备源"，进入 Task 0.3 择源。

---

## Task 0.3：高频赔率备源择一（需至少一个备源 key）

**产出：** 三选一结论 + 该源契约。逐个探测，覆盖世界杯则记录。

- [ ] **Step 1: The Odds API**

Run:
```bash
source .env
curl -sD data/probe/theoddsapi_headers.txt \
  "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds?regions=eu&markets=h2h,spreads,totals&apiKey=$THE_ODDS_API_KEY" \
  -o data/probe/theoddsapi_wc.json
python -c "import json;d=json.load(open('data/probe/theoddsapi_wc.json'));print('events',len(d) if isinstance(d,list) else d)"
grep -i "x-requests" data/probe/theoddsapi_headers.txt || true
```
记录：是否返回赛事、`bookmakers`、`markets`（spreads/totals 是否真有）、响应头 `x-requests-remaining`（credit 消耗）。

- [ ] **Step 2: odds-api.io**

Run:
```bash
curl -s "https://api.odds-api.io/v2/sports?apiKey=$ODDS_API_IO_KEY" -o data/probe/oddsapiio_sports.json
# 在返回里找 world cup / soccer 对应 sport key，再查 odds
```
记录：是否含世界杯 sport、盘口、bookmaker 数、每小时额度实测。（端点以官方文档为准，记录真实路径。）

- [ ] **Step 3: OddsPapi（低频交叉校验）**

Run:
```bash
test -n "$ODDSPAPI_KEY"
printf '%s\n' "先打开 OddsPapi 官方文档，记录世界杯赛事/赔率真实 endpoint、认证方式与计费口径；确认真实 endpoint 后再保存请求到 data/probe/oddspapi_probe.json。未确认真实 endpoint 前，OddsPapi 不进入主/备源候选。"
```
记录：一次请求返回多少家、是否含世界杯、月额度。（端点以官方文档为准。）

- [ ] **Step 4: 决策门**

按"覆盖世界杯 + 含三盘口 + 额度够分级刷新"打分，**择一**作高频备源；写入契约：选了哪个、字段映射、额度策略、credit/请求计费方式（喂 quota ledger）。

---

## Task 0.4：Elo 探测（抓网页）

**产出：** `data/probe/elo.html` + 解析可行性结论。

- [ ] **Step 1: 抓取国家队 Elo**

Run:
```bash
curl -sL "https://www.eloratings.net/" -o data/probe/elo_home.html
# 国家队当前排名页/数据接口（eloratings 有 _j/ 数据文件，记录真实可解析来源）
```
记录：哪个 URL 能拿到 {国家 → Elo 分} 的可解析数据；队名格式（与赛程/赔率源如何对齐）。若 eloratings 难解析，改试 footballratings.org，记录结论。

- [ ] **Step 2: 验证可解析出 {team: elo}**

写一个一次性解析片段，确认能得到 dict，至少覆盖参赛 48 队中的大部分。记录无法匹配的队名 → 进入别名表。

---

## Task 0.5：产出数据契约文档（汇总）

**产出：** `docs/superpowers/data-contract.md`（Plan 2 的输入）。

- [ ] **Step 1: 汇总各源结论**，按章节写：赛程源、实力(Elo)源、主赔率源、备赔率源；每章含：真实 URL/端点、认证方式、字段 → 内部模型映射、时区、额度/计费、已知坑。
- [ ] **Step 2: 写「team 名称别名表」初稿**：把赛程/赔率/Elo 三源的队名对齐到统一 key（这是多源拼接的关键）。
- [ ] **Step 3: 写「降级/状态判定」落地参数**：远期 `NO_MARKET_YET` 阈值（>14 天）、`ODDS_PENDING`（14 天内无盘）、`D`（赛前 24h/2h 仍无盘），与各源实际出盘时间对齐。
- [ ] **Step 4: 决策记录**：主源/备源最终选型 + 理由；标记哪些进入 Plan 2。

---

## 完成标准（DoD）

- [ ] 每个采用的源都有：可复现的真实请求 + 保存的样例响应 + 字段映射。
- [ ] `data-contract.md` 足够让 Plan 2 不靠猜接口就能写 collector 与解析测试（用保存的样例响应做离线解析测试）。
- [ ] 明确主源/备源选型；明确 team 名称别名策略。
- [ ] 所有 key 仅在 `.env`，样例响应中如含敏感信息需脱敏后再留存。
