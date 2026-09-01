from __future__ import annotations

import copy
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory


def _module():
    from worldcup import league_daily_state
    return league_daily_state


def _reject(fn, message=None):
    try:
        fn()
    except ValueError as exc:
        if message:
            assert str(exc) == message, str(exc)
    else:
        raise AssertionError('invalid state accepted')


def _context():
    return {'competition_id': 'epl_2026_27', 'acceptance_fingerprint': 'a' * 64,
            'registry_fingerprint': 'b' * 64, 'markets': ['h2h'],
            'expected_snapshot_id': 'snap-1', 'request_at': '2026-09-01T10:00:00+00:00',
            'events': [{'event_id': 'e1', 'kickoff_at_utc': '2026-09-01T12:00:00+00:00',
                        'home_canonical': 'A', 'away_canonical': 'B'}],
            'anchor_metadata': {'e1': ['epl_2026_27|e1|2026-09-01T12:00:00+00:00|T-6h']}}


def _attempt_state():
    m = _module()
    return m.reserve_attempt(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a',
                             estimated=1, limit=3, context=_context())


def _snapshot():
    return {'competition': {'id': 'epl_2026_27'}, 'snapshot_id': 'snap-1',
            'snapshot_at': '2026-09-01T10:00:00+00:00',
            'matches': [{'source_event_id': 'e1', 'kickoff_at_utc': '2026-09-01T12:00:00+00:00',
                         'home_canonical': 'A', 'away_canonical': 'B'}]}


def test_reservation_survives_unknown_response_and_is_idempotent():
    m = _module()
    first = m.reserve_credits(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=3, limit=3)
    assert m.reserve_credits(first, date_bj='2026-09-01', attempt_id='a', estimated=3, limit=3) == first
    _reject(lambda: m.reserve_credits(first, date_bj='2026-09-01', attempt_id='b', estimated=1, limit=3), 'daily_budget_exhausted')


def test_settlement_uses_request_cost_unknown_stays_reserved_and_release_is_explicit():
    m = _module()
    state = m.reserve_credits(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=3, limit=3)
    unknown = m.settle_credits(state, attempt_id='a', actual_cost=None)
    assert unknown['budgets']['2026-09-01']['reserved_credits'] == 3
    _reject(lambda: m.release_credits(unknown, attempt_id='a', request_sent=False))
    settled = m.settle_credits(unknown, attempt_id='a', actual_cost=1)
    assert settled['budgets']['2026-09-01']['reserved_credits'] == 1
    assert m.settle_credits(settled, attempt_id='a', actual_cost=1) == settled
    _reject(lambda: m.settle_credits(settled, attempt_id='a', actual_cost=0))
    released = m.release_credits(state, attempt_id='a', request_sent=False)
    assert released['budgets']['2026-09-01']['reserved_credits'] == 0
    _reject(lambda: m.release_credits(state, attempt_id='a', request_sent=True))
    assert state['budgets']['2026-09-01']['reserved_credits'] == 3


def test_validation_rejects_non_json_bad_time_and_immutable_context():
    m = _module()
    state = _attempt_state()
    for key, value in [('competition_id', 'la_liga'), ('markets', ['totals']), ('request_at', '2026-09-01T10:00:00')]:
        context = _context(); context[key] = value
        _reject(lambda: m.reserve_attempt(state, date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=context))
    for mutate in [lambda s: s.update({'bad': {1, 2}}),
                   lambda s: s['budgets']['2026-09-01'].update({'reserved_credits': -1}),
                   lambda s: s['competitions']['epl_2026_27'].update({'last_attempt_signatures': {'unused': 'bad'}}),
                   lambda s: s['attempts']['a']['context'].update({'request_at': '2026-09-01T10:00:00'}),
                   lambda s: s.update({'bad': float('nan')})]:
        bad = copy.deepcopy(state); mutate(bad)
        _reject(lambda: m.validate_daily_state(bad))


def test_phase_graph_requires_evidence_and_recovery_does_not_trust_history():
    m = _module(); state = _attempt_state(); snapshot = _snapshot()
    _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='published', evidence={}))
    fetched = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot})
    assert m.recovery_action(fetched['attempts']['a'], history=snapshot) == 'retry_commit'
    assert m.recovery_action(state['attempts']['a']) == 'unknown_request'
    bad = copy.deepcopy(snapshot); bad['snapshot_id'] = 'wrong'
    _reject(lambda: m.recovery_action(fetched['attempts']['a'], history=bad, current=bad))
    bad = copy.deepcopy(snapshot); bad['snapshot_at'] = '2026-09-01T09:00:00+00:00'
    _reject(lambda: m.recovery_action(fetched['attempts']['a'], history=bad))
    bad = copy.deepcopy(snapshot); bad['matches'] = []
    _reject(lambda: m.recovery_action(fetched['attempts']['a'], history=bad))
    committed = m.advance_attempt(fetched, attempt_id='a', phase='committed', evidence={'history': snapshot, 'current': snapshot})
    pending_evidence = {'endpoint': 'https://example.org/ingest', 'body_sha256': 'c' * 64, 'snapshot': snapshot}
    pending = m.advance_attempt(committed, attempt_id='a', phase='pending', evidence=pending_evidence)
    receipt = {**pending_evidence, 'status': 'stored'}
    assert m.recovery_action(pending['attempts']['a'], published_receipt=receipt) == 'published'
    published = m.advance_attempt(pending, attempt_id='a', phase='published', evidence=receipt)
    assert published['competitions']['epl_2026_27']['successful_anchors'] == _context()['anchor_metadata']['e1']
    _reject(lambda: m.advance_attempt(published, attempt_id='a', phase='pending', evidence=pending_evidence))


