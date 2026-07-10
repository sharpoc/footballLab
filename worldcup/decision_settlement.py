from __future__ import annotations

from collections import Counter
import math
from typing import Any, Iterable


NO_PICK_LABELS = {"NO_CLEAN_MARKET", "NO_PICK", "ABSTAIN"}
LEGACY_PICK_LABELS = {
    "STRONG_VALUE",
    "VALUE_CANDIDATE",
    "HIGH_CONFIDENCE_LEAN",
    "LOW_CONFIDENCE_LEAN",
}
STATUS_LABELS = {
    "hit": "命中",
    "miss": "未中",
    "push": "走水",
    "no_pick": "暂无可靠首选",
    "missing_decision": "历史首选未记录",
    "invalid_decision": "首选记录无效",
    "unresolved": "赛果待确认",
}


def _as_score(result: dict[str, Any] | None) -> tuple[int, int] | None:
    if not isinstance(result, dict):
        return None
    try:
        home, away = int(result["home_score"]), int(result["away_score"])
    except (KeyError, TypeError, ValueError):
        return None
    return (home, away) if home >= 0 and away >= 0 else None


def _as_line(value: Any) -> float | None:
    try:
        line = float(value)
    except (TypeError, ValueError):
        return None
    return line if math.isfinite(line) else None


def _selection(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized.startswith("home_"):
        return "home"
    if normalized.startswith("away_"):
        return "away"
    return normalized or None


def _settlement_unit(score_margin: float, line: float) -> float:
    x4 = round(line * 4)
    if abs(line * 4 - x4) > 1e-9:
        raise ValueError("line must be a quarter increment")
    if x4 % 4 in (0, 2):
        adjusted = score_margin + line
        if adjusted > 1e-9:
            return 1.0
        if adjusted < -1e-9:
            return -1.0
        return 0.0
    return 0.5 * _settlement_unit(score_margin, line - 0.25) + 0.5 * _settlement_unit(
        score_margin,
        line + 0.25,
    )


def _unit_result(unit: float) -> tuple[str, str]:
    if unit >= 0.75:
        return "hit", "full_win"
    if unit > 0.0:
        return "hit", "half_win"
    if unit <= -0.75:
        return "miss", "full_loss"
    if unit < 0.0:
        return "miss", "half_loss"
    return "push", "push"


def _result(
    status: str,
    *,
    detail: str = "",
    settlement_class: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "status": status,
        "label": STATUS_LABELS[status],
        "detail": detail,
    }
    if settlement_class is not None:
        out["settlement_class"] = settlement_class
    return out


def settle_match_decision(
    decision: dict[str, Any] | None,
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Settle one frozen closing decision without consulting legacy grades."""

    if not isinstance(decision, dict):
        return _result("missing_decision")
    label = str(decision.get("label") or "")
    if label in NO_PICK_LABELS:
        return _result("no_pick")
    try:
        schema_version = int(decision.get("schema_version") or 1)
    except (TypeError, ValueError):
        return _result("invalid_decision")
    if schema_version == 2:
        if label != "MATCH_PICK":
            return _result("invalid_decision")
    elif label not in LEGACY_PICK_LABELS:
        return _result("invalid_decision")

    market = str(decision.get("market") or "")
    selection = _selection(decision.get("selection"))
    if not market or selection is None:
        return _result("invalid_decision")
    score = _as_score(result)
    if score is None:
        return _result("unresolved")
    home_score, away_score = score
    score_text = f"全场 {home_score}-{away_score}"

    if market == "1X2":
        if selection not in {"home", "draw", "away"}:
            return _result("invalid_decision")
        actual = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        status = "hit" if selection == actual else "miss"
        settlement_class = "full_win" if status == "hit" else "full_loss"
        return _result(status, detail=score_text, settlement_class=settlement_class)

    if market in {"DNB", "AH"}:
        if selection not in {"home", "away"}:
            return _result("invalid_decision")
        line = _as_line(decision.get("line"))
        if line is None and market == "DNB":
            line = 0.0
        if line is None:
            return _result("invalid_decision")
        margin = home_score - away_score if selection == "home" else away_score - home_score
        try:
            status, settlement_class = _unit_result(_settlement_unit(margin, line))
        except ValueError:
            return _result("invalid_decision")
        return _result(status, detail=score_text, settlement_class=settlement_class)

    if market == "OU":
        if selection not in {"over", "under"}:
            return _result("invalid_decision")
        line = _as_line(decision.get("line"))
        if line is None:
            return _result("invalid_decision")
        total = home_score + away_score
        margin = -total if selection == "under" else total
        settlement_line = line if selection == "under" else -line
        try:
            status, settlement_class = _unit_result(
                _settlement_unit(margin, settlement_line)
            )
        except ValueError:
            return _result("invalid_decision")
        return _result(status, detail=score_text, settlement_class=settlement_class)

    return _result("invalid_decision")


def summarize_decision_records(
    records: Iterable[dict[str, Any]],
    *,
    min_sample: int = 20,
    skipped_no_closing: int = 0,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    record_count = 0
    for record in records:
        record_count += 1
        decision = record.get("closing_match_decision")
        if isinstance(decision, dict):
            try:
                schema_version = int(decision.get("schema_version") or 1)
            except (TypeError, ValueError):
                schema_version = -1
            if schema_version != 2:
                counts["legacy_decision"] += 1
                continue
        settled = settle_match_decision(
            decision,
            record.get("result"),
        )
        counts[settled["status"]] += 1

    tally = {key: counts[key] for key in ("hit", "miss", "push", "no_pick")}
    decided = tally["hit"] + tally["miss"]
    actionable = decided + tally["push"]
    decision_count = actionable + tally["no_pick"]
    missing = counts["missing_decision"]
    invalid = counts["invalid_decision"]
    unresolved = counts["unresolved"]
    legacy = counts["legacy_decision"]
    return {
        "decision_tally": tally,
        "sample": {
            "min_sample": min_sample,
            "decided": decided,
            "actionable": actionable,
            "decision_count": decision_count,
            "sample_too_small": decided < min_sample,
            "hit_rate": (tally["hit"] / decided if decided else None),
            "pick_rate": (actionable / decision_count if decision_count else None),
        },
        "coverage": {
            "finished_result_count": record_count + int(skipped_no_closing),
            "closing_available_count": record_count,
            "missing_closing_count": int(skipped_no_closing),
            "decision_available_count": decision_count,
            "missing_decision_count": missing,
            "invalid_decision_count": invalid,
            "unresolved_count": unresolved,
            "legacy_decision_count": legacy,
        },
    }
