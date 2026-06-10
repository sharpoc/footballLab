# 国际比赛历史回测基线（2026-06-10）

- 数据来源：martj42/international_results 本地样例（`data/probe/intl_results_martj42.csv`，更新至 2026-06-08）
- 赛前 Elo：本仓库 `worldcup.elo_replay` 按 eloratings 公开公式从 1872 年重放推演（非官方历史评分）
- 样本范围：2010-01-01 起、两队均可映射 Elo alias 的国家队比赛，共 14901 场
- 局限：无历史收盘赔率，本报告只评估模型概率质量（vs uniform 基线），不评估 EV/Edge 阈值
- 免责：仅用于研究分析，不构成投注建议

## Elo replay 与官方榜对照

命令：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.elo_replay
```

输出：

```json
{
  "matches_replayed": 49378,
  "teams_rated": 336,
  "teams_mapped_to_codes": 236,
  "official_top": 10,
  "replay_pool": 30,
  "overlap_hits": 10,
  "detail": [
    {
      "code": "ES",
      "official_rank": 1,
      "official_rating": 2155,
      "replay_rank": 1,
      "replay_rating": 2219.0
    },
    {
      "code": "AR",
      "official_rank": 2,
      "official_rating": 2114,
      "replay_rank": 2,
      "replay_rating": 2188.8
    },
    {
      "code": "FR",
      "official_rank": 3,
      "official_rating": 2062,
      "replay_rank": 3,
      "replay_rating": 2125.1
    },
    {
      "code": "EN",
      "official_rank": 4,
      "official_rating": 2021,
      "replay_rank": 4,
      "replay_rating": 2086.4
    },
    {
      "code": "BR",
      "official_rank": 5,
      "official_rating": 1991,
      "replay_rank": 5,
      "replay_rating": 2069.1
    },
    {
      "code": "PT",
      "official_rank": 6,
      "official_rating": 1986,
      "replay_rank": 7,
      "replay_rating": 2042.7
    },
    {
      "code": "CO",
      "official_rank": 7,
      "official_rating": 1982,
      "replay_rank": 6,
      "replay_rating": 2064.3
    },
    {
      "code": "NL",
      "official_rank": 8,
      "official_rating": 1944,
      "replay_rank": 9,
      "replay_rating": 2010.8
    },
    {
      "code": "EC",
      "official_rank": 9,
      "official_rating": 1938,
      "replay_rank": 8,
      "replay_rating": 2028.2
    },
    {
      "code": "DE",
      "official_rank": 10,
      "official_rating": 1932,
      "replay_rank": 10,
      "replay_rating": 2004.7
    }
  ]
}
```

## 1X2 模型质量基线

命令：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv data/local/backtest/intl_history.csv --out data/local/backtest/intl_report.json
```

输出：

```json
{
  "n_matches": 14901,
  "n_1x2": 0,
  "n_ou": 0,
  "n_ah": 0,
  "min_sample": 200,
  "sample_too_small": false
}
```

指标摘录命令输出：

```json
{
  "sample": {
    "n_matches": 14901,
    "n_1x2": 0,
    "n_ou": 0,
    "n_ah": 0,
    "min_sample": 200,
    "sample_too_small": false
  },
  "markets_1x2": {
    "model": {
      "n": 14901,
      "brier": 0.5252676489648002,
      "log_loss": 0.8933331617985814
    },
    "model_matched": {
      "n": 0,
      "brier": null,
      "log_loss": null
    },
    "market": {
      "n": 0,
      "brier": null,
      "log_loss": null
    },
    "uniform": {
      "n": 14901,
      "brier": 0.6666666666666667,
      "log_loss": 1.0986122886681098
    }
  },
  "markets_ou_2_5": {
    "model": {
      "n": 14901,
      "brier": 0.4980964602285635,
      "log_loss": 0.6912429989821982
    },
    "model_matched": {
      "n": 0,
      "brier": null,
      "log_loss": null
    },
    "market": {
      "n": 0,
      "brier": null,
      "log_loss": null
    },
    "uniform": {
      "n": 14901,
      "brier": 0.5,
      "log_loss": 0.6931471805599453
    }
  }
}
```

