# 联赛日常刷新与可靠发布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 不依赖正式首发，让已验收联赛按预算生成、归档和可靠发布有效赛前首选，证明默认 dry-run 零副作用。

**Architecture:** 保留现有模型、batch/store 和 HMAC ingest，通过新的日常编排入口连接生产赛事缓存、纯调度、额度预留、共享写锁及持久发布 outbox。日常和首发后发布统一防回退；服务端在同一 SQLite 事务中校验联赛组件版本并存储，不能依赖客户端锁解决 HTTP 乱序。

**Tech Stack:** Python >=3.11、标准库、JSON、fcntl、现有 SQLiteSnapshotStore、项目自带 tests/run_tests.py；不安装新依赖。

**Spec:** `docs/superpowers/specs/2026-08-31-league-production-loops-design.md`。执行者必须完整阅读设计与本计划。

状态：用户已确认第一份计划，本地 Tasks 1–6 与最终 I3 事件保留/version-binding 修订均已实现；最新完整离线回归见 README/RECENT_WORK。未启用 live、修改生产数据或安装定时器，不授权 commit/push/PR/merge/deploy。赛后公网同步仍属第二份计划。

## Global Constraints

- 范围为固定六联赛；世界杯、中超的调度、数据、统计和通知保持独立。
- 每场有有效可结算盘口即输出唯一 schema v2 `MATCH_PICK`；没有有效盘口才为 `NO_CLEAN_MARKET`。不改 `match_pick_v3`、模型参数或 club rating pending 语义。
- 正式首发不是日常推荐前置条件；首发后复核继续走现有 observer，严格 confirmed 判定不放宽。
- `active` 必须验证证据指纹和严格 identity；不以字符串或 slug 代替。日常刷新按已验收联赛运行，不伪造德甲资格。
- 赛后正式 live/write 仍要求六联赛 Gate A 全部完成，再逐项授权 Gates B–D。五联赛 dry-run 通过不自动改写该门禁。
- 不事后生成 closing，不把 reconstructed、legacy、世界杯或中超混入六联赛正式统计。不删除或静默改写已接受比分。
- 仅供研究分析，不构成投注建议，不输出金额、追损或串关建议。
- 默认 dry-run 不读 `.env`、不联网、不建锁、不写运行产物、不通知；测试只在 TemporaryDirectory 写入。
- 日常刷新不调用旧 scores lifecycle；不删除旧兼容代码或证据。赛后公网同步不在本计划实现范围。

## 执行环境与验证方式

实施须在从用户确认 main 提交建立的 `codex/` 隔离 worktree 内进行，按 using-git-worktrees 流程；运行根目录目前有用户文档改动，不得覆盖、stash 或带入无关记录。本计划阶段不创建工作树。

每任务按 RED → GREEN → 全量回归；禁止真实 provider 和 WxPusher。以下命令在工作树根目录运行，不需要 pytest：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -B tests/run_tests.py
git diff --check
```

指定文件聚焦测试用实际文件名替换列表，仅加载本地测试函数：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -B - <<'PY'
import runpy
for path in ['tests/test_league_daily_plan.py']:
    for name, fn in runpy.run_path(path).items():
        if name.startswith('test_') and callable(fn):
            fn()
print('focused tests passed')
PY
```

每任务末尾是可独立审查交付点，不自动 commit。用户另行确认提交推送后，才允许在该阶段分支执行精确文件列表的 git add/commit；不要用设计中的代码块推导永久 Git 授权。

## 固定策略及契约

### 调度参数

- timer 生成器建议每 300 秒唤醒，`RunAtLoad=false`；本计划不安装它。
- 普通重复刷新最短间隔 30 分钟；同一 event/kickoff 的 T-6h、T-90m、T-25m 各完成一次。首次进入更晚锚点或新的 expiry 证据可提前于普通间隔，但不能重放同一已成功签名。
- 无未来赛事或生产缓存缺失时，每联赛最多 24 小时一次 discovery；discovery 失败后 30 分钟再试，仍受每日额度与失败预留限制。已完成锚点不能阻断新发现赛事。
- 正常额度下，T-6h/T-90m/EXPIRY 使用 h2h，T-25m 使用 h2h+spreads+totals；同联赛本轮所需 markets 取并集。expiry 不得覆盖更丰富 markets。
- 所有已配置槽位都无大于 30 的已知额度且无未探测槽位时，降级为 T-25m h2h；暂停 expiry/discovery。无可用额度则停止。
- `--daily-credit-limit N`：正整数，live 必填，没有隐式无限预算；干跑可缺省并报告 `daily_budget_unconfigured`。首次启用前用户单独确认数值，不能将本计划或曾经的 4-credit 估算当预算授权。
- 每日预算按北京时间自然日计算，覆盖新日常链路及接入共享边界的六联赛首发后赔率请求；不改变世界杯、中超原有预算，但请求前重读它们共同使用的真实 quota ledger。
- 同一轮按 T-25、T-90、EXPIRY、T-6、discovery 的优先级，再按最近开球及 competition_id 排序。预算不够显式记录跳过，不删除比赛。

