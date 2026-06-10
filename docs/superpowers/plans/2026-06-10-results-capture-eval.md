# 赛果回填 + 自有赔率历史评估链路 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 世界杯期间自动积累"自有收盘赔率 + 赛果"数据：每次 live refresh 把 snapshot 归档到本地历史目录；新增赛果采集 CLI 从已缓存的 openfootball 数据提取完赛比分；新增评估数据构建 CLI 把"赛前最后一份归档 snapshot 的赔率 + 赛果"join 成带赔率的回测 CSV——从而让现有 `worldcup.backtest` 的 EV 分层、`model_matched` vs `market` 对比在真实自有数据上可用（这是免费拿不到的数据）。

**Architecture:** 三个独立环节：(1) `refresh_runner` 在写完 `data/cache/analysis_snapshot.json` 后追加归档一份到被忽略的 `data/local/history/snapshot_<run_id>.json`（失败不阻断发布链路）；(2) `worldcup/results_capture.py` 解析 `data/cache/openfootball_2026.json` 中已完赛比分（openfootball 完赛后会出现 `score1/score2`），幂等 upsert 到 `data/local/results/wc2026_results.csv`；(3) `worldcup/eval_data.py` 对每条赛果挑选"开球前最后一份"归档 snapshot，提取 Elo 与 1X2/OU 聚合赔率，产出回测契约 CSV。引擎与线上 API 零改动。

**Tech Stack:** 纯标准库 Python，无新依赖；测试用 `tests/run_tests.py` 纯函数断言风格。

---

## 背景与约束

- 基于最新 `main` 新建分支（需包含回测框架与 `--sweep`；与 `2026-06-10-mu-dr-prior.md` 计划相互独立，谁先合并都不冲突——若同分支连续执行，本计划在后即可）。
- **归档环节改动 live refresh 链路，是本计划唯一碰生产路径的点**，必须满足：归档失败只打 stderr 警告、不抛异常、不影响 snapshot 写入与发布；归档目录 `data/local/history/` 已被 gitignore（`data/local/` 整体忽略）。
- macmini 的 LaunchAgent 直接运行本仓库代码，**合并进本机 `main` 后下一次 live refresh 即开始归档，无需 ECS 部署**（本计划不改服务端任何代码）。
- openfootball 完赛比分字段：标准格式是顶层 `score1`/`score2` 整数（90 分钟比分；加时/点球在 `score1et`/`score1p` 等字段，本计划只取 90 分钟，与现有 `1X2_90min` 市场对齐）；解析器同时兼容 `score: {"ft": [a, b]}` 形态。当前缓存里 2026 比赛尚未开打、无比分字段，解析器必须把"无比分"当未完赛跳过，不报错。
- 评估 CSV 的 `neutral` 一律写 1（snapshot 未保存东道主修正信息；美墨加主场场次的模型重放会与生产略有出入，作为已知局限写入文档，不在本计划修）。
- AH 赔率不进评估 CSV（snapshot 的 `market` 块只有 1X2 与 OU 聚合赔率，AH 仅在 signals 里且不含赔率），首版只评 1X2 + OU。
- 不触发 live refresh、不调用 The Odds API、不改 scheduler/ingest/HMAC/store schema、不改 LaunchAgent plist。
- 验证命令（一次跑全部测试）：

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- 本项目允许本地 commit，不允许 push。

## 文件结构

| 文件 | 操作 | 职责 |
|---|---|---|
| `worldcup/refresh_runner.py` | 修改 | live refresh 后归档 snapshot 到 `data/local/history/` |
| `worldcup/collectors/models.py` | 修改 | 新增 `MatchResult` dataclass |
| `worldcup/collectors/openfootball.py` | 修改 | 新增 `parse_openfootball_results`（含 `_extract_score`） |
| `worldcup/results_capture.py` | 新建 | 完赛比分幂等 upsert CSV + CLI |
| `worldcup/eval_data.py` | 新建 | 归档 snapshot × 赛果 → 带赔率回测 CSV + CLI |
| `tests/test_refresh_runner.py` | 修改 | 两处调用补 `history_dir`，归档断言 |
| `tests/collectors/test_openfootball_results.py` | 新建 | 比分解析测试 |
| `tests/test_results_capture.py` | 新建 | upsert 幂等/更新测试 |
| `tests/test_eval_data.py` | 新建 | 收盘选择 + roundtrip 测试 |
| `README.md`、`RECENT_WORK.md` | 修改 | 运行手册与近期记录 |

