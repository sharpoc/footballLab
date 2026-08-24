# Six-League Live Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate real pre-match single-game picks, closing capture, verified 90-minute settlement, and independent statistics for Serie A, Brasileirão, La Liga, Premier League, Bundesliga, and Ligue 1, with each league enabled only after its own acceptance gates pass.

**Architecture:** Add a pure dynamic planner and deterministic acceptance state machine in front of the existing `league_v1` offline pipeline. A single live orchestrator processes competitions by nearest kickoff, uses the existing five-slot quota boundary, commits partitioned artifacts atomically, and isolates per-league failures; networking, state writes, scheduling, publishing, and deployment remain separate confirmed stages.

**Tech Stack:** Python standard library, existing The Odds API transports/collectors, existing MatchPick v3 engine, JSON files with flock/fsync/os.replace, custom `tests/run_tests.py` runner.

**Spec:** `docs/superpowers/specs/2026-08-24-six-league-live-activation-design.md`

## Global Constraints

- Public output remains research-only and uses only `MATCH_PICK` or `NO_CLEAN_MARKET`; no amounts or execution instructions.
- Already-started or finished matches never receive reconstructed pre-match picks.
- All six leagues participate in discovery; request priority is computed from nearest future kickoff, not a fixed league order.
- Each league has independent acceptance, runtime state, artifacts, closing, results, and statistics.
- Formal identity uses exact sport key plus event ID, canonical teams, and UTC kickoff; no permissive slug fallback.
- A score is not formally settled until its 90-minute semantics are independently verified.
- Dry-run does not read `.env`, call a network, write quota/state/artifacts, publish, or deploy.
- Real provider calls, local production writes, scheduler installation, push, and deploy are separately confirmed gates.
- No new dependency, database schema, club Elo, MatchPick v3 behavior change, or CSL/World Cup refactor.

---

### Task 1: Dynamic six-league refresh planner

**Files:**
- Create: `worldcup/league_live_planner.py`
- Create: `tests/test_league_live_planner.py`

**Interfaces:**
- Consumes: `formal_single_match_competitions()`, injected event rows, acceptance states, quota summaries, scheduler state, and timezone-aware `now`.
- Produces: `plan_league_live_refresh(...) -> dict[str, Any]` with safe `requests`, `skipped`, `estimated_credits`, and `stop_reason` fields.

- [ ] **Step 1: Write failing tests for T-6h/T-90m/T-25m anchors and nearest-kickoff order**

Cover six mixed competition payloads, same-key coalescing, already-started exclusion, exact markets per anchor, and stable tie-breaking by kickoff/competition/event.

- [ ] **Step 2: Run focused tests and verify RED**

Run a direct import of `tests/test_league_live_planner.py`; the missing module/function must fail.

- [ ] **Step 3: Implement the minimal pure planner**

Reuse policy constants where safe, but do not couple to daily Top4 selection. Include `valid_until - 20m`, reschedule conflicts, active/probing priority, quota-exhausted stop, and one request per sport key/anchor.

- [ ] **Step 4: Add adversarial planner tests**

Cover naive datetime rejection, duplicate event ID with different kickoff, unknown quota, all keys exhausted, active freshness guard ahead of probes, and deterministic output under shuffled input.

- [ ] **Step 5: Run focused and full tests**

Run the focused module, `python -m py_compile worldcup/league_live_planner.py`, then the project runner.

- [ ] **Step 6: Commit**

Commit: `feat: plan six-league live refreshes`

---

### Task 2: Acceptance state machine and atomic report store

**Files:**
- Create: `worldcup/league_acceptance.py`
- Create: `tests/test_league_acceptance.py`
- Modify: `worldcup/query.py`
- Modify: `tests/test_query.py`

**Interfaces:**
- Consumes: deterministic evidence blocks for sport catalog, odds schema, identity coverage, and result semantics.
- Produces: `evaluate_league_acceptance(...)`, `LeagueAcceptanceStore`, and a public-safe status projection.

- [ ] **Step 1: Write failing transition tests**

Require ordered states `disabled_until_live_acceptance -> probing -> odds_sample_verified -> identity_verified -> result_contract_verified -> active`; reject manual jumps, evidence regression, competition mismatch, duplicate evidence IDs, and unknown states.

