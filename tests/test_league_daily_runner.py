import json
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.league_acceptance import LeagueAcceptanceStore, evaluate_league_acceptance
from worldcup.league_daily_state import DailyStateStore
from worldcup.league_team_identity import accepted_league_team_identity_registry, league_team_identity_registry_fingerprint

NOW = '2026-09-01T10:00:00+00:00'
CID = 'epl_2026_27'
ENDPOINT = 'https://research.fixture-domain.net/api/ingest'


def _setup(root, competition_ids=(CID,)):
    registry = accepted_league_team_identity_registry()
    evidence = {name: {'verified': True, 'fingerprint': 'a' * 64} for name in
                ('sport_catalog', 'odds_sample', 'team_identity', 'result_contract')}
    rows = {}
    for cid in competition_ids:
        evidence['team_identity']['fingerprint'] = league_team_identity_registry_fingerprint(registry, cid)
        rows[cid] = evaluate_league_acceptance(cid, evidence)
    LeagueAcceptanceStore(root / 'data/local/leagues/acceptance.json').write({'schema_version': 1, 'competitions': rows})


def _transport(calls, body=None):
    class Response:
        status = 200
        def __init__(self, body, headers): self.body = body; self.headers = headers
        def read(self): return self.body
    event = {'id': 'e1', 'sport_key': 'soccer_epl', 'commence_time': '2026-09-01T12:00:00+00:00',
             'home_team': 'Arsenal', 'away_team': 'Chelsea', 'bookmakers': [{
                 'key': 'test', 'title': 'Test', 'last_update': NOW, 'markets': [{
                     'key': 'h2h', 'last_update': NOW, 'outcomes': [
                         {'name': 'Arsenal', 'price': 1.6}, {'name': 'Chelsea', 'price': 5.0},
                         {'name': 'Draw', 'price': 4.0}]}]}]}
    def send(url):
        calls.append(url)
        return Response(json.dumps([event] if body is None else body).encode(), {'x-requests-last': '1'})
    return send


def _run(root, calls, publish_fn, **kwargs):
    from worldcup.league_daily_runner import run_league_daily
    return run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
        endpoint=ENDPOINT, daily_credit_limit=10,
        env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
        odds_fetcher=_transport(calls), publish_fn=publish_fn,
        observed_clock=lambda: NOW, **kwargs)


def test_dry_run_never_creates_missing_root_or_calls_dependencies():
    from worldcup.league_daily_runner import run_league_daily
    def forbidden(*args, **kwargs):
        raise AssertionError('side effect in dry-run')
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve() / 'absent'
        result = run_league_daily(root=root, now=NOW, env_loader=forbidden,
            odds_fetcher=forbidden, publish_fn=forbidden)
        assert result['mode'] == 'dry_run' and result['status'] == 'blocked'
        assert not root.exists()


def test_live_missing_budget_and_symlink_state_block_before_dependencies():
    from worldcup.league_daily_runner import run_league_daily
    def forbidden(*args, **kwargs): raise AssertionError('dependency called')
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root)
        result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, env_loader=forbidden)
        assert result['status'] == 'blocked'
        state = root / 'data/local/leagues/daily_refresh_state.json'
        state.symlink_to(root / 'other.json')
        result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10, env_loader=forbidden)
        assert result['status'] == 'blocked'


def test_actual_daily_pipeline_persists_pick_history_and_durable_publication():
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []; published = []
        def publish_fn(*, payload, endpoint, timestamp):
            published.append(payload)
            assert endpoint == ENDPOINT and timestamp == NOW
            return {'status': 'stored'}
        result = _run(root, calls, publish_fn)
        assert result['status'] == 'published', result
        snapshot = json.loads((root / 'data/cache/leagues' / CID / 'snapshot.json').read_text())
        assert snapshot['matches'][0]['match_decision']['label'] == 'MATCH_PICK'
        assert (root / 'data/local/leagues' / CID / 'history' / (snapshot['snapshot_id'] + '.json')).exists()
        assert len(calls) == len(published) == 1
        state = DailyStateStore(root).read()
        assert {row['phase'] for row in state['attempts'].values()} == {'published'}
        assert state['budgets']['2026-09-01']['reserved_credits'] == 1