### 运行状态和锁

新增 ignored `data/local/leagues/daily_refresh_state.json`（schema 1）：逐联赛成功锚点签名、last_attempt_at、last_success_at、next_discovery_at、attempts，以及逐北京时间日期的额度 reservations。attempt 必须包含 competition、acceptance/registry 指纹、markets、expected_snapshot_id、实际请求时间、phase 和安全错误码；phase 固定 `reserved/fetched/committed/pending/published/blocked`。结果未知的已发请求保持预留，不能自动视为未消费。

新增 ignored `data/cache/leagues/<competition>/events.json`（schema 1）：`competition_id/observed_at/source_snapshot_id/events`；event 包含 source_event_id、canonical home/away、timezone-aware kickoff。损坏缓存报错，不能按空缓存触发额外付费发现。请求返回缺失 event 只表示本轮未覆盖，不代表比赛取消或完赛。

共享 `data/local/leagues/odds_execution.lock` 串行化日常和首发后赔率执行。锁顺序固定：各 runner 已有外层锁 → odds_execution → snapshot/closing/state 短锁；额度 ledger 锁只在 header 更新期间持有，不与 state 锁嵌套。发布锁在 odds_execution 后获取，但进入发送前释放所有 snapshot/closing 短锁。任何调用者不得从内部短锁反向请求 odds_execution 或另一个 runner 的外层锁。

新增 `data/local/leagues/publication_state.json`（schema 1）和独立 publication 锁：保存已发布 component 向量、一个绑定 endpoint 的 pending（payload/body hash、accepted fingerprint、component 向量）及 superseded 审计条目。服务端成功后先落 sent state 再清 pending。outbox 重试重签当前时间，不改变冻结 payload。

### 公共发布版本保护

aggregate 增加 `league_publication` 字段，schema 1、`components` 映射。当前只允许 `odds:<competition_id>`；每项 `snapshot_id/snapshot_at/content_sha256`。哈希原文固定为该分区的 `competition_id/snapshot_id/snapshot_at/matches` 四字段，其中 matches 按 source_event_id 排序、使用实际发布的安全字段；规范化 JSON 使用 sort_keys=True、紧凑分隔符、ensure_ascii=False。这称为“发布组件哈希”，不是源 snapshot 全文件哈希。服务端按同样规则从 aggregate matches 与 component 身份重建并校验一致性，不能只信任 caller 声称的新版本。完整 vector 必须覆盖本轮 active 集合，不得漏掉旧已发布 component。

比较规则：component 时间倒退拒绝；同一时间不同 snapshot_id/hash 拒绝；完全相同允许重复；当前已保存 component 缺失拒绝。`snapshot_at` 必须 timezone-aware。每个事件绑定实际产生它的 `source_snapshot_id`；provider 新响应未覆盖的旧事件保留原始事件版本，不能改绑为本轮新 snapshot。odds component 的公开事件 membership 只能保持或扩张；较新 component 删除旧事件，或同一 `event_id` 改写 `kickoff_at_utc/home_canonical/away_canonical`，均由客户端和 SQLite 服务端 fail closed。后续只有第二份计划的严格 terminal/result component 能授权公开移除，当前不开放任意 namespace。

