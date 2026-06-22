from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

DEFAULT_LABEL = "xin.celab.football.pre-match"
DEFAULT_START_INTERVAL_SECONDS = 300
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "worldcup"
DEFAULT_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
)


def build_pre_match_launch_agent(
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    label: str = DEFAULT_LABEL,
    start_interval: int = DEFAULT_START_INTERVAL_SECONDS,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
    allow_live_refresh: bool = False,
) -> dict[str, Any]:
    program_args = [
        str(python_path),
        "-m",
        "worldcup.pre_match_runner",
        "--live-lineups",
        "--write-lineups",
        "--notify-missing",
        "--notify-audit",
    ]
    if allow_live_refresh:
        program_args += ["--refresh-guard", "--refresh-after-lineups", "--live-refresh"]

    logs = Path(log_dir).expanduser()
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(Path(workdir).expanduser()),
        "StandardOutPath": str(logs / "pre-match.out.log"),
        "StandardErrorPath": str(logs / "pre-match.err.log"),
        "StartInterval": int(start_interval),
        "RunAtLoad": bool(run_at_load),
    }


def write_pre_match_launch_agent(
    path: str | Path,
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    label: str = DEFAULT_LABEL,
    start_interval: int = DEFAULT_START_INTERVAL_SECONDS,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
    allow_live_refresh: bool = False,
) -> Path:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    plist = build_pre_match_launch_agent(
        python_path=python_path,
        workdir=workdir,
        label=label,
        start_interval=start_interval,
        log_dir=log_dir,
        run_at_load=run_at_load,
        allow_live_refresh=allow_live_refresh,
    )
    with open(out, "wb") as fh:
        plistlib.dump(plist, fh, sort_keys=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the pre-match LaunchAgent plist. Does not load or kickstart launchd."
    )
    parser.add_argument("--out", default=None, help="Write plist to this path. Omit to print JSON only.")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--workdir", default=str(Path.cwd()))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--interval", type=int, default=DEFAULT_START_INTERVAL_SECONDS)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--run-at-load", action="store_true")
    parser.add_argument(
        "--allow-live-refresh",
        action="store_true",
        help="Include --refresh-after-lineups --live-refresh; this can consume The Odds API quota.",
    )
    args = parser.parse_args(argv)

    plist = build_pre_match_launch_agent(
        python_path=args.python,
        workdir=args.workdir,
        label=args.label,
        start_interval=args.interval,
        log_dir=args.log_dir,
        run_at_load=args.run_at_load,
        allow_live_refresh=args.allow_live_refresh,
    )
    written = None
    if args.out:
        written = write_pre_match_launch_agent(
            args.out,
            python_path=args.python,
            workdir=args.workdir,
            label=args.label,
            start_interval=args.interval,
            log_dir=args.log_dir,
            run_at_load=args.run_at_load,
            allow_live_refresh=args.allow_live_refresh,
        )

    print(
        json.dumps(
            {
                "status": "written" if written else "dry_run",
                "path": str(written) if written else None,
                "launch_agent_path": str(DEFAULT_LAUNCH_AGENT_PATH),
                "plist": plist,
                "loaded": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