def test_failed_publication_restarts_frozen_pending_without_fetch():
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        first = _run(root, calls, lambda **kwargs: {'status': 'failed'})
        assert first['status'] == 'pending', first
        second = _run(root, calls, lambda **kwargs: {'status': 'duplicate'})
        assert second['status'] == 'published', second
        assert len(calls) == 1
        assert {row['phase'] for row in DailyStateStore(root).read()['attempts'].values()} == {'published'}


def test_post_lineup_missing_shared_budget_blocks_new_odds():
    from worldcup.league_post_lineup_refresh import run_post_lineup_refresh
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root)
        receipt = {'event_id': 'e1', 'source_match_id': 's1',
            'kickoff_at_utc': '2026-09-01T12:00:00+00:00', 'fetched_at': NOW,
            'lineup_fingerprint': 'a'*64, 'ack_key': {'competition_id': CID,
                'event_id': 'e1', 'lineup_fingerprint': 'a'*64}}
        result = run_post_lineup_refresh(root=root, now=NOW, live=True,
            newly_confirmed={CID: [receipt]}, endpoint=ENDPOINT, observed_clock=lambda: NOW)
        assert result['status'] == 'blocked'
        assert result['acks']['blocked'][0]['reason'] == 'daily_budget_unconfigured'


def test_supersede_pending_exact_hash_cas_never_erases_changed_outbox():
    from worldcup.league_publication import supersede_pending
    from worldcup.league_daily_runner import read_daily_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root)
        _run(root, [], lambda **kwargs: {'status': 'failed'})
        before = read_daily_publication(root)
        result = supersede_pending(root=root, reason='post_lineup_pending_expired', now=NOW,
            expected_body_sha256='0'*64)
        assert result['status'] == 'rejected'
        assert read_daily_publication(root) == before
        result = supersede_pending(root=root, reason='post_lineup_pending_expired', now=NOW,
            expected_body_sha256=before['pending']['body_sha256'])
        assert result['status'] == 'superseded'
        after = read_daily_publication(root)
        assert after['pending'] is None and after['sent'] is None
        assert after['superseded'][-1]['pending'] == before['pending']


def test_empty_discovery_only_real_empty_response_gets_daily_cooldown():
    from worldcup.league_daily_runner import run_league_daily
    for body, expected in [([], 'discovery_complete'), ([{'id': 'bad'}], 'stale')]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); _setup(root); calls = []
            result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
                endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
                odds_fetcher=_transport(calls, body), publish_fn=lambda **kwargs: {'status': 'stored'}, observed_clock=lambda: NOW)
            assert result['competitions'][CID]['status'] == expected, result
            state = DailyStateStore(root).read()
            if body:
                assert {r['phase'] for r in state['attempts'].values()} == {'blocked'}
            else:
                assert state['competitions'][CID]['next_discovery_at'] == '2026-09-02T10:00:00+00:00'
            assert not (root / 'data/cache/leagues' / CID / 'snapshot.json').exists()


def test_response_crossing_kickoff_never_creates_observed_history():
    from worldcup.league_daily_runner import run_league_daily
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []; clock = [NOW]
        transport = _transport(calls)
        def fetch(url):
            response = transport(url); clock[0] = '2026-09-01T12:00:01+00:00'; return response
        result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
            odds_fetcher=fetch, publish_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError('send')),
            observed_clock=lambda: clock[0])
        assert result['status'] == 'blocked'
        assert len(calls) == 1
        assert not (root / 'data/local/leagues' / CID / 'history').exists()
        assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 1


def test_history_write_crash_recovers_exact_fetched_without_provider():
    import worldcup.league_live_store as live_store
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        original = live_store._atomic_write
        def fail_current(path, payload):
            if path.name == 'snapshot.json': raise OSError('injected current crash')
            original(path, payload)
        live_store._atomic_write = fail_current
        try:
            first = _run(root, calls, lambda **kwargs: {'status': 'stored'})
        finally:
            live_store._atomic_write = original
        assert first['status'] == 'blocked'
        assert {r['phase'] for r in DailyStateStore(root).read()['attempts'].values()} == {'fetched'}
        second = _run(root, calls, lambda **kwargs: {'status': 'stored'})
        assert second['status'] == 'published', second
        assert len(calls) == 1