def test_store_read_is_side_effect_free_and_corruption_symlink_fail_closed():
    m = _module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve() / 'absent'; store = m.DailyStateStore(root)
        assert store.read() == m.empty_daily_state(); assert not root.exists()
        store.commit(_attempt_state()); assert store.read()['attempts']['a']['phase'] == 'reserved'
        path = root / 'data/local/leagues/daily_refresh_state.json'
        path.write_text('{broken', encoding='utf-8')
        _reject(store.read)
        path.unlink(); other = Path(tmp) / 'other'; other.write_text('{}', encoding='utf-8'); path.symlink_to(other)
        _reject(store.read); _reject(lambda: store.commit(m.empty_daily_state()))


def test_stale_commits_merge_and_recheck_budget_under_lock():
    m = _module()
    with TemporaryDirectory() as tmp:
        store = m.DailyStateStore(Path(tmp).resolve()); initial = store.read()
        one = m.reserve_credits(initial, date_bj='2026-09-01', attempt_id='a', estimated=2, limit=3)
        two = m.reserve_credits(initial, date_bj='2026-09-01', attempt_id='b', estimated=2, limit=3)
        store.commit(one)
        _reject(lambda: store.commit(two), 'daily_budget_exhausted')
        three = m.reserve_credits(initial, date_bj='2026-09-01', attempt_id='c', estimated=1, limit=3)
        store.commit(three)
        assert set(store.read()['attempts']) == {'a', 'c'}
        assert store.read()['budgets']['2026-09-01']['reserved_credits'] == 3


def _hold_lock(root, ready, release):
    from worldcup.league_daily_state import odds_execution_lock
    with odds_execution_lock(Path(root)):
        ready.set()
        if not release.wait(10):
            raise RuntimeError('test lock timeout')


def test_cross_process_execution_lock_is_nonblocking():
    m = _module(); ctx = multiprocessing.get_context('fork')
    with TemporaryDirectory() as tmp:
        ready, release = ctx.Event(), ctx.Event()
        root = Path(tmp).resolve()
        process = ctx.Process(target=_hold_lock, args=(str(root), ready, release)); process.start()
        try:
            assert ready.wait(10)
            def acquire():
                with m.odds_execution_lock(root):
                    raise AssertionError('lock acquired twice')
            _reject(acquire, 'odds_execution_busy')
        finally:
            release.set(); process.join(10)
            if process.is_alive(): process.terminate(); process.join()
        assert process.exitcode == 0
        with m.odds_execution_lock(root):
            pass


def test_recovery_rejects_fabricated_phase_and_released_attempt_cannot_fetch():
    m = _module(); state = _attempt_state()
    fake = copy.deepcopy(state['attempts']['a']); fake['phase'] = 'published'
    _reject(lambda: m.recovery_action(fake))
    released = m.release_credits(state, attempt_id='a', request_sent=False)
    _reject(lambda: m.advance_attempt(released, attempt_id='a', phase='fetched', evidence={'snapshot': _snapshot()}))


def test_bad_container_types_return_safe_value_errors():
    m = _module()
    for change in [lambda s: s['attempts']['a'].update(phase=[]),
                   lambda s: s['attempts']['a']['context'].update(markets=[{}]),
                   lambda s: s['budgets']['2026-09-01']['reservations']['a'].update(status=[]),
                   lambda s: s['attempts']['a']['context'].update(anchor_metadata={'e1': [{}]})]:
        state = _attempt_state(); change(state)
        _reject(lambda: m.validate_daily_state(state))


