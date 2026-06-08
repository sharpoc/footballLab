# 世界杯足彩分析站 MVP 设计文档（v8）

- 日期：2026-06-08
- 状态：已与用户多轮确认，待用户最终审阅 spec
- 范围：2026 世界杯期间可上线的最小可用版本（MVP）
- 定位：**研究/分析站**，输出价值信号，不构成投注建议、不显示金额
- v3 相比 v2：macmini 经 ECS ingest API 上传（不直连 RDS）；新增 Task 0 数据源探测；刷新触发表述改实；强制区分市场类型；赔率按 line 聚合；Poisson λ 给具体公式与 clamp；launchd 保活 + heartbeat；措辞改为价值信号/无价值/禁止参考
- v4 相比 v3：删除金额示例（彻底不提金额）；新增「赔率新鲜度/报价不足 → 禁止 S/A」降级硬规则；ingest 补幂等键+HMAC+schema 校验防重放；人工录入限定为本地 manual_overrides.yaml（无 admin）；API-Football 覆盖措辞改为「预期，以 Task 0 为准」；赔率聚合补异常值过滤；删除文末多余代码块围栏
- v5 相比 v4：亚洲让球改为严谨结算表（区分整数/半盘/四分之一盘，明确 push 与半赢半输）；大小球明确「仅 2.5 主线，无报价则标 D」；server 测试要求至少一个 Postgres 集成测试（覆盖幂等/约束）；Task 0 明确为实施第一步；禁睡眠标注为可选+需用户确认+脚本不默认改系统
- v6 相比 v5：The Odds API 的 spreads/totals 改为「须 Task 0 实测、仅作备胎」；降级硬规则只在「依赖高频备源或赔率不新鲜」时压制 S/A（主源 fresh 不误伤）；亚洲让球只用 EV 定级（不用 Edge）；补 API-Football 赔率窗口（赛前 1–14 天可用、历史仅 7 天，远期无赔率属正常）；ingest 鉴权固定为 HMAC+timestamp+id（同 id 不同 payload→409）；测试描述「半盘线」纠正为「四分之一盘线」
- v7 相比 v6：新增 `NO_MARKET_YET` 状态（远期正常无赔率，区别于真异常 D）；全文 token 表述统一为 HMAC（`.env` 用 `INGEST_HMAC_SECRET`）；明确「每轮成功都上传 snapshot+heartbeat，仅关键变化才生成 change events」；1X2/OU 等级改为 EV+Edge 双阈值（A: Edge≥2%、B: Edge≥1%）；Elo 平局给最小公式与 config 常数；Poisson 矩阵 0~8→0~10 并加尾部质量阈值测试
- v8 相比 v7：明确时区（DB 存 UTC、字段 tz-aware、前端 Asia/Shanghai + venue 当地时间）；写死 HMAC 签名规范（签 raw body、header、±300s、重放表 7 天）；赔率状态增设 `ODDS_PENDING` 三态（仅赛前 24h/2h 仍无主盘口才判 D）；The Odds API 措辞改为「官方有示例但须实测公司/线/credit」；新增本地 quota ledger（读不到远端额度时本地估算降频）；Poisson 尾部阈值放宽到 1%

## 0. 背景与硬约束

- 今天 2026-06-08，2026 世界杯 6 月 11 日开赛（3 天后），共 104 场。
- 目标：赶本届，先把「采集 → 分析 → 公开网站」整条链路跑通；简单模型 + 赔率对比即可。
- 数据：从零开始，只用免费 / 可抓数据源。
- 网站：公开给他人访问，用用户自有域名（整理中，先用占位符 `你的域名`）。
- **支持的市场类型（仅这三种，90 分钟内）**：
  - `1X2_90min`（胜平负）
  - `OverUnder_90min`（大小球，主线 2.5）
  - `AsianHandicap_90min`（亚洲让球，主盘口线）
  - **不支持**：晋级/出线盘（To_Qualify）、冠军盘（Tournament_Winner）、半全场、比分等。淘汰赛严禁把「晋级概率」与「90 分钟胜平负」混用。