def test_raw_capture_recovery_uses_real_build_time_without_refetch_or_reaging_odds():
    import worldcup.league_daily_runner as runner
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        original = runner.commit_fetched_daily_odds
        runner.commit_fetched_daily_odds = lambda **kwargs: (_ for _ in ()).throw(OSError('crash'))
        try:
            first = _run(root, calls, lambda **kwargs: {'status': 'stored'})
        finally:
            runner.commit_fetched_daily_odds = original
        assert first['status'] == 'blocked'
        second = runner.run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
            odds_fetcher=lambda url: (_ for _ in ()).throw(AssertionError('refetch')),
            publish_fn=lambda **kwargs: {'status': 'stored'}, observed_clock=lambda: '2026-09-01T10:10:00+00:00')
        assert second['status'] == 'published', second
        snapshot = json.loads((root / 'data/cache/leagues' / CID / 'snapshot.json').read_text())
        assert snapshot['snapshot_at'] == '2026-09-01T10:10:00+00:00' and len(calls) == 1
        assert snapshot['data_quality']['odds_response_observed_at'] == NOW
        assert snapshot['matches'][0]['match_decision']['computed_at'] == snapshot['snapshot_at']
        assert snapshot['matches'][0]['match_decision']['odds_latest_at'] == NOW


def test_raw_only_recovery_after_kickoff_cannot_fabricate_backdated_history():
    import worldcup.league_daily_runner as runner
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        original = runner.commit_fetched_daily_odds
        runner.commit_fetched_daily_odds = lambda **kwargs: (_ for _ in ()).throw(OSError('crash before build'))
        try:
            assert _run(root, calls, lambda **kwargs: {'status': 'stored'})['status'] == 'blocked'
        finally:
            runner.commit_fetched_daily_odds = original
        assert {row['phase'] for row in DailyStateStore(root).read()['attempts'].values()} == {'reserved'}
        result = runner.run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
            odds_fetcher=lambda url: (_ for _ in ()).throw(AssertionError('refetch')),
            publish_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError('backdated publish')),
            observed_clock=lambda: '2026-09-01T12:01:00+00:00')
        assert result['status'] == 'blocked', result
        assert not (root / 'data/local/leagues' / CID / 'history').exists()
        assert not (root / 'data/cache/leagues' / CID / 'snapshot.json').exists()
        assert len(calls) == 1


def test_daily_and_post_lineup_share_lock_and_budget_before_provider():
    from worldcup.league_daily_state import odds_execution_lock
    from worldcup.league_post_lineup_refresh import run_post_lineup_refresh
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        receipt = {'event_id': 'e1', 'source_match_id': 's1', 'kickoff_at_utc': '2026-09-01T12:00:00+00:00',
            'fetched_at': NOW, 'lineup_fingerprint': 'a'*64, 'ack_key': {'competition_id': CID,
                'event_id': 'e1', 'lineup_fingerprint': 'a'*64}}
        with odds_execution_lock(root):
            assert _run(root, calls, lambda **kwargs: {'status': 'stored'})['reason'] == 'odds_execution_busy'
            post = run_post_lineup_refresh(root=root, now=NOW, live=True, endpoint=ENDPOINT,
                daily_credit_limit=10, newly_confirmed={CID: [receipt]}, observed_clock=lambda: NOW)
            assert post['acks']['blocked'][0]['reason'] == 'odds_execution_busy'
        assert not calls
        DailyStateStore(root).reserve(date_bj='2026-09-01', attempt_id='post-budget', estimated=10, limit=10)
        result = _run(root, calls, lambda **kwargs: {'status': 'stored'})
        assert not calls and not result['plan']['requests']


def test_cli_dry_run_and_live_now_never_read_env_or_create_paths():
    import io
    from contextlib import redirect_stdout
    from worldcup.league_daily_runner import main
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve() / 'absent'
        for flags in [[], ['--live', '--write', '--publish', '--endpoint', ENDPOINT, '--daily-credit-limit', '10']]:
            with redirect_stdout(io.StringIO()) as out:
                code = main(['--root', str(root), '--now', NOW, '--env', '/nonexistent/secret', *flags])
            assert code == 2 and json.loads(out.getvalue())['status'] == 'blocked'
            assert not root.exists()


