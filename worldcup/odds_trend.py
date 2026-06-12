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
        match["odds_trend"] = extract_match_trend(history, home, away)
