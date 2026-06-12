# The Odds API 赛果源接入（scores capture）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给赛果链加一个及时的机器源：用 The Odds API `/v4/sports/soccer_fifa_world_cup/scores/?daysFrom=2` 抓取完赛比分，灌进既有 `data/local/results/wc2026_results.csv`，解除 openfootball 上游录入滞后对评估链/日报的阻塞；openfootball 保持原位（Elo 重放与页面赛果显示仍用它，追上后幂等合并）。

**Architecture:** 四层复用既有模式：collector 纯解析（`parse_theoddsapi_scores` → 既有 `MatchResult`）、source 抓取（镜像 `fetch_worldcup_odds` 的缓存/quota/槽位记账，`estimated_last=2`）、CLI `worldcup.scores_capture`（默认 dry-run，`--live` 才联网，key 用 `choose_key_slot` 轮换）、`daily_eval --live-scores` 接入每日链。另做一次性的 results CSV 主键迁移：`(kickoff, home, away)` → `(kickoff 日期, home, away)`，吸收跨源 kickoff 分钟级差异（历史上有 Brazil vs Haiti 30 分钟差异先例），当前 CSV 为空是安全迁移窗口。

**Tech Stack:** Python 标准库，自带测试 runner（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

基线 `320/320`（以实际为准）。

**探测结论（2026-06-12 真实样例，实现者必读）：**

- 样例已保存：`data/probe/theoddsapi_scores_sample.json`（72 个事件；揭幕战 Mexico 2-0 South Africa `completed=true`）。
- 真实计费：`x-requests-last: 2`（带 `daysFrom`）。响应头与 odds 端点同款 quota 计数器。
- 事件结构：`{id, commence_time, completed, home_team, away_team, scores: [{name, score(字符串)}] | null, last_update}`；未完赛 `completed=false` 且 `scores=null`。
- **结算延迟**：feed 约赛后 1-4 小时才标 `completed`（揭幕战 21:00 UTC 完赛、01:11 UTC 才结算）。因此每日抓取/日报时间要后移到北京 16:30（当日最晚场约 04:00 UTC 完赛 + 4h 结算 ≈ 16:00 北京）。
- 队名与 The Odds API odds 事件同源，既有 `canonicalize_team` 直接适用。
- 安全规矩照旧：不打印 key、dry-run 默认、`--live` 才消耗额度（每次 2 credits）；测试全部 fake transport 离线。
- 全程不 push、不部署；除 Task 6 标注的一次 `--live` smoke（用户已批准，2 credits）外不调用真实 API。每任务本地 commit。

---

### Task 1: collector 纯解析层

**Files:**
- Create: `worldcup/collectors/theoddsapi_scores.py`
- Test: `tests/collectors/test_theoddsapi_scores.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/collectors/test_theoddsapi_scores.py`：