---

### Task 1: refresh 后归档 snapshot

**Files:**
- Modify: `worldcup/refresh_runner.py`
- Test: `tests/test_refresh_runner.py`

- [ ] **Step 1: 写失败测试**

修改 `tests/test_refresh_runner.py` 中 `test_refresh_cache_and_build_snapshot_with_injected_transports` 的调用与断言：调用处加 `history_dir=root / "history"`（紧跟 `observed_at=` 之后），断言块末尾追加：

```python
        archive = root / "history" / "snapshot_20260608T000000Z-live.json"
        assert result.archive_path == archive
        assert archive.exists()
        assert json.loads(archive.read_text())["run"]["run_id"] == "20260608T000000Z-live"
```

同文件第二个测试 `test_refresh_uses_stale_odds_cache_when_theoddsapi_times_out` 的 `refresh_cache_and_build_snapshot(` 调用处同样补 `history_dir=root / "history"`（防止默认目录把归档写进真实 `data/local/history/`）。

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 第一个测试 FAIL（`TypeError: unexpected keyword argument 'history_dir'`）。

- [ ] **Step 3: 实现**

`worldcup/refresh_runner.py`：

3a. 顶部 import 区加 `import sys`。

3b. `RefreshResult` dataclass 末尾加字段：

```python
    archive_path: Path | None = None
```

3c. `refresh_cache_and_build_snapshot` 签名在 `observed_at` 参数之前加：

```python
    history_dir: str | Path = "data/local/history",
```

3d. `write_snapshot(snapshot, snapshot_output)` 之后、`return RefreshResult(` 之前插入：

```python
    archive_path: Path | None = None
    try:
        archive_dir = Path(history_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"snapshot_{run_metadata['run_id']}.json"
        write_snapshot(snapshot, archive_path)
    except OSError as exc:
        print(f"warning: snapshot archive failed: {exc}", file=sys.stderr)
        archive_path = None
```

3e. `return RefreshResult(` 的参数列表末尾加 `archive_path=archive_path,`。

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/refresh_runner.py tests/test_refresh_runner.py
git commit -m "feat: archive live snapshots to local history"
```

### Task 2: openfootball 完赛比分解析

**Files:**
- Modify: `worldcup/collectors/models.py`
- Modify: `worldcup/collectors/openfootball.py`
- Create: `tests/collectors/test_openfootball_results.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/collectors/test_openfootball_results.py`：

```python
from worldcup.collectors.openfootball import parse_openfootball_results

DOC = {
    "matches": [
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "13:00 UTC-6",
            "team1": "Mexico",
            "team2": "South Africa",
            "score1": 2,
            "score2": 1,
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-11",
            "time": "20:00 UTC-6",
            "team1": "South Korea",
            "team2": "Czech Republic",
        },
        {
            "round": "Matchday 1",
            "date": "2026-06-12",
            "time": "13:00 UTC-6",
            "team1": "Canada",
            "team2": "Bosnia and Herzegovina",
            "score": {"ft": [0, 0]},
        },
        {
            "round": "Round of 32",
            "date": "2026-06-29",
            "time": "13:00 UTC-6",
            "team1": "1A",
            "team2": "3C/D/F",
            "score1": 1,
            "score2": 0,
        },
    ]
}


def test_parse_results_extracts_only_finished_real_matches():
    results = parse_openfootball_results(DOC)
    assert len(results) == 2
    first = results[0]
    assert first.home_team_name == "Mexico"
    assert (first.home_score, first.away_score) == (2, 1)
    assert first.home_canonical == "mexico"
    second = results[1]
    assert second.home_team_name == "Canada"
    assert (second.home_score, second.away_score) == (0, 0)


