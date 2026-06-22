"""Refresh FIFA public official lineups into the local lineup cache.

Default mode is dry-run: no network access, no writes, no notifications.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from worldcup.collectors.fifa_lineups import PROVIDER, parse_fifa_live_match
from worldcup.notifications import send_wxpusher_notification
from worldcup.sources.fifa_lineups import (
    DEFAULT_COMPETITION_ID,
    DEFAULT_SEASON_ID,
    fetch_fifa_calendar_matches,
    fetch_fifa_live_match,
)

DEFAULT_OUT_PATH = "data/cache/lineups_wc2026.json"
DEFAULT_NOTIFICATION_STATE_PATH = "data/local/lineups_missing_notifications.json"


def _parse_utc(value: str | None) -> datetime:
    if value:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _write_json(path: str | Path, payload: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_existing_matches(path: str | Path) -> list[dict[str, Any]]:
    cache_path = Path(path)
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    matches = payload.get("matches") if isinstance(payload, dict) else []
    if not isinstance(matches, list):
        return []
    return [match for match in matches if isinstance(match, dict)]


def _match_cache_key(match: dict[str, Any]) -> tuple[Any, ...]:
    source_match_no = match.get("source_match_no")
    if source_match_no not in (None, ""):
        return ("source_match_no", source_match_no)
    return (
        "teams",
        match.get("kickoff_at_utc"),
        match.get("home_team"),
        match.get("away_team"),
    )


def _merge_cache_matches(existing: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for match in existing:
        if match.get("confirmed_starting_xi") is True:
            merged[_match_cache_key(match)] = match
    for match in current:
        merged[_match_cache_key(match)] = match
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("kickoff_at_utc") or ""),
    )


def _confirmed_cache_keys(matches: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    return {
        _match_cache_key(match)
        for match in matches
        if match.get("confirmed_starting_xi") is True
    }


def _load_notification_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    if not state_path.exists():
        return {"sent": {}}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sent": {}}
    sent = payload.get("sent") if isinstance(payload, dict) else {}
    return {"sent": sent if isinstance(sent, dict) else {}}


def _lineup_player(player) -> dict[str, Any]:
    out = {"name": player.name}
    if player.position is not None:
        out["position"] = player.position
    if player.player_id is not None:
        out["player_id"] = player.player_id
    return out


def _context_to_cache_match(context) -> dict[str, Any]:
    return {
        "source_match_no": context.source_match_no,
        "kickoff_at_utc": context.kickoff_at_utc.isoformat() if context.kickoff_at_utc else None,
        "home_team": context.home_team_name,
        "away_team": context.away_team_name,
        "source": context.source,
        "provider": context.provider,
        "confirmed_starting_xi": context.confirmed_starting_xi,
        "lineup_confirmed_at": context.lineup_confirmed_at.isoformat()
        if context.lineup_confirmed_at
        else None,
        "lineup_confidence": context.lineup_confidence,
        "home": {
            "team": context.home_team_name,
            "formation": context.home_formation,
            "starting": [_lineup_player(player) for player in context.home_starting],
            "bench": [_lineup_player(player) for player in context.home_bench],
            "absent": [],
            "impact": {"attack_delta": 0.0, "defense_delta": 0.0, "goalkeeper_delta": 0.0},
        },
        "away": {
            "team": context.away_team_name,
            "formation": context.away_formation,
            "starting": [_lineup_player(player) for player in context.away_starting],
            "bench": [_lineup_player(player) for player in context.away_bench],
            "absent": [],
            "impact": {"attack_delta": 0.0, "defense_delta": 0.0, "goalkeeper_delta": 0.0},
        },
    }


def _calendar_match_ids(match: dict[str, Any]) -> tuple[str, str, str, str] | None:
    values = (
        match.get("IdCompetition"),
        match.get("IdSeason"),
        match.get("IdStage"),
        match.get("IdMatch"),
    )
    if any(value in (None, "") for value in values):
        return None
    return tuple(str(value) for value in values)  # type: ignore[return-value]


def _missing_alerts(missing: list[dict[str, Any]], now: datetime, window_minutes: int) -> list[dict[str, Any]]:
    alerts = []
    for item in missing:
        kickoff_raw = item.get("kickoff_at_utc")
        if not kickoff_raw:
            continue
        try:
            kickoff = _parse_utc(str(kickoff_raw))
        except ValueError:
            continue
        minutes = (kickoff - now).total_seconds() / 60
        if 0 <= minutes <= window_minutes:
            alerts.append({**item, "minutes_to_kickoff": round(minutes)})
    return alerts


def _send_missing_notification(
    alerts: list[dict[str, Any]],
    *,
    now: datetime,
    state_path: str | Path,
    notify_fn: Callable[..., dict],
) -> dict[str, Any]:
    if not alerts:
        return {"status": "skipped", "reason": "no_missing_lineups_in_window"}

    state = _load_notification_state(state_path)
    sent = state["sent"]
    fresh = []
    for item in alerts:
        key = f"{PROVIDER}:{item.get('fifa_match_id')}"
        if key not in sent:
            fresh.append((key, item))

    if not fresh:
        return {"status": "skipped", "reason": "already_notified"}

    lines = ["世界杯官方首发未抓到", f"检查时间：{now.isoformat()}", ""]
    for _, item in fresh:
        lines.append(
            f"{item.get('home_team')} vs {item.get('away_team')} "
            f"开赛前约 {item.get('minutes_to_kickoff')} 分钟仍未返回两队 11 人首发。"
        )
        lines.append(f"FIFA match id: {item.get('fifa_match_id')}")
    summary = f"世界杯官方首发未抓到：{len(fresh)} 场"
    result = notify_fn("\n".join(lines).strip(), summary=summary)
    for key, _ in fresh:
        sent[key] = now.isoformat()
    _write_json(state_path, {"sent": sent})
    return {**result, "summary": summary, "match_count": len(fresh)}


def run_lineups_refresh(
    *,
    live: bool,
    write: bool = False,
    notify: bool = False,
    now: str | None = None,
    out_path: str | Path = DEFAULT_OUT_PATH,
    notification_state_path: str | Path = DEFAULT_NOTIFICATION_STATE_PATH,
    lookahead_hours: int = 24,
    missing_notify_minutes: int = 35,
    id_competition: str = DEFAULT_COMPETITION_ID,
    id_season: str = DEFAULT_SEASON_ID,
    transport: Callable[[str], Any] | None = None,
    notify_fn: Callable[..., dict] = send_wxpusher_notification,
) -> dict[str, Any]:
    observed = _parse_utc(now)
    if not live:
        return {
            "status": "dry_run",
            "note": "pass --live to fetch FIFA public official lineups",
            "would_write": str(out_path) if write else None,
            "notification": None,
        }

    from_date = observed.date().isoformat()
    to_date = (observed + timedelta(hours=lookahead_hours)).date().isoformat()
    calendar = fetch_fifa_calendar_matches(
        from_date=from_date,
        to_date=to_date,
        id_competition=id_competition,
        id_season=id_season,
        transport=transport,
    ).json_body

    confirmed_contexts = []
    missing = []
    source_errors = []
    for match in calendar.get("Results") or []:
        if not isinstance(match, dict):
            continue
        ids = _calendar_match_ids(match)
        if ids is None:
            continue
        id_comp, season, stage, match_id = ids
        try:
            live_match = fetch_fifa_live_match(
                id_competition=id_comp,
                id_season=season,
                id_stage=stage,
                id_match=match_id,
                transport=transport,
            ).json_body
            context = parse_fifa_live_match(live_match, fetched_at=observed)
        except Exception as exc:  # pragma: no cover - exercised through caller summaries.
            source_errors.append({"match_id": match_id, "error": type(exc).__name__})
            continue
        if context.confirmed_starting_xi:
            confirmed_contexts.append(context)
        else:
            missing.append(
                {
                    "fifa_match_id": match_id,
                    "kickoff_at_utc": context.kickoff_at_utc.isoformat()
                    if context.kickoff_at_utc
                    else match.get("Date"),
                    "home_team": context.home_team_name,
                    "away_team": context.away_team_name,
                    "reason": "official_lineup_not_available",
                }
            )

    existing_matches = _load_existing_matches(out_path)
    current_matches = [_context_to_cache_match(context) for context in confirmed_contexts]
    existing_confirmed_keys = _confirmed_cache_keys(existing_matches)
    current_confirmed_keys = _confirmed_cache_keys(current_matches)
    newly_confirmed_keys = current_confirmed_keys - existing_confirmed_keys
    payload = {
        "schema_version": 1,
        "provider": PROVIDER,
        "source": "fifa_live_football",
        "generated_at": observed.isoformat(),
        "matches": _merge_cache_matches(
            existing_matches if write else [],
            current_matches,
        ),
    }
    if write:
        _write_json(out_path, payload)

    alerts = _missing_alerts(missing, observed, missing_notify_minutes)
    notification = None
    if notify:
        notification = _send_missing_notification(
            alerts,
            now=observed,
            state_path=notification_state_path,
            notify_fn=notify_fn,
        )

    return {
        "status": "captured",
        "provider": PROVIDER,
        "observed_at": observed.isoformat(),
        "matches_checked": len(confirmed_contexts) + len(missing) + len(source_errors),
        "confirmed": len(confirmed_contexts),
        "newly_confirmed": len(newly_confirmed_keys),
        "missing": len(missing),
        "missing_alerts": len(alerts),
        "source_errors": source_errors,
        "out": str(out_path) if write else None,
        "notification": notification,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh official FIFA public lineups into data/cache/lineups_wc2026.json."
    )
    parser.add_argument("--live", action="store_true", help="Fetch FIFA public API for real.")
    parser.add_argument("--write", action="store_true", help="Write confirmed lineups to cache.")
    parser.add_argument("--notify", action="store_true", help="Notify once when lineups are missing near kickoff.")
    parser.add_argument("--now", default=None)
    parser.add_argument("--out", default=DEFAULT_OUT_PATH)
    parser.add_argument("--notification-state-path", default=DEFAULT_NOTIFICATION_STATE_PATH)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    parser.add_argument("--missing-notify-minutes", type=int, default=35)
    args = parser.parse_args(argv)

    result = run_lineups_refresh(
        live=args.live,
        write=args.write,
        notify=args.notify,
        now=args.now,
        out_path=args.out,
        notification_state_path=args.notification_state_path,
        lookahead_hours=args.lookahead_hours,
        missing_notify_minutes=args.missing_notify_minutes,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
