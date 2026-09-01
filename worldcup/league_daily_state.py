"""Durable daily request accounting; pure transitions and short atomic state locks.

Callers hold odds_execution_lock around live request/commit orchestration. State
locks are inner locks and never acquire provider/quota/publication locks.
"""
from __future__ import annotations

import copy
import fcntl
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


PHASE_GRAPH = {
    'reserved': {'fetched', 'blocked'}, 'fetched': {'committed', 'blocked'},
    'committed': {'pending', 'blocked'}, 'pending': {'published', 'blocked'},
    'published': set(), 'blocked': set(),
}
_CONTEXT = {'competition_id', 'acceptance_fingerprint', 'registry_fingerprint',
            'markets', 'expected_snapshot_id', 'request_at', 'events', 'anchor_metadata'}


def empty_daily_state() -> dict:
    return {'schema_version': 1, 'competitions': {}, 'attempts': {}, 'budgets': {}}


def _fail(code='daily_refresh_state_invalid'):
    raise ValueError(code)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        _fail()
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        _fail()
    return value


def _utc(value):
    _text(value)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _fail()
    if result.tzinfo is None or result.utcoffset() is None:
        _fail()
    return result.astimezone(timezone.utc)


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        _fail()
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail()


def _json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value: _json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values(): _json(item)
        return
    _fail()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        _fail()


def _signature(value, competition_id, event=None):
    parts = _text(value).split('|')
    if len(parts) not in (4, 6) or parts[0] != competition_id or not parts[1]:
        _fail()
    kickoff = _utc(parts[2])
    if parts[3] not in {'T-6h', 'T-90m', 'T-25m', 'EXPIRY'}:
        _fail()
    if (parts[3] == 'EXPIRY') != (len(parts) == 6):
        _fail()
    if len(parts) == 6:
        _utc(parts[4]); _text(parts[5])
    if event and (parts[1] != event['event_id'] or kickoff != _utc(event['kickoff_at_utc'])):
        _fail()


def _context(value):
    if not isinstance(value, dict) or set(value) != _CONTEXT:
        _fail()
    cid = value['competition_id']
    if cid not in FORMAL_SINGLE_MATCH_IDS:
        _fail()
    _hash(value['acceptance_fingerprint']); _hash(value['registry_fingerprint'])
    if not re.fullmatch(r'[A-Za-z0-9_-]+', _text(value['expected_snapshot_id'])):
        _fail()
    requested = _utc(value['request_at'])
    markets = value['markets']
    if not isinstance(markets, list) or not markets or any(m not in ('h2h', 'spreads', 'totals') for m in markets) or len(set(markets)) != len(markets):
        _fail()
    events = value['events']
    if not isinstance(events, list):
        _fail()
    by_id = {}
    for event in events:
        if not isinstance(event, dict) or set(event) != {'event_id', 'kickoff_at_utc', 'home_canonical', 'away_canonical'}:
            _fail()
        event_id = _text(event['event_id'])
        if event_id in by_id or _utc(event['kickoff_at_utc']) <= requested:
            _fail()
        if _text(event['home_canonical']) == _text(event['away_canonical']):
            _fail()
        by_id[event_id] = event
    metadata = value['anchor_metadata']
    if not isinstance(metadata, dict) or set(metadata) != set(by_id):
        _fail()
    for event_id, signatures in metadata.items():
        if not isinstance(signatures, list) or not signatures or len(set(signatures)) != len(signatures):
            _fail()
        for signature in signatures:
            _signature(signature, cid, by_id[event_id])