def test_parse_results_keeps_kickoff_utc():
    results = parse_openfootball_results(DOC)
    assert results[0].kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（`parse_openfootball_results` 不存在）。

- [ ] **Step 3: 实现**

3a. `worldcup/collectors/models.py` 末尾追加：

```python
@dataclass(frozen=True)
class MatchResult:
    kickoff_at_utc: datetime
    home_team_name: str
    away_team_name: str
    home_canonical: str | None
    away_canonical: str | None
    home_score: int
    away_score: int
```

3b. `worldcup/collectors/openfootball.py`：import 行改为 `from worldcup.collectors.models import Fixture, MatchResult`，文件末尾追加：

```python
def _extract_score(match: dict[str, Any]) -> tuple[int, int] | None:
    score1, score2 = match.get("score1"), match.get("score2")
    if isinstance(score1, int) and isinstance(score2, int):
        return score1, score2
    score = match.get("score")
    if isinstance(score, dict):
        ft = score.get("ft")
        if isinstance(ft, list) and len(ft) == 2 and all(isinstance(v, int) for v in ft):
            return ft[0], ft[1]
    return None


def parse_openfootball_results(raw: dict[str, Any]) -> list[MatchResult]:
    results: list[MatchResult] = []
    fixtures = parse_openfootball_fixtures(raw)
    for fixture, match in zip(fixtures, raw.get("matches", [])):
        if fixture.has_placeholder_team:
            continue
        score = _extract_score(match)
        if score is None:
            continue
        results.append(
            MatchResult(
                kickoff_at_utc=fixture.kickoff_at_utc,
                home_team_name=fixture.home_team_name,
                away_team_name=fixture.away_team_name,
                home_canonical=fixture.home_canonical,
                away_canonical=fixture.away_canonical,
                home_score=score[0],
                away_score=score[1],
            )
        )
    return results
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/collectors/models.py worldcup/collectors/openfootball.py tests/collectors/test_openfootball_results.py
git commit -m "feat: parse finished match results from openfootball"
```

### Task 3: 赛果采集 CLI（幂等 upsert）

**Files:**
- Create: `worldcup/results_capture.py`
- Create: `tests/test_results_capture.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_results_capture.py`：

