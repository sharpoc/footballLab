from __future__ import annotations

from pathlib import Path
from typing import Any

from worldcup.http_app import handle_request


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers", []):
        key = raw_key.decode("latin-1")
        value = raw_value.decode("latin-1")
        headers[key] = value
    return headers


async def _read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def create_asgi_app(db_path: str | Path, secret: str):
    async def app(scope, receive, send):
        if scope["type"] != "http":
            raise RuntimeError(f"Unsupported ASGI scope type: {scope['type']}")

        body_bytes = await _read_body(receive)
        path = scope.get("path", "/")
        query = scope.get("query_string", b"")
        if query:
            path = f"{path}?{query.decode('latin-1')}"

        response = handle_request(
            method=scope.get("method", "GET"),
            path=path,
            headers=_headers_from_scope(scope),
            body=body_bytes.decode("utf-8"),
            db_path=db_path,
            secret=secret,
        )
        response_body = response["body"].encode("utf-8")
        headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in response["headers"].items()
        ]
        headers.append((b"content-length", str(len(response_body)).encode("latin-1")))
        await send({"type": "http.response.start", "status": response["status"], "headers": headers})
        await send({"type": "http.response.body", "body": response_body, "more_body": False})

    return app