def test_bad_current_partition_keeps_exact_published_lkg_while_healthy_advances(corrupt='{bad'):
    from worldcup.league_daily_runner import run_league_daily, read_daily_publication
    second = 'laliga_2026_27'
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root, (CID, second)); calls = []; sent = []; clock = [NOW]
        source = _transport(calls)
        def transport(url):
            response = source(url)
            if 'soccer_spain_la_liga' in url:
                response.body = response.body.replace(b'soccer_epl', b'soccer_spain_la_liga').replace(b'e1', b's1').replace(b'Arsenal', b'Barcelona').replace(b'Chelsea', b'Real Madrid')
            return response
        def run():
            return run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
                endpoint=ENDPOINT, daily_credit_limit=20, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
                odds_fetcher=transport, publish_fn=lambda **kwargs: sent.append(kwargs['payload']) or {'status': 'stored'},
                observed_clock=lambda: clock[0])
        assert run()['status'] == 'published'
        first_vector = read_daily_publication(root)['components']
        (root / 'data/cache/leagues' / second / 'snapshot.json').write_text(corrupt)
        clock[0] = '2026-09-01T10:40:00+00:00'
        result = run()
        assert result['status'] == 'partial', result
        last = read_daily_publication(root)['components']
        assert last['odds:' + second] == first_vector['odds:' + second]
        assert last['odds:' + CID]['snapshot_at'] == clock[0]
        assert len(calls) == 3  # invalid current is isolated before paid fetch
        assert sent[-1]['snapshot']['data_quality']['stale_competitions'] == [second]


def test_json_null_list_and_bad_competition_partition_use_lkg():
    for value in [None, [], {'competition': []}]:
        test_bad_current_partition_keeps_exact_published_lkg_while_healthy_advances(json.dumps(value))


def test_post_lineup_shared_budget_exhaustion_blocks_before_claim_or_fetch():
    from worldcup.league_post_lineup_refresh import run_post_lineup_refresh, PostLineupRefreshStateStore
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        assert _run(root, calls, lambda **kwargs: {'status': 'stored'})['status'] == 'published'
        DailyStateStore(root).reserve(date_bj='2026-09-01', attempt_id='other-shared', estimated=9, limit=10)
        receipt = {'event_id': 'e1', 'source_match_id': 's1', 'kickoff_at_utc': '2026-09-01T12:00:00+00:00',
            'fetched_at': NOW, 'lineup_fingerprint': 'a'*64, 'ack_key': {'competition_id': CID,
                'event_id': 'e1', 'lineup_fingerprint': 'a'*64}}
        result = run_post_lineup_refresh(root=root, now=NOW, live=True, endpoint=ENDPOINT,
            daily_credit_limit=10, newly_confirmed={CID: [receipt]}, observed_clock=lambda: NOW,
            identity_registry=accepted_league_team_identity_registry(), env={'THE_ODDS_API_KEY_PRIMARY': 'fake'},
            quota_ledger={'providers': {'theoddsapi_primary': {'remaining': 100, 'observed_at': NOW}}},
            odds_fetcher=lambda *args: (_ for _ in ()).throw(AssertionError('fetch')))
        assert result['acks']['blocked'][0]['reason'] == 'daily_budget_exhausted', result
        assert PostLineupRefreshStateStore(root).read()['receipts'] == {}


def test_actual_two_process_daily_execution_excludes_post_lineup_writer():
    import multiprocessing
    from worldcup.league_daily_runner import run_league_daily
    from worldcup.league_post_lineup_refresh import run_post_lineup_refresh
    context = multiprocessing.get_context('fork')
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root)
        entered = context.Event(); release = context.Event(); result_queue = context.Queue()
        def worker():
            transport = _transport([])
            def fetch(url):
                entered.set()
                assert release.wait(10)
                return transport(url)
            result_queue.put(run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
                endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
                odds_fetcher=fetch, publish_fn=lambda **kwargs: {'status': 'stored'}, observed_clock=lambda: NOW))
        child = context.Process(target=worker)
        child.start()
        try:
            assert entered.wait(10)
            receipt = {'event_id': 'e1', 'source_match_id': 's1', 'kickoff_at_utc': '2026-09-01T12:00:00+00:00',
                'fetched_at': NOW, 'lineup_fingerprint': 'a'*64, 'ack_key': {'competition_id': CID,
                    'event_id': 'e1', 'lineup_fingerprint': 'a'*64}}
            result = run_post_lineup_refresh(root=root, now=NOW, live=True, endpoint=ENDPOINT,
                daily_credit_limit=10, newly_confirmed={CID: [receipt]}, observed_clock=lambda: NOW,
                odds_fetcher=lambda *args: (_ for _ in ()).throw(AssertionError('concurrent fetch')))
            assert result['acks']['blocked'][0]['reason'] == 'odds_execution_busy'
        finally:
            release.set(); child.join(10)
            if child.is_alive(): child.terminate(); child.join()
        assert child.exitcode == 0
        assert result_queue.get(timeout=2)['status'] == 'published'
        assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 1