def _snapshot(attempt, snapshot):
    context = attempt.get('context')
    if context is None or not isinstance(snapshot, dict):
        _fail('daily_recovery_evidence_invalid')
    if 'empty_discovery' in attempt.get('evidence', {}).get('fetched', {}):
        _fail('daily_recovery_evidence_invalid')
    _json(snapshot)
    competition = snapshot.get('competition')
    if not isinstance(competition, dict) or competition.get('id') != context['competition_id'] or snapshot.get('snapshot_id') != context['expected_snapshot_id']:
        _fail('daily_recovery_evidence_invalid')
    snapshot_at = _utc(snapshot.get('snapshot_at'))
    if snapshot_at < _utc(context['request_at']):
        _fail('daily_recovery_evidence_invalid')
    rows = snapshot.get('matches')
    if not isinstance(rows, list) or not rows: _fail('daily_recovery_evidence_invalid')
    by_id = {}
    for row in rows:
        if not isinstance(row, dict): _fail('daily_recovery_evidence_invalid')
        event_id = _text(row.get('source_event_id'))
        if event_id in by_id: _fail('daily_recovery_evidence_invalid')
        if _text(row.get('home_canonical')) == _text(row.get('away_canonical')) or _utc(row.get('kickoff_at_utc')) <= snapshot_at:
            _fail('daily_recovery_evidence_invalid')
        by_id[event_id] = row
    for event in context['events']:
        row = by_id.get(event['event_id'])
        if row is None:
            continue  # Provider omission is coverage, not a cancellation.
        if _utc(row.get('kickoff_at_utc')) != _utc(event['kickoff_at_utc']) or row.get('home_canonical') != event['home_canonical'] or row.get('away_canonical') != event['away_canonical']:
            _fail('daily_recovery_evidence_invalid')
    previous = attempt.get('evidence', {}).get('fetched', {}).get('snapshot')
    if previous is not None and previous != snapshot:
        _fail('daily_recovery_evidence_invalid')


def _evidence(attempt, phase, evidence):
    if not isinstance(evidence, dict): _fail('daily_recovery_evidence_invalid')
    if phase == 'blocked':
        if set(evidence) != {'error_code'} or not re.fullmatch(r'[a-z][a-z0-9_]{0,99}', _text(evidence['error_code'])):
            _fail('daily_recovery_evidence_invalid')
    elif phase == 'fetched':
        if set(evidence) == {'empty_discovery'}:
            context = attempt.get('context', {})
            empty = evidence['empty_discovery']
            if not isinstance(empty, dict) or set(empty) != {'competition_id', 'observed_at', 'event_ids'} or empty['event_ids'] != [] or context.get('events') != [] or empty['competition_id'] != context.get('competition_id'):
                _fail('daily_recovery_evidence_invalid')
            if _utc(empty['observed_at']) < _utc(context['request_at']):
                _fail('daily_recovery_evidence_invalid')
        else:
            if set(evidence) != {'snapshot'}: _fail('daily_recovery_evidence_invalid')
            _snapshot(attempt, evidence['snapshot'])
    elif phase == 'committed':
        if set(evidence) != {'history', 'current'} or evidence['history'] != evidence['current']:
            _fail('daily_recovery_evidence_invalid')
        _snapshot(attempt, evidence['history'])
    elif phase in ('pending', 'published'):
        keys = {'endpoint', 'body_sha256', 'snapshot'} | ({'status'} if phase == 'published' else set())
        if set(evidence) != keys or not _text(evidence['endpoint']).startswith('https://'):
            _fail('daily_recovery_evidence_invalid')
        _hash(evidence['body_sha256']); _snapshot(attempt, evidence['snapshot'])
        if phase == 'published':
            pending = attempt.get('evidence', {}).get('pending')
            if evidence['status'] not in {'stored', 'duplicate'} or pending is None or any(evidence[k] != pending[k] for k in pending):
                _fail('daily_recovery_evidence_invalid')


def validate_daily_state(state: dict) -> None:
    """Reject malformed persisted inputs with a safe, stable ValueError."""
    try:
        _validate_daily_state(state)
    except (TypeError, KeyError, OverflowError, RecursionError):
        _fail()