在 SQLite `put_snapshot` 内，同一 `BEGIN IMMEDIATE` 事务先处理已有幂等键，再读取最近的合法 multi_league publication、验证向量、INSERT。不新增表/索引；一次 top-level multi_league 候选查询，禁止按六个 competition 逐一扫描。首次升级时，旧聚合缺精确分区时间，不能伪造完整向量：以其顶层 snapshot_at 作为保守时间下界，已有 component membership 必须保留；未变化的分区必须匹配旧 snapshot_id 和安全 matches，新分区版本必须晚于此下界。无法证明兼容则阻断首次发布并申请独立迁移，不自动忽略旧数据。首个新契约保存后，无新字段的旧 multi_league 写入返回 `league_publication_contract_required`，世界杯、中超不受影响。新字段不支持的 store 返回显式 unsupported，不降级绕过；Postgres 正式适配与迁移不在本次范围。

HTTP 入站把版本拒绝投影为已有 rejected 错误响应，不冒充 stored/duplicate；客户端收到 stale/rejected 不更新成功锚点。第二份计划将测试结果组件与旧赛前 payload 的互相覆盖，本次先交付赔率组件乱序保护。

## 文件职责地图

| 文件 | 本次职责 |
| --- | --- |
| 新增 `worldcup/league_daily_plan.py` | 生产赛事输入、纯 due/预算/markets 合并 |
| 新增 `worldcup/league_daily_state.py` | schema、原子状态、attempt 预留/恢复、共享执行锁 |
| 新增 `worldcup/league_daily_runner.py` | 默认 dry-run 的生产 CLI 和依赖装配 |
| 新增 `worldcup/league_publication.py` | 纯组件比较及持久发布协调 |
| 新增 `worldcup/league_daily_launch_agent.py` | 仅生成独立 timer 配置 |
| 修改 `worldcup/league_scheduled_publish.py` | 聚合声明版本、公共发布入口复用；保留旧 dry-run API |
| 修改 `worldcup/league_batch_runner.py`、`league_live_store.py` | 已有接缝仅在必要处支持精确 market/context 与防回退，保持旧调用兼容 |
| 修改 `worldcup/league_post_lineup_refresh.py` | 接入共享执行/发布边界，保留 receipt/ACK 语义 |
| 修改 `worldcup/store.py`、`ingest_app.py` | SQLite 原子向量验证及安全 rejected 响应 |
| 修改 README、必要架构摘要、RECENT_WORK | 只描述实际实现状态及安全启用流程 |

## Task 1: 生产输入与纯日常计划

**Files:** 新建 `worldcup/league_daily_plan.py`、`tests/test_league_daily_plan.py`；读取现有 `league_live_planner.py` 与 `league_team_identity.py`，不改模型。

**Interfaces:**

```python
def merge_market_requests(requests: list[dict]) -> list[dict]:
    """每 competition 一行，event_ids 去重，markets 按 h2h/spreads/totals 排序。"""

def load_daily_events(root: Path, acceptance: dict, registry: LeagueTeamIdentityRegistry) -> dict:
    """返回 competitions/events/errors；只读正式 snapshot 与生产 events，禁止读取 probe。"""

def plan_daily_refresh(*, now: str, events: dict, acceptance: dict,
                       state: dict, quota_mode: str,
                       daily_credit_limit: int | None) -> dict:
    """返回 requests/estimated_credits/skipped/next_due_at/live_blockers。"""
```

- [x] 写 RED：先覆盖同联赛 market 并集，明确不能出现一个 expiry 请求吞掉 T-25 markets。

```python
from worldcup.league_daily_plan import merge_market_requests

def test_expiry_does_not_drop_t25_markets():
    requests = [
        {'competition_id': 'epl_2026_27', 'event_ids': ['a'], 'markets': ['h2h']},
        {'competition_id': 'epl_2026_27', 'event_ids': ['b'], 'markets': ['h2h', 'spreads', 'totals']},
    ]
    merged = merge_market_requests(requests)
    assert len(merged) == 1
    assert merged[0]['event_ids'] == ['a', 'b']
    assert merged[0]['markets'] == ['h2h', 'spreads', 'totals']
    assert merged[0]['estimated_credits'] == 3
```

- [x] 运行聚焦测试，确认因为新增接口缺失失败；随后按以下核心归并实现，再加入严格 shape/competition 校验：

```python
order = ('h2h', 'spreads', 'totals')
merged_markets = [m for m in order if any(m in r['markets'] for r in rows)]
merged_ids = sorted({event_id for r in rows for event_id in r['event_ids']})
```

