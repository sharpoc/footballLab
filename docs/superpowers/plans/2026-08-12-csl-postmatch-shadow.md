# CSL Postmatch Shadow Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动中超赛前首选、公开 API 和赔率额度的前提下，建立一条默认 dry-run、可幂等、失败非阻断的本地赛后 shadow 闭环，稳定产出封盘首选结算、coverage、分组与校准指标。

**Architecture:** 保留 `csl_eval_data` 作为唯一 closing 匹配边界，在不破坏现有 `closing_match_entry()` 接口的情况下增加带 snapshot 审计元数据的返回类型。新增 `csl_postmatch_shadow` 先纯计算 decision-only 报告与稳定指纹，再把现有 `csl_postmatch_runner` 的 eval/backtest/pending-gate 产物全部生成到 staging 目录，校验后逐文件原子提升，canonical shadow report 倒数第二、state 最后提交。`csl_scheduled_publish` 只在已进入 live/due 的现有结果刷新步骤后调用 shadow，并把所有 shadow 异常降级为安全 warning。

**Tech Stack:** Python 3 标准库（`dataclasses` / `hashlib` / `json` / `os.replace` / `tempfile` / `shutil`），现有 `worldcup.decision_settlement`、`worldcup.csl_eval_data`、`worldcup.csl_postmatch_runner`、`worldcup.csl_scheduled_publish`，项目自建 `tests/run_tests.py` 测试器。

**Status:** Implemented, verified, and released through PR #2 in remote `main` commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`.

## Global Constraints

- 本计划的业务契约以已确认设计 [`docs/superpowers/specs/2026-08-12-csl-postmatch-shadow-design.md`](../specs/2026-08-12-csl-postmatch-shadow-design.md) 为准。
- 实现前必须先获得本阶段的“确认实现”；该口令不授权 commit、push、部署、修改 LaunchAgent、读写 secret 或线上数据。
- 如要按任务创建本地 commit，必须另行获得“确认提交推送”的适用授权；未获授权时，跳过各任务末尾的 commit checkpoint，但仍要逐任务测试。
- 当前工作树已有用户改动：`AGENTS.md`、`CLAUDE.md`、`RECENT_WORK.md`、`tests/test_ssh_deploy.py`、`worldcup/ssh_deploy.py` 和 `.claude/`。实现时不得覆盖、格式化或清理这些无关改动；文档更新只做精确追加。
- 默认 CLI 必须零写入；只有显式 `--write` 或已进入 live/due 且结果双源已接受的调度调用可写 ignored 本地产物。
- shadow 不网联、不读 `.env`、不调 The Odds API、不写 quota、不通知、不发布、不部署、不改公开 API/preview/static export。
- 不修改 `match_pick_v3`、`club_rating_pending`、模型参数或每场唯一首选策略；35 场样本只是观察，不用于调参。
- 正式结算必须通过 `settle_match_decision()`，正式聚合必须通过 `summarize_decision_records()`，shadow 不复制 1X2/OU/AH/DNB 规则。
- 只把 `schema_version=2` 当作当前策略；legacy decision 只进 coverage，不进当前命中率。
- `club_results_csl_2026.csv` 是 2023–2026 评级 replay 合集；shadow 的结算、coverage 和指纹只使用 `season=2026` 赛果。旧赛季赛果继续交给现有 pending gate/评级回放，不得误记为当季 `missing_closing`。
- 不把跨文件提升宣称为真正事务。消费者仅在 state 中的 canonical hash 与 report 实际 hash 一致时认定新一轮完整成功。
- 所有新产物留在 ignored `data/local/` / `data/cache/`；代码、测试和文档才进 Git 范围。

## File Structure and Responsibilities

### Files to add

- `worldcup/csl_postmatch_shadow.py`
  - 定义报告 schema、decision 安全投影、分组/校准纯函数、稳定指纹、dry-run/write runner、staging/提升/state 和 CLI。
  - 不包含网络客户端、密钥读取、发布代码或新的盘口结算规则。
- `tests/test_csl_postmatch_shadow.py`
  - 覆盖报告契约、小样本、分组/校准、指纹幂等、dry-run、原子提升回滚、状态 hash 一致性和 CLI 安全摘要。

### Files to modify

- `worldcup/csl_eval_data.py`
  - 新增不可变 `ClosingMatch` 元数据对象和 `closing_match()`。
  - 现有 `closing_match_entry()` 保持签名与返回值兼容，内部改为包装新函数。
- `tests/test_csl_eval_data.py`
  - 增加 closing 审计元数据、开赛后快照排除、延期/改期/跨赛事/主客对调的回归用例。
- `worldcup/csl_scheduled_publish.py`
  - 在结果刷新之后、odds refresh 之前注入调用 shadow runner。
  - 对 shadow 异常做安全摘要与 warning，并在后续所有返回路径带上 `postmatch_shadow` 摘要。
- `tests/test_csl_scheduled_publish.py`
  - 为所有 live 用例注入 fake shadow，避免测试误写工作区。
  - 增加 accepted/blocked/error/odds-refresh-failure/pending-retry 的触发次数和非阻断断言。