```python
import json
from pathlib import Path

from worldcup.collectors.theoddsapi_scores import parse_theoddsapi_scores

PROBE_DIR = Path(__file__).resolve().parents[2] / "data" / "probe"

SAMPLE = [
    {
        "id": "e1",
        "commence_time": "2026-06-11T19:00:00Z",
        "completed": True,
        "home_team": "Mexico",
        "away_team": "South Africa",
        "scores": [
            {"name": "Mexico", "score": "2"},
            {"name": "South Africa", "score": "0"},
        ],
        "last_update": "2026-06-12T01:11:15Z",
    },
    {
        "id": "e2",
        "commence_time": "2026-06-12T02:00:00Z",
        "completed": False,
        "home_team": "South Korea",
        "away_team": "Czech Republic",
        "scores": None,
        "last_update": "2026-06-12T01:11:15Z",
    },
    {
        "id": "e3",
        "commence_time": "2026-06-12T19:00:00Z",
        "completed": True,
        "home_team": "Canada",
        "away_team": "Bosnia and Herzegovina",
        "scores": [{"name": "Canada", "score": "1"}],
        "last_update": "2026-06-13T01:00:00Z",
    },
]


def test_parse_scores_returns_completed_results_only():
    results = parse_theoddsapi_scores(SAMPLE)

    assert len(results) == 1
    result = results[0]
    assert result.kickoff_at_utc.isoformat() == "2026-06-11T19:00:00+00:00"
    assert result.home_team_name == "Mexico"
    assert result.away_team_name == "South Africa"
    assert result.home_canonical == "mexico"
    assert result.away_canonical == "south_africa"
    assert result.home_score == 2
    assert result.away_score == 0


def test_parse_scores_skips_incomplete_score_arrays():
    # e3 只有单边比分，必须跳过，不能猜测
    results = parse_theoddsapi_scores([SAMPLE[2]])
    assert results == []


def test_saved_scores_probe_sample_parses_when_present():
    path = PROBE_DIR / "theoddsapi_scores_sample.json"
    if not path.exists():
        return
    results = parse_theoddsapi_scores(json.loads(path.read_text(encoding="utf-8")))
    assert any(
        r.home_canonical == "mexico" and r.home_score == 2 and r.away_score == 0
        for r in results
    )
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.collectors.theoddsapi_scores'`。

- [ ] **Step 3: 实现**

```python
"""Parse The Odds API scores responses into MatchResult records. Offline only."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from worldcup.collectors.models import MatchResult
from worldcup.collectors.team_aliases import canonicalize_team


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_theoddsapi_scores(raw: list[dict[str, Any]]) -> list[MatchResult]:
    results: list[MatchResult] = []
    for event in raw or []:
        if not event.get("completed"):
            continue
        home = str(event.get("home_team") or "").strip()
        away = str(event.get("away_team") or "").strip()
        commence = event.get("commence_time")
        if not home or not away or not commence:
            continue
        by_name = {
            str(s.get("name") or "").strip(): str(s.get("score") or "").strip()
            for s in (event.get("scores") or [])
        }
        home_score = by_name.get(home)
        away_score = by_name.get(away)
        if home_score is None or away_score is None:
            continue
        if not home_score.isdigit() or not away_score.isdigit():
            continue
        results.append(
            MatchResult(
                kickoff_at_utc=_parse_at(commence),
                home_team_name=home,
                away_team_name=away,
                home_canonical=canonicalize_team(home),
                away_canonical=canonicalize_team(away),
                home_score=int(home_score),
                away_score=int(away_score),
            )
        )
    return results
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/collectors/theoddsapi_scores.py tests/collectors/test_theoddsapi_scores.py
git commit -m "feat: parse theoddsapi scores into match results"
```

---

### Task 2: source 抓取层（缓存 + 槽位 quota 记账）

**Files:**
- Create: `worldcup/sources/theoddsapi_scores.py`
- Test: `tests/sources/test_theoddsapi_scores_source.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/sources/test_theoddsapi_scores_source.py`（FakeResponse 模式照抄 `tests/sources/test_theoddsapi_source.py`）：

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.sources.theoddsapi_scores import fetch_worldcup_scores


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "73",
        "x-requests-remaining": "427",
        "x-requests-last": "2",
    }

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_fetch_scores_writes_cache_and_slot_quota():
    body = json.dumps(
        [
            {
                "id": "e1",
                "commence_time": "2026-06-11T19:00:00Z",
                "completed": True,
                "home_team": "Mexico",
                "away_team": "South Africa",
                "scores": [
                    {"name": "Mexico", "score": "2"},
                    {"name": "South Africa", "score": "0"},
                ],
            }
        ]
    ).encode()
    captured = {}

    def transport(url):
        captured["url"] = url
        return FakeResponse(body)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = fetch_worldcup_scores(
            api_key="fake-key",
            transport=transport,
            cache_path=root / "theoddsapi_scores.json",
            quota_path=root / "quota.json",
            observed_at="2026-06-12T08:00:00+00:00",
            quota_provider="theoddsapi_primary",
        )

        assert "scores/?daysFrom=2" in captured["url"]
        assert "fake-key" in captured["url"]
        assert result.status == 200
        assert json.loads((root / "theoddsapi_scores.json").read_text())[0]["completed"] is True
        quota = json.loads((root / "quota.json").read_text())["providers"]
        assert quota["theoddsapi_primary"]["remaining"] == 427
        assert quota["theoddsapi"]["remaining"] == 427
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.sources.theoddsapi_scores'`。

