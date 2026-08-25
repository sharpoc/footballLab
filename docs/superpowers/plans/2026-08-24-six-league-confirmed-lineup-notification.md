# Six-League Confirmed Lineup Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed six-league FotMob confirmed-lineup pipeline that polls only near kickoff, refreshes post-lineup odds under quota guard, republishes the aggregate recommendation snapshot, and sends idempotent WxPusher notifications.

**Architecture:** Keep lineup collection separate from the World Cup pipeline. A pure planner reads committed active-league fixtures and persisted state; a strict FotMob collector/parser produces only confirmed 11+11 observations; an atomic store commits immutable accepted lineups; the pre-match orchestrator groups post-lineup refreshes per competition, invokes the existing league refresh/publish boundary, and commits notification/outbox state only in the correct order.

**Tech Stack:** Python 3.12 standard library, existing league competition/identity/snapshot modules, The Odds API quota ledger, HMAC ingest, WxPusher helper, macOS LaunchAgent, repository test runner.

**Spec:** `docs/superpowers/specs/2026-08-24-six-league-confirmed-lineup-notification-design.md`

## Global Constraints

- FotMob is a free unofficial source with no SLA; schema ambiguity must fail closed.
- Only explicit `confirmed` lineups with exactly 11 home and 11 away starters may be accepted.
- `predicted`, `probable`, `expected`, `unknown`, incomplete, post-kickoff, ambiguous-identity, or duplicate observations must never alter recommendations.
- Confirmed lineups trigger a fresh market-odds MatchPick v3 run; they do not add player weights or change model semantics.
- Only formal `active` league acceptance rows participate; Bundesliga remains excluded until separately activated.
- No fixture inside 90 minutes means zero FotMob requests. Poll at most every 15 minutes in T-90..T-45 and every 5 minutes in T-45..T-0.
- The Odds API refresh requires a usable quota slot and is coalesced to one request per competition/sport key per run.
- Public publication remains one aggregate snapshot built only from committed active-league partitions.
- Dry-run must not load env, access network, write files, consume quota, publish, or notify.
- Do not store raw FotMob responses, Cookie, authorization headers, API keys, HMAC secrets, WxPusher token/UID, or request headers.
- Push, merge, deployment, live provider probe, quota consumption, LaunchAgent installation/loading, and real notification each require their own confirmation.

---

### Task 1: Pure lineup polling planner

**Files:**
- Create: `worldcup/league_lineup_planner.py`
- Create: `tests/test_league_lineup_planner.py`

**Interfaces:**
- Consumes: timezone-aware `now`, committed fixture rows, formal acceptance report, persisted per-event poll/confirmed state.
- Produces: `plan_league_lineup_poll(*, now, fixtures_by_competition, acceptance_report, state) -> dict` with `requests`, `skipped`, `next_due_at`, and safe counts.

- [ ] **Step 1: Write failing zero-request and cadence tests**

Cover no fixtures, fixtures outside 90 minutes, T-90..T-45 15-minute throttling, T-45..T-0 5-minute throttling, confirmed/postponed/cancelled/started exclusion, non-active acceptance exclusion, naive datetime rejection, and restart-stable state.

- [ ] **Step 2: Run the focused test file and verify RED**

Run:

    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "import tests.test_league_lineup_planner as m; [getattr(m,n)() for n in dir(m) if n.startswith('test_')]"

Expected: import or missing-function failure, followed by assertion failures only as individual behaviors are added.

- [ ] **Step 3: Implement the minimal pure planner**

Use timezone-aware UTC comparisons, deterministic competition/event sorting, and explicit reason counters. Do not import networking, env, quota writers, notifications, or stores.

- [ ] **Step 4: Run focused tests and full regression**

Run the focused command above, then:

    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py

- [ ] **Step 5: Commit**

    git add worldcup/league_lineup_planner.py tests/test_league_lineup_planner.py
    git commit -m "feat: plan six-league lineup polling"

---