- `README.md`
  - 在现有中超赛后评估章节增加 shadow CLI、产物、默认 dry-run、调度非阻断与不影响 quota/公开边界。
- `AGENTS.md` 和 `CLAUDE.md`
  - 只在现有中超规则中同步追加 shadow 契约；保留当前 dirty diff，两份内容保持一致。
- `RECENT_WORK.md`
  - 实现和验证完成后只追加一条近期记录，不归档、不压缩、不删除旧记录。

### Files explicitly not to modify

- `worldcup/match_decision.py`
- `worldcup/league_runner.py`
- `worldcup/http_app.py` 及任何公开 API / preview / static export 投影
- `worldcup/csl_results_refresh.py` 的双源接受契约
- `worldcup/postmatch_publish.py` 的世界杯赛后发布链
- 任何 LaunchAgent、`.env`、quota ledger、ECS 或数据库 schema

---

## Task 1: Expose auditable closing metadata without breaking callers

**Files:**

- Modify: `worldcup/csl_eval_data.py`
- Modify: `tests/test_csl_eval_data.py`
- Test: `tests/test_csl_eval_data.py`

### Interfaces

Add this immutable return type and selector:

```python
@dataclass(frozen=True)
class ClosingMatch:
    entry: dict[str, Any]
    snapshot_at: str
    snapshot_run_id: str | None


def closing_match(
    snapshots: list[dict[str, Any]],
    match_date: str,
    home_canonical: str,
    away_canonical: str,
    competition_id: str | None = None,
) -> ClosingMatch | None:
    """Return the latest strictly pre-kickoff matching entry and its snapshot metadata."""
```

Keep the old public interface as a compatibility wrapper:

```python
def closing_match_entry(
    snapshots: list[dict[str, Any]],
    match_date: str,
    home_canonical: str,
    away_canonical: str,
    competition_id: str | None = None,
) -> dict[str, Any] | None:
    selected = closing_match(
        snapshots,
        match_date,
        home_canonical,
        away_canonical,
        competition_id=competition_id,
    )
    return selected.entry if selected is not None else None
```

`snapshot_run_id` comes from `(snapshot.get("run") or {}).get("run_id")`; missing run metadata is valid and yields `None`. `snapshot_at` is kept as the original normalized string used by the source snapshot, while comparison continues to use `_parse_utc()`.

### Steps

- [ ] Add focused tests before implementation:

```python
def test_closing_match_returns_latest_prematch_snapshot_metadata():
    selected = closing_match(
        [
            _snapshot("2026-08-09T10:00:00Z", run_id="early"),
            _snapshot("2026-08-09T10:55:00Z", run_id="closing"),
            _snapshot("2026-08-09T11:01:00Z", run_id="post-kickoff"),
        ],
        "2026-08-09",
        "shandong_taishan",
        "changchun_yatai",
        competition_id="csl_2026",
    )

    assert selected is not None
    assert selected.snapshot_at == "2026-08-09T10:55:00Z"
    assert selected.snapshot_run_id == "closing"
    assert selected.entry["home_canonical"] == "shandong_taishan"


def test_closing_match_entry_remains_a_dict_compatibility_wrapper():
    selected = closing_match(
        snapshots,
        "2026-08-09",
        "shandong_taishan",
        "changchun_yatai",
        competition_id="csl_2026",
    )
    assert selected is not None
    assert closing_match_entry(
        snapshots,
        "2026-08-09",
        "shandong_taishan",
        "changchun_yatai",
        competition_id="csl_2026",
    ) == selected.entry
```

- [ ] Add a parameterized rejection test covering:
  - `snapshot_at == kickoff_at_utc` and `snapshot_at > kickoff_at_utc`;
  - `fixture_status=POSTPONED`;
  - a different `competition.id`;
  - reversed home/away canonical identities;
  - same clubs on a different UTC kickoff date.
- [ ] Run the focused test and confirm the expected red state is only the missing `ClosingMatch` / `closing_match` API:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v tests.test_csl_eval_data
```

Expected before implementation: import or assertion failure for the new API. Existing closing tests must remain green.

- [ ] Implement `ClosingMatch` and move the existing selection loop into `closing_match()` without changing its matching predicates.
- [ ] Replace only the body of `closing_match_entry()` with the compatibility wrapper.
- [ ] Re-run the focused tests.

Expected after implementation: all `tests.test_csl_eval_data` tests pass; no existing caller change is required.

- [ ] Run a caller regression because `build_rows()` still uses `closing_match_entry()`:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v tests.test_csl_postmatch_runner
```

- [ ] Commit checkpoint, only if separate commit authorization is active:

```bash
git add worldcup/csl_eval_data.py tests/test_csl_eval_data.py
git commit -m "refactor: expose csl closing metadata"
```

---

## Task 2: Build the pure decision-only shadow report

**Files:**

- Create: `worldcup/csl_postmatch_shadow.py`
- Create: `tests/test_csl_postmatch_shadow.py`
- Test: `tests/test_csl_postmatch_shadow.py`

### Public-safe decision projection

Use an explicit allowlist; never copy the complete match entry or `market` block:

