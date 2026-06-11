from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from worldcup.notifications import (
    build_change_notification,
    load_snapshot_if_exists,
    send_wxpusher_notification,
)
from worldcup.publish import publish_snapshot
from worldcup.refresh_runner import _load_env, refresh_cache_and_build_snapshot
from worldcup.scheduled_refresh import run_scheduled_refresh

DEFAULT_ENDPOINT = "https://example.invalid/api/ingest/snapshot"


def run_scheduled_publish(
    now: str | None = None,
    live: bool = False,
    force: bool = False,
    env_path: str | Path = ".env",
    cache_dir: str | Path = "data/cache",
    snapshot_path: str | Path = "data/cache/analysis_snapshot.json",
    quota_path: str | Path = "data/cache/quota.json",
    endpoint: str = DEFAULT_ENDPOINT,
    api_key: str | None = None,
    secret: str | None = None,
    notify: bool = True,
    refresh_fn: Callable[..., object] = refresh_cache_and_build_snapshot,
    publish_fn: Callable[..., dict] = publish_snapshot,
    notify_fn: Callable[..., dict] = send_wxpusher_notification,
) -> dict:
    env = _load_env(env_path)
    previous_snapshot = load_snapshot_if_exists(snapshot_path) if notify else None
    refresh = run_scheduled_refresh(
        now=now,
        live=live,
        force=force,
        env_path=env_path,
        cache_dir=cache_dir,
        snapshot_path=snapshot_path,
        quota_path=quota_path,
        api_key=api_key,
        refresh_fn=refresh_fn,
    )

    if refresh["status"] != "refreshed":
        return {
            "status": refresh["status"],
            "force": force,
            "refresh": refresh,
            "publish": None,
            "notification": None,
        }

    if int((refresh.get("refresh") or {}).get("matches") or 0) <= 0:
        return {
            "status": "blocked",
            "reason": "empty_refreshed_snapshot",
            "force": force,
            "refresh": refresh,
            "publish": None,
            "notification": None,
        }

    resolved_secret = secret or env.get("INGEST_HMAC_SECRET")
    if not resolved_secret:
        raise ValueError("INGEST_HMAC_SECRET is missing")

    publish = publish_fn(
        snapshot_path=refresh["refresh"]["snapshot_path"],
        endpoint=endpoint,
        secret=resolved_secret,
        timestamp=now,
        live=live,
    )
    notification_result = None
    if notify:
        current_snapshot = load_snapshot_if_exists(refresh["refresh"]["snapshot_path"])
        if current_snapshot is None:
            notification_result = {"status": "skipped", "reason": "missing_current_snapshot"}
        else:
            notification = build_change_notification(previous_snapshot, current_snapshot)
            if notification["should_send"]:
                sent = notify_fn(notification["content"], summary=notification["summary"])
                notification_result = {
                    **sent,
                    "summary": notification["summary"],
                    "item_count": len(notification["items"]),
                }
            else:
                notification_result = {"status": "skipped", "reason": "no_significant_changes"}

    return {
        "status": "published",
        "force": force,
        "refresh": refresh,
        "publish": publish,
        "notification": notification_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run scheduled refresh and publish refreshed snapshots. Defaults to dry-run."
    )
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--snapshot-path", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--quota-path", default="data/cache/quota.json")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--now", default=None)
    parser.add_argument("--live", action="store_true", help="Refresh and publish when due.")
    parser.add_argument("--force", action="store_true", help="With --live, refresh even when not due.")
    parser.add_argument("--no-notify", action="store_true", help="Do not send WxPusher change notifications.")
    args = parser.parse_args(argv)

    result = run_scheduled_publish(
        now=args.now,
        live=args.live,
        force=args.force,
        env_path=args.env,
        cache_dir=args.cache_dir,
        snapshot_path=args.snapshot_path,
        quota_path=args.quota_path,
        endpoint=args.endpoint,
        notify=not args.no_notify,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
