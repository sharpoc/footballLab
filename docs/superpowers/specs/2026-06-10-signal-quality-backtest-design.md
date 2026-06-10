# 信号质量防抖与最小回测设计

- 日期：2026-06-10
- 状态：设计已确认，待用户审阅 spec
- 范围：2026 世界杯研究台账的信号可信度增强与后续回测基线设计
- 定位：研究/分析工具增强，不构成投注建议，不显示下注金额

## 背景

当前项目已经完成 Elo + Poisson + 赔率去水 + EV / Edge / 等级状态，并已上线公开研究台账。近期已补充赔率新鲜度、兜底缓存降级、东道主优势、强弱悬殊平局下限和冷门保护。

当前最大短板不是算法数量不足，而是：

1. 缺少历史回测与校准证据，无法证明当前参数和阈值是否可靠。
2. Elo 与 Poisson 可能在个别比赛上明显分歧，但现有信号没有把分歧当作风险。
3. 多家 bookmaker 报价可能分裂，平均赔率可能掩盖市场不稳定。
4. 伤病、首发、轮换、天气、战意等临场信息对国家队比赛重要，但自动化结构化成本高，当前不应急着接新闻或 ML。

因此本设计选择先增强信号质量控制，再搭最小回测框架。复杂模型和自动新闻源留到回测证据充分以后再评估。

## 目标

第一阶段目标：

1. 新增 `model_disagreement` 降级条件：Elo 与 Poisson 明显分歧时，S/A 信号最高压到 B。
2. 新增 `market_dispersion` 降级条件：同一盘口下 bookmaker 报价离散过大时，S/A 信号最高压到 B。
3. 保持现有纯函数引擎边界，不联网、不连接数据库、不改变线上发布流程。
4. 公开页继续只解释研究价值信号，不引入下注金额、仓位、凯利或喊单措辞。

第二阶段目标：

1. 搭建最小回测框架，用历史比赛结果与历史赔率评估当前模型。
2. 输出 Brier Score、Log Loss、按赔率区间和 EV 分层的表现摘要。
3. 为后续调整 `mu_total`、Elo/Poisson 权重、EV/Edge 阈值和降级规则提供证据。

## 非目标

- 不引入机器学习、XGBoost、神经网络或动态模型权重。
- 不自动抓取伤病新闻、首发新闻或社交媒体。
- 不新增真实外部 API 调用。
- 不触发 live refresh，不消耗 The Odds API 额度。
- 不改线上调度、ingest、Nginx、systemd 或云资源。
- 不显示下注金额、仓位、凯利或任何执行建议。
- 不扩展冠军盘、晋级盘、比分盘、半全场等市场。

## 推荐方案

采用两阶段路线：

1. 先做信号质量防抖层。
2. 再做最小回测框架。

推荐原因：

- `model_disagreement` 和 `market_dispersion` 都能基于现有 snapshot 输入完成，改动小、风险低、易测试。
- 这两个条件只会保守降级，不会制造新的强信号。
- 回测框架是后续所有参数优化和复杂算法的前置证据。
- 当前世界杯即将开赛，优先提高信号解释可信度，比堆模型更稳。

## 方案对比

### 方案 A：只加质量防抖

优点是最快上线，能马上减少过强信号。缺点是仍然没有历史验证，参数阈值只能凭经验设定。

适合用于世界杯开赛前的紧急加固，但不应作为长期终点。

### 方案 B：质量防抖 + 最小回测框架

这是推荐方案。先用低风险降级规则提升保守性，再用回测框架验证当前模型和阈值。

优点是短期和长期都受益；缺点是工作量比只加防抖多，但仍比接新数据源或 ML 小得多。

### 方案 C：直接引入复杂模型或新闻源

本阶段不采用。复杂模型需要稳定历史特征和严格回测；新闻源结构化容易引入噪声。缺少验证时，这类改动可能让公开信号看起来更丰富，但实际可信度下降。

## 第一阶段：信号质量防抖

### `model_disagreement`

用途：识别 Elo 和 Poisson 对同一场 1X2 判断明显不一致的情况。

建议配置：

```yaml
quality:
  disagreement_prob_delta: 0.12
  disagreement_cap_grades: ["S", "A"]
```

触发条件建议：

1. 对同一 selection，`abs(elo_prob - poisson_prob) >= disagreement_prob_delta`。
2. 或 Elo 的最高概率方向与 Poisson 的最高概率方向不同。

触发后行为：

- 原始等级为 S/A 时，最高压到 B。
- 追加 reason：`model_disagreement`。
- 不改变模型概率、市场概率、EV 或 Edge 原始值。

适用范围：

- 先只用于 `1X2_90min`。
- 大小球和亚洲让球当前主要来自 Poisson，没有 Elo 对照，不强行套用。

### `market_dispersion`

用途：识别同一盘口下不同 bookmaker 报价分裂明显的情况。

建议配置：

```yaml
quality:
  odds_dispersion_ratio_max: 1.18
  odds_dispersion_cap_grades: ["S", "A"]
```

触发条件建议：