def _validate_daily_state(state: dict) -> None:
    _json(state)
    if not isinstance(state, dict) or set(state) != {'schema_version', 'competitions', 'attempts', 'budgets'} or type(state['schema_version']) is not int or state['schema_version'] != 1:
        _fail()
    if any(not isinstance(state[k], dict) for k in ('competitions', 'attempts', 'budgets')):
        _fail()
    for cid, row in state['competitions'].items():
        if cid not in FORMAL_SINGLE_MATCH_IDS or not isinstance(row, dict) or set(row) - {'successful_anchors', 'last_attempt_at', 'last_success_at', 'next_discovery_at', 'last_attempt_signatures'}:
            _fail()
        signatures = row.get('successful_anchors', [])
        if not isinstance(signatures, list): _fail()
        for signature in signatures: _signature(signature, cid)
        for key in ('last_attempt_at', 'last_success_at', 'next_discovery_at'):
            if key in row: _utc(row[key])
        attempts = row.get('last_attempt_signatures', {})
        if not isinstance(attempts, dict): _fail()
        for signature, attempted_at in attempts.items():
            _signature(signature, cid); _utc(attempted_at)
    covered = set()
    for day, budget in state['budgets'].items():
        _day(day)
        if not isinstance(budget, dict) or set(budget) != {'limit', 'reserved_credits', 'reservations'}:
            _fail()
        _integer(budget['limit'], 1); _integer(budget['reserved_credits'])
        if not isinstance(budget['reservations'], dict): _fail()
        total = 0
        for attempt_id, reservation in budget['reservations'].items():
            _text(attempt_id)
            if attempt_id in covered or not isinstance(reservation, dict) or set(reservation) != {'estimated', 'actual_cost', 'status'}:
                _fail()
            covered.add(attempt_id)
            _integer(reservation['estimated'], 1)
            status, actual = reservation['status'], reservation['actual_cost']
            if status not in {'reserved', 'unknown', 'settled', 'released'}: _fail()
            if status == 'settled': total += _integer(actual)
            elif status == 'released':
                if actual != 0: _fail()
            else:
                if actual is not None: _fail()
                total += reservation['estimated']
            attempt = state['attempts'].get(attempt_id)
            if not isinstance(attempt, dict) or attempt.get('date_bj') != day or attempt.get('estimated') != reservation['estimated']:
                _fail()
        if budget['reserved_credits'] != total: _fail()
    if covered != set(state['attempts']): _fail()
    for attempt in state['attempts'].values():
        if set(attempt) - {'date_bj', 'estimated', 'phase', 'context', 'evidence'} or attempt.get('phase') not in PHASE_GRAPH:
            _fail()
        phase = attempt['phase']; evidence = attempt.get('evidence', {})
        if not isinstance(evidence, dict) or any(k not in PHASE_GRAPH or k == 'reserved' for k in evidence): _fail()
        if 'context' in attempt:
            _context(attempt['context'])
            request_at = _utc(attempt['context']['request_at'])
            if (request_at + timedelta(hours=8)).date().isoformat() != attempt['date_bj']: _fail()
        elif phase != 'reserved': _fail()
        expected = [] if phase in {'reserved', 'blocked'} else ['fetched', 'committed', 'pending', 'published'][:['fetched', 'committed', 'pending', 'published'].index(phase) + 1]
        if any(k not in evidence for k in expected) or (phase == 'reserved' and evidence): _fail()
        if phase != 'blocked' and set(evidence) != set(expected): _fail()
        if phase == 'blocked' and 'blocked' not in evidence: _fail()
        for evidence_phase, value in evidence.items(): _evidence(attempt, evidence_phase, value)


def reserve_credits(state: dict, *, date_bj: str, attempt_id: str, estimated: int, limit: int) -> dict:
    validate_daily_state(state); _day(date_bj); _text(attempt_id)
    _integer(estimated, 1); _integer(limit, 1)
    result = copy.deepcopy(state)
    if attempt_id in result['attempts']:
        old = result['attempts'][attempt_id]
        if old['date_bj'] != date_bj or old['estimated'] != estimated or result['budgets'][date_bj]['limit'] != limit:
            _fail('daily_attempt_conflict')
        return result
    budget = result['budgets'].setdefault(date_bj, {'limit': limit, 'reserved_credits': 0, 'reservations': {}})
    if budget['limit'] != limit: _fail('daily_budget_limit_conflict')
    if budget['reserved_credits'] + estimated > limit: _fail('daily_budget_exhausted')
    budget['reservations'][attempt_id] = {'estimated': estimated, 'actual_cost': None, 'status': 'reserved'}
    budget['reserved_credits'] += estimated
    result['attempts'][attempt_id] = {'date_bj': date_bj, 'estimated': estimated, 'phase': 'reserved'}
    return result