- [x] 扩展 RED/GREEN：T-6/T-90/T-25边界、expiry与同时间多场、已开始跳过、未知身份、损坏缓存、缺缓存 discovery、active指纹失效、低额度仅T25、预算不足及北京时间跨日。event kickoff/identity 冲突不能“最后一条覆盖”。只有明确没有状态文件才使用空初始状态，畸形状态必须阻断。
- [x] 定义锚点签名为 competition/event_id/kickoff/anchor；expiry 另绑定旧 decision valid_until 和源 snapshot_id。已完成签名过滤后再合并，不以重复轮询累计消费。
- [x] 删除生产输入对 probe 的依赖：缺生产 events 可从正式 snapshot 提取严格 canonical event，不回填 probe 文件。运行全量测试，交付纯计划与零写入证明。

## Task 2: 持久尝试、额度预留和重启恢复

**Files:** 新建 `worldcup/league_daily_state.py`、`tests/test_league_daily_state.py`。

**Interfaces:**

```python
def empty_daily_state() -> dict:
    return {'schema_version': 1, 'competitions': {}, 'attempts': {}, 'budgets': {}}

def reserve_credits(state: dict, *, date_bj: str, attempt_id: str,
                    estimated: int, limit: int) -> dict:
    """纯函数；同 attempt 幂等，超限 ValueError('daily_budget_exhausted')。"""

def odds_execution_lock(root: Path):
    """仅 live 使用的非阻塞 context manager；竞争报告 busy。"""
```

`DailyStateStore(root: Path)` 提供 `read() -> dict` 和 `commit(state: dict) -> None`。实现方法见下方原子写入步骤；状态校验必须拒绝非 JSON 类型、不合法 UTC 时间、跨联赛 attempt 复用、负额度和 phase 回退。

- [x] 写 RED，运行 Task 2 文件：

```python
from worldcup.league_daily_state import empty_daily_state, reserve_credits

def test_reservation_survives_unknown_response_and_is_idempotent():
    first = reserve_credits(empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=3, limit=3)
    assert reserve_credits(first, date_bj='2026-09-01', attempt_id='a', estimated=3, limit=3) == first
    try:
        reserve_credits(first, date_bj='2026-09-01', attempt_id='b', estimated=1, limit=3)
    except ValueError as exc:
        assert str(exc) == 'daily_budget_exhausted'
    else:
        raise AssertionError('unknown attempt was incorrectly refunded')
```

- [x] GREEN 核心：先查同 attempt 的日期/估算一致性，再计算本日所有 reservation 的 committed-or-reserved 总额；只在网络请求明确未发出时撤销。成功 header 结算该 request 的 actual cost，不用整个账户 used 差值冒充本任务消费。无可信 header 保持预留并记录 unknown。
- [x] `DailyStateStore.commit` 采用 tempfile、flush/fsync、replace、目录 fsync；相同锁内重读并验证单调 merge。读操作不能创建目录或锁。执行锁以 `LOCK_EX | LOCK_NB` 获取。
- [x] 临时目录测试：损坏JSON、并发预留、预留后崩溃、fetch后未commit、history已写但current失败、published回执已写但清pending失败。不能将孤立历史文件直接认作公开成功；expected_snapshot_id、实际时间和事件 membership 必须绑定。
- [x] 跨进程锁测试用 multiprocessing/Event 控制顺序而非 sleep。全量 GREEN 后交付状态模块；不操作生产 state。

## Task 3: 精确盘口采集和真实赛程发现

**Files:** 新建 `tests/test_league_daily_fetch.py`；在 `worldcup/league_daily_runner.py` 首次实现本任务 fetch 接缝；必要时最小修改 `league_batch_runner.py`，不改变现有首发路径默认 markets。

**Interfaces:**

```python
def fetch_daily_odds(*, request: dict, env: dict, root: Path,
                     observed_at: str, transport=None) -> dict:
    """返回 raw events、slot名称及脱敏 quota，不返回 key/url/headers。"""

def discover_events(*, raw_events: list, competition_id: str,
                    registry: LeagueTeamIdentityRegistry,
                    observed_at: str) -> dict:
    """严格标准化同一 odds 响应的赛事，返回 events/rejected；不联网。"""
```

