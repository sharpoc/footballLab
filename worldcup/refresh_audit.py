"""Read-only audit helper for local refresh archives and LaunchAgent wiring."""
from __future__ import annotations

import argparse
import json
import plistlib
from pathlib import Path
from typing import Any

DEFAULT_LAUNCH_AGENT = (
    Path.home() / "Library" / "LaunchAgents" / "xin.celab.football.scheduled-publish.plist"
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_summary(path: Path) -> dict[str, Any]:
    try:
        snapshot = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "path": str(path),
            "status": "unreadable",
            "error": str(exc),
        }

    run = snapshot.get("run") or {}
    data_quality = snapshot.get("data_quality") or {}
    return {
        "path": str(path),
        "status": "ok",
        "run_id": run.get("run_id") or path.stem.removeprefix("snapshot_"),
        "snapshot_at": snapshot.get("snapshot_at"),
        "matches": (snapshot.get("counts") or {}).get("matches"),
        "source_errors_count": len(data_quality.get("source_errors") or []),
        "stale_sources": list(data_quality.get("stale_sources") or []),
    }


def summarize_history(history_dir: str | Path, limit: int = 2) -> dict[str, Any]:
    history = Path(history_dir).expanduser()
    paths = sorted(history.glob("snapshot_*.json"), reverse=True) if history.exists() else []
    return {
        "path": str(history),
        "count": len(paths),
        "latest": [_snapshot_summary(path) for path in paths[:limit]],
    }


def _module_from_args(args: list[str]) -> str | None:
    for index, arg in enumerate(args[:-1]):
        if arg == "-m":
            return args[index + 1]
    return None


def _python_from_args(args: list[str]) -> str | None:
    for arg in args:
        name = Path(arg).name
        if name.startswith("python"):
            return arg
    return args[0] if args else None


def inspect_launch_agent(path: str | Path = DEFAULT_LAUNCH_AGENT) -> dict[str, Any]:
    plist_path = Path(path).expanduser()
    if not plist_path.exists():
        return {
            "path": str(plist_path),
            "status": "missing",
        }

    try:
        with open(plist_path, "rb") as fh:
            data = plistlib.load(fh)
    except (OSError, plistlib.InvalidFileException) as exc:
        return {
            "path": str(plist_path),
            "status": "unreadable",
            "error": str(exc),
        }

    program_args = data.get("ProgramArguments") or []
    if not isinstance(program_args, list):
        program_args = []
    program_args = [str(arg) for arg in program_args]
    return {
        "path": str(plist_path),
        "status": "present",
        "label": data.get("Label"),
        "program": data.get("Program"),
        "program_arguments": program_args,
        "python": _python_from_args(program_args),
        "module": _module_from_args(program_args),
        "working_directory": data.get("WorkingDirectory"),
        "standard_out_path": data.get("StandardOutPath"),
        "standard_error_path": data.get("StandardErrorPath"),
        "start_interval": data.get("StartInterval"),
        "start_calendar_interval": data.get("StartCalendarInterval"),
        "run_at_load": data.get("RunAtLoad", False),
        "disabled": data.get("Disabled", False),
    }


def build_audit(
    history_dir: str | Path = "data/local/history",
    launch_agent_path: str | Path = DEFAULT_LAUNCH_AGENT,
    limit: int = 2,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "history": summarize_history(history_dir, limit=limit),
        "launch_agent": inspect_launch_agent(launch_agent_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit for refresh history and LaunchAgent wiring."
    )
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--launch-agent", default=str(DEFAULT_LAUNCH_AGENT))
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build_audit(args.history, args.launch_agent, limit=args.limit),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
