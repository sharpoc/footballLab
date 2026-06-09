# Plan 5 Deployment Dry-Run Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare a single-server production launch checklist that makes ECS/domain/secret/rollback requirements explicit before any real cloud deployment.

**Architecture:** Keep this phase documentation-first and local-only. The intended rollout is one production server with a controlled smoke window: start the app on the production server first, keep scheduled refresh off, verify health/read/ingest behavior, then separately enable the public domain and macmini refresh. SQLite is the default MVP store; PostgreSQL/RDS is optional future hardening, not a launch blocker.

**Tech Stack:** Python 3, existing `worldcup.readiness`, `worldcup.export`, existing `tests/run_tests.py`, FastAPI local adapter, SQLite by default, Markdown runbook documentation.

---

## Boundaries

- Do not deploy to ECS.
- Do not create, modify, or delete RDS, DNS, OSS, security group, SSL, cron, launchd, or cloud secret resources.
- Do not connect to a real PostgreSQL/RDS instance.
- Do not send HTTP requests to a real ingest endpoint.
- Do not run live source refresh or spend The Odds API credits.
- Do not print or commit API keys, HMAC secrets, `DATABASE_URL`, cookies, tokens, passwords, signatures, or request bodies.
- Do not push the branch.

## File Structure

- Create `docs/superpowers/plans/2026-06-09-plan5-deployment-dry-run-checklist.md`: this implementation plan and execution checklist.
- Modify `docs/ops/local-to-cloud-checklist.md`: operational dry-run runbook for ECS/storage/domain/secret/rollback.
- Modify `README.md`: update current status and next-step summary for Plan 5.
- Modify `RECENT_WORK.md`: record the local-only Plan 5 dry-run checklist and verification result.

## Single-Server Readiness Model

Plan 5 has three gates for one production server:

| Gate | Meaning | Allowed Actions |
|---|---|---|
| Gate A: local dry-run | Prove local repo, static export, readiness, redaction, and dry-run guards are coherent | Local file reads/writes in ignored dirs, local tests, docs |
| Gate B: controlled production-server smoke | Use the one real server after explicit confirmation, without scheduled refresh or final public cutover | Same ECS/server, SQLite default, temporary port or temporary host, signed ingest smoke |
| Gate C: production activation | Enable public domain/HTTPS and scheduled refresh after Gate B passes and rollback is ready | Domain/HTTPS, macmini scheduled refresh, monitored rollout |

This plan only completes Gate A.

## ECS Checklist

- [ ] Confirm target Alibaba Cloud region.
- [ ] Confirm ECS instance type, OS image, disk size, and security group.
- [ ] Confirm process manager: `systemd` service or container runtime.
- [ ] Confirm app command for SQLite MVP launch, for example:

```bash
python3 -m worldcup.fastapi_app --host 127.0.0.1 --port 8788 --env /etc/worldcup/.env --store sqlite --db /var/lib/worldcup/worldcup.db
```

- [ ] Confirm `/healthz` is exposed through the chosen reverse proxy or load balancer.
- [ ] Confirm only required ports are public, normally `80` / `443`; app port should stay internal.
- [ ] Confirm logs redact headers and never include body, HMAC signature, `.env`, `DATABASE_URL`, or API keys.
- [ ] Confirm deployment artifact source: Git checkout, release tarball, or container image.
- [ ] Confirm release identity: Git commit SHA must be recorded before any smoke test.

## Storage Checklist

- [ ] Use SQLite for MVP launch unless there is a separate explicit decision to add RDS.
- [ ] Confirm SQLite path, for example `/var/lib/worldcup/worldcup.db`, is on persistent disk and backed up.
- [ ] Confirm app user can read/write only the app directory, `.env`, log directory, and SQLite data path.
- [ ] Confirm idempotency is still enforced by `idempotency_key`.
- [ ] Confirm smoke expectation: same signed payload twice returns `stored`, then `duplicate`.
- [ ] If PostgreSQL/RDS is chosen later, require `WORLDCUP_STORE=postgres`, `DATABASE_URL`, least-privilege app user, ECS-to-RDS network access, and backup/snapshot policy.
- [ ] macmini must never connect directly to SQLite files, RDS, or OSS; it only sends signed ingest payloads to the server.

## Domain And HTTPS Checklist

- [ ] Confirm domain name and whether ICP filing is complete.
- [ ] Confirm DNS provider and record type.
- [ ] Gate B may use a temporary port or temporary host, for example `preprod.example.invalid`, before final production activation.
- [ ] Confirm TLS certificate source: Alibaba Cloud certificate, Let's Encrypt, or existing cert.
- [ ] Confirm HTTP to HTTPS redirect behavior.
- [ ] Confirm CDN/OSS/static hosting choice if static Research Ledger is served outside FastAPI.
- [ ] Confirm cache policy: HTML short TTL, JSON API/static export short TTL during tournament window.

## Secret Checklist

The required variable names are:

```text
THE_ODDS_API_KEY
INGEST_HMAC_SECRET
WORLDCUP_STORE
DATABASE_URL
```