`calibration_1x2` 命令输出：

```json
[
  {
    "range": [
      0.0,
      0.1
    ],
    "n": 2930,
    "p_mean": 0.053817572939176425,
    "hit_rate": 0.039590443686006824
  },
  {
    "range": [
      0.1,
      0.2
    ],
    "n": 8095,
    "p_mean": 0.15436914109969035,
    "hit_rate": 0.12291537986411365
  },
  {
    "range": [
      0.2,
      0.3
    ],
    "n": 15626,
    "p_mean": 0.24630257043221204,
    "hit_rate": 0.25636759247408164
  },
  {
    "range": [
      0.3,
      0.4
    ],
    "n": 4666,
    "p_mean": 0.3482262854808013,
    "hit_rate": 0.33090441491641664
  },
  {
    "range": [
      0.4,
      0.5
    ],
    "n": 3955,
    "p_mean": 0.44901466531741674,
    "hit_rate": 0.43691529709228827
  },
  {
    "range": [
      0.5,
      0.6
    ],
    "n": 3410,
    "p_mean": 0.5493447464033605,
    "hit_rate": 0.5615835777126099
  },
  {
    "range": [
      0.6,
      0.7
    ],
    "n": 2747,
    "p_mean": 0.648124180879002,
    "hit_rate": 0.6763742264288315
  },
  {
    "range": [
      0.7,
      0.8
    ],
    "n": 1863,
    "p_mean": 0.7455476531960796,
    "hit_rate": 0.7836822329575953
  },
  {
    "range": [
      0.8,
      0.9
    ],
    "n": 1411,
    "p_mean": 0.8516495472106779,
    "hit_rate": 0.9064493267186393
  }
]
```

按 outcome 聚合的补充命令输出：

```json
{
  "home": {
    "n": 14901,
    "p_mean": 0.4682465819074596,
    "hit_rate": 0.47808871887792764,
    "hit_minus_p": 0.00984213697046804
  },
  "draw": {
    "n": 14901,
    "p_mean": 0.22134889858245135,
    "hit_rate": 0.23508489363129992,
    "hit_minus_p": 0.013735995048848565
  },
  "away": {
    "n": 14901,
    "p_mean": 0.3104045195100888,
    "hit_rate": 0.28682638749077244,
    "hit_minus_p": -0.023578132019316356
  }
}
```

## 总进球与 |dr| 的关系（mu 模型证据）

`totals_by_abs_dr` 命令输出：

```json
[
  {
    "range": [
      0.0,
      100.0
    ],
    "n": 4434,
    "mean_total_goals": 2.3249887235002253,
    "mean_mu_used": 2.6000000000002115
  },
  {
    "range": [
      100.0,
      200.0
    ],
    "n": 3702,
    "mean_total_goals": 2.519178822258239,
    "mean_mu_used": 2.6000000000001817
  },
  {
    "range": [
      200.0,
      300.0
    ],
    "n": 2779,
    "mean_total_goals": 2.5901403382511696,
    "mean_mu_used": 2.6000000000001213
  },
  {
    "range": [
      300.0,
      10000.0
    ],
    "n": 3986,
    "mean_total_goals": 3.2458605117912693,
    "mean_mu_used": 2.6000000000001946
  }
]
```

客观观察：样本内 `mean_total_goals` 随 `|dr|` 分桶整体上升，而当前无 OU 市场时 `mean_mu_used` 固定为配置先验约 2.6。

## dc_rho 扫描