```python
from datetime import datetime, timezone

from worldcup.collectors.models import MatchResult
from worldcup.results_capture import upsert_results


def _result(home: str, away: str, hs: int, aw: int) -> MatchResult:
    return MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        home_team_name=home,
        away_team_name=away,
        home_canonical=home.lower(),
        away_canonical=away.lower(),
        home_score=hs,
        away_score=aw,
    )


def test_upsert_adds_new_results():
    rows, added, updated = upsert_results([_result("Mexico", "South Africa", 2, 1)], [], "2026-06-11T22:00:00+00:00")
    assert (added, updated) == (1, 0)
    assert rows[0]["home_score"] == "2"
    assert rows[0]["captured_at"] == "2026-06-11T22:00:00+00:00"


def test_upsert_is_idempotent_for_same_score():
    rows1, _, _ = upsert_results([_result("Mexico", "South Africa", 2, 1)], [], "t1")
    rows2, added, updated = upsert_results([_result("Mexico", "South Africa", 2, 1)], rows1, "t2")
    assert (added, updated) == (0, 0)
    assert rows2[0]["captured_at"] == "t1"


def test_upsert_updates_changed_score():
    rows1, _, _ = upsert_results([_result("Mexico", "South Africa", 1, 1)], [], "t1")
    rows2, added, updated = upsert_results([_result("Mexico", "South Africa", 2, 1)], rows1, "t2")
    assert (added, updated) == (0, 1)
    assert rows2[0]["home_score"] == "2"
    assert rows2[0]["captured_at"] == "t2"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（模块不存在）。

- [ ] **Step 3: 实现**

新建 `worldcup/results_capture.py`：

```python
"""Capture finished-match scores from the cached openfootball feed.

只读本地缓存，不联网；输出写入被忽略的 data/local/results/。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from worldcup.collectors.models import MatchResult
from worldcup.collectors.openfootball import parse_openfootball_results

COLUMNS = (
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_canonical",
    "away_canonical",
    "home_score",
    "away_score",
    "captured_at",
)


def _key(row: dict) -> tuple:
    return (row["kickoff_at_utc"], row["home_canonical"], row["away_canonical"])


def _to_row(result: MatchResult, captured_at: str) -> dict:
    return {
        "kickoff_at_utc": result.kickoff_at_utc.isoformat(),
        "home_team": result.home_team_name,
        "away_team": result.away_team_name,
        "home_canonical": result.home_canonical or "",
        "away_canonical": result.away_canonical or "",
        "home_score": str(result.home_score),
        "away_score": str(result.away_score),
        "captured_at": captured_at,
    }


def upsert_results(
    results: list[MatchResult],
    existing_rows: list[dict],
    captured_at: str,
) -> tuple[list[dict], int, int]:
    by_key = {_key(row): dict(row) for row in existing_rows}
    added = updated = 0
    for result in results:
        row = _to_row(result, captured_at)
        key = _key(row)
        if key not in by_key:
            by_key[key] = row
            added += 1
        elif (by_key[key]["home_score"], by_key[key]["away_score"]) != (
            row["home_score"],
            row["away_score"],
        ):
            by_key[key] = row
            updated += 1
    return sorted(by_key.values(), key=_key), added, updated


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_rows(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture finished scores into local results csv")
    parser.add_argument("--source", default="data/cache/openfootball_2026.json")
    parser.add_argument("--out", default="data/local/results/wc2026_results.csv")
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.source).read_text(encoding="utf-8"))
    results = parse_openfootball_results(raw)
    out = Path(args.out)
    rows, added, updated = upsert_results(
        results, _load_rows(out), datetime.now(timezone.utc).isoformat()
    )
    _write_rows(rows, out)
    print(
        json.dumps(
            {"finished": len(results), "added": added, "updated": updated, "total": len(rows)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过，并对当前缓存做 smoke**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.results_capture`
Expected: 开赛前输出 `{"finished": 0, "added": 0, "updated": 0, "total": 0}`（当前无完赛比赛，属正常）。

- [ ] **Step 5: Commit**

```bash
git add worldcup/results_capture.py tests/test_results_capture.py
git commit -m "feat: add finished results capture cli"
```

### Task 4: 评估数据构建 CLI（收盘 snapshot × 赛果）

**Files:**
- Create: `worldcup/eval_data.py`
- Create: `tests/test_eval_data.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_eval_data.py`：

```python
import json
import tempfile
from pathlib import Path

from worldcup.eval_data import build_rows, closing_match_entry


def _snapshot(snapshot_at: str, odds_home: float) -> dict:
    return {
        "snapshot_at": snapshot_at,
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_canonical": "mexico",
                "away_canonical": "south_africa",
                "elo": {"home": 1875, "away": 1700},
                "market": {
                    "1x2": {"odds": {"home": odds_home, "draw": 3.6, "away": 4.8}},
                    "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                },
            }
        ],
    }


RESULT_ROW = {
    "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
    "home_team": "Mexico",
    "away_team": "South Africa",
    "home_canonical": "mexico",
    "away_canonical": "south_africa",
    "home_score": "2",
    "away_score": "1",
}


def test_closing_picks_last_snapshot_before_kickoff():
    snapshots = [
        _snapshot("2026-06-11T06:00:00+00:00", 1.7),
        _snapshot("2026-06-11T18:00:00+00:00", 1.9),
        _snapshot("2026-06-11T20:00:00+00:00", 2.4),
    ]
    entry = closing_match_entry(
        snapshots, "2026-06-11T19:00:00+00:00", "mexico", "south_africa"
    )
    assert entry["market"]["1x2"]["odds"]["home"] == 1.9


def test_build_rows_joins_and_roundtrips_through_backtest_loader():
    from worldcup.backtest import load_matches

    snapshots = [_snapshot("2026-06-11T18:00:00+00:00", 1.8)]
    rows, skipped = build_rows(snapshots, [RESULT_ROW])
    assert skipped == 0
    assert rows[0]["odds_home"] == 1.8
    assert rows[0]["neutral"] == 1

    from worldcup.eval_data import write_csv

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "eval.csv"
        write_csv(rows, out)
        loaded = load_matches(out)
    assert len(loaded) == 1
    assert loaded[0].odds_1x2 == {"home": 1.8, "draw": 3.6, "away": 4.8}
    assert loaded[0].odds_ou == {"over": 1.9, "under": 2.0}
    assert loaded[0].home_elo_before == 1875.0
    assert loaded[0].home_score == 2


def test_build_rows_skips_result_without_pre_kickoff_snapshot():
    snapshots = [_snapshot("2026-06-11T20:00:00+00:00", 2.0)]
    rows, skipped = build_rows(snapshots, [RESULT_ROW])
    assert rows == [] and skipped == 1


def test_build_rows_blank_odds_when_market_incomplete():
    snap = _snapshot("2026-06-11T18:00:00+00:00", 1.8)
    snap["matches"][0]["market"]["ou_2_5"] = {"odds": {"over": 1.9}}
    rows, _ = build_rows([snap], [RESULT_ROW])
    assert rows[0]["odds_over"] == "" and rows[0]["odds_under"] == ""
    assert rows[0]["odds_home"] == 1.8
```

- [ ] **Step 2: 运行测试确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 新测试 FAIL（模块不存在）。

- [ ] **Step 3: 实现**

新建 `worldcup/eval_data.py`：

```python
"""Join archived snapshots with captured results into an odds-bearing backtest CSV.

