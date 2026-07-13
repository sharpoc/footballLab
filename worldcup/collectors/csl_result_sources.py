from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from worldcup.collectors.club_aliases import match_known_club_alias


_SCORE_RE = re.compile(r"^(\d+)-(\d+)(?:\(|$)")
_CST = ZoneInfo("Asia/Shanghai")
_OFFICIAL_FIXTURE_STATUSES = {
    "fixture": "SCHEDULED",
    "postponed": "POSTPONED",
    "played": "PLAYED",
}


def _official_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    saved = payload.get("matches")
    if isinstance(saved, list):
        return [item for item in saved if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("dataList"), list):
        return [item for item in data["dataList"] if isinstance(item, dict)]
    raise ValueError("cfl official payload does not contain a match list")


def _known_official_team(match: dict[str, Any], side: str) -> str:
    candidates = (
        match.get(f"{side}_contestant_name"),
        match.get(f"{side}_contestant_name_en"),
        match.get(f"{side}_contestant_official_name"),
        match.get(f"{side}_contestant_official_name_en"),
    )
    for candidate in candidates:
        name = str(candidate or "").strip()
        if name and match_known_club_alias("csl_2026", name).canonical_key is not None:
            return name
    return str(candidates[0] or "").strip()


def _kickoff_at_utc(match_date: str, kickoff_time: str) -> str:
    parsed = datetime.fromisoformat(f"{match_date}T{kickoff_time}").replace(tzinfo=_CST)
    return parsed.astimezone(timezone.utc).isoformat()


def _fixture_row(
    *,
    season: str,
    round_value: Any,
    match_date: str,
    kickoff_time: str,
    home_team: str,
    away_team: str,
    status: str,
    source_match_id: Any,
    source_url: str,
) -> dict[str, str] | None:
    home_alias = match_known_club_alias("csl_2026", home_team)
    away_alias = match_known_club_alias("csl_2026", away_team)
    if home_alias.canonical_key is None or away_alias.canonical_key is None:
        return None
    try:
        kickoff_at_utc = _kickoff_at_utc(match_date, kickoff_time)
    except ValueError:
        return None
    return {
        "season": season,
        "round": str(round_value or "").strip(),
        "kickoff_at_utc": kickoff_at_utc,
        "home_team": home_team,
        "away_team": away_team,
        "home_canonical": home_alias.canonical_key,
        "away_canonical": away_alias.canonical_key,
        "status": status,
        "source_match_id": str(source_match_id or "").strip(),
        "source_url": source_url,
    }


