# P9.2 中超 Club Rating 本地基线设计

日期：2026-06-22
状态：待用户 review

## 背景

P9.1 已完成俱乐部联赛 adapter 底座，中超 `csl_2026` 可以从本地 odds cache 生成联赛 snapshot，并保留英超和五大联赛后续接入的 registry 边界。

当前缺口是俱乐部强度模型仍为 `club_rating_pending`。`league_runner` 使用 1500 占位 rating，并把强信号压制到 B 或以下。这是安全的保守状态，但无法产出可信的中超强弱差异。

P9.2 的目标不是立刻宣称中超模型可用，而是建立可复用的 club rating 本地基线能力：让系统能读取本地历史赛果、按 competition 独立重放 Elo、把质量状态写入 snapshot，并在数据不足时继续保持保守降级。

## 目标

1. 新增通用 `club_rating` 本地模块，按 `competition_id` 独立维护俱乐部 rating pool。
2. 定义中超历史赛果 CSV 契约，并用测试样例验证 replay、别名映射、缺队和样本不足。
3. 让 `league_runner` 能优先读取 club rating；读取失败或样本不足时继续使用 `club_rating_pending`。
4. 将 club rating 来源、模式、样本量、缺失球队等写入 `data_quality`，不静默降级。
5. 保持引擎层纯函数；不联网、不写线上、不触发 live odds、不消耗 The Odds API quota。

## 非目标

1. 不在 P9.2 接入真实中超历史数据源。
2. 不抓取网络赛果，不新增外部 API，不安装依赖。
3. 不解除 `club_rating_pending` 的强信号压制。
4. 不调模型参数，不校准 EV/Edge 阈值。
5. 不上线、不部署、不更新 LaunchAgent、不写 ECS。

## 推荐方案

采用“框架先行，真实数据后置”的路线：

- P9.2 只建立 `club_rating` 模块、CSV 契约、样例测试和 `league_runner` 接入。
- 真实中超历史赛果以后单独确认来源、清洗规则和样本覆盖，再放入被 git 忽略的本地目录。
- 只有真实赛果覆盖足够、回测通过后，才允许把 `csl_2026.rating_policy` 从 `club_rating_pending` 调整为可用状态。

这个方案牺牲一点短期速度，但能避免把脏数据或随手样例变成看似精确的 Elo。

## 备选方案

### 方案 A：直接接真实中超历史数据

优点：最快看到真实 rating 数值。

代价：数据源、球队名、赛季升降级、补赛、主客场和缺失比分都未确认，容易污染 rating pool。若同时接入 live odds，会制造过度自信的信号。

结论：暂不采用。

### 方案 B：长期保持 1500 占位

优点：最安全，完全不引入数据质量风险。

代价：中超无法形成球队强弱差异，联赛 adapter 只能做 odds/preview 通道验证，价值信号长期不可用。

结论：只适合作为当前兜底，不适合作为下一阶段主线。

### 方案 C：P9.2 建框架和契约

优点：先把边界、质量状态和可测试接口做稳，真实中超、英超、五大联赛都能复用。

代价：P9.2 后仍不能宣布中超强信号可用，需要后续真实数据和回测。

结论：推荐采用。

## 模块设计

新增 `worldcup/club_rating.py`，职责只包括本地输入解析、rating replay、质量摘要和 `league_runner` 可用的查询接口。

建议公开数据结构：

```python
ClubResult
ClubRating
ClubRatingPool
ClubRatingLoadResult
ClubRatingQuality
```

建议公开函数：

```python
load_club_results_csv(path, competition_id) -> list[ClubResult]
replay_club_ratings(results, competition_id, initial=1500.0, k=30.0, home_adv=100.0) -> ClubRatingPool
load_club_rating_pool(cache_dir, competition_id, min_matches=20) -> ClubRatingLoadResult
```

`club_rating.py` 可以复用 `worldcup.elo_replay.update_pair()` 和 `goal_index()`，但不能复用国家队 Elo 文件、国家队 alias 或 `elo_local.py` 的 baseline 文件名。

## CSV 契约

第一版本地 CSV 文件建议命名：

```text
data/cache/club_results_csl_2026.csv
```

该路径属于本地 cache，不进 git。测试样例应写在测试代码内或测试 fixture 内，不依赖真实 cache。

字段：

```text
competition_id,season,date,home_team,away_team,home_score,away_score,neutral
```

字段规则：

- `competition_id` 必须等于当前 runner 的 competition id，例如 `csl_2026`。
- `season` 作为审计字段保留，不参与首版 replay 参数。
- `date` 使用 `YYYY-MM-DD`，排序使用该字段。
- `home_team` / `away_team` 使用原始名称，进入 replay 前通过 `club_aliases.canonicalize_club()` 映射。
- `home_score` / `away_score` 必须是非负整数；空值或非数字跳过并计入质量摘要。
- `neutral` 为 `0/1` 或 `true/false`；中超常规主客场默认 `0`。

## Rating Replay

首版规则：