```python
SAFE_DECISION_FIELDS = (
    "schema_version",
    "policy_version",
    "label",
    "market",
    "selection",
    "line",
    "odds",
    "p_hit",
    "p_hit_safe",
    "p_no_loss_safe",
    "evidence_score",
    "uncertainty_penalty",
    "selected_option_id",
    "method",
    "computed_at",
    "odds_latest_at",
    "valid_until",
    "reasons",
    "risks",
)


def project_decision(decision: Any) -> dict[str, Any] | None:
    if not isinstance(decision, dict):
        return None
    return {key: decision[key] for key in SAFE_DECISION_FIELDS if key in decision}
```

The selected single price may be retained as `odds` for reference-price buckets. Provider payloads, per-book rows, API request metadata, source URLs, secrets and bankroll fields are forbidden recursively.

### Pure report interface

```python
SETTLEMENT_CONTRACT = "decision_settlement_v1"
REPORT_SCHEMA_VERSION = 1
DEFAULT_MIN_SAMPLE = 50


def build_shadow_report(
    snapshots: list[dict[str, Any]],
    results: list[ClubResult],
    *,
    generated_at: str,
    competition_id: str = "csl_2026",
    season: str = "2026",
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> dict[str, Any]:
    """Build one deterministic decision-only CSL postmatch report in memory."""
```

Each matched row has this stable shape:

```python
{
    "match_id": "csl_2026:2026-08-09:shandong_taishan:changchun_yatai",
    "competition_id": "csl_2026",
    "season": "2026",
    "kickoff_at_utc": "2026-08-09T11:00:00Z",
    "home_team": "山东泰山",
    "away_team": "长春亚泰",
    "home_canonical": "shandong_taishan",
    "away_canonical": "changchun_yatai",
    "closing_snapshot_at": "2026-08-09T10:55:00Z",
    "closing_snapshot_run_id": "20260809T105500Z-csl-live",
    "closing_match_decision": {
        "schema_version": 2,
        "policy_version": "match_pick_v3",
        "label": "MATCH_PICK",
        "market": "OU",
        "selection": "over",
        "line": 2.5,
        "p_hit_safe": 0.56,
    },
    "result": {"home_score": 2, "away_score": 1},
    "settlement": {
        "status": "hit",
        "label": "命中",
        "detail": "全场 2-1",
        "settlement_class": "full_win",
    },
}
```

Missing-closing results remain in `matches` as diagnostic rows with `closing_snapshot_at=None`, `closing_match_decision=None`, `settlement={"status": "missing_closing", "label": "缺少封盘快照", "detail": ""}`. They are excluded from the records passed to `summarize_decision_records()` and counted via its `skipped_no_closing` argument.

### Fingerprint projection

The fingerprint must be independent of `generated_at`, file path, dictionary insertion order, older replay seasons and unrelated history:

```python
def input_fingerprint(
    audit_rows: list[dict[str, Any]],
    competition_id: str,
    season: str,
) -> str:
    payload = {
        "competition_id": competition_id,
        "season": season,
        "settlement_contract": SETTLEMENT_CONTRACT,
        "matches": [
            {
                "match_id": row["match_id"],
                "result": row["result"],
                "closing_snapshot_at": row.get("closing_snapshot_at"),
                "closing_snapshot_run_id": row.get("closing_snapshot_run_id"),
                "closing_match_decision": row.get("closing_match_decision"),
            }
            for row in sorted(audit_rows, key=lambda item: item["match_id"])
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

This intentionally includes current-season results with missing closing, so accepting a new 2026 finished result changes the fingerprint even before its coverage gap is fixed. It intentionally excludes older replay seasons and snapshots that no accepted current-season result selected. `build_shadow_report()` must filter with `result.season == season` before building rows.

### Aggregation contract

Map the canonical summary without renaming its meaning:

```python
canonical = summarize_decision_records(
    matched_records,
    min_sample=min_sample,
    skipped_no_closing=missing_closing_count,
)