### Task 2: Strict FotMob calendar/details parser and league identity join

**Files:**
- Create: `worldcup/collectors/league_fotmob_lineups.py`
- Create: `worldcup/sources/league_fotmob_lineups.py`
- Create: `tests/collectors/test_league_fotmob_lineups.py`
- Create: `tests/sources/test_league_fotmob_lineups_source.py`
- Modify: `worldcup/league_team_identity.py` only if saved fixtures expose an already-approved explicit alias gap.

**Interfaces:**
- Consumes: saved calendar/details payloads, `competition_id`, local fixture rows, `LeagueTeamIdentityRegistry`, `fetched_at`.
- Produces: `parse_confirmed_fotmob_lineups(...) -> {"accepted": [...], "rejected": [...]}` and injectable `fetch_fotmob_calendar/date` / `fetch_fotmob_details/match_id` transports.

- [ ] **Step 1: Add saved test fixtures**

Store synthetic/minimal fixtures under `tests/fixtures/fotmob_lineups/`, not ignored live probe paths. Include confirmed 11+11, predicted 11+11, unknown 11+11, incomplete, swapped home/away, unknown club, duplicate candidate, kickoff mismatch, and post-kickoff cases.

- [ ] **Step 2: Write failing parser and source tests**

Assert exact rejection reason codes, 5-minute kickoff tolerance, canonical home/away join, no slug fallback, safe output fields, deterministic lineup fingerprint, calendar/date URL construction, details/match URL construction, and transport injection.

- [ ] **Step 3: Run focused tests and verify RED**

Run both modules through the project Python runtime; failure must be due to missing parser/source behavior, not missing fixtures.

- [ ] **Step 4: Implement parser and source boundary**

Reuse only safe parsing concepts from `worldcup.lineup_source_probe`; do not route production through the diagnostic probe. Keep the default transport private and never return headers or raw responses.

- [ ] **Step 5: Verify focused tests, py_compile, and regression**

    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/collectors/league_fotmob_lineups.py worldcup/sources/league_fotmob_lineups.py
    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py

- [ ] **Step 6: Commit**

    git add worldcup/collectors/league_fotmob_lineups.py worldcup/sources/league_fotmob_lineups.py tests/collectors/test_league_fotmob_lineups.py tests/sources/test_league_fotmob_lineups_source.py tests/fixtures/fotmob_lineups
    git commit -m "feat: parse confirmed FotMob league lineups"

---

### Task 3: Atomic non-degrading lineup cache and poll state

**Files:**
- Create: `worldcup/league_lineup_store.py`
- Create: `tests/test_league_lineup_store.py`

**Interfaces:**
- Produces: `LeagueLineupStore(root).read_competition(id)`, `commit_confirmed(id, report)`, `read_state()`, and `commit_state(state)`.
- Paths: `data/cache/leagues/lineups/<competition_id>.json` and `data/local/leagues/lineup_state.json`.

- [ ] **Step 1: Write failing store contract tests**

Cover path isolation, schema validation, formal competition IDs, atomic replace, lock behavior, unchanged idempotency, confirmed non-degradation, conflicting fingerprint rejection, state-after-cache ordering, malformed JSON fail-closed, and no raw/header/secret fields.

- [ ] **Step 2: Verify RED**

Run only `tests/test_league_lineup_store.py` functions with the project runtime.

- [ ] **Step 3: Implement the minimal locked atomic store**

Follow `LeagueLiveStore`/`LeagueAcceptanceStore` patterns: same-directory temporary file, fsync, os.replace, directory fsync, and fcntl lock. Never silently recover malformed committed state.

- [ ] **Step 4: Verify focused and full tests**

Run focused tests, `py_compile`, `git diff --check`, then the full test runner.

- [ ] **Step 5: Commit**

    git add worldcup/league_lineup_store.py tests/test_league_lineup_store.py
    git commit -m "feat: persist confirmed league lineups"

---

### Task 4: League lineup refresh runner with zero-side-effect dry-run