- 部署：阿里云；网站独立于 macmini（macmini 只算，结果经 ECS 写云端，macmini 关机网站照常在）。
- **时区**：DB 统一存 **UTC**；所有时间字段（`fetched_at / kickoff_at / snapshot_at / heartbeat_at` 等）必须 **timezone-aware**；前端默认显示 **Asia/Shanghai**，并附 venue 当地时间（赛事跨美/加/墨多时区）。

## 1. 总目标与原则

按分级节奏自动：采集 → 多模型量化分析 → 与赔率对比找正期望 → 检测与上一轮的重要变化 → 经 ECS 写云端并在网站体现。网站同时展示「分析步骤」和「分析结果」，重要变化高亮。

核心原则：
1. 先算概率，再比赔率；「更可能赢」≠「有价值」。
2. 只在「模型概率 > 去水市场概率 + 安全边际」时才视为价值信号候选。
3. 没有明显正期望就明确输出「无价值」。
4. 本届模型**未经回测验证**，公开页只输出「价值信号」，**不显示任何下注金额**，措辞避免喊单。
5. 公开站必须带醒目免责声明（仅供研究参考、不构成投注建议、理性投注、风险自负、遵守当地法律）。
6. 诚实披露：单届样本极小、本届未做回测，结果仅供研究娱乐参考。

## 2. 架构（阿里云：macmini 算 + ECS ingest/web + RDS + OSS）

```
macmini（launchd 保活 → apscheduler 常驻，按分级节奏跑 pipeline）
  1. collectors/  采集：赛程 / Elo / FIFA 排名 / 赔率
  2. engine/      分析：Elo + Poisson + 集成 + 去水 + EV/等级（不含金额）
  3. differ/      对比上一轮，标出「重要变化」
  4. publisher/   调用 ECS ingest API 上传本轮结果（HTTPS + HMAC 签名）
        │ HTTPS POST（HMAC 签名 + timestamp + run_id）
        ▼
阿里云 ECS / 轻量应用服务器
  ├─ ingest API（FastAPI，鉴权）：校验 → 写 RDS + 上传快照到 OSS
  └─ web API + 静态前端：读 RDS 对外提供 JSON/页面（Nginx + HTTPS，绑域名）
        │                                  ▲
        ▼                                  │
阿里云 RDS PostgreSQL（真相源：       访客手机/浏览器
  数据 / 历史快照 / 变化事件流）       （macmini 关机时显示最后一轮）
阿里云 OSS（原始快照 / 备份 / 冷存储）
```

关键点：
- **macmini 不直连 RDS/OSS**，只调用 ECS 的 ingest API（HMAC 签名鉴权）。RDS 仅对 ECS 内网开放，**不暴露公网、不需要家庭动态 IP 白名单/SSL 配置**。
- **ingest 幂等与防重放**：固定采用 **HMAC 签名 + 请求时间戳 + `run_id`/`snapshot_id` 幂等键**（非二选一）。ECS 校验 payload schema 与签名、拒绝过期/超时请求；同一 id 且 payload hash 相同 → **重复上传返回同一结果**（不重复写快照/事件）；**同一 id 但 payload hash 不同 → 返回 `409` 拒绝**。定时任务的重试/断线重传因此安全。
- **签名规范（macmini client 与 ECS server 共用，写死避免对不上）**：HMAC-SHA256 对 **raw request body 字节**签名（不依赖 JSON 序列化差异）；请求头 `X-Signature`(hex) / `X-Timestamp`(epoch 秒) / `X-Run-Id`；时间偏移窗口 ±300s；密钥 `INGEST_HMAC_SECRET`；重放表保存 `run_id/snapshot_id + payload hash` ≥ 7 天。
- ECS 从 RDS 读取对外服务，与 macmini 解耦：macmini 关机时网站仍显示最后一轮结果。
- 网站显示 **worker heartbeat**（macmini 最近一次成功上传时间），超时则在数据健康面板告警「采集端可能离线」。

