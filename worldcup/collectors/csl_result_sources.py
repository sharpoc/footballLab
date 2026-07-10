from __future__ import annotations

import ast
import re
from typing import Any

from worldcup.collectors.club_aliases import match_known_club_alias


_SCORE_RE = re.compile(r"^(\d+)-(\d+)(?:\(|$)")


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
