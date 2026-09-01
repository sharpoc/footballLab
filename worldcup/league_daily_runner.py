"""Daily odds seams. The runtime caller owns live gates and the execution lock."""
from __future__ import annotations

import base64
import hashlib
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import urlopen

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS, get_competition
from worldcup.league_daily_state import DailyStateStore
from worldcup.quota import load_quota_ledger, update_quota_from_headers
from worldcup.sources.theoddsapi import SourceFetchError, fetch_odds_for_sport
from worldcup.theoddsapi_keys import choose_key_slot


def _time(value):
    if not isinstance(value, str):
        raise ValueError('daily_time_invalid')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('daily_time_invalid')
    return result.astimezone(timezone.utc)


def _safe_path(path):
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError('daily_response_path_invalid')


def _save_once(path, payload):
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _cost(headers):
    value = headers.get('x-requests-last')
    return int(value) if isinstance(value, str) and re.fullmatch(r'[0-9]+', value) else None


def fetch_daily_odds(*, request: dict, env: dict, root: Path,
                     observed_at: str, transport=None, clock=None, quota_path=None) -> dict:
    """One pre-reserved transport attempt; persist raw bytes before JSON parsing.

    Caller must hold odds_execution_lock and validate acceptance/registry first.
    request needs attempt_id/competition_id/markets matching durable reservation.
    clock is an injectable completion clock, never the supplied planning time.
    """
    store = DailyStateStore(root)
    try:
        attempt_id = request['attempt_id']
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', attempt_id):
            raise ValueError('daily_attempt_invalid')
        cid = request['competition_id']; markets = request['markets']
        if cid not in FORMAL_SINGLE_MATCH_IDS or not isinstance(markets, list) or not markets:
            raise ValueError('daily_request_invalid')
        if any(m not in ('h2h', 'spreads', 'totals') for m in markets) or len(set(markets)) != len(markets):
            raise ValueError('daily_request_invalid')
        state = store.read(); attempt = state['attempts'][attempt_id]
        context = attempt['context']
        if (attempt['phase'] != 'reserved' or context['competition_id'] != cid
                or context['markets'] != markets or _time(context['request_at']) != _time(observed_at)):
            raise ValueError('daily_reservation_mismatch')
        reservation = next(day['reservations'][attempt_id] for day in state['budgets'].values()
                           if attempt_id in day['reservations'])
        if reservation['status'] != 'reserved' or reservation['estimated'] != len(markets):
            raise ValueError('daily_reservation_mismatch')
        directory = Path(root) / 'data/local/leagues/daily_attempts' / attempt_id
        response_path = directory / 'response.json'
        sent_path = directory / 'request_started.json'
        _safe_path(response_path); _safe_path(sent_path)
        if response_path.exists() or sent_path.exists():
            raise ValueError('daily_attempt_already_sent')
        quota_path = Path(quota_path) if quota_path is not None else Path(root) / 'data/cache/quota.json'
        _safe_path(quota_path)
        selected = choose_key_slot(env, load_quota_ledger(quota_path).get('providers', {}))
        if selected is None:
            raise ValueError('missing_or_exhausted_key')
    except (ValueError, KeyError, TypeError, StopIteration):
        return {'status': 'blocked', 'reason': 'daily_fetch_precondition_failed'}

    capture = {}
    def wrapped(url):
        _save_once(sent_path, {'attempt_id': attempt_id, 'request_at': observed_at})
        try:
            response = transport(url) if transport else urlopen(url, timeout=30)
        except HTTPError as exc:
            response = exc
        try:
            body = response.read()
            completed_at = (clock or (lambda: datetime.now(timezone.utc).isoformat()))()
            if _time(completed_at) < _time(observed_at):
                raise ValueError('daily_clock_regression')
            headers = {str(k).lower(): str(v) for k, v in dict(getattr(response, 'headers', {})).items()}
            status = int(getattr(response, 'status', 200))
            actual = _cost(headers)
            capture.update(completed_at=completed_at, actual_cost=actual,
                           response_path=str(response_path), slot=selected.slot)
            _save_once(response_path, {'schema_version': 1, 'attempt_id': attempt_id,
                'context': context, 'completed_at': completed_at, 'actual_cost': actual,
                'slot': selected.slot, 'status': status,
                'body_sha256': hashlib.sha256(body).hexdigest(),
                'body_base64': base64.b64encode(body).decode('ascii')})
            store.settle(attempt_id=attempt_id, actual_cost=actual)
            # Parse failures and HTTP errors still carry quota headers; do not lose them.
            safe_headers = {k: v for k, v in headers.items() if k in
                            ('x-requests-last', 'x-requests-used', 'x-requests-remaining')}
            for provider in (selected.provider, 'theoddsapi'):
                entry = update_quota_from_headers(quota_path, provider, safe_headers,
                            estimated_last=len(markets), observed_at=completed_at)
            capture['quota'] = {k: entry[k] for k in ('used', 'remaining', 'last') if k in entry}
            class Buffered:
                def read(self):
                    return body
            buffered = Buffered(); buffered.status = status; buffered.headers = safe_headers
            return buffered
        finally:
            close = getattr(response, 'close', None)
            if close is not None:
                close()
    try:
        result = fetch_odds_for_sport(api_key=selected.api_key,
            sport_key=get_competition(cid).theoddsapi_sport_key, transport=wrapped,
            markets=tuple(markets), regions='eu', max_attempts=1)
    except SourceFetchError as exc:
        return {'status': 'error', 'reason': exc.reason, 'actual_cost': None, **capture}
    if not isinstance(result.json_body, list):
        return {'status': 'error', 'reason': 'daily_response_invalid', **capture}
    return {'status': 'fetched', 'raw_events': result.json_body, **capture}


