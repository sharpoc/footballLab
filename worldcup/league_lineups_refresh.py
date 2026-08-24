from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from worldcup.collectors.league_fotmob_lineups import parse_confirmed_fotmob_lineups
from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_acceptance import LeagueAcceptanceStore, acceptance_row_is_active
from worldcup.league_lineup_planner import plan_league_lineup_poll
from worldcup.league_lineup_store import LeagueLineupStore
from worldcup.league_team_identity import (
    LeagueTeamIdentityRegistry,
    accepted_league_team_identity_registry,
)
from worldcup.sources.league_fotmob_lineups import fetch_fotmob_calendar, fetch_fotmob_details


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid_datetime") from None
    else:
        raise ValueError("invalid_datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_datetime")
    return parsed.astimezone(timezone.utc)


def _scalar_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text or None


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": reason,
        "skipped": {},
        "rejection_reasons": {},
        "newly_confirmed": {},
        "next_due_at": None,
        "counts": {
            "fixture_count": 0,
            "request_count": 0,
            "calendar_fetch_count": 0,
            "details_fetch_count": 0,
            "accepted_count": 0,
            "newly_confirmed_count": 0,
            "rejection_count": 0,
            "source_failure_count": 0,
            "cache_commit_count": 0,
            "state_commit_count": 0,
        },
    }


