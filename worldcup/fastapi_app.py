from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from worldcup.http_app import handle_request
from worldcup.refresh_runner import _load_env
from worldcup.store_contract import SnapshotStore


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
    store: SnapshotStore | None = None,
) -> Response:
    result = handle_request(
        method=method,
        path=path,
        headers=_headers(request),
        body=body,
        db_path=db_path,
        secret=secret,
        store=store,
    )
    return _response(result)


def create_fastapi_app(
    db_path: str | Path = "data/local/worldcup.db",
    secret: str = "",
    store: SnapshotStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Worldcup Analysis API", version="0.1.0")

    @app.get("/healthz")
    async def healthz(request: Request) -> Response:
        return await _dispatch(request, "GET", "/healthz", db_path, secret, store=store)

    @app.get("/api/snapshot/latest")
    async def latest_snapshot(request: Request) -> Response:
        return await _dispatch(request, "GET", "/api/snapshot/latest", db_path, secret, store=store)

    @app.get("/api/matches")
    async def matches(request: Request) -> Response:
        return await _dispatch(request, "GET", "/api/matches", db_path, secret, store=store)

    @app.get("/preview")
    async def preview(request: Request) -> Response:
        return await _dispatch(request, "GET", "/preview", db_path, secret, store=store)

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
            store=store,
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
