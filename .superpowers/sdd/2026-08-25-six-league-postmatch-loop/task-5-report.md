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
