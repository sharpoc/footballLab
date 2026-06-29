# HTTP Ingest Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the local HTTP ingest boundary without changing snapshot analysis, model behavior, scheduler behavior, or store semantics.

**Architecture:** Keep HMAC verification and idempotency in `worldcup.ingest_server` / `worldcup.ingest_app`. Add request boundary checks and structured response shaping in `worldcup.http_app`, then let FastAPI continue to delegate to the same route contract.

**Tech Stack:** Python standard library HTTP adapter, FastAPI adapter wrapper, existing custom test runner.

---

### Task 1: HTTP Ingest Boundary Tests

**Files:**
- Modify: `tests/test_http_app.py`
- Modify: `tests/test_fastapi_app.py`

- [ ] Write failing tests for unsupported content type, oversized ingest body, structured HMAC rejection, and request id echo.
- [ ] Run focused tests and confirm they fail before production changes.

### Task 2: Standard Library HTTP Contract

**Files:**
- Modify: `worldcup/http_app.py`

- [ ] Add a default max ingest body size constant.
- [ ] Normalize inbound headers for `Content-Type` and `X-Request-Id`.
- [ ] Reject non-JSON ingest requests with HTTP 415.
- [ ] Reject oversized ingest bodies with HTTP 413 before calling ingest.
- [ ] Return structured JSON errors with `error.code` and `error.request_id`.
- [ ] Echo request id through `X-Request-Id` on ingest responses.
- [ ] Guard `BaseHTTPRequestHandler.do_POST` against invalid `Content-Length`, oversized body, and invalid UTF-8 before dispatch.

### Task 3: FastAPI Header Parity

**Files:**
- Modify: `worldcup/fastapi_app.py`

- [ ] Preserve non-content response headers from `handle_request`, including `X-Request-Id` and `Cache-Control`.
- [ ] Keep FastAPI as a thin adapter; do not duplicate HMAC or idempotency logic.

### Task 4: Docs And Verification

**Files:**
- Modify: `README.md`
- Modify: `RECENT_WORK.md`

- [ ] Document the ingest hardening behavior at a high level.
- [ ] Run `git diff --check`.
- [ ] Run focused HTTP/FastAPI tests.
- [ ] Run `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`.

## Adversarial Review

- Body size checks in `handle_request` protect the shared contract, while `do_POST` must also check `Content-Length` before reading from the socket.
- Structured errors must not include request bodies, signatures, secrets, raw headers, or payload JSON.
- Request id echo is useful for operations but must sanitize caller-provided values before reflecting them.
- This phase must not alter model probabilities, value signal grading, CSL `club_rating_pending`, live refresh, scheduler cadence, or cloud deployment state.