**Files:**
- Create: `worldcup/league_lineups_refresh.py`
- Create: `tests/test_league_lineups_refresh.py`

**Interfaces:**
- Consumes the Task 1 plan, Task 2 source/parser, Task 3 store, formal acceptance, and committed fixtures.
- Produces: `run_league_lineups_refresh(*, root, now, live=False, write=False, ...) -> dict` with safe counts, `newly_confirmed` grouped by competition, rejection reasons, and next due time.

- [ ] **Step 1: Write failing orchestration tests**

Prove dry-run never invokes env/network/write/notify dependencies; no-due live mode makes zero transport calls; one calendar call can serve multiple candidates; only due details are fetched; per-match source failure is isolated; cache failure prevents state commit; repeated confirmed fingerprint returns zero newly confirmed.

- [ ] **Step 2: Verify RED**

Run the focused module and confirm assertions fail for missing orchestration.

- [ ] **Step 3: Implement runner with injected transports**

Default CLI remains dry-run. Live requires both `live=True` and `write=True`. Do not read .env because FotMob needs no key. Emit only safe provider/error summaries.

- [ ] **Step 4: Verify focused tests and complete regression**

Also snapshot/hash the temp root before and after dry-run to prove zero writes.

- [ ] **Step 5: Commit**

    git add worldcup/league_lineups_refresh.py tests/test_league_lineups_refresh.py
    git commit -m "feat: refresh confirmed league lineups"

---

### Task 5: Post-lineup quota guard and coalesced league refresh

**Files:**
- Create: `worldcup/league_post_lineup_refresh.py`
- Create: `tests/test_league_post_lineup_refresh.py`
- Modify: `worldcup/league_batch_runner.py`
- Modify: `worldcup/league_scheduled_publish.py`
- Modify: `tests/test_league_batch_runner.py`
- Modify: `tests/test_league_scheduled_publish.py`

**Interfaces:**
- Consumes newly confirmed rows grouped by competition, quota ledger, accepted identity registry, formal acceptance, existing league refresh/publish functions.
- Produces: `run_post_lineup_refresh(..., live=False) -> dict` and a planned-refresh adapter that returns committed snapshot receipts.

- [ ] **Step 1: Write failing guard/coalescing tests**

Cover quota unknown, below minimum, exhausted, available next key, one competition with several matches causing one sport-key fetch, two competitions causing two fetches, started match exclusion, same fingerprint no second fetch, refresh failure isolation, and no state commit before committed snapshot receipt.

- [ ] **Step 2: Write failing real aggregate/ingest contract tests**

Require refreshed snapshots to be re-read from `data/cache/leagues/<id>/snapshot.json`, snapshot ID to match the commit receipt, all active caches to exist, aggregate to include `run.run_id`, and `build_ingest_request` to accept it. A missing partition must preserve the previous public aggregate.

- [ ] **Step 3: Verify RED**

Run the four focused test modules and confirm failures match missing planned live adapter/guard behavior.

- [ ] **Step 4: Implement the minimal planned adapter**

Fetch only competitions present in the post-lineup plan; reuse five-slot key selection and quota ledger updates; commit through `LeagueLiveStore`; then call the existing single-aggregate publication path. Do not add player weights.

- [ ] **Step 5: Verify focused, full, py_compile, diff, and sensitive scan**

The scan must reject api_key values, Authorization/Cookie, HMAC secret values, raw bookmaker rows, and WxPusher identifiers in new tracked files.

- [ ] **Step 6: Commit**

    git add worldcup/league_post_lineup_refresh.py worldcup/league_batch_runner.py worldcup/league_scheduled_publish.py tests/test_league_post_lineup_refresh.py tests/test_league_batch_runner.py tests/test_league_scheduled_publish.py
    git commit -m "feat: refresh league picks after confirmed lineups"

---

### Task 6: Idempotent lineup notification outbox

