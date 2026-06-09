# Plan 3A FastAPI/ECS Local Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI API layer for the World Cup analysis service while preserving the existing HMAC ingest, idempotency, SQLite, read projection, preview, and readiness behavior.

**Architecture:** Add FastAPI as a thin adapter over the already-tested `worldcup.http_app.handle_request` contract. Keep SQLite as the local store and introduce a small store protocol so PostgreSQL can be added in Plan 3B without rewriting API routes.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, HTTPX/TestClient, SQLite, existing `tests/run_tests.py`.

---

## File Structure

- Modify `pyproject.toml`: add FastAPI/Uvicorn/HTTPX runtime and test dependencies.
- Create `worldcup/fastapi_app.py`: FastAPI app factory, route handlers, local CLI entrypoint.
- Create `worldcup/store_contract.py`: `SnapshotStore` protocol shared by SQLite and future PostgreSQL store.
- Modify `worldcup/store.py`: make `SQLiteSnapshotStore` explicitly satisfy `SnapshotStore`.
- Create `tests/test_fastapi_app.py`: route-level FastAPI tests.
- Create `tests/test_store_contract.py`: store protocol compatibility test.
- Modify `README.md`: document local FastAPI run and verification commands.
- Modify `docs/superpowers/data-contract.md`: record the FastAPI route contract and store boundary.
- Modify `docs/ops/local-to-cloud-checklist.md`: add local FastAPI smoke checks.
- Modify `RECENT_WORK.md`: record Plan 3A implementation progress.

## Task 1: Add FastAPI Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update dependency manifest**

Replace the current dependency block:

```toml
dependencies = []
```

with:

```toml
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn[standard]>=0.30,<1",
  "httpx>=0.27,<1",
]
```

- [ ] **Step 2: Install local package with dependencies**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pip install -e .
```

Expected: command exits `0` and does not print secrets.

- [ ] **Step 3: Verify FastAPI import**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import fastapi
import httpx
import uvicorn
print("fastapi dependencies available")
PY
```

Expected output:

```text
fastapi dependencies available
```

- [ ] **Step 4: Commit dependency manifest**

Run:

```bash
git add pyproject.toml
git commit -m "build: add fastapi local api dependencies"
```

## Task 2: Add FastAPI Health and Read Tests

**Files:**
- Create: `tests/test_fastapi_app.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_fastapi_app.py` with:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from worldcup.fastapi_app import create_fastapi_app
from worldcup.store import SQLiteSnapshotStore


def _snapshot():
    return {
        "snapshot_at": "2026-06-08T00:00:00+00:00",
        "run": {"run_id": "run-1"},
        "counts": {"matches": 1},
        "data_quality": {"stale_sources": [], "source_errors": []},
        "matches": [
            {
                "kickoff_at_utc": "2026-06-11T19:00:00+00:00",
                "stage": "Matchday 1",
                "group": "Group A",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "signals": [{"grade": "A"}],
            }
        ],
    }


def _store_snapshot(db_path: Path):
    SQLiteSnapshotStore(db_path).put_snapshot(
        idempotency_key="run-1:snapshot-1",
        payload={
            "run_id": "run-1",
            "snapshot_id": "snapshot-1",
            "snapshot_at": "2026-06-08T00:00:00+00:00",
            "snapshot": _snapshot(),
        },
        stored_at="2026-06-08T00:02:00+00:00",
    )


def test_fastapi_healthz_does_not_require_db_or_secret():
    with TemporaryDirectory() as tmp:
        app = create_fastapi_app(db_path=Path(tmp) / "missing.db", secret="")
        client = TestClient(app)

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.json() == {
            "schema_version": 1,
            "service": "worldcup-analysis",
            "status": "ok",
        }


def test_fastapi_get_matches_returns_safe_projection():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/api/matches")

        assert response.status_code == 200
        row = response.json()["matches"][0]
        assert row["match_label"] == "Mexico vs South Africa"
        assert "stake" not in row
        assert "bet_amount" not in row


