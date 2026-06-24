"""local dry-run daily report writer for ops_check."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
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
ALLOWED_REPORT_STATUSES = {"ok", "warn", "error"}


def _normalize_generated_at(value: str | None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _timestamp_for_path(generated_at: str) -> str:
    normalized = _normalize_generated_at(generated_at)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def _extension(output_format: str) -> str:
    return "md" if output_format == "markdown" else "json"


def default_report_path(root: Path, generated_at: str, output_format: str) -> Path:
    timestamp = _timestamp_for_path(generated_at)
    return root / "data/cache" / f"ops_daily_report_{timestamp}.{_extension(output_format)}"


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _status_from_counts(errors: int, warnings: int) -> str:
    if errors > 0:
        return "error"
    if warnings > 0:
        return "warn"
    return "ok"


def _sanitized_report(ops_result: dict[str, Any]) -> dict[str, Any]:
    raw_report = ops_result.get("report")
    report = raw_report if isinstance(raw_report, dict) else {}
    raw_status = report.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in ALLOWED_REPORT_STATUSES else None
    errors = _as_int(report.get("errors"))
    warnings = _as_int(report.get("warnings"))
    has_csl_report = isinstance(report.get("csl_live_odds"), dict)
    csl_report = report.get("csl_live_odds") if has_csl_report else {}
    expected_status = _status_from_counts(errors, warnings)
    malformed = (
        not isinstance(raw_report, dict)
        or status is None
        or not has_csl_report
        or not csl_report
        or status != expected_status
    )
    return {
        **report,
        "status": "error" if malformed else status,
        "errors": max(errors, 1) if malformed else errors,
        "warnings": warnings,
        "csl_live_odds": deepcopy(csl_report),
    }


def build_daily_ops_report(
    ops_result: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    report = _sanitized_report(ops_result)
    return {
        "schema_version": 1,
        "generated_at": _normalize_generated_at(generated_at),
        "mode": "local_dry_run",
        "scope": {
            "public": False,
            "remote": False,
            "live_refresh": False,
            "notify": False,
            "deploy": False,
        },
        "research_notice": RESEARCH_NOTICE,
        "status": report.get("status"),
        "errors": _as_int(report.get("errors")),
        "warnings": _as_int(report.get("warnings")),
        "ops_summary": format_ops_report(report),
        "csl_live_odds": deepcopy(report["csl_live_odds"]),
        "delivery": {
            "status": "skipped",
            "reason": "dry_run_no_notification",
        },
    }


def format_daily_ops_markdown(daily_report: dict[str, Any]) -> str:
    delivery = daily_report.get("delivery") if isinstance(daily_report.get("delivery"), dict) else {}
    lines = [
        "# Ops Daily Report",
        "",
        f"generated_at: {daily_report.get('generated_at')}",
        f"mode: {daily_report.get('mode')}",
        f"status: {daily_report.get('status')}",
        f"errors: {_as_int(daily_report.get('errors'))}",
        f"warnings: {_as_int(daily_report.get('warnings'))}",
        f"delivery: {delivery.get('status')} ({delivery.get('reason')})",
        "",
        str(daily_report.get("research_notice") or RESEARCH_NOTICE),
        "",
        "```text",
        str(daily_report.get("ops_summary") or ""),
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
        content = json.dumps(daily_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        content = format_daily_ops_markdown(daily_report)
    path.write_text(content, encoding="utf-8")
    return path


def _pre_match_launch_agent_arg(value: str) -> str | None:
    return None if value.lower() in {"none", "skip", "skipped"} else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a local dry-run daily ops report.")
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
    try:
        generated_at = _normalize_generated_at(args.generated_at)
    except ValueError:
        parser.error(f"invalid --generated-at: {args.generated_at}")

    ops_result = run_ops_check(
        root=root,
        public_base_url=None,
        remote_host=None,
        launch_agent_path=args.launch_agent,
        local_log_paths=list(DEFAULT_LOCAL_LOGS if args.local_logs is None else args.local_logs),
        pre_match_launch_agent_path=_pre_match_launch_agent_arg(args.pre_match_launch_agent),
        pre_match_log_paths=list([] if args.pre_match_logs is None else args.pre_match_logs),
        lineup_audit_path=args.lineup_audit,
        timeout=args.timeout,
    )
    daily_report = build_daily_ops_report(ops_result, generated_at=generated_at)
    out = Path(args.out) if args.out else default_report_path(
        root,
        generated_at=str(daily_report["generated_at"]),
        output_format=args.format,
    )
    written_path = write_daily_ops_report(daily_report, out, output_format=args.format)
    summary = {
        "status": daily_report.get("status"),
        "errors": _as_int(daily_report.get("errors")),
        "warnings": _as_int(daily_report.get("warnings")),
        "mode": daily_report.get("mode"),
        "format": args.format,
        "path": str(written_path),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["status"] != "error" and summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