report = {
    "schema_version": REPORT_SCHEMA_VERSION,
    "competition_id": competition_id,
    "season": season,
    "generated_at": generated_at,
    "input_fingerprint": input_fingerprint(rows, competition_id, season),
    "settlement_contract": SETTLEMENT_CONTRACT,
    "status": "ok",
    "decision_sample": canonical["sample"],
    "decision_tally": canonical["decision_tally"],
    "decision_coverage": {
        **canonical["coverage"],
        "identity_mismatch_count": 0,
        "result_source_blocked_count": 0,
    },
    "breakdowns": _build_breakdowns(rows, min_sample=min_sample),
    "calibration": _build_calibration(rows, min_sample=min_sample),
    "matches": rows,
    "warnings": ["sample_too_small"] if canonical["sample"]["sample_too_small"] else [],
    "research_notice": "仅用于研究分析，不构成投注建议。",
}
```

Only identity-normalized `ClubResult` rows enter this function, so `identity_mismatch_count` is zero here. Source failures never call this builder; scheduled integration records them in its safe run summary, while the last successful canonical report/state remain unchanged.

Breakdowns are arrays sorted by bucket key. Each item must include at least `bucket`, `sample`, `hit`, `miss`, `push`, `no_pick`, `hit_rate`, and `sample_too_small`. Implement these exact groups:

Breakdowns only consume schema-v2 decisions. `MATCH_PICK` and `NO_CLEAN_MARKET` may appear; legacy, missing and invalid decisions stay coverage-only and cannot enter grouped hit rate.

- `market`: `1X2` / `OU` / `AH` / `DNB` / `missing`;
- `selection`: normalized decision selection;
- `line`: exact finite line serialized with compact decimal formatting, otherwise `missing`;
- `reference_odds`: `<1.60`, `1.60-1.79`, `1.80-1.99`, `2.00-2.24`, `>=2.25`, `missing`;
- `p_hit_safe`: `<0.50`, `0.50-0.54`, `0.55-0.59`, `0.60-0.64`, `>=0.65`, `missing`;
- `evidence_score`: `<0.50`, `0.50-0.69`, `0.70-0.84`, `>=0.85`, `missing`;
- `risk_flags`: one row per individual `risks` value plus `none`; this is a multi-membership diagnostic, so its samples are not summed as a partition.
- `bookmaker_coverage_risk`: `thin_market` when that exact risk flag is present, otherwise `not_flagged`;
- `dispersion_risk`: `severe_dispersion` when that exact risk flag is present, otherwise `not_flagged`.

Calibration only uses settled current-strategy `hit`/`miss` rows that have finite `p_hit_safe` in `[0, 1]`:

```python
brier = sum((probability - actual) ** 2 for probability, actual in points) / len(points)
```

Return `sample`, `brier_score`, `sample_too_small`, and the same `p_hit_safe` buckets with `mean_predicted`, `actual_hit_rate`, `sample`, `hit`, `miss`.

### Steps

- [ ] Add a frozen synthetic fixture covering 1X2, OU, AH and DNB, plus no-pick, legacy, missing decision, invalid line and missing closing.
- [ ] Include one 2025 replay row in the fixture and assert it affects neither 2026 coverage nor fingerprint; it remains available to the existing pending-gate call in Task 3.
- [ ] Add the August 9 minimal fixture with three `OU / over / 2.5` decisions and scores that settle to exactly `2 hit / 1 miss`; keep it synthetic and independent of the growing ignored production files.
- [ ] Add red tests asserting:
  - all per-match settlement results equal direct calls to `settle_match_decision()`;
  - official tally comes from current schema v2 decisions only;
  - legacy/missing/invalid/missing-closing counts stay separated;
  - every breakdown carries an explicit sample and low-sample marker;
  - calibration Brier and bucket rates equal hand-computed values;
  - report recursively excludes keys containing `api_key`, `secret`, `bankroll`, `stake`, `bookmakers`, `provider_payload` and `raw_odds`.
- [ ] Add fingerprint tests:

```python
def test_fingerprint_ignores_generated_at_and_unselected_future_snapshots():
    first = build_shadow_report(base_snapshots, results, generated_at="2026-08-12T00:00:00Z")
    second = build_shadow_report(
        [*base_snapshots, unrelated_future_snapshot],
        results,
        generated_at="2026-08-12T01:00:00Z",
    )
    assert first["input_fingerprint"] == second["input_fingerprint"]


def test_fingerprint_changes_for_score_or_selected_closing_decision():
    assert report_for(changed_score)["input_fingerprint"] != baseline_fingerprint
    assert report_for(changed_selected_decision)["input_fingerprint"] != baseline_fingerprint
```

- [ ] Run the new test module and confirm it fails only because the module/API is not implemented:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v tests.test_csl_postmatch_shadow
```

- [ ] Implement only pure helpers and `build_shadow_report()` in this task. Do not add writing or CLI behavior yet.
- [ ] Re-run `tests.test_csl_postmatch_shadow` and the existing settlement coverage in `tests.test_finished_record`.

Expected: exact synthetic tallies pass; no production data is read or written.

- [ ] Commit checkpoint, only if separately authorized:

```bash
git add worldcup/csl_postmatch_shadow.py tests/test_csl_postmatch_shadow.py
git commit -m "feat: build csl postmatch shadow report"
```

---

## Task 3: Add dry-run, staged artifacts, idempotency and best-effort rollback

**Files:**

- Modify: `worldcup/csl_postmatch_shadow.py`
- Modify: `tests/test_csl_postmatch_shadow.py`
- Test: `tests/test_csl_postmatch_shadow.py`
- Test: `tests/test_csl_postmatch_runner.py`

### Runner interface and defaults

