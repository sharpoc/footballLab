from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TREND_WINDOW_DAYS = 10
MAX_POINTS = 30
TREND_MARKETS = (
    ("1x2", ("home", "draw", "away")),
    ("ou_2_5", ("over", "under")),
    ("ah_main", ("home", "away")),
)

_NAME_RE = re.compile(r"^snapshot_(\d{8}T\d{6})Z.*\.json$")


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _file_time(path: Path) -> datetime | None:
    matched = _NAME_RE.match(path.name)
    if not matched:
        return None
    return datetime.strptime(matched.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def list_history_files(history_dir: str | Path, since: str | None = None) -> list[Path]:
    cutoff = _parse_at(since) if since else None
    out: list[tuple[datetime, Path]] = []
    for path in Path(history_dir).glob("snapshot_*.json"):
        at = _file_time(path)
        if at is None:
            continue
        if cutoff is not None and at < cutoff:
            continue
        out.append((at, path))
    return [path for _at, path in sorted(out)]


def _match_entry(snapshot: dict, home_canonical: str, away_canonical: str) -> dict | None:
    for entry in snapshot.get("matches", []):
        if (
            entry.get("home_canonical") == home_canonical
            and entry.get("away_canonical") == away_canonical
        ):
            return entry
    return None


def _point(at: str, market: str, block: dict, selection: str) -> list | None:
    odds = (block.get("odds") or {}).get(selection)
    if odds is None:
        return None
    if market == "ah_main":
        return [at, odds, block.get("line_home")]
    if market == "ou_2_5" and block.get("line") is not None:
        return [at, odds, block.get("line")]
    return [at, odds]


def _compress(raw: list[list], max_points: int) -> list[list]:
    if not raw:
        return []
    compressed = [raw[0]]
    for prev, point in zip(raw, raw[1:]):
        if point[1:] != prev[1:]:
            compressed.append(point)
    if compressed[-1][0] != raw[-1][0]:
        compressed.append(raw[-1])
    if max_points > 0 and len(compressed) > max_points:
        if max_points == 1:
            return [compressed[-1]]
        compressed = [compressed[0]] + compressed[-(max_points - 1) :]
    return compressed


def _first_latest(series: list[list]) -> tuple[list, list] | tuple[None, None]:
    if not series:
        return None, None
    return series[0], series[-1]


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _selection_movement(series: list[list]) -> dict:
    first, latest = _first_latest(series)
    if first is None or latest is None:
        return {
            "points": 0,
            "first_odds": None,
            "latest_odds": None,
            "absolute_move": None,
            "relative_move": None,
            "direction": "insufficient_history",
        }
    first_odds = float(first[1])
    latest_odds = float(latest[1])
    absolute_move = latest_odds - first_odds
    relative_move = absolute_move / first_odds if first_odds else None
    if absolute_move < 0:
        direction = "shortened"
    elif absolute_move > 0:
        direction = "drifted"
    else:
        direction = "unchanged"
    return {
        "points": len(series),
        "first_at": first[0],
        "latest_at": latest[0],
        "first_odds": first_odds,
        "latest_odds": latest_odds,
        "absolute_move": _round(absolute_move),
        "relative_move": _round(relative_move),
        "direction": direction,
    }


def _line_from_point(point: list | None) -> float | None:
    if point is None or len(point) < 3 or point[2] is None:
        return None
    return float(point[2])


def _ah_movement(trend: dict[str, dict[str, list]]) -> dict:
    market = trend.get("ah_main") or {}
    home = market.get("home") or []
    away = market.get("away") or []
    first, latest = _first_latest(home)
    first_line = _line_from_point(first)
    latest_line = _line_from_point(latest)
    line_move = latest_line - first_line if first_line is not None and latest_line is not None else None
    if line_move is None or line_move == 0:
        favorite_direction = "unchanged" if line_move == 0 else "unknown"
    elif latest_line < first_line:
        favorite_direction = "home_strengthened"
    else:
        favorite_direction = "away_strengthened"
    return {
        "home": _selection_movement(home),
        "away": _selection_movement(away),
        "first_line_home": first_line,
        "latest_line_home": latest_line,
        "line_move": _round(line_move),
        "line_move_abs": _round(abs(line_move)) if line_move is not None else None,
        "favorite_line_direction": favorite_direction,
    }


def _ou_movement(trend: dict[str, dict[str, list]]) -> dict:
    market = trend.get("ou_2_5") or {}
    over = market.get("over") or []
    under = market.get("under") or []
    first, latest = _first_latest(over)
    first_line = _line_from_point(first)
    latest_line = _line_from_point(latest)
    line_move = latest_line - first_line if first_line is not None and latest_line is not None else None
    return {
        "over": _selection_movement(over),
        "under": _selection_movement(under),
        "first_line": first_line,
        "latest_line": latest_line,
        "total_line_move": _round(line_move),
        "line_move_abs": _round(abs(line_move)) if line_move is not None else None,
    }


def _quality(movement: dict) -> dict:
    point_counts = []
    for market in ("1x2",):
        for block in movement.get(market, {}).values():
            point_counts.append(block.get("points", 0))
    for market in ("ah_main", "ou"):
        for selection in ("home", "away", "over", "under"):
            block = (movement.get(market) or {}).get(selection)
            if isinstance(block, dict):
                point_counts.append(block.get("points", 0))
    line_changed = any(
        (movement.get(market) or {}).get(key) not in (None, 0)
        for market, key in (("ah_main", "line_move"), ("ou", "total_line_move"))
    )
    enough_points = bool(point_counts) and max(point_counts) >= 2
    sparse = not enough_points
    return {
        "enough_points": enough_points,
        "min_points": min(point_counts) if point_counts else 0,
        "max_points": max(point_counts) if point_counts else 0,
        "line_changed": line_changed,
        "stale": False,
        "sparse": sparse,
        "noisy_or_sparse": sparse,
    }


def build_odds_movement(trend: dict[str, dict[str, list]]) -> dict:
    movement = {
        "schema_version": 1,
        "window": "captured_history",
        "1x2": {
            selection: _selection_movement((trend.get("1x2") or {}).get(selection) or [])
            for selection in ("home", "draw", "away")
        },
        "ah_main": _ah_movement(trend),
        "ou": _ou_movement(trend),
    }
    movement["quality"] = _quality(movement)
    return movement


def _quality_allows_shadow_support(movement: dict) -> bool:
    quality = movement.get("quality") or {}
    return bool(quality.get("enough_points")) and not bool(quality.get("noisy_or_sparse"))


def _movement_base(movement: dict) -> dict:
    return {
        "schema_version": 1,
        "activation": "shadow_only",
        "source": "odds_movement",
        "quality": movement.get("quality") or {},
    }


def _odds_direction_supports(block: dict) -> bool:
    return block.get("direction") == "shortened"


def _ah_signal_side(selection: str) -> str | None:
    if selection.startswith("home_"):
        return "home"
    if selection.startswith("away_"):
        return "away"
    return None


def _ah_line_supports(side: str | None, direction: str | None) -> bool:
    if side == "home":
        return direction == "home_strengthened"
    if side == "away":
        return direction == "away_strengthened"
    return False


def _ou_line_supports(selection: str, line_move: float | None) -> bool:
    if line_move is None or line_move == 0:
        return False
    if selection == "over":
        return line_move > 0
    if selection == "under":
        return line_move < 0
    return False


def _signal_movement_shadow(signal: dict, movement: dict) -> dict | None:
    market_type = signal.get("market_type")
    selection = str(signal.get("selection") or "")
    usable = _quality_allows_shadow_support(movement)

    if market_type == "1X2_90min":
        block = ((movement.get("1x2") or {}).get(selection) or {})
        odds_support = _odds_direction_supports(block)
        return {
            **_movement_base(movement),
            "market_odds_direction": block.get("direction"),
            "odds_direction_supports_signal": odds_support,
            "line_direction_supports_signal": False,
            "supports_signal": usable and odds_support,
        }

    if market_type == "AsianHandicap_90min":
        side = _ah_signal_side(selection)
        block = ((movement.get("ah_main") or {}).get(side or "") or {})
        line_direction = (movement.get("ah_main") or {}).get("favorite_line_direction")
        odds_support = _odds_direction_supports(block)
        line_support = _ah_line_supports(side, line_direction)
        line_conflicts = (
            side in ("home", "away")
            and line_direction in ("home_strengthened", "away_strengthened")
            and not line_support
        )
        return {
            **_movement_base(movement),
            "market_odds_direction": block.get("direction"),
            "line_direction": line_direction,
            "odds_direction_supports_signal": odds_support,
            "line_direction_supports_signal": line_support,
            "supports_signal": usable and (line_support or (odds_support and not line_conflicts)),
        }

    if market_type == "OverUnder_90min":
        block = ((movement.get("ou") or {}).get(selection) or {})
        line_move = (movement.get("ou") or {}).get("total_line_move")
        odds_support = _odds_direction_supports(block)
        line_support = _ou_line_supports(selection, line_move)
        return {
            **_movement_base(movement),
            "market_odds_direction": block.get("direction"),
            "line_move": line_move,
            "odds_direction_supports_signal": odds_support,
            "line_direction_supports_signal": line_support,
            "supports_signal": usable and (odds_support or line_support),
        }

    return None


def _attach_signal_movement_shadow(match: dict[str, Any], movement: dict) -> None:
    for signal in match.get("signals") or []:
        shadow = _signal_movement_shadow(signal, movement)
        if shadow is not None:
            signal["movement_shadow"] = shadow


_OFFICIAL_GRADES = {"S", "A"}
_CANDIDATE_HARD_VETO_REASONS = {
    "stale_odds",
    "few_books",
    "market_dispersion",
    "longshot_uncertainty",
    "unconfirmed_backup",
    "line_changed_unknown",
    "model_disagreement",
    "extreme_favorite_handicap",
}


def _lineup_allows_ah_promotion(match: dict[str, Any]) -> bool:
    shadow = ((match.get("model") or {}).get("lineup_shadow") or {})
    return (
        shadow.get("confirmed_starting_xi") is True
        and shadow.get("post_information_odds_available") is True
    )


def _has_hard_veto(signal: dict[str, Any]) -> bool:
    return any(str(reason) in _CANDIDATE_HARD_VETO_REASONS for reason in signal.get("reasons") or [])


def _is_supported_ah_candidate(signal: dict[str, Any], match: dict[str, Any]) -> bool:
    raw_grade = str(signal.get("raw_grade") or "")
    return (
        signal.get("market_type") == "AsianHandicap_90min"
        and raw_grade in _OFFICIAL_GRADES
        and signal.get("grade") not in _OFFICIAL_GRADES
        and signal.get("candidate_grade") == f"{raw_grade}-candidate"
        and ((signal.get("ah_validation_shadow") or {}).get("candidate_validated") is True)
        and ((signal.get("movement_shadow") or {}).get("supports_signal") is True)
        and not _has_hard_veto(signal)
        and _lineup_allows_ah_promotion(match)
    )


def _promote_ah_candidate(signal: dict[str, Any]) -> None:
    from_grade = signal.get("grade")
    to_grade = signal.get("raw_grade")
    candidate_grade = signal.pop("candidate_grade", None)
    candidate_reasons = list(signal.pop("candidate_reasons", []) or [])
    reasons = [
        str(reason)
        for reason in signal.get("reasons") or []
        if str(reason) != "ah_market_edge_missing"
    ]
    if "ah_candidate_promoted" not in reasons:
        reasons.append("ah_candidate_promoted")
    signal["grade"] = to_grade
    signal["reasons"] = reasons
    signal["ah_market_validated"] = True
    signal["promotion"] = {
        "schema_version": 1,
        "activation": "official",
        "method": "ah_candidate_v1",
        "from_grade": from_grade,
        "to_grade": to_grade,
        "source_candidate_grade": candidate_grade,
        "validated_by": ["ah_validation_shadow", "movement_shadow"],
        "candidate_reasons": candidate_reasons,
        "promotion_reasons": [
            "ah_validation_shadow_candidate_validated",
            "movement_shadow_supports_signal",
        ],
    }


def _promote_supported_ah_candidates(match: dict[str, Any]) -> None:
    for signal in match.get("signals") or []:
        if _is_supported_ah_candidate(signal, match):
            _promote_ah_candidate(signal)


def extract_match_trend(
    snapshots: list[dict],
    home_canonical: str,
    away_canonical: str,
    max_points: int = MAX_POINTS,
) -> dict[str, dict[str, list]]:
    raw: dict[str, dict[str, list]] = {
        market: {selection: [] for selection in selections}
        for market, selections in TREND_MARKETS
    }
    ordered = sorted(
        (snapshot for snapshot in snapshots if snapshot.get("snapshot_at")),
        key=lambda snapshot: snapshot["snapshot_at"],
    )
    for snapshot in ordered:
        entry = _match_entry(snapshot, home_canonical, away_canonical)
        if entry is None:
            continue
        at = snapshot["snapshot_at"]
        market_blocks = entry.get("market") or {}
        for market, selections in TREND_MARKETS:
            block = market_blocks.get(market) or {}
            for selection in selections:
                point = _point(at, market, block, selection)
                if point is not None:
                    raw[market][selection].append(point)
    return {
        market: {
            selection: _compress(series, max_points)
            for selection, series in selections.items()
        }
        for market, selections in raw.items()
    }


def attach_trends(
    snapshot: dict[str, Any],
    history_dir: str | Path,
    now: str | None = None,
    window_days: int = TREND_WINDOW_DAYS,
) -> None:
    now_at = _parse_at(now) if now else datetime.now(timezone.utc)
    since = (now_at - timedelta(days=window_days)).isoformat()
    history: list[dict] = []
    for path in list_history_files(history_dir, since=since):
        try:
            history.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if not history:
        return
    for match in snapshot.get("matches", []):
        home = match.get("home_canonical")
        away = match.get("away_canonical")
        if not home or not away:
            continue
        trend = extract_match_trend(history, home, away)
        match["odds_trend"] = trend
        movement = build_odds_movement(trend)
        match["odds_movement"] = movement
        _attach_signal_movement_shadow(match, movement)
        _promote_supported_ah_candidates(match)