部署复杂度说明（风险）：ECS + RDS + OSS 的开通与打通（安全组、内网连通、HTTPS、域名解析、ingest 鉴权）比纯静态托管重，3 天内属偏紧任务，需预留配置时间。

## 3. 数据源（多源分层）

```
赛程主源        : openfootball/worldcup.json  (CC0, 无 key, 零配额)
一站式低频主源  : API-Football 免费档 (100 次/天; 赛前赔率每 3 小时更新)
赔率高频备源    : odds-api.io / The Odds API / OddsPapi —— 经 Task 0 实测后择一
回测历史        : Football-Data.co.uk (历史结果 + 赔率 CSV)
Elo            : eloratings.net / footballratings.org (抓取, 需容错)
FIFA 排名      : 公开排名页
```

关键事实与策略：
- **API-Football 免费档 = 100 次/天**（00:00 UTC 重置），全端点含赛前赔率，**预期覆盖世界杯（最终以 Task 0 实测为准）**。但**赛前赔率每 3 小时更新**，官方建议每 3 小时调用一次。
- **双新鲜度**：**模型**按分级节奏高频重算（不耗外部配额）；**赔率**新鲜度受限于 3 小时，网站明确标注「赔率为 X 分钟/小时前快照」。
- API-Football 赛前赔率还有**窗口限制**：通常**赛前 1–14 天才有赔率**、**历史赔率仅保留 7 天**。因此**远期比赛（>14 天）暂无赔率属正常**，标状态 `NO_MARKET_YET`（**不是 D**）：该场仅出无赔率的模型预测，市场对比/等级留空。回测历史赔率改用 Football-Data.co.uk。
  - 状态区分（三态，避免误判）：`NO_MARKET_YET` = 距开赛 >14 天正常无赔率；`ODDS_PENDING` = 赛前 14 天内但暂未出主盘口赔率（赔率可用性因联赛/比赛/公司而异，先不判 D）；`D` = **赛前 24h/2h 内仍无主盘口** / 盘口异常 / 报价不足等真异常（见 5.5）。
- 若需更快的临场赔率变化，再用 odds-api.io（免费 100 次/小时）/ The Odds API / OddsPapi 做**补充探测**；这些源是否覆盖世界杯、含几家博彩公司，**须经 Task 0 实测确认**，不预先承诺。
- OddsPapi（250 次/月，一次返回某场全部博彩公司）作**低频交叉校验**，不作高频主源。
- The Odds API 免费档弱（500 credits/月 ≈ 166 次三盘口调用）；官方 World Cup 页面有 `soccer_fifa_world_cup` + `h2h,spreads,totals` 示例，但**仍须 Task 0 实测可用博彩公司、盘口线与 credit 消耗**，仅作实测通过后的备胎。
- SportsGameOdds 免费 Amateur 档（2.5k objects/月、9 家、8 联赛），覆盖有限，不作主力。

容错原则：
- 每个 collector 独立 try/except，单源失败不影响其他源，用上一轮缓存顶上。
- 每条数据带 `fetched_at`，网站显示新鲜度。
- 失败记错误日志；网站「数据健康」面板显示：哪些源正常 / 用旧缓存 / 各 API 剩余配额 / worker heartbeat。

敏感信息：所有 API key、`INGEST_HMAC_SECRET`（ingest HMAC 密钥）、RDS 连接串放本地环境变量或 `.env`，绝不进 git / 网站 / 任何说明文件。

## 4. 刷新节奏（分级，apscheduler 动态间隔）

