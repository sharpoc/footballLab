from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any

from worldcup.publish import DEFAULT_ENDPOINT


DEFAULT_LABEL = "xin.celab.football.league-pre-match"
DEFAULT_START_INTERVAL_SECONDS = 300
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "worldcup"
DEFAULT_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
)


def build_league_pre_match_launch_agent(
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    start_interval: int = DEFAULT_START_INTERVAL_SECONDS,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
    full_live: bool = False,
    env_path: str | Path = ".env",
    quota_path: str | Path = "data/cache/quota.json",
    endpoint: str = DEFAULT_ENDPOINT,
    daily_credit_limit: int | None = None,
) -> dict[str, Any]:
    root = Path(workdir).expanduser()
    program_args = [
        str(Path(python_path).expanduser()),
        "-m",
        "worldcup.league_pre_match_runner",
        "--root",
        str(root),
        "--live-lineups",
        "--write-lineups",
    ]
    if full_live:
        if type(daily_credit_limit) is not int or daily_credit_limit <= 0:
            raise ValueError('daily_budget_unconfigured')
        program_args.extend([
            "--refresh-after-lineups",
            "--live-refresh",
            "--refresh-guard",
            "--publish",
            "--notify",
            "--env",
            str(env_path),
            "--quota-path",
            str(quota_path),
            "--endpoint",
            endpoint,
            '--daily-credit-limit',
            str(daily_credit_limit),
        ])
    logs = Path(log_dir).expanduser()
    return {
        "Label": DEFAULT_LABEL,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "league-pre-match.out.log"),
        "StandardErrorPath": str(logs / "league-pre-match.err.log"),
        "StartInterval": int(start_interval),
        "RunAtLoad": bool(run_at_load),
    }


def write_league_pre_match_launch_agent(
    path: str | Path,
    **kwargs: Any,
) -> Path:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    plist = build_league_pre_match_launch_agent(**kwargs)
    with out.open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the independent six-league pre-match LaunchAgent plist. "
            "Does not call launchctl."
        )
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--workdir", default=str(Path.cwd()))
    parser.add_argument("--interval", type=int, default=DEFAULT_START_INTERVAL_SECONDS)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--run-at-load", action="store_true")
    parser.add_argument("--full-live", action="store_true")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument('--daily-credit-limit', type=int)
    args = parser.parse_args(argv)
    build_args = {
        "python_path": args.python,
        "workdir": args.workdir,
        "start_interval": args.interval,
        "log_dir": args.log_dir,
        "run_at_load": args.run_at_load,
        "full_live": args.full_live,
        "env_path": args.env,
        "quota_path": args.quota_path,
        "endpoint": args.endpoint,
        'daily_credit_limit': args.daily_credit_limit,
    }
    plist = build_league_pre_match_launch_agent(**build_args)
    written = write_league_pre_match_launch_agent(args.out, **build_args) if args.out else None
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
