# Commute Local Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the local MVP into a deployment-ready shape without deploying: ASGI-compatible adapter, static site/API export, and local readiness checks.

**Architecture:** Keep all runtime code standard-library-only. Reuse `worldcup.http_app.handle_request` as the single route contract, add an ASGI adapter around it, add a static export layer around `query`/`preview`, and add readiness checks that inspect local files and ignore rules without consuming quota.

**Tech Stack:** Python 3.11+, standard library only, existing `tests/run_tests.py`.

---

## Safety Boundaries

- No dependency installation.
- No deployment, push, commit, PR, or cloud writes.
- No `--live` refresh or API quota consumption.
- No long-running local server started automatically.
- Output files must go under ignored directories such as `data/cache/` or `data/local/`.

---

## Tasks

### Task 1: ASGI Adapter

**Files:**
- Create: `worldcup/asgi_app.py`
- Create: `tests/test_asgi_app.py`

Steps:
- [ ] Write tests that call an ASGI app with fake scope/receive/send and assert `GET /api/matches` returns JSON and `GET /preview` returns HTML.
- [ ] Run tests and confirm RED because `worldcup.asgi_app` does not exist.
- [ ] Implement `create_asgi_app(db_path, secret)` wrapping `worldcup.http_app.handle_request`.
- [ ] Run all tests and confirm GREEN.

### Task 2: Static Export

**Files:**
- Create: `worldcup/export.py`
- Create: `tests/test_export.py`

Steps:
- [ ] Write tests for exporting `index.html`, `api/snapshot/latest.json`, and `api/matches.json` from a snapshot or SQLite DB.
- [ ] Run tests and confirm RED.
- [ ] Implement export functions and CLI with default output under `data/cache/site/`.
- [ ] Run tests and confirm GREEN.

### Task 3: Readiness Check

**Files:**
- Create: `worldcup/readiness.py`
- Create: `tests/test_readiness.py`

Steps:
- [ ] Write tests checking a complete local environment reports ok and missing cache/env reports actionable warnings.
- [ ] Run tests and confirm RED.
- [ ] Implement checks for snapshot, preview, quota, `.env` variable names, ignored output paths, and no requirement to contact network.
- [ ] Run tests and confirm GREEN.

### Task 4: Docs and Verification

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`
- Modify: `docs/superpowers/data-contract.md`

Steps:
- [ ] Document ASGI adapter, static export, readiness check, and boundaries.
- [ ] Generate ignored static export if a cache snapshot exists.
- [ ] Run full verification:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.refresh_runner
git diff --check
git check-ignore -v .env data/cache/preview.html data/cache/site/index.html data/local/worldcup.db
```

- [ ] Scan for key leakage outside ignored directories.

---

## Done Criteria

- All tests pass.
- No live API calls or network writes are made.
- New generated artifacts are ignored.
- Docs clearly say deployment/FastAPI/ECS still require explicit confirmation.
