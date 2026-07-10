from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from worldcup.ledger import (
    format_match_decision_market_label,
    format_matchup_label,
    format_probability,
)

WXPUSHER_REMIND = "/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind"
PICK_PROBABILITY_CHANGE = 0.02
PICK_ODDS_CHANGE = 0.05


def load_snapshot_if_exists(path: str | Path) -> dict[str, Any] | None:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return None
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _match_key(match: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(match.get("kickoff_at_utc") or ""),
        str(match.get("home_team") or "").casefold(),
        str(match.get("away_team") or "").casefold(),
    )


def _active_pick(match: dict[str, Any] | None) -> dict[str, Any] | None:
    decision = (match or {}).get("match_decision")
    if not isinstance(decision, dict):
        return None
    if decision.get("label") == "NO_CLEAN_MARKET":
        return None
    if not decision.get("market") or not decision.get("selection"):
        return None
    return decision


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_identity(decision: dict[str, Any]) -> tuple[str, str, float | None]:
    return (
        str(decision.get("market") or ""),
        str(decision.get("selection") or ""),
        _as_float(decision.get("line")),
    )


def _format_odds(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def _pick_change_items(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    *,
    limit: int,
) -> list[dict[str, str]]:
    if previous_snapshot is None:
        return []
    previous_matches = {
        _match_key(match): match for match in previous_snapshot.get("matches") or []
    }
    items: list[dict[str, str]] = []
    for match in current_snapshot.get("matches") or []:
        previous = previous_matches.get(_match_key(match))
        if previous is None:
            continue
        before = _active_pick(previous)
        after = _active_pick(match)
        changes: list[str] = []
        if before is None and after is not None:
            changes.append(f"新增首选：{format_match_decision_market_label(after)}")
        elif before is not None and after is None:
            changes.append("原首选撤销，当前暂无可靠首选")
        elif before is not None and after is not None:
            before_market = format_match_decision_market_label(before)
            after_market = format_match_decision_market_label(after)
            if _pick_identity(before) != _pick_identity(after):
                changes.append(f"首选 {before_market} → {after_market}")
            before_probability = _as_float(before.get("p_hit_safe"))
            after_probability = _as_float(after.get("p_hit_safe"))
            if (
                before_probability is not None
                and after_probability is not None
                and abs(after_probability - before_probability) >= PICK_PROBABILITY_CHANGE
            ):
                changes.append(
                    "安全命中率 "
                    f"{format_probability(before_probability)} → {format_probability(after_probability)}"
                )
            before_odds = _as_float(before.get("odds"))
            after_odds = _as_float(after.get("odds"))
            if (
                before_odds is not None
                and after_odds is not None
                and abs(after_odds - before_odds) >= PICK_ODDS_CHANGE
            ):
                changes.append(f"参考赔率 {_format_odds(before_odds)} → {_format_odds(after_odds)}")
        if not changes:
            continue
        items.append(
            {
                "title": format_matchup_label(match.get("home_team"), match.get("away_team")),
                "detail": "；".join(changes),
            }
        )
        if len(items) >= max(1, limit):
            break
    return items


def build_change_notification(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    items = _pick_change_items(previous_snapshot, current_snapshot, limit=limit)
    if not items:
        return {
            "should_send": False,
            "summary": "",
            "content": "",
            "items": [],
        }

    run = current_snapshot.get("run") or {}
    run_id = run.get("run_id") or current_snapshot.get("snapshot_at") or "unknown"
    total = len(items)
    lines = [
        "世界杯本场首选更新",
        f"run: {run_id}",
        f"显著变化：{total} 场",
        "",
    ]
    for item in items[: max(1, limit)]:
        lines.append(f"{item['title']}")
        lines.append(f"{item['detail']}")
        lines.append("")

    return {
        "should_send": True,
        "summary": f"世界杯本场首选更新：{total} 场变化",
        "content": "\n".join(lines).strip(),
        "items": items,
    }


def send_wxpusher_notification(
    content: str,
    *,
    summary: str,
    command: str = WXPUSHER_REMIND,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    try:
        result = runner(
            [command, "--summary", summary, content],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"status": "failed", "exit_code": None}
    if result.returncode != 0:
        return {"status": "failed", "exit_code": result.returncode}
    return {"status": "sent", "exit_code": 0}
