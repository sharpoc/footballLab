# Six-League Initial Bootstrap Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed one-time production command that creates and publishes the first complete aggregate snapshot for every currently active league.

**Architecture:** A new orchestration module reads the committed acceptance report and saved provider event identities, derives the exact active competition/event set, and calls the existing planned refresh and aggregate publish boundaries. Dry-run returns only a safe plan; live execution requires `--live --write --force-initial`, rejects an already-complete bootstrap, uses the existing five-slot quota-aware fetcher and strict identity registry, and publishes only after every active partition is durably committed.

**Tech Stack:** Python 3.12 standard library, existing league acceptance/planner/batch/store/publish/HMAC modules, project test runner.

**Spec:** `docs/superpowers/specs/2026-08-24-six-league-confirmed-lineup-notification-design.md`

## Global Constraints

- Only acceptance rows with four evidence fingerprints and `state=active` participate; Bundesliga remains excluded.
- Offline prediction files are never published directly.
- Dry-run does not read `.env`, call FotMob/The Odds API, write files, publish, or notify.
- Live requires all of `--live --write --force-initial` and a non-placeholder HTTPS endpoint.
- Live holds a non-blocking single-instance lock across plan re-read, completion check, refresh, publish, and state commit; contention blocks before env/provider access.
- Expected event IDs and snapshot attempt IDs are bound before provider calls; unknown teams fail closed through the strict identity registry.
- Any active-league refresh failure blocks aggregate publication; no incomplete aggregate may be published.
- Existing complete active partitions block a second initial bootstrap unless a future separately designed refresh workflow handles them.
- No model, public API, settlement, database schema, LaunchAgent, or notification behavior changes.

---

### Task 1: Bootstrap orchestration and CLI

**Files:**
- Create: `worldcup/league_bootstrap_publish.py`
- Create: `tests/test_league_bootstrap_publish.py`

**Interfaces:**
- Consumes: `LeagueAcceptanceStore.read()`, `acceptance_fingerprint()`, `accepted_league_team_identity_registry()`, `run_planned_league_refresh()`, and `publish_committed_league_snapshots()`.
- Produces: `build_league_bootstrap_plan(root) -> dict`, `run_league_bootstrap_publish(...) -> dict`, and a default-safe CLI.

- [ ] Write failing tests proving dry-run has zero dependency calls; unsafe flag combinations, placeholder endpoint, missing/invalid acceptance, missing/empty/duplicate event IDs, and an already-complete bootstrap are blocked.
- [ ] Run `tests/test_league_bootstrap_publish.py` and confirm RED because the module is absent.
- [ ] Implement deterministic active competition/event planning with safe counts, acceptance fingerprint binding, and per-competition `league-attempt-bootstrap-*` IDs.
- [ ] Run focused tests and confirm GREEN.
- [ ] Write failing tests proving live execution passes only active competitions to planned refresh, refuses partial refresh results, publishes one complete aggregate only after all durable receipts exist, and returns safe publisher receipts.
- [ ] Implement minimal dependency-injected live orchestration and CLI adapters for env loading, quota-aware odds fetch, HMAC publish, and strict identity.
- [ ] Run focused tests and confirm GREEN.

### Task 2: Documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

**Interfaces:**
- Consumes: the CLI contract from Task 1.
- Produces: operator commands that clearly separate dry-run from confirmed live execution.

- [ ] Document the dry-run and triple-gated live command, quota/online-write risk, idempotency rule, and Bundesliga exclusion.
- [ ] Record local implementation status without claiming any real provider call or publication.
- [ ] Run `py_compile`, focused tests, `git diff --check`, sensitive-field scan, default CLI dry-run with before/after file hashes, and the full project test runner.

## Adversarial Review

- Partial commit risk: publication is permitted only when refresh returns `refreshed` and receipts cover the exact active set; the aggregate builder re-reads committed partitions and revalidates acceptance fingerprint.
- Concurrent invocation risk: a dedicated live lock prevents duplicate quota consumption and competing aggregate publications; the plan and completion state are rebuilt after lock acquisition.
- Stale probe risk: exact saved event IDs are pre-bound and must appear in the fresh provider snapshot; missing or changed events fail closed instead of silently shrinking coverage.
- Quota risk: no live dependency is constructed in dry-run; live reuses the existing selected-key and response-ledger boundary.
- Replay risk: initial bootstrap blocks when every active partition already exists, preventing accidental repeated force usage.
- Scope risk: this adds only the missing initial production entry point; recurring schedule, lineup behavior, model semantics, settlement, and UI projection remain unchanged.
- Rollback: before publication, failures leave only auditable committed partitions and no online change; after a confirmed ingest, rollback remains the existing snapshot/release operational procedure rather than deleting history.