**Files:**
- Create: `worldcup/league_lineup_notifications.py`
- Create: `tests/test_league_lineup_notifications.py`
- Reuse: `worldcup/notifications.py` and global `send_wxpusher_notification` boundary.

**Interfaces:**
- Produces safe notification event builders plus `LeagueLineupNotificationOutbox` under `data/local/leagues/lineup_notification_state.json`.
- Events: published refresh changed, published refresh unchanged, missing at T-20, quota blocked, sustained source failure, source recovery.

- [ ] **Step 1: Write failing message and idempotency tests**

Assert Chinese message content, Beijing kickoff time, old-to-new pick, safe probability/reference odds, unchanged wording, disclaimer, no amount/EV/Edge/legacy fields, one send per fingerprint, failed send retained in outbox, successful receipt committed, and publish failure never creates success event.

- [ ] **Step 2: Verify RED**

Run the focused notification test module.

- [ ] **Step 3: Implement event builder and atomic outbox**

Use at-least-once outbox semantics. State may mark sent only after the injected notify function returns success. Never store notification provider response bodies or credentials.

- [ ] **Step 4: Verify focused/full tests and sensitive scan**

Use exploding stubs to prove dry-run and skipped paths do not call WxPusher.

- [ ] **Step 5: Commit**

    git add worldcup/league_lineup_notifications.py tests/test_league_lineup_notifications.py
    git commit -m "feat: notify confirmed league lineup updates"

---

### Task 7: Single pre-match orchestrator and independent LaunchAgent

**Files:**
- Create: `worldcup/league_pre_match_runner.py`
- Create: `worldcup/league_pre_match_launch_agent.py`
- Create: `tests/test_league_pre_match_runner.py`
- Create: `tests/test_league_pre_match_launch_agent.py`
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

**Interfaces:**
- Runner order: pending publish retry -> local plan -> lineup refresh -> atomic lineup commit -> quota guard -> post-lineup odds refresh -> committed aggregate publish -> notification outbox/send -> state commit.
- LaunchAgent label: `xin.celab.football.league-pre-match`; interval: 300 seconds.

- [ ] **Step 1: Write failing orchestrator-order and lock tests**

Cover default dry-run exploding dependencies, no-fixture zero network, single-instance lock contention, pending-first without provider refresh, lineup write failure stop, quota block notification, publish failure no success notification, notify failure outbox, and successful state ordering.

- [ ] **Step 2: Write failing plist tests**

Assert the independent label, exact project/runtime paths, StartInterval 300, logs, no RunAtLoad by default, quota guard in full-live mode, no secret values/environment block, and no modification of the World Cup label.

- [ ] **Step 3: Verify RED**

Run the two focused test modules.

- [ ] **Step 4: Implement CLI and plist generator**

Default CLI exposes no side effects. Require explicit layered flags: `--live-lineups --write-lineups --refresh-after-lineups --live-refresh --refresh-guard --publish --notify`. Generator prints JSON unless an output path is explicitly supplied and never invokes launchctl.

- [ ] **Step 5: Update architecture/operations documentation**

Document exact dry-run, observation-mode plist generation, full-live command, file paths, request cadence, quota risks, notification behavior, rollback, and the fact that no timer is installed by code generation.

- [ ] **Step 6: Verify the complete repository**

    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
    /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/league_pre_match_runner.py worldcup/league_pre_match_launch_agent.py
    git diff --check

Run a real local CLI dry-run with before/after hashes of `data/cache/leagues` and `data/local/leagues`; hashes and mtimes must remain unchanged.

- [ ] **Step 7: Request independent code review and fix all Critical/Important findings**

Review fail-closed identity, request cadence, quota coalescing, state ordering, aggregate compatibility, notification storm prevention, and timer isolation. Re-run full verification after fixes.

- [ ] **Step 8: Commit**

    git add worldcup/league_pre_match_runner.py worldcup/league_pre_match_launch_agent.py tests/test_league_pre_match_runner.py tests/test_league_pre_match_launch_agent.py README.md RECENT_WORK.md
    git commit -m "feat: orchestrate six-league pre-match lineups"