只读 data/local/history/ 与 data/local/results/，不联网。
已知局限：neutral 一律按 1 处理（snapshot 未保存东道主修正），
AH 不进评估（snapshot market 块无 AH 聚合赔率）。
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

OUTPUT_COLUMNS = (
    "match_id",
    "kickoff_at_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "home_elo_before",
    "away_elo_before",
    "neutral",
    "odds_home",
    "odds_draw",
    "odds_away",
    "odds_over",
    "odds_under",
)


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_snapshots(history_dir: str | Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(history_dir).glob("snapshot_*.json"))
    ]


def closing_match_entry(
    snapshots: list[dict],
    kickoff_at_utc: str,
    home_canonical: str,
    away_canonical: str,
) -> dict | None:
    kickoff = _parse_at(kickoff_at_utc)
    best: dict | None = None
    best_at: datetime | None = None
    for snapshot in snapshots:
        snapshot_at = snapshot.get("snapshot_at")
        if not snapshot_at:
            continue
        at = _parse_at(snapshot_at)
        if at >= kickoff:
            continue
        for entry in snapshot.get("matches", []):
            if (
                entry.get("home_canonical") == home_canonical
                and entry.get("away_canonical") == away_canonical
                and (entry.get("kickoff_at_utc") or "")[:10] == kickoff_at_utc[:10]
            ):
                if best_at is None or at > best_at:
                    best, best_at = entry, at
    return best


def _market_odds(entry: dict, market: str, selections: tuple[str, ...]) -> dict[str, float]:
    odds = ((entry.get("market") or {}).get(market) or {}).get("odds") or {}
    if all(selection in odds for selection in selections):
        return {selection: odds[selection] for selection in selections}
    return {}


def build_rows(snapshots: list[dict], results_rows: list[dict]) -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    for result in results_rows:
        entry = closing_match_entry(
            snapshots,
            result["kickoff_at_utc"],
            result["home_canonical"],
            result["away_canonical"],
        )
        if entry is None:
            skipped += 1
            continue
        odds_1x2 = _market_odds(entry, "1x2", ("home", "draw", "away"))
        odds_ou = _market_odds(entry, "ou_2_5", ("over", "under"))
        rows.append(
            {
                "match_id": (
                    f"{result['kickoff_at_utc'][:10]}_"
                    f"{result['home_canonical']}_{result['away_canonical']}"
                ),
                "kickoff_at_utc": result["kickoff_at_utc"],
                "home_team": result["home_team"],
                "away_team": result["away_team"],
                "home_score": result["home_score"],
                "away_score": result["away_score"],
                "home_elo_before": entry["elo"]["home"],
                "away_elo_before": entry["elo"]["away"],
                "neutral": 1,
                "odds_home": odds_1x2.get("home", ""),
                "odds_draw": odds_1x2.get("draw", ""),
                "odds_away": odds_1x2.get("away", ""),
                "odds_over": odds_ou.get("over", ""),
                "odds_under": odds_ou.get("under", ""),
            }
        )
    return rows, skipped