- [ ] **Step 3: 实现**

镜像 `worldcup/sources/theoddsapi.py` 的结构（URL、默认 transport、缓存写入、`update_quota_from_headers` 主槽位 + legacy 双写），差异点：

```python
SCORES_URL = BASE_URL + "/sports/soccer_fifa_world_cup/scores/"
# 查询参数：daysFrom=days_from（默认 2）、apiKey=api_key
# estimated_last=2（探测实测 x-requests-last 为 2）
# 函数签名：
def fetch_worldcup_scores(
    api_key: str,
    transport=None,
    cache_path=None,
    quota_path=None,
    observed_at=None,
    quota_provider: str = LEGACY_PROVIDER,
    days_from: int = 2,
) -> SourceFetchResult: ...
```

（`BASE_URL`、`SourceFetchResult`、quota 双写逻辑从 `worldcup/sources/theoddsapi.py` import 或同构实现，以现有文件为准，保持行为一致。）

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/sources/theoddsapi_scores.py tests/sources/test_theoddsapi_scores_source.py
git commit -m "feat: fetch worldcup scores with slot quota accounting"
```

---

### Task 3: results CSV 主键迁移为日期级

**Files:**
- Modify: `worldcup/results_capture.py`（`_key` ~L28）
- Test: `tests/test_results_capture.py`

- [ ] **Step 1: 写失败测试**

`tests/test_results_capture.py` 追加（fixture 构造方式照抄文件内既有测试）：

```python
def test_upsert_merges_same_day_pairing_despite_kickoff_minutes():
    from datetime import datetime, timezone

    from worldcup.collectors.models import MatchResult
    from worldcup.results_capture import upsert_results

    first = MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 0, tzinfo=timezone.utc),
        home_team_name="Mexico",
        away_team_name="South Africa",
        home_canonical="mexico",
        away_canonical="south_africa",
        home_score=2,
        away_score=0,
    )
    # 同日同对阵，但 kickoff 差 30 分钟（跨源差异），比分修正为 2-1
    second = MatchResult(
        kickoff_at_utc=datetime(2026, 6, 11, 19, 30, tzinfo=timezone.utc),
        home_team_name="Mexico",
        away_team_name="South Africa",
        home_canonical="mexico",
        away_canonical="south_africa",
        home_score=2,
        away_score=1,
    )

    rows, added, updated = upsert_results([first], [], "2026-06-12T08:00:00+00:00")
    assert added == 1
    rows, added, updated = upsert_results([second], rows, "2026-06-12T09:00:00+00:00")

    assert added == 0
    assert updated == 1
    assert len(rows) == 1
    assert int(rows[0]["home_score"]) == 2 and int(rows[0]["away_score"]) == 1
```

（用 `int(...)` 断言：`_to_row` 直出的行是 int，经 CSV 读回是 str，两种来源都要兼容。）

- [ ] **Step 2: 运行确认失败**

Expected: 该测试 FAIL（当前 `_key` 含完整 kickoff 时间，30 分钟差被当成两场，`added == 1`）。

- [ ] **Step 3: 最小实现**

`worldcup/results_capture.py` 的 `_key` 改为日期级：

```python
def _key(row: dict) -> tuple:
    return (row["kickoff_at_utc"][:10], row["home_canonical"], row["away_canonical"])
