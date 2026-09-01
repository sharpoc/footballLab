import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit

from worldcup.league_daily_state import DailyStateStore
from worldcup.league_team_identity import accepted_league_team_identity_registry

NOW = '2026-09-01T10:00:00+00:00'
DONE = '2026-09-01T10:01:00+00:00'
CID = 'epl_2026_27'


def _event(**changes):
    return dict({'id': 'e1', 'sport_key': 'soccer_epl',
                 'commence_time': '2026-09-01T12:00:00+00:00',
                 'home_team': 'Arsenal', 'away_team': 'Chelsea', 'bookmakers': []}, **changes)


def _reserve(root, markets):
    request = {'attempt_id': 'a1', 'competition_id': CID, 'markets': markets}
    DailyStateStore(root).reserve(context={
        'competition_id': CID, 'markets': markets, 'acceptance_fingerprint': 'a'*64,
        'registry_fingerprint': 'b'*64, 'expected_snapshot_id': 'league-attempt-a1',
        'request_at': NOW, 'events': [], 'anchor_metadata': {}},
        date_bj='2026-09-01', attempt_id='a1', estimated=len(markets), limit=10)
    return request


class Response:
    status = 200

    def __init__(self, body, headers):
        self.body = body
        self.headers = headers

    def read(self):
        return self.body


def test_exact_markets_and_response_cost_not_estimated_quota():
    from worldcup.league_daily_runner import fetch_daily_odds
    for markets, cost in [(['h2h'], '0'), (['h2h', 'spreads', 'totals'], None)]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); request = _reserve(root, markets); calls = []
            def transport(url):
                calls.append(url)
                query = parse_qs(urlsplit(url).query)
                assert query['markets'] == [','.join(markets)]
                assert query['regions'] == ['eu']
                headers = {'x-requests-remaining': '97', 'x-requests-used': '3'}
                if cost is not None: headers['X-Requests-Last'] = cost
                return Response(json.dumps([_event()]).encode(), headers)
            result = fetch_daily_odds(request=request, env={'THE_ODDS_API_KEY_PRIMARY': 'secret-key'},
                root=root, observed_at=NOW, transport=transport, clock=lambda: DONE)
            assert result['status'] == 'fetched' and result['completed_at'] == DONE
            assert result['actual_cost'] == (0 if cost else None)
            assert len(calls) == 1 and 'secret-key' not in json.dumps(result)
            assert Path(result['response_path']).is_file()
            expected = 0 if cost else 3
            assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == expected


def test_missing_reservation_blocks_transport_and_parse_failure_keeps_charge():
    from worldcup.league_daily_runner import fetch_daily_odds
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); called = []
        def transport(url):
            called.append(url); return Response(b'{bad', {'x-requests-last': '2'})
        kwargs = dict(root=root, env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, observed_at=NOW,
                      transport=transport, clock=lambda: DONE)
        request = {'attempt_id': 'a1', 'competition_id': CID, 'markets': ['h2h']}
        assert fetch_daily_odds(request=request, **kwargs)['status'] == 'blocked'
        assert not called
        _reserve(root, ['h2h'])
        result = fetch_daily_odds(request=request, **kwargs)
        assert result['status'] == 'error' and len(called) == 1
        assert result['actual_cost'] == 2 and Path(result['response_path']).is_file()
        assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 2
        assert fetch_daily_odds(request=request, **kwargs)['status'] == 'blocked'
        assert len(called) == 1


def test_discovery_strict_identity_time_sport_duplicates_and_terminal():
    from worldcup.league_daily_runner import discover_events
    registry = accepted_league_team_identity_registry()
    for bad in [_event(sport_key=None), _event(commence_time='2026-09-01T12:00:00'),
                _event(home_team='invented'), _event(completed=True),
                _event(commence_time=DONE)]:
        result = discover_events(raw_events=[bad], competition_id=CID, registry=registry, observed_at=DONE)
        assert not result['events'] and result['rejected']
    result = discover_events(raw_events=[_event(), _event(away_team='Liverpool'), _event(id='e2')],
                             competition_id=CID, registry=registry, observed_at=DONE)
    assert [row['source_event_id'] for row in result['events']] == ['e2']
    assert result['events'][0]['home_canonical'] == 'arsenal'