- [ ] **Step 2: Verify RED and implement pure evaluator**

Evidence fingerprints must determine state; configuration alone cannot activate a league. Store blocked reasons as stable codes without raw payloads.

- [ ] **Step 3: Write failing atomic/idempotency tests**

Cover path isolation at `data/local/leagues/acceptance.json`, lock contention, replace failure preserving the old file, same-input `unchanged`, and six independent states.

- [ ] **Step 4: Implement `LeagueAcceptanceStore`**

Use flock, owned temporary file, flush/fsync, `os.replace`, parent-directory fsync, and schema validation on read/write.

- [ ] **Step 5: Project credible status to the single-match page**

Map acceptance/runtime states without generating fake match rows; preserve existing disabled behavior when the report is absent or invalid.

- [ ] **Step 6: Run focused/full verification and commit**

Commit: `feat: gate league live activation by evidence`

---

### Task 3: Explicit six-league team identity registry

**Files:**
- Create: `worldcup/league_team_identity.py`
- Create: `tests/test_league_team_identity.py`
- Modify: `worldcup/collectors/league_odds.py`
- Modify: `tests/collectors/test_league_odds.py`
- Modify: `worldcup/league_competition_pipeline.py`
- Modify: `tests/test_league_competition_pipeline.py`

**Interfaces:**
- Consumes: competition-scoped provider team names and an explicit alias registry.
- Produces: `resolve_league_team_identity(competition_id, provider_name) -> TeamIdentityResult` and an unmatched-team evidence report.

- [ ] **Step 1: Write failing strict-identity tests**

Known aliases resolve only within their competition; unknown names, blank names, cross-league aliases, ambiguous aliases, and home=away fail closed. Confirm national-team and CSL aliases remain unchanged.

- [ ] **Step 2: Implement the registry boundary**

Seed only aliases proven by saved fixtures/tests. Do not invent the full season roster and do not fall back to `canonicalize_club()` for formal live decisions.

- [ ] **Step 3: Wire strict resolution into odds parsing/pipeline**

Parser output carries unmatched evidence; only affected matches are blocked. Exact event ID/sport key/kickoff/team tuple remains mandatory.

- [ ] **Step 4: Verify focused regression and commit**

Commit: `feat: enforce six-league team identity`

---

### Task 4: Generic sport-key scores source and semantics evidence

**Files:**
- Modify: `worldcup/sources/theoddsapi_scores.py`
- Modify: `tests/sources/test_theoddsapi_scores_source.py`
- Modify: `worldcup/league_results.py`
- Modify: `tests/test_league_results.py`
- Create: `worldcup/league_result_evidence.py`
- Create: `tests/test_league_result_evidence.py`

**Interfaces:**
- Consumes: injected exact sport key, provider scores response, safe headers, and explicit semantics evidence.
- Produces: generic score fetch result and `evaluate_result_contract(...)` evidence; formal parsing accepts an evidence fingerprint rather than a free boolean.

- [ ] **Step 1: Write failing generic URL/transport tests**

Verify exact sport key encoding, injected transport, safe quota updates, no key leakage, no cache overwrite on malformed JSON/error, and World Cup wrapper compatibility.

- [ ] **Step 2: Generalize the scores source minimally**

Keep `fetch_worldcup_scores()` as a wrapper. Add no live call in tests.

- [ ] **Step 3: Replace the free `score_semantics_verified` boolean**

Write RED tests proving callers cannot self-assert verification. Require a matching competition/sport key/schema/source evidence fingerprint; otherwise return `result_90min_semantics_unverified`.

- [ ] **Step 4: Test score conflicts and corrections**

Cover boolean/negative/decimal scores, duplicate team entries, incomplete status, event/team/kickoff mismatch, score revision, and finished regression.

- [ ] **Step 5: Run focused/full verification and commit**

Commit: `feat: verify league score contracts`

---

### Task 5: Probe bundle writer and offline acceptance CLI

**Files:**
- Create: `worldcup/league_live_probe.py`
- Create: `tests/test_league_live_probe.py`
- Modify: `.gitignore`
- Modify: `.env.example` only if a required variable name is missing; never add values.

**Interfaces:**
- Consumes: planner output and injected sports/events/odds/scores transports.
- Produces: dry-run request manifest, or explicitly live per-league probe bundles under `data/probe/leagues/<competition_id>/`, plus a deterministic offline acceptance report.