def test_persisted_restart_replays_without_refetch_and_receipt_survives_pending_cleanup():
    m = _module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); store = m.DailyStateStore(root)
        store.reserve(date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=_context())
        assert m.recovery_action(m.DailyStateStore(root).read()['attempts']['a']) == 'unknown_request'
        store.settle(attempt_id='a', actual_cost=None)
        assert m.DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 1
        store.advance(attempt_id='a', phase='fetched', evidence={'snapshot': _snapshot()})
        assert m.recovery_action(m.DailyStateStore(root).read()['attempts']['a'], history=_snapshot()) == 'retry_commit'
        store.advance(attempt_id='a', phase='committed', evidence={'history': _snapshot(), 'current': _snapshot()})
        pending = {'endpoint': 'https://example.org/ingest', 'body_sha256': 'c' * 64, 'snapshot': _snapshot()}
        store.advance(attempt_id='a', phase='pending', evidence=pending)
        receipt = {**pending, 'status': 'duplicate'}
        store.advance(attempt_id='a', phase='published', evidence=receipt)
        after_crash = m.DailyStateStore(root).read()
        assert m.recovery_action(after_crash['attempts']['a'], published_receipt=receipt) == 'published'
        assert store.advance(attempt_id='a', phase='published', evidence=receipt) == after_crash
        _reject(lambda: store.commit(_attempt_state()))
        assert store.read() == after_crash


def test_pending_receipt_requires_exact_endpoint_body_and_safe_status():
    m = _module(); state = _attempt_state(); snapshot = _snapshot()
    state = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot})
    state = m.advance_attempt(state, attempt_id='a', phase='committed', evidence={'history': snapshot, 'current': snapshot})
    pending = {'endpoint': 'https://example.org/ingest', 'body_sha256': 'c' * 64, 'snapshot': snapshot}
    state = m.advance_attempt(state, attempt_id='a', phase='pending', evidence=pending)
    for key, wrong in [('endpoint', 'https://other.example/ingest'), ('body_sha256', 'd' * 64), ('status', 'rejected'), ('status', 'stale')]:
        receipt = {**pending, 'status': 'stored', key: wrong}
        _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='published', evidence=receipt))
        assert state['competitions']['epl_2026_27']['successful_anchors'] == []


def _stale_writer(root, attempt_id, ready, go, result):
    from worldcup.league_daily_state import DailyStateStore, reserve_credits
    store = DailyStateStore(Path(root))
    candidate = reserve_credits(store.read(), date_bj='2026-09-01', attempt_id=attempt_id, estimated=2, limit=3)
    ready.set()
    if not go.wait(10): raise RuntimeError('test writer timeout')
    try:
        store.commit(candidate)
        result.put('stored')
    except ValueError as exc:
        result.put(str(exc))


def test_two_process_stale_reservations_cannot_overspend():
    m = _module(); ctx = multiprocessing.get_context('fork')
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); go = ctx.Event(); ready = [ctx.Event(), ctx.Event()]; result = ctx.Queue()
        processes = [ctx.Process(target=_stale_writer, args=(str(root), aid, signal, go, result)) for aid, signal in zip(('a', 'b'), ready)]
        for process in processes: process.start()
        try:
            assert all(signal.wait(10) for signal in ready); go.set()
            outcomes = [result.get(timeout=10), result.get(timeout=10)]
            assert sorted(outcomes) == ['daily_budget_exhausted', 'stored']
        finally:
            go.set()
            for process in processes:
                process.join(10)
                if process.is_alive(): process.terminate(); process.join()
            result.close(); result.join_thread()
        assert all(process.exitcode == 0 for process in processes)
        assert m.DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 2


def test_snapshot_binding_checks_team_identity_and_event_membership():
    m = _module(); state = _attempt_state()
    for mutate in [lambda s: s['matches'][0].update(home_canonical='other'),
                   lambda s: s['matches'][0].update(kickoff_at_utc='2026-09-02T12:00:00+00:00'),
                   lambda s: s.update(snapshot_at='2026-09-01T12:00:00+00:00')]:
        snapshot = _snapshot(); mutate(snapshot)
        _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot}))


def test_commit_atomic_replace_failure_preserves_previous_file():
    from unittest.mock import patch
    m = _module()
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); store = m.DailyStateStore(root); store.commit(_attempt_state())
        before = store.path.read_bytes()
        candidate = m.settle_credits(store.read(), attempt_id='a', actual_cost=1)
        with patch('worldcup.league_daily_state.os.replace', side_effect=OSError('injected interruption')):
            try: store.commit(candidate)
            except OSError: pass
            else: raise AssertionError('injected write interruption missing')
        assert store.path.read_bytes() == before
        assert list(store.path.parent.glob('.daily-refresh-*.tmp')) == []


