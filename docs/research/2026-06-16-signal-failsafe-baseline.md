# Signal fail-safe baseline（2026-06-16）

- baseline_name: `signal-failsafe-v1`
- code_commit: `71c4d68`
- deployment_doc_commit: `893110e`
- publish_run: `20260616T025759Z-local`
- snapshot_id: `7f095fb7017c0acf588c017d11406f81ddabaccb2c81f509fd1e75f4090e0098`
- snapshot_at: `2026-06-16T02:57:59.196979+00:00`
- matches: `57`
- 免责：仅用于研究分析，不构成投注建议

## 目的

本 baseline 标记 P0 signal fail-safe 上线后的第一份稳定口径。之后所有信号分布、日报、回测和复盘应明确区分：

- `pre-failsafe`
- `post-failsafe`

二者的 S/A/B/C 分布不可直接横向比较，因为 P0 修复改变的是信号评级口径，不是模型参数、赔率源或刷新策略。

## P0 修复范围

本次修复是口径修复 / fail-safe 补丁：

- 不改 `mu_total`
- 不改 `mu_dr_slope`
- 不启用 `dc_rho`
- 不改 S/A 阈值
- 不改赔率源
- 不放宽 odds age / min books / dispersion
- 不改变 refresh、publish、quota 或部署策略

核心约束：

- 同场 OU 市场参与反推 total 时，OU 信号不允许被包装成 S/A 强信号。
- AH 尚无 market edge / fair-line delta / line consensus 闭环时，AH EV-only 信号不允许被评级为 S/A。

## 上线后不变量

受控 snapshot 发布后，远端 latest snapshot 满足：

| invariant | value |
|---|---:|
| `same_market_total_anchor=true` 的 OU S/A 数量 | `0` |
| `ah_market_validated=false` 的 AH S/A 数量 | `0` |

等级分布：

| grade | count |
|---|---:|
| S | 3 |
| A | 4 |
| B | 95 |
| C | 297 |

fail-safe reason 计数：

| reason | count |
|---|---:|
| `market_informed_total` | 114 |
| `ah_market_edge_missing` | 114 |

## 发布与验证口径

本次发布是受控本地缓存重算：

- 未触发 live refresh
- 未调用 The Odds API
- 未消耗 quota
- 本地 `data/cache/analysis_snapshot.json` 与 `data/local/history/snapshot_20260616T025759Z-local.json` 已同步
- ECS ingest 返回 stored
- 公网 `/healthz`、`/api/matches`、`/api/finished`、首页和 `/preview` 均返回 200

代码部署到 ECS release：

```text
/opt/worldcup/releases/71c4d68
```

`origin/main` 中用于记录部署结果的文档 commit 为：

```text
893110e docs: record signal failsafe deployment
```

## ops_check warning 分类

P0.5 只读复查中，`python3 -m worldcup.ops_check` 返回：

```json
{"ok": true, "errors": 0, "warnings": 3}
```

warning 分类：

| warning | classification | conclusion |
|---|---|---|
| 本地 `scheduled-publish.err.log` 命中历史 traceback | `data_quality_warning` | log mtime 为 `2026-06-15 12:09:38`，早于 P0 部署；包含一次 HTTPS publish TLS EOF 和多次 `decimal odds must be > 1.0`。不阻塞当前 baseline，但 `decimal odds` 应作为 P0.5 小修复候选。 |
| 远端 nginx `access.log` 5xx/upstream 计数 | `expected_warning` | 主要是公网扫描探针访问同机其他 server/upstream，不是足球站核心 API。 |
| 远端 nginx `error.log` 5xx/upstream 计数 | `expected_warning` | 与 access log 同源，server/upstream 指向 `api.celab.xin` / `127.0.0.1:8020`，不是 `football.celab.xin` / 8788。 |

## 后续观察口径

下一次 scheduled publish 后至少检查：

- `source_errors` 是否仍为 0
- `stale_sources` 是否为空
- quota 是否按 scheduler due 预期变化
- matches 数量是否合理
- `same_market_total_anchor=true` 的 OU S/A 是否仍为 0
- `ah_market_validated=false` 的 AH S/A 是否仍为 0
- fail-safe reasons 是否仍写入
- 公网 `/healthz`、`/api/matches`、`/api/finished` 是否正常

## 暂不进入模型优化

本 baseline 后的下一阶段应先做工程卫生和概率族 schema 设计，不直接改模型参数。

暂不采纳：

- `mu_total=2.2`
- `mu_dr_slope=0.0015`
- `dc_rho=-0.15`
- 调整 S/A 阈值
- 放宽 odds age / min books / dispersion
- 根据当前小样本赛果调参