- [ ] `.env.example` must keep names only, with empty values.
- [ ] Real `.env` must stay git ignored.
- [ ] `INGEST_HMAC_SECRET` must be shared only between the producer and ECS ingest server.
- [ ] `THE_ODDS_API_KEY` remains low-frequency and quota-aware.
- [ ] `WORLDCUP_STORE=sqlite` is the default single-server MVP launch setting.
- [ ] `DATABASE_URL` is only required if PostgreSQL/RDS is explicitly chosen later, and must never appear in logs, commits, docs, screenshots, shell history snippets, or chat.
- [ ] Dry-run outputs must omit request body and `X-Worldcup-Signature`.

## Macmini Refresh Checklist

- [ ] Confirm refresh owner: macmini scheduled task is recommended for MVP.
- [ ] Keep scheduled refresh disabled during Gate B production-server smoke.
- [ ] Confirm command stays explicit `--live` only after Gate C production activation is approved.
- [ ] Confirm scheduler low-quota behavior remains active.
- [ ] Confirm macmini sends only signed ingest payload to ECS and never connects to RDS.
- [ ] Confirm failed source refresh uses stale cache only with `data_quality.source_errors` and `data_quality.stale_sources`.

## Rollback Checklist

- [ ] Record last known good Git SHA.
- [ ] Keep previous ECS service artifact or container image.
- [ ] Keep previous `.env`/secret version available in secret manager.
- [ ] Keep latest database backup/snapshot before production write.
- [ ] Rollback app first, then DNS only if app rollback fails.
- [ ] If bad data was ingested, stop scheduled refresh before replaying or restoring data.
- [ ] Verify rollback with:

```bash
curl -fsS https://preprod.example.invalid/healthz
curl -fsS https://preprod.example.invalid/api/matches
```

Expected: health returns `status=ok`; matches response contains no stake, bet amount, bankroll, payout, wager, unit, or Chinese money/staking terms.

## Task 1: Update Operational Checklist

**Files:**
- Modify: `docs/ops/local-to-cloud-checklist.md`

- [ ] **Step 1: Add Plan 5 gate model**

Add a section that distinguishes local dry-run, controlled production-server smoke, and production activation.

- [ ] **Step 2: Add ECS/storage/domain/secret/rollback sections**

Document the exact checklist items from this plan in the ops runbook.

- [ ] **Step 3: Add local-only verification commands**

Include:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.export --snapshot data/cache/analysis_snapshot.json --out-dir data/cache/site
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

For the PostgreSQL smoke guard, expected local result is either:

- `dry_run_ready` when `.env` is intentionally configured for `WORLDCUP_STORE=postgres` with a redacted PostgreSQL `DATABASE_URL`.
- `blocked` with `expected_postgres` when `.env` is still local SQLite mode.

Both are local dry-run outcomes; neither connects to RDS or sends HTTP. In the single-server SQLite launch path, the blocked SQLite-mode result is expected and acceptable.

## Task 2: Update Project Status Docs

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README current status**

Add that Plan 5 deployment dry-run checklist is documented and local-only.

- [ ] **Step 2: Update next steps**

Set the next action to explicit approval for controlled production-server smoke, not full public activation.

- [ ] **Step 3: Update Recent Work**

Record that Plan 5 only produced a dry-run checklist and local verification.

## Task 3: Run Local Verification

**Files:**
- No source changes expected beyond docs.

- [ ] **Step 1: Run tests**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected:

```text
156/156 tests passed
```

- [ ] **Step 2: Regenerate static export**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.export --snapshot data/cache/analysis_snapshot.json --out-dir data/cache/site
```

Expected: command exits `0`, writes only ignored files under `data/cache/site/`.

- [ ] **Step 3: Scan static export**

```bash
rg -ni "\brun_id\b|\bquota\b|x-requests|source_errors|stale_sources|TimeoutError|\b(stake|bet amount|bankroll|payout|wager|unit)\b|下注金额|投注金额|本金|重注|追损|串关|喊单" data/cache/site/manifest.json data/cache/site/api/snapshot/latest.json data/cache/site/api/matches.json data/cache/site/index.html
```

Expected: no matches.

- [ ] **Step 4: Run readiness**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
```

Expected:

```text
"ok": true
"errors": 0
"warnings": 0
```

- [ ] **Step 5: Run PostgreSQL smoke guard**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.postgres_smoke --env .env --snapshot data/cache/analysis_snapshot.json --endpoint https://example.invalid/api/ingest/snapshot
```

Expected in current local SQLite mode: exits non-zero with `status=blocked` and `message=expected_postgres`, without printing secret or `DATABASE_URL`. If `.env` has explicitly been switched to PostgreSQL mode, expected is `status=dry_run_ready` with redacted request metadata only.

- [ ] **Step 6: Run whitespace and sensitive value checks**

```bash
git diff --check
```

Expected: no output, exit `0`.

Run tracked-file sensitive value scan against `.env` values. Expected:

```text
tracked_sensitive_value_leaks= 0
```

## Completion Criteria

- Plan 5 docs exist and list ECS/storage/domain/secret/rollback requirements.
- Ops checklist makes local dry-run vs production-server smoke vs production activation explicit.
- Current local repo passes tests, readiness, export redaction scan, and sensitive value scan.
- PostgreSQL smoke guard is exercised locally and either blocks safely in SQLite mode or reports redacted `dry_run_ready` if PostgreSQL is explicitly chosen.
- No deployment, cloud resource change, RDS connection, live API refresh, push, or online write occurred.