def test_fastapi_get_preview_returns_disclaimer_html():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        _store_snapshot(db_path)
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/preview")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "研究分析工具，不构成投注建议" in response.text
        assert "Mexico vs South Africa" in response.text


def test_fastapi_latest_snapshot_returns_404_when_missing():
    with TemporaryDirectory() as tmp:
        app = create_fastapi_app(db_path=Path(tmp) / "empty.db", secret="test-hmac-secret")
        client = TestClient(app)

        response = client.get("/api/snapshot/latest")

        assert response.status_code == 404
        assert response.json()["error"] == "snapshot_not_found"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: fails with `ModuleNotFoundError` or import failure for `worldcup.fastapi_app`.

## Task 3: Implement FastAPI Thin Wrapper

**Files:**
- Create: `worldcup/fastapi_app.py`

- [ ] **Step 1: Create FastAPI app factory**

Create `worldcup/fastapi_app.py` with:

```python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from worldcup.http_app import handle_request
from worldcup.refresh_runner import _load_env


def _headers(request: Request) -> dict[str, str]:
    return {key: value for key, value in request.headers.items()}


def _response(result: dict[str, Any]) -> Response:
    content_type = result["headers"].get("Content-Type", "application/json")
    if content_type.startswith("text/html"):
        return HTMLResponse(
            content=result["body"],
            status_code=result["status"],
            media_type="text/html",
        )
    return Response(
        content=result["body"],
        status_code=result["status"],
        media_type="application/json",
    )


async def _dispatch(
    request: Request,
    method: str,
    path: str,
    db_path: str | Path,
    secret: str,
    body: str = "",
) -> Response:
    result = handle_request(
        method=method,
        path=path,
        headers=_headers(request),
        body=body,
        db_path=db_path,
        secret=secret,
    )
    return _response(result)


def create_fastapi_app(db_path: str | Path = "data/local/worldcup.db", secret: str = "") -> FastAPI:
    app = FastAPI(title="Worldcup Analysis API", version="0.1.0")

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        return await _dispatch(request, "GET", "/healthz", db_path, secret)

    @app.get("/api/snapshot/latest")
    async def latest_snapshot(request: Request) -> Response:
        return await _dispatch(request, "GET", "/api/snapshot/latest", db_path, secret)

    @app.get("/api/matches")
    async def matches(request: Request) -> Response:
        return await _dispatch(request, "GET", "/api/matches", db_path, secret)

    @app.get("/preview")
    async def preview(request: Request) -> Response:
        return await _dispatch(request, "GET", "/preview", db_path, secret)

    @app.post("/api/ingest/snapshot")
    async def ingest_snapshot(request: Request) -> Response:
        raw_body = await request.body()
        return await _dispatch(
            request,
            "POST",
            "/api/ingest/snapshot",
            db_path,
            secret,
            body=raw_body.decode("utf-8"),
        )

    return app


def load_secret(env_path: str | Path = ".env", secret_env: str = "INGEST_HMAC_SECRET") -> str:
    secret = _load_env(str(env_path)).get(secret_env)
    if not secret:
        raise SystemExit(f"{secret_env} is missing in {env_path}")
    return secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local FastAPI adapter.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--db", default="data/local/worldcup.db")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default="INGEST_HMAC_SECRET")
    args = parser.parse_args(argv)

    import uvicorn

    app = create_fastapi_app(db_path=args.db, secret=load_secret(args.env, args.secret_env))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run tests to verify read routes pass**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: the FastAPI health, matches, preview, and missing snapshot tests pass.

- [ ] **Step 3: Commit FastAPI read wrapper**

Run:

```bash
git add worldcup/fastapi_app.py tests/test_fastapi_app.py
git commit -m "feat: add local fastapi read API adapter"
```

## Task 4: Add FastAPI Ingest Tests

**Files:**
- Modify: `tests/test_fastapi_app.py`

- [ ] **Step 1: Append ingest tests**

Append to `tests/test_fastapi_app.py`:

```python
from worldcup.ingest import build_ingest_request


