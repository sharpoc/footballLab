from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from worldcup.ingest_app import process_local_ingest
from worldcup.preview import build_preview_html
from worldcup.export import build_public_snapshot
from worldcup.query import (
    load_latest_snapshot,
    load_latest_snapshot_view,
    load_recent_snapshot_views,
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


class SnapshotViewCache:
    """Small process-local cache for expensive multi-snapshot public views."""

    def __init__(self, preview_cache_path: str | Path | None = None) -> None:
        self._lock = Lock()
        self._recent: dict[tuple[str, int | None, int], list[dict[str, Any]]] = {}
        self._preview_html: dict[tuple[str, int | None, int], str] = {}
        self._preview_cache_path = Path(preview_cache_path) if preview_cache_path else None

    def clear(self) -> None:
        with self._lock:
            self._recent.clear()
            self._preview_html.clear()

    def recent_views(
        self,
        db_path: str | Path,
        store: SnapshotStore | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        key = (str(db_path), id(store) if store is not None else None, int(limit))
        with self._lock:
            cached = self._recent.get(key)
            if cached is not None:
                return cached
        computed = load_recent_snapshot_views(db_path, store=store, limit=limit)
        with self._lock:
            cached = self._recent.get(key)
            if cached is None:
                self._recent[key] = computed
                cached = computed
            return cached

    def latest_view(
        self,
        db_path: str | Path,
        store: SnapshotStore | None,
    ) -> dict[str, Any] | None:
        recent = self.recent_views(db_path, store, limit=1)
        return recent[0] if recent else None

    def _preview_meta_path(self) -> Path | None:
        if self._preview_cache_path is None:
            return None
        return self._preview_cache_path.with_suffix(self._preview_cache_path.suffix + ".meta.json")

    def _preview_signature(self, recent: list[dict[str, Any]], time_bucket: int) -> str:
        payload = json.dumps(
            {"recent": recent, "time_bucket": time_bucket},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _preview_time_bucket(self) -> int:
        # A cached page may contain a pick whose odds validity expires without a
        # new ingest. Re-render at least every five minutes so stale picks cannot
        # survive indefinitely in process or disk cache.
        return int(datetime.now(timezone.utc).timestamp() // 300)

    def _read_preview_disk_cache(self, signature: str) -> str | None:
        html_path = self._preview_cache_path
        meta_path = self._preview_meta_path()
        if html_path is None or meta_path is None:
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("signature") != signature:
                return None
            return html_path.read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            return None

    def _write_preview_disk_cache(self, signature: str, html: str) -> None:
        html_path = self._preview_cache_path
        meta_path = self._preview_meta_path()
        if html_path is None or meta_path is None:
            return
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_html = html_path.with_suffix(html_path.suffix + ".tmp")
            tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")
            tmp_html.write_text(html, encoding="utf-8")
            tmp_meta.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "signature": signature,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            tmp_html.replace(html_path)
            tmp_meta.replace(meta_path)
        except OSError:
            return

    def preview_html(
        self,
        db_path: str | Path,
        store: SnapshotStore | None,
    ) -> str | None:
        time_bucket = self._preview_time_bucket()
        key = (str(db_path), id(store) if store is not None else None, time_bucket)
        with self._lock:
            cached = self._preview_html.get(key)
            if cached is not None:
                return cached
        recent = self.recent_views(db_path, store, limit=2)
        if not recent:
            return None
        signature = self._preview_signature(recent, time_bucket)
        disk_cached = self._read_preview_disk_cache(signature)
        if disk_cached is not None:
            with self._lock:
                self._preview_html[key] = disk_cached
            return disk_cached
        previous = recent[1] if len(recent) > 1 else None
        rendered = build_preview_html(recent[0], previous_snapshot=previous)
        self._write_preview_disk_cache(signature, rendered)
        with self._lock:
            cached = self._preview_html.get(key)
            if cached is None:
                self._preview_html[key] = rendered
                cached = rendered
            return cached


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


def _default_preview_cache_path(db_path: str | Path) -> Path:
    return Path(db_path).with_suffix(".preview.html")


def _latest_or_404(db_path: str | Path, store: SnapshotStore | None = None) -> dict[str, Any] | None:
    return load_latest_snapshot(db_path, store=store)


def _latest_view(
    db_path: str | Path,
    store: SnapshotStore | None,
    view_cache: SnapshotViewCache | None,
) -> dict[str, Any] | None:
    if view_cache is not None:
        return view_cache.latest_view(db_path, store)
    return load_latest_snapshot_view(db_path, store=store)


def _recent_views(
    db_path: str | Path,
    store: SnapshotStore | None,
    view_cache: SnapshotViewCache | None,
    limit: int,
) -> list[dict[str, Any]]:
    if view_cache is not None:
        return view_cache.recent_views(db_path, store, limit=limit)
    return load_recent_snapshot_views(db_path, store=store, limit=limit)


def _preview_html(
    db_path: str | Path,
    store: SnapshotStore | None,
    view_cache: SnapshotViewCache | None,
) -> str | None:
    if view_cache is not None:
        return view_cache.preview_html(db_path, store)
    recent = load_recent_snapshot_views(db_path, store=store, limit=2)
    if not recent:
        return None
    previous = recent[1] if len(recent) > 1 else None
    return build_preview_html(recent[0], previous_snapshot=previous)


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
    view_cache: SnapshotViewCache | None = None,
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

    if method_upper == "GET" and route == "/readyz":
        snapshot = _latest_view(db_path, store, view_cache)
        if snapshot is None:
            return _json_response(
                503,
                {
                    "schema_version": 1,
                    "service": "worldcup-analysis",
                    "status": "not_ready",
                },
            )
        return _json_response(
            200,
            {
                "match_count": len(snapshot.get("matches") or []),
                "schema_version": 1,
                "service": "worldcup-analysis",
                "status": "ready",
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
        if view_cache is not None:
            view_cache.clear()
        response_body = dict(result)
        response_body["request_id"] = request_id
        return _json_response(200, response_body, extra_headers=_ingest_headers(request_id))

    if method_upper == "GET" and route == "/api/snapshot/latest":
        snapshot = _latest_or_404(db_path, store=store)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"snapshot": build_public_snapshot(snapshot)})

    if method_upper == "GET" and route == "/api/matches":
        snapshot = _latest_view(db_path, store, view_cache)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"matches": project_match_rows(snapshot)})

    if method_upper == "GET" and route == "/api/finished":
        snapshot = _latest_view(db_path, store, view_cache)
        if snapshot is None:
            return _json_response(404, {"error": "snapshot_not_found"})
        return _json_response(200, {"finished": project_finished_rows(snapshot)})

    if method_upper == "GET" and route == "/preview":
        html = _preview_html(db_path, store, view_cache)
        if html is None:
            return _html_response(404, "<!doctype html><title>Not Found</title><p>snapshot_not_found</p>")
        return _html_response(200, html)

    return _json_response(404, {"error": "not_found"})


def make_handler(db_path: str | Path, secret: str):
    view_cache = SnapshotViewCache(preview_cache_path=_default_preview_cache_path(db_path))

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
                    view_cache=view_cache,
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
                    view_cache=view_cache,
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
