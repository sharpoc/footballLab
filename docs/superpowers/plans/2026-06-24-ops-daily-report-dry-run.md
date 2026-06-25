# Ops Daily Report Dry-Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the P9.10 `worldcup.ops_check --format summary` output into a local daily report artifact that can be reviewed by hand before any notification or deployment work.

**Architecture:** Add a small `worldcup.ops_daily_report` wrapper that runs `run_ops_check()` in forced local-only mode, derives a sanitized daily payload only from `result["report"]`, and writes either Markdown or JSON under ignored `data/cache/`. The command is dry-run by design: no public HTTP checks, no SSH remote checks, no live refresh, no `.env` read, no The Odds API call, no WxPusher send, no ECS deployment, and no LaunchAgent change.

**Tech Stack:** Python standard library, existing `worldcup.ops_check`, existing custom `tests/run_tests.py` runner.

---

## File Structure

- Create: `worldcup/ops_daily_report.py`
  - New local-only CLI: `python3 -m worldcup.ops_daily_report`.
  - Calls `run_ops_check(public_base_url=None, remote_host=None, ...)`.
  - Builds a stable daily report payload from `result["report"]`.
  - Writes Markdown or JSON to ignored `data/cache/ops_daily_report_<YYYYMMDDTHHMMSSZ>.<md|json>`.
  - Prints a short machine-readable write summary to stdout.
- Create: `tests/test_ops_daily_report.py`
  - Unit tests for sanitized payload construction, Markdown formatting, default path generation, and CLI file writing.
  - Tests must prove raw odds/bookmaker/price/API-like values from the full ops payload cannot leak into the daily report.
- Modify: `README.md`
  - Document local daily report dry-run usage and clarify it is separate from notification/deployment.
- Modify after implementation: `RECENT_WORK.md`
  - Record implementation, verification, generated ignored artifact shape, and safety boundaries.

## Scope Guard

- P9.11 does not change P9.10 `worldcup.ops_check` issue semantics.
- P9.11 does not add WxPusher, cron, LaunchAgent, ECS deployment, public preview, live refresh, or The Odds API calls.
- P9.11 does not read `.env` and does not print secret-like values.
- P9.11 writes only to ignored `data/cache/` by default.
- P9.11 reports `club_rating_pending` as existing runner context only; it does not lift or reinterpret the rating policy.
- P9.11 is a local report artifact, not a betting signal and not a public user-facing page.

## Current Baseline

P9.10 provides the sanitized summary source:

```bash
python3 -m worldcup.ops_check --no-public --no-remote --format summary
```

Expected current local shape:

```text
ops_check: ok errors=0 warnings=0
CSL live odds: ok events=8 fixtures=8 odds_events=8
  guards: synthetic=false alias_unmatched=0 invalid_odds=0 issues=none
  runner: ok matches=8 rating_policy=club_rating_pending club_rating=sample_replay
  warnings=club_rating_pending,odds_event_only strong_grades=none
```

The exact warning count can differ by local log state; P9.11 must preserve the underlying `ops_check` status instead of hiding it.

## Task 1: Add Daily Report Contract Tests

**Files:**
- Create: `tests/test_ops_daily_report.py`
- Create in Task 2: `worldcup/ops_daily_report.py`

- [ ] **Step 1: Create failing tests for sanitized report construction and Markdown formatting**

Create `tests/test_ops_daily_report.py` with these tests:

```python
from __future__ import annotations

import io
import json
import plistlib
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.ops_daily_report import (
    build_daily_ops_report,
    default_report_path,
    format_daily_ops_markdown,
    main as ops_daily_main,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_plist(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(
            {
                "Label": "xin.celab.football.scheduled-publish",
                "ProgramArguments": [
                    "/opt/python/bin/python3",
                    "-m",
                    "worldcup.scheduled_publish",
                    "--live",
                ],
                "WorkingDirectory": "/Users/eagod/ai-dev/足彩",
                "StandardOutPath": str(path.parent / "scheduled-publish.out.log"),
                "StandardErrorPath": str(path.parent / "scheduled-publish.err.log"),
                "StartInterval": 900,
            },
            fh,
        )


def _minimal_ops_result() -> dict:
    return {
        "summary": {"errors": 0, "warnings": 0},
        "report": {
            "schema_version": 1,
            "status": "ok",
            "errors": 0,
            "warnings": 0,
            "csl_live_odds": {
                "status": "ok",
                "competition_id": "csl_2026",
                "events": 8,
                "fixtures": 8,
                "odds_events": 8,
                "sport_keys": ["soccer_china_superleague"],
                "observed_at": "2026-06-24T01:51:18+00:00",
                "provider": "theoddsapi_secondary",
                "quota_remaining": 248,
                "quota_last": 3,
                "has_synthetic_marker": False,
                "club_alias_unmatched_count": 0,
                "invalid_odds_count": 0,
                "runner_status": "ok",
                "runner_matches": 8,
                "runner_warnings": ["club_rating_pending", "odds_event_only"],
                "runner_errors_count": 0,
                "runner_strong_grades": [],
                "rating_policy": "club_rating_pending",
                "club_rating_mode": "sample_replay",
                "club_rating_matches_replayed": 840,
                "club_rating_teams_rated": 22,
                "issues": [],
            },
        },
        "local": {
            "csl_live_odds": {
                "raw_should_not_leak": {
                    "bookmaker": "must-not-leak",
                    "price": 2.05,
                    "api_key": "secret-like-value",
                }
            }
        },
    }


def test_build_daily_ops_report_uses_only_sanitized_ops_report():
    daily = build_daily_ops_report(
        _minimal_ops_result(),
        generated_at="2026-06-24T08:00:00Z",
    )

    assert daily["schema_version"] == 1
    assert daily["generated_at"] == "2026-06-24T08:00:00Z"
    assert daily["mode"] == "local_dry_run"
    assert daily["status"] == "ok"
    assert daily["errors"] == 0
    assert daily["warnings"] == 0
    assert daily["scope"] == {
        "public": False,
        "remote": False,
        "live_refresh": False,
        "notify": False,
        "deploy": False,
    }
    assert daily["delivery"] == {
        "status": "skipped",
        "reason": "dry_run_no_notification",
    }
    assert daily["csl_live_odds"]["events"] == 8
    assert daily["csl_live_odds"]["runner_warnings"] == [
        "club_rating_pending",
        "odds_event_only",
    ]
    text = json.dumps(daily, ensure_ascii=False, sort_keys=True)
    assert "must-not-leak" not in text
    assert "2.05" not in text
    assert "secret-like-value" not in text
    assert "bookmaker" not in text
    assert "api_key" not in text


def test_format_daily_ops_markdown_is_reviewable_and_safe():
    daily = build_daily_ops_report(
        _minimal_ops_result(),
        generated_at="2026-06-24T08:00:00Z",
    )

    markdown = format_daily_ops_markdown(daily)

    assert markdown.startswith("# Ops Daily Report\n")
    assert "generated_at: 2026-06-24T08:00:00Z" in markdown
    assert "mode: local_dry_run" in markdown
    assert "delivery: skipped (dry_run_no_notification)" in markdown
    assert "仅用于研究分析，不构成投注建议。" in markdown
    assert "CSL live odds: ok events=8 fixtures=8 odds_events=8" in markdown
    assert "warnings=club_rating_pending,odds_event_only strong_grades=none" in markdown
    assert "must-not-leak" not in markdown
    assert "2.05" not in markdown
    assert "api_key" not in markdown


def test_default_report_path_uses_cache_and_timestamp_extension():
    path = default_report_path(
        Path("/tmp/worldcup"),
        generated_at="2026-06-24T08:00:00Z",
        output_format="markdown",
    )

    assert path == Path("/tmp/worldcup/data/cache/ops_daily_report_20260624T080000Z.md")


def test_ops_daily_report_cli_writes_local_markdown_without_public_or_remote_checks():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        launch_agent = root / "logs" / "xin.celab.football.scheduled-publish.plist"
        _write_plist(launch_agent)
        _write(
            root / "data/cache/analysis_snapshot.json",
            json.dumps(
                {
                    "snapshot_at": "2026-06-10T10:07:25+00:00",
                    "counts": {"matches": 72},
                    "matches": [],
                    "run": {"run_id": "20260610T100725Z-live"},
                    "data_quality": {"source_errors": [], "stale_sources": []},
                }
            ),
        )
        _write(
            root / "data/cache/quota.json",
            json.dumps(
                {
                    "providers": {
                        "theoddsapi_secondary": {
                            "remaining": 248,
                            "used": 252,
                            "last": 3,
                            "api_key": "must-not-leak",
                        }
                    }
                }
            ),
        )
        _write(
            root / "data/cache/theoddsapi_csl_2026_odds.json",
            json.dumps(
                [
                    {
                        "id": "csl-event-1",
                        "sport_key": "soccer_china_superleague",
                        "commence_time": "2026-06-25T11:35:00Z",
                        "home_team": "Shanghai SIPG FC",
                        "away_team": "Beijing FC",
                        "bookmakers": [
                            {
                                "key": "must-not-leak",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "outcomes": [
                                            {"name": "Shanghai SIPG FC", "price": 2.05},
                                            {"name": "Beijing FC", "price": 3.10},
                                            {"name": "Draw", "price": 3.30},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_odds_refresh.json",
            json.dumps(
                {
                    "status": "fetched",
                    "events": 1,
                    "observed_at": "2026-06-24T01:51:18+00:00",
                    "has_synthetic_marker": False,
                    "theoddsapi_provider": "theoddsapi_secondary",
                    "quota_remaining": 248,
                    "quota_last": 3,
                }
            ),
        )
        _write(
            root / "data/local/diagnostics/csl_live_league_runner_check.json",
            json.dumps(
                {
                    "status": "ok",
                    "counts": {
                        "fixtures": 1,
                        "odds_events": 1,
                        "match_inputs": 1,
                        "matches": 1,
                    },
                    "warnings": ["club_rating_pending", "odds_event_only"],
                    "club_alias_unmatched": [],
                    "invalid_odds_count": 0,
                    "rating_policy": "club_rating_pending",
                    "club_rating": {
                        "mode": "sample_replay",
                        "matches_replayed": 840,
                        "teams_rated": 22,
                        "errors": [],
                    },
                    "strong_grades": [],
                }
            ),
        )
        out = root / "data/cache/custom_report.md"
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = ops_daily_main(
                [
                    "--root",
                    str(root),
                    "--launch-agent",
                    str(launch_agent),
                    "--local-log",
                    str(root / "logs" / "missing.out.log"),
                    "--pre-match-launch-agent",
                    "none",
                    "--generated-at",
                    "2026-06-24T08:00:00Z",
                    "--out",
                    str(out),
                ]
            )

        assert exit_code == 0
        assert out.exists()
        report_text = out.read_text(encoding="utf-8")
        write_summary = json.loads(stdout.getvalue())
        assert write_summary == {
            "status": "ok",
            "errors": 0,
            "warnings": 0,
            "mode": "local_dry_run",
            "format": "markdown",
            "path": str(out),
        }
        assert "public" not in report_text.lower()
        assert "remote" not in report_text.lower()
        assert "CSL live odds: ok events=1 fixtures=1 odds_events=1" in report_text
        assert "must-not-leak" not in report_text
        assert "2.05" not in report_text
        assert "api_key" not in report_text
```

