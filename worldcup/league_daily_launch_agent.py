"""Generate an independent daily timer; never install or load it."""
from __future__ import annotations

import argparse
import json
import plistlib
import sys
from pathlib import Path

from worldcup.league_daily_runner import valid_daily_endpoint


def build_league_daily_launch_agent(*, python_path=sys.executable, workdir=Path.cwd(),
                                   full_live=False, endpoint=None, daily_credit_limit=None) -> dict:
    root = Path(workdir).expanduser()
    args = [str(Path(python_path).expanduser()), '-m', 'worldcup.league_daily_runner', '--root', str(root)]
    if full_live:
        if not valid_daily_endpoint(endpoint):
            raise ValueError('daily_endpoint_invalid')
        if type(daily_credit_limit) is not int or daily_credit_limit <= 0:
            raise ValueError('daily_budget_unconfigured')
        args.extend(['--live', '--write', '--publish', '--endpoint', endpoint,
                     '--daily-credit-limit', str(daily_credit_limit)])
    logs = Path.home() / 'Library' / 'Logs' / 'worldcup'
    return {'Label': 'xin.celab.football.league-daily', 'ProgramArguments': args,
            'WorkingDirectory': str(root), 'StartInterval': 300, 'RunAtLoad': False,
            'StandardOutPath': str(logs / 'league-daily.out.log'),
            'StandardErrorPath': str(logs / 'league-daily.err.log')}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Generate a daily plist; does not install or load it.')
    parser.add_argument('--out')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--workdir', default=str(Path.cwd()))
    parser.add_argument('--full-live', action='store_true')
    parser.add_argument('--endpoint')
    parser.add_argument('--daily-credit-limit', type=int)
    args = parser.parse_args(argv)
    plist = build_league_daily_launch_agent(python_path=args.python, workdir=args.workdir,
        full_live=args.full_live, endpoint=args.endpoint, daily_credit_limit=args.daily_credit_limit)
    out = Path(args.out).expanduser() if args.out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open('wb') as handle:
            plistlib.dump(plist, handle, sort_keys=True)
    print(json.dumps({'status': 'written' if out else 'dry_run', 'path': str(out) if out else None,
                      'plist': plist, 'loaded': False}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
