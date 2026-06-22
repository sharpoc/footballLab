# P9.3 中超历史赛果来源与清洗设计

日期：2026-06-22
状态：待用户 review

## 背景

P9.1 已完成俱乐部联赛 adapter 本地 MVP，中超 `csl_2026` 可以从本地 odds cache 生成研究 snapshot。

P9.2 已新增本地 `club_rating` 基线能力：系统可以读取 `data/cache/club_results_<competition_id>.csv`，按 competition 独立重放 Elo-style rating，并将缺文件、样本不足、CSV 无效和 fixture 缺 rating 写入 `data_quality.club_rating`。

当前缺口是：真实中超历史赛果来源、清洗规则和质量门槛尚未确认。若直接把未验证数据放入 `club_rating`，会把队名映射错误、补赛日期、比分口径或单源脏数据扩散到 rating pool，制造看似精确的强弱差异。

P9.3 的目标是先设计真实赛果数据链路，不直接接入生产 runner，不解除 `club_rating_pending`。

## 已确认方向

- 覆盖范围：`2023-2026` 中超联赛赛果。
- 数据策略：双源交叉校验。
- Phase 0：允许只读公开源探测，但不接生产链路。
- 源优先级：开源结构化主源 + 官方/权威校验源。
- 质量门槛：定义解除 `club_rating_pending` 的条件，但 P9.3 不解除。

## 目标

1. 定义 2023-2026 中超历史赛果的数据源探测方案。
2. 设计开源结构化主源与官方/权威校验源的双源校验流程。
3. 明确标准化赛果字段、清洗规则、队名 alias、异常行和冲突行处理方式。
4. 明确进入 `club_rating` replay 前的质量门槛。
5. 保持所有探测和清洗输出在本地 ignored 目录，不写线上，不改变模型参数。

## 非目标

1. 不在本设计阶段实现 scraper、collector 或 runner。
2. 不抓取或保存真实数据。
3. 不使用 The Odds API、API key、HMAC secret、Cookie、账号登录态或付费数据源。
4. 不写 `.env`，不修改 LaunchAgent，不发布 snapshot，不写 ECS，不部署。
5. 不修改 `csl_2026.rating_policy`，不解除强信号压制。
6. 不输出下注金额、执行建议、追损、重注或串关建议。

## 推荐方案

采用“开源结构化主源 + 官方/权威校验源 + 本地质量门槛”的方案。

主源优先选择可下载、可重复解析、字段稳定的公开结构化数据，例如 CSV、JSON、表格或可版本化仓库。校验源优先选择官方或权威页面，用于抽样或全量核对比赛日期、主队、客队、比分和轮次。

不采用“单源直接进入 replay”，因为中超队名变化、升降级、补赛和第三方数据口径差异会直接污染 rating pool。

不采用“官方页面直接作为主源”，因为官方页面可能反爬、结构变动、分页复杂或缺少批量下载能力。官方/权威源更适合作为校验与冲突仲裁依据。

## 数据源分层

### 主源：开源结构化源

Phase 0 只读探测时，主源候选应满足：

- 可公开访问，不需要登录、密钥、Cookie 或付费。
- 可按赛季获取 2023、2024、2025、2026 中超赛果。
- 至少包含比赛日期、主队、客队、主队进球、客队进球。
- 最好包含赛季、轮次、比赛状态、比赛 ID 或来源 URL。
- 可保存原始样例到 `data/probe/` 或 `data/cache/`，且不进 git。

主源探测输出只记录来源摘要，不把真实大数据提交到仓库：

```text
source_id
source_type
license_or_terms_summary
season_coverage
fields_available
sample_path
row_count_by_season
known_limitations
```

### 校验源：官方/权威源

校验源候选应满足：

- 可公开访问，不需要登录、密钥、Cookie 或付费。
- 可核对 2023-2026 至少关键字段：日期、主客队、比分。
- 对补赛、延期、取消、弃赛等异常有较高可信度。
- 如果无法全量解析，也可以先做抽样校验或冲突仲裁。

校验源不要求直接进入 replay。它的首要职责是发现主源错误。