- [x] RED 验证 fetch 参数，不只检查 planner：使用注入式 transport 捕获 query 后返回保存的赔率响应和 quota headers；断言 h2h 请求 URL 的 markets 只有 h2h，三市场为精确集合、region为单一现有 region。响应内容取项目已有 `tests/test_league_odds_refresh.py` 对应的已保存样例结构，禁止测试访问外网。
- [x] GREEN：复用 `fetch_odds_for_sport`，显式传 `request['markets']` 及 `max_attempts=1`；每次 request 前使用 `choose_key_slot` 重读 ledger。禁用本层隐式多次 provider 重试；每个实际网络尝试均先预留。发布重试与抓取重试分开。

```python
markets = tuple(request['markets'])
assert markets and set(markets) <= {'h2h', 'spreads', 'totals'}
estimated_credits = len(markets)  # 单一已验证 region
```

- [x] discovery 使用同一个已验收 odds 响应，不新增未经验证的 `/events` live 接口；合法空响应写入生产发现的成功时间，无 event 可建 snapshot 时不触发 batch 强造 snapshot。下一轮24小时后再发现。已有未来赛事不能因一次响应未返回而标为取消/完赛。
- [x] 原始 response 保存到当前 attempt ignored 路径，然后按真实响应完成时钟重新校验 kickoff，过滤已开赛或terminal事件，使用严格 registry 解析新 event。拒绝缺失sport_key、冲突ID、naive kickoff和不匹配队伍；错误不影响其他联赛合法分区。
- [x] 以 `run_planned_league_refresh` 接入：expected IDs 来自本次严格解析后的未来事件，expected snapshot id 绑定 attempt；不能用过期 probe IDs 要求新 response 必须包含已结束比赛。将采集结果作为注入 fetcher 的返回值，不能再发第二次网络请求。
- [x] 新增参数化断言：同响应既发现新赛程又产生首选，只消费一次；provider已收费但解析失败仍记预算；响应跨开球不得产生新的赛前closing。全量回归。

## Task 4: 发布组件防回退与原子服务端校验

**Files:** 新增 `worldcup/league_publication.py`、`tests/test_league_publication.py`；修改 `worldcup/store.py`、`worldcup/ingest_app.py`、`worldcup/league_scheduled_publish.py`；扩展 `tests/test_store.py`、`tests/test_ingest_app.py`、`tests/test_league_scheduled_publish.py`。

**Interfaces:**

```python
def validate_component_vector(previous: dict, current: dict) -> None:
    """无回退返回 None，否则 ValueError，错误码固定 league_component_regression/conflict。"""

def build_publication_vector(snapshots: list[dict]) -> dict:
    """生成并严格校验 odds component manifest。"""

def deliver_league_publication(*, root: Path, endpoint: str, snapshot: dict,
                              publish_fn, now: str) -> dict:
    """持久化/恢复统一 pending，回传 stored/duplicate/pending/rejected。"""
```

- [x] RED：同时间冲突和迟到回退：

```python
from worldcup.league_publication import validate_component_vector

def test_delayed_component_is_rejected():
    def vector(at, ident, digest):
        return {'odds:epl_2026_27': {'snapshot_at': at, 'snapshot_id': ident, 'content_sha256': digest * 64}}
    new = vector('2026-09-01T12:00:00+00:00', 'new', 'a')
    old = vector('2026-09-01T11:00:00+00:00', 'old', 'b')
    validate_component_vector(new, new)
    try:
        validate_component_vector(new, old)
    except ValueError as exc:
        assert str(exc) == 'league_component_regression'
    else:
        raise AssertionError('delayed payload must not replace newer data')
```

- [x] GREEN 比较精确元组；不能把 hash 字典序当时间。manifest 采用本计划定义的四字段发布组件哈希；服务端从 aggregate 中重建，公开 projection 严格白名单，不泄露原始 provider/secret。不额外嵌入原始 provider 响应，也不把发布组件哈希当成源文件哈希。
- [x] SQLite 同事务校验与 INSERT，幂等键先查；版本拒绝不插入行、不改变 latest。入站只投影安全错误码，已有 duplicate 语义保持。首次新契约验证旧聚合基线；不支持原子校验的 store 必须拒绝新契约，不返回假成功。额外回归必须覆盖“新时间戳但少一场”和“同 event_id 改身份”，两者都不得替换 latest。
- [x] 真实临时 SQLite + HMAC 请求集成测试：先A后B再迟到A不同run_id，latest仍为B；两个并发请求互斥检查；缺component拒绝；旧格式世界杯/中超照常；升级后旧格式multi_league拒绝；完整重复仍duplicate；单一 multi_league 查询次数不随联赛数增加。
- [x] outbox先落pending再发送，失败保留，重启重签timestamp；accepted回执持久化失败可重发同snapshot并得到duplicate。不允许 timeout后删除pending当成功；不同endpoint拒绝复用pending。
- [x] 扩展 aggregate snapshot_id：由全部实际component hash/identity构成；相同源snapshot集合生成相同payload与ID，不能靠wall-clock让同内容反复成为新版本。
- [x] 全量回归；服务端契约变更必须单独部署后才能启用新客户端。此任务不执行部署。