```python
DEFAULT_SHADOW_REPORT = "data/local/diagnostics/csl_postmatch_shadow.json"
DEFAULT_SHADOW_STATE = "data/local/diagnostics/csl_postmatch_shadow_state.json"
DEFAULT_GATE_OUT = "data/local/diagnostics/csl_pending_gate_latest.json"
ACCEPTED_SOURCE_STATUSES = {"updated", "verified"}


def run_csl_postmatch_shadow(
    *,
    root: str | Path = ".",
    history: str | Path = csl_postmatch_runner.DEFAULT_HISTORY,
    results: str | Path = csl_postmatch_runner.DEFAULT_RESULTS,
    shadow_report: str | Path = DEFAULT_SHADOW_REPORT,
    state_path: str | Path = DEFAULT_SHADOW_STATE,
    eval_out: str | Path = csl_postmatch_runner.DEFAULT_EVAL_OUT,
    backtest_out: str | Path = csl_postmatch_runner.DEFAULT_REPORT_OUT,
    gate_out: str | Path = DEFAULT_GATE_OUT,
    competition_id: str = "csl_2026",
    season: str = "2026",
    generated_at: str | None = None,
    source_status: str = "verified",
    write: bool = False,
    decision_min_sample: int = 50,
    backtest_min_sample: int = 30,
    warmup_matches: int = 300,
    min_eval_matches: int = 200,
    config: str | Path | None = None,
    postmatch_fn: Callable[..., dict[str, Any]] = csl_postmatch_runner.run_postmatch,
) -> dict[str, Any]:
    """Compute a candidate or commit one validated local shadow bundle."""
```

Safe return values:

- `dry_run_ready`: candidate computed, no file written;
- `stored`: new fingerprint committed;
- `unchanged`: existing successful fingerprint and canonical hash still match;
- `blocked`: `source_status` is not `updated`/`verified`, no report or auxiliary artifact changes;
- `error`: only the scheduled wrapper emits this when it catches an exception.

The summary may include counts, status, fingerprint prefix and relative/local output paths. It must not include complete decisions, scores, per-book data, exception messages, secrets or environment values.

### State contract

```json
{
  "schema_version": 1,
  "competition_id": "csl_2026",
  "season": "2026",
  "last_success": {
    "input_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "succeeded_at": "2026-08-12T10:00:00Z",
    "decision_count": 35,
    "decided": 35,
    "canonical_report_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "last_attempt": {
    "attempted_at": "2026-08-12T10:00:00Z",
    "status": "stored|unchanged|blocked|error",
    "reason": null,
    "error_type": null
  }
}
```

`unchanged` and `blocked` atomically update only the state `last_attempt`, preserving `last_success`; they must never rewrite the canonical report or auxiliary artifacts. If even this diagnostic state write fails, propagate to the scheduled wrapper, which will downgrade it to a warning.

Any generation, validation or promotion exception must first attempt one atomic state-only update with `last_attempt.status="error"`, a stable generic reason (`shadow_generation_failed`, `shadow_validation_failed` or `shadow_commit_failed`) and `error_type=type(exc).__name__`, while preserving `last_success`. Never persist `str(exc)`, traceback text or source payloads. After the best-effort diagnostic update, re-raise the original exception so the scheduled wrapper can enforce the non-blocking boundary.

### Staging and promotion

- Resolve every configured path under `root` using the existing relative/absolute semantics.
- Validate all target paths are distinct after `Path.resolve(strict=False)`; fail closed with `ValueError("shadow_output_path_collision")` on overlap.
- Create the staging directory under the canonical report parent so staged report and its final atomic replace are on the same filesystem.
- Call the existing `run_postmatch()` with staging paths for eval CSV, backtest JSON and pending gate JSON.
- Build and write the shadow report in staging.
- Cross-check before promotion:
  - staged files exist and are non-empty;
  - staged JSON files parse;
  - `postmatch_summary["joined"] == report["decision_coverage"]["closing_available_count"]`;
  - the set of staged eval CSV `match_id` values exactly equals the set of report rows with a non-null `closing_snapshot_at`;
  - every report match came from `result.season == season`;
  - report fingerprint equals the precomputed candidate fingerprint.

`postmatch_summary["results"]` is deliberately not compared with shadow `finished_result_count`: the existing runner loads all replay seasons so the pending gate can retain its 2023–2026 rating sample, while the shadow report is a 2026 decision contract.
- Build target bytes before touching any canonical file.
- Promote in this exact order: eval CSV, backtest report, pending gate, canonical shadow report, state.
- For every existing target, first copy bytes to a uniquely named sibling backup. Each promotion uses sibling temp + flush + `os.fsync()` + `os.replace()`.
- If any promotion fails, restore all already promoted files in reverse order from backups; delete newly created targets that had no prior version; do not swallow the original exception.
- Always clean staging and backup files in `finally`.
- The state embeds the SHA-256 of the exact canonical report bytes and is promoted last.

This is a best-effort local bundle commit, not a database transaction. The state/report hash pair is the authority for consumers.

### Idempotency

Before staging `run_postmatch()`:

1. Build the pure shadow candidate and fingerprint.
2. Load state and canonical report safely.
3. Return `unchanged` only when all are true:
   - `state.last_success.input_fingerprint == candidate.input_fingerprint`;
   - canonical report bytes hash equals `state.last_success.canonical_report_sha256`;
   - canonical report parses and carries the same input fingerprint.
4. Before returning `unchanged`, atomically advance only `state.last_attempt`; do not call `run_postmatch()` or rewrite report/eval/backtest/gate.
5. If the fingerprint matches but canonical/state hash is missing or wrong, regenerate and repair instead of skipping.

`generated_at` changes alone must not rewrite the canonical report or auxiliary outputs. The state mtime may advance because `last_attempt` is an explicit diagnostic field; its `last_success` block and canonical hash must remain byte-for-byte equivalent after JSON parsing.

