"""Backfill shadow diagnostics for finished raw-strong signals.

This module reads local snapshot history only. It recomputes current shadow
diagnostics for old closing snapshots and compares them with finished results;
it does not change model parameters, signal grades, refresh behavior, or
online state.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from worldcup.config import load_config
from worldcup.engine import handicap, poisson
from worldcup.ledger import _prediction_result
from worldcup.odds_trend import (
    _signal_movement_shadow,
    build_odds_movement,
    extract_match_trend,
)
from worldcup.pipeline import _ah_validation_shadow

TRACKED_RAW_GRADES = ("S", "A")
OUTCOME_KEYS = ("hit", "miss", "push")
RESEARCH_BOUNDARY = "仅用于研究分析，不构成投注建议"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _empty_outcomes() -> dict[str, int]:
    return {key: 0 for key in OUTCOME_KEYS}


def _load_history(history_dir: str | Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted(Path(history_dir).glob("snapshot_*.json")):
        try:
            snapshots.append(_read_json(path))
        except (OSError, ValueError):
            continue
    return sorted(
        (snapshot for snapshot in snapshots if snapshot.get("snapshot_at")),
        key=lambda snapshot: snapshot["snapshot_at"],
    )


def _match_record_entry(snapshot: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    for match in snapshot.get("matches") or []:
        if (
            match.get("home_canonical") == record.get("home_canonical")
            and match.get("away_canonical") == record.get("away_canonical")
            and (match.get("kickoff_at_utc") or "")[:10]
            == (record.get("kickoff_at_utc") or "")[:10]
        ):
            return match
    return None


def _closing_entry(
    history: list[dict[str, Any]],
    record: dict[str, Any],
) -> dict[str, Any] | None:
    closing_at = record.get("closing_snapshot_at")
    if closing_at:
        for snapshot in history:
            if snapshot.get("snapshot_at") == closing_at:
                found = _match_record_entry(snapshot, record)
                if found is not None:
                    return found

    kickoff = record.get("kickoff_at_utc")
    if not kickoff:
        return None
    before_kickoff = [
        snapshot
        for snapshot in history
        if snapshot.get("snapshot_at") and _parse_at(snapshot["snapshot_at"]) < _parse_at(kickoff)
    ]
    for snapshot in reversed(before_kickoff):
        found = _match_record_entry(snapshot, record)
        if found is not None:
            return found
    return None


def _history_until_closing(
    history: list[dict[str, Any]],
    record: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    cutoff_raw = record.get("closing_snapshot_at") or entry.get("kickoff_at_utc")
    if not cutoff_raw:
        return []
    cutoff = _parse_at(cutoff_raw)
    return [
        snapshot
        for snapshot in history
        if snapshot.get("snapshot_at") and _parse_at(snapshot["snapshot_at"]) <= cutoff
    ]


def _ah_signal_side(selection: Any) -> str | None:
    text = str(selection or "")
    if text.startswith("home_"):
        return "home"
    if text.startswith("away_"):
        return "away"
    return None


def _invert_dist(dist: dict[int, float]) -> dict[int, float]:
    return {-diff: prob for diff, prob in dist.items()}


def _side_dist(entry: dict[str, Any], side: str | None, cfg: dict) -> dict[int, float] | None:
    lambdas = (entry.get("model") or {}).get("lambdas") or {}
    home = lambdas.get("home")
    away = lambdas.get("away")
    if not isinstance(home, (int, float)) or not isinstance(away, (int, float)):
        return None
    matrix, _tail = poisson.score_matrix(float(home), float(away), cfg["poisson"])
    dist = handicap.diff_distribution(matrix)
    if side == "home":
        return dist
    if side == "away":
        return _invert_dist(dist)
    return None


def _ah_shadow(entry: dict[str, Any], signal: dict[str, Any], cfg: dict) -> dict | None:
    if signal.get("market_type") != "AsianHandicap_90min":
        return None
    existing = signal.get("ah_validation_shadow")
    if isinstance(existing, dict):
        return existing

    side = _ah_signal_side(signal.get("selection"))
    line = signal.get("line")
    if side is None or not isinstance(line, (int, float)):
        return None
    dist = _side_dist(entry, side, cfg)
    if dist is None:
        return None
    market_ah = ((entry.get("market") or {}).get("ah_main") or {})
    if not market_ah:
        return None
    return _ah_validation_shadow(
        SimpleNamespace(market_ah_main=market_ah),
        side,
        float(line),
        dist,
        None,
        cfg,
    )


def _movement_shadow(
    history: list[dict[str, Any]],
    record: dict[str, Any],
    entry: dict[str, Any],
    signal: dict[str, Any],
) -> dict | None:
    existing = signal.get("movement_shadow")
    if isinstance(existing, dict):
        return existing

    movement = entry.get("odds_movement")
    if not isinstance(movement, dict):
        window = _history_until_closing(history, record, entry)
        trend = extract_match_trend(
            window,
            str(record.get("home_canonical") or ""),
            str(record.get("away_canonical") or ""),
        )
        movement = build_odds_movement(trend)
    return _signal_movement_shadow(signal, movement)


def _bucket_key(value: bool | None) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "missing"


def _bump(bucket: dict[str, dict[str, int]], key: str, outcome: str | None) -> None:
    if outcome not in OUTCOME_KEYS:
        return
    entry = bucket.setdefault(key, _empty_outcomes())
    entry[outcome] += 1


def _raw_grade(signal: dict[str, Any]) -> str:
    return str(signal.get("raw_grade") or signal.get("grade") or "")


def _settled_match(entry: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") or {}
    return {**entry, "result": {"status": "finished", **result}}


def _signal_row(
    record: dict[str, Any],
    entry: dict[str, Any],
    signal: dict[str, Any],
    history: list[dict[str, Any]],
    cfg: dict,
) -> dict[str, Any] | None:
    raw_grade = _raw_grade(signal)
    if raw_grade not in TRACKED_RAW_GRADES:
        return None
    prediction = _prediction_result(_settled_match(entry, record), signal)
    outcome = (prediction or {}).get("status")
    ah_shadow = _ah_shadow(entry, signal, cfg)
    movement = _movement_shadow(history, record, entry, signal)
    return {
        "kickoff_at_utc": record.get("kickoff_at_utc"),
        "home_team": record.get("home_team"),
        "away_team": record.get("away_team"),
        "match_label": f"{record.get('home_team')} vs {record.get('away_team')}",
        "closing_snapshot_at": record.get("closing_snapshot_at") or entry.get("snapshot_at"),
        "market_type": signal.get("market_type"),
        "selection": signal.get("selection"),
        "line": signal.get("line"),
        "grade": signal.get("grade"),
        "raw_grade": raw_grade,
        "ev": signal.get("ev"),
        "edge": signal.get("edge"),
        "outcome": outcome,
        "prediction": prediction,
        "reasons": list(signal.get("reasons") or []),
        "ah_validation_shadow": ah_shadow,
        "movement_shadow": movement,
        "source_coverage": {
            "closing_entry": True,
            "ah_shadow": ah_shadow is not None,
            "movement_shadow": movement is not None,
        },
    }


def _coverage_summary(signals: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("closing_entry", "ah_shadow", "movement_shadow")
    return {
        key: sum(1 for signal in signals if (signal.get("source_coverage") or {}).get(key))
        for key in keys
    }


def _records_from_snapshot_or_store(snapshot: dict[str, Any], store_path: str | Path | None = None) -> list[dict]:
    records = (snapshot.get("finished") or {}).get("matches") or []
    if records:
        return list(records)
    if store_path is None:
        return []
    path = Path(store_path)
    if not path.exists():
        return []
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(store, dict):
        return []
    return list(store.values())


def build_shadow_backfill_diagnostics(
    snapshot: dict[str, Any],
    history_dir: str | Path,
    store_path: str | Path | None = None,
    generated_at: str | None = None,
    min_sample: int = 20,
    cfg: dict | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    history = _load_history(history_dir)
    records = _records_from_snapshot_or_store(snapshot, store_path)
    signals: list[dict[str, Any]] = []
    buckets = {
        "by_outcome": _empty_outcomes(),
        "by_grade": {},
        "by_raw_grade": {},
        "by_market": {},
        "by_ah_candidate_validated": {},
        "by_movement_supports_signal": {},
        "by_both_shadow_support": {},
    }
    missing_closing = 0

    for record in records:
        entry = _closing_entry(history, record)
        if entry is None:
            missing_closing += 1
            continue
        for signal in entry.get("signals") or []:
            row = _signal_row(record, entry, signal, history, cfg)
            if row is None:
                continue
            signals.append(row)
            outcome = row.get("outcome")
            if outcome in OUTCOME_KEYS:
                buckets["by_outcome"][outcome] += 1
            _bump(buckets["by_grade"], str(row.get("grade")), outcome)
            _bump(buckets["by_raw_grade"], str(row.get("raw_grade")), outcome)
            _bump(buckets["by_market"], str(row.get("market_type")), outcome)

            ah_shadow = row.get("ah_validation_shadow") or {}
            movement = row.get("movement_shadow") or {}
            ah_key = _bucket_key(ah_shadow.get("candidate_validated") if ah_shadow else None)
            movement_key = _bucket_key(movement.get("supports_signal") if movement else None)
            both_key = _bucket_key(
                bool(ah_shadow.get("candidate_validated")) and bool(movement.get("supports_signal"))
                if ah_shadow and movement
                else None
            )
            _bump(buckets["by_ah_candidate_validated"], ah_key, outcome)
            _bump(buckets["by_movement_supports_signal"], movement_key, outcome)
            _bump(buckets["by_both_shadow_support"], both_key, outcome)

    decided = buckets["by_outcome"]["hit"] + buckets["by_outcome"]["miss"]
    return {
        "schema_version": 1,
        "generated_at": generated_at or _now_utc_iso(),
        "snapshot_at": snapshot.get("snapshot_at"),
        "research_boundary": RESEARCH_BOUNDARY,
        "summary": {
            "match_count": len(records),
            "raw_strong_signal_count": len(signals),
            "decided_raw_strong_signal_count": decided,
            "min_sample": min_sample,
            "sample_too_small": decided < min_sample,
            "missing_closing_entry": missing_closing,
            "source_coverage": _coverage_summary(signals),
        },
        "buckets": buckets,
        "signals": signals,
        "observations": [
            "Backfilled shadows are research diagnostics only.",
            "Historical recomputation may differ from what was stored at the time.",
            "Small samples are observations, not tuning evidence.",
        ],
    }


def write_report(report: dict[str, Any], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local shadow backfill diagnostics report.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--store", default="data/local/finished_record_store.json")
    parser.add_argument("--out", default="data/local/diagnostics/shadow_backfill_diagnostics.json")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)

    snapshot = _read_json(args.snapshot) if Path(args.snapshot).exists() else {}
    report = build_shadow_backfill_diagnostics(
        snapshot,
        args.history,
        store_path=args.store,
        generated_at=args.generated_at,
        min_sample=args.min_sample,
    )
    write_report(report, args.out)
    print(
        json.dumps(
            {
                "status": "ok",
                "out": str(args.out),
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
