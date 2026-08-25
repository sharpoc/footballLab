# Task 5 Report: Dry-Run-First Live Runner

## Implementation

- Added `worldcup.league_postmatch_runner` with the requested `run_league_postmatch(...)` interface and `python3 -m worldcup.league_postmatch_runner` CLI.
- Default execution is read-only dry-run. Every partial live/write/notify flag combination exits before lock creation, provider access, local writes, or notification delivery. The CLI rejects `--live` combined with replay-only `--now`.
- Full provider access requires exact `live=True, write=True`, a formal six-league partition, an active acceptance row, and a local FotMob evidence payload whose verified fingerprint exactly matches the acceptance `result_contract` fingerprint. No `.env`, The Odds API score/quota path, provider fallback, or secret is read.
- Live execution uses the independent non-blocking `data/local/leagues/league_postmatch.lock`. Within the lock it checks the durable notification outbox first; pending notification retry exits without reading unrelated acceptance/state and without any provider call.
- Due events are built from immutable partition history plus Task 2 committed result receipts. Provider requests are grouped by formal competition/day, and only due event details are requested. Task 1 parser output is projected to the exact due set before Task 2 merge.
- Settlement order is results receipt commit, closing commit, postmatch commit, aggregate statistics commit, runner state commit, then optional notification. Task 3 receives only the Task 2 receipt reread and revalidated from `results.json`, never an in-memory parser payload.
- Atomic-stage failures have safe stage-specific reasons. A committed receipt is reconciled into missing closing/postmatch/statistics/state without a second provider call; a failed results write remains safely retryable with a later provider call. Per-league source/conflict failure does not block healthy formal partitions.
- State retains the exact aggregate transition so a crash after state commit but before notification intent persistence can reconstruct the same daily/threshold event. Task 4 fingerprints and durable sent receipts make that replay idempotent. Daily summaries use the Beijing calendar date.
- Runner output contains only safe statuses, counts, formal competition IDs, scope, and fingerprints. Raw calendar/detail/notifier mappings and exception text are never returned.

## TDD evidence

Initial RED:

```text
ModuleNotFoundError: No module named 'worldcup.league_postmatch_runner'
```

Subsequent RED cycles independently exposed and fixed:

- positional two-argument notifier adaptation and Beijing settlement date;
- stage-specific `postmatch/statistics/state` atomic failure reporting;
- Task 2 results and closing atomic recovery semantics;
- pending outbox retry being incorrectly blocked by unrelated malformed acceptance/state.

Focused GREEN:

```text
13/13 Task 5 runner tests passed
```

The focused suite uses only temporary files, injected FotMob fetchers, and injected notification senders. It performs no real network request, WxPusher send, environment read, quota use, or operational write.

## Full verification

```text
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
1429/1429 tests passed, 1 module(s) skipped
Skipped: test_fastapi_app.py (optional: fastapi)
```

Also passed:

```text
python3 -m py_compile worldcup/league_postmatch_runner.py tests/test_league_postmatch_runner.py
git diff --check
```

The production runner scan found no The Odds API/quota/env loader, timer installation, launchctl, raw provider projection, or secret-bearing output path. Test-only sentinel strings verify redaction and are never real credentials.

## Scope and remaining gates

- This task does not perform a real FotMob probe, `--live --write`, notification, LaunchAgent installation, push, PR, merge, or deployment.
- Live correctness still depends on Gate A proving that each accepted FotMob sample fingerprint came from a reviewed real saved sample and that its provider event identity matches the immutable pre-match snapshot/closing identity. The runner fails closed and does not invent an ID mapping or fallback.
- Local notification delivery remains at-least-once across the unavoidable crash window after the external sender succeeds but before Task 4 persists its sent receipt; deterministic event fingerprints are retained for downstream idempotency.
- The resulting statistics are research-only, do not constitute betting advice, and contain no stake, EV/Edge, or execution recommendation.

## Review fix round 1

### RED evidence

Added focused regressions for all five Important and two Minor findings before implementation. The old runner produced nine failures:

```text
1428/1437 tests passed, 1 module skipped
```

The failures covered saved-sample/registry acceptance binding, malformed dry-run inputs, exact due identity joins, mixed successful/failed detail fetches, durable notification transition recovery and cross-day consumption, truthful notification safety flags, and pre-write statistics regression validation.

### Corrections

- Live acceptance now requires `result_contract_evidence.json.sample_path` to resolve to a regular file strictly below this run root's `data/probe/` or `data/cache/` boundary. The runner hashes the actual saved bytes without decoding or projecting them and requires that SHA-256 to equal the FotMob evidence `source_reference`. Missing files, tampered bytes, absolute/out-of-bound paths, and symlink escapes fail before provider access. The acceptance `result_contract` fingerprint still binds the verified evidence payload.
- The current per-competition strict identity registry is deterministically projected as sorted provider-name/canonical pairs and hashed. The active acceptance row's `team_identity` fingerprint must equal that current registry fingerprint; a stale or substituted registry blocks the partition before provider access.
- Every accepted Task 1 parser result is joined to one exact due identity across `source_event_id`, `competition_id`, normalized `kickoff_at_utc`, `home_canonical`, and `away_canonical`. Extra, malformed, wrong-team, or wrong-kickoff accepted rows fail closed with `result_due_identity_mismatch`; Task 2 receives nothing and cannot create a receipt.
- Mixed detail outcomes retain successful accepted results while returning an explicit `partial` partition projection with `result_count`, `pending_count`, `source_error_count`, and sorted safe event ID/reason rows. A successful settlement can no longer hide another due event's detail failure or make the aggregate look fully settled.
- Candidate aggregate statistics and runner state are both built and checked before `postmatch_statistics.json` is touched. Formal scope, exact partition membership, empty exclusions, per-league/aggregate settled counts, decided counts, and finished-result counts must remain monotonic. A regression or excluded partition returns `statistics_validation_failed` while preserving both prior statistics and state bytes.
- Runner state schema v2 records the Beijing `notification_date` and `notification_transition_consumed`. A new aggregate is first committed as unconsumed; Task 4 then durably stores every daily/threshold intent (also when `--notify` is absent), after which a second atomic state commit consumes the transition. An unconsumed transition is recovered and exits before acceptance/provider work, so a later aggregate cannot overwrite it. A zero-due wake on a later Beijing day cannot rebuild the prior daily summary.
- Pending Task 4 intents still retry before acceptance/state/provider reads. `safety.notified` is true only for a confirmed `sent` status, not for `failed`, `pending`, `already_sent`, or attempted delivery. `safety.wrote` reflects confirmed artifact/outbox/state writes rather than an attempt flag.
- Dry-run now returns safe `blocked` metadata for malformed acceptance or history instead of silently projecting a normal empty plan; it remains network-, notification-, environment-, and write-free.

Task 4 delivery remains intentionally at-least-once in the external receipt window: if the sender accepts a message and the process crashes before the outbox atomically records `sent`, the durable pending intent is retried and the external service may receive a duplicate. Event fingerprints make that window explicit and enable downstream deduplication, but the runner does not claim exactly-once delivery.

### Final verification

```text
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
1437/1437 tests passed, 1 module(s) skipped
Skipped: test_fastapi_app.py (optional: fastapi)

python3 -m py_compile worldcup/league_postmatch_runner.py tests/test_league_postmatch_runner.py
git diff --check
```

All commands exited 0. A focused scan also found no The Odds API/quota access, env loader, `launchctl`, direct WxPusher command, credential field, or authorization/cookie path in the runner. This fix round performed no real probe, live/write run, notification, timer installation, push, PR, merge, or deployment.