- 对同一 `market_type + line + selection`，离群过滤后的最大赔率 / 最小赔率超过阈值。
- 有效报价数量不足时仍沿用既有 `few_books` 规则，不重复制造新原因。

触发后行为：

- 原始等级为 S/A 时，最高压到 B。
- 追加 reason：`market_dispersion`。
- 保留现有聚合均值和去水市场概率，避免破坏 snapshot 契约。

适用范围：

- `1X2_90min`
- `OverUnder_90min`
- `AsianHandicap_90min`

## 第二阶段：最小回测框架

### 输入

最小输入数据：

```text
match_id
kickoff_at_utc
home_team
away_team
home_score
away_score
home_elo_before
away_elo_before
closing_odds_1x2
closing_odds_ou_2_5
closing_odds_ah_main_line
```

如果部分市场缺失，回测框架应跳过对应市场，不把缺失当作失败。

### 输出指标

1. `Brier Score`：衡量概率校准。
2. `Log Loss`：惩罚过度自信的错误概率。
3. 按赔率区间分层的命中与偏差。
4. 按 EV 分层的结果表现。
5. 可选：CLV 观察，如果有开盘和收盘赔率。

### 边界

回测框架只读本地历史数据文件，不联网。历史数据文件来源后续单独确认，不能把不完整或不可追溯的数据混入正式结论。

## 数据流

第一阶段：

```text
Fixture + Elo + OddsQuote
  -> analyze_match_input
  -> elo_1x2 / poisson_1x2 / market aggregates
  -> generate_value_signals
  -> quality context
  -> grade_signal
  -> Signal reasons 增加 model_disagreement / market_dispersion
```

第二阶段：

```text
historical matches + historical odds
  -> replay model probability
  -> compare actual result
  -> compute Brier / Log Loss / EV buckets
  -> local report JSON or markdown
```

## 组件设计

### 配置

新增 `quality` 配置块，避免把信号质量条件塞进 `value` 阈值中。

建议字段：

```yaml
quality:
  disagreement_prob_delta: 0.12
  odds_dispersion_ratio_max: 1.18
```

### 赔率聚合

`worldcup.engine.odds.aggregate` 可在现有返回值中补充离散度摘要：

```text
min_odds
max_odds
dispersion_ratio
```

保持现有 `odds` 和 `n_books` 字段不变。

### 信号上下文

`worldcup.pipeline._signal_ctx` 继续负责构造降级所需上下文。新增字段：

```text
model_disagreement: bool
odds_dispersion_ratio: float | None
```

### 等级逻辑

`worldcup.engine.value.grade_signal` 继续负责最终定级。新增降级原因：

```text
model_disagreement
market_dispersion
```

降级只影响等级，不修改原始计算值。

### 回测模块

后续新增独立模块，建议命名：

```text
worldcup/backtest.py
```

它不参与线上 pipeline，不被 scheduled publish 调用。首版只提供本地 CLI 或函数入口。

## 错误处理

- Elo 或 Poisson 概率缺失时，不触发 `model_disagreement`，沿用现有缺数据逻辑。
- 报价数量不足时优先使用 `few_books`，不额外触发 `market_dispersion`。
- 离散度计算遇到无效赔率时沿用现有过滤和 D 级逻辑。
- 回测输入缺字段时返回明确错误，不能静默补假数据。
- 回测样本过小时报告 `sample_too_small`，不能给出强结论。

## 测试计划

第一阶段测试：

1. Elo 与 Poisson 最佳方向不同，S 信号压到 B，并记录 `model_disagreement`。
2. Elo 与 Poisson 同方向但概率差超过阈值，A 信号压到 B。
3. Elo 与 Poisson 差异未超阈值，不触发降级。
4. bookmaker 离散度超过阈值，S/A 信号压到 B，并记录 `market_dispersion`。
5. 报价不足时仍只触发或优先体现 `few_books`。
6. AH 市场可触发 `market_dispersion`，但不触发 `model_disagreement`。
7. 现有 `stale_odds`、`unconfirmed_backup`、`longshot_uncertainty` 行为不回退。

第二阶段测试：

1. Brier Score 计算正确。
2. Log Loss 对极端概率做数值保护。
3. 1X2 结果映射正确。
4. 缺失市场跳过对应指标。
5. 小样本报告明确标记。

## 验收标准

第一阶段完成后：

1. 本地测试通过。
2. Snapshot 中强信号可因为模型分歧或市场离散被压级。
3. 页面详情能显示新增 reason 的中文解释。
4. 不改变现有 API 路由、ingest 签名、SQLite schema 或发布调度。

第二阶段完成后：

1. 可以对本地历史样例生成回测摘要。
2. 指标输出不包含资金建议。
3. 回测报告明确样本范围、数据来源和样本量限制。

## 实施顺序

1. 第一阶段：测试先行实现 `model_disagreement`。
2. 第一阶段：测试先行实现 `market_dispersion`。
3. 第一阶段：补页面 reason 文案和本地验证。
4. 第二阶段：单独确认历史数据来源后，再实现最小回测框架。

本设计确认后，下一步应先写第一阶段实现计划；回测框架作为第二阶段单独计划，不和防抖实现混在同一批改动里。