def test_partial_response_freezes_returned_membership_not_planned_events():
    m = _module(); state = _attempt_state(); snapshot = _snapshot()
    snapshot['matches'][0]['source_event_id'] = 'new-event'
    fetched = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot})
    assert fetched['attempts']['a']['context']['events'][0]['event_id'] == 'e1'
    assert fetched['attempts']['a']['evidence']['fetched']['snapshot']['matches'][0]['source_event_id'] == 'new-event'
    assert m.recovery_action(fetched['attempts']['a'], history=snapshot) == 'retry_commit'
    _reject(lambda: m.recovery_action(fetched['attempts']['a'], history=_snapshot()))
    committed = m.advance_attempt(fetched, attempt_id='a', phase='committed', evidence={'history': snapshot, 'current': snapshot})
    pending = {'endpoint': 'https://example.org/ingest', 'body_sha256': 'c' * 64, 'snapshot': snapshot}
    pending_state = m.advance_attempt(committed, attempt_id='a', phase='pending', evidence=pending)
    published = m.advance_attempt(pending_state, attempt_id='a', phase='published', evidence={**pending, 'status': 'stored'})
    assert published['competitions']['epl_2026_27']['successful_anchors'] == []


def test_discovery_response_requires_future_canonical_identity():
    m = _module(); context = _context(); context['events'] = []; context['anchor_metadata'] = {}
    state = m.reserve_attempt(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=context)
    for key, value in [('home_canonical', ''), ('away_canonical', 'A'), ('kickoff_at_utc', '2026-09-01T09:00:00+00:00')]:
        snapshot = _snapshot(); snapshot['matches'][0][key] = value
        _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot}))


def test_empty_discovery_success_has_cooldown_without_fake_snapshot_or_publication():
    m = _module(); context = _context(); context.update(events=[], anchor_metadata={})
    state = m.reserve_attempt(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=context)
    assert state['competitions']['epl_2026_27']['last_attempt_at'] == '2026-09-01T10:00:00+00:00'
    assert 'last_success_at' not in state['competitions']['epl_2026_27']
    evidence = {'empty_discovery': {'competition_id': 'epl_2026_27', 'observed_at': '2026-09-01T10:00:02+00:00', 'event_ids': []}}
    state = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence=evidence)
    row = state['competitions']['epl_2026_27']
    assert row['next_discovery_at'] == '2026-09-02T10:00:02+00:00'
    assert row['last_success_at'] == '2026-09-01T10:00:02+00:00'
    assert row['successful_anchors'] == []
    assert m.recovery_action(state['attempts']['a']) == 'discovery_complete'
    _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='committed', evidence={'history': _snapshot(), 'current': _snapshot()}))
    _reject(lambda: m.advance_attempt(_attempt_state(), attempt_id='a', phase='fetched', evidence=evidence))


def test_failed_discovery_after_prior_success_restores_thirty_minute_backoff():
    m = _module(); context = _context(); context.update(events=[], anchor_metadata={})
    state = m.reserve_attempt(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=context)
    state = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'empty_discovery': {'competition_id': 'epl_2026_27', 'observed_at': '2026-09-01T10:00:00+00:00', 'event_ids': []}})
    context.update(request_at='2026-09-02T10:01:00+00:00', expected_snapshot_id='snap-2')
    retried = m.reserve_attempt(state, date_bj='2026-09-02', attempt_id='b', estimated=1, limit=3, context=context)
    assert retried['competitions']['epl_2026_27']['next_discovery_at'] == '2026-09-02T10:31:00+00:00'


def test_blocked_phase_does_not_recover_to_publication():
    m = _module(); state = _attempt_state(); snapshot = _snapshot()
    state = m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot})
    state = m.advance_attempt(state, attempt_id='a', phase='committed', evidence={'history': snapshot, 'current': snapshot})
    pending = {'endpoint': 'https://example.org/ingest', 'body_sha256': 'c' * 64, 'snapshot': snapshot}
    state = m.advance_attempt(state, attempt_id='a', phase='pending', evidence=pending)
    blocked = m.advance_attempt(state, attempt_id='a', phase='blocked', evidence={'error_code': 'publication_superseded'})
    assert m.recovery_action(blocked['attempts']['a'], published_receipt={**pending, 'status': 'stored'}) == 'blocked'
    assert blocked['competitions']['epl_2026_27']['successful_anchors'] == []


def test_empty_ordinary_snapshot_cannot_bypass_empty_discovery_publication_guard():
    m = _module(); snapshot = _snapshot(); snapshot['matches'] = []
    for discovery in (True, False):
        context = _context()
        if discovery: context.update(events=[], anchor_metadata={})
        state = m.reserve_attempt(m.empty_daily_state(), date_bj='2026-09-01', attempt_id='a', estimated=1, limit=3, context=context)
        _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot}), 'daily_recovery_evidence_invalid')
        assert state['attempts']['a']['phase'] == 'reserved'


def test_snapshot_competition_wrong_container_fails_with_safe_value_error():
    m = _module(); state = _attempt_state(); snapshot = _snapshot()
    snapshot['competition'] = ['epl_2026_27']
    _reject(lambda: m.advance_attempt(state, attempt_id='a', phase='fetched', evidence={'snapshot': snapshot}), 'daily_recovery_evidence_invalid')
