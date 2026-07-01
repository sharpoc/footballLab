from __future__ import annotations

import argparse
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from worldcup.ingest_app import process_local_ingest
from worldcup.preview import build_preview_html
from worldcup.query import (
    load_latest_snapshot,
    load_recent_snapshots,
    project_finished_rows,
    project_match_rows,
)
from worldcup.refresh_runner import _load_env
from worldcup.store_contract import SnapshotStore


DEFAULT_MAX_INGEST_BODY_BYTES = 5_000_000
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_AUTH_REJECTION_REASONS = {
    "signature_format_invalid",
    "signature_mismatch",
}


def _json_response(
    status: int,
    data: dict[str, Any],
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(dict(extra_headers))
    return {
        "status": status,
        "headers": headers,
        "body": json.dumps(data, ensure_ascii=False, sort_keys=True),
    }


def _html_response(status: int, body: str) -> dict[str, Any]:
    return {
        "status": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def _latest_or_404(db_path: str | Path, store: SnapshotStore | None = None) -> dict[str, Any] | None:
    return load_latest_snapshot(db_path, store=store)


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _request_id(headers: Mapping[str, str]) -> str:
    normalized = _normalize_headers(headers)
    candidate = normalized.get("x-request-id", "").strip()
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _is_json_content_type(value: str | None) -> bool:
    if not value:
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


def _content_length(headers: Mapping[str, str]) -> int | None:
    normalized = _normalize_headers(headers)
    value = normalized.get("content-length")
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("invalid_content_length") from exc
    if parsed < 0:
        raise ValueError("invalid_content_length")
    return parsed


def _ingest_headers(request_id: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Request-Id": request_id,
    }


def _ingest_error_response(status: int, code: str, request_id: str) -> dict[str, Any]:
    return _json_response(
        status,
        {
            "error": {
                "code": code,
                "request_id": request_id,
            }
        },
        extra_headers=_ingest_headers(request_id),
    )


def _ingest_rejection_status(reason: str) -> int:
    if reason in _AUTH_REJECTION_REASONS:
        return 401
    return 400


def handle_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    db_path: str | Path,
    secret: str,
    now: str | None = None,
    store: SnapshotStore | None = None,
    max_ingest_body_bytes: int = DEFAULT_MAX_INGEST_BODY_BYTES,
) -> dict[str, Any]:
    route = path.split("?", 1)[0]
    method_upper = method.upper()

    if method_upper == "GET" and route == "/healthz":
        return _json_response(
            200,
            {
                "schema_version": 1,
                "service": "worldcup-analysis",
                "status": "ok",
            },
        )

    if method_upper == "POST" and route == "/api/ingest/snapshot":
        request_id = _request_id(headers)
        normalized_headers = _normalize_headers(headers)
        if not _is_json_content_type(normalized_headers.get("content-type")):
            return _ingest_error_response(415, "unsupported_media_type", request_id)
        try:
            declared_length = _content_length(headers)
        except ValueError:
            return _ingest_error_response(400, "invalid_content_length", request_id)
        if declared_length is not None and declared_length > max_ingest_body_bytes:
            return _ingest_error_response(413, "body_too_large", request_id)
        if len(body.encode("utf-8")) > max_ingest_body_bytes:
            return _ingest_error_response(413, "body_too_large", request_id)

        result = process_local_ingest(
            db_path=db_path,
            method=method_upper,
            path=route,
            headers=headers,
            body=body,
            secret=secret,
            now=now,
            store=store,
        )
        if result["status"] == "rejected":
            return _ingest_error_response(
                _ingest_rejection_status(result["reason"]),
                result["reason"],
                request_id,
            )
        response_body = dict(result)
        response_body["request_id"] = request_id
        return _json_response(200, response_body, extra_headers=_ingest_headers(request_id))

    if method_upper == "GET" and route == "/api/snapshot/latest":
        snapshot = _latest_or_404(db_path, store=store)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"snapshot": snapshot})

    if method_upper == "GET" and route == "/api/matches":
        snapshot = _latest_or_404(db_path, store=store)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"matches": project_match_rows(snapshot)})

    if method_upper == "GET" and route == "/api/finished":
        snapshot = _latest_or_404(db_path, store=store)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"finished": project_finished_rows(snapshot)})

    if method_upper == "GET" and route == "/preview":
        recent = load_recent_snapshots(db_path, store=store, limit=2)
        if not recent:
            return _html_response(404, "<!doctype html><title>Not Found</title><p>snapshot_not_found</p>")
        previous = recent[1] if len(recent) > 1 else None
        return _html_response(200, build_preview_html(recent[0], previous_snapshot=previous))

    return _json_response(404, {"error": "not_found"})


def make_handler(db_path: str | Path, secret: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, response: dict[str, Any]) -> None:
            body_bytes = response["body"].encode("utf-8")
            self.send_response(response["status"])
            for key, value in response["headers"].items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def do_GET(self) -> None:
            self._send(
                handle_request(
                    method="GET",
                    path=self.path,
                    headers=dict(self.headers.items()),
                    body="",
                    db_path=db_path,
                    secret=secret,
                )
            )

        def do_POST(self) -> None:
            headers = dict(self.headers.items())
            request_id = _request_id(headers)
            try:
                length = _content_length(headers) or 0
            except ValueError:
                self._send(_ingest_error_response(400, "invalid_content_length", request_id))
                return
            if length > DEFAULT_MAX_INGEST_BODY_BYTES:
                self._send(_ingest_error_response(413, "body_too_large", request_id))
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
            except UnicodeDecodeError:
                self._send(_ingest_error_response(400, "invalid_utf8_body", request_id))
                return
            self._send(
                handle_request(
                    method="POST",
                    path=self.path,
                    headers=headers,
                    body=body,
                    db_path=db_path,
                    secret=secret,
                )
            )

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local preview HTTP adapter.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default="data/local/worldcup.db")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--secret-env", default="INGEST_HMAC_SECRET")
    args = parser.parse_args(argv)

    secret = _load_env(args.env).get(args.secret_env)
    if not secret:
        raise SystemExit(f"{args.secret_env} is missing in {args.env}")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.db, secret))
    print(f"serving http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