def discover_events(*, raw_events: list, competition_id: str, registry,
                    observed_at: str) -> dict:
    """Strict same-response discovery; omissions never imply cancellation."""
    if competition_id not in FORMAL_SINGLE_MATCH_IDS or not isinstance(raw_events, list):
        raise ValueError('daily_response_invalid')
    observed = _time(observed_at)
    sport = get_competition(competition_id).theoddsapi_sport_key
    ids = Counter(row.get('id') for row in raw_events if isinstance(row, dict)
                  and isinstance(row.get('id'), str))
    events = []; rejected = []; accepted_raw = []
    for index, row in enumerate(raw_events):
        reason = None
        try:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
                raise ValueError('invalid_event')
            if ids[row['id']] != 1:
                raise ValueError('duplicate_event_id')
            if row.get('sport_key') != sport:
                raise ValueError('sport_key_mismatch')
            kickoff = _time(row.get('commence_time'))
            if kickoff <= observed or row.get('completed') is True or row.get('fixture_status') in (
                    'FT', 'FINISHED', 'CANCELLED', 'POSTPONED'):
                raise ValueError('event_not_future')
            identity = registry.resolve_fixture(competition_id, row.get('home_team'), row.get('away_team'))
            if identity['status'] != 'verified':
                raise ValueError('unmatched_team')
            events.append({'source_event_id': row['id'], 'kickoff_at_utc': kickoff.isoformat(),
                           'home_canonical': identity['home_canonical'],
                           'away_canonical': identity['away_canonical']})
            accepted_raw.append(row)
        except (ValueError, TypeError):
            reason = 'daily_event_rejected'
        if reason:
            rejected.append({'index': index, 'reason': reason})
    return {'events': events, 'rejected': rejected, 'raw_events': accepted_raw}


def load_daily_response(*, root: Path, attempt_id: str) -> dict:
    """Read-only recovery of frozen response; never retry transport or move clocks.

    Caller may settle the recovered actual_cost idempotently if capture preceded
    a crash in accounting. Invalid/missing artifacts do not prove a free request.
    """
    try:
        if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', attempt_id):
            raise ValueError('daily_attempt_invalid')
        context = DailyStateStore(root).read()['attempts'][attempt_id]['context']
        path = Path(root) / 'data/local/leagues/daily_attempts' / attempt_id / 'response.json'
        _safe_path(path)
        evidence = json.loads(path.read_text(encoding='utf-8'))
        actual = evidence['actual_cost']
        if (evidence['schema_version'] != 1 or evidence['attempt_id'] != attempt_id
                or evidence['context'] != context
                or _time(evidence['completed_at']) < _time(context['request_at'])
                or (actual is not None and (type(actual) is not int or actual < 0))
                or evidence['slot'] not in ('primary', 'secondary', 'tertiary', 'quaternary', 'quinary')
                or type(evidence['status']) is not int):
            raise ValueError('daily_response_invalid')
        body = base64.b64decode(evidence['body_base64'], validate=True)
        if evidence.get('body_sha256') != hashlib.sha256(body).hexdigest():
            raise ValueError('daily_response_evidence_invalid')
    except (ValueError, TypeError, KeyError, OSError):
        return {'status': 'blocked', 'reason': 'daily_response_evidence_invalid'}
    result = {'actual_cost': actual, 'completed_at': evidence['completed_at'],
              'response_path': str(path), 'slot': evidence['slot']}
    if not 200 <= evidence['status'] < 300:
        return {'status': 'error', 'reason': 'http_error', **result}
    try:
        raw = json.loads(body)
        if not isinstance(raw, list):
            raise ValueError('daily_response_invalid')
    except (ValueError, UnicodeError):
        return {'status': 'error', 'reason': 'daily_response_invalid', **result}
    return {'status': 'fetched', 'raw_events': raw, **result}


