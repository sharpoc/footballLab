# P9.18 Engineering Hardening Guardrails

## Goal

Turn the external engineering review into low-risk guardrails before any broad `worldcup.pipeline` split or model adjustment. This phase should make future refactors safer without changing model output, CSL `club_rating_pending`, live refresh behavior, ingest semantics, or public UI content.

## Scope

- Move FastAPI/Uvicorn/HTTPX out of core install requirements and into optional extras.
- Add a small GitHub Actions CI workflow that installs test extras and runs the existing local test runner.
- Record the follow-up hardening sequence so pipeline refactors do not outrun test and dependency boundaries.

## Non-Goals

- Do not split `worldcup/pipeline.py` in this phase.
- Do not tune Elo/Poisson/EV thresholds or CSL rating policy.
- Do not call The Odds API, read `.env`, consume quota, publish snapshots, deploy, update LaunchAgent, or write cloud state.
- Do not add lint/type gates until the current test-only CI is stable.

## Implementation

1. Update `pyproject.toml`.
   - Keep `[project].dependencies` empty for the core package.
   - Add `web` extra for FastAPI/Uvicorn/HTTPX.
   - Keep `postgres` extra for `psycopg`.
   - Add `dev` extra for web test dependencies plus pytest.

2. Add `.github/workflows/ci.yml`.
   - Use Python 3.12.
   - Install `.[dev]`.
   - Run `PYTHONDONTWRITEBYTECODE=1 python tests/run_tests.py`.

3. Update entry docs.
   - README should describe optional dependency groups.
   - RECENT_WORK should record the guardrail phase and validation.

## Follow-Up Order

1. P9.19 HTTP ingest hardening: request body size limit, content type check, structured JSON errors, and request id.
2. P9.20 source fetch hardening: bounded retry policy, credential vs transient error split, safe URL logging, JSON parse failures, and cache fallback reporting.
3. P9.21 typed configuration and competition profile boundary: define the minimal profile interface before moving competition-specific code.
4. P9.22 pipeline split: move code behind the existing facade with behavior-preserving tests already protected by CI.
5. Later: SQLite WAL/busy timeout/index fields, coverage/ruff/mypy gates, and RECENT_WORK archival policy.

## Validation

- `git diff --check`
- `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py`

## Adversarial Review

- CI can fail on GitHub if optional extras miss a dependency that is currently present only in this local machine; installing `.[dev]` is the control.
- Moving dependencies to extras is safe for core CLI paths only if FastAPI modules stay optional and tests install `dev`.
- Adding CI is a repository state change only; it does not deploy or call external services.
- Pipeline splitting remains deliberately delayed because broad code movement before CI can hide behavioral drift.
