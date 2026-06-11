from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from worldcup.ledger import build_snapshot_change_items

WXPUSHER_REMIND = "/Users/eagod/ai-dev/wxpusher-reminder/bin/wxpusher-remind"
NO_PREVIOUS_TITLE = "暂无上一轮数据"
NO_CHANGE_TITLE = "最近一轮无显著变化"


def load_snapshot_if_exists(path: str | Path) -> dict[str, Any] | None:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return None
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _is_actionable_change(item: dict[str, str]) -> bool:
    return item.get("title") not in {NO_PREVIOUS_TITLE, NO_CHANGE_TITLE}


def build_change_notification(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    items = [
        item
        for item in build_snapshot_change_items(previous_snapshot, current_snapshot, limit=limit)
        if _is_actionable_change(item)
    ]
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
        "世界杯信号更新",
        f"run: {run_id}",
        f"显著变化：{total} 条",
        "",
    ]
    for item in items[: max(1, limit)]:
        lines.append(f"{item['title']}")
        lines.append(f"{item['detail']}")
        lines.append("")

    return {
        "should_send": True,
        "summary": f"世界杯信号更新：{total} 条变化",
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