```

（若 `_key` 入参处有用 `MatchResult` 转出的 row，确认统一走 `_to_row` 后的字符串字段；世界杯无同日同对阵二次交手，日期级键无歧义。）

- [ ] **Step 4: 运行全量确认通过（既有 results/eval/daily 测试都不应受影响）；Step 5: commit**

```bash
git add worldcup/results_capture.py tests/test_results_capture.py
git commit -m "feat: merge results by match day pairing"
```

---

### Task 4: `worldcup.scores_capture` CLI（默认 dry-run）

**Files:**
- Create: `worldcup/scores_capture.py`
- Test: `tests/test_scores_capture.py`

- [ ] **Step 1: 写失败测试**

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.scores_capture import run_scores_capture


def _scores_body() -> list[dict]:
    return [
        {
            "id": "e1",
            "commence_time": "2026-06-11T19:00:00Z",
            "completed": True,
            "home_team": "Mexico",
            "away_team": "South Africa",
            "scores": [
                {"name": "Mexico", "score": "2"},
                {"name": "South Africa", "score": "0"},
            ],
        }
    ]


class FakeResponse:
    status = 200
    headers = {
        "x-requests-used": "73",
        "x-requests-remaining": "427",
        "x-requests-last": "2",
    }

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def test_scores_capture_dry_run_does_not_fetch():
    calls = []

    def transport(url):
        calls.append(url)
        raise AssertionError("dry-run must not fetch")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=False,
            env={"THE_ODDS_API_KEY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
        )

    assert out["status"] == "dry_run"
    assert calls == []
    assert not (root / "results.csv").exists()


def test_scores_capture_live_upserts_results():
    def transport(_url):
        return FakeResponse(json.dumps(_scores_body()).encode())

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=root / "quota.json",
            results_out=root / "results.csv",
            transport=transport,
        )

        assert out["status"] == "captured"
        assert out["completed"] == 1
        assert out["added"] == 1
        assert out["slot"] == "primary"
        rows = (root / "results.csv").read_text()
        assert "mexico" in rows and "2" in rows


def test_scores_capture_live_blocks_when_all_slots_exhausted():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        quota_path = root / "quota.json"
        quota_path.write_text(
            json.dumps({"providers": {"theoddsapi_primary": {"remaining": 0}}})
        )

        out = run_scores_capture(
            live=True,
            env={"THE_ODDS_API_KEY_PRIMARY": "fake-key"},
            cache_path=root / "scores.json",
            quota_path=quota_path,
            results_out=root / "results.csv",
            transport=lambda url: (_ for _ in ()).throw(AssertionError("no fetch")),
        )

    assert out["status"] == "blocked"
    assert out["reason"] == "quota_exhausted"
```

- [ ] **Step 2: 运行确认失败**

Expected: `No module named 'worldcup.scores_capture'`。

- [ ] **Step 3: 实现 `worldcup/scores_capture.py`**

