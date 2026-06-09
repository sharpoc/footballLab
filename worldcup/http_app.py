from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from worldcup.ingest_app import process_local_ingest
from worldcup.preview import build_preview_html
from worldcup.query import load_latest_snapshot, project_match_rows
from worldcup.refresh_runner import _load_env
from worldcup.store_contract import SnapshotStore


def _json_response(status: int, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "headers": {"Content-Type": "application/json"},
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


def handle_request(
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: str,
    db_path: str | Path,
    secret: str,
    now: str | None = None,
    store: SnapshotStore | None = None,
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
        return _json_response(200 if result["status"] != "rejected" else 400, result)

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

    if method_upper == "GET" and route == "/preview":
        snapshot = _latest_or_404(db_path, store=store)
        if snapshot is None:
            return _html_response(404, "<!doctype html><title>Not Found</title><p>snapshot_not_found</p>")
        return _html_response(200, build_preview_html(snapshot))

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
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            self._send(
                handle_request(
                    method="POST",
                    path=self.path,
                    headers=dict(self.headers.items()),
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