def write_csv(rows: list[dict], path: str | Path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build odds-bearing eval csv from local history")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--results", default="data/local/results/wc2026_results.csv")
    parser.add_argument("--out", default="data/local/backtest/wc2026_eval.csv")
    args = parser.parse_args(argv)

    with open(args.results, newline="", encoding="utf-8") as fh:
        results_rows = list(csv.DictReader(fh))
    snapshots = load_snapshots(args.history)
    rows, skipped = build_rows(snapshots, results_rows)
    write_csv(rows, args.out)
    print(
        json.dumps(
            {
                "snapshots": len(snapshots),
                "results": len(results_rows),
                "joined": len(rows),
                "skipped_no_closing": skipped,
                "out": args.out,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add worldcup/eval_data.py tests/test_eval_data.py
git commit -m "feat: build odds-bearing eval csv from snapshot history"
```

### Task 5: 运行手册与文档

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: README 增加「世界杯期间评估数据」小节**

在「离线回测」小节之后追加：

```markdown
## 世界杯期间评估数据（自有赔率 + 赛果）

每次 live refresh 会把 snapshot 归档到被忽略的 `data/local/history/`（merge 进本机 main 后自动生效，无需部署服务端）。比赛日之后跑：

```bash
# 1) 从已缓存的 openfootball 数据提取完赛比分（幂等，可重复跑）
python3 -m worldcup.results_capture

# 2) 用"开球前最后一份"归档 snapshot 的赔率 join 赛果，生成带赔率的回测 CSV
python3 -m worldcup.eval_data

# 3) 用现有回测评估真实表现（EV 分层、model_matched vs market 此时有意义）
python3 -m worldcup.backtest --csv data/local/backtest/wc2026_eval.csv --min-sample 30 --out data/local/backtest/wc2026_report.json
```

已知局限：评估 CSV 的 `neutral` 一律为 1（不含东道主修正）；AH 不进评估；样本量小时报告会标 `sample_too_small`，小组赛阶段结论只做方向参考。
```

- [ ] **Step 2: 更新 RECENT_WORK.md**

顶部插入条目：完成事项（snapshot 归档、赛果采集、评估 CSV 构建）、归档自合并进 main 后的下一次 live refresh 开始积累、开赛后每日运行的三条命令、验证结果（全量测试 + `results_capture` smoke 输出 `finished: 0` 属赛前正常）、风险（归档失败只警告不阻断发布；openfootball 比分字段以实战首日为准，若实际格式不同需回来调 `_extract_score`）。

- [ ] **Step 3: 全量验证 + Commit**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: 全部 PASS。

```bash
git add README.md RECENT_WORK.md
git commit -m "docs: record results capture and eval pipeline"
```

---

## 风险与说明

1. **唯一碰生产链路的点是归档**：失败被捕获、只打 stderr 警告，发布流程不受影响；归档文件每天约 12 份、每份 ~300KB，世界杯全程 < 200MB，无需清理策略。
2. **openfootball 比分字段是合理推断**：标准格式是 `score1/score2`（90 分钟），解析器同时兼容 `score.ft`；两种都不在则视为未完赛跳过。首个比赛日（北京时间 6-12 凌晨）跑一次 `results_capture` 验证真实格式，不符再修 `_extract_score`。
3. **评估在 90 分钟口径**：与 `1X2_90min`、`OU_90min` 市场一致；淘汰赛加时/点球不影响（openfootball 的 `score1/score2` 即 90 分钟比分）。
4. **`neutral=1` 简化**：美墨加东道主场次的模型重放与生产有轻微出入，影响 `model` 指标不影响 `market` 基线与 EV 分层结论；后续若要修，需在 snapshot 里保存主场修正，单独计划。
5. **本计划不改**：引擎、pipeline、信号、API、ingest、scheduler、LaunchAgent。