- [ ] **Step 1: Write dry-run safety tests**

Assert no env loader, transport, quota write, file write, notification, publish, or deployment call. Output only safe sport keys, markets, estimated cost, and reason.

- [ ] **Step 2: Write bundle security/atomicity tests**

Accept injected fake responses; strip API key, URL query, Authorization/Cookie, raw request headers, and unrelated provider fields. Reject symlink/wrong partition and preserve old bundle on failure.

- [ ] **Step 3: Implement explicit live probe boundary**

Process one planned request at a time, reload quota after each response, stop on insufficient quota, and return partial completion without retrying successful keys.

- [ ] **Step 4: Add offline `--evaluate` mode**

It reads bundles without network/env and calls Tasks 2–4 evaluators. It never activates configuration or writes formal snapshot/closing/statistics.

- [ ] **Step 5: Run secret scan, focused/full tests, and commit**

Commit: `feat: probe six-league live data safely`

---

### Task 6: Active-league refresh, snapshot/history, and failure isolation

**Files:**
- Modify: `worldcup/league_batch_runner.py`
- Modify: `tests/test_league_batch_runner.py`
- Create: `worldcup/league_live_store.py`
- Create: `tests/test_league_live_store.py`
- Modify: `worldcup/league_competition_pipeline.py`
- Modify: `tests/test_league_competition_pipeline.py`

**Interfaces:**
- Consumes: acceptance report, planner requests, injected odds fetcher, and existing snapshot builder.
- Produces: per-competition snapshot/history and scheduler state; live/write remains blocked for every non-active league.

- [ ] **Step 1: Write acceptance-gate and zero-side-effect RED tests**

Non-active, invalid report, missing evidence, and stale acceptance must not call env/fetch/write. One active league must not implicitly activate others.

- [ ] **Step 2: Implement atomic partitioned store**

Paths are exact `data/cache/leagues/<id>/snapshot.json` and `data/local/leagues/<id>/history/`; use lock/fsync/replace/fingerprint. Commit scheduler state only after both snapshot and history succeed.

- [ ] **Step 3: Wire the live batch orchestrator**

Use planner order, one key at a time, five-slot selection, limited transient retry, safe quota summary, and per-league `built/degraded/stale/blocked/error`. Never reuse stale odds beyond legal decision freshness.

- [ ] **Step 4: Test partial failures and restart idempotency**

Odds failure, writer failure, quota exhaustion after first key, process restart, duplicate anchor, and one-league malformed payload must preserve successful independent work without falsely committing failed state.

- [ ] **Step 5: Verify and commit**

Commit: `feat: refresh active leagues independently`

---

### Task 7: Closing, postmatch, statistics, and page integration

**Files:**
- Modify: `worldcup/league_closing.py`
- Modify: `tests/test_league_closing.py`
- Modify: `worldcup/league_postmatch.py`
- Modify: `tests/test_league_postmatch.py`
- Modify: `worldcup/league_statistics.py`
- Modify: `tests/test_league_statistics.py`
- Modify: `worldcup/query.py`
- Modify: `tests/test_query.py`
- Modify: `worldcup/ledger_html.py`
- Modify: `tests/test_preview.py`

**Interfaces:**
- Consumes: partitioned history, verified results, acceptance/runtime state.
- Produces: immutable closing, finished/statistics artifacts, and public-safe per-league status/matches/statistics.

- [ ] **Step 1: Write end-to-end offline lifecycle tests**

For one league: T-90 snapshot, T-25 snapshot, kickoff boundary, closing selection, verified result, settlement, statistics, and public projection. Repeat for two leagues to prove isolation.

- [ ] **Step 2: Add failure/coverage tests**

Missing closing, no pick, invalid result evidence, score revision, kickoff conflict, postponed event, legacy decision, reconstructed record, and unknown league must not enter formal hit rate.

- [ ] **Step 3: Wire atomic closing/postmatch/statistics runners**

Reuse existing pure settlement functions; do not change MatchPick v3. Failures affect only the competition/event and remain visible in coverage.

- [ ] **Step 4: Update single-match page states**

Show credible `probing/active/degraded/quota_blocked/result_pending`; show picks only for active future matches. Preserve disclaimer and forbidden-field checks.