def test_fastapi_post_ingest_snapshot_stores_signed_request():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp="2026-06-08T00:02:00+00:00",
        )
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "stored"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1


def test_fastapi_post_ingest_snapshot_rejects_bad_signature_without_writing():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp="2026-06-08T00:02:00+00:00",
        )
        headers = dict(request["headers"])
        headers["X-Worldcup-Signature"] = "sha256=bad"
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        response = client.post(
            "/api/ingest/snapshot",
            headers=headers,
            content=request["body"],
        )

        body = response.json()
        assert response.status_code == 400
        assert body["status"] == "rejected"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 0


def test_fastapi_post_ingest_snapshot_duplicate_is_idempotent():
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "worldcup.db"
        request = build_ingest_request(
            snapshot=_snapshot(),
            endpoint="https://example.com/api/ingest/snapshot",
            secret="test-hmac-secret",
            timestamp="2026-06-08T00:02:00+00:00",
        )
        app = create_fastapi_app(db_path=db_path, secret="test-hmac-secret")
        client = TestClient(app)

        first = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )
        second = client.post(
            "/api/ingest/snapshot",
            headers=request["headers"],
            content=request["body"],
        )

        assert first.json()["status"] == "stored"
        assert second.json()["status"] == "duplicate"
        assert SQLiteSnapshotStore(db_path).count_snapshots() == 1
```

- [ ] **Step 2: Run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all FastAPI ingest tests pass and the full suite remains green.

- [ ] **Step 3: Commit ingest route tests**

Run:

```bash
git add tests/test_fastapi_app.py
git commit -m "test: cover fastapi ingest contract"
```

## Task 5: Add Store Protocol Boundary

**Files:**
- Create: `worldcup/store_contract.py`
- Modify: `worldcup/store.py`
- Create: `tests/test_store_contract.py`

- [ ] **Step 1: Write failing store protocol test**

Create `tests/test_store_contract.py` with:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from worldcup.store import SQLiteSnapshotStore
from worldcup.store_contract import SnapshotStore


def test_sqlite_store_satisfies_snapshot_store_protocol():
    with TemporaryDirectory() as tmp:
        store = SQLiteSnapshotStore(Path(tmp) / "worldcup.db")

        assert isinstance(store, SnapshotStore)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: fails because `worldcup.store_contract` does not exist.

- [ ] **Step 3: Create store protocol**

Create `worldcup/store_contract.py` with:

```python
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SnapshotStore(Protocol):
    def put_snapshot(
        self,
        idempotency_key: str,
        payload: dict[str, Any],
        stored_at: str | None = None,
    ) -> dict[str, Any]:
        pass

    def count_snapshots(self) -> int:
        pass

    def latest_snapshot(self) -> dict[str, Any] | None:
        pass
```

- [ ] **Step 4: Make SQLite store explicitly implement protocol**

In `worldcup/store.py`, add this import near the existing imports:

```python
from worldcup.store_contract import SnapshotStore
```

Change the class definition from:

```python
class SQLiteSnapshotStore:
```

to:

```python
class SQLiteSnapshotStore(SnapshotStore):
```

- [ ] **Step 5: Run tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit store boundary**

Run:

```bash
git add worldcup/store_contract.py worldcup/store.py tests/test_store_contract.py
git commit -m "refactor: define snapshot store protocol"
```

## Task 6: Update Readiness and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/data-contract.md`
- Modify: `docs/ops/local-to-cloud-checklist.md`
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Update README**

In `README.md`, update current status to include:

```text
Plan 3A FastAPI local adapter is implemented and tested.
```

Add local run command:

```bash
python3 -m worldcup.fastapi_app --host 127.0.0.1 --port 8788 --db data/local/worldcup.db --env .env
```

Add the safety note:

```text
The FastAPI app is local-only until ECS deployment is explicitly confirmed.
```

- [ ] **Step 2: Update data contract**