---

### Task 8: Confirmed live probe, push/merge, deploy, and timer activation gates

**Files/State:**
- Ignored probe target: `data/probe/leagues/lineups/fotmob/`
- Git branch: new `codex/...` PR branch; never force-push main.
- ECS release/current and local `~/Library/LaunchAgents/xin.celab.football.league-pre-match.plist`.

- [ ] **Step 1: Produce a zero-network probe plan**

List candidate active matches inside 90 minutes, expected calendar/details request counts, and whether no-match means zero requests. Do not read .env.

- [ ] **Step 2: Stop for explicit live FotMob probe confirmation**

After confirmation, perform the smallest request set. Save only a redacted fixture/fingerprint; verify schema/status and immediately stop on ambiguity, rate limiting, terms/access failure, or identity mismatch.

- [ ] **Step 3: Re-run offline parser tests against the saved redacted fixture**

Do not loosen the parser merely to accept the sample. If the contract differs, stop and revise the design with user approval.

- [ ] **Step 4: Stop for local commit and push confirmation**

Create a non-main `codex/` branch pointing at the reviewed commits, push it, open a PR, and wait for CI. Do not push main or force-push.

- [ ] **Step 5: Stop for merge confirmation**

Only squash merge the CI-green PR to main. Record the exact main commit.

- [ ] **Step 6: Stop for deployment confirmation**

Deploy only that main commit using the existing SSH release process, bind the current Wi-Fi address if required, switch `current` atomically, and smoke `/healthz`, readiness, aggregate matches API, single-match page, and timestamps. Roll back on any failure.

- [ ] **Step 7: Generate observation-mode LaunchAgent and stop for installation confirmation**

First install without live odds refresh, publish, or notify. Bootstrap and kickstart only after confirmation; verify one label, exit code 0, and zero requests when no eligible fixture exists.

- [ ] **Step 8: Stop for full-live timer and real notification confirmation**

Replace the plist with all guarded live flags, bootstrap/kickstart, and send one explicit WxPusher test summary that contains no secrets or betting amount. Verify request counts, quota delta, state/outbox, aggregate snapshot, public API, logs, and notification status.

- [ ] **Step 9: Final adversarial operational audit**

Confirm no empty-window FotMob calls, no predicted acceptance, no duplicated sport-key refresh, no repeated notification, no secret in logs/files, World Cup/CSL timers unchanged, and documented rollback commands available.

---

## Plan-Level Adversarial Review

- The plan solves the actual goal only after both local collection and ECS public deployment are active; deploying code alone or installing the timer alone is insufficient.
- The plan deliberately avoids a player-impact model because no validated player ratings exist. Recommendation changes are market-refreshed MatchPick v3 outputs, not fabricated lineup adjustments.
- A five-minute LaunchAgent cadence is safe only if Task 1's local fixture gate and persisted throttling are proven with exploding transports and operational logs.
- FotMob schema or access changes are a contract change, not a routine bug fix; implementation must stop rather than loosen confirmed semantics.
- State ordering spans lineup cache, quota-consuming refresh, committed partitions, aggregate publish, notification outbox, and sent receipt. Each boundary needs injected failure tests; a green happy path is not sufficient evidence.
- All active aggregate partitions must remain present. Partial aggregate publication is forbidden even when one league refresh succeeds.
- The free-source choice increases maintenance risk and can miss lineups. The allowed fallback is old recommendation plus a deduplicated warning, never predicted lineups.
- Timer installation, full-live flags, provider requests, quota use, WxPusher, push, merge, and deploy remain separate confirmation gates with independent rollback.

Review conclusion: the plan covers data semantics, identity, cadence, quota, persistence, publication, notification, deployment, and rollback without adding player-model claims. Execution must pause if live evidence contradicts the saved confirmed-lineup contract or if a requested state transition has not been separately authorized.