```python
"""Capture finished scores from The Odds API into the local results csv.

默认 dry-run：不联网、不写文件。--live 才抓取（约 2 credits），key 按槽位轮换。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from worldcup.collectors.theoddsapi_scores import parse_theoddsapi_scores
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env
from worldcup.results_capture import _load_rows, _write_rows, upsert_results
from worldcup.sources.theoddsapi_scores import fetch_worldcup_scores
from worldcup.theoddsapi_keys import choose_key_slot


def run_scores_capture(
    live: bool,
    env: dict[str, str],
    cache_path: str | Path = "data/cache/theoddsapi_scores.json",
    quota_path: str | Path = "data/cache/quota.json",
    results_out: str | Path = "data/local/results/wc2026_results.csv",
    transport: Callable[[str], object] | None = None,
    observed_at: str | None = None,
) -> dict:
    if not live:
        return {"status": "dry_run", "note": "pass --live to fetch scores (~2 credits)"}

    providers = load_quota_ledger(quota_path).get("providers", {})
    selected = choose_key_slot(env, providers)
    if selected is None:
        return {"status": "blocked", "reason": "quota_exhausted"}

    observed = observed_at or datetime.now(timezone.utc).isoformat()
    fetch_worldcup_scores(
        api_key=selected.api_key,
        transport=transport,
        cache_path=cache_path,
        quota_path=quota_path,
        observed_at=observed,
        quota_provider=selected.provider,
    )
    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    results = parse_theoddsapi_scores(raw)
    out = Path(results_out)
    rows, added, updated = upsert_results(results, _load_rows(out), observed)
    _write_rows(rows, out)
    return {
        "status": "captured",
        "events": len(raw),
        "completed": len(results),
        "added": added,
        "updated": updated,
        "total": len(rows),
        "slot": selected.slot,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture scores from The Odds API (default dry-run).")
    parser.add_argument("--live", action="store_true", help="Fetch for real (~2 credits).")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--cache-path", default="data/cache/theoddsapi_scores.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--out", default="data/local/results/wc2026_results.csv")
    args = parser.parse_args(argv)

    result = run_scores_capture(
        live=args.live,
        env=_load_env(args.env),
        cache_path=args.cache_path,
        quota_path=args.quota_path,
        results_out=args.out,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/scores_capture.py tests/test_scores_capture.py
git commit -m "feat: add scores capture cli with slot rotation"
```

---

### Task 5: `daily_eval --live-scores` 接入

**Files:**
- Modify: `worldcup/daily_eval.py`
- Test: `tests/test_daily_eval.py`

- [ ] **Step 1: 写失败测试**

`tests/test_daily_eval.py` 追加：

```python
def test_daily_eval_live_scores_runs_capture_first():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)
        capture_calls = []

        def scores_capture_fn(**kwargs):
            capture_calls.append(kwargs)
            return {"status": "captured", "completed": 1, "added": 1, "updated": 0}

        code = main(
            [
                "--cache-dir", str(paths["cache_dir"]),
                "--history", str(paths["history_dir"]),
                "--results-out", str(paths["results_out"]),
                "--eval-out", str(paths["eval_out"]),
                "--report-out", str(paths["report_out"]),
                "--min-sample", "1",
                "--live-scores",
            ],
            scores_capture_fn=scores_capture_fn,
        )

        assert code == 0
        assert len(capture_calls) == 1
        assert capture_calls[0]["live"] is True
        assert str(capture_calls[0]["results_out"]) == str(paths["results_out"])
```

- [ ] **Step 2: 运行确认失败**

Expected: `main` 不接受 `scores_capture_fn` / `--live-scores`。

- [ ] **Step 3: 最小实现**

`daily_eval.main` 增加参数 `scores_capture_fn: Callable[..., dict] | None = None` 与 CLI flag `--live-scores`；在 `run_daily_eval` 调用**之前**执行：

```python
    scores_stats = None
    if args.live_scores:
        from worldcup.refresh_runner import _load_env
        from worldcup.scores_capture import run_scores_capture

        capture = scores_capture_fn or run_scores_capture
        scores_stats = capture(
            live=True,
            env=_load_env(".env"),
            cache_path=Path(args.cache_dir) / "theoddsapi_scores.json",
            quota_path=Path(args.cache_dir) / "quota.json",
            results_out=args.results_out,
        )
```

并把 `scores_stats` 加进最终打印的 digest（键名 `"scores"`）；模块 docstring 的"不联网"说明改为"默认不联网；`--live-scores` 会调用 The Odds API scores 端点（约 2 credits）"。

- [ ] **Step 4: 运行确认通过；Step 5: commit**

```bash
git add worldcup/daily_eval.py tests/test_daily_eval.py
git commit -m "feat: pull scores before daily eval chain"
```

---

### Task 6: LaunchAgent 改时 + 真实 smoke + 文档

**Files:**
- Modify: `~/Library/LaunchAgents/xin.celab.football.daily-eval.plist`（系统文件）
- Modify: `README.md`、`RECENT_WORK.md`