def test_production_cli_uses_real_pipeline_frozen_signer_and_explicit_quota_path():
    import io
    from contextlib import redirect_stdout
    import worldcup.league_daily_runner as runner
    import worldcup.league_pre_match_runner as pre
    import worldcup.observed_clock as clocks
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []; sends = []
        original_env, original_send, original_open, original_clock = pre._load_env, pre._default_sender, runner.urlopen, clocks.system_utc_now
        pre._load_env = lambda path: {'THE_ODDS_API_KEY_PRIMARY': 'fake', 'INGEST_HMAC_SECRET': 'a93f80b7d5e628104b97ce236a509fd1e54c7290b3a618d5f20479bc61a083ed'}
        pre._default_sender = lambda request: sends.append(request) or {'http_status': 200, 'body': '{"status":"stored"}'}
        transport = _transport(calls)
        runner.urlopen = lambda url, timeout: transport(url)
        clocks.system_utc_now = lambda: NOW
        try:
            with redirect_stdout(io.StringIO()) as output:
                code = runner.main(['--root', str(root), '--live', '--write', '--publish', '--endpoint', ENDPOINT,
                    '--daily-credit-limit', '10', '--quota-path', 'data/cache/custom-quota.json'])
        finally:
            pre._load_env, pre._default_sender, runner.urlopen, clocks.system_utc_now = original_env, original_send, original_open, original_clock
        assert code == 0 and json.loads(output.getvalue())['status'] == 'published'
        assert len(calls) == len(sends) == 1
        assert sends[0]['url'] == ENDPOINT and sends[0]['headers']['X-Worldcup-Timestamp'] == NOW
        assert (root / 'data/cache/custom-quota.json').exists()
        assert not (root / 'data/cache/quota.json').exists()


def test_superseded_pending_attempt_is_terminal_not_permanent_recovery_barrier():
    from worldcup.league_daily_runner import read_daily_publication
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        assert _run(root, calls, lambda **kwargs: {'status': 'failed'})['status'] == 'pending'
        result = _run(root, calls, lambda **kwargs: {'status': 'rejected', 'reason': 'league_component_regression'})
        assert result['status'] == 'rejected'
        state = DailyStateStore(root).read()
        assert {row['phase'] for row in state['attempts'].values()} == {'blocked'}
        assert read_daily_publication(root)['superseded']
        assert len(calls) == 1


def test_unknown_transport_failure_keeps_reserved_attempt_and_never_refetches():
    from worldcup.league_daily_runner import run_league_daily
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        def fail(url): calls.append(url); raise TimeoutError('test transport uncertainty')
        kwargs = dict(root=root, now=NOW, live=True, write=True, publish=True, endpoint=ENDPOINT,
            daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'}, odds_fetcher=fail,
            publish_fn=lambda **kwargs: {'status': 'stored'}, observed_clock=lambda: NOW)
        assert run_league_daily(**kwargs)['status'] == 'blocked'
        state = DailyStateStore(root).read()
        assert {row['phase'] for row in state['attempts'].values()} == {'reserved'}
        assert state['budgets']['2026-09-01']['reserved_credits'] == 1
        assert run_league_daily(**kwargs)['status'] == 'blocked'
        assert len(calls) == 1


def test_malformed_quota_container_fails_closed_before_transport():
    for value in [[], {'providers': []}, {'providers': {'theoddsapi_primary': []}},
                  {'providers': {'theoddsapi_primary': {'remaining': '100'}}}]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); _setup(root); calls = []
            path = root / 'data/cache/quota.json'; path.parent.mkdir(parents=True)
            path.write_text(json.dumps(value))
            result = _run(root, calls, lambda **kwargs: {'status': 'stored'})
            assert result['status'] == 'blocked' and result['reason'] == 'daily_quota_invalid', result
            assert not calls


