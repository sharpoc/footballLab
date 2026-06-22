"""Local audit for official lineup capture and post-information odds usage.

Reads local cache/history files only. It does not fetch live sources, refresh
odds, publish snapshots, or change LaunchAgent state.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from worldcup.collectors.team_aliases import canonicalize_team
from worldcup.notifications import send_wxpusher_notification

DEFAULT_LINEUPS_PATH = "data/cache/lineups_wc2026.json"
DEFAULT_SNAPSHOT_PATH = "data/cache/analysis_snapshot.json"
DEFAULT_HISTORY_DIR = "data/local/history"
DEFAULT_NOTIFICATION_STATE_PATH = "data/local/lineups_missing_notifications.json"
DEFAULT_OUT_PATH = "data/local/diagnostics/lineup_audit.json"
RESEARCH_BOUNDARY = "仅用于研究分析，不构成投注建议"
ACTIONABLE_AUDIT_FLAGS = {
    "captured_without_snapshot_input",
    "captured_without_post_information_odds",
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_json_if_exists(path: str | Path) -> Any | None:
    candidate = Path(path)
    if not candidate.exists() or candidate.is_dir():
        return None
    try:
        return _read_json(candidate)
    except (OSError, ValueError):
        return None


def _parse_optional_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _team_key(value: Any) -> str:
    return canonicalize_team(str(value or "").strip())


def _match_key(home: Any, away: Any, kickoff: Any) -> tuple[str, str, str]:
    kickoff_dt = _parse_optional_utc(kickoff)
    return (
        _team_key(home),
        _team_key(away),
        _iso(kickoff_dt) or str(kickoff or ""),
    )


def _cache_match_key(match: dict[str, Any]) -> tuple[str, str, str]:
    return _match_key(match.get("home_team"), match.get("away_team"), match.get("kickoff_at_utc"))


def _snapshot_match_key(match: dict[str, Any]) -> tuple[str, str, str]:
    home = match.get("home_canonical") or match.get("home_team")
    away = match.get("away_canonical") or match.get("away_team")
    return _match_key(home, away, match.get("kickoff_at_utc"))


def _load_history(history_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(history_dir)
    if not root.exists() or not root.is_dir():
        return []
    snapshots: list[dict[str, Any]] = []
    for path in sorted(root.glob("snapshot_*.json")):
        payload = _read_json_if_exists(path)
        if isinstance(payload, dict):
            snapshots.append(payload)
    return snapshots


def _load_snapshots(snapshot_path: str | Path | None, history_dir: str | Path | None) -> list[dict[str, Any]]:
    snapshots = _load_history(history_dir) if history_dir is not None else []
    latest = _read_json_if_exists(snapshot_path) if snapshot_path is not None else None
    if isinstance(latest, dict):
        latest_at = latest.get("snapshot_at")
        if not any(snapshot.get("snapshot_at") == latest_at for snapshot in snapshots):
            snapshots.append(latest)
    return sorted(
        (snapshot for snapshot in snapshots if isinstance(snapshot, dict)),
        key=lambda snapshot: str(snapshot.get("snapshot_at") or ""),
    )


def _matching_snapshot_entries(
    snapshots: list[dict[str, Any]],
    lineup_match: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    key = _cache_match_key(lineup_match)
    entries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for snapshot in snapshots:
        for match in snapshot.get("matches") or []:
            if isinstance(match, dict) and _snapshot_match_key(match) == key:
                entries.append((snapshot, match))
    return entries


def _first_lineup_shadow_entry(
    entries: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    for snapshot, match in entries:
        shadow = ((match.get("model") or {}).get("lineup_shadow") or {})
        if isinstance(shadow, dict) and shadow:
            return snapshot, match, shadow
    return None


def _first_post_information_entry(
    entries: list[tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    for snapshot, match in entries:
        shadow = ((match.get("model") or {}).get("lineup_shadow") or {})
        if isinstance(shadow, dict) and shadow.get("post_information_odds_available") is True:
            return snapshot, match, shadow
    return None


def _strong_signal_count(match: dict[str, Any]) -> int:
    return sum(1 for signal in match.get("signals") or [] if signal.get("grade") in ("S", "A"))


def _starting_count(match: dict[str, Any], side: str) -> int:
    block = match.get(side) if isinstance(match.get(side), dict) else {}
    starting = block.get("starting")
    return len(starting) if isinstance(starting, list) else 0


def _lineup_row(lineup_match: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    kickoff = _parse_optional_utc(lineup_match.get("kickoff_at_utc"))
    confirmed_at = _parse_optional_utc(lineup_match.get("lineup_confirmed_at"))
    minutes_before = None
    if kickoff is not None and confirmed_at is not None:
        minutes_before = round((kickoff - confirmed_at).total_seconds() / 60)

    entries = _matching_snapshot_entries(snapshots, lineup_match)
    first_shadow = _first_lineup_shadow_entry(entries)
    first_post = _first_post_information_entry(entries)
    shadow_snapshot, shadow_match, shadow = first_shadow if first_shadow is not None else ({}, {}, {})
    post_snapshot, post_match, post_shadow = first_post if first_post is not None else ({}, {}, {})

    entered_snapshot = first_shadow is not None
    post_information = first_post is not None
    issue_flags: list[str] = []
    if not entered_snapshot:
        issue_flags.append("captured_without_snapshot_input")
    if not post_information:
        issue_flags.append("captured_without_post_information_odds")
    if minutes_before is not None and minutes_before < 0:
        issue_flags.append("captured_after_kickoff")

    return {
        "source_match_no": lineup_match.get("source_match_no"),
        "kickoff_at_utc": _iso(kickoff),
        "home_team": lineup_match.get("home_team"),
        "away_team": lineup_match.get("away_team"),
        "match_label": f"{lineup_match.get('home_team')} vs {lineup_match.get('away_team')}",
        "provider": lineup_match.get("provider"),
        "confirmed_starting_xi": lineup_match.get("confirmed_starting_xi") is True,
        "lineup_confirmed_at": _iso(confirmed_at),
        "minutes_before_kickoff": minutes_before,
        "captured_before_kickoff": bool(minutes_before is not None and minutes_before >= 0),
        "starting_counts": {
            "home": _starting_count(lineup_match, "home"),
            "away": _starting_count(lineup_match, "away"),
        },
        "entered_snapshot": entered_snapshot,
        "entered_snapshot_at": shadow_snapshot.get("snapshot_at"),
        "post_information_odds_available": post_information,
        "post_information_odds_snapshot_at": post_snapshot.get("snapshot_at"),
        "odds_observed_at": post_shadow.get("odds_observed_at") or shadow.get("odds_observed_at"),
        "latest_matched_snapshot_at": entries[-1][0].get("snapshot_at") if entries else None,
        "latest_matched_odds_updated_at": entries[-1][1].get("odds_updated_at") if entries else None,
        "strong_signal_count": _strong_signal_count(post_match or shadow_match),
        "issue_flags": issue_flags,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "confirmed_lineups": len(rows),
        "captured_before_kickoff": sum(1 for row in rows if row["captured_before_kickoff"]),
        "entered_snapshot": sum(1 for row in rows if row["entered_snapshot"]),
        "post_information_odds_available": sum(
            1 for row in rows if row["post_information_odds_available"]
        ),
        "captured_without_snapshot_input": sum(
            1 for row in rows if "captured_without_snapshot_input" in row["issue_flags"]
        ),
        "captured_without_post_information_odds": sum(
            1 for row in rows if "captured_without_post_information_odds" in row["issue_flags"]
        ),
    }


def _notification_summary(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "sent_count": 0, "latest_sent_at": None, "keys": []}
    payload = _read_json_if_exists(path)
    sent = payload.get("sent") if isinstance(payload, dict) else {}
    if not isinstance(sent, dict):
        sent = {}
    values = sorted(str(value) for value in sent.values() if value)
    return {
        "path": str(path),
        "sent_count": len(sent),
        "latest_sent_at": values[-1] if values else None,
        "keys": sorted(str(key) for key in sent.keys()),
    }


def build_lineup_audit(
    *,
    lineups_path: str | Path = DEFAULT_LINEUPS_PATH,
    snapshot_path: str | Path | None = DEFAULT_SNAPSHOT_PATH,
    history_dir: str | Path | None = DEFAULT_HISTORY_DIR,
    notification_state_path: str | Path | None = DEFAULT_NOTIFICATION_STATE_PATH,
    generated_at: str | None = None,
) -> dict[str, Any]:
    lineups = _read_json_if_exists(lineups_path)
    matches = lineups.get("matches") if isinstance(lineups, dict) else []
    confirmed = [
        match
        for match in matches or []
        if isinstance(match, dict) and match.get("confirmed_starting_xi") is True
    ]
    snapshots = _load_snapshots(snapshot_path, history_dir)
    rows = sorted(
        (_lineup_row(match, snapshots) for match in confirmed),
        key=lambda row: (str(row.get("kickoff_at_utc") or ""), str(row.get("match_label") or "")),
    )
    return {
        "schema_version": 1,
        "generated_at": generated_at or _now_utc_iso(),
        "research_boundary": RESEARCH_BOUNDARY,
        "inputs": {
            "lineups_path": str(lineups_path),
            "snapshot_path": str(snapshot_path) if snapshot_path is not None else None,
            "history_dir": str(history_dir) if history_dir is not None else None,
            "notification_state_path": str(notification_state_path)
            if notification_state_path is not None
            else None,
            "snapshots_scanned": len(snapshots),
        },
        "summary": _summary(rows),
        "notifications": _notification_summary(notification_state_path),
        "matches": rows,
    }


def write_lineup_audit(report: dict[str, Any], out_path: str | Path) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_audit_notification_state(path: str | Path) -> dict[str, Any]:
    payload = _read_json_if_exists(path)
    if not isinstance(payload, dict):
        payload = {}
    sent = payload.get("sent")
    audit_sent = payload.get("audit_sent")
    return {
        "sent": sent if isinstance(sent, dict) else {},
        "audit_sent": audit_sent if isinstance(audit_sent, dict) else {},
    }


def _write_audit_notification_state(path: str | Path, state: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _actionable_audit_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in report.get("matches") or []:
        if not isinstance(row, dict):
            continue
        minutes = row.get("minutes_before_kickoff")
        if not isinstance(minutes, (int, float)) or minutes < 0:
            continue
        flags = set(str(flag) for flag in row.get("issue_flags") or [])
        if flags & ACTIONABLE_AUDIT_FLAGS:
            rows.append(row)
    return rows


def _audit_notification_key(row: dict[str, Any]) -> str:
    source = row.get("source_match_no") or row.get("match_label") or "unknown"
    kickoff = row.get("kickoff_at_utc") or "unknown"
    flags = ",".join(sorted(set(str(flag) for flag in row.get("issue_flags") or [])))
    return f"lineup_audit:{source}:{kickoff}:{flags}"


def send_lineup_audit_notification(
    report: dict[str, Any],
    *,
    state_path: str | Path = DEFAULT_NOTIFICATION_STATE_PATH,
    notify_fn: Callable[..., dict] = send_wxpusher_notification,
    limit: int = 5,
) -> dict[str, Any]:
    rows = _actionable_audit_rows(report)
    if not rows:
        return {"status": "skipped", "reason": "no_actionable_lineup_audit_issues"}

    state = _load_audit_notification_state(state_path)
    audit_sent = state["audit_sent"]
    fresh = []
    for row in rows:
        key = _audit_notification_key(row)
        if key not in audit_sent:
            fresh.append((key, row))

    if not fresh:
        return {"status": "skipped", "reason": "already_notified"}

    lines = ["世界杯首发链路待处理", f"检查时间：{report.get('generated_at') or _now_utc_iso()}", ""]
    for _, row in fresh[: max(1, limit)]:
        flags = ", ".join(str(flag) for flag in row.get("issue_flags") or [])
        lines.append(
            f"{row.get('match_label')} 开赛前约 {row.get('minutes_before_kickoff')} 分钟仍有链路缺口。"
        )
        lines.append(f"flags: {flags}")
        if row.get("entered_snapshot") is False:
            lines.append("首发尚未进入最新/历史 snapshot。")
        if row.get("post_information_odds_available") is False:
            lines.append("尚未确认首发后的 post-information odds。")
        lines.append("")

    summary = f"世界杯首发链路待处理：{len(fresh)} 场"
    result = notify_fn("\n".join(lines).strip(), summary=summary)
    sent_at = report.get("generated_at") or _now_utc_iso()
    for key, _ in fresh:
        audit_sent[key] = sent_at
    _write_audit_notification_state(state_path, state)
    return {**(result or {}), "summary": summary, "match_count": len(fresh)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit local official lineup capture and snapshot usage.")
    parser.add_argument("--lineups", default=DEFAULT_LINEUPS_PATH)
    parser.add_argument("--snapshot", default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument("--history-dir", default=DEFAULT_HISTORY_DIR)
    parser.add_argument("--notification-state-path", default=DEFAULT_NOTIFICATION_STATE_PATH)
    parser.add_argument("--out", default=DEFAULT_OUT_PATH)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--notify", action="store_true", help="Notify once for pre-kickoff lineup audit gaps.")
    args = parser.parse_args(argv)

    report = build_lineup_audit(
        lineups_path=args.lineups,
        snapshot_path=args.snapshot,
        history_dir=args.history_dir,
        notification_state_path=args.notification_state_path,
        generated_at=args.generated_at,
    )
    write_lineup_audit(report, args.out)
    notification = None
    if args.notify:
        notification = send_lineup_audit_notification(
            report,
            state_path=args.notification_state_path,
        )
    print(
        json.dumps(
            {"status": "ok", "out": args.out, "summary": report["summary"], "notification": notification},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
