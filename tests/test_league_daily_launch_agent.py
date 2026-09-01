import contextlib
import io
import json
import plistlib
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_daily_launch_agent import build_league_daily_launch_agent, main


def test_observation_timer_has_no_live_flags():
    p = build_league_daily_launch_agent(python_path='/usr/bin/python3', workdir='/tmp/league-test')
    assert p['Label'] == 'xin.celab.football.league-daily'
    assert p['StartInterval'] == 300 and p['RunAtLoad'] is False
    assert p['ProgramArguments'] == ['/usr/bin/python3', '-m', 'worldcup.league_daily_runner', '--root', '/tmp/league-test']
    assert 'EnvironmentVariables' not in p


def test_live_timer_requires_valid_endpoint_and_explicit_positive_integer_budget():
    for endpoint, budget in [(None, 10), ('http://research.test/ingest', 10),
                             ('https://example.invalid/ingest', 10), ('https://example.com/ingest', 10),
                             ('https://research.internal-company.net/ingest', None), ('https://research.internal-company.net/ingest', 0),
                             ('https://research.internal-company.net/ingest', -1), ('https://research.internal-company.net/ingest', True)]:
        try:
            build_league_daily_launch_agent(full_live=True, endpoint=endpoint, daily_credit_limit=budget)
        except ValueError:
            pass
        else:
            raise AssertionError((endpoint, budget))
    p = build_league_daily_launch_agent(full_live=True, endpoint='https://research.internal-company.net/ingest', daily_credit_limit=10)
    args = p['ProgramArguments']
    for flag in ('--live', '--write', '--publish'):
        assert args.count(flag) == 1
    assert args[args.index('--daily-credit-limit') + 1] == '10'
    assert '--notify' not in args and p['RunAtLoad'] is False


def test_full_live_timer_rejects_reserved_placeholder_domains():
    for host in ('research.test', 'test', 'research.invalid', 'invalid',
                 'research.localhost', 'localhost', 'research.example', 'example',
                 'example.com', 'research.example.com', 'example.net',
                 'research.example.net', 'example.org', 'research.example.org',
                 'RESEARCH.TEST.'):
        try:
            build_league_daily_launch_agent(full_live=True, endpoint=f'https://{host}/ingest', daily_credit_limit=10)
        except ValueError:
            pass
        else:
            raise AssertionError(f'placeholder accepted: {host}')


def test_generator_cli_only_writes_explicit_output():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            assert main(['--workdir', tmp]) == 0
        assert not list(root.iterdir())
        assert json.loads(stream.getvalue())['loaded'] is False
        out = root / 'nested' / 'daily.plist'
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(['--workdir', tmp, '--out', str(out)]) == 0
        with out.open('rb') as handle:
            p = plistlib.load(handle)
        assert p['StartInterval'] == 300 and p['RunAtLoad'] is False
        assert list(root.rglob('*.plist')) == [out]