def test_same_response_builds_pick_once_and_cross_kickoff_creates_no_snapshot():
    from worldcup.league_daily_runner import fetch_daily_odds, commit_fetched_daily_odds
    from worldcup.league_acceptance import acceptance_fingerprint, evaluate_league_acceptance
    report = {'schema_version': 1, 'competitions': {CID: evaluate_league_acceptance(CID, {
        name: {'verified': True, 'fingerprint': name} for name in
        ('sport_catalog', 'odds_sample', 'team_identity', 'result_contract')})}}
    for done, expected in [(DONE, 'refreshed'), ('2026-09-01T12:00:01+00:00', 'empty')]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); request = _reserve(root, ['h2h']); calls = []
            event = _event(bookmakers=[{'key': 'test', 'title': 'Test', 'last_update': NOW,
                'markets': [{'key': 'h2h', 'last_update': NOW, 'outcomes': [
                    {'name': 'Arsenal', 'price': 1.6}, {'name': 'Chelsea', 'price': 5.0},
                    {'name': 'Draw', 'price': 4.0}]}]}])
            def transport(url):
                calls.append(url); return Response(json.dumps([event]).encode(), {'x-requests-last': '1'})
            fetched = fetch_daily_odds(request=request, root=root, observed_at=NOW,
                env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, transport=transport, clock=lambda: done)
            frozen = []
            result = commit_fetched_daily_odds(request=request, fetched=fetched, root=root,
                env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, acceptance_report=report,
                guarded_acceptance_fingerprint=acceptance_fingerprint(report),
                registry=accepted_league_team_identity_registry(), before_commit=lambda s: frozen.append(dict(s)))
            assert result['status'] == expected, result
            assert len(calls) == 1
            path = root / 'data/cache/leagues' / CID / 'snapshot.json'
            if expected == 'empty':
                assert not path.exists()
            else:
                snapshot = json.loads(path.read_text())
                assert snapshot['snapshot_id'] == 'league-attempt-a1'
                assert snapshot['snapshot_at'] == DONE
                assert snapshot['matches'][0]['match_decision']['label'] == 'MATCH_PICK'
                assert frozen == [snapshot]


def test_network_failure_is_one_attempt_and_unknown_charge_is_not_refunded():
    from worldcup.league_daily_runner import fetch_daily_odds
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); request = _reserve(root, ['h2h']); calls = []
        def transport(url):
            calls.append(url); raise TimeoutError('secret timeout')
        result = fetch_daily_odds(request=request, root=root, observed_at=NOW,
            env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, transport=transport)
        assert result['status'] == 'error' and len(calls) == 1
        assert result['actual_cost'] is None and 'secret' not in json.dumps(result)
        assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 1


def test_offline_response_recovery_preserves_clock_cost_and_immutable_raw():
    from worldcup.league_daily_runner import fetch_daily_odds, load_daily_response
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); request = _reserve(root, ['h2h'])
        result = fetch_daily_odds(request=request, root=root, observed_at=NOW,
            env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, clock=lambda: DONE,
            transport=lambda url: Response(json.dumps([_event()]).encode(), {'x-requests-last': '1'}))
        recovered = load_daily_response(root=root, attempt_id='a1')
        assert recovered['raw_events'] == result['raw_events']
        assert recovered['completed_at'] == DONE and recovered['actual_cost'] == 1
        assert recovered['status'] == 'fetched'
        assert load_daily_response(root=root, attempt_id='../escape')['status'] == 'blocked'


def test_raw_response_digest_rejects_changed_valid_json_and_missing_digest():
    import base64
    from worldcup.league_daily_runner import fetch_daily_odds, load_daily_response
    for mutate in ('changed', 'missing'):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); request = _reserve(root, ['h2h']); calls = []
            def transport(url):
                calls.append(url)
                return Response(json.dumps([_event()]).encode(), {'x-requests-last': '1'})
            result = fetch_daily_odds(request=request, root=root, observed_at=NOW,
                env={'THE_ODDS_API_KEY_PRIMARY': 'secret'}, clock=lambda: DONE, transport=transport)
            path = Path(result['response_path']); evidence = json.loads(path.read_text())
            if mutate == 'changed':
                evidence['body_base64'] = base64.b64encode(json.dumps([_event(away_team='Liverpool')]).encode()).decode()
            else:
                evidence.pop('body_sha256', None)
            path.write_text(json.dumps(evidence))
            recovered = load_daily_response(root=root, attempt_id='a1')
            assert recovered['status'] == 'blocked', recovered
            assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 1
            assert len(calls) == 1