| 状态 | 模型重算间隔 | 赔率刷新 |
|------|------|------|
| 平时（距开赛 >24h） | 60 分钟 | 受 API-Football 3h 限制 / 低频 |
| 赛前 24 小时内 | 30 分钟 | 每 3 小时 + 必要时备源探测 |
| 赛前 2 小时内 | 15 分钟 | 每 3 小时 + 备源探测（若已接入） |

触发与发布（无 webhook，变化只能在轮询后发现）：
- **每轮成功都上传 current snapshot + worker heartbeat**（刷新"最后更新时间"与心跳，即使无变化）；differ **只有发现关键变化时才额外生成 change events**（喂首页高亮区 + 详情页时间线）。这样无变化时网站也不会显示假性"变旧"。
- **人工录入（MVP 范围限定）**：不做 admin 后台 / 网页录入入口；仅支持本地 `data/manual_overrides.yaml`（首发/伤病等），由 macmini pipeline 读取，下一轮生效；必要时手动跑一轮使其立即生效。
- 每轮采集前预估外部请求数，超配额自动降频并在网站标注。
- **本地 quota ledger**：记录每个 provider 每日/每小时请求数；优先读远端返回的剩余额度，**读不到时用本地估算**降频。快用完时网站告警并停止刷新赔率（用最后快照）。

## 5. 分析引擎

### 5.1 Elo 模型 → 胜平负
- 主胜期望 `We = 1 / (10^(-dr/400) + 1)`，`dr = 主队Elo − 客队Elo`；中立场不加主场分，非中立 dr 先 +100。
- 平局概率给定最小公式（常数入 config，待回测微调）：
  - `p_draw = clamp(base_draw - k * abs(dr), min_draw, max_draw)`，初始 `base_draw=0.28`、`k=0.0003`、`min_draw=0.18`、`max_draw=0.32`。
  - 非平局概率 `1 - p_draw` 按 Elo 胜负倾向拆分：`p_home = (1 - p_draw) * We`，`p_away = (1 - p_draw) * (1 - We)`。
  - 三者天然和为 1。

### 5.2 Poisson 比分矩阵 → 三盘口（核心，给具体 λ 公式）
初始公式（所有常数写入 `config/settings.yaml`，初始值待回测微调）：
- 大赛平均总进球 `mu_total = 2.6`。
- Elo 差映射净胜球：`gd = clamp(dr / 250, -2.5, 2.5)`（dr 含中立/主场修正后）。
- `lambda_home = clamp(mu_total/2 + gd/2, 0.15, 4.5)`
- `lambda_away = clamp(mu_total/2 - gd/2, 0.15, 4.5)`
- 比分矩阵 `P(i,j) = Poisson(i; lambda_home) × Poisson(j; lambda_away)`，i,j 取 **0~10**（比 0~8 更稳，减小尾部压缩）。
- **尾部归一化**：矩阵除以其总和，使概率和 = 1（弥补截断尾部）。测试对归一化后的矩阵断言；并断言**归一化前截断尾部质量 < 阈值（先放宽到 1%）**，超阈值时测试提示调大矩阵上限——不把 MVP 卡在极端 λ 的数学边界上。
- 从同一矩阵推出全部盘口：
  - `1X2_90min`：下三角 / 对角 / 上三角求和
  - `OverUnder_90min`（2.5）：i+j≥3 vs ≤2
  - `AsianHandicap_90min`：见 5.5 结算表

### 5.3 集成
- 胜平负 = Elo 与 Poisson 加权（初始各 50%，写进 config 可调）。
- 大小球 / 让球 = Poisson 单独出。
- 本届不做动态权重 / 校准，代码留接口。