## Phase 0 只读探测

Phase 0 只允许做公开源只读探测：

1. 枚举候选主源与校验源。
2. 对每个候选源保存极小样例或字段摘要到 ignored 路径。
3. 生成本地探测报告，说明覆盖范围、字段、可解析性和风险。
4. 不修改 `worldcup/` 生产链路。
5. 不把真实样例数据提交到 git。

建议输出：

```text
data/local/diagnostics/csl_results_source_probe.json
data/probe/csl_results_source_<source_id>_<season>_sample.*
```

`data/probe/` 和 `data/local/` 已按项目规则不进 git。若后续需要保存小型脱敏 fixture，应另行确认后放入测试代码或专用 fixture。

## 标准化赛果契约

P9.3 不改变 P9.2 的最终 replay CSV 契约，但会定义清洗中间层。最终进入 `club_rating` 的 CSV 仍为：

```text
competition_id,season,date,home_team,away_team,home_score,away_score,neutral
```

建议清洗中间层增加审计字段：

```text
competition_id
season
round
date
kickoff_time_local
home_team_raw
away_team_raw
home_team
away_team
home_canonical
away_canonical
home_score
away_score
neutral
status
source_primary_id
source_primary_url
source_check_id
source_check_url
source_agreement
quality_flags
```

字段规则：

- `competition_id` 固定为 `csl_2026` 对应的历史 competition pool；若后续按赛季拆分，可在 implementation plan 中另行命名。
- `season` 为 `2023`、`2024`、`2025`、`2026`。
- `date` 必须严格为 `YYYY-MM-DD`。
- `home_score` / `away_score` 必须为非负整数。
- `neutral` 默认 `0`，只有明确中立场或赛会制才标 `1`。
- `status` 只允许已完赛进入 replay；未赛、延期、取消、腰斩、判负等必须单独标记。
- `home_team` / `away_team` 是清洗后的展示名，`home_canonical` / `away_canonical` 是 replay 使用的 key。

## 队名与俱乐部身份

中超 2023-2026 可能出现队名变更、升降级、俱乐部解散或迁移。清洗规则必须显式区分：

- 同一俱乐部的名称变化。
- 不同俱乐部但名称相似。
- 升降级导致某赛季不存在的球队。
- 英文名、中文名、简称和数据源别名。

队名映射应扩展现有 `worldcup.collectors.club_aliases` 的 competition-scoped 边界，但 P9.3 只设计，不直接修改 alias 文件。

后续实现时，未匹配球队不得静默 slugify 后进入 replay；应进入 `quality_flags` 和 probe 报告，等人工确认 alias。

## 双源校验规则

每场比赛用规范化 key 对齐：

```text
season + date + home_canonical + away_canonical
```

若日期可能因时区或补赛记录不同产生偏差，可在 probe 报告中另列候选匹配，但不得自动合并到 replay。

校验结果：

```text
match_agree
score_mismatch
date_mismatch
home_away_mismatch
team_alias_unmatched
missing_in_primary
missing_in_check
status_not_finished
duplicate_candidate
manual_review_required
```

默认规则：

- 双源日期、主客队和比分一致：可进入 clean candidate。
- 比分冲突：阻断该场进入 replay，进入人工 review。
- 主客队冲突：阻断该场进入 replay。
- 日期轻微差异：不自动修正，先标记 `date_mismatch`。
- 主源有、校验源缺失：进入 degraded candidate，不计入解除 pending 的一致率通过项。
- 校验源有、主源缺失：进入 missing primary 报告，不进入 replay。

## 质量门槛

P9.3 定义门槛，但不解除 `club_rating_pending`。

建议后续解除 pending 前至少满足：

```text
season_coverage: 2023, 2024, 2025, 2026 已完成赛果可枚举
primary_required_fields_coverage >= 99%
dual_source_score_agreement >= 99%
date_home_away_agreement >= 98%
team_alias_unmatched_rate == 0%
manual_review_required_rate <= 1%
valid_finished_matches >= 300
per_current_fixture_team_min_matches >= 10
no_unresolved_score_mismatch
no_unresolved_home_away_mismatch
```

