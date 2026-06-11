# 赛后链路每日自动化（results → eval → backtest + WxPusher 日报）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把开赛后每天手动跑的 `results_capture` → `eval_data` → `backtest` 串成一条命令 `python3 -m worldcup.daily_eval`，并由每日 LaunchAgent 在北京时间 12:30 自动执行、把摘要（完赛数 / 评估样本 / 模型 vs 市场指标 / S 与 A 级信号命中统计）推送到 WxPusher。

**Architecture:** 新增 `worldcup/daily_eval.py` 编排器：在进程内依次调用三个既有 CLI 的 `main(argv)`（它们的参数与单行 JSON stdout 是被测稳定契约），用 `redirect_stdout` 截获统计；读取 backtest 报告文件提取头部指标；从当前 `analysis_snapshot.json` 用 `ledger._prediction_result` 统计 S/A 级信号命中；无新增赛果时跳过推送。全链路只读 `data/cache/`、读写 `data/local/`，**不联网、不消耗 The Odds API 额度、不写线上**。

**Tech Stack:** Python 标准库，自带测试 runner（无 pytest）。

**验证命令（全程唯一）：**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

基线为当前全绿（316/316，以实际为准）。

**既有契约（实现者必读，已核实勿改）：**

- `results_capture.main(["--source", ..., "--out", ...])` → stdout 单行 JSON `{"finished","added","updated","total"}`，返回 0。
- `eval_data.main(["--history", ..., "--results", ..., "--out", ...])` → stdout 单行 JSON `{"snapshots","results","joined","skipped_no_closing","out"}`。
- `backtest.main(["--csv", ..., "--min-sample", ..., "--out", ...])` → 把报告 JSON 写入 `--out`；报告含 `sample.n_matches`、`sample.sample_too_small`、`markets["1x2"|"ou_2_5"]` 下 `model/model_matched/market/uniform` 各为 `{"n","brier","log_loss"}`、`sample.n_ah`。
- `ledger._prediction_result(match, signal)` 接收 snapshot 序列化字典，返回 `{"label": "命中"|"未中"|"走水", "detail": ...}` 或 `None`；跨模块复用私有函数在本仓库有先例（`scheduled_publish` 用 `refresh_runner._load_env`）。
- `notifications.send_wxpusher_notification(content, *, summary, ...)` 已脱敏、失败返回 `{"status":"failed",...}` 不抛异常。
- 推送内容不得出现下注金额/资金字段；保留研究免责声明口径。
- 全程不 push、不部署、不触发 live refresh；每任务本地 commit。唯一系统状态变更是 Task 3 的 LaunchAgent 安装（用户已确认要定时执行）。

---

### Task 1: `worldcup/daily_eval.py` 编排与摘要

**Files:**
- Create: `worldcup/daily_eval.py`
- Test: `tests/test_daily_eval.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_daily_eval.py`：

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.daily_eval import run_daily_eval


def _seed_project(root: Path, with_score: bool) -> dict:
    cache = root / "cache"
    history = root / "history"
    local = root / "local"
    cache.mkdir()
    history.mkdir()
    local.mkdir()
    match = {
        "round": "Matchday 1",
        "date": "2026-06-11",
        "time": "13:00 UTC-6",
        "team1": "Mexico",
        "team2": "South Africa",
        "ground": "Mexico City",
    }
    if with_score:
        match["score1"] = 2
        match["score2"] = 0
    (cache / "openfootball_2026.json").write_text(json.dumps({"matches": [match]}))
    (history / "snapshot_20260611T120000Z-live.json").write_text(
        json.dumps(
            {
                "snapshot_at": "2026-06-11T12:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "home_canonical": "mexico",
                        "away_canonical": "south_africa",
                        "elo": {"home": 1875, "away": 1700},
                        "market": {
                            "1x2": {"odds": {"home": 1.8, "draw": 3.6, "away": 4.8}},
                            "ou_2_5": {"odds": {"over": 1.9, "under": 2.0}},
                        },
                    }
                ],
            }
        )
    )
    # 当前 snapshot：带完赛 result 与一条 S 级 1X2 主胜信号，供命中统计
    (cache / "analysis_snapshot.json").write_text(
        json.dumps(
            {
                "snapshot_at": "2026-06-12T04:00:00+00:00",
                "matches": [
                    {
                        "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                        "home_team": "Mexico",
                        "away_team": "South Africa",
                        "result": {"status": "finished", "home_score": 2, "away_score": 0},
                        "signals": [
                            {
                                "market_type": "1X2_90min",
                                "selection": "home",
                                "grade": "S",
                                "line": None,
                            }
                        ],
                    }
                ],
            }
        )
    )
    return {
        "cache_dir": cache,
        "history_dir": history,
        "results_out": local / "results.csv",
        "eval_out": local / "eval.csv",
        "report_out": local / "report.json",
    }