- [ ] **Step 2: Run the new test file and verify it fails before implementation**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_daily_report.py")
spec = importlib.util.spec_from_file_location("test_ops_daily_report", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
PY
```

Expected: FAIL with `ModuleNotFoundError: No module named 'worldcup.ops_daily_report'`.

## Task 2: Implement Local-Only Daily Report Writer

**Files:**
- Create: `worldcup/ops_daily_report.py`
- Test: `tests/test_ops_daily_report.py`

- [ ] **Step 1: Add the implementation module**

Create `worldcup/ops_daily_report.py`:

```python
"""Local dry-run daily report writer for ops_check."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worldcup.ops_check import (
    DEFAULT_LAUNCH_AGENT,
    DEFAULT_LINEUP_AUDIT_PATH,
    DEFAULT_LOCAL_LOGS,
    format_ops_report,
    run_ops_check,
)

RESEARCH_NOTICE = "仅用于研究分析，不构成投注建议。"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stamp(generated_at: str) -> str:
    return _parse_utc(generated_at).strftime("%Y%m%dT%H%M%SZ")


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def default_report_path(root: Path, generated_at: str, output_format: str) -> Path:
    extension = "md" if output_format == "markdown" else "json"
    return root / "data/cache" / f"ops_daily_report_{_stamp(generated_at)}.{extension}"


def build_daily_ops_report(
    ops_result: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    report = _dict(ops_result.get("report"))
    csl = _dict(report.get("csl_live_odds"))
    return {
        "schema_version": 1,
        "generated_at": generated_at or _utc_now_iso(),
        "mode": "local_dry_run",
        "scope": {
            "public": False,
            "remote": False,
            "live_refresh": False,
            "notify": False,
            "deploy": False,
        },
        "research_notice": RESEARCH_NOTICE,
        "status": report.get("status", "unknown"),
        "errors": _as_int(report.get("errors")),
        "warnings": _as_int(report.get("warnings")),
        "ops_summary": format_ops_report(report),
        "csl_live_odds": csl,
        "delivery": {
            "status": "skipped",
            "reason": "dry_run_no_notification",
        },
    }


def format_daily_ops_markdown(daily_report: dict[str, Any]) -> str:
    delivery = _dict(daily_report.get("delivery"))
    lines = [
        "# Ops Daily Report",
        "",
        f"- generated_at: {daily_report.get('generated_at', 'n/a')}",
        f"- mode: {daily_report.get('mode', 'n/a')}",
        f"- status: {daily_report.get('status', 'unknown')}",
        f"- errors: {_as_int(daily_report.get('errors'))}",
        f"- warnings: {_as_int(daily_report.get('warnings'))}",
        f"- delivery: {delivery.get('status', 'unknown')} ({delivery.get('reason', 'n/a')})",
        "",
        str(daily_report.get("research_notice") or RESEARCH_NOTICE),
        "",
        "```text",
        str(daily_report.get("ops_summary") or "").strip(),
        "```",
        "",
    ]
    return "\n".join(lines)


def write_daily_ops_report(
    daily_report: dict[str, Any],
    path: Path,
    *,
    output_format: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(
            json.dumps(daily_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        path.write_text(format_daily_ops_markdown(daily_report), encoding="utf-8")
    return path


def _optional_path(value: str) -> str | Path | None:
    if value.lower() in {"none", "skip", "skipped"}:
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a local dry-run daily report from worldcup.ops_check."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--launch-agent", default=str(DEFAULT_LAUNCH_AGENT))
    parser.add_argument("--local-log", action="append", dest="local_logs")
    parser.add_argument("--pre-match-launch-agent", default="none")
    parser.add_argument("--pre-match-log", action="append", dest="pre_match_logs")
    parser.add_argument("--lineup-audit", default=str(DEFAULT_LINEUP_AUDIT_PATH))
    parser.add_argument("--generated-at")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--out")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args(argv)

    root = Path(args.root)
    generated_at = args.generated_at or _utc_now_iso()
    ops_result = run_ops_check(
        root=root,
        public_base_url=None,
        remote_host=None,
        launch_agent_path=args.launch_agent,
        local_log_paths=list(DEFAULT_LOCAL_LOGS if args.local_logs is None else args.local_logs),
        pre_match_launch_agent_path=_optional_path(args.pre_match_launch_agent),
        pre_match_log_paths=list([] if args.pre_match_logs is None else args.pre_match_logs),
        lineup_audit_path=args.lineup_audit,
        timeout=args.timeout,
    )
    daily_report = build_daily_ops_report(ops_result, generated_at=generated_at)
    out_path = Path(args.out) if args.out else default_report_path(root, generated_at, args.format)
    written = write_daily_ops_report(daily_report, out_path, output_format=args.format)
    print(
        json.dumps(
            {
                "status": daily_report["status"],
                "errors": daily_report["errors"],
                "warnings": daily_report["warnings"],
                "mode": daily_report["mode"],
                "format": args.format,
                "path": str(written),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if daily_report["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the focused tests and verify they pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_daily_report.py")
spec = importlib.util.spec_from_file_location("test_ops_daily_report", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in sorted(dir(module)):
    if name.startswith("test_"):
        getattr(module, name)()
print("tests/test_ops_daily_report.py direct tests passed")
PY
```

Expected: PASS and the direct runner prints `tests/test_ops_daily_report.py direct tests passed`.

## Task 3: Document P9.11 Daily Report Dry-Run

**Files:**
- Modify: `README.md`
- Modify after implementation: `RECENT_WORK.md`

- [ ] **Step 1: Add README usage under the existing ops_check section**

In `README.md`, after the `worldcup.ops_check --no-public --no-remote --format summary` command block, add:

```markdown
P9.11 起，可把上述本地巡检摘要写成本地 dry-run 日报文件：

```bash
python3 -m worldcup.ops_daily_report
```

默认输出到被忽略的 `data/cache/ops_daily_report_<UTC>.md`，只跑本地 `ops_check`，并强制跳过公网 HTTP、ECS remote、live refresh、通知发送和部署动作。该日报只使用 `ops_check` 已脱敏的 `report` 摘要，不输出 raw odds、bookmaker、market、price、URL、API key、HMAC、`.env` 值或原始响应。需要 JSON 产物时：

```bash
python3 -m worldcup.ops_daily_report --format json
```
```

- [ ] **Step 2: Add RECENT_WORK entry after implementation**

Prepend this entry to `RECENT_WORK.md`:

```markdown
## 2026-06-24 P9.11 ops_check 本地日报 dry-run 计划

- 新增 implementation plan：`docs/superpowers/plans/2026-06-24-ops-daily-report-dry-run.md`。
- 计划目标是在 P9.10 `worldcup.ops_check --format summary` 基础上，新增本地 `worldcup.ops_daily_report` dry-run，把已脱敏的 `report` 摘要写入 ignored `data/cache/ops_daily_report_<UTC>.md` 或 `.json`。
- 计划安全边界：默认只做本地 `ops_check`，强制跳过公网 HTTP、ECS remote、live refresh、通知发送和部署动作；不读取 `.env`，不调用 The Odds API，不消耗 quota，不改 LaunchAgent，不接 WxPusher。
- 该日报只作为人工巡检产物，不构成投注建议，不输出资金或执行建议；`club_rating_pending` 仍作为 runner context 保留，不解除。
```

## Task 4: Verification

**Files:**
- No code changes beyond Tasks 1-3.

- [ ] **Step 1: Run formatting guard**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 2: Run ops daily focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import importlib.util
from pathlib import Path
module_path = Path("tests/test_ops_daily_report.py")
spec = importlib.util.spec_from_file_location("test_ops_daily_report", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
for name in sorted(dir(module)):
    if name.startswith("test_"):
        getattr(module, name)()
print("tests/test_ops_daily_report.py direct tests passed")
PY
```

Expected: PASS.

- [ ] **Step 3: Run full project tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass. The previous local baseline after dependency install was `558/558 tests passed`; the expected count after adding this test file is higher.

- [ ] **Step 4: Run local daily report dry-run on this workspace**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.ops_daily_report --generated-at 2026-06-24T08:00:00Z
```

Expected:

```json
{"errors":0,"format":"markdown","mode":"local_dry_run","path":"data/cache/ops_daily_report_20260624T080000Z.md","status":"ok","warnings":0}
```

The exact `warnings` value can be higher if local logs contain historical warnings. The command must not perform public HTTP checks, SSH remote checks, live refresh, notification, deployment, `.env` read, or The Odds API calls.

## Antagonistic Self-Review

- Root cause fit: P9.10 made the summary readable; P9.11 only adds a stable local artifact. This avoids prematurely coupling report content to WxPusher or ECS.
- Scope control: the new module is a wrapper around sanitized `result["report"]`, not a second health-check implementation.
- Data safety: tests intentionally put raw bookmaker, raw decimal price, and API-like fields in the full ops payload and assert they do not appear in the daily artifact.
- Quota and live risk: forced `public_base_url=None` and `remote_host=None` prevent public/ECS checks; no refresh runner or The Odds API client is imported.
- Notification risk: delivery is explicitly `skipped` with `dry_run_no_notification`; real WxPusher wiring needs a separate confirmation and plan.
- Operational caveat: writing a timestamped local file creates ignored state under `data/cache/`; this is acceptable for dry-run, but implementation should mention the generated path in `RECENT_WORK.md` only as a pattern, not as a permanent artifact.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-ops-daily-report-dry-run.md`. Two execution options:

**1. Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Choose an option before implementation begins.
