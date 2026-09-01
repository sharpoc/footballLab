"""Real saved odds -> batch -> frozen HMAC -> SQLite -> public rows, offline."""
import copy
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

from worldcup.ingest import build_frozen_ingest_request
from worldcup.ingest_app import process_local_ingest
from worldcup.league_daily_runner import run_league_daily, fetch_daily_odds, load_daily_response
from worldcup.league_daily_state import DailyStateStore
from worldcup.query import project_match_rows
from worldcup.store import SQLiteSnapshotStore
from tests.test_league_daily_runner import _setup, CID, NOW, ENDPOINT
from tests.test_league_daily_fetch import _reserve

FIXTURE = Path(__file__).parent / 'fixtures/league_daily/odds.json'
SECRET = 'offline-e2e-signing-only'


def _manifest(root):
    return {str(p.relative_to(root)): ('dir' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob('*'))}


def _odds(quote_at=NOW):
    # Explicit replay-clock adaptation; retain saved teams, prices and provider structure.
    events = json.loads(FIXTURE.read_text())
    events[0]['commence_time'] = '2026-09-01T12:00:00+00:00'
    for book in events[0]['bookmakers']:
        book['last_update'] = quote_at
        for market in book['markets']:
            market['last_update'] = quote_at
    return events


def _two_odds(quote_at=NOW):
    events = _odds(quote_at)
    second = copy.deepcopy(events[0])
    second['id'] = 'retained-event'
    second['commence_time'] = '2026-09-01T12:30:00+00:00'
    second['home_team'] = 'Liverpool'
    second['away_team'] = 'Manchester City'
    for book in second['bookmakers']:
        for market in book['markets']:
            for outcome in market['outcomes']:
                if outcome['name'] == 'Arsenal':
                    outcome['name'] = 'Liverpool'
                elif outcome['name'] == 'Chelsea':
                    outcome['name'] = 'Manchester City'
    return [events[0], second]


class _Harness:
    def __init__(self, root):
        self.root = root
        _setup(root)
        self.db = root / 'public.sqlite'
        self.store = SQLiteSnapshotStore(self.db)
        self.now = NOW
        self.quote_at = NOW
        self.fetch_count = 0
        self.payloads = []
        self.fail_publish = False

    def fetch(self, url):
        self.fetch_count += 1
        query = parse_qs(urlsplit(url).query)
        assert query['markets'] == ['h2h'] and query['regions'] == ['eu']
        body = json.dumps(_odds(self.quote_at)).encode()
        class Response:
            status = 200
            headers = {'x-requests-last': '1', 'x-requests-remaining': '99', 'x-requests-used': '1'}
            def read(self): return body
        return Response()

    def ingest(self, payload, at):
        request = build_frozen_ingest_request(payload, ENDPOINT, SECRET, at)
        return process_local_ingest(self.db, request['method'], request['path'],
            request['headers'], request['body'], SECRET, now=at, store=self.store)

    def publish(self, *, payload, endpoint, timestamp):
        assert endpoint == ENDPOINT and timestamp == self.now
        self.payloads.append(copy.deepcopy(payload))
        if self.fail_publish:
            return {'status': 'failed'}
        return self.ingest(payload, timestamp)

    def run(self):
        return run_league_daily(root=self.root, now=self.now, live=True, write=True, publish=True,
            endpoint=ENDPOINT, daily_credit_limit=10,
            env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'offline-fixture-key'},
            odds_fetcher=self.fetch, publish_fn=self.publish, observed_clock=lambda: self.now)

    def rows(self):
        return project_match_rows(self.store.latest_snapshot()['snapshot'], as_of=datetime.fromisoformat(self.now))