def reserve_attempt(state: dict, *, context: dict, **kwargs) -> dict:
    _json(context); _context(context)
    result = reserve_credits(state, **kwargs)
    attempt = result['attempts'][kwargs['attempt_id']]
    if 'context' in attempt and attempt['context'] != context: _fail('daily_attempt_conflict')
    if 'context' in attempt:
        return result  # Idempotent reserve must not undo a successful cooldown.
    attempt['context'] = copy.deepcopy(context)
    row = result['competitions'].setdefault(context['competition_id'], {'successful_anchors': []})
    when = _utc(context['request_at']).isoformat()
    row['last_attempt_at'] = max(row.get('last_attempt_at', when), when, key=_utc)
    if not context['events']:
        row['next_discovery_at'] = (_utc(when) + timedelta(minutes=30)).isoformat()
    signatures = row.setdefault('last_attempt_signatures', {})
    for values in context['anchor_metadata'].values():
        for signature in values:
            signatures[signature] = max(signatures.get(signature, when), when, key=_utc)
    validate_daily_state(result)
    return result


def _recount(state):
    for budget in state['budgets'].values():
        budget['reserved_credits'] = sum(r['actual_cost'] if r['status'] in {'settled', 'released'} else r['estimated'] for r in budget['reservations'].values())


def settle_credits(state: dict, *, attempt_id: str, actual_cost: int | None) -> dict:
    validate_daily_state(state)
    if actual_cost is not None: _integer(actual_cost)
    result = copy.deepcopy(state)
    if attempt_id not in result['attempts']: _fail('daily_attempt_missing')
    reservation = result['budgets'][result['attempts'][attempt_id]['date_bj']]['reservations'][attempt_id]
    if reservation['status'] == 'released': _fail('daily_reservation_conflict')
    if reservation['status'] == 'settled':
        if reservation['actual_cost'] != actual_cost: _fail('daily_reservation_conflict')
        return result
    reservation.update(status='unknown' if actual_cost is None else 'settled', actual_cost=actual_cost)
    _recount(result)
    return result


def release_credits(state: dict, *, attempt_id: str, request_sent: bool) -> dict:
    validate_daily_state(state)
    if request_sent is not False: _fail('daily_request_may_have_been_sent')
    result = copy.deepcopy(state)
    if attempt_id not in result['attempts']: _fail('daily_attempt_missing')
    attempt = result['attempts'][attempt_id]
    reservation = result['budgets'][attempt['date_bj']]['reservations'][attempt_id]
    if reservation['status'] not in {'reserved', 'released'} or attempt['phase'] != 'reserved':
        _fail('daily_request_may_have_been_sent')
    reservation.update(status='released', actual_cost=0)
    _recount(result)
    return result


