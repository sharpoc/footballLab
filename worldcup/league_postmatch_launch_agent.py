"""Generate, but never install, the six-league postmatch LaunchAgent plist."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_LABEL = "xin.celab.football.league-postmatch"
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "worldcup"
DEFAULT_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
)
SCHEDULE = [
    {"Hour": 10, "Minute": 30},
    {"Hour": 16, "Minute": 30},
]


def _absolute_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def build_league_postmatch_launch_agent(
    *,
    python: str,
    workdir: str,
    full_live: bool = False,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> dict[str, Any]:
    """Return a deterministic plist for the read-only or explicitly live runner."""
    root = _absolute_path(workdir)
    python_path = _absolute_path(python)
    logs = _absolute_path(log_dir)
    program_arguments = [
        str(python_path),
        "-m",
        "worldcup.league_postmatch_runner",
        "--root",
        str(root),
    ]
    if full_live:
        program_arguments.extend(["--live", "--write", "--notify"])
    return {
        "Label": DEFAULT_LABEL,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "league-postmatch.out.log"),
        "StandardErrorPath": str(logs / "league-postmatch.err.log"),
        "StartCalendarInterval": SCHEDULE,
        "RunAtLoad": False,
    }


def write_league_postmatch_launch_agent(
    path: str | Path,
    *,
    python: str,
    workdir: str,
    full_live: bool = False,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> Path:
    """Atomically replace only ``path`` with a generated plist."""
    requested_path = Path(path).expanduser()
    output_path = _absolute_path(requested_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = plistlib.dumps(
        build_league_postmatch_launch_agent(
            python=python,
            workdir=workdir,
            full_live=full_live,
            log_dir=log_dir,
        ),
        sort_keys=True,
    )
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
        directory = os.open(output_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return requested_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate the six-league postmatch LaunchAgent plist without installing it."
    )
    parser.add_argument("--out", default=None, help="Requested plist path; omit to print JSON only.")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--workdir", default=str(Path.cwd()))
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument(
        "--full-live",
        action="store_true",
        help="Include the exact --live --write --notify runner flags.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    build_args = {
        "python": args.python,
        "workdir": args.workdir,
        "full_live": args.full_live,
        "log_dir": args.log_dir,
    }
    plist = build_league_postmatch_launch_agent(**build_args)
    written = write_league_postmatch_launch_agent(args.out, **build_args) if args.out else None
    print(json.dumps({
        "status": "written" if written else "dry_run",
        "path": str(written) if written else None,
        "launch_agent_path": str(DEFAULT_LAUNCH_AGENT_PATH),
        "plist": plist,
        "loaded": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