### CLI

Add `main()` with `allow_abbrev=False` and these arguments:

```text
--root
--history
--results
--shadow-report
--state-path
--eval-out
--backtest-out
--gate-out
--competition-id / --competition
--season
--generated-at
--source-status
--decision-min-sample
--backtest-min-sample
--warmup-matches
--min-eval-matches
--config
--write
```

No `--live` exists because this module has no network mode. Default invocation is dry-run.

### Steps

- [ ] Add a recursive snapshot of all files under a temporary root, then test default runner and default CLI leave that snapshot unchanged.
- [ ] Add a first-write test that asserts all five target files exist, report hash equals state hash, and the existing postmatch runner summary agrees with shadow coverage.
- [ ] Add a repeated-write test with a different `generated_at`; assert status `unchanged`, hashes and `st_mtime_ns` for report/eval/backtest/gate remain unchanged, state changes only in `last_attempt`, `last_success` is equal, and `postmatch_fn` is not called again.
- [ ] Add corruption recovery tests for missing state, wrong state hash and malformed canonical report; each must regenerate instead of returning `unchanged`.
- [ ] Add blocked-source tests asserting canonical/auxiliary hashes are unchanged and only `last_attempt.status=blocked` may advance.
- [ ] Add path-collision and malformed-input fail-closed tests.
- [ ] Add fault-injection tests by patching the atomic replacement helper to fail at each promotion position. Seed distinct old bytes first and assert report/eval/backtest/gate return to their exact old bytes. State must preserve the exact old `last_success`; it may either retain the old `last_attempt` when the injected fault also blocks diagnostic persistence, or advance only to the safe error attempt. Assert no new success fingerprint is committed.
- [ ] Run the test module and confirm the new cases fail before persistence implementation.
- [ ] Implement path resolution, report/state hashing, atomic single-file writes, staging validation, ordered promotion and rollback.
- [ ] Implement idempotency before `run_postmatch()` and the safe status summary.
- [ ] Implement CLI and test stdout recursively for forbidden secret/provider/raw-price fields.
- [ ] Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v \
  tests.test_csl_postmatch_shadow \
  tests.test_csl_postmatch_runner \
  tests.test_csl_eval_data
```

Expected: all tests pass; no file outside each temporary test root changes.

- [ ] Commit checkpoint, only if separately authorized:

```bash
git add worldcup/csl_postmatch_shadow.py tests/test_csl_postmatch_shadow.py
git commit -m "feat: persist csl shadow reports safely"
```

---

## Task 4: Integrate shadow as a non-blocking scheduled side effect

**Files:**

- Modify: `worldcup/csl_scheduled_publish.py`
- Modify: `tests/test_csl_scheduled_publish.py`
- Test: `tests/test_csl_scheduled_publish.py`

### Injection boundary

At module level:

```python
from worldcup.csl_postmatch_shadow import run_csl_postmatch_shadow

PostmatchShadowFn = Callable[..., dict[str, Any]]
```

Extend `run_csl_scheduled_publish()` with:

```python
postmatch_shadow_fn: PostmatchShadowFn = run_csl_postmatch_shadow,
postmatch_shadow_root: str | Path = ".",
```

Compute `history_path` before the result refresh so the same resolved history path is passed both to shadow and the later archive operation.

### Call order and arguments

Immediately after the `_safe_results_refresh` call and before the `refresh_fn` call, invoke the runner only for an accepted result-source status:

```python
result_source_status = str(results_refresh.get("status") or "error")
if result_source_status in {"updated", "verified"}:
    try:
        postmatch_shadow = _safe_postmatch_shadow(
            postmatch_shadow_fn(
                root=postmatch_shadow_root,
                history=history_path,
                results=Path(cache_dir) / f"club_results_{DEFAULT_COMPETITION_ID}.csv",
                competition_id=DEFAULT_COMPETITION_ID,
                season="2026",
                generated_at=observed,
                source_status=result_source_status,
                write=True,
            )
        )
    except Exception as exc:
        postmatch_shadow = {
            "status": "error",
            "reason": "csl_postmatch_shadow_failed",
            "error_type": type(exc).__name__,
        }
else:
    postmatch_shadow = {
        "status": "blocked",
        "reason": "result_source_not_accepted",
    }
```

`_safe_postmatch_shadow()` must expose only:

```python
SAFE_SHADOW_KEYS = {
    "status",
    "reason",
    "competition_id",
    "results",
    "closing_available",
    "decided",
    "sample_too_small",
    "input_fingerprint_prefix",
    "error_type",
}
```

The broad `Exception` boundary is intentional because shadow must never block the product chain for an unexpected local parser, validation or persistence exception. It still does not catch `BaseException`, `KeyboardInterrupt` or `SystemExit`, and it never includes exception text.

### Result propagation

- Add `postmatch_shadow` to every return body after its call point, including odds-refresh blocked/error, empty snapshot, publish-pending and published.
- Pre-refresh early exits remain unchanged and must not call shadow:
  - scheduled dry-run;
  - missing/weak HMAC secret;
  - pending publish retry;
  - not-due skip.
- If `postmatch_shadow.status == "error"`, append `csl_postmatch_shadow_failed` to the newly built snapshot's `data_quality.warnings` exactly once.
- `blocked` due to result-source status does not add a second warning; existing `club_results_refresh_failed` / stale-source diagnostics already explain the source failure.
- A shadow failure must not change the later odds refresh, snapshot build, archive, HMAC publish, pending retry or quota behavior.

### Steps

- [ ] Update every existing live scheduled-publish test to inject a fake shadow function. This prevents the production default from writing project-local ignored files during tests.
- [ ] Extend the successful live test with call-order recording:

```python
events = []

