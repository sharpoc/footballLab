"""Generate the daily strict World Cup postmatch publisher LaunchAgent plist."""
from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path
from typing import Any


DEFAULT_LABEL = "xin.celab.football.postmatch-publish"
DEFAULT_ENDPOINT = "https://football.celab.xin/api/ingest/snapshot"
DEFAULT_HOUR = 16
DEFAULT_MINUTE = 40
DEFAULT_LOG_DIR = Path.home() / "Library" / "Logs" / "worldcup"
DEFAULT_LAUNCH_AGENT_PATH = (
    Path.home() / "Library" / "LaunchAgents" / f"{DEFAULT_LABEL}.plist"
)


def _project_path(workdir: Path, relative: str) -> str:
    return str(workdir / relative)


def build_postmatch_launch_agent(
    *,
    python_path: str | Path = sys.executable,
    workdir: str | Path = Path.cwd(),
    label: str = DEFAULT_LABEL,
    endpoint: str = DEFAULT_ENDPOINT,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    run_at_load: bool = False,
) -> dict[str, Any]:
    if not 0 <= int(hour) <= 23 or not 0 <= int(minute) <= 59:
        raise ValueError("invalid_launch_agent_time")

    root = Path(workdir).expanduser()
    program_args = [
        str(Path(python_path).expanduser()),
        "-m",
        "worldcup.postmatch_publish",
        "--live",
        "--endpoint",
        endpoint,
        "--base-snapshot",
        _project_path(root, "data/cache/analysis_snapshot.json"),
        "--out",
        _project_path(root, "data/cache/wc2026_postmatch_snapshot.json"),
        "--state",
        _project_path(root, "data/cache/wc2026_postmatch_state.json"),
        "--openfootball-cache",
        _project_path(root, "data/cache/openfootball_2026.json"),
        "--history",
        _project_path(root, "data/local/history"),
        "--results",
        _project_path(root, "data/local/results/wc2026_results.csv"),
        "--finished-store",
        _project_path(root, "data/local/finished_record_store.json"),
        "--env",
        _project_path(root, ".env"),
    ]
    logs = Path(log_dir).expanduser()
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "WorkingDirectory": str(root),
        "StandardOutPath": str(logs / "postmatch-publish.out.log"),
        "StandardErrorPath": str(logs / "postmatch-publish.err.log"),
        "StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)},
        "RunAtLoad": bool(run_at_load),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def write_postmatch_launch_agent(
    path: str | Path,
    **kwargs: Any,
) -> Path:
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    plist = build_postmatch_launch_agent(**kwargs)
    with out.open("wb") as fh:
        plistlib.dump(plist, fh, sort_keys=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the daily postmatch publisher LaunchAgent plist. Does not load launchd."
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--workdir", default=str(Path.cwd()))
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--run-at-load", action="store_true")
    args = parser.parse_args(argv)

    build_args = {
        "python_path": args.python,
        "workdir": args.workdir,
        "label": args.label,
        "endpoint": args.endpoint,
        "hour": args.hour,
        "minute": args.minute,
        "log_dir": args.log_dir,
        "run_at_load": args.run_at_load,
    }
    plist = build_postmatch_launch_agent(**build_args)
    written = write_postmatch_launch_agent(args.out, **build_args) if args.out else None
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