def _validate_acceptance(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise ValueError("invalid_acceptance_report")
    competitions = value.get("competitions")
    if not isinstance(competitions, Mapping) or not set(competitions).issubset(FORMAL_SINGLE_MATCH_IDS):
        raise ValueError("invalid_acceptance_report")
    checked: dict[str, Any] = {}
    for competition_id, row in competitions.items():
        if not isinstance(competition_id, str) or not isinstance(row, Mapping):
            raise ValueError("invalid_acceptance_report")
        if row.get("competition_id") != competition_id or not isinstance(row.get("state"), str):
            raise ValueError("invalid_acceptance_report")
        if row.get("state") == "active" and not acceptance_row_is_active(row, competition_id):
            raise ValueError("invalid_acceptance_report")
        checked[competition_id] = dict(row)
    return {"schema_version": 1, "competitions": checked}


def _fixture_text(row: Mapping[str, Any], side: str) -> str:
    for key in (f"{side}_team", f"{side}_team_name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("invalid_fixture_snapshot")


def _validate_fixtures(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping) or not set(value).issubset(FORMAL_SINGLE_MATCH_IDS):
        raise ValueError("invalid_fixture_snapshot")
    checked: dict[str, list[dict[str, Any]]] = {}
    for competition_id, rows in value.items():
        if not isinstance(competition_id, str) or not isinstance(rows, list):
            raise ValueError("invalid_fixture_snapshot")
        projected: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("invalid_fixture_snapshot")
            event_id = _scalar_id(row.get("event_id") or row.get("source_event_id") or row.get("id"))
            if event_id is None:
                raise ValueError("invalid_fixture_snapshot")
            kickoff = _utc(row.get("kickoff_at_utc") or row.get("commence_time"))
            declared = row.get("competition_id")
            if declared not in (None, "", competition_id):
                raise ValueError("invalid_fixture_snapshot")
            fixture = {
                "competition_id": competition_id,
                "event_id": event_id,
                "kickoff_at_utc": kickoff.isoformat(),
                "home_team": _fixture_text(row, "home"),
                "away_team": _fixture_text(row, "away"),
            }
            for name in ("fixture_status", "status", "lineup_status"):
                if name in row:
                    if row[name] is not None and not isinstance(row[name], str):
                        raise ValueError("invalid_fixture_snapshot")
                    fixture[name] = row[name]
            projected.append(fixture)
        checked[competition_id] = projected
    return checked


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "events"}:
        raise ValueError("invalid_lineup_state")
    if value.get("schema_version") != 1 or not isinstance(value.get("events"), Mapping):
        raise ValueError("invalid_lineup_state")
    checked: dict[str, dict[str, Any]] = {}
    for key, row in value["events"].items():
        if not isinstance(key, str) or ":" not in key or not isinstance(row, Mapping):
            raise ValueError("invalid_lineup_state")
        competition_id, event_id = key.split(":", 1)
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or not event_id:
            raise ValueError("invalid_lineup_state")
        confirmed = row.get("confirmed")
        expected = {"last_polled_at", "confirmed"}
        if confirmed is True:
            expected.add("accepted_fingerprint")
        if not isinstance(confirmed, bool) or set(row) != expected:
            raise ValueError("invalid_lineup_state")
        event = {
            "last_polled_at": _utc(row.get("last_polled_at")).isoformat(),
            "confirmed": confirmed,
        }
        if confirmed:
            fingerprint = row.get("accepted_fingerprint")
            if (
                not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
            ):
                raise ValueError("invalid_lineup_state")
            event["accepted_fingerprint"] = fingerprint
        checked[key] = event
    return {"schema_version": 1, "events": {key: checked[key] for key in sorted(checked)}}


def _load_acceptance(root: str | Path) -> dict[str, Any]:
    report = LeagueAcceptanceStore(Path(root) / "data/local/leagues/acceptance.json").read()
    if report is None:
        raise ValueError("invalid_acceptance_report")
    return report


def _load_fixtures(root: str | Path, acceptance_report: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    root_path = Path(root)
    fixtures: dict[str, list[dict[str, Any]]] = {}
    for competition_id in sorted(acceptance_report["competitions"]):
        path = root_path / "data/cache/leagues" / competition_id / "snapshot.json"
        if not path.exists():
            fixtures[competition_id] = []
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ValueError("invalid_fixture_snapshot") from None
        competition = payload.get("competition") if isinstance(payload, Mapping) else None
        rows = payload.get("matches") if isinstance(payload, Mapping) else None
        if not isinstance(competition, Mapping) or competition.get("id") != competition_id or not isinstance(rows, list):
            raise ValueError("invalid_fixture_snapshot")
        fixtures[competition_id] = rows
    return fixtures


def _load_state(root: str | Path) -> dict[str, Any]:
    path = Path(root) / "data/local/leagues/lineup_state.json"
    if not path.exists():
        return {"schema_version": 1, "events": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("invalid_lineup_state") from None


def _provider_ids(
    acceptance_report: Mapping[str, Any],
    explicit: Mapping[str, Any] | None,
) -> dict[str, str]:
    if explicit is not None and (
        not isinstance(explicit, Mapping) or not set(explicit).issubset(FORMAL_SINGLE_MATCH_IDS)
    ):
        raise ValueError("invalid_provider_competition_ids")
    resolved: dict[str, str] = {}
    for competition_id, row in acceptance_report["competitions"].items():
        value = explicit.get(competition_id) if explicit is not None else None
        providers = row.get("providers") if isinstance(row, Mapping) else None
        fotmob = providers.get("fotmob") if isinstance(providers, Mapping) else None
        if value is None and isinstance(fotmob, Mapping):
            value = fotmob.get("competition_id")
        if value is None and isinstance(row, Mapping):
            value = row.get("fotmob_competition_id")
        provider_id = _scalar_id(value)
        if provider_id is not None:
            resolved[competition_id] = provider_id
    return resolved


def _increment(reasons: dict[str, dict[str, int]], competition_id: str, reason: str, count: int = 1) -> None:
    competition = reasons.setdefault(competition_id, {})
    competition[reason] = competition.get(reason, 0) + count


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": row["event_id"],
        "source_match_id": row["source_match_id"],
        "kickoff_at_utc": row["kickoff_at_utc"],
        "fetched_at": row["fetched_at"],
        "lineup_fingerprint": row["lineup_fingerprint"],
    }


def _merge_state_for_poll(
    state: Mapping[str, Any],
    requests: list[Mapping[str, Any]],
    accepted_by_competition: Mapping[str, list[Mapping[str, Any]]],
    now: datetime,
) -> dict[str, Any]:
    events = {key: dict(row) for key, row in state["events"].items()}
    timestamp = now.isoformat()
    for request in requests:
        key = f"{request['competition_id']}:{request['event_id']}"
        events[key] = {"last_polled_at": timestamp, "confirmed": False}
    for competition_id, rows in accepted_by_competition.items():
        for row in rows:
            events[f"{competition_id}:{row['event_id']}"] = {
                "last_polled_at": timestamp,
                "confirmed": True,
                "accepted_fingerprint": row["lineup_fingerprint"],
            }
    return {"schema_version": 1, "events": {key: events[key] for key in sorted(events)}}


def run_league_lineups_refresh(
    *,
    root: str | Path,
    now: Any,
    live: bool = False,
    write: bool = False,
    acceptance_report: Mapping[str, Any] | None = None,
    fixtures_by_competition: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    provider_competition_ids: Mapping[str, Any] | None = None,
    identity_registry: LeagueTeamIdentityRegistry | None = None,
    acceptance_loader: Callable[[str | Path], Any] = _load_acceptance,
    fixtures_loader: Callable[[str | Path, Mapping[str, Any]], Any] = _load_fixtures,
    state_loader: Callable[[str | Path], Any] = _load_state,
    calendar_fetcher: Callable[..., Any] = fetch_fotmob_calendar,
    details_fetcher: Callable[..., Any] = fetch_fotmob_details,
    parser: Callable[..., dict[str, list[dict[str, Any]]]] = parse_confirmed_fotmob_lineups,
    store_factory: Callable[[str | Path], Any] = LeagueLineupStore,
    calendar_transport: Callable[[str], Any] | None = None,
    details_transport: Callable[[str], Any] | None = None,
    env_loader: Callable[..., Any] | None = None,
    notifier: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    del env_loader, notifier
    if live != write:
        return _blocked("live_write_must_be_explicit")
    try:
        now_dt = _utc(now)
    except ValueError:
        return _blocked("invalid_now")
    try:
        acceptance_value = acceptance_loader(root) if acceptance_report is None else acceptance_report
        acceptance = _validate_acceptance(acceptance_value)
    except (OSError, TypeError, ValueError):
        return _blocked("invalid_acceptance_report")
    try:
        fixture_value = fixtures_loader(root, acceptance) if fixtures_by_competition is None else fixtures_by_competition
        fixtures = _validate_fixtures(fixture_value)
    except (OSError, TypeError, ValueError):
        return _blocked("invalid_fixture_snapshot")
    try:
        state_value = state_loader(root) if state is None else state
        poll_state = _validate_state(state_value)
    except (OSError, TypeError, ValueError):
        return _blocked("invalid_lineup_state")
    try:
        provider_ids = _provider_ids(acceptance, provider_competition_ids)
    except (TypeError, ValueError):
        return _blocked("invalid_provider_competition_ids")

    plan = plan_league_lineup_poll(
        now=now_dt,
        fixtures_by_competition=fixtures,
        acceptance_report=acceptance,
        state=poll_state,
    )
    counts = {
        "fixture_count": plan["counts"]["fixture_count"],
        "request_count": plan["counts"]["request_count"],
        "calendar_fetch_count": 0,
        "details_fetch_count": 0,
        "accepted_count": 0,
        "newly_confirmed_count": 0,
        "rejection_count": 0,
        "source_failure_count": 0,
        "cache_commit_count": 0,
        "state_commit_count": 0,
    }
    base_result = {
        "status": "dry_run" if not live else "no_due",
        "skipped": plan["skipped"],
        "rejection_reasons": {},
        "newly_confirmed": {},
        "next_due_at": plan["next_due_at"],
        "counts": counts,
    }
    if not live:
        return base_result
    requests = plan["requests"]
    if not requests:
        return base_result

    fixture_index = {
        (competition_id, row["event_id"]): row
        for competition_id, rows in fixtures.items()
        for row in rows
    }
    rejection_reasons: dict[str, dict[str, int]] = {}
    usable_requests: list[dict[str, Any]] = []
    for request in requests:
        competition_id = request["competition_id"]
        if competition_id not in provider_ids:
            _increment(rejection_reasons, competition_id, "provider_competition_id_missing")
            continue
        usable_requests.append(request)
    if not usable_requests:
        result = _blocked("provider_competition_id_missing")
        result["skipped"] = plan["skipped"]
        result["rejection_reasons"] = rejection_reasons
        result["next_due_at"] = plan["next_due_at"]
        result["counts"].update({
            "fixture_count": counts["fixture_count"],
            "request_count": counts["request_count"],
            "rejection_count": sum(sum(row.values()) for row in rejection_reasons.values()),
        })
        return result

    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for request in usable_requests:
        kickoff = _utc(request["kickoff_at_utc"])
        grouped[kickoff.strftime("%Y%m%d")][request["competition_id"]].append(request)

    registry = identity_registry or accepted_league_team_identity_registry()
    accepted_by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected_rows_by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    had_source_failure = False
    for date in sorted(grouped):
        try:
            calendar_payload = calendar_fetcher(date=date, transport=calendar_transport)
            counts["calendar_fetch_count"] += 1
        except Exception:
            counts["calendar_fetch_count"] += 1
            counts["source_failure_count"] += 1
            had_source_failure = True
            for competition_id, date_requests in grouped[date].items():
                _increment(rejection_reasons, competition_id, "calendar_fetch_failed", len(date_requests))
            continue
        for competition_id in sorted(grouped[date]):
            date_requests = grouped[date][competition_id]
            local_fixtures = [fixture_index[(competition_id, row["event_id"])] for row in date_requests]
            parser_arguments = {
                "calendar_payload": calendar_payload,
                "competition_id": competition_id,
                "local_fixtures": local_fixtures,
                "registry": registry,
                "fetched_at": now_dt,
                "provider_competition_id": provider_ids[competition_id],
            }
            try:
                discovery = parser(details_by_match_id={}, **parser_arguments)
            except Exception:
                _increment(rejection_reasons, competition_id, "parser_failed", len(date_requests))
                counts["source_failure_count"] += 1
                had_source_failure = True
                continue
            due_source_ids = sorted({
                row["source_match_id"]
                for row in discovery.get("rejected", [])
                if isinstance(row, Mapping)
                and row.get("reason") == "details_missing"
                and _scalar_id(row.get("source_match_id")) is not None
            })
            details_by_match_id: dict[str, Any] = {}
            failed_source_ids: set[str] = set()
            for source_match_id in due_source_ids:
                try:
                    counts["details_fetch_count"] += 1
                    details_by_match_id[source_match_id] = details_fetcher(
                        match_id=source_match_id,
                        transport=details_transport,
                    )
                except Exception:
                    failed_source_ids.add(source_match_id)
                    counts["source_failure_count"] += 1
                    had_source_failure = True
            try:
                report = parser(details_by_match_id=details_by_match_id, **parser_arguments)
            except Exception:
                _increment(rejection_reasons, competition_id, "parser_failed", len(date_requests))
                counts["source_failure_count"] += 1
                had_source_failure = True
                continue
            accepted_by_competition[competition_id].extend(report.get("accepted", []))
            for row in report.get("rejected", []):
                if not isinstance(row, Mapping):
                    continue
                reason = str(row.get("reason") or "parser_rejected")
                if reason == "details_missing" and row.get("source_match_id") in failed_source_ids:
                    reason = "details_fetch_failed"
                _increment(rejection_reasons, competition_id, reason)
                rejected_rows_by_competition[competition_id].append({
                    "provider": "fotmob",
                    "competition_id": competition_id,
                    "source_match_id": _scalar_id(row.get("source_match_id")) or "",
                    "reason": reason,
                })

    for competition_id, rows in accepted_by_competition.items():
        unique = {(row["event_id"], row["lineup_fingerprint"]): row for row in rows}
        accepted_by_competition[competition_id] = [unique[key] for key in sorted(unique)]
    counts["accepted_count"] = sum(len(rows) for rows in accepted_by_competition.values())
    counts["rejection_count"] = sum(sum(row.values()) for row in rejection_reasons.values())

    store = store_factory(root)
    new_candidates: dict[str, list[dict[str, Any]]] = {}
    try:
        for competition_id in sorted(accepted_by_competition):
            accepted_rows = accepted_by_competition[competition_id]
            if not accepted_rows:
                continue
            existing = store.read_competition(competition_id)
            existing_fingerprints = {
                row["lineup_fingerprint"] for row in (existing or {}).get("accepted", [])
            }
            store.commit_confirmed(competition_id, {
                "accepted": accepted_rows,
                "rejected": rejected_rows_by_competition.get(competition_id, []),
            })
            counts["cache_commit_count"] += 1
            new_rows = [row for row in accepted_rows if row["lineup_fingerprint"] not in existing_fingerprints]
            if new_rows:
                new_candidates[competition_id] = new_rows
    except Exception:
        result = {
            **base_result,
            "status": "error",
            "reason": "cache_commit_failed",
            "rejection_reasons": rejection_reasons,
            "counts": counts,
        }
        return result

    next_state = _merge_state_for_poll(poll_state, usable_requests, accepted_by_competition, now_dt)
    try:
        store.commit_state(next_state)
        counts["state_commit_count"] = 1
    except Exception:
        result = {
            **base_result,
            "status": "error",
            "reason": "state_commit_failed",
            "rejection_reasons": rejection_reasons,
            "counts": counts,
        }
        return result

    next_plan = plan_league_lineup_poll(
        now=now_dt,
        fixtures_by_competition=fixtures,
        acceptance_report=acceptance,
        state=next_state,
    )
    newly_confirmed = {
        competition_id: [_summary(row) for row in rows]
        for competition_id, rows in sorted(new_candidates.items())
    }
    counts["newly_confirmed_count"] = sum(len(rows) for rows in newly_confirmed.values())
    if had_source_failure:
        status = "partial" if counts["accepted_count"] else "error"
    else:
        status = "refreshed" if counts["accepted_count"] else "polled"
    return {
        "status": status,
        "skipped": plan["skipped"],
        "rejection_reasons": rejection_reasons,
        "newly_confirmed": newly_confirmed,
        "next_due_at": next_plan["next_due_at"],
        "counts": counts,
    }


def main(argv: list[str] | None = None) -> int:
    argument_parser = argparse.ArgumentParser(description="Refresh confirmed six-league FotMob lineups.")
    argument_parser.add_argument("--root", default=".")
    argument_parser.add_argument("--now", required=True)
    argument_parser.add_argument("--live", action="store_true")
    argument_parser.add_argument("--write", action="store_true")
    arguments = argument_parser.parse_args(argv)
    result = run_league_lineups_refresh(
        root=arguments.root,
        now=arguments.now,
        live=arguments.live,
        write=arguments.write,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
