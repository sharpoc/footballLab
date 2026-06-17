"""Post-match diagnostics for finished strong signals.

Reads local snapshot/history files only. The report is a research diagnostic:
it explains where finished S/A signals hit or missed, without changing model
parameters, signal grades, refresh behavior, or online state.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACKED_GRADES = ("S", "A")
OUTCOME_KEYS = ("hit", "miss", "push")
RESEARCH_BOUNDARY = "仅用于研究分析，不构成投注建议"
RAW_ACTIVE_GAP_THRESHOLD = 0.05

_OUTCOME_LABELS = {
    "命中": "hit",
    "未中": "miss",
    "走水": "push",
    "hit": "hit",
    "miss": "miss",
    "push": "push",
}


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_outcomes() -> dict[str, int]:
    return {key: 0 for key in OUTCOME_KEYS}


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_history(history_dir: str | Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted(Path(history_dir).glob("snapshot_*.json")):
        try:
            snapshots.append(_read_json(path))
        except (OSError, ValueError):
            continue
    return snapshots


def _outcome_key(signal: dict[str, Any]) -> str | None:
    label = ((signal.get("prediction") or {}).get("label")) or signal.get("outcome")
    if label is None:
        return None
    return _OUTCOME_LABELS.get(str(label), str(label).lower())


def _selection_key(selection: Any) -> str | None:
    if selection is None:
        return None
    normalized = str(selection).lower()
    if normalized.startswith("home_"):
        return "home"
    if normalized.startswith("away_"):
        return "away"
    return normalized


def _line_equal(left: Any, right: Any) -> bool:
    if left in (None, "") and right in (None, ""):
        return True
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return left == right


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
    for snapshot in sorted(
        (item for item in history if item.get("snapshot_at")),
        key=lambda item: item["snapshot_at"],
        reverse=True,
    ):
        found = _match_record_entry(snapshot, record)
        if found is not None:
            return found
    return None


def _signal_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left.get("market_type") == right.get("market_type")
        and str(left.get("selection")) == str(right.get("selection"))
        and _line_equal(left.get("line"), right.get("line"))
    )


def _full_signal(entry: dict[str, Any] | None, signal: dict[str, Any]) -> dict[str, Any]:
    if entry is None:
        return {}
    for candidate in entry.get("signals") or []:
        if _signal_matches(candidate, signal):
            return candidate
    return {}


def _family_probability(
    family: dict[str, Any],
    family_name: str,
    market_type: str | None,
    selection: str | None,
) -> float | None:
    if selection is None:
        return None
    if market_type == "1X2_90min":
        key = "1x2" if family_name == "market_only" else "combined_1x2"
        value = (family.get(key) or {}).get(selection)
    elif market_type == "OverUnder_90min":
        value = ((family.get("ou") or {}).get("probs") or {}).get(selection)
    else:
        value = None
    return float(value) if isinstance(value, (int, float)) else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _probability_diagnostics(entry: dict[str, Any] | None, signal: dict[str, Any]) -> tuple[dict, dict]:
    frozen_probs = signal.get("probability_family_probs")
    frozen_deltas = signal.get("probability_family_deltas")
    if isinstance(frozen_probs, dict) and isinstance(frozen_deltas, dict):
        return frozen_probs, frozen_deltas

    families_block = (((entry or {}).get("model") or {}).get("probability_families") or {})
    families = families_block.get("families") or {}
    active = families_block.get("active_signal_family") or "model_market_total"
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    probs = {
        name: _family_probability(families.get(name) or {}, name, market_type, selection)
        for name in ("model_raw", "model_market_total", "market_only")
    }
    active_prob = _family_probability(
        families.get(active) or {},
        active,
        market_type,
        selection,
    )
    raw = probs.get("model_raw")
    market = probs.get("market_only")
    deltas = {
        "active_family": active,
        "model_raw_minus_active": _round(raw - active_prob)
        if raw is not None and active_prob is not None
        else None,
        "active_minus_market": _round(active_prob - market)
        if active_prob is not None and market is not None
        else None,
    }
    return probs, deltas


def _movement_quality(entry: dict[str, Any] | None) -> dict[str, Any]:
    frozen = (entry or {}).get("odds_movement_quality")
    if isinstance(frozen, dict):
        return {
            "enough_points": bool(frozen.get("enough_points")),
            "line_changed": bool(frozen.get("line_changed")),
            "sparse": bool(frozen.get("sparse")),
        }
    quality = (((entry or {}).get("odds_movement") or {}).get("quality") or {})
    return {
        "enough_points": bool(quality.get("enough_points")),
        "line_changed": bool(quality.get("line_changed")),
        "sparse": bool(quality.get("sparse")),
    }


def _diagnostic_flags(outcome: str | None, movement: dict, deltas: dict) -> list[str]:
    flags: list[str] = []
    if outcome:
        flags.append(outcome)
    if movement.get("line_changed"):
        flags.append("line_changed")
    if movement.get("sparse"):
        flags.append("sparse_history")
    gap = deltas.get("model_raw_minus_active")
    if isinstance(gap, (int, float)) and abs(gap) >= RAW_ACTIVE_GAP_THRESHOLD:
        flags.append("raw_active_gap_ge_5pp")
    return flags


def _bump_bucket(bucket: dict[str, dict[str, int]], key: str, outcome: str | None) -> None:
    if outcome not in OUTCOME_KEYS:
        return
    entry = bucket.setdefault(key, _empty_outcomes())
    entry[outcome] += 1


def _signal_row(
    record: dict[str, Any],
    signal: dict[str, Any],
    entry: dict[str, Any] | None,
) -> dict[str, Any]:
    full = _full_signal(entry, signal)
    signal_source = full or signal
    reasons = list(full.get("reasons") or signal.get("reasons") or [])
    outcome = _outcome_key(signal)
    probs, deltas = _probability_diagnostics(entry, signal)
    movement = _movement_quality(signal if signal.get("odds_movement_quality") else entry)
    coverage = {
        "closing_entry": entry is not None,
        "full_signal": bool(full) or signal.get("diagnostic_schema_version") == 2,
        "reason": bool(reasons),
        "probability_family": any(value is not None for value in probs.values()),
        "odds_movement": bool(signal.get("odds_movement_quality"))
        or bool(((entry or {}).get("odds_movement") or {})),
    }
    return {
        "kickoff_at_utc": record.get("kickoff_at_utc"),
        "home_team": record.get("home_team"),
        "away_team": record.get("away_team"),
        "match_label": f"{record.get('home_team')} vs {record.get('away_team')}",
        "result": record.get("result") or {},
        "closing_snapshot_at": record.get("closing_snapshot_at"),
        "market_type": signal.get("market_type"),
        "selection": signal.get("selection"),
        "line": signal.get("line"),
        "grade": signal.get("grade"),
        "raw_grade": signal_source.get("raw_grade") or signal.get("grade"),
        "odds": signal.get("odds"),
        "ev": signal_source.get("ev"),
        "edge": signal_source.get("edge"),
        "outcome": outcome,
        "reasons": reasons,
        "probability_family_probs": probs,
        "probability_family_deltas": deltas,
        "odds_movement_quality": movement,
        "source_coverage": coverage,
        "diagnostic_flags": list(signal.get("diagnostic_flags") or _diagnostic_flags(outcome, movement, deltas)),
    }


def _source_coverage_summary(signals: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "closing_entry",
        "full_signal",
        "reason",
        "probability_family",
        "odds_movement",
    )
    return {
        key: sum(1 for signal in signals if (signal.get("source_coverage") or {}).get(key))
        for key in keys
    }


def build_postmatch_diagnostics(
    snapshot: dict[str, Any],
    history_dir: str | Path,
    generated_at: str | None = None,
    min_sample: int = 20,
) -> dict[str, Any]:
    history = _load_history(history_dir)
    records = (snapshot.get("finished") or {}).get("matches") or []
    signals: list[dict[str, Any]] = []
    buckets = {
        "by_outcome": _empty_outcomes(),
        "by_grade": {},
        "by_market": {},
        "by_reason": {},
    }

    for record in records:
        entry = _closing_entry(history, record)
        for signal in record.get("closing_signals") or []:
            grade = signal.get("grade")
            if grade not in TRACKED_GRADES:
                continue
            row = _signal_row(record, signal, entry)
            signals.append(row)
            outcome = row.get("outcome")
            if outcome in OUTCOME_KEYS:
                buckets["by_outcome"][outcome] += 1
            _bump_bucket(buckets["by_grade"], str(grade), outcome)
            _bump_bucket(buckets["by_market"], str(signal.get("market_type")), outcome)
            for reason in row.get("reasons") or ["no_reason"]:
                _bump_bucket(buckets["by_reason"], str(reason), outcome)

    decided = buckets["by_outcome"]["hit"] + buckets["by_outcome"]["miss"]
    return {
        "schema_version": 1,
        "generated_at": generated_at or _now_utc_iso(),
        "snapshot_at": snapshot.get("snapshot_at"),
        "research_boundary": RESEARCH_BOUNDARY,
        "summary": {
            "match_count": len(records),
            "strong_signal_count": len(signals),
            "decided_strong_signal_count": decided,
            "min_sample": min_sample,
            "sample_too_small": decided < min_sample,
            "skipped_no_closing": (snapshot.get("finished") or {}).get("skipped_no_closing", 0),
            "source_coverage": _source_coverage_summary(signals),
        },
        "buckets": buckets,
        "signals": signals,
        "observations": [
            "Diagnostics describe finished S/A signals only.",
            "Small samples are observations, not tuning evidence.",
        ],
    }


def write_report(report: dict[str, Any], out_path: str | Path) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build local post-match diagnostics report.")
    parser.add_argument("--snapshot", default="data/cache/analysis_snapshot.json")
    parser.add_argument("--history", default="data/local/history")
    parser.add_argument("--out", default="data/local/diagnostics/postmatch_diagnostics.json")
    parser.add_argument("--min-sample", type=int, default=20)
    parser.add_argument("--generated-at", default=None)
    args = parser.parse_args(argv)

    snapshot = _read_json(args.snapshot)
    report = build_postmatch_diagnostics(
        snapshot,
        args.history,
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