def test_no_lineup_saved_odds_produce_public_pick_and_repeat_timer_does_not_fetch():
    with TemporaryDirectory() as tmp:
        h = _Harness(Path(tmp).resolve())
        assert h.run()['status'] == 'published'
        rows = h.rows()
        assert len(rows) == 1 and rows[0]['match_decision']['label'] == 'MATCH_PICK'
        assert rows[0]['match_decision']['valid_until'] > NOW
        assert h.store.latest_snapshot()['snapshot']['league_publication']['components']
        assert 'league_publication' not in json.dumps(rows)
        assert not list(h.root.rglob('*lineup*'))
        # Discovery does not claim an event anchor it did not know before fetching.
        h.now = '2026-09-01T10:05:00+00:00'
        assert h.run()['status'] == 'published'
        assert h.fetch_count == 2
        h.now = '2026-09-01T10:10:00+00:00'
        result = h.run()
        assert result['plan']['requests'] == [], result
        assert h.fetch_count == 2, result
        assert h.rows()[0]['match_decision']['label'] == 'MATCH_PICK'


def test_pending_retry_reuses_exact_payload_and_real_ingest_without_new_charge():
    with TemporaryDirectory() as tmp:
        h = _Harness(Path(tmp).resolve()); h.fail_publish = True
        assert h.run()['status'] == 'pending'
        before = DailyStateStore(h.root).read()['budgets']
        h.now = '2026-09-01T10:01:00+00:00'; h.fail_publish = False
        assert h.run()['status'] == 'published'
        assert h.fetch_count == 1 and h.payloads[0] == h.payloads[1]
        assert DailyStateStore(h.root).read()['budgets'] == before
        assert h.store.count_snapshots() == 1
        assert h.rows()[0]['match_decision']['label'] == 'MATCH_PICK'


def test_old_provider_quotes_never_become_fresh_public_pick():
    with TemporaryDirectory() as tmp:
        h = _Harness(Path(tmp).resolve()); h.quote_at = '2026-08-31T10:00:00+00:00'
        assert h.run()['status'] == 'published'
        decision = h.rows()[0]['match_decision']
        assert decision['label'] == 'NO_CLEAN_MARKET', decision
        assert h.fetch_count == 1


def test_late_old_signed_payload_cannot_roll_back_public_latest():
    with TemporaryDirectory() as tmp:
        h = _Harness(Path(tmp).resolve())
        assert h.run()['status'] == 'published'
        old = copy.deepcopy(h.payloads[0])
        h.now = h.quote_at = '2026-09-01T10:35:00+00:00'
        assert h.run()['status'] == 'published'
        latest = h.store.latest_snapshot()
        # A different idempotency key exercises version rejection, not duplicate ACK.
        old['run_id'] = old['run_id'] + '-late'
        result = h.ingest(old, '2026-09-01T10:36:00+00:00')
        assert result['status'] == 'rejected', result
        assert h.store.latest_snapshot() == latest
        assert h.store.count_snapshots() == 2 and h.fetch_count == 2