## Task 5: 日常 runner 与首发后链路协调

**Files:** 完成 `worldcup/league_daily_runner.py`、新增 `tests/test_league_daily_runner.py`；修改 `worldcup/league_post_lineup_refresh.py`、`worldcup/league_scheduled_publish.py`；扩展首发既有测试。

**Interfaces:**

```python
def run_league_daily(*, root: Path, now: str, live: bool = False,
                     write: bool = False, publish: bool = False,
                     endpoint: str | None = None, daily_credit_limit: int | None = None,
                     env_loader=None, odds_fetcher=None, publish_fn=None,
                     observed_clock=None) -> dict:
    """返回 mode/status/plan/competitions/publish/safety。"""
```

- [x] RED：默认 dry-run不调用注入依赖、不创建目录：

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from worldcup.league_daily_runner import run_league_daily

def test_dry_run_never_creates_missing_root_or_calls_dependencies():
    def forbidden(*args, **kwargs):
        raise AssertionError('side effect in dry-run')
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / 'absent'
        result = run_league_daily(root=root, now='2026-09-01T12:00:00+00:00',
                                  env_loader=forbidden, odds_fetcher=forbidden, publish_fn=forbidden)
        assert result['mode'] == 'dry_run'
        assert result['status'] == 'blocked'
        assert not root.exists()
```

- [x] GREEN 顺序实现：只读输入计划 → live flags/endpoint/预算门禁 → 外锁/共享执行锁 → 新鲜时间/验收/quota复验 → 优先处理publication pending → 预留attempt → fetch → 存raw并采样真实时钟 → 严格标准化 → batch commit/history → 聚合vector/outbox → 发布回执 → 记录成功签名。逐联赛安全错误显式投影；坏分区保留LKG并标stale，不能用success包装。
- [x] CLI 默认 dry-run，live 需同时 `--live --write --publish --endpoint --daily-credit-limit`；live拒绝 `--now`，dry-run `--now`可选。`--env/--quota-path` 只在live门禁通过后使用。预算未配、unknown state、symlink state或不合法endpoint均在联网前阻断。
- [x] 持久fetched attempt优先离线恢复，不再次抓取；pending先重发，不再付费；publication stale拒绝转显式 superseded 审计，再读取最新分区重建，不覆盖较新状态。不得将publish失败当成需要重新执行同一fetch的理由。
- [x] 首发后执行复用odds_execution锁和budget reservation；已有confirmed receipt仍需post-information odds，不能用首发前的日常snapshot代替ACK。若日常已产生时间晚于confirmed的精确合法snapshot，可复用，但必须通过已有receipt/context/market evidence验证。
- [x] 首发后和日常统一使用publication coordinator；保持原有Task4/Task5 ACK只有durable published成立，拒绝版本不能确认ACK。未配置共享预算时允许无赔率的首发观察，但新live赔率路径显式blocked，必须在实际启用前同步本机配置。
- [x] 双runner竞争、预算不足、事件跨开球、过期pending、失败分区健康分区并存、保存history后崩溃各写一条可复现集成回归；复用现有测试helper但不得改测试令旧安全门禁变宽。全量回归。

## Task 6: Timer 生成器与公开端到端验证

**Files:** 新增 `worldcup/league_daily_launch_agent.py`、`tests/test_league_daily_launch_agent.py`、`tests/test_league_daily_end_to_end.py`；修改 README、RECENT_WORK，若已有 ARCHITECTURE.md 则同步相关边界。

**Interfaces:**

```python
def build_league_daily_launch_agent(*, python_path: str, workdir: str,
                                   full_live: bool = False, endpoint: str | None = None,
                                   daily_credit_limit: int | None = None) -> dict:
    """纯plist字典；默认观察、interval=300、RunAtLoad=False。"""