- 每个 competition 独立 rating pool。
- 初始 rating 为 1500。
- 中超首版 `k=30`。
- 主场优势默认复用当前 `config/settings.yaml` 中 `elo.home_adv=100`，但作为 club rating replay 参数传入，后续可按 competition 独立配置。
- 按 `date` 升序重放已完赛比赛。
- 使用 `update_pair(home_rating, away_rating, home_score, away_score, k, neutral, home_adv)` 更新。
- 输出 rounded integer rating 给 `EloRating`，内部 replay 可保留 float。

首版不做赛季回归。赛季回归属于真实历史数据接入后的独立设计点，因为它会改变跨赛季强弱口径。

## League Runner 接入

`league_runner.build_league_snapshot_from_cache()` 增加可选 club rating 加载逻辑：

1. 从 odds cache 解析 fixtures 和 odds events。
2. 尝试读取 `club_results_<competition_id>.csv`。
3. 若 rating pool 可用，按 fixture 的 `home_canonical` / `away_canonical` 查询 rating。
4. 若两队 rating 都存在，构建 `MatchAnalysisInput` 使用真实 club rating。
5. 若文件不存在、样本不足、球队缺失或解析失败，使用 1500 占位，并保留 `club_rating_pending` 压制。

首版不修改 `CompetitionConfig.rating_policy` 的默认值。即使样例 rating 可用，P9.2 仍保持中超 strong signal cap，直到真实数据接入和回测另行确认。

## Data Quality

snapshot `data_quality` 增加 club rating 摘要：

```json
{
  "club_rating": {
    "mode": "pending|sample_replay|missing|invalid|sample_too_small",
    "source": "data/cache/club_results_csl_2026.csv",
    "competition_id": "csl_2026",
    "matches_replayed": 0,
    "teams_rated": 0,
    "missing_teams": [],
    "skipped_rows": 0,
    "sample_too_small": true
  }
}
```

规则：

- 文件不存在：`mode=missing`，继续 `club_rating_pending`。
- 文件存在但有效比赛数低于 `min_matches`：`mode=sample_too_small`，继续 `club_rating_pending`。
- 文件存在且可 replay：`mode=sample_replay`，但 P9.2 仍不解除 strong signal cap。
- fixture 中球队不在 rating pool：写入 `missing_teams`，该场继续占位。
- CSV 解析异常：`mode=invalid`，记录错误摘要，不抛到页面层造成 snapshot 全部失败，除非 odds cache 本身不可读。

页面不需要展示完整内部字段，首版只保证 public HTML 不泄漏原始错误栈、不显示下注金额、不把样例 rating 宣称为正式信号。

## 测试策略

新增或扩展测试：

1. `tests/test_club_rating.py`
   - CSV 解析成功。
   - 非当前 competition 行被跳过。
   - 非数字比分被跳过并计数。
   - replay 后胜队 rating 上升、负队下降。
   - 样本不足返回 `sample_too_small`。
   - 缺文件返回 `missing`，不异常中断。

2. `tests/test_league_runner.py`
   - 有样例 club results 时，match `elo` 不再是双 1500。
   - 缺少一队 rating 时，该场回到占位，并在 `data_quality.club_rating.missing_teams` 标记。
   - P9.2 即使存在样例 rating，也不输出 S/A 强信号。

3. 回归测试
   - 世界杯 snapshot 不受影响。
   - `tests/run_tests.py` 全量通过。
   - `git diff --check` 通过。

## 文件影响范围

预计新增：

- `worldcup/club_rating.py`
- `tests/test_club_rating.py`

预计修改：

- `worldcup/league_runner.py`
- `tests/test_league_runner.py`
- `README.md`
- `RECENT_WORK.md`

不修改：

- `worldcup/elo_local.py`
- `worldcup/elo_replay.py` 的现有国际赛语义
- `config/settings.yaml`
- 线上部署、scheduler、publish、ingest、LaunchAgent

## 验证命令

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
```

中超 runner 本地 smoke 在有样例 cache 时执行：

```bash
python3 -m worldcup.league_runner --competition csl_2026 --cache-dir data/cache --out data/cache/league_analysis_snapshot.json
```

该命令仍只读本地 odds cache 和 club results cache，不联网。

## 对抗性自审

- 根因：P9.2 解决的是俱乐部 rating 底座缺失，不解决真实中超赛果来源缺失；真实数据接入必须后续单独确认。
- 范围控制：不接 live odds、不部署、不发布、不调参，避免把 rating 底座工作扩大成完整联赛上线。
- 数据风险：样例 CSV 只能证明接口，不代表中超模型可用；必须通过 `mode=sample_replay` 和 `club_rating_pending` 保留降级。
- 口径风险：主场优势暂复用 `elo.home_adv=100`，这是工程默认，不是经中超校准的参数。
- 迁移风险：CSV 和 replay 接口按 competition 隔离，避免中超特例阻碍英超/五大联赛接入。
- 验证风险：如果没有本地 odds cache，runner smoke 会因 cache 缺失失败，这是安全结果，不应自动联网补齐。
- 安全边界：不写 `.env`、不打印 secret、不触发 The Odds API、不写 ECS、不显示下注金额或执行建议。

## 用户确认点

实现 P9.2 前需要确认：

1. 接受“先框架与样例，真实中超历史数据后置”的路线。
2. 接受 P9.2 不解除 `club_rating_pending` 强信号压制。
3. 接受首版 CSV 契约和 `data/cache/club_results_csl_2026.csv` 本地路径。