def test_partial_provider_response_retains_omitted_public_event_in_bound_component():
    """Removing retention would make an unconfirmed match disappear from the public view."""
    with TemporaryDirectory() as tmp:
        h = _Harness(Path(tmp).resolve())

        def fetch_all(url):
            h.fetch_count += 1
            body = json.dumps(_two_odds(h.quote_at)).encode()
            return type('Response', (), {
                'status': 200,
                'headers': {'x-requests-last': '1', 'x-requests-remaining': '99', 'x-requests-used': '1'},
                'read': lambda self: body,
            })()

        h.fetch = fetch_all
        assert h.run()['status'] == 'published'
        first = h.store.latest_snapshot()['snapshot']
        first_partition = json.loads(
            (h.root / 'data/cache/leagues' / CID / 'snapshot.json').read_text()
        )
        assert {row['source_event_id'] for row in first['matches']} == {
            _two_odds()[0]['id'], 'retained-event'
        }

        h.now = h.quote_at = '2026-09-01T10:35:00+00:00'

        def fetch_partial(url):
            h.fetch_count += 1
            body = json.dumps([_odds(h.quote_at)[0]]).encode()
            return type('Response', (), {
                'status': 200,
                'headers': {'x-requests-last': '1', 'x-requests-remaining': '98', 'x-requests-used': '2'},
                'read': lambda self: body,
            })()

        h.fetch = fetch_partial
        result = h.run()
        assert result['status'] == 'published', result
        latest = h.store.latest_snapshot()['snapshot']
        assert {row['source_event_id'] for row in latest['matches']} == {
            _two_odds()[0]['id'], 'retained-event'
        }
        latest_partition = json.loads(
            (h.root / 'data/cache/leagues' / CID / 'snapshot.json').read_text()
        )
        partition_rows = {
            row['source_event_id']: row for row in latest_partition['matches']
        }
        assert partition_rows['retained-event']['source_snapshot_id'] == first_partition['snapshot_id']
        assert partition_rows[_two_odds()[0]['id']]['source_snapshot_id'] == latest_partition['snapshot_id']
        event_rows = {
            row['source_event_id']: row
            for row in json.loads(
                (h.root / 'data/cache/leagues' / CID / 'events.json').read_text()
            )['events']
        }
        assert event_rows['retained-event']['source_snapshot_id'] == first_partition['snapshot_id']
        assert event_rows[_two_odds()[0]['id']]['source_snapshot_id'] == latest_partition['snapshot_id']
        component = latest['league_publication']['components']['odds:' + CID]
        assert component['snapshot_id'] == latest['components'][0]['snapshot_id']
        assert len(component['content_sha256']) == 64


def test_default_dry_run_preserves_entire_temp_tree_and_saved_fixture_tree():
    def forbidden(*args, **kwargs): raise AssertionError('dry-run dependency invoked')
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); _setup(root)
        before = _manifest(root); fixtures = _manifest(FIXTURE.parent)
        result = run_league_daily(root=root, now=NOW, env_loader=forbidden,
            odds_fetcher=forbidden, publish_fn=forbidden, observed_clock=forbidden)
        assert result['mode'] == 'dry_run'
        assert _manifest(root) == before and _manifest(FIXTURE.parent) == fixtures


def test_http_error_cost_is_durable_and_raw_recovery_is_whole_tree_read_only():
    with TemporaryDirectory() as tmp:
        root = Path(tmp).resolve(); request = _reserve(root, ['h2h']); calls = []
        def transport(url):
            calls.append(url)
            raise HTTPError(url, 503, 'offline failure', {'x-requests-last': '2'}, io.BytesIO(b'{"error":"busy"}'))
        result = fetch_daily_odds(request=request, env={'THE_ODDS_API_KEY_PRIMARY': 'offline-fixture-key'},
            root=root, observed_at=NOW, transport=transport, clock=lambda: NOW)
        assert result['status'] == 'error' and result['actual_cost'] == 2
        assert DailyStateStore(root).read()['budgets']['2026-09-01']['reserved_credits'] == 2
        before = _manifest(root)
        recovered = load_daily_response(root=root, attempt_id='a1')
        assert recovered['status'] == 'error' and recovered['actual_cost'] == 2
        assert _manifest(root) == before and len(calls) == 1


