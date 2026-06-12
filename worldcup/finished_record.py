from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worldcup.eval_data import closing_match_entry
from worldcup.ledger import _prediction_result
from worldcup.odds_trend import extract_match_trend, list_history_files

TRACKED_GRADES = ("S", "A")
CLOSING_WINDOW_DAYS = 3

_LABEL_TO_KEY = {"命中": "hit", "未中": "miss", "走水": "push"}
_HISTORY_NAME_RE = re.compile(r"^snapshot_(\d{8}T\d{6})Z.*\.json$")


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _record_key(row: dict) -> str:
    return f"{row['kickoff_at_utc'][:10]}_{row['home_canonical']}_{row['away_canonical']}"


def _history_file_time(path: Path) -> datetime | None:
    matched = _HISTORY_NAME_RE.match(path.name)
    if not matched:
        return None
    return datetime.strptime(matched.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def _window_files(history_dir: str | Path, since: str, before: str) -> list[Path]:
    before_at = _parse_at(before)
    out = []
    for path in list_history_files(history_dir, since=since):
        at = _history_file_time(path)
        if at is not None and at < before_at:
            out.append(path)
    return out


def _closing_odds(entry: dict, signal: dict) -> float | None:
    market = entry.get("market") or {}
    market_type = signal.get("market_type")
    selection = str(signal.get("selection") or "")
    if market_type == "1X2_90min":
        value = ((market.get("1x2") or {}).get("odds") or {}).get(selection)
    elif market_type == "OverUnder_90min":
        value = ((market.get("ou_2_5") or {}).get("odds") or {}).get(selection)
    elif market_type == "AsianHandicap_90min":
        side = selection.split("_", 1)[0]
        value = ((market.get("ah_main") or {}).get("odds") or {}).get(side)
    else:
        value = None
    return float(value) if isinstance(value, (int, float)) else None


def _freeze_record(entry: dict, row: dict, history: list[dict]) -> dict:
    result = {"home_score": int(row["home_score"]), "away_score": int(row["away_score"])}
    settled_match = {**entry, "result": {"status": "finished", **result}}
    closing_signals = []
    for signal in entry.get("signals") or []:
        prediction = _prediction_result(settled_match, signal)
        closing_signals.append(
            {
                "market_type": signal.get("market_type"),
                "selection": signal.get("selection"),
                "line": signal.get("line"),
                "grade": signal.get("grade"),
                "odds": _closing_odds(entry, signal),
                "prediction": prediction,
            }
        )
    return {
        "kickoff_at_utc": row["kickoff_at_utc"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "home_canonical": row["home_canonical"],
        "away_canonical": row["away_canonical"],
        "stage": entry.get("stage"),
        "group": entry.get("group"),
        "result": result,
        "closing_snapshot_at": None,
        "closing_signals": closing_signals,
        "odds_trend": extract_match_trend(history, row["home_canonical"], row["away_canonical"]),
    }


def _empty_tally() -> dict[str, dict[str, int]]:
    return {grade: {"hit": 0, "miss": 0, "push": 0} for grade in TRACKED_GRADES}


def _tally(records: list[dict]) -> dict[str, dict[str, int]]:
    tally = _empty_tally()
    for record in records:
        for signal in record.get("closing_signals") or []:
            grade = signal.get("grade")
            if grade not in tally:
                continue
            label = ((signal.get("prediction") or {}).get("label")) or ""
            key = _LABEL_TO_KEY.get(label)
            if key:
                tally[grade][key] += 1
    return tally


def _load_store(store_file: Path) -> dict[str, dict]:
    if not store_file.exists():
        return {}
    try:
        loaded = json.loads(store_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_results(results_file: Path) -> list[dict]:
    if not results_file.exists():
        return []
    with open(results_file, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_window(history_dir: str | Path, row: dict) -> list[dict]:
    kickoff = _parse_at(row["kickoff_at_utc"])
    since = (kickoff - timedelta(days=CLOSING_WINDOW_DAYS)).isoformat()
    history = []
    for path in _window_files(history_dir, since=since, before=row["kickoff_at_utc"]):
        try:
            history.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return history


def _closing_snapshot_at(window: list[dict], row: dict) -> str | None:
    kickoff = _parse_at(row["kickoff_at_utc"])
    best: str | None = None
    for snapshot in window:
        snapshot_at = snapshot.get("snapshot_at")
        if not snapshot_at or _parse_at(snapshot_at) >= kickoff:
            continue
        for match in snapshot.get("matches", []):
            if (
                match.get("home_canonical") == row["home_canonical"]
                and match.get("away_canonical") == row["away_canonical"]
                and (match.get("kickoff_at_utc") or "")[:10] == row["kickoff_at_utc"][:10]
            ):
                if best is None or _parse_at(snapshot_at) > _parse_at(best):
                    best = snapshot_at
    return best


def build_finished_block(
    history_dir: str | Path,
    results_csv: str | Path,
    store_path: str | Path,
) -> dict[str, Any]:
    store_file = Path(store_path)
    store = _load_store(store_file)
    results_rows = _load_results(Path(results_csv))

    skipped = 0
    for row in results_rows:
        key = _record_key(row)
        if key in store:
            continue

        window = _load_window(history_dir, row)
        entry = closing_match_entry(
            window,
            row["kickoff_at_utc"],
            row["home_canonical"],
            row["away_canonical"],
        )
        if entry is None:
            skipped += 1
            continue

        record = _freeze_record(entry, row, window)
        record["closing_snapshot_at"] = _closing_snapshot_at(window, row)
        store[key] = record

    try:
        store_file.parent.mkdir(parents=True, exist_ok=True)
        store_file.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    records = sorted(store.values(), key=lambda record: record.get("kickoff_at_utc") or "")
    return {
        "matches": records,
        "tally": _tally(records),
        "skipped_no_closing": skipped,
    }