def advance_attempt(state: dict, *, attempt_id: str, phase: str, evidence: dict) -> dict:
    validate_daily_state(state); _json(evidence)
    result = copy.deepcopy(state)
    if attempt_id not in result['attempts']: _fail('daily_attempt_missing')
    attempt = result['attempts'][attempt_id]
    reservation = result['budgets'][attempt['date_bj']]['reservations'][attempt_id]
    if reservation['status'] == 'released': _fail('daily_request_not_reserved')
    if phase == attempt['phase']:
        if attempt.get('evidence', {}).get(phase) != evidence: _fail('daily_attempt_conflict')
        return result
    if phase not in PHASE_GRAPH[attempt['phase']] or 'context' not in attempt: _fail('daily_phase_regression')
    _evidence(attempt, phase, evidence)
    attempt['phase'] = phase
    attempt.setdefault('evidence', {})[phase] = copy.deepcopy(evidence)
    if phase == 'fetched' and 'empty_discovery' in evidence:
        # A proven empty discovery succeeded locally but has NOTHING to publish.
        context = attempt['context']; row = result['competitions'][context['competition_id']]
        when = evidence['empty_discovery']['observed_at']
        row['last_success_at'] = max(row.get('last_success_at', when), when, key=_utc)
        row['next_discovery_at'] = (_utc(when) + timedelta(hours=24)).isoformat()
    if phase == 'published':
        context = attempt['context']; row = result['competitions'][context['competition_id']]
        when = evidence['snapshot']['snapshot_at']
        row['last_success_at'] = max(row.get('last_success_at', when), when, key=_utc)
        fetched_ids = {match['source_event_id'] for match in evidence['snapshot']['matches']}
        row['successful_anchors'] = sorted(set(row.get('successful_anchors', [])) | {s for event_id, values in context['anchor_metadata'].items() if event_id in fetched_ids for s in values})
        if not context['events']:
            row['next_discovery_at'] = (_utc(when) + timedelta(hours=24)).isoformat()
    validate_daily_state(result)
    return result


def recovery_action(attempt: dict, *, history: dict | None = None, current: dict | None = None, published_receipt: dict | None = None) -> str:
    """Classify verified artifacts, never infer a publication from snapshot files.

    Artifact reads and replaying each durable transition belong to the runner.
    A reserved attempt with no evidence is UNKNOWN, including a crash before send.
    """
    if not isinstance(attempt, dict): _fail()
    # Standalone callers receive the same full evidence validation as store.read;
    # a forged phase name by itself is never treated as a durable receipt.
    day = attempt.get('date_bj'); estimated = attempt.get('estimated')
    _day(day); _integer(estimated, 1)
    check = empty_daily_state()
    check['attempts']['recovery'] = attempt
    check['budgets'][day] = {'limit': estimated, 'reserved_credits': estimated,
                             'reservations': {'recovery': {'estimated': estimated, 'actual_cost': None, 'status': 'reserved'}}}
    validate_daily_state(check)
    if attempt['phase'] == 'blocked':
        return 'blocked'
    if 'empty_discovery' in attempt.get('evidence', {}).get('fetched', {}):
        if any(value is not None for value in (history, current, published_receipt)):
            _fail('daily_recovery_evidence_invalid')
        return 'blocked' if attempt['phase'] == 'blocked' else 'discovery_complete'
    if published_receipt is not None:
        _evidence(attempt, 'published', published_receipt)
        return 'published'
    for snapshot in (history, current):
        if snapshot is not None: _snapshot(attempt, snapshot)
    if attempt['phase'] == 'blocked': return 'blocked'
    if attempt['phase'] == 'published': return 'published'
    if attempt['phase'] == 'pending': return 'retry_publish'
    if history is not None and current is not None:
        if history != current: _fail('daily_recovery_evidence_invalid')
        return 'prepare_publish'
    if history is not None or attempt['phase'] == 'fetched': return 'retry_commit'
    if attempt['phase'] == 'committed': return 'prepare_publish'
    return 'unknown_request'


def _path_safe(path):
    for part in (path, *path.parents):
        if part.is_symlink(): _fail('daily_state_symlink')