说明：

- `valid_finished_matches >= 300` 是首版建议值，implementation plan 可根据实际赛季轮次调整。
- `per_current_fixture_team_min_matches >= 10` 是避免当前待分析球队完全无历史样本。
- 质量门槛通过只代表“可以进入回测”，不代表直接释放强信号。

解除 `club_rating_pending` 仍需后续回测：

- 与 1500 baseline 比较。
- 与市场基准比较。
- 检查小样本和 S/A 信号样本数。
- 检查主场优势参数是否适合中超。
- 明确没有把样例数据误当真实生产数据。

## 输出与数据质量

后续实现应输出本地诊断 JSON：

```json
{
  "competition_id": "csl_2026",
  "coverage": {
    "seasons": ["2023", "2024", "2025", "2026"],
    "primary_rows": 0,
    "check_rows": 0,
    "valid_finished_matches": 0
  },
  "quality": {
    "primary_required_fields_coverage": 0.0,
    "dual_source_score_agreement": 0.0,
    "date_home_away_agreement": 0.0,
    "team_alias_unmatched": [],
    "score_mismatches": [],
    "manual_review_required": []
  },
  "pending_gate": {
    "can_enter_replay": false,
    "can_lift_club_rating_pending": false,
    "reasons": []
  }
}
```

`can_lift_club_rating_pending` 在 P9.3 必须始终为 `false`，因为 P9.3 只负责数据链路设计与探测。

## 文件影响范围

P9.3 spec 后续 implementation plan 预计可能涉及：

- 新增 `worldcup/collectors/csl_results.py`
- 新增 `tests/collectors/test_csl_results.py`
- 新增 `worldcup/csl_results_probe.py` 或 `worldcup/sources/csl_results.py`
- 新增 `tests/test_csl_results_probe.py`
- 可能扩展 `worldcup/collectors/club_aliases.py`
- 不修改 `worldcup/club_rating.py` 的 replay 语义，除非清洗契约发现必须补充质量字段。
- 不修改 `worldcup/league_runner.py` 的强信号压制。

真实样例与诊断默认写入：

```text
data/probe/
data/local/diagnostics/
data/cache/
```

这些路径不进 git。

## 验证策略

后续实现计划应包含：

1. 源探测 parser 单元测试：用极小内联样例验证字段解析。
2. 双源对齐测试：一致、比分冲突、日期冲突、主客队冲突、缺失行。
3. alias 测试：同一俱乐部别名映射、未知队名阻断。
4. 质量门槛测试：通过、未达覆盖率、未达一致率、未匹配球队。
5. dry-run CLI 测试：默认只写本地诊断，不联网或只访问显式确认的公开源。
6. 安全测试：不打印 secret，不读取 `.env`，不写线上。

## 对抗性自审

- 根因：P9.3 解决真实赛果来源与清洗质量，不解决模型参数校准，也不解除 strong signal cap。
- 范围控制：本设计不实现 scraper，不接生产 runner，不抓真实数据。
- 数据风险：公开源可能结构变化、缺赛季、比分修正滞后或许可不明；Phase 0 必须记录限制，不能默认可信。
- 口径风险：补赛、延期、弃赛、判负和中立场会影响 rating replay，必须显式标记。
- 队名风险：俱乐部改名和升降级不能靠 slugify 静默处理。
- 验证风险：双源一致不等于绝对正确，但能降低单源脏数据风险。
- 生产边界：即使质量门槛通过，也只能进入回测；解除 `club_rating_pending` 需后续单独确认。
- 安全边界：不使用密钥、不消耗 The Odds API quota、不写 `.env`、不部署、不发布线上 snapshot。

## 用户确认点

进入 implementation plan 前需要用户确认：

1. 接受 P9.3 只做数据源探测与清洗链路，不接生产 runner。
2. 接受 2023-2026 作为首版覆盖范围。
3. 接受双源冲突阻断进入 replay，优先保证数据质量。
4. 接受 P9.3 只定义解除 pending 门槛，但不解除 `club_rating_pending`。