def test_production_cli_missing_signing_secret_blocks_before_paid_fetch():
    import io
    from contextlib import redirect_stdout
    import worldcup.league_daily_runner as runner
    import worldcup.league_pre_match_runner as pre
    import worldcup.observed_clock as clocks
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root); calls = []
        old_env, old_open, old_clock = pre._load_env, runner.urlopen, clocks.system_utc_now
        pre._load_env = lambda path: {'THE_ODDS_API_KEY_PRIMARY': 'fake'}
        runner.urlopen = lambda url, timeout: calls.append(url) or _transport([])(url)
        clocks.system_utc_now = lambda: NOW
        try:
            with redirect_stdout(io.StringIO()) as out:
                code = runner.main(['--root', str(root), '--live', '--write', '--publish', '--endpoint', ENDPOINT, '--daily-credit-limit', '10'])
        finally:
            pre._load_env, runner.urlopen, clocks.system_utc_now = old_env, old_open, old_clock
        assert code == 2 and json.loads(out.getvalue())['status'] == 'blocked'
        assert calls == []


def test_reserved_endpoints_block_before_inputs_env_locks_and_transport():
    from worldcup.league_daily_runner import run_league_daily
    def forbidden(*args, **kwargs): raise AssertionError('side effect')
    for host in ['example.invalid', 'research.test', 'research.example.org',
                 'example.com.', 'EXAMPLE.NET.', 'localhost.', 'a.example', '127.0.0.1']:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'absent'
            result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
                endpoint='https://' + host + '/ingest', daily_credit_limit=10,
                env_loader=forbidden, odds_fetcher=forbidden, publish_fn=forbidden)
            assert result.get('reason') == 'daily_endpoint_invalid', (host, result)
            assert not root.exists()


def test_initial_partial_failure_without_snapshot_blocks_cleanly():
    from worldcup.league_daily_runner import run_league_daily
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root, (CID, 'laliga_2026_27'))
        transport = _transport([])
        def fetch(url):
            if 'soccer_spain_la_liga' in url: raise TimeoutError('unknown charge')
            return transport(url)
        result = run_league_daily(root=root, now=NOW, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
            odds_fetcher=fetch, publish_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError('incomplete vector')),
            observed_clock=lambda: NOW)
        assert result['status'] == 'blocked' and result['reason'] == 'daily_lkg_missing', result


def test_unknown_charge_allows_new_attempt_after_cooldown_without_refund():
    from worldcup.league_daily_runner import run_league_daily
    for retry_at, limit, expected_calls in [('2026-09-01T10:29:00+00:00', 2, 1),
            ('2026-09-01T10:31:00+00:00', 2, 2), ('2026-09-01T10:31:00+00:00', 1, 1),
            ('2026-09-02T10:31:00+00:00', 1, 2)]:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); _setup(root); calls = []; clock = [NOW]
            def transport(url):
                calls.append(url)
                if len(calls) == 1: raise TimeoutError('unknown charge')
                return _transport([], [])(url)
            def run():
                return run_league_daily(root=root, now=clock[0], live=True, write=True, publish=True,
                    endpoint=ENDPOINT, daily_credit_limit=limit, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'},
                    odds_fetcher=transport, publish_fn=lambda **kwargs: {'status': 'stored'}, observed_clock=lambda: clock[0])
            assert run()['status'] == 'blocked'
            original = DailyStateStore(root).read()
            old_id = next(iter(original['attempts']))
            clock[0] = retry_at
            result = run()
            assert len(calls) == expected_calls, (retry_at, limit, result)
            state = DailyStateStore(root).read()
            assert state['budgets']['2026-09-01']['reservations'][old_id] == original['budgets']['2026-09-01']['reservations'][old_id]
            assert len(state['attempts']) == expected_calls
            if expected_calls == 2:
                assert state['attempts'][old_id]['evidence']['blocked']['error_code'] == 'daily_response_unknown'
                assert result['competitions'][CID]['status'] == 'discovery_complete'