### 5.4 赔率去水与聚合 → 市场隐含概率
- 原始隐含 = `1/赔率`，按市场归一化去水钱。
- **聚合规则**：严格按 `market_type + line + selection` 聚合，**只对同一盘口线求平均**（`Over 2.5` 不能与 `Over 2.25` 平均；`-0.5` 不能与 `-0.75` 平均）。
- **只展示并分析主盘口线**：大小球主线 2.5；让球取该场最主流盘口线（按多家公司众数/挂盘量）。
- **异常值过滤**：剔除明显离群赔率、过期 bookmaker、暂停盘口（suspended/blocked）；有效报价公司少于 N 家（如 < 3）时不出 S/A（配合 5.5 降级硬规则）。
- **大小球仅做 2.5 主线（MVP）**：若该场无 2.5 报价，则 `OverUnder_90min` 市场**不分析、标 D**，不退化到 2.25 / 2.75 / 3.0。（「跟随任意主盘口线 + Poisson 推导任意 line 概率」列入以后加。）

### 5.5 EV / Edge / 等级（不含金额）
- 整数盘（`1X2_90min`、`OverUnder_90min`）：`Edge = 模型概率 − 市场概率`，`EV = 模型概率 × 赔率 − 1`。
- **亚洲让球用结算表（settlement table）算 EV**（不能用二元 `p×odds−1`）。先用比分矩阵得到「让球调整后净胜球」分布，再按盘口类型结算（以 1 单位本金、赔率 odds 为基准）：
  - **整数盘**（如 -1 / 0 / +1）：净胜球优于线 → 赢，净收益 `odds−1`；等于线 → **push 退本**，净收益 0；劣于线 → 输，净收益 −1。
  - **半盘**（如 -0.5 / +0.5 / -1.5）：只有赢（`odds−1`）/ 输（−1），**无 push**。
  - **四分之一盘**（如 -0.25 / -0.75 / +0.25 / +0.75）：拆成相邻的「整数盘」与「半盘」**各下一半本金**，两半分别结算后相加；因此会出现「半赢」（一半赢、一半 push）与「半输」（一半输、一半 push）。
    - 例：`-0.25 = ½×(0) + ½×(-0.5)`；`-0.75 = ½×(-0.5) + ½×(-1)`。
  - `EV = Σ 各净胜球结果概率 × 该结果按上表的净收益`。
- **等级（公开显示，仅标签 + 含义，无金额，措辞避免喊单）**。整数盘（`1X2_90min`/`OverUnder_90min`）用 **EV + Edge 双阈值**（Edge 即原则里的安全边际）：
  - S：**强价值信号** —— EV ≥ 8% 且 Edge ≥ 4%
  - A：**价值信号** —— EV 5–8% 且 Edge ≥ 2%
  - B：**弱价值信号 / 仅观察** —— EV 3–5% 且 Edge ≥ 1%
  - C：**无价值** —— 不满足上述（EV < 3% 或 Edge 不足）
  - D：**数据异常 / 禁止参考** —— 赛前 24h/2h 内仍无主盘口 / 盘口异常 / 报价不足等真异常
  - `NO_MARKET_YET` / `ODDS_PENDING`：远期(>14 天)正常无赔率 / 14 天内暂未出盘，**均非 D**，市场对比/等级留空（见第 3 节三态）
  - 所有 EV/Edge 阈值写入 config，待回测微调。
- **亚洲让球只用 EV 定级**（AH 有 push/半赢/半输，`模型概率−市场概率` 定义不清，不用 Edge）：S EV≥8% / A 5–8% / B 3–5% / C <3%。如需边际可内部定义 `break_even_prob = 1/有效赔率` 与 `fair_odds`，但**不参与 AH 定级、可不在前端显示**。
- **降级硬规则（禁止 S/A）**：满足以下任一时，等级最高只能 B（存疑则 C/D）：
  - 用于定价的赔率快照超过新鲜度阈值（如 > 3.5 小时）
  - **该场/该市场依赖高频备源、而该备源尚未经 Task 0 确认覆盖该场**（主源 API-Football 数据新鲜且报价充足时不受此限）
  - 主盘口线发生变化但来源不明
  - 有效报价公司过少（见 5.4 异常值过滤）
