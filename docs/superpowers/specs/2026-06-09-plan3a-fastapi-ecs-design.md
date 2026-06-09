# Plan 3A FastAPI/ECS Local Design

- Date: 2026-06-09
- Status: Draft for user review
- Scope: local FastAPI/ECS API design before cloud deployment

## Goal

Plan 3A turns the already-tested local HTTP/ASGI contract into a production-shaped API layer that can later run on ECS. This phase stays local: no deployment, no cloud resource changes, no push, no live odds refresh, and no online writes.

The success condition is a locally tested service boundary that preserves the current security and data semantics:

- HMAC ingest verification with timestamp, body hash, run id, snapshot id, and idempotency key.
- Idempotent snapshot persistence.
- Read-only latest snapshot and match projection APIs.
- Public preview with research disclaimer and no stake or bet amount fields.
- `/healthz` that only reports liveness.

## Non-Goals

- No ECS deployment.
- No RDS/PostgreSQL provisioning.
- No OSS, CDN, DNS, TLS, or domain setup.
- No frontend redesign.
- No new model logic or ML.
- No increase in The Odds API usage.
- No secrets in code, docs, commits, logs, or responses.

## Recommended Approach

Use a thin FastAPI adapter over the current tested application boundary.

The current modules already define the important behavior:

- `worldcup.ingest_server` verifies HMAC, replay window, body hash, snapshot id, and idempotency key.
- `worldcup.ingest_app` connects verified ingest requests to a store.
- `worldcup.store.SQLiteSnapshotStore` gives a local persistence implementation.
- `worldcup.query` projects preview/API-safe match rows.
- `worldcup.preview` builds HTML with the required disclaimer.
- `worldcup.http_app` and `worldcup.asgi_app` define the existing route contract.

FastAPI should not reimplement these rules. It should call the existing modules and focus on HTTP concerns: request body reading, headers, response status codes, content type, and dependency injection for `db_path` and `INGEST_HMAC_SECRET`.

This keeps the risk low and preserves the 104-test safety net.

## Alternatives Considered

### Option A: Thin FastAPI Wrapper

This is the recommended path. It adds FastAPI as an adapter around proven functions, with tests that compare route behavior against the current contract. It is quick, low-risk, and easy to swap from SQLite to PostgreSQL later.

Trade-off: some internal response handling may still look like the standard-library adapter until the service layer is fully extracted.

### Option B: Service-Layer First, FastAPI Second

This would extract a formal service layer before any FastAPI code. It is architecturally clean but slower and more likely to churn already-working local code.

Trade-off: better long-term shape, but not necessary before the first ECS-shaped local API exists.

### Option C: Direct ECS Prototype

This would install dependencies, create FastAPI, connect deployment scripts, and test on ECS in one pass. It may feel faster, but it mixes local behavior, cloud networking, secrets, and persistence changes at once.

Trade-off: highest chance of confusing local bugs with cloud configuration problems. This is not recommended.

## API Contract