def commit_fetched_daily_odds(*, request: dict, fetched: dict, root: Path, env: dict,
                             acceptance_report: dict, guarded_acceptance_fingerprint: str,
                             registry, before_commit=None, commit_callback=None,
                             build_at: str | None = None) -> dict:
    """Build/commit only future strict events from the durable response, no IO fetch.

    before_commit(snapshot) allows Task5 to persist fetched evidence before batch's
    durable history/current commit. State success and cache merging belong to caller.
    Empty does not manufacture a snapshot or imply that prior events were cancelled.
    build_at is the real build observation for raw-only recovery: source bytes and
    provider quote timestamps remain frozen, while time eligibility/freshness and
    snapshot/decision time use build_at. A durably fetched full snapshot is instead
    recovered by the caller without rebuilding it.
    """
    from worldcup.league_batch_runner import run_planned_league_refresh
    from worldcup.league_competition_pipeline import build_league_competition_snapshot
    cid = request['competition_id']
    attempt_id = request['attempt_id']
    context = DailyStateStore(root).read()['attempts'][attempt_id]['context']
    recovered = load_daily_response(root=root, attempt_id=attempt_id)
    if recovered['status'] != 'fetched' or fetched.get('status') != 'fetched':
        raise ValueError('daily_response_evidence_mismatch')
    raw = recovered['raw_events']
    completed = recovered['completed_at']
    if raw != fetched.get('raw_events') or completed != fetched.get('completed_at'):
        raise ValueError('daily_response_evidence_mismatch')
    built_at = completed if build_at is None else _time(build_at).isoformat()
    if _time(built_at) < _time(completed):
        raise ValueError('daily_clock_regression')
    discovery = discover_events(raw_events=raw, competition_id=cid, registry=registry,
                                observed_at=built_at)
    if not discovery['events']:
        return {'status': 'empty', 'discovery': discovery, 'completed_at': completed,
                'valid_empty_response': raw == []}
    snapshots = []
    def builder(payload, competition_id, observed_at, **kwargs):
        snapshot = build_league_competition_snapshot(payload, competition_id, observed_at, **kwargs)
        snapshot['snapshot_id'] = context['expected_snapshot_id']
        snapshot['run_id'] = context['expected_snapshot_id']
        for match in snapshot['matches']:
            match['source_snapshot_id'] = snapshot['snapshot_id']
        if build_at is not None:
            snapshot['data_quality']['odds_response_observed_at'] = completed
        previous = _read_json_safe(
            Path(root) / 'data/cache/leagues' / competition_id / 'snapshot.json'
        )
        if previous is not None:
            from worldcup.league_publication import build_publication_vector
            build_publication_vector([previous])
            old_matches = {row['source_event_id']: row for row in previous['matches']}
            new_matches = {row['source_event_id']: row for row in snapshot['matches']}
            retained = []
            for event_id, old in old_matches.items():
                new = new_matches.get(event_id)
                if new is not None:
                    if any(new.get(field) != old.get(field) for field in (
                        'kickoff_at_utc', 'home_canonical', 'away_canonical'
                    )):
                        raise ValueError('daily_event_identity_conflict')
                    continue
                retained_match = dict(old)
                source_snapshot_id = retained_match.get('source_snapshot_id', previous['snapshot_id'])
                if not isinstance(source_snapshot_id, str) or not source_snapshot_id.strip():
                    raise ValueError('daily_event_source_snapshot_invalid')
                retained_match['source_snapshot_id'] = source_snapshot_id.strip()
                snapshot['matches'].append(retained_match)
                retained.append(event_id)
            if retained:
                snapshot['matches'].sort(key=lambda row: row['source_event_id'])
                snapshot['data_quality']['provider_omission_retained_event_ids'] = sorted(retained)
                snapshot['data_quality']['provider_omission_source_snapshot_id'] = previous['snapshot_id']
        if before_commit is not None:
            before_commit(snapshot)
        snapshots.append(snapshot)
        return snapshot
    def memory_fetch(sport_key, _env):
        if sport_key != get_competition(cid).theoddsapi_sport_key:
            raise ValueError('daily_response_sport_mismatch')
        return discovery['raw_events']
    result = run_planned_league_refresh(root=root, observed_at=built_at,
        competition_ids=[cid], env=env, odds_fetcher=memory_fetch,
        acceptance_report=acceptance_report, identity_registry=registry,
        guarded_acceptance_fingerprint=guarded_acceptance_fingerprint,
        expected_event_ids_by_competition={cid: [row['source_event_id'] for row in discovery['events']]},
        expected_snapshot_ids_by_competition={cid: context['expected_snapshot_id']},
        snapshot_builder=builder, commit_callback=commit_callback)
    return {**result, 'discovery': discovery, 'built_snapshots': snapshots, 'completed_at': completed}


def valid_daily_endpoint(endpoint):
    from urllib.parse import urlsplit
    if not isinstance(endpoint, str):
        return False
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or '').rstrip('.').lower()
        parsed.port
    except ValueError:
        return False
    reserved = ('test', 'invalid', 'localhost', 'example', 'example.com', 'example.net', 'example.org')
    return (parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username
            and not parsed.password and not parsed.fragment
            and host != '127.0.0.1'
            and not any(host == domain or host.endswith('.' + domain) for domain in reserved))