- **系统不提供任何下注金额或仓位建议**（不计算、不显示凯利、不显示金额、不喊价）。
- 风控（本届最小）：缺数据降级 D；赔率异常降级；明确「无价值」优先。

## 6. 网站（移动端优先，中文，价值信号定位）

### 6.1 首页 / 比赛列表
- 顶部：免责声明条 + 最后更新时间 + 数据健康指示灯（含配额状态 + worker heartbeat）。
- 「本轮重要变化」高亮区。
- 比赛卡片（按开赛时间排序）：对阵、开赛时间、阶段、**等级徽章（S/A/B/C/D 带色）+ 含义**、最佳方向 + EV；临近开赛置顶。**不显示金额**。

### 6.2 比赛详情页（体现「分析步骤」）
1. 模型预测：三种市场类型各自概率
2. 市场概率：去水后概率 + 用了哪几家赔率 + 盘口线 + 赔率新鲜度
3. 模型优势：最佳方向、Edge、EV
4. 各模型倾向：Elo / Poisson 分别说啥（展示分歧）
5. 风险与数据状态：赔率新鲜度、哪些数据用旧缓存、配额、heartbeat
6. 最终结论：等级 + 含义 + 价值判断（价值信号 / 无价值 / 禁止参考）+ 理由（无金额、不喊单）
7. 本场变化时间线：历轮等级 / EV / 赔率变化

### 6.3 关于页
方法说明 + 免责声明 + 「本届未做回测、仅供研究、不构成投注建议」诚实说明 + 等级含义表。

### 6.4 变化检测
- 每轮结果与变化事件经 ingest API 写入 RDS，原始快照存 OSS。
- differ 对比当前轮 vs 上一轮，「重要变化」定义：
  - 推荐等级变化（如 C→S）
  - EV 变化 ≥ 阈值（如 ±3%）
  - 赔率异动 ≥ 阈值（如某方向 ≥5%）
  - 新增 / 移除比赛
- 变化事件供首页高亮区 + 详情页时间线读取。

### 6.5 视觉
简洁数据型、移动优先；等级配色 S 红 / A 橙 / B 黄 / C 灰 / D 黑；静态前端（原生 JS 或 Alpine.js）+ ECS FastAPI 提供 JSON。

## 7. 技术栈

- 采集 + 分析（macmini）：Python 3 + pandas / numpy / scipy / requests / pyyaml / python-dotenv；调度 apscheduler；**launchd 负责启动 + 保活** apscheduler 进程。禁止 macmini 睡眠（`caffeinate` / `pmset`）属于**修改系统电源状态，为可选步骤、需用户确认后手动执行，脚本默认不改系统设置**。macmini 端**不需要** sqlalchemy/psycopg（不直连 DB），只用 requests 调 ingest API。
- ECS（ingest + web）：FastAPI + sqlalchemy + psycopg（连 RDS）+ 阿里云 OSS SDK + Nginx + HTTPS。
- 存储：阿里云 RDS PostgreSQL + OSS。
- 不在本届：ML（lightgbm / xgboost / catboost / scikit-learn）、PyTorch / TensorFlow。

## 8. 目录结构（提案，建文件前会再确认）

```
足彩/
├── CLAUDE.md / AGENTS.md / RECENT_WORK.md / README.md   # 建前确认
├── .env.example            # 只放变量名
├── .gitignore              # 忽略 .env、data/raw、缓存
├── config/settings.yaml    # 权重、阈值、λ常数、刷新节奏、数据源开关
├── collectors/             # openfootball / api_football / odds_backup / elo / fifa
├── engine/                 # elo.py / poisson.py / ensemble.py / odds.py / value.py / handicap.py
├── differ/                 # 变化检测
├── client/                 # 调 ECS ingest API 的上传客户端（macmini 侧）
├── scheduler.py            # apscheduler 动态分级调度（launchd 拉起）
├── pipeline.py             # 采集→分析→变化→上传
├── server/                 # ECS 侧：ingest API + web API + 前端 + RDS 模型(sqlalchemy) + OSS
├── data/manual_overrides.yaml  # 人工录入（首发/伤病），pipeline 读取
├── data/raw/               # 本地原始快照缓存（不进 git）
└── tests/                  # pytest
```

