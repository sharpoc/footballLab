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
from worldcup.quota import load_quota_ledger
from worldcup.refresh_runner import _load_env, refresh_cache_and_build_snapshot
from worldcup.scheduled_refresh import run_scheduled_refresh
from worldcup.theoddsapi_keys import (
    LEGACY_PROVIDER,
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    configured_key_slots,
)

DEFAULT_ENDPOINT = "https://example.invalid/api/ingest/snapshot"
QUOTA_ALERT_THRESHOLDS = (100, 30, 10, 0)
_SLOT_LABELS = {
    PRIMARY_PROVIDER: "PRIMARY",
    SECONDARY_PROVIDER: "SECONDARY",
    LEGACY_PROVIDER: "LEGACY",
}


def _watched_providers(env: dict[str, str]) -> list[str]:
    slots = configured_key_slots(env)
    if slots:
        return [slot.provider for slot in slots]
    return [LEGACY_PROVIDER]


def _quota_by_provider(quota_path: str | Path, providers: list[str]) -> dict[str, int | None]:
    try:
        ledger = load_quota_ledger(quota_path).get("providers", {})
    except (OSError, ValueError):
        ledger = {}

    out: dict[str, int | None] = {}
    for provider in providers:
        value = (ledger.get(provider) or {}).get("remaining")
        out[provider] = value if isinstance(value, int) else None
    return out


def _build_quota_alert(
    before: dict[str, int | None],
    after: dict[str, int | None],
) -> dict | None:
    slot_reports = []
    for provider, remaining_after in after.items():
        remaining_before = before.get(provider)
        if remaining_before is None or remaining_after is None:
            continue
        crossed = sorted(
            threshold
            for threshold in QUOTA_ALERT_THRESHOLDS
            if remaining_before > threshold >= remaining_after
        )
        if crossed:
            slot_reports.append(
                {
                    "slot": _SLOT_LABELS.get(provider, provider),
                    "remaining": remaining_after,
                    "thresholds_crossed": crossed,
                }
            )

    if not slot_reports:
        return None

    lines = ["The Odds API 额度告警"]
    for report in slot_reports:
        if 0 in report["thresholds_crossed"]:
            lines.append(
                f"{report['slot']} 槽位已耗尽（剩余 {report['remaining']}），"
                "调度将自动切换备用槽位；全部耗尽时自动刷新暂停。"
            )
        else:
            lines.append(
                f"{report['slot']} 槽位剩余 {report['remaining']}"
                f"（已跌破 {max(report['thresholds_crossed'])}）。"
            )
    lines += [
        "处理：申请新免费 key 替换 .env 中耗尽槽位的",
        "THE_ODDS_API_KEY_PRIMARY / THE_ODDS_API_KEY_SECONDARY，",
        "再经确认执行一次 python3 -m worldcup.scheduled_publish --live --force",
        "让新额度写回 quota 台账（耗尽状态下调度不会自行恢复该槽位）。",
    ]
    lowest = min(report["remaining"] for report in slot_reports)
    return {
        "summary": f"The Odds API 额度告警：最低槽位剩余 {lowest}",
        "content": "\n".join(lines),
        "slots": slot_reports,
    }


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
    env = _load_env(env_path) if live else {}
    watched_providers = _watched_providers(env) if live and notify else []
    quota_before = _quota_by_provider(quota_path, watched_providers) if live and notify else {}
    previous_snapshot = load_snapshot_if_exists(snapshot_path) if live and notify else None
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
            "quota_alert": None,
        }

    if int((refresh.get("refresh") or {}).get("matches") or 0) <= 0:
        return {
            "status": "blocked",
            "reason": "empty_refreshed_snapshot",
            "force": force,
            "refresh": refresh,
            "publish": None,
            "notification": None,
            "quota_alert": None,
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

    quota_alert_result = None
    if notify:
        alert = _build_quota_alert(
            quota_before, _quota_by_provider(quota_path, watched_providers)
        )
        if alert is not None:
            sent = notify_fn(alert["content"], summary=alert["summary"])
            quota_alert_result = {**sent, "slots": alert["slots"]}

    return {
        "status": "published",
        "force": force,
        "refresh": refresh,
        "publish": publish,
        "notification": notification_result,
        "quota_alert": quota_alert_result,
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