- [ ] **Step 5: Verify and commit**

Commit: `feat: complete league live settlement lifecycle`

---

### Task 8: Scheduler wrapper, documentation, and implementation verification

**Files:**
- Create: `worldcup/league_scheduled_publish.py`
- Create: `tests/test_league_scheduled_publish.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`

**Interfaces:**
- Consumes: local planner, live runner, postmatch runner, optional existing HMAC publisher, and injected notifier.
- Produces: default dry-run CLI and a future single-timer command; it does not install LaunchAgent by itself.

- [ ] **Step 1: Write default dry-run and pending-retry tests**

Dry-run must not load env or call provider/publisher. Publish pending retries the existing snapshot before any refresh and does not consume quota again.

- [ ] **Step 2: Implement the scheduler wrapper**

One invocation handles all six leagues under a single lock. It runs verified postmatch work, plans due refreshes, refreshes active leagues only when explicitly live, and publishes only successfully committed snapshots.

- [ ] **Step 3: Add quota/lock/retry adversarial tests**

Cover concurrent invocation, transient provider failure, publish 5xx, pending restart, all keys exhausted, partial league success, and no-due zero-request exit.

- [ ] **Step 4: Update project documentation**

Document exact dry-run/live commands, artifact paths, state meanings, request-cost confirmation gate, no-betting-advice boundary, rollback, and that no scheduler is installed yet.

- [ ] **Step 5: Run final verification**

Run focused lifecycle tests, `py_compile` for all new league modules, `git diff --check`, sensitive-field scan, and the full project runner. Re-read the spec and check every acceptance criterion against code/test evidence.

- [ ] **Step 6: Request code review and commit**

Commit: `feat: schedule six-league live lifecycle`

---

## Post-Implementation Operational Gates

These are not authorized by implementation approval and must not be folded into Tasks 1–8.

### Gate A: Zero-credit plan

Run only dry-run/local commands. Report the six sport keys in kickoff order, exact planned markets, estimated credits, available safe slot summaries, and the first stop reason. No `.env` read or provider call.

### Gate B: Minimal real probes

After explicit confirmation, call one sport key at a time in planner order. Save safe samples, update quota from headers, report remaining quota, and stop immediately if the next request is not affordable. Do not activate a league merely because the HTTP call succeeded.

### Gate C: Per-league activation and local write

For each league whose evidence passes, report its evidence fingerprints, unmatched-team count, score-semantics status, next kickoff, and rollback path. Require explicit approval before changing its state to active or writing production local artifacts.

### Gate D: Scheduler installation

Present the exact LaunchAgent command, five-minute wake behavior, lock path, log paths, quota ceiling, unload/rollback command, and confirmation that no competing timer exists. Install only after explicit approval.

### Gate E: Push and deploy

Fresh full tests, clean worktree, exact commit, safe git target, ECS release path, health/readiness/API/preview smoke, and automatic rollback must be reported before confirmation. Push, deploy, secret changes, DB migrations, and business writes remain separate permissions.

## Adversarial Plan Review

- The plan does not assume that all current leagues have odds or completed scores available at the same moment; acceptance and activation are per league.
- A free boolean cannot certify 90-minute score semantics; Task 4 replaces it with evidence-bound verification.
- Dynamic priority is proven with shuffled input and mixed states, preventing registry order from silently becoming business priority.
- Probe samples cannot promote themselves into formal closing/statistics; Tasks 2, 5, and 6 keep evidence, acceptance, and production artifacts separate.
- Team aliases are not inferred wholesale from provider names; only explicit, competition-scoped mappings can enter formal decisions.
- Quota is recalculated after every provider response; partial success is committed only for completed independent keys.
- The timer is deliberately deferred until the full local lifecycle passes, preventing a five-minute wakeup from repeatedly consuming quota during development.
- The plan changes no prediction weights, club ratings, legacy settlement semantics, database schema, secrets, or unrelated competition paths.

Review conclusion: Tasks 1–8 produce a fully testable implementation without requiring live access. Operational Gates A–E preserve explicit control over quota, local production state, scheduling, push, and deployment. The remaining external uncertainty is whether provider score documentation and real payloads prove the required 90-minute scope; if not, the affected league remains non-active rather than weakening settlement correctness.