FastAPI should expose the same public routes already tested locally:

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/healthz` | Return `{"schema_version": 1, "service": "worldcup-analysis", "status": "ok"}` |
| `POST` | `/api/ingest/snapshot` | Verify HMAC request and store snapshot idempotently |
| `GET` | `/api/snapshot/latest` | Return latest full snapshot, or `404` when none exists |
| `GET` | `/api/matches` | Return `project_match_rows(snapshot)` only |
| `GET` | `/preview` | Return HTML preview with research disclaimer |

`/healthz` must not read the database, require secrets, expose quota, expose snapshot contents, or output environment variables.

`/api/matches` must not include stake, bet amount, wager size, or other money-management fields.

## Configuration

Local FastAPI configuration should come from environment variables or CLI defaults:

| Name | Purpose | Local default |
|---|---|---|
| `WORLDCUP_DB_PATH` | SQLite DB path while local | `data/local/worldcup.db` |
| `INGEST_HMAC_SECRET` | HMAC verification secret | required |

The implementation can continue using `.env` only for local runs. `.env.example` must remain value-free. Readiness checks should continue to avoid printing values.

## Persistence Boundary

Plan 3A should keep SQLite for local testing, but introduce a small store protocol before PostgreSQL work begins.

The storage boundary should preserve these operations:

- `put_snapshot(idempotency_key, payload, stored_at) -> stored | duplicate`
- `latest_snapshot() -> snapshot | None`
- `count_snapshots() -> int` for local tests

SQLite remains the first implementation. PostgreSQL should be a later Plan 3B step using the same behavior:

- `idempotency_key` unique constraint.
- Full ingest payload stored for audit.
- Full snapshot JSON stored for read APIs.
- `snapshot_at` and `stored_at` indexed enough for latest snapshot lookup.

This avoids binding FastAPI route code to SQLite details.

## Data Flow

### Ingest

1. macmini or local script builds an ingest request from `analysis_snapshot.json`.
2. FastAPI receives `POST /api/ingest/snapshot`.
3. FastAPI reads raw request body and headers.
4. Existing HMAC verification validates timestamp, body hash, run id, snapshot id, body snapshot hash, and idempotency key.
5. Store writes the snapshot if the idempotency key is new.
6. Duplicate idempotency key returns duplicate status without rewriting.
7. Rejected requests return `400` and do not write state.

### Read APIs

1. `GET /api/snapshot/latest` reads the latest snapshot from the store.
2. `GET /api/matches` reads the same snapshot and returns the safe projection.
3. `GET /preview` renders the same snapshot as HTML.
4. Missing snapshot returns `404`.

## Error Handling

Use explicit status and body shapes:

| Case | Status | Body |
|---|---:|---|
| Health check | `200` | `status=ok` |
| Ingest stored | `200` | `status=stored` |
| Ingest duplicate | `200` | `status=duplicate` |
| Ingest rejected | `400` | `status=rejected`, reason without secret values |
| Latest snapshot missing | `404` | `error=snapshot_not_found` |
| Unknown route | FastAPI default or explicit `404` | no secrets |

No error response should include API keys, HMAC secrets, RDS connection strings, cookies, tokens, raw `.env` content, or full request headers.

## Testing Strategy

Plan 3A implementation should add tests before production code:

1. FastAPI `/healthz` returns ok without a DB or secret.
2. FastAPI `GET /api/matches` returns safe projected rows from a seeded SQLite store.
3. FastAPI `GET /preview` returns HTML with the disclaimer.
4. FastAPI `POST /api/ingest/snapshot` stores a signed request.
5. Bad HMAC request returns `400` and writes nothing.
6. Duplicate ingest returns duplicate status and does not create a second snapshot.
7. `.env`, `data/cache/`, `data/local/`, and `data/probe/` remain ignored.
8. Full suite remains green.

If adding FastAPI requires a dependency install or `pyproject.toml` update, that change should be explicit and verified locally before committing.

## Deployment Readiness Checks

Plan 3A does not deploy, but the implementation should prepare for these later checks:

- `GET /healthz` smoke test.
- HMAC ingest smoke test against a staging or local endpoint.
- Read API smoke test confirming no stake or bet amount fields.
- Preview smoke test confirming the disclaimer.
- Log scan confirming no secrets.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| FastAPI route code duplicates security logic | Keep verification in `worldcup.ingest_server` |
| SQLite details leak into route handlers | Introduce or preserve a small store boundary |
| `.env` values leak during debugging | Keep readiness and errors value-free |
| Cloud work starts before local contract is stable | Complete and commit local FastAPI tests first |
| PostgreSQL migration changes behavior | Treat PostgreSQL as Plan 3B behind the same store contract |

## Acceptance Criteria

Plan 3A is complete when:

- Local FastAPI app exists and exposes the agreed routes.
- The FastAPI app passes route tests for health, ingest, read APIs, and preview.
- The implementation uses existing HMAC verification and safe projection logic.
- SQLite remains usable for local development.
- Readiness remains green with the local `.env`.
- No deployment, push, cloud resource change, live API call, or online write has occurred.
