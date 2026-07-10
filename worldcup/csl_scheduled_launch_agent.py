from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from worldcup.csl_scheduled_publish import (
    DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    DEFAULT_ENDPOINT,
    DEFAULT_MIN_INTERVAL_SECONDS,
)

DEFAULT_LABEL = "xin.celab.football.csl-scheduled-publish"
DEFAULT_START_INTERVAL_SECONDS = 30 * 60
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "worldcup"
DEFAULT_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
)


def _project_path(workdir: str | Path, relative: str) -> str:
    return str(Path(workdir).expanduser() / relative)


def build_csl_scheduled_launch_agent(
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    label: str = DEFAULT_LABEL,
    start_interval: int = DEFAULT_START_INTERVAL_SECONDS,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
    endpoint: str = DEFAULT_ENDPOINT,
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
) -> dict[str, Any]:
    root = Path(workdir).expanduser()
    program_args = [
        str(python_path),
        "-m",
        "worldcup.csl_scheduled_publish",
        "--live",
        "--cache-dir",
        _project_path(root, "data/cache"),
        "--quota-path",
        _project_path(root, "data/cache/quota.json"),
        "--snapshot-path",
        _project_path(root, "data/cache/csl_publish_snapshot.json"),
        "--diagnostics-snapshot-path",
        _project_path(root, "data/local/diagnostics/csl_live_league_snapshot.json"),
        "--env",
        _project_path(root, ".env"),
        "--endpoint",
        endpoint,
        "--min-interval-seconds",
        str(int(min_interval_seconds)),
        "--discovery-interval-seconds",
        str(int(discovery_interval_seconds)),
    ]
    logs = Path(log_dir).expanduser()
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "csl-scheduled-publish.out.log"),
        "StandardErrorPath": str(logs / "csl-scheduled-publish.err.log"),
        "StartInterval": int(start_interval),
        "RunAtLoad": bool(run_at_load),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def write_csl_scheduled_launch_agent(
    path: str | Path,
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    label: str = DEFAULT_LABEL,
    start_interval: int = DEFAULT_START_INTERVAL_SECONDS,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
    endpoint: str = DEFAULT_ENDPOINT,
    min_interval_seconds: int = DEFAULT_MIN_INTERVAL_SECONDS,
    discovery_interval_seconds: int = DEFAULT_DISCOVERY_INTERVAL_SECONDS,
) -> Path:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    plist = build_csl_scheduled_launch_agent(
        python_path=python_path,
        workdir=workdir,
        label=label,
        start_interval=start_interval,
        log_dir=log_dir,
        run_at_load=run_at_load,
        endpoint=endpoint,
        min_interval_seconds=min_interval_seconds,
        discovery_interval_seconds=discovery_interval_seconds,
    )
    with out.open("wb") as fh:
        plistlib.dump(plist, fh, sort_keys=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the CSL scheduled publish LaunchAgent plist. Does not load launchd."
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--workdir", default=str(Path.cwd()))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--interval", type=int, default=DEFAULT_START_INTERVAL_SECONDS)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--run-at-load", action="store_true")
    parser.add_argument("--min-interval-seconds", type=int, default=DEFAULT_MIN_INTERVAL_SECONDS)
    parser.add_argument(
        "--discovery-interval-seconds",
        type=int,
        default=DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    )
    args = parser.parse_args(argv)
    plist = build_csl_scheduled_launch_agent(
        python_path=args.python,
        workdir=args.workdir,
        label=args.label,
        start_interval=args.interval,
        log_dir=args.log_dir,
        endpoint=args.endpoint,
        run_at_load=args.run_at_load,
        min_interval_seconds=args.min_interval_seconds,
        discovery_interval_seconds=args.discovery_interval_seconds,
    )
    written = None
    if args.out:
        written = write_csl_scheduled_launch_agent(
            args.out,
            python_path=args.python,
            workdir=args.workdir,
            label=args.label,
            start_interval=args.interval,
            log_dir=args.log_dir,
            endpoint=args.endpoint,
            run_at_load=args.run_at_load,
            min_interval_seconds=args.min_interval_seconds,
            discovery_interval_seconds=args.discovery_interval_seconds,
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
