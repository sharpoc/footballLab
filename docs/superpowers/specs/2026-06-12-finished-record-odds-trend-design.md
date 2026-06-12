# 已完赛战绩区 + 赔率走势设计（P3 展示层二件套）

> 状态：设计已经用户确认（2026-06-12）。实现按对应 plan 执行。
> 免责：仅用于研究分析，不构成投注建议；不显示资金/下注字段。

## 背景与动机

- 实测发现：The Odds API 在比赛结束数小时后会把赔率事件下架，而 openfootball 赛果录入滞后更久。结果是**完赛场次会从最新 snapshot 整场消失**（2026-06-12 揭幕战墨西哥 vs 南非已消失，snapshot 从 72 场变 71 场），已上线的"预测结果/赛后验证"展示在最新快照里永远等不到 result 与 signals 同框。
- 因此本设计把"已完赛"升级为一等公民：完赛数据在本地"定格"后随 snapshot 发布，页面不再依赖最新一轮的 odds 事件存活。
- 同时补上赔率走势展示：锚点调度（每日 → T-12h → T-6h → T-90 → T-55 → T-25）已让每场积累 6-10+ 个有意义的赔率采样点，history 归档里数据齐全，只缺提取与展示。

## 已确认的决策（用户拍板）

1. **完赛场次拆独立区块**（选项 B）：主台账只放未开赛场次；完赛沉淀到下方"已完赛战绩"区，按比赛日分组。
2. **战绩只统计 S/A**（选项 A）：closing（开球前最后一轮）等级口径，与评估链一致；B/C/D 在明细中弱化显示但不进统计。
3. **走势用迷你 SVG 折线**（选项 B）：放在展开详情里，手写 polyline 无依赖，附文本点列兜底。
4. **本地富化 snapshot**（方案一）：所有新数据由本地 Mac 在发布前算好打进 snapshot；服务器/ingest/SQLite/静态导出零改动。

## 页面结构

- **顶部"本届信号战绩"卡**（进现有摘要卡片区）：
  - `S 级：命中 x · 未中 y · 走水 z · 命中率 p%`
  - `A 级：同上`
  - 命中率 = 命中 / (命中 + 未中)，走水不进分母；样本数随行标注。
- **主台账**：结构不变；展开"分析详情"新增"赔率走势"块——该信号方向的 SVG 折线（起终点标数值、整体涨跌着色）+ 文本点列兜底（示例：`06-10 1.85 → T-12h 1.82 → T-25 1.78，累计 ↓3.8%`）。
- **去重规则**：主台账渲染时跳过已出现在 `finished` 块中的场次（按 kickoff 日期 + 双方 canonical 去重）——openfootball 追上而 odds 事件尚未下架的过渡窗口里，完赛场可能短暂同时存在于 `matches[]` 与 `finished`，以 finished 区为准，避免双重显示。
- **"已完赛战绩"区**（主台账下方）：
  - 按比赛日（北京时间）分组，沿用现有日期行模式；
  - 每场一行：比分、阶段/分组、该场 S/A closing 信号的等级章 + 命中/未中/走水徽章；
  - 行可展开：全部 closing 信号明细（B/C/D 弱化）+ 定格在 closing 的走势图；
  - 复用现有表格滚动容器与行展开交互，移动端行为不变。

## snapshot 数据契约（增量兼容）

每场 match 新增：

```json
"odds_trend": {
  "1x2":    {"home": [["<iso>", 1.85], ...], "draw": [...], "away": [...]},
  "ou_2_5": {"over": [...], "under": [...]},
  "ah_main": {"home": [["<iso>", 1.74, -1.0], ...], "away": [...]}
}
```

- `ah_main` 逐点带让球线（线轮间可能移动）；
- **压缩规则**：每个方向只记"赔率相对上一保留点发生变化"的轮次，强制保留首点与最新点，上限 30 点/方向。

snapshot 顶层新增：

```json
"finished": {
  "matches": [
    {
      "kickoff_at_utc": "...", "home_team": "...", "away_team": "...",
      "home_canonical": "...", "away_canonical": "...",
      "stage": "...", "group": "...",
      "result": {"home_score": 2, "away_score": 0},
      "closing_snapshot_at": "...",
      "closing_signals": [
        {"market_type": "...", "selection": "...", "line": null,
         "grade": "S", "odds": 1.78,
         "prediction": {"label": "命中", "detail": "..."}}
      ],
      "odds_trend": { "...同上，定格在 closing..." }
    }
  ],
  "tally": {"S": {"hit": 0, "miss": 0, "push": 0}, "A": {"hit": 0, "miss": 0, "push": 0}},
  "skipped_no_closing": 0
}
```

- `tally` 键用英文（hit/miss/push），展示层渲染中文；`prediction.label` 直接存展示文案（命中/未中/走水），与现页面 `ledger._prediction_result` 输出一致。

## 计算与数据流

- 新增纯函数模块 `worldcup/finished_record.py`：输入 history 归档目录 + results CSV，输出 `finished` 块。closing 快照选取复用 `eval_data.closing_match_entry`；命中判定复用 `ledger._prediction_result`。**不引入任何新口径。**
- 新增纯函数模块 `worldcup/odds_trend.py`：从 history 归档提取每场各方向走势并按压缩规则瘦身。
- 接线点：`refresh_runner` 在 `build_snapshot_from_cache` 之后、写盘/发布之前注入 `odds_trend`（未完赛场）与 `finished` 块；**注入失败只向 stderr 记 warning，不阻断刷新与发布**（与归档容错同款）。
- 体积预算：380KB → 约 600-700KB；决赛时完赛区攒满约再 +100KB。ingest/HMAC/SQLite 无压力。

## 边界与降级

- 页面对缺失 `finished` / `odds_trend` 键完全容忍（老快照、`local_runner` 离线构建不带这两块）。
- 完赛但无 closing 归档的场次跳过并计入 `skipped_no_closing`，不编造数据。
- 赛果真相源 = `data/local/results/wc2026_results.csv`（The Odds API scores capture 喂，及时）；openfootball 滞后不影响完赛区。
- 走势只画聚合赔率，不画单家报价；不出现资金字段；免责声明保持。
- 已知局限：淘汰赛阶段 scores 含加时/点球的 90 分钟口径风险沿用 scores-capture 计划的 6-27 回评约定，本设计不重复处理。

## 测试要点

- `finished_record`：tally 口径（含走水不进分母）、closing 选取、无 closing 跳过计数、S/A 过滤。
- `odds_trend`：变化才记点、首末点强制保留、30 点上限、AH 线变动逐点记线。
- `ledger_html`：战绩卡渲染、完赛区分组/徽章、sparkline SVG 输出、缺键不渲染、移动端滚动容器。
- `refresh_runner`：注入接线 + 注入失败不阻断。
- 浏览器 QA：桌面 + 390px 移动视口。

## 上线路径

实现合入本机 `main` 并本地验证后：push + ECS 部署一波（单独确认），顺带把此前欠的"更新规则"卡片新文案带上线。

## 范围外

- 不做跨届/历史赛事战绩；只覆盖 2026 本届。
- 不做单家 bookmaker 走势、不做 line movement 信号化（另案：赛后回测立项）。
- 不改模型、信号分级、调度、评估链。
- 不在服务器端做任何跨快照计算。