def test_real_http_version_rejection_retires_pending_for_both_writers():
    import urllib.request
    from worldcup.http_app import handle_request
    from worldcup.league_daily_runner import frozen_cli_publisher, read_daily_publication
    from worldcup.league_publication import build_publication_vector
    from worldcup.league_post_lineup_refresh import run_post_lineup_refresh
    for writer in ('daily', 'post_lineup'):
        with TemporaryDirectory() as tmp:
            h = _Harness(Path(tmp).resolve()); h.fail_publish = True
            assert h.run()['status'] == 'pending'
            newer = copy.deepcopy(h.payloads[0]); snapshot = newer['snapshot']
            snapshot['snapshot_at'] = '2026-09-01T10:20:00+00:00'
            snapshot['snapshot_id'] = newer['snapshot_id'] = 'server-newer'
            newer['run_id'] = 'server-newer'
            component = {'competition': {'id': CID}, 'snapshot_id': 'server-newer',
                'snapshot_at': snapshot['snapshot_at'], 'matches': snapshot['matches']}
            snapshot['league_publication']['components'] = build_publication_vector([component])
            from worldcup.ingest import build_ingest_payload
            newer = build_ingest_payload(snapshot, generated_at=h.now)
            accepted = h.ingest(newer, '2026-09-01T10:40:00+00:00')
            assert accepted['status'] == 'stored', accepted
            h.now = h.quote_at = '2026-09-01T10:40:00+00:00'
            endpoint = ENDPOINT + '/snapshot'
            # Bind the harness endpoint to the real route before creating the outbox.
            state_path = h.root / 'data/local/leagues/publication_state.json'
            state = json.loads(state_path.read_text()); state['pending']['endpoint'] = endpoint
            state_path.write_text(json.dumps(state))
            statuses = []
            def local_http(req, timeout):
                response = handle_request(req.method, urlsplit(req.full_url).path, dict(req.header_items()),
                    req.data.decode(), h.db, SECRET, now=h.now, store=h.store)
                statuses.append(response['status'])
                body = response['body'].encode()
                if response['status'] >= 400:
                    raise HTTPError(req.full_url, response['status'], 'offline route', response['headers'], io.BytesIO(body))
                class Response:
                    status = response['status']
                    def read(self): return body
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                return Response()
            original = urllib.request.urlopen
            urllib.request.urlopen = local_http
            publisher = frozen_cli_publisher(lambda: {'INGEST_HMAC_SECRET': SECRET})
            try:
                kwargs = dict(root=h.root, now=h.now, live=True, endpoint=endpoint,
                    daily_credit_limit=10, publish_fn=publisher, observed_clock=lambda: h.now)
                if writer == 'daily':
                    result = run_league_daily(**kwargs, write=True, publish=True,
                        odds_fetcher=h.fetch, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'})
                else:
                    receipt = {'event_id': 'new-receipt', 'source_match_id': 's1',
                        'kickoff_at_utc': '2026-09-01T12:00:00+00:00', 'fetched_at': h.now,
                        'lineup_fingerprint': 'a'*64, 'ack_key': {'competition_id': CID,
                            'event_id': 'new-receipt', 'lineup_fingerprint': 'a'*64}}
                    result = run_post_lineup_refresh(**kwargs, newly_confirmed={CID: [receipt]})
                state = read_daily_publication(h.root)
                assert statuses == [400], result
                assert state['pending'] is None and state['superseded'][-1]['reason'] == 'league_component_regression', result
                assert h.fetch_count == 1
                h.now = h.quote_at = '2026-09-01T10:50:00+00:00'
                result = run_league_daily(**dict(kwargs, now=h.now), write=True, publish=True,
                    odds_fetcher=h.fetch, env_loader=lambda: {'THE_ODDS_API_KEY_PRIMARY': 'fake'})
                assert result['status'] == 'published', result
                assert statuses == [400, 200] and h.fetch_count == 2
            finally:
                urllib.request.urlopen = original


def test_unrecognized_http_400_does_not_retire_outbox():
    import worldcup.league_pre_match_runner as pre
    from worldcup.league_daily_runner import frozen_cli_publisher, read_daily_publication
    original = pre._default_sender
    try:
        for body in ['{"error":{"code":"invalid_body","secret":"never surface"}}', '[]', 'not-json']:
            pre._default_sender = lambda request: {'http_status': 400, 'body': body}
            with TemporaryDirectory() as tmp:
                h = _Harness(Path(tmp).resolve())
                h.publish = frozen_cli_publisher(lambda: {'INGEST_HMAC_SECRET': SECRET})
                result = h.run()
                state = read_daily_publication(h.root)
                assert result['status'] == 'pending' and state['pending'] is not None and not state['superseded']
                assert 'never surface' not in json.dumps(result)
    finally:
        pre._default_sender = original