def parse_cfl_official_fixture_rows(
    payload: dict[str, Any],
    *,
    season: str,
    source_url: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _official_matches(payload):
        status = _OFFICIAL_FIXTURE_STATUSES.get(
            str(match.get("match_status") or "").strip().casefold()
        )
        if status is None:
            continue
        match_date = str(match.get("local_date") or "").strip()
        kickoff_time = str(match.get("local_time") or "").strip()
        if not match_date or not kickoff_time:
            continue
        row = _fixture_row(
            season=season,
            round_value=match.get("week"),
            match_date=match_date,
            kickoff_time=kickoff_time,
            home_team=_known_official_team(match, "home"),
            away_team=_known_official_team(match, "away"),
            status=status,
            source_match_id=match.get("id"),
            source_url=source_url,
        )
        if row is not None:
            rows.append(row)
    return rows


def parse_cfl_official_result_rows(
    payload: dict[str, Any],
    *,
    season: str,
    source_url: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _official_matches(payload):
        if str(match.get("match_status") or "").strip().casefold() != "played":
            continue
        home_score = match.get("ft_home_score")
        away_score = match.get("ft_away_score")
        if not isinstance(home_score, int) or not isinstance(away_score, int):
            continue
        match_date = str(match.get("local_date") or "").strip()
        if not match_date:
            continue
        rows.append(
            {
                "season": season,
                "round": str(match.get("week") or "").strip(),
                "date": match_date,
                "kickoff_time_local": str(match.get("local_time") or "").strip(),
                "home_team": _known_official_team(match, "home"),
                "away_team": _known_official_team(match, "away"),
                "home_score": str(home_score),
                "away_score": str(away_score),
                "neutral": "0",
                "status": "finished",
                "source_match_id": str(match.get("id") or "").strip(),
                "source_url": source_url,
            }
        )
    return rows


def _js_array(source: str, name: str) -> list[Any]:
    matched = re.search(
        rf"\bvar\s+{re.escape(name)}\s*=\s*(\[.*?\])\s*;",
        source,
        flags=re.DOTALL,
    )
    if matched is None:
        raise ValueError(f"sevenm fixture array missing: {name}")
    value = ast.literal_eval(matched.group(1))
    if not isinstance(value, list):
        raise ValueError(f"sevenm fixture value is not an array: {name}")
    return value


def _sevenm_datetime(value: Any) -> tuple[str, str]:
    parts = [part.strip() for part in str(value or "").split(",")]
    if len(parts) != 6 or not all(part.isdigit() for part in parts):
        raise ValueError(f"invalid sevenm match datetime: {value}")
    year, month, day, hour, minute, second = (int(part) for part in parts[:6])
    return (
        f"{year:04d}-{month:02d}-{day:02d}",
        f"{hour:02d}:{minute:02d}:{second:02d}",
    )


def _strip_neutral_marker(value: Any) -> tuple[str, bool]:
    name = str(value or "").strip()
    neutral = name.endswith("(中)") or name.endswith("（中）")
    if neutral:
        name = re.sub(r"(?:\(中\)|（中）)$", "", name).strip()
    return name, neutral


def parse_sevenm_fixture_result_rows(
    source: str,
    *,
    season: str,
    source_url: str,
) -> list[dict[str, str]]:
    arrays = {
        name: _js_array(source, name)
        for name in (
            "Tmp_bh_Arr",
            "Run_Arr",
            "Time_Arr",
            "Scores_Arr",
            "TeamA_Arr",
            "TeamB_Arr",
            "Stat_Arr",
        )
    }
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("sevenm fixture arrays have inconsistent lengths")

    rows: list[dict[str, str]] = []
    for index, score_text in enumerate(arrays["Scores_Arr"]):
        score = _SCORE_RE.match(str(score_text or "").strip())
        if score is None:
            continue
        match_date, kickoff_time = _sevenm_datetime(arrays["Time_Arr"][index])
        home_team, home_neutral = _strip_neutral_marker(arrays["TeamA_Arr"][index])
        away_team, away_neutral = _strip_neutral_marker(arrays["TeamB_Arr"][index])
        rows.append(
            {
                "season": season,
                "round": str(arrays["Run_Arr"][index]),
                "date": match_date,
                "kickoff_time_local": kickoff_time,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": score.group(1),
                "away_score": score.group(2),
                "neutral": "1" if home_neutral or away_neutral else "0",
                "status": "finished",
                "source_match_id": str(arrays["Tmp_bh_Arr"][index]),
                "source_url": source_url,
            }
        )
    return rows


def parse_sevenm_fixture_rows(
    source: str,
    *,
    season: str,
    source_url: str,
) -> list[dict[str, str]]:
    arrays = {
        name: _js_array(source, name)
        for name in (
            "Tmp_bh_Arr",
            "Run_Arr",
            "Time_Arr",
            "Scores_Arr",
            "TeamA_Arr",
            "TeamB_Arr",
        )
    }
    lengths = {len(value) for value in arrays.values()}
    if len(lengths) != 1:
        raise ValueError("sevenm fixture arrays have inconsistent lengths")
    count = len(arrays["Tmp_bh_Arr"])
    try:
        memo = _js_array(source, "Memo_Arr")
    except ValueError:
        memo = [""] * count
    if len(memo) != count:
        raise ValueError("sevenm fixture arrays have inconsistent lengths")

    rows: list[dict[str, str]] = []
    for index in range(count):
        match_date, kickoff_time = _sevenm_datetime(arrays["Time_Arr"][index])
        home_team, _home_neutral = _strip_neutral_marker(arrays["TeamA_Arr"][index])
        away_team, _away_neutral = _strip_neutral_marker(arrays["TeamB_Arr"][index])
        score_text = str(arrays["Scores_Arr"][index] or "").strip()
        memo_text = str(memo[index] or "").strip()
        if _SCORE_RE.match(score_text):
            status = "PLAYED"
        elif "延期" in memo_text:
            status = "POSTPONED"
        else:
            status = "SCHEDULED"
        row = _fixture_row(
            season=season,
            round_value=arrays["Run_Arr"][index],
            match_date=match_date,
            kickoff_time=kickoff_time,
            home_team=home_team,
            away_team=away_team,
            status=status,
            source_match_id=arrays["Tmp_bh_Arr"][index],
            source_url=source_url,
        )
        if row is not None:
            rows.append(row)
    return rows