In `docs/superpowers/data-contract.md`, add a `FastAPI adapter` subsection under local route contract:

```markdown
### FastAPI adapter

`worldcup.fastapi_app.create_fastapi_app` exposes the same local route contract as `worldcup.http_app` and delegates security-sensitive behavior to existing modules. It must not reimplement HMAC verification, idempotency, query projection, or preview rendering.

The adapter is local-only until ECS deployment is separately confirmed.
```

- [ ] **Step 3: Update cloud checklist**

In `docs/ops/local-to-cloud-checklist.md`, add:

```markdown
## Local FastAPI Smoke

1. Start local FastAPI only after `.env` readiness is green.
2. Check `GET /healthz`.
3. Check `GET /api/matches` contains no stake or bet amount fields.
4. Check `GET /preview` contains the research disclaimer.
5. Stop the local process before deploying or changing cloud resources.
```

- [ ] **Step 4: Update RECENT_WORK**

Add a new 2026-06-09 entry:

```markdown
- Implemented Plan 3A local FastAPI adapter over the existing route contract; no ECS deployment, push, live API call, or online write was performed.
- Added `SnapshotStore` protocol to preserve SQLite behavior and prepare for PostgreSQL Plan 3B.
- Local validation: `tests/run_tests.py` and `worldcup.readiness` pass.
```

- [ ] **Step 5: Commit docs**

Run:

```bash
git add README.md docs/superpowers/data-contract.md docs/ops/local-to-cloud-checklist.md RECENT_WORK.md
git commit -m "docs: record fastapi local api workflow"
```

## Task 7: Final Verification

**Files:**
- Verify entire repository state.

- [ ] **Step 1: Run full tests**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 2: Run readiness**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.readiness --root .
```

Expected:

```text
"ok": true
```

- [ ] **Step 3: Confirm dry-run behavior remains dry**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.refresh_runner
```

Expected:

```text
dry-run only: pass --live to call external sources and consume quota
```

- [ ] **Step 4: Check formatting and staged safety**

Run:

```bash
git diff --check
git status --short --branch
git check-ignore -v .env data/cache/analysis_snapshot.json data/local/worldcup.db data/probe/openfootball_2026.json
```

Expected:

- `git diff --check` exits `0`.
- `.env`, `data/cache/`, `data/local/`, and `data/probe/` are ignored.
- `.env.example` is not ignored.

- [ ] **Step 5: Scan for local secrets**

Run:

```bash
key="$(awk -F= '/^THE_ODDS_API_KEY=/{print $2; exit}' .env | tr -d "\"'")"
if [ -n "$key" ]; then
  rg -l --fixed-strings "$key" --glob '!.env' --glob '!data/probe/**' --glob '!data/cache/**' --glob '!data/local/**' .
fi
```

Expected: no output.

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path
from subprocess import run, PIPE

secret = None
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    if line.startswith("INGEST_HMAC_SECRET="):
        secret = line.split("=", 1)[1].strip()
        break
if not secret:
    raise SystemExit("missing INGEST_HMAC_SECRET")
proc = run(
    [
        "rg",
        "-l",
        "--fixed-strings",
        secret,
        "--glob",
        "!.env",
        "--glob",
        "!data/probe/**",
        "--glob",
        "!data/cache/**",
        "--glob",
        "!data/local/**",
        ".",
    ],
    text=True,
    stdout=PIPE,
    stderr=PIPE,
)
if proc.stdout.strip():
    print(proc.stdout, end="")
    raise SystemExit(1)
print("hmac_secret_not_found_outside_ignored_files")
PY
```

Expected:

```text
hmac_secret_not_found_outside_ignored_files
```

- [ ] **Step 6: Final local commit if needed**

If there are any remaining tracked changes after Tasks 1-6, commit them:

```bash
git add -A
git diff --cached --name-only
git commit -m "feat: add local fastapi ecs api adapter"
```

Before committing, confirm the cached file list does not contain `.env`, `data/cache/`, `data/local/`, or `data/probe/`.
