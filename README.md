# 世界杯足彩分析站

这是一个 2026 世界杯研究/分析站项目。当前 MVP 目标是先跑通：

1. 数据源探测
2. Elo + Poisson + 赔率去水分析
3. 价值信号分级
4. 后续上传到阿里云网站展示

项目定位是**研究/分析工具**，不构成投注建议，不显示下注金额，不做追损、重注或喊单。

## 当前状态

- Git 仓库已初始化。
- Plan 1 引擎核心已完成第一版。
- 本地测试执行器通过：`41/41 tests passed`。
- Plan 0 数据源探测计划已写好，等待 API key 后执行。

## 技术栈

- Python 3
- 标准库优先
- 当前引擎不联网、不连数据库、不依赖云资源
- 后续采集层会使用 `requests`
- 后续云端计划使用 FastAPI + PostgreSQL + OSS

## 目录结构

```text
config/
  settings.yaml                 # 模型常数、阈值、刷新参数

docs/superpowers/specs/
  2026-06-08-worldcup-prediction-mvp-design.md

docs/superpowers/plans/
  2026-06-08-engine-core.md
  2026-06-08-plan0-data-source-probe.md

worldcup/
  config.py                     # 配置读取
  models.py                     # 数据模型与枚举
  differ.py                     # 两轮变化检测
  engine/
    odds.py                     # 赔率去水、聚合
    elo.py                      # Elo 1X2 概率
    poisson.py                  # Poisson 比分矩阵
    handicap.py                 # 亚洲让球 EV
    ensemble.py                 # Elo + Poisson 集成
    value.py                    # EV / Edge / 等级 / 状态

tests/
  run_tests.py                  # 无 pytest 环境下的本地测试执行器
```

## 本地验证

当前机器没有安装 `pytest` 时，用：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

如果以后安装了 `pytest`，也可以用：

```bash
python3 -m pytest -v
```

## API 注册清单

至少先注册 API-Football；赔率备源至少注册一个。

| 用途 | 服务 | 注册 / 官网 |
|---|---|---|
| 主数据源：赛程、结果、赔率探测 | API-Football | https://www.api-football.com/ |
| 赔率备源 | The Odds API | https://the-odds-api.com/ |
| 赔率备源 | odds-api.io | https://odds-api.io/ |
| 赔率低频交叉校验 | OddsPapi | https://oddspapi.io/ |
| 免费赛程源 | openfootball/worldcup.json | https://github.com/openfootball/worldcup.json |
| Elo 数据源 | World Football Elo Ratings | https://www.eloratings.net/ |

拿到 key 后，本地创建 `.env`，不要提交：

```bash
API_FOOTBALL_KEY=...
THE_ODDS_API_KEY=...
ODDS_API_IO_KEY=...
ODDSPAPI_KEY=...
```

`.env` 已被 `.gitignore` 忽略。

## 下一步

1. 注册 API key。
2. 按 `docs/superpowers/plans/2026-06-08-plan0-data-source-probe.md` 执行数据源探测。
3. 产出 `docs/superpowers/data-contract.md`。
4. 根据真实字段写 Plan 2 采集层。

## 重要约束

- API key、RDS 连接串、HMAC 密钥、Cookie、token 不得写入 git、文档或回复。
- macmini 不直连 RDS/OSS，后续只调用 ECS ingest API。
- 所有公开输出都必须保留免责声明。
- 当前模型未经历史回测验证，公开页只能显示研究价值信号。