```

- [x] RED 测试生成器不隐含付费标志：

```python
from worldcup.league_daily_launch_agent import build_league_daily_launch_agent

def test_observation_timer_has_no_live_flags():
    p = build_league_daily_launch_agent(python_path='/usr/bin/python3', workdir='/tmp/league-test')
    assert p['Label'] == 'xin.celab.football.league-daily'
    assert p['StartInterval'] == 300 and p['RunAtLoad'] is False
    assert '--live' not in p['ProgramArguments']
    assert '--write' not in p['ProgramArguments']
    assert '--notify' not in p['ProgramArguments']
```

- [x] GREEN：复用现有plist生成模式，模块为worldcup.league_daily_runner；full_live缺endpoint/budget直接ValueError；默认只打印，显式 `--out` 才写指定文件。不得调用launchctl，测试生成写入仅临时目录。
- [x] 端到端使用临时JSON/SQLite、保存样例、fake transport、真实HMAC和project_match_rows：无首发但有合法赔率时，生成有效MATCH_PICK且不泄露内部manifest；发布重试后quota调用计数不增；重复timer无新due不请求；provider过期则无法计算而非假新鲜；乱序旧发布不能回退公网latest。
- [x] 维护说明写明：本功能不完成赛后公网关闭；首发probe/德甲Gate A未被改变；新server先部署，然后更新本机运行代码、配置预算并验证，最后独立授权安装timer。通知仍只有原有首发触发，不新增日常群发。
- [x] 完整tests/run_tests.py、git diff --check、新增文件敏感字段检查；默认dry-run前后比较临时业务树及fixture树manifest。记录实际测试数字，不复用1505作为新结果。
- [x] 请求代码审查，修复阻断项；停止在本地已验证交付，不自动提交、推送、合并或上线。

## 分阶段上线清单（需要另行确认，不是本轮执行步骤）

1. 第一份计划和公开版本字段获批后，确认本地实现；真实环境保持旧链路。
2. 经确认 commit/push/PR，CI通过后再单独合并。
3. 部署服务端原子校验与旧数据兼容，GET smoke通过；验证不影响世界杯/中超。
4. 只读重算active名单、markets、可用槽位、预算和预计消耗；重新确认首次live写入/发布及budget数值。不能直接沿用4 credits。
5. 首次live按有限联赛请求验证真实header、分区history、ingest receipt及公网未过期首选。任何阶段失败保留证据，不自行扩大请求量。
6. 同步本机首发与日常共享边界和预算配置；观察版timer先验证零副作用，再独立确认full-live安装，RunAtLoad=false且不kickstart。
7. 停用回滚只bootout对应label，不删除产物。新契约启动后不得恢复会发送旧multi_league契约的writer；服务器版本校验拒绝旧写入时显式保持停用。

## 对抗性自审与覆盖表

| 推翻计划的反例 | 处理与验证任务 |
| --- | --- |
| 完成定时器但依然只跑dry-run | Task 5显式生产依赖与flags，Task 6端到端验证 |
| 计划1credit实际3markets | Task 1并集、Task 3捕获真实transport参数 |
| probe赛事用完后永久没新赛程 | Task 1生产输入、Task 3同odds响应发现和空列表发现 |
| 失败请求/进程崩溃绕过预算 | Task 2先预留后请求，unknown不自动退款 |
| 首发和日常各持有自己的锁仍重复调用 | Task 2/5共同执行锁与统一reservations |
| 网络旧请求晚到，客户端锁已释放 | Task 4服务器同事务组件校验，不靠本地锁证明 |
| 新scheduler顺手激活赛后或德甲 | Global Constraints及Task 5不调用scores lifecycle |
| 本地成功但公网仍旧数据 | Task 6真实投影端到端，独立live后GET验收 |

自审结论：第一份计划覆盖设计第4节和发布防回退基础，设计第5节的FT结果组件、历史缺closing展示与公网关闭明确归第二份计划；不能以完成本计划声称整套赛后链路已交付。daily budget数值尚未授权，live入口必须因此可阻断而不是选取隐含费用。新公共manifest及服务端拒绝规则为本计划的显式契约变更，需要用户审核；未获批不得开始实现。