def read_daily_acceptance(root, registry=None):
    """Persisted approval is the trust boundary, never an injected active flag."""
    from worldcup.league_acceptance import LeagueAcceptanceStore, acceptance_row_is_active
    from worldcup.league_team_identity import accepted_league_team_identity_registry, league_team_identity_registry_fingerprint
    path = Path(root) / 'data/local/leagues/acceptance.json'
    _safe_path(path)
    report = LeagueAcceptanceStore(path).read()
    if report is None:
        raise ValueError('daily_acceptance_missing')
    registry = registry if registry is not None else accepted_league_team_identity_registry()
    for cid, row in report['competitions'].items():
        if row.get('state') != 'active':
            continue
        fingerprints = row.get('fingerprints', {})
        if not acceptance_row_is_active(row, cid) or any(
            not isinstance(fingerprints.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', fingerprints[key])
            for key in ('sport_catalog', 'odds_sample', 'team_identity', 'result_contract')):
            raise ValueError('daily_acceptance_invalid')
        if fingerprints['team_identity'] != league_team_identity_registry_fingerprint(registry, cid):
            raise ValueError('daily_registry_changed')
    return report, registry


def _read_json_safe(path):
    _safe_path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def read_daily_publication(root):
    """Read-only structural checks also prevent symlink traversal before env/send."""
    path = Path(root) / 'data/local/leagues/publication_state.json'
    _safe_path(path.with_name('publication.lock'))
    value = _read_json_safe(path)
    if value is None:
        return {'schema_version': 1, 'components': {}, 'pending': None, 'sent': None, 'superseded': []}
    from worldcup.league_publication import validate_component_vector, publication_vector
    from worldcup.ingest import canonical_json
    import hashlib
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('daily_publication_invalid')
    validate_component_vector({}, value.get('components'))
    if not isinstance(value.get('superseded'), list):
        raise ValueError('daily_publication_invalid')
    pending = value.get('pending')
    if pending is not None:
        if not isinstance(pending, dict) or not valid_daily_endpoint(pending.get('endpoint')):
            raise ValueError('daily_publication_invalid')
        body = canonical_json(pending['payload'])
        if (body != pending['body'] or hashlib.sha256(body.encode()).hexdigest() != pending['body_sha256']
                or publication_vector(pending['payload']['snapshot']) != pending['components']):
            raise ValueError('daily_publication_invalid')
    sent = value.get('sent')
    if sent is not None and (not isinstance(sent, dict) or sent.get('status') not in {'stored', 'duplicate'}
            or not valid_daily_endpoint(sent.get('endpoint')) or not re.fullmatch(r'[a-f0-9]{64}', str(sent.get('body_sha256')))):
        raise ValueError('daily_publication_invalid')
    return value


def _daily_inputs(root, now, limit, quota_mode='normal'):
    from worldcup.league_daily_plan import load_daily_events, plan_daily_refresh
    from worldcup.league_acceptance import acceptance_row_is_active
    state = DailyStateStore(root).read()
    report, registry = read_daily_acceptance(root)
    publication = read_daily_publication(root)
    for cid in report['competitions']:
        for relative in (f'data/cache/leagues/{cid}/events.json', f'data/cache/leagues/{cid}/snapshot.json',
                         f'data/local/leagues/{cid}/history', f'data/local/leagues/{cid}/.snapshot.lock'):
            _safe_path(Path(root) / relative)
    loaded = load_daily_events(root, report, registry)
    events = dict(loaded['events'])
    from worldcup.league_publication import build_publication_vector, validate_component_vector
    for cid in list(events):
        try:
            current_path = Path(root) / 'data/cache/leagues' / cid / 'snapshot.json'
            current = _read_json_safe(current_path)
            if current_path.exists():
                vector = build_publication_vector([current])
                previous = publication['components'].get('odds:' + cid)
                if previous:
                    validate_component_vector({'odds:' + cid: previous}, vector)
        except (ValueError, TypeError, KeyError, OSError):
            events.pop(cid)
            loaded['errors'].append({'competition_id': cid, 'reason': 'daily_partition_regression'})
    for error in loaded['errors']:
        if error['reason'] == 'production_events_missing':
            events[error['competition_id']] = []
    plan = plan_daily_refresh(now=now, events=events, acceptance=report, state=state,
                              quota_mode=quota_mode, daily_credit_limit=limit)
    if not any(acceptance_row_is_active(row, cid) for cid, row in report['competitions'].items()):
        plan['live_blockers'].append('daily_no_active_competitions')
    return report, registry, state, publication, events, loaded['errors'], plan


def _quota_mode(root, env, quota_path):
    from worldcup.theoddsapi_keys import configured_key_slots
    _safe_path(quota_path)
    ledger = load_quota_ledger(quota_path)
    if not isinstance(ledger, dict) or not isinstance(ledger.get('providers'), dict):
        raise ValueError('daily_quota_invalid')
    providers = ledger['providers']
    for entry in providers.values():
        if not isinstance(entry, dict) or any(key in entry and entry[key] is not None and
                (type(entry[key]) is not int or entry[key] < 0) for key in ('used', 'remaining', 'last')):
            raise ValueError('daily_quota_invalid')
    slots = configured_key_slots(env)
    if not slots:
        return 'exhausted'
    remaining = [providers.get(slot.provider, {}).get('remaining') for slot in slots]
    if any(value is None or (type(value) is int and value > 30) for value in remaining):
        return 'normal'
    return 'low' if any(type(value) is int and value > 0 for value in remaining) else 'exhausted'


def _merge_events(root, cid, discovery, snapshot, *, write=False):
    from worldcup.league_live_store import _atomic_write
    path = Path(root) / 'data/cache/leagues' / cid / 'events.json'
    previous = _read_json_safe(path)
    if previous is None:
        old = _read_json_safe(path.with_name('snapshot.json')) or {}
        rows = old.get('matches', [])
    else:
        rows = previous['events']
    previous_snapshot_id = previous.get('source_snapshot_id') if isinstance(previous, dict) else None
    by_id = {}
    for row in rows:
        retained = dict(row)
        source_snapshot_id = retained.get('source_snapshot_id', previous_snapshot_id)
        if not isinstance(source_snapshot_id, str) or not source_snapshot_id.strip():
            raise ValueError('daily_event_source_snapshot_invalid')
        retained['source_snapshot_id'] = source_snapshot_id.strip()
        by_id[retained['source_event_id']] = retained
    for row in discovery:
        old = by_id.get(row['source_event_id'])
        if old is not None and any(old.get(key) != row.get(key) for key in
                                   ('kickoff_at_utc', 'home_canonical', 'away_canonical')):
            raise ValueError('daily_event_identity_conflict')
        current = dict(row)
        current['source_snapshot_id'] = snapshot['snapshot_id']
        by_id[row['source_event_id']] = current
    # Keep decision/expiry evidence for every returned event, and retain omissions.
    for row in snapshot['matches']:
        current = dict(row)
        source_snapshot_id = current.get('source_snapshot_id', snapshot['snapshot_id'])
        if not isinstance(source_snapshot_id, str) or not source_snapshot_id.strip():
            raise ValueError('daily_event_source_snapshot_invalid')
        current['source_snapshot_id'] = source_snapshot_id.strip()
        by_id[row['source_event_id']] = current
    value = {'schema_version': 1, 'competition_id': cid, 'observed_at': snapshot['snapshot_at'],
             'source_snapshot_id': snapshot['snapshot_id'], 'events': list(by_id.values())}
    if write:
        _atomic_write(path, json.dumps(value, sort_keys=True, ensure_ascii=False))
    return value


def _commit_daily_snapshot(root, store, attempt_id, snapshot):
    from worldcup.league_live_store import LeagueLiveStore
    cid = snapshot['competition']['id']
    current_path = Path(root) / 'data/cache/leagues' / cid / 'snapshot.json'
    current = _read_json_safe(current_path)
    if current is not None:
        if (_time(current['snapshot_at']) > _time(snapshot['snapshot_at']) or
                (_time(current['snapshot_at']) == _time(snapshot['snapshot_at']) and current != snapshot)):
            raise ValueError('daily_snapshot_regression')
    LeagueLiveStore(root).commit_snapshot(cid, snapshot)
    history = _read_json_safe(Path(root) / 'data/local/leagues' / cid / 'history' / (snapshot['snapshot_id'] + '.json'))
    current = _read_json_safe(current_path)
    store.advance(attempt_id=attempt_id, phase='committed', evidence={'history': history, 'current': current})
    _merge_events(root, cid, snapshot['matches'], snapshot, write=True)


def reconcile_daily_publication(root):
    """Only the exact durable coordinator vector+hash can complete an attempt."""
    from worldcup.league_publication import build_publication_vector
    publication = read_daily_publication(root)
    store = DailyStateStore(root)
    for aid, attempt in store.read()['attempts'].items():
        if attempt.get('context') is None:
            continue
        if attempt['phase'] not in {'committed', 'pending'}:
            continue
        snapshot = attempt['evidence']['fetched']['snapshot']
        key = 'odds:' + attempt['context']['competition_id']
        component = build_publication_vector([snapshot])[key]
        retired = [entry for entry in publication['superseded']
            if isinstance(entry, dict) and isinstance(entry.get('pending'), dict)
            and entry['pending'].get('components', {}).get(key) == component]
        if retired:
            store.advance(attempt_id=aid, phase='blocked', evidence={'error_code': 'daily_publication_superseded'})
            continue
        pending = publication['pending']
        sent = publication['sent']
        binding = pending if pending and pending['components'].get(key) == component else None
        published = sent if sent and publication['components'].get(key) == component else None
        if binding is None and published is None:
            continue
        selected = binding or published
        evidence = {'snapshot': snapshot, 'endpoint': selected['endpoint'], 'body_sha256': selected['body_sha256']}
        if attempt['phase'] == 'committed':
            store.advance(attempt_id=aid, phase='pending', evidence=evidence)
        if published and published['body_sha256'] == evidence['body_sha256']:
            store.advance(attempt_id=aid, phase='published', evidence={**evidence, 'status': published['status']})


def publish_daily_components(*, root, endpoint, publish_fn, now, acceptance_fingerprint, stale=None):
    from worldcup.league_scheduled_publish import build_aggregate_league_snapshot
    from worldcup.league_publication import deliver_league_publication, build_publication_vector
    publication = read_daily_publication(root)
    if publication['pending'] is not None:
        if publication['pending']['accepted_fingerprint'] != acceptance_fingerprint:
            return {'status': 'blocked', 'reason': 'daily_acceptance_changed'}
        result = deliver_league_publication(root=Path(root), endpoint=endpoint, snapshot=None,
                                            publish_fn=publish_fn, now=now)
        reconcile_daily_publication(root)
        return result
    snapshots = []
    report, _ = read_daily_acceptance(root)
    from worldcup.league_acceptance import acceptance_row_is_active
    for cid, row in report['competitions'].items():
        if not acceptance_row_is_active(row, cid):
            continue
        current_path = Path(root) / 'data/cache/leagues' / cid / 'snapshot.json'
        try:
            snapshot = _read_json_safe(current_path)
            vector = build_publication_vector([snapshot])
            from worldcup.league_publication import validate_component_vector
            previous = publication['components'].get('odds:' + cid)
            if previous:
                validate_component_vector({'odds:' + cid: previous}, vector)
        except (ValueError, TypeError, KeyError, OSError):
            previous = publication['components'].get('odds:' + cid)
            if previous is None:
                raise ValueError('daily_lkg_missing')
            snapshot = _read_json_safe(Path(root) / 'data/local/leagues' / cid / 'history' / (previous['snapshot_id'] + '.json'))
            if build_publication_vector([snapshot]).get('odds:' + cid) != previous:
                raise ValueError('daily_lkg_invalid')
            if stale is not None:
                stale[cid] = {'status': 'stale', 'reason': 'daily_partition_regression'}
        snapshots.append(snapshot)
    aggregate = build_aggregate_league_snapshot(root=root, snapshots=snapshots,
        expected_acceptance_fingerprint=acceptance_fingerprint)
    aggregate['data_quality']['stale_competitions'] = sorted(cid for cid, row in (stale or {}).items() if row['status'] == 'stale')
    result = deliver_league_publication(root=Path(root), endpoint=endpoint, snapshot=aggregate,
                                        publish_fn=publish_fn, now=now)
    reconcile_daily_publication(root)
    return result


def run_league_daily(*, root: Path, now: str, live=False, write=False, publish=False,
                     endpoint=None, daily_credit_limit=None, env_loader=None,
                     odds_fetcher=None, publish_fn=None, observed_clock=None, quota_path=None):
    """Dry-run is read-only. odds_fetcher is the single HTTP transport boundary."""
    from contextlib import ExitStack
    from uuid import uuid4
    from zoneinfo import ZoneInfo
    from worldcup.league_daily_state import _lock, odds_execution_lock
    from worldcup.league_acceptance import acceptance_fingerprint
    from worldcup.league_team_identity import league_team_identity_registry_fingerprint
    result = {'mode': 'live' if live else 'dry_run', 'status': 'blocked', 'plan': {},
              'competitions': {}, 'publish': None, 'safety': {'network': False}}
    root = Path(root)
    try:
        _time(now)
        if any((live, write, publish)) and not all((live, write, publish)):
            raise ValueError('daily_live_flags_required')
        if live and not valid_daily_endpoint(endpoint):
            raise ValueError('daily_endpoint_invalid')
        if live and (type(daily_credit_limit) is not int or daily_credit_limit <= 0):
            raise ValueError('daily_budget_unconfigured')
        inputs = _daily_inputs(root, now, daily_credit_limit)
        result['plan'] = inputs[-1]
        if not live:
            result['status'] = 'blocked' if result['plan']['live_blockers'] else 'planned'
            return result
        if result['plan']['live_blockers']:
            return result
        from worldcup.observed_clock import MonotonicUtcClock
        monotonic_clock = MonotonicUtcClock(observed_clock)
        clock = lambda: monotonic_clock.now().isoformat()
        with ExitStack() as stack:
            stack.enter_context(_lock(root / 'data/local/leagues/daily_runner.lock', nonblocking=True))
            stack.enter_context(odds_execution_lock(root))
            observed = _time(clock()).isoformat()
            report, registry, state, publication, events, errors, plan = _daily_inputs(root, observed, daily_credit_limit)
            fingerprint = acceptance_fingerprint(report)
            if publication['pending'] and publication['pending']['accepted_fingerprint'] != fingerprint:
                raise ValueError('daily_acceptance_changed')
            if publish_fn is None:
                raise ValueError('daily_publisher_missing')
            if publication['pending']:
                result['publish'] = publish_daily_components(root=root, endpoint=endpoint, publish_fn=publish_fn,
                    now=observed, acceptance_fingerprint=fingerprint)
                result['status'] = 'published' if result['publish']['status'] in {'stored', 'duplicate'} else result['publish']['status']
                return result
            reconcile_daily_publication(root)
            env = env_loader() if env_loader else {}
            if not isinstance(env, dict):
                raise ValueError('daily_env_invalid')
            quota_file = Path(quota_path) if quota_path is not None else root / 'data/cache/quota.json'
            mode = _quota_mode(root, env, quota_file)
            inputs = _daily_inputs(root, _time(clock()).isoformat(), daily_credit_limit, mode)
            report, registry, state, publication, events, errors, plan = inputs
            result['plan'] = plan
            for error in errors:
                if error['reason'] != 'production_events_missing':
                    result['competitions'][error['competition_id']] = {'status': 'stale', 'reason': error['reason']}
            store = DailyStateStore(root)
            work = []
            recovering = set()
            for aid, attempt in state['attempts'].items():
                if attempt.get('context') is None:
                    continue
                if attempt['phase'] == 'reserved':
                    response_path = root / 'data/local/leagues/daily_attempts' / aid / 'response.json'
                    _safe_path(response_path)
                    if not response_path.exists() and _time(clock()) >= _time(attempt['context']['request_at']) + timedelta(minutes=30):
                        # Retain the unknown charge and exact request audit forever;
                        # only a new independently budgeted attempt may use transport.
                        store.advance(attempt_id=aid, phase='blocked', evidence={'error_code': 'daily_response_unknown'})
                        continue
                if attempt['phase'] in {'reserved', 'fetched', 'committed', 'pending'} and 'empty_discovery' not in attempt.get('evidence', {}).get('fetched', {}):
                    recovering.add(attempt['context']['competition_id'])
                    work.append((aid, attempt, None))
            work.extend((None, None, request) for request in plan['requests'] if request['competition_id'] not in recovering)
            changed = any(a['phase'] in {'committed', 'pending'} for a in state['attempts'].values())
            for aid, attempt, request in work:
                cid = attempt['context']['competition_id'] if attempt else request['competition_id']
                try:
                    fresh_report, registry = read_daily_acceptance(root)
                    if acceptance_fingerprint(fresh_report) != fingerprint:
                        raise ValueError('daily_acceptance_changed')
                    if attempt is None:
                        requested = _time(clock()).isoformat()
                        # Fresh plan protects budget, quorum and kickoff after waiting/work.
                        fresh = _daily_inputs(root, requested, daily_credit_limit, _quota_mode(root, env, quota_file))
                        request = next((r for r in fresh[-1]['requests'] if r['competition_id'] == cid), None)
                        if request is None:
                            continue
                        aid = 'daily-' + uuid4().hex
                        selected_events = [{key: row[key] for key in ('event_id', 'kickoff_at_utc', 'home_canonical', 'away_canonical')}
                                           for row in fresh[4].get(cid, []) if row['event_id'] in request['event_ids']]
                        context = {'competition_id': cid, 'acceptance_fingerprint': fingerprint,
                            'registry_fingerprint': league_team_identity_registry_fingerprint(registry, cid),
                            'markets': request['markets'], 'expected_snapshot_id': 'league-attempt-' + aid,
                            'request_at': requested, 'events': selected_events, 'anchor_metadata': request.get('anchor_metadata', {})}
                        store.reserve(context=context, date_bj=_time(requested).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat(),
                                      attempt_id=aid, estimated=len(request['markets']), limit=daily_credit_limit)
                        attempt = store.read()['attempts'][aid]
                        fetched = fetch_daily_odds(request={**request, 'attempt_id': aid}, root=root, env=env,
                            observed_at=requested, transport=odds_fetcher, clock=clock, quota_path=quota_file)
                        result['safety']['network'] = True
                    else:
                        context = attempt['context']
                        if context['acceptance_fingerprint'] != fingerprint or context['registry_fingerprint'] != league_team_identity_registry_fingerprint(registry, cid):
                            raise ValueError('daily_acceptance_changed')
                        fetched = load_daily_response(root=root, attempt_id=aid) if attempt['phase'] == 'reserved' else None
                    if attempt['phase'] == 'reserved':
                        if fetched.get('response_path') is not None:
                            store.settle(attempt_id=aid, actual_cost=fetched.get('actual_cost'))
                        if fetched['status'] != 'fetched':
                            if fetched['status'] == 'error' and fetched.get('response_path') is not None:
                                store.advance(attempt_id=aid, phase='blocked', evidence={'error_code': 'daily_response_invalid'})
                            raise ValueError('daily_response_recovery_required')
                        store.settle(attempt_id=aid, actual_cost=fetched['actual_cost'])
                        context = attempt['context']
                        def before(snapshot):
                            _merge_events(root, cid, snapshot['matches'], snapshot)
                            current = _read_json_safe(root / 'data/cache/leagues' / cid / 'snapshot.json')
                            if current and _time(current['snapshot_at']) >= _time(snapshot['snapshot_at']) and current != snapshot:
                                raise ValueError('daily_snapshot_regression')
                            store.advance(attempt_id=aid, phase='fetched', evidence={'snapshot': snapshot})
                        built = commit_fetched_daily_odds(request={'attempt_id': aid, 'competition_id': cid}, fetched=fetched,
                            root=root, env=env, acceptance_report=report, guarded_acceptance_fingerprint=fingerprint,
                            registry=registry, before_commit=before, build_at=clock())
                        if built['status'] == 'empty':
                            if not context['events'] and built['valid_empty_response']:
                                store.advance(attempt_id=aid, phase='fetched', evidence={'empty_discovery': {
                                    'competition_id': cid, 'observed_at': built['completed_at'], 'event_ids': []}})
                                result['competitions'][cid] = {'status': 'discovery_complete'}
                                continue
                            store.advance(attempt_id=aid, phase='blocked', evidence={'error_code': 'daily_no_future_valid_events'})
                            raise ValueError('daily_no_future_valid_events')
                        attempt = store.read()['attempts'][aid]
                        if attempt['phase'] != 'fetched':
                            raise ValueError('daily_batch_commit_failed')
                    if attempt['phase'] == 'fetched':
                        snapshot = attempt['evidence']['fetched']['snapshot']
                        _commit_daily_snapshot(root, store, aid, snapshot)
                    changed = True
                    result['competitions'][cid] = {'status': 'committed'}
                except (ValueError, TypeError, KeyError, OSError) as exc:
                    code = str(exc) if re.fullmatch(r'[a-z_]+', str(exc)) else 'daily_partition_failed'
                    result['competitions'][cid] = {'status': 'stale', 'reason': code}
            if changed:
                result['publish'] = publish_daily_components(root=root, endpoint=endpoint, publish_fn=publish_fn,
                    now=_time(clock()).isoformat(), acceptance_fingerprint=fingerprint, stale=result['competitions'])
                result['status'] = 'published' if result['publish']['status'] in {'stored', 'duplicate'} else result['publish']['status']
                if result['status'] == 'published' and any(row['status'] == 'stale' for row in result['competitions'].values()):
                    result['status'] = 'partial'
            else:
                result['status'] = 'blocked' if any(row['status'] == 'stale' for row in result['competitions'].values()) else 'not_due'
    except (ValueError, TypeError, KeyError, OSError) as exc:
        result['reason'] = str(exc) if re.fullmatch(r'[a-z_]+', str(exc)) else 'daily_input_invalid'
    return result


def frozen_cli_publisher(env_loader):
    def send(*, payload, endpoint, timestamp):
        from worldcup.ingest import build_frozen_ingest_request
        from worldcup.league_pre_match_runner import _default_sender
        env = env_loader()
        request = build_frozen_ingest_request(payload=payload, endpoint=endpoint,
            secret=env.get('INGEST_HMAC_SECRET', ''), timestamp=timestamp)
        response = _default_sender(request)
        try:
            body = json.loads(response.get('body', '{}'))
        except (ValueError, TypeError):
            return {'status': 'failed'}
        if response.get('http_status') not in range(200, 300):
            error = body.get('error') if isinstance(body, dict) else None
            if response.get('http_status') == 400 and isinstance(error, dict) and error.get('code') in {
                    'league_component_regression', 'league_component_conflict'}:
                return {'status': 'rejected', 'reason': error['code']}
            return {'status': 'failed'}
        return body if isinstance(body, dict) else {'status': 'failed'}
    return send


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Daily league refresh; read-only unless all live gates are explicit.')
    parser.add_argument('--root', default='.')
    parser.add_argument('--now')
    parser.add_argument('--env', default='.env')
    parser.add_argument('--quota-path', default='data/cache/quota.json')
    parser.add_argument('--endpoint')
    parser.add_argument('--daily-credit-limit', type=int)
    for flag in ('live', 'write', 'publish'):
        parser.add_argument('--' + flag, action='store_true')
    args = parser.parse_args(argv)
    if args.now is not None and any((args.live, args.write, args.publish)):
        print(json.dumps({'status': 'blocked', 'reason': 'live_now_override_forbidden'}))
        return 2
    from worldcup.league_pre_match_runner import _load_env, validate_hmac_secret
    def loader():
        env = _load_env(Path(args.root) / args.env)
        try:
            validate_hmac_secret(env.get('INGEST_HMAC_SECRET'))
        except ValueError:
            raise ValueError('daily_publish_secret_invalid') from None
        return env
    result = run_league_daily(root=Path(args.root), now=args.now or datetime.now(timezone.utc).isoformat(),
        live=args.live, write=args.write, publish=args.publish, endpoint=args.endpoint,
        daily_credit_limit=args.daily_credit_limit, env_loader=loader, publish_fn=frozen_cli_publisher(loader),
        quota_path=Path(args.root) / args.quota_path)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