def test_daily_eval_runs_chain_and_builds_digest():
    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)

        digest = run_daily_eval(min_sample=1, **paths)

        assert digest["status"] == "ok"
        assert digest["results"]["total"] == 1
        assert digest["results"]["added"] == 1
        assert digest["eval"]["joined"] == 1
        assert digest["backtest"]["n_matches"] == 1
        assert digest["backtest"]["model_1x2"]["n"] == 1
        assert digest["signal_tally"]["S"] == {"命中": 1}
        assert Path(paths["report_out"]).exists()


def test_daily_eval_skips_backtest_without_results():
    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=False)

        digest = run_daily_eval(min_sample=1, **paths)

        assert digest["status"] == "no_new_results"
        assert digest["results"]["total"] == 0
        assert digest["eval"] is None
        assert digest["backtest"] is None
        assert not Path(paths["report_out"]).exists()
```

- [ ] **Step 2: 运行确认失败**

Run: `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`
Expected: `FAIL test_daily_eval.py::...`，报 `No module named 'worldcup.daily_eval'`。

- [ ] **Step 3: 实现 `worldcup/daily_eval.py`**

```python
"""Daily post-match chain: results capture -> eval csv -> backtest -> digest.

只读 data/cache/，读写 data/local/；不联网、不消耗 The Odds API 额度、不写线上。
推送内容仅研究摘要，不含资金/下注字段。
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

from worldcup import backtest, eval_data, results_capture
from worldcup.ledger import _prediction_result

TRACKED_GRADES = ("S", "A")


def _run_json_cli(fn: Callable[[list[str]], int], argv: list[str]) -> dict:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = fn(argv)
    if code != 0:
        raise RuntimeError(f"{fn.__module__}.main exited with {code}")
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {}


def signal_tally(snapshot: dict) -> dict[str, dict[str, int]]:
    tally: dict[str, dict[str, int]] = {grade: {} for grade in TRACKED_GRADES}
    for match in snapshot.get("matches", []):
        if ((match.get("result") or {}).get("status")) != "finished":
            continue
        for signal in match.get("signals", []):
            grade = str(signal.get("grade") or "")
            if grade not in tally:
                continue
            outcome = _prediction_result(match, signal)
            if not outcome:
                continue
            label = str(outcome.get("label") or "")
            if label:
                tally[grade][label] = tally[grade].get(label, 0) + 1
    return tally


def _market_summary(report: dict, market: str) -> dict:
    block = (report.get("markets") or {}).get(market) or {}
    return {
        "model": block.get("model"),
        "model_matched": block.get("model_matched"),
        "market": block.get("market"),
    }


def run_daily_eval(
    cache_dir: str | Path = "data/cache",
    history_dir: str | Path = "data/local/history",
    results_out: str | Path = "data/local/results/wc2026_results.csv",
    eval_out: str | Path = "data/local/backtest/wc2026_eval.csv",
    report_out: str | Path = "data/local/backtest/wc2026_report.json",
    min_sample: int = 30,
) -> dict[str, Any]:
    cache = Path(cache_dir)
    results_stats = _run_json_cli(
        results_capture.main,
        ["--source", str(cache / "openfootball_2026.json"), "--out", str(results_out)],
    )

    digest: dict[str, Any] = {
        "results": results_stats,
        "eval": None,
        "backtest": None,
        "signal_tally": {grade: {} for grade in TRACKED_GRADES},
    }
    fresh = (results_stats.get("added", 0) or 0) + (results_stats.get("updated", 0) or 0)
    if results_stats.get("total", 0) <= 0 or fresh <= 0:
        digest["status"] = "no_new_results"
        return digest

    eval_stats = _run_json_cli(
        eval_data.main,
        [
            "--history",
            str(history_dir),
            "--results",
            str(results_out),
            "--out",
            str(eval_out),
        ],
    )
    digest["eval"] = eval_stats

    if eval_stats.get("joined", 0) > 0:
        _run_json_cli(
            backtest.main,
            [
                "--csv",
                str(eval_out),
                "--min-sample",
                str(min_sample),
                "--out",
                str(report_out),
            ],
        )
        report = json.loads(Path(report_out).read_text(encoding="utf-8"))
        digest["backtest"] = {
            "n_matches": report["sample"]["n_matches"],
            "n_ah": report["sample"]["n_ah"],
            "sample_too_small": report["sample"]["sample_too_small"],
            "model_1x2": _market_summary(report, "1x2")["model"],
            "market_1x2": _market_summary(report, "1x2")["market"],
            "model_ou": _market_summary(report, "ou_2_5")["model"],
            "market_ou": _market_summary(report, "ou_2_5")["market"],
        }

    snapshot_path = cache / "analysis_snapshot.json"
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        digest["signal_tally"] = signal_tally(snapshot)

    digest["status"] = "ok"
    return digest
```

- [ ] **Step 4: 运行确认通过**

Expected: 全绿（新增 2 个测试 PASS）。注意第一个测试会真实跑通 `eval_data` 与 `backtest`，对编排正确性是端到端验证。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/daily_eval.py tests/test_daily_eval.py
git commit -m "feat: add daily post-match eval chain"
```

---

### Task 2: 日报推送与 CLI

**Files:**
- Modify: `worldcup/daily_eval.py`（追加 digest 文案、notify、`main`）
- Test: `tests/test_daily_eval.py`（追加）

- [ ] **Step 1: 写失败测试**

`tests/test_daily_eval.py` 追加：

```python
def test_daily_eval_notify_sends_digest_once():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=True)
        calls = []

        def notify_fn(content, *, summary):
            calls.append({"content": content, "summary": summary})
            return {"status": "sent", "exit_code": 0}

        code = main(
            [
                "--cache-dir", str(paths["cache_dir"]),
                "--history", str(paths["history_dir"]),
                "--results-out", str(paths["results_out"]),
                "--eval-out", str(paths["eval_out"]),
                "--report-out", str(paths["report_out"]),
                "--min-sample", "1",
                "--notify",
            ],
            notify_fn=notify_fn,
        )

        assert code == 0
        assert len(calls) == 1
        assert "赛后日报" in calls[0]["summary"]
        assert "S 级" in calls[0]["content"]
        assert "命中" in calls[0]["content"]
        assert "研究" in calls[0]["content"]


def test_daily_eval_no_notify_without_new_results():
    from worldcup.daily_eval import main

    with TemporaryDirectory() as tmp:
        paths = _seed_project(Path(tmp), with_score=False)
        calls = []

        def notify_fn(content, *, summary):
            calls.append(summary)
            return {"status": "sent", "exit_code": 0}

        code = main(
            [
                "--cache-dir", str(paths["cache_dir"]),
                "--history", str(paths["history_dir"]),
                "--results-out", str(paths["results_out"]),
                "--eval-out", str(paths["eval_out"]),
                "--report-out", str(paths["report_out"]),
                "--min-sample", "1",
                "--notify",
            ],
            notify_fn=notify_fn,
        )

        assert code == 0
        assert calls == []
```

- [ ] **Step 2: 运行确认失败**

Expected: `FAIL ...::test_daily_eval_notify_sends_digest_once`（`main` 不存在）。

- [ ] **Step 3: 实现文案与 CLI**

`worldcup/daily_eval.py` 追加：

```python
def _fmt_metric(metrics: dict | None) -> str:
    if not metrics or not metrics.get("n"):
        return "—"
    return f"LogLoss {metrics['log_loss']:.4f} (n={metrics['n']})"


def _fmt_tally(tally: dict[str, int]) -> str:
    if not tally:
        return "暂无"
    return " ".join(f"{label}{count}" for label, count in sorted(tally.items()))


def build_digest_message(digest: dict) -> dict[str, str]:
    results = digest.get("results") or {}
    lines = [
        "世界杯赛后日报",
        f"完赛 {results.get('total', 0)} 场（新增 {results.get('added', 0)}，更新 {results.get('updated', 0)}）",
    ]
    eval_stats = digest.get("eval")
    if eval_stats:
        lines.append(
            f"评估样本 {eval_stats.get('joined', 0)} 场"
            f"（无 closing 快照跳过 {eval_stats.get('skipped_no_closing', 0)}）"
        )
    backtest_stats = digest.get("backtest")
    if backtest_stats:
        lines.append(f"1X2 模型 {_fmt_metric(backtest_stats.get('model_1x2'))}，市场 {_fmt_metric(backtest_stats.get('market_1x2'))}")
        lines.append(f"OU 模型 {_fmt_metric(backtest_stats.get('model_ou'))}，市场 {_fmt_metric(backtest_stats.get('market_ou'))}")
        lines.append(f"AH 进评估 {backtest_stats.get('n_ah', 0)} 场")
        if backtest_stats.get("sample_too_small"):
            lines.append("样本不足（sample_too_small），指标仅记录不作结论")
    tally = digest.get("signal_tally") or {}
    lines.append(f"S 级信号：{_fmt_tally(tally.get('S') or {})}")
    lines.append(f"A 级信号：{_fmt_tally(tally.get('A') or {})}")
    lines.append("仅用于研究分析，不构成投注建议")
    return {
        "summary": f"世界杯赛后日报：完赛 {results.get('total', 0)} 场",
        "content": "\n".join(lines),
    }


def main(argv: list[str] | None = None, notify_fn: Callable[..., dict] | None = None) -> int:
    import argparse

    from worldcup.notifications import send_wxpusher_notification

    parser = argparse.ArgumentParser(description="Run daily post-match eval chain.")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--results-out", default="data/local/results/wc2026_results.csv")
    parser.add_argument("--eval-out", default="data/local/backtest/wc2026_eval.csv")
    parser.add_argument("--report-out", default="data/local/backtest/wc2026_report.json")
    parser.add_argument("--min-sample", type=int, default=30)
    parser.add_argument("--notify", action="store_true", help="Send WxPusher digest.")
    args = parser.parse_args(argv)

    digest = run_daily_eval(
        cache_dir=args.cache_dir,
        history_dir=args.history,
        results_out=args.results_out,
        eval_out=args.eval_out,
        report_out=args.report_out,
        min_sample=args.min_sample,
    )
    notification = None
    if args.notify and digest.get("status") == "ok":
        message = build_digest_message(digest)
        send = notify_fn or send_wxpusher_notification
        notification = send(message["content"], summary=message["summary"])
    print(json.dumps({**digest, "notification": notification}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行确认通过**

Expected: 全绿（净增 4 个测试）。

- [ ] **Step 5: 本地 commit（不 push）**

```bash
git add worldcup/daily_eval.py tests/test_daily_eval.py
git commit -m "feat: push daily eval digest via wxpusher"
```

---

### Task 3: LaunchAgent 安装、真实 smoke 与文档

**Files:**
- Create: `~/Library/LaunchAgents/xin.celab.football.daily-eval.plist`（系统文件，不进 git）
- Modify: `README.md`、`RECENT_WORK.md`

- [ ] **Step 1: 真实只读 smoke（赛前应为 no_new_results，不发推送）**

```bash
cd /Users/eagod/ai-dev/足彩
python3 -m worldcup.daily_eval --notify
```

Expected: 首批比赛完赛前输出 `"status": "no_new_results"`、`"notification": null`，不发推送；若已有完赛则输出完整 digest 并真实推送一条（属正常首跑）。

- [ ] **Step 2: 安装每日 LaunchAgent（唯一系统状态变更，用户已确认要定时执行）**

先读现有 plist 确认 Python 路径并复用：

```bash
plutil -p ~/Library/LaunchAgents/xin.celab.football.scheduled-publish.plist | head -20
```

然后写入新 plist（`<PYTHON>` 用上一步看到的同一解释器路径）：

```bash
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
  </array>
  <key>WorkingDirectory</key><string>/Users/eagod/ai-dev/足彩</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>/Users/eagod/Library/Logs/worldcup/daily-eval.out.log</string>
  <key>StandardErrorPath</key><string>/Users/eagod/Library/Logs/worldcup/daily-eval.err.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/xin.celab.football.daily-eval.plist
launchctl print gui/$(id -u)/xin.celab.football.daily-eval | head -12
```

Expected: bootstrap 无报错；`launchctl print` 显示任务存在、calendar interval 12:30。北京时间 12:30 触发（Mac 本地时区即北京时区；当日最后场次约 11:00 前结束，留出 openfootball 更新缓冲）。

- [ ] **Step 3: kickstart 一次做端到端 smoke 并检查日志安全**

```bash
launchctl kickstart gui/$(id -u)/xin.celab.football.daily-eval
sleep 5
tail -5 ~/Library/Logs/worldcup/daily-eval.out.log
grep -ciE "api[_-]?key|secret|token|signature|cookie" ~/Library/Logs/worldcup/daily-eval.out.log ~/Library/Logs/worldcup/daily-eval.err.log
```

Expected: 日志末尾出现 digest JSON（赛前为 `no_new_results`）；敏感词计数为 0。

- [ ] **Step 4: 文档同步**

4a. `README.md`：把"开赛后日常命令"段补充为：三条手动命令仍可单独执行；新增一条说明每日自动化——

> 赛后链路已由 LaunchAgent `xin.celab.football.daily-eval` 每天北京时间 12:30 自动执行 `python3 -m worldcup.daily_eval --notify`：依次 results_capture → eval_data → backtest 并推送研究日报（完赛数、评估样本、模型 vs 市场指标、S/A 级信号命中统计）；无新增赛果不推送。手动补跑同一命令即可，幂等。

4b. `RECENT_WORK.md` 顶部按既有格式追加"2026-06-11 赛后链路每日自动化"一节：编排方式（进程内复用三个 CLI 契约）、digest 字段、无新增不推送、LaunchAgent 安装与 kickstart smoke 结果、日志敏感词扫描为 0，以及"未 push、未部署、未触发 live refresh、未调用 The Odds API"。

- [ ] **Step 5: 最终全量验证与 commit（不 push）**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
git diff --check
git add README.md RECENT_WORK.md
git commit -m "docs: record daily eval automation"
```

Expected: 全绿；无空白错误。

---

## 范围外（明确不做）

- 不改 `results_capture` / `eval_data` / `backtest` 本身（编排只调用既有契约）。
- 不做模型参数决策（mu/dc_rho 候选仍等样本，人工另案）。
- 不把日报推到公开页面（仅 WxPusher 私推）。
- 不处理 openfootball 比分滞后（幂等 upsert，次日自动补齐）。
- 不部署 ECS、不 push。

## 风险与说明

- 明早（6-12）首批完赛后建议先人工跑一遍 `python3 -m worldcup.daily_eval`（不带 `--notify`）核对 `_extract_score` 真实格式解析正常，再放心交给 12:30 的定时任务；若解析有问题，按 RECENT_WORK 已知风险条目回到 `_extract_score` 调整。
- `signal_tally` 用当前 snapshot 的信号与赛果，口径与页面"预测结果"列一致；累计口径（跨轮全量统计）等样本多了再另案设计。
