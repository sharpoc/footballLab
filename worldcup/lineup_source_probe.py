"""Probe candidate lineup sources without feeding them into the model.

Default mode is dry-run: no network access and no writes. Live mode is for
observability only; it records whether each source has confirmed, predicted,
or missing starting elevens at the observed time.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from worldcup.collectors.fifa_lineups import parse_fifa_live_match
from worldcup.sources.fifa_lineups import (
    DEFAULT_COMPETITION_ID,
    DEFAULT_SEASON_ID,
    fetch_fifa_calendar_matches,
    fetch_fifa_live_match,
)

DEFAULT_OUT_PATH = "data/local/diagnostics/lineup_source_probe.json"
DEFAULT_HISTORY_PATH = "data/local/diagnostics/lineup_source_probe_history.jsonl"
DEFAULT_REPORT_PATH = "data/local/diagnostics/lineup_source_probe_report.md"
FOTMOB_BASE_URL = "https://www.fotmob.com/api"
FOTMOB_WORLD_CUP_LEAGUE_ID = 77


def _parse_utc(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_optional_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _parse_utc(str(value))
    except ValueError:
        return None


def _write_json(path: str | Path, payload: Any) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fh.write("\n")


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _read_json_response(response: Any) -> Any:
    body = response.read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def _default_fotmob_transport(url: str) -> Any:
    headers = {
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 lineup-source-probe/1.0",
        "x-fm-req": "1",
    }
    return urlopen(Request(url, headers=headers), timeout=20)


def _fetch_json(url: str, *, transport: Callable[[str], Any] | None = None) -> Any:
    return _read_json_response((transport or _default_fotmob_transport)(url))


def _date_values(observed: datetime, lookahead_hours: int) -> list[str]:
    end = observed + timedelta(hours=lookahead_hours)
    current = observed.date()
    dates = []
    while current <= end.date():
        dates.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def build_fotmob_matches_url(date: str) -> str:
    return f"{FOTMOB_BASE_URL}/data/matches?{urlencode({'date': date})}"


def build_fotmob_match_details_url(match_id: str | int) -> str:
    return f"{FOTMOB_BASE_URL}/data/matchDetails?{urlencode({'matchId': str(match_id)})}"


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


def _captured_before_kickoff(observed: datetime, kickoff: datetime | None) -> bool | None:
    if kickoff is None:
        return None
    return observed < kickoff


def _minutes_to_kickoff(observed: datetime, kickoff: datetime | None) -> float | None:
    if kickoff is None:
        return None
    return round((kickoff - observed).total_seconds() / 60.0, 1)


def _lineup_status(
    *,
    home_count: int,
    away_count: int,
    raw_status: Any = None,
    confirmed_hint: bool = False,
) -> str:
    if home_count != 11 or away_count != 11:
        return "missing"
    text = str(raw_status or "").strip().lower()
    if confirmed_hint or any(marker in text for marker in ("confirm", "official", "available")):
        return "confirmed"
    if any(marker in text for marker in ("predict", "expected", "probable")):
        return "predicted"
    return "unknown"


def _fifa_observation(context: Any, *, observed: datetime) -> dict[str, Any]:
    kickoff = context.kickoff_at_utc
    home_count = len(context.home_starting)
    away_count = len(context.away_starting)
    status = _lineup_status(
        home_count=home_count,
        away_count=away_count,
        confirmed_hint=context.confirmed_starting_xi is True,
    )
    return {
        "source": "fifa_public_api",
        "provider": "fifa_public_api",
        "source_match_id": str(context.source_match_no) if context.source_match_no is not None else None,
        "observed_at": observed.isoformat(),
        "kickoff_at_utc": kickoff.isoformat() if kickoff else None,
        "home_team": context.home_team_name,
        "away_team": context.away_team_name,
        "lineup_status": status,
        "home_starting_count": home_count,
        "away_starting_count": away_count,
        "home_formation": context.home_formation,
        "away_formation": context.away_formation,
        "captured_before_kickoff": _captured_before_kickoff(observed, kickoff),
        "minutes_to_kickoff": _minutes_to_kickoff(observed, kickoff),
    }


def _probe_fifa(
    *,
    observed: datetime,
    lookahead_hours: int,
    transport: Callable[[str], Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from_date = observed.date().isoformat()
    to_date = (observed + timedelta(hours=lookahead_hours)).date().isoformat()
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    calendar = fetch_fifa_calendar_matches(
        from_date=from_date,
        to_date=to_date,
        id_competition=DEFAULT_COMPETITION_ID,
        id_season=DEFAULT_SEASON_ID,
        transport=transport,
    ).json_body
    for match in calendar.get("Results") or []:
        if not isinstance(match, dict):
            continue
        ids = _calendar_match_ids(match)
        if ids is None:
            continue
        id_comp, season, stage, match_id = ids
        try:
            raw = fetch_fifa_live_match(
                id_competition=id_comp,
                id_season=season,
                id_stage=stage,
                id_match=match_id,
                transport=transport,
            ).json_body
            observations.append(_fifa_observation(parse_fifa_live_match(raw, fetched_at=observed), observed=observed))
        except Exception as exc:  # pragma: no cover - summary path for live-source instability.
            errors.append({"source": "fifa_public_api", "match_id": match_id, "error": type(exc).__name__})
    return observations, errors


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _match_id(match: dict[str, Any]) -> str | None:
    for key in ("id", "matchId", "primary_id", "primaryId"):
        value = match.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _team_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "shortName", "fullName"):
            candidate = value.get(key)
            if candidate:
                return str(candidate)
    return ""


def _fotmob_match_kickoff(match: dict[str, Any]) -> datetime | None:
    status = _as_dict(match.get("status"))
    for value in (
        status.get("utcTime"),
        status.get("startTimeUTC"),
        match.get("matchTimeUTC"),
        match.get("timeUTC"),
    ):
        parsed = _parse_optional_utc(value)
        if parsed is not None:
            return parsed
    return None


def _fotmob_matches(raw: Any, *, league_ids: set[int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for league in _as_dict(raw).get("leagues") or []:
        if not isinstance(league, dict):
            continue
        league_id = league.get("id") or league.get("primaryId")
        try:
            league_id_int = int(league_id)
        except (TypeError, ValueError):
            league_id_int = None
        if league_ids and league_id_int not in league_ids:
            continue
        for match in league.get("matches") or []:
            if isinstance(match, dict):
                out.append(match)
    return out


def _player_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    name = value.get("name")
    if isinstance(name, dict):
        for key in ("fullName", "name", "shortName"):
            if name.get(key):
                return str(name[key])
    if isinstance(name, str):
        return name
    for key in ("fullName", "name", "shortName", "displayName"):
        if value.get(key):
            return str(value[key])
    return ""


def _is_starter(player: dict[str, Any]) -> bool:
    for key in ("isStarter", "starter", "isStarting"):
        if player.get(key) is True:
            return True
    role = str(player.get("role") or player.get("lineupRole") or player.get("status") or "").lower()
    return role in {"starter", "starting", "startingxi", "lineup"}


def _extract_starters(block: Any) -> list[dict[str, Any]]:
    if isinstance(block, list):
        return [item for item in block if isinstance(item, dict) and _player_name(item)]
    if not isinstance(block, dict):
        return []
    for key in ("starters", "startingXI", "starting_xi", "starting", "lineup"):
        value = block.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict) and _player_name(item)]
    players = block.get("players")
    if isinstance(players, list):
        return [
            item
            for item in players
            if isinstance(item, dict) and _is_starter(item) and _player_name(item)
        ]
    return []


def _formation(block: Any) -> str | None:
    if not isinstance(block, dict):
        return None
    value = block.get("formation") or block.get("lineup")
    return str(value).strip() if value else None


def _fotmob_lineup_blocks(lineup: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    lineup_dict = _as_dict(lineup)
    home = _as_dict(
        lineup_dict.get("homeTeam")
        or lineup_dict.get("home")
        or lineup_dict.get("homeLineup")
    )
    away = _as_dict(
        lineup_dict.get("awayTeam")
        or lineup_dict.get("away")
        or lineup_dict.get("awayLineup")
    )
    return home, away


def _fotmob_observation(
    raw: Any,
    *,
    match: dict[str, Any],
    observed: datetime,
) -> dict[str, Any]:
    raw_dict = _as_dict(raw)
    general = _as_dict(raw_dict.get("general"))
    content = _as_dict(raw_dict.get("content"))
    lineup = _as_dict(content.get("lineup"))
    home_lineup, away_lineup = _fotmob_lineup_blocks(lineup)
    home_starters = _extract_starters(home_lineup)
    away_starters = _extract_starters(away_lineup)
    kickoff = _parse_optional_utc(general.get("matchTimeUTC")) or _fotmob_match_kickoff(match)
    raw_status = (
        lineup.get("lineupStatus")
        or lineup.get("status")
        or lineup.get("type")
        or lineup.get("availability")
    )
    confirmed_hint = lineup.get("isConfirmed") is True or lineup.get("confirmed") is True
    home_team = _team_name(general.get("homeTeam")) or _team_name(match.get("home"))
    away_team = _team_name(general.get("awayTeam")) or _team_name(match.get("away"))
    return {
        "source": "fotmob",
        "provider": "fotmob",
        "source_match_id": str(general.get("matchId") or _match_id(match) or ""),
        "observed_at": observed.isoformat(),
        "kickoff_at_utc": kickoff.isoformat() if kickoff else None,
        "home_team": home_team,
        "away_team": away_team,
        "lineup_status": _lineup_status(
            home_count=len(home_starters),
            away_count=len(away_starters),
            raw_status=raw_status,
            confirmed_hint=confirmed_hint,
        ),
        "home_starting_count": len(home_starters),
        "away_starting_count": len(away_starters),
        "home_formation": _formation(home_lineup),
        "away_formation": _formation(away_lineup),
        "captured_before_kickoff": _captured_before_kickoff(observed, kickoff),
        "minutes_to_kickoff": _minutes_to_kickoff(observed, kickoff),
    }


def _probe_fotmob(
    *,
    observed: datetime,
    lookahead_hours: int,
    transport: Callable[[str], Any] | None,
    league_ids: Iterable[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    league_id_set = {int(value) for value in league_ids}
    seen_match_ids: set[str] = set()
    for date in _date_values(observed, lookahead_hours):
        try:
            matches = _fotmob_matches(
                _fetch_json(build_fotmob_matches_url(date), transport=transport),
                league_ids=league_id_set,
            )
        except Exception as exc:  # pragma: no cover - summary path for live-source instability.
            errors.append({"source": "fotmob", "date": date, "error": type(exc).__name__})
            continue
        for match in matches:
            match_id = _match_id(match)
            if match_id is None:
                continue
            if match_id in seen_match_ids:
                continue
            seen_match_ids.add(match_id)
            try:
                details = _fetch_json(build_fotmob_match_details_url(match_id), transport=transport)
                observations.append(_fotmob_observation(details, match=match, observed=observed))
            except Exception as exc:  # pragma: no cover - summary path for live-source instability.
                errors.append({"source": "fotmob", "match_id": match_id, "error": type(exc).__name__})
    return observations, errors


def _normalise_sources(sources: Iterable[str] | str | None) -> tuple[str, ...]:
    if sources is None:
        return ("fifa", "fotmob")
    if isinstance(sources, str):
        parts = sources.split(",")
    else:
        parts = list(sources)
    normalised = []
    for item in parts:
        source = str(item).strip().lower()
        if source:
            normalised.append(source)
    return tuple(normalised)


def _summary(observations: list[dict[str, Any]], errors: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"confirmed": 0, "predicted": 0, "missing": 0, "unknown": 0}
    for item in observations:
        status = str(item.get("lineup_status") or "unknown")
        counts[status if status in counts else "unknown"] += 1
    return {
        "observations": len(observations),
        **counts,
        "source_errors": len(errors),
    }


def _source_label(source: Any) -> str:
    return {
        "fifa_public_api": "FIFA public API",
        "fotmob": "FotMob",
    }.get(str(source or ""), str(source or "unknown"))


def _match_label(row: dict[str, Any]) -> str:
    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    if home or away:
        return f"{home} vs {away}".strip()
    return str(row.get("source_match_id") or "unknown")


def _format_probe_minutes(value: Any) -> str:
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return "时间未知"
    prefix = "T-" if minutes >= 0 else "T+"
    return f"{prefix}{abs(minutes):.1f} 分钟"


def build_lineup_source_report(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        status = str(row.get("lineup_status") or "unknown")
        bucket = counts.setdefault(
            source,
            {"observations": 0, "confirmed": 0, "predicted": 0, "missing": 0, "unknown": 0},
        )
        bucket["observations"] += 1
        bucket[status if status in bucket else "unknown"] += 1

    lines = [
        "# 首发源观测报告",
        "",
        "仅用于数据源可用性研究，不进入模型，不构成投注建议。",
        "",
        "## Source Summary",
        "",
    ]
    if not counts:
        lines.append("- 暂无观测记录。")
    for source in sorted(counts):
        bucket = counts[source]
        lines.append(
            "- {source}: observations: {observations}, confirmed: {confirmed}, "
            "predicted: {predicted}, missing: {missing}, unknown: {unknown}".format(
                source=_source_label(source),
                observations=bucket["observations"],
                confirmed=bucket["confirmed"],
                predicted=bucket["predicted"],
                missing=bucket["missing"],
                unknown=bucket["unknown"],
            )
        )

    lines.extend(["", "## Match Observations", ""])
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("kickoff_at_utc") or ""),
            str(item.get("source") or ""),
            str(item.get("observed_at") or ""),
        ),
    ):
        lines.append(
            "- {match} | {source} | {status} | {minutes} | {home_count}-{away_count}".format(
                match=_match_label(row),
                source=_source_label(row.get("source")),
                status=str(row.get("lineup_status") or "unknown"),
                minutes=_format_probe_minutes(row.get("minutes_to_kickoff")),
                home_count=row.get("home_starting_count", 0),
                away_count=row.get("away_starting_count", 0),
            )
        )
    lines.append("")
    return "\n".join(lines)


def run_lineup_source_probe(
    *,
    live: bool,
    write: bool = False,
    sources: Iterable[str] | str | None = None,
    now: str | datetime | None = None,
    out_path: str | Path = DEFAULT_OUT_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    lookahead_hours: int = 24,
    transport: Callable[[str], Any] | None = None,
    fotmob_league_ids: Iterable[int] = (FOTMOB_WORLD_CUP_LEAGUE_ID,),
    append_history: bool = False,
    write_report: bool = False,
) -> dict[str, Any]:
    selected_sources = _normalise_sources(sources)
    observed = _parse_utc(now)
    if not live:
        return {
            "schema_version": 1,
            "status": "dry_run",
            "generated_at": observed.isoformat(),
            "sources": list(selected_sources),
            "note": "pass --live to request candidate lineup sources",
            "would_write": str(out_path) if write else None,
            "would_append_history": str(history_path) if append_history else None,
            "would_write_report": str(report_path) if write_report else None,
        }

    observations: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if "fifa" in selected_sources:
        source_observations, source_errors = _probe_fifa(
            observed=observed,
            lookahead_hours=lookahead_hours,
            transport=transport,
        )
        observations.extend(source_observations)
        errors.extend(source_errors)
    if "fotmob" in selected_sources:
        source_observations, source_errors = _probe_fotmob(
            observed=observed,
            lookahead_hours=lookahead_hours,
            transport=transport,
            league_ids=fotmob_league_ids,
        )
        observations.extend(source_observations)
        errors.extend(source_errors)

    payload = {
        "schema_version": 1,
        "status": "captured",
        "generated_at": observed.isoformat(),
        "sources": list(selected_sources),
        "summary": _summary(observations, errors),
        "observations": observations,
        "source_errors": errors,
        "research_boundary": "仅用于研究分析，不构成投注建议",
    }
    if write:
        _write_json(out_path, payload)
    if append_history:
        history_rows = [
            {
                **observation,
                "run_observed_at": observed.isoformat(),
                "run_generated_at": observed.isoformat(),
            }
            for observation in observations
        ]
        _append_jsonl(history_path, history_rows)
        payload["history"] = {"out": str(history_path), "appended": len(history_rows)}
    if write_report:
        report_rows = _read_jsonl(history_path) if append_history or Path(history_path).exists() else observations
        if not report_rows:
            report_rows = observations
        report_body = build_lineup_source_report(report_rows)
        report_out = Path(report_path)
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(report_body, encoding="utf-8")
        payload["report"] = {"out": str(report_path), "observations": len(report_rows)}
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe candidate lineup sources without model ingestion.")
    parser.add_argument("--live", action="store_true", help="Fetch candidate sources for real.")
    parser.add_argument("--dry-run", action="store_true", help="Do not fetch sources; this is the default.")
    parser.add_argument("--write", action="store_true", help=f"Write JSON diagnostics to {DEFAULT_OUT_PATH}.")
    parser.add_argument("--append-history", action="store_true", help=f"Append observations to {DEFAULT_HISTORY_PATH}.")
    parser.add_argument("--write-report", action="store_true", help=f"Write Markdown report to {DEFAULT_REPORT_PATH}.")
    parser.add_argument("--sources", default="fifa,fotmob", help="Comma-separated source list: fifa,fotmob.")
    parser.add_argument("--now", default=None)
    parser.add_argument("--out", default=DEFAULT_OUT_PATH)
    parser.add_argument("--history", default=DEFAULT_HISTORY_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--lookahead-hours", type=int, default=24)
    args = parser.parse_args(argv)

    result = run_lineup_source_probe(
        live=args.live and not args.dry_run,
        write=args.write,
        sources=args.sources,
        now=args.now,
        out_path=args.out,
        history_path=args.history,
        report_path=args.report,
        lookahead_hours=args.lookahead_hours,
        append_history=args.append_history,
        write_report=args.write_report,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