def fake_results_refresh(**kwargs):
    events.append("results")
    return {"status": "updated"}

def fake_shadow(**kwargs):
    events.append("shadow")
    assert kwargs["write"] is True
    assert kwargs["source_status"] == "updated"
    return {"status": "stored", "decided": 3}

def fake_refresh(**kwargs):
    events.append("odds")
    return {
        "status": "fetched",
        "events": 1,
        "quota_entry": {"remaining": 197, "used": 303, "last": 3},
        "theoddsapi_provider": "theoddsapi_secondary",
    }

assert events[:3] == ["results", "shadow", "odds"]
```

- [ ] Add a source-blocked test injecting a shadow function that raises if called; assert the function is not called, the scheduled result contains `postmatch_shadow.status="blocked"`, and odds publish still proceeds with the existing results warning.
- [ ] Add a shadow-exception test asserting:
  - publish still returns `published`;
  - `refresh_fn` and `publish_fn` each run once;
  - output snapshot includes `csl_postmatch_shadow_failed` exactly once;
  - returned diagnostic contains only the safe error type/code, not exception text.
- [ ] Add an odds-refresh-failure test asserting shadow ran before the failure and its summary is still returned.
- [ ] Extend the pending-retry test to inject a shadow function that raises if called on the second invocation; assert retry does not rerun results or shadow and does not consume refresh/quota.
- [ ] Run the scheduled publish tests and confirm they fail before integration.
- [ ] Implement the injected boundary, safe summary, call order, return propagation and warning behavior.
- [ ] Re-run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v tests.test_csl_scheduled_publish
```

Expected: existing scheduling/publish behavior stays green; new non-blocking cases pass.

- [ ] Run the adjacent source and archive regressions:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest -v \
  tests.test_csl_results_refresh \
  tests.test_csl_snapshot_archive \
  tests.test_csl_scheduled_publish
```

- [ ] Commit checkpoint, only if separately authorized:

```bash
git add worldcup/csl_scheduled_publish.py tests/test_csl_scheduled_publish.py
git commit -m "feat: run csl shadow after verified results"
```

---

## Task 5: Document the operating contract and run offline acceptance

**Files:**

- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`
- Verify only: ignored `data/local/diagnostics/csl_postmatch_shadow.json`
- Verify only: ignored `data/local/diagnostics/csl_postmatch_shadow_state.json`
- Verify only: ignored `data/local/backtest/csl_2026_eval.csv`
- Verify only: ignored `data/local/backtest/csl_2026_report.json`
- Verify only: ignored `data/local/diagnostics/csl_pending_gate_latest.json`

### Documentation content

Add the following operational command near the existing `csl_postmatch_runner` section:

```bash
# 默认只读：计算 shadow 候选摘要，不写任何产物
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_shadow

# 显式本地写入：原子更新 ignored shadow/eval/backtest/gate/state
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_shadow --write
```

Document these boundaries in prose:

- scheduled publish only calls it after the existing result refresh step and treats errors as warnings;
- it performs no network access and no The Odds API request;
- outputs are local ignored diagnostics, not public `/api/finished` data;
- state/report hash pair identifies a complete successful bundle;
- low sample means observation only, not automatic tuning or lifting `club_rating_pending`.

Synchronize this compact project rule into both `AGENTS.md` and `CLAUDE.md`:

```text
- 中超已接受的双源赛果刷新后运行本地非阻断 postmatch shadow；只用开赛前最后合法 closing 和当前 schema v2 首选结算，通过指纹幂等更新 ignored shadow/eval/backtest/gate 产物。shadow 失败只记 warning，不阻断赛前刷新/发布，不消耗 The Odds API quota，不进公开 API，不自动调参或解除 `club_rating_pending`。
```

Before editing these dirty documentation files, inspect `git diff -- <file>` and patch around existing changes. Do not restore or reflow unrelated user content.

### Offline acceptance steps

- [ ] Run default dry-run against the real ignored local cache/history and snapshot before/after hashes for all five target outputs.

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_shadow
```

Expected: `status=dry_run_ready`; before/after hashes and mtimes are identical; no `.env` or network access occurs.

- [ ] After the implementation stage has explicit local-write authorization, run the same real-data input with `--write` once.

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m worldcup.csl_postmatch_shadow --write
```

Expected for the frozen 2026-08-12 baseline if no newer accepted result has appeared:

- current-strategy `decided=35`;
- `hit=17`, `miss=18`, `push=0`;
- 2026-08-09 has three `OU / over / 2.5` rows and settles to `2 hit / 1 miss`;
- `decision_sample.sample_too_small=true` because the implementation threshold is 50;
- no legacy decision is mixed into those 35 decided rows.