命令：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.backtest --csv data/local/backtest/intl_history.csv --out data/local/backtest/intl_report.json --sweep "poisson.dc_rho=0,-0.05,-0.1,-0.15"
```

输出：

```json
{
  "sweep": "poisson.dc_rho",
  "results": [
    {
      "value": "0",
      "1x2_model": {
        "n": 14901,
        "brier": 0.5252676489648002,
        "log_loss": 0.8933331617985814
      },
      "ou_model": {
        "n": 14901,
        "brier": 0.4980964602285635,
        "log_loss": 0.6912429989821982
      }
    },
    {
      "value": "-0.05",
      "1x2_model": {
        "n": 14901,
        "brier": 0.5249887737483605,
        "log_loss": 0.8926425682690249
      },
      "ou_model": {
        "n": 14901,
        "brier": 0.4980964602285635,
        "log_loss": 0.6912429989821982
      }
    },
    {
      "value": "-0.1",
      "1x2_model": {
        "n": 14901,
        "brier": 0.5247975560301094,
        "log_loss": 0.8921275757415663
      },
      "ou_model": {
        "n": 14901,
        "brier": 0.4980964602285635,
        "log_loss": 0.6912429989821982
      }
    },
    {
      "value": "-0.15",
      "1x2_model": {
        "n": 14901,
        "brier": 0.5246939958100466,
        "log_loss": 0.891783627002631
      },
      "ou_model": {
        "n": 14901,
        "brier": 0.4980964602285635,
        "log_loss": 0.6912429989821982
      }
    }
  ]
}
```

扫描总结命令输出：

```json
{
  "rows": [
    {
      "dc_rho": "0",
      "1x2_brier": 0.5252676489648002,
      "1x2_log_loss": 0.8933331617985814,
      "ou_brier": 0.4980964602285635,
      "ou_log_loss": 0.6912429989821982
    },
    {
      "dc_rho": "-0.05",
      "1x2_brier": 0.5249887737483605,
      "1x2_log_loss": 0.8926425682690249,
      "ou_brier": 0.4980964602285635,
      "ou_log_loss": 0.6912429989821982
    },
    {
      "dc_rho": "-0.1",
      "1x2_brier": 0.5247975560301094,
      "1x2_log_loss": 0.8921275757415663,
      "ou_brier": 0.4980964602285635,
      "ou_log_loss": 0.6912429989821982
    },
    {
      "dc_rho": "-0.15",
      "1x2_brier": 0.5246939958100466,
      "1x2_log_loss": 0.891783627002631,
      "ou_brier": 0.4980964602285635,
      "ou_log_loss": 0.6912429989821982
    }
  ],
  "best_by_1x2_log_loss": {
    "dc_rho": "-0.15",
    "1x2_brier": 0.5246939958100466,
    "1x2_log_loss": 0.891783627002631,
    "ou_brier": 0.4980964602285635,
    "ou_log_loss": 0.6912429989821982
  },
  "log_loss_improvement_vs_0": 0.0015495347959503247,
  "brier_improvement_vs_0": 0.0005736531547535506
}
```

## 结论候选（待用户决策，不自动改生产配置）

- 在本批无赔率样本中，`dc_rho=-0.15` 的 1X2 Log Loss 最低，命令输出中的 `log_loss_improvement_vs_0` 为 `0.0015495347959503247`，`brier_improvement_vs_0` 为 `0.0005736531547535506`。
- `dc_rho` 扫描只影响 1X2 低比分形状；本轮 OU 指标四档相同，命令输出均为 `ou_brier: 0.4980964602285635`、`ou_log_loss: 0.6912429989821982`。
- 按 outcome 聚合看，当前基线对主胜和平局略低估、对客胜略高估：`home.hit_minus_p = 0.00984213697046804`，`draw.hit_minus_p = 0.013735995048848565`，`away.hit_minus_p = -0.023578132019316356`。
- 本报告不修改 `config/settings.yaml`。是否启用非零 `dc_rho`、取值多少，需要用户基于本报告另行确认。
- 无历史赔率样本时，EV/Edge 阈值和 `poisson.mu_market_weight` 不能用本报告校准；需要世界杯期间自有赔率快照和赛果回填后的下一轮计划。