- [ ] **Step 1: 改 LaunchAgent 为 16:30 并加 `--live-scores`**

直接整文件重写（`plutil -insert` 对 `--` 开头的值不可靠）；`<PYTHON>` 沿用现 plist 里的解释器路径（先 `plutil -p` 看一眼再填）：

```bash
launchctl bootout gui/$(id -u)/xin.celab.football.daily-eval
cat > ~/Library/LaunchAgents/xin.celab.football.daily-eval.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>xin.celab.football.daily-eval</string>
  <key>ProgramArguments</key>
  <array>
    <string><PYTHON></string>
    <string>-m</string>
    <string>worldcup.daily_eval</string>
    <string>--notify</string>
    <string>--live-scores</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/eagod/ai-dev/足彩</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>/Users/eagod/Library/Logs/worldcup/daily-eval.out.log</string>
  <key>StandardErrorPath</key><string>/Users/eagod/Library/Logs/worldcup/daily-eval.err.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/xin.celab.football.daily-eval.plist
plutil -p ~/Library/LaunchAgents/xin.celab.football.daily-eval.plist
```

Expected: plist 显示 Hour=16、Minute=30、ProgramArguments 含 `--live-scores`。改时理由（写进 RECENT_WORK）：scores feed 赛后约 1-4 小时结算，当日最晚场 ≈ 04:00 UTC 完赛，16:30 北京能一次覆盖完整上一比赛日。

- [ ] **Step 2: 一次真实端到端 smoke（用户已批准，约 2 credits）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.daily_eval --live-scores --notify
```

Expected: digest 中 `scores.status=captured`、`results.added >= 1`（揭幕战 Mexico 2-0 入库）、`eval.joined >= 1`（closing 快照已含 AH 主盘）、backtest 报告生成（`sample_too_small=true` 属预期）、`signal_tally` 出现计数、真实推送一条日报。**核对 `data/local/backtest/wc2026_eval.csv` 的 `ah_line` / `odds_ah_home` / `odds_ah_away` 三列有值。**

- [ ] **Step 3: 文档同步**

3a. `README.md`：开赛后日常命令段补 `python3 -m worldcup.scores_capture --live`（手动补抓赛果，约 2 credits）；LaunchAgent 说明从 12:30 改为 16:30 并注明 `--live-scores` 与额度消耗（每天约 2 credits）；数据源表加一行"赛果及时源 | The Odds API scores | 同 key 轮换"。已知局限补一句：Elo 重放与页面赛果显示仍以 openfootball 为准，openfootball 录入滞后期间页面"预测结果"可能晚于日报。

3b. `RECENT_WORK.md` 顶部按既有格式追加"2026-06-12 The Odds API 赛果源接入"一节：探测结论（实测 2 credits、结算延迟 1-4h）、results 主键日期级迁移、scores_capture 默认 dry-run、daily_eval `--live-scores`、LaunchAgent 16:30、smoke 结果，以及"未 push、未部署"。

- [ ] **Step 4: 最终全量验证与 commit（不 push）**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
git add README.md RECENT_WORK.md
git commit -m "docs: record theoddsapi scores capture"
```

---

## 范围外（明确不做）

- 不把 scores 源接进 Elo 重放或页面赛果显示（openfootball 保持该职责；其录入后幂等合并，必要时另案统一）。
- 不在 15 分钟刷新主链路里拉 scores（额度翻倍，不必要）。
- 不做加时/点球比分区分（feed 仅总比分；90 分钟口径风险淘汰赛阶段另案评估——小组赛无加时，不受影响）。
- 不改模型、不动信号、不部署、不 push。

## 已知风险

- **淘汰赛阶段**：scores feed 的比分可能含加时/点球，而 1X2 信号按 90 分钟口径结算。小组赛（6-27 前）无此问题；进入淘汰赛前必须回来评估，必要时淘汰赛停用 scores 自动入库、改回 openfootball/人工核对。此条已写入 README 已知局限。