If current local results have legitimately grown, do not force the report back to 35. Instead compare every added result and selected closing, document the new actual tally, and retain the frozen synthetic 35/17/18 regression fixture as the historical baseline.

- [ ] Immediately run `--write` again with a later `--generated-at` and assert `status=unchanged`, identical hashes/mtimes for report/eval/backtest/gate, and a state-only `last_attempt` advance with unchanged `last_success`.
- [ ] Parse report/state and independently verify:

```python
assert sha256(report_bytes).hexdigest() == state["last_success"]["canonical_report_sha256"]
assert report["input_fingerprint"] == state["last_success"]["input_fingerprint"]
assert report["decision_tally"] == recomputed_tally_from_report_matches
```

- [ ] Scan report/state recursively for secrets, raw bookmaker structures and money/execution fields. Treat any hit as blocking.
- [ ] Confirm `git status --short` does not show any `data/local/` or `data/cache/` artifact.

### Full verification

- [ ] Compile only changed Python modules:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile \
  worldcup/csl_eval_data.py \
  worldcup/csl_postmatch_shadow.py \
  worldcup/csl_scheduled_publish.py
```

- [ ] Run the complete configured test suite:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected baseline before implementation was `951/951` with one optional FastAPI module skipped. Final result must have zero new failures; report the new exact count instead of copying the baseline.

- [ ] Run whitespace validation:

```bash
git diff --check
```

- [ ] Run targeted contract scans:

```bash
rg -n "csl_postmatch_shadow|postmatch_shadow" worldcup tests README.md AGENTS.md CLAUDE.md
rg -n "csl_postmatch_shadow|csl_2026_eval|csl_pending_gate_latest" .gitignore
git status --short
```

Expected: no public API/UI file modified, no `.env`/LaunchAgent/quota/deploy file modified, and ignored runtime artifacts are absent from Git status.

- [ ] Append one concise implementation/verification record to `RECENT_WORK.md`; do not clean its existing history.
- [ ] Commit documentation checkpoint only if separately authorized:

```bash
git add README.md AGENTS.md CLAUDE.md RECENT_WORK.md
git commit -m "docs: record csl postmatch shadow operations"
```

---

## Task 6: Adversarial review before handoff

**Files:**

- Review: all files changed by Tasks 1-5
- Verify: current git diff and ignored local outputs

### Review checklist

- [ ] **Goal coverage:** prove one accepted result set maps deterministically to one closing/settlement/report bundle and that every design output exists.
- [ ] **Closing integrity:** inspect at least one normal match, one excluded post-kickoff snapshot, one postponed/rescheduled case and one missing-closing row.
- [ ] **Settlement authority:** confirm there is no duplicated 1X2/OU/AH/DNB result logic outside `decision_settlement.py`.
- [ ] **Sample integrity:** confirm `hit_rate` denominator excludes legacy, no-pick, missing, invalid, unresolved and push exactly as the canonical summarizer defines.
- [ ] **Fingerprint integrity:** confirm a new accepted score or selected closing changes the fingerprint, while `generated_at` and unrelated future snapshots do not.
- [ ] **Atomicity claim:** inject promotion failure after at least one auxiliary file has moved; verify rollback/state behavior and ensure docs call this best-effort, not transactional.
- [ ] **Non-blocking behavior:** prove a shadow exception does not change odds refresh/publish call counts or final publish status.
- [ ] **Side-effect boundary:** default dry-run must not read `.env`, call network, write any file, consume quota, notify or publish.
- [ ] **Scope boundary:** inspect `git diff --name-only`; reject any unplanned model, API, preview, static export, deployment, database, secret or LaunchAgent change.
- [ ] **Data safety:** recursively scan runtime report/state and scheduled summaries for provider payloads, per-book rows, secrets, account/payment/amount fields and raw exception strings.
- [ ] **Real-data overclaim:** if the sample is below 50, ensure outputs say observation/sample-too-small and contain no tuning recommendation.
- [ ] **Rollback:** document that disabling/removing the injected scheduled call restores old behavior; ignored diagnostics can remain and need no migration.

### Blocking findings and revision rule

Any finding involving closing contamination, score/source bypass, public API leakage, quota/network side effects, canonical/state hash mismatch, failed rollback, or changed match-pick semantics blocks completion. Fix it under the same TDD cycle, rerun focused tests and the full suite, then recheck only the blocking finding plus the full side-effect boundary.

### Final completion evidence

Handoff must report:

- changed files;
- focused and full test counts;
- dry-run zero-write proof;
- real local sample/tally at execution time, explicitly labelled observation;
- report/state hash agreement;
- repeated-run `unchanged` proof;
- whether any shadow warning occurred;
- explicit confirmation that no network/quota/public API/deploy/LaunchAgent/model change occurred;
- remaining phase-2 observation requirement: at least 7 days and coverage of the 2026-08-14 to 2026-08-15 matchday before any public promotion decision.

- [ ] Commit final code/test fixes only if separately authorized; never push, merge or deploy without the corresponding later authorization.