@contextmanager
def _lock(path, nonblocking=False):
    _path_safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _path_safe(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        except BlockingIOError:
            _fail('odds_execution_busy')
        yield
    finally:
        os.close(fd)


@contextmanager
def odds_execution_lock(root: Path):
    """Live-only shared nonblocking lock. Lock order: runner -> odds -> short locks."""
    with _lock(Path(root) / 'data/local/leagues/odds_execution.lock', nonblocking=True):
        yield


def _merge(old, incoming):
    result = copy.deepcopy(old)
    for day, budget in incoming['budgets'].items():
        existing = result['budgets'].setdefault(day, {'limit': budget['limit'], 'reserved_credits': 0, 'reservations': {}})
        if existing['limit'] != budget['limit']: _fail('daily_budget_limit_conflict')
        added = 0
        for aid, reservation in budget['reservations'].items():
            previous = existing['reservations'].get(aid)
            if previous is not None:
                if previous['estimated'] != reservation['estimated']: _fail('daily_attempt_conflict')
                if previous != reservation and (previous['status'] in {'settled', 'released'} or (previous['status'] == 'unknown' and reservation['status'] not in {'unknown', 'settled'})):
                    _fail('daily_reservation_regression')
            else:
                added += reservation['estimated']
            existing['reservations'][aid] = copy.deepcopy(reservation)
        # Compare the old charged total plus NEW reservations before settlements;
        # conservative on stale writers and does not lose unknown charges.
        if added and existing['reserved_credits'] + added > existing['limit']:
            _fail('daily_budget_exhausted')
    for aid, attempt in incoming['attempts'].items():
        previous = result['attempts'].get(aid)
        if previous is not None:
            if any(previous.get(k) != attempt.get(k) for k in ('date_bj', 'estimated')) or ('context' in previous and previous['context'] != attempt.get('context')):
                _fail('daily_attempt_conflict')
            phase = previous['phase']
            reachable = {phase}; frontier = [phase]
            while frontier:
                for nxt in PHASE_GRAPH[frontier.pop()]:
                    if nxt not in reachable: reachable.add(nxt); frontier.append(nxt)
            if attempt['phase'] not in reachable: _fail('daily_phase_regression')
            if any(attempt.get('evidence', {}).get(k) != v for k, v in previous.get('evidence', {}).items()):
                _fail('daily_attempt_conflict')
        result['attempts'][aid] = copy.deepcopy(attempt)
    for cid, row in incoming['competitions'].items():
        existing = result['competitions'].setdefault(cid, {})
        existing['successful_anchors'] = sorted(set(existing.get('successful_anchors', [])) | set(row.get('successful_anchors', [])))
        for key in ('last_attempt_at', 'last_success_at', 'next_discovery_at'):
            if key in row: existing[key] = max(existing.get(key, row[key]), row[key], key=_utc)
        signatures = existing.setdefault('last_attempt_signatures', {})
        for signature, when in row.get('last_attempt_signatures', {}).items():
            signatures[signature] = max(signatures.get(signature, when), when, key=_utc)
    _recount(result); validate_daily_state(result)
    return result


class DailyStateStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / 'data/local/leagues/daily_refresh_state.json'
        self.lock_path = self.path.with_name('.daily_refresh_state.lock')

    def read(self) -> dict:
        _path_safe(self.path)
        if not self.path.exists(): return empty_daily_state()
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result: _fail()
                    result[key] = value
                return result
            with self.path.open(encoding='utf-8') as stream:
                result = json.load(stream, object_pairs_hook=pairs)
        except (OSError, UnicodeError, json.JSONDecodeError):
            _fail('daily_refresh_state_invalid')
        validate_daily_state(result)
        return result

    def _write(self, state):
        _path_safe(self.path)
        fd, temporary = tempfile.mkstemp(prefix='.daily-refresh-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            _path_safe(self.path)
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def commit(self, state: dict) -> None:
        validate_daily_state(state)
        _path_safe(self.path)
        with _lock(self.lock_path):
            merged = _merge(self.read(), state)
            self._write(merged)

    def update(self, transform) -> dict:
        """Transform a fresh state inside its lock; no external I/O in callback."""
        _path_safe(self.path)
        with _lock(self.lock_path):
            old = self.read(); incoming = transform(copy.deepcopy(old))
            validate_daily_state(incoming)
            merged = _merge(old, incoming)
            self._write(merged)
            return merged

    def reserve(self, *, context=None, **kwargs) -> dict:
        return self.update(lambda state: reserve_credits(state, **kwargs) if context is None else reserve_attempt(state, context=context, **kwargs))

    def settle(self, **kwargs) -> dict:
        return self.update(lambda state: settle_credits(state, **kwargs))

    def release(self, **kwargs) -> dict:
        return self.update(lambda state: release_credits(state, **kwargs))

    def advance(self, **kwargs) -> dict:
        return self.update(lambda state: advance_attempt(state, **kwargs))