## 9. 测试策略

- engine 为测试重点（纯数学，可确定性验证），用 TDD：
  - Poisson 归一化后矩阵概率和 = 1；已知 λ 下大小球概率对手算值；λ clamp 边界生效。
  - 去水后市场概率和 = 1；整数盘 EV 公式用已知输入验证；聚合只对同一 line 求平均。
  - **让球结算表**：-0.25 / -0.75 等四分之一盘线在构造的比分分布下 EV 与手算一致；整数盘 push 退本、半盘无 push、四分之一盘半赢/半输均正确。
  - Elo 期望胜率公式边界值。
- collectors：用保存的样例响应做解析测试（不打网络），接口/页面结构变能发现。
- differ：构造两轮假数据验证「重要变化」判定。
- server：**至少一个 Postgres 集成测试**（本地/容器 Postgres，不动生产 RDS），覆盖：ingest 鉴权失败拒绝、`run_id/snapshot_id` 重放被拒、重复上传不产生重复快照/事件（去重展示）、JSON 字段 + 时间戳 + 唯一约束 + 事务行为。纯解析逻辑可用 SQLite，但幂等/约束相关**必须在 Postgres 上测**（SQLite 与 Postgres 在唯一约束/JSON/事务行为可能不同）。

## 10. 第二步（紧跟 MVP）：最小回测基线

- 用 Football-Data.co.uk 历史（结果 + 赔率）对 Elo+Poisson+去水 流水线做**无泄漏滚动回测**。
- 指标：Log Loss、Brier（校准）、按赔率区间的 ROI、最大回撤；并看是否拿到正 CLV（用收盘赔率）。
- 目的：给「该流水线历史上是否站得住」的证据，再决定是否加强结论措辞或引入校准。
- 本届公开页先上「价值信号」，回测基线作为紧接其后的工作，不阻塞 MVP 上线。

## 11. 明确不在本届范围（以后加）

xG、伤病/新闻结构化、ML/贝叶斯/蒙特卡洛、模型动态权重、校准上线、相关性风控、熔断、晋级/冠军/半全场/比分盘、公开显示金额。代码留接口，本届不做。

## 12. Task 0（实施第一步）：数据源探测

**这是 implementation plan 的第一步，先于所有 collectors 编码**——必须先跑通，**不通过则切备源**，避免按假接口写采集逻辑：
- 注册 API-Football key，实测：2026 World Cup 的 league id、fixtures、`/odds`、可用 bookmakers、bets（market 类型）、lineups 是否可用，确认覆盖与字段。
- 确认 openfootball/worldcup.json 2026 数据结构与字段。
- 用 key 实测 odds-api.io / The Odds API / OddsPapi 是否覆盖世界杯、含哪些公司，**择一**作高频赔率备源。
- 抓一次 eloratings.net / footballratings.org，确认可解析国家队 Elo。
- 产出：一份「各源可用性 + 字段映射」结论，作为实现计划的数据契约。

## 13. 风险与诚实声明

- 单届样本极小、本届未做回测，模型未经验证，结果仅供研究与娱乐参考，不构成投注建议。
- 免费数据源（抓网页 + 免费 API）稳定性有限、可能失效或不覆盖世界杯；高频赔率备源须 Task 0 实测确认。
- API-Football 赛前赔率 3 小时更新，临场赔率新鲜度有限，网站需明确标注。
- 阿里云 ECS/RDS/OSS 部署配置较重，3 天工期偏紧。
- macmini 睡眠/重启/崩溃会中断采集，靠 launchd 保活 + 禁睡眠 + heartbeat 缓解。
- 公开站必须带醒目免责声明，强调理性投注、风险自负、遵守当地法律。
