from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

EM_DASH = "\u2014"
GRADE_ORDER = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
STRONG_GRADES = {"S", "A"}
WATCH_GRADES = {"B"}
WEAK_GRADES = {"C", "D"}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dedupe_stable(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(value)
    return deduped


def _quality_values(snapshot: dict[str, Any], key: str) -> list[Any]:
    data_quality = snapshot.get("data_quality") or {}
    run = snapshot.get("run") or {}
    if key in data_quality:
        return _dedupe_stable(_as_list(data_quality.get(key)))
    return _dedupe_stable(_as_list(run.get(key)))


def _selection_label(selection: str | None) -> str:
    labels = {
        "home": "Home",
        "away": "Away",
        "draw": "Draw",
        "over": "Over",
        "under": "Under",
    }
    if selection is None:
        return EM_DASH
    normalized = str(selection).lower()
    if normalized.startswith("home_"):
        normalized = "home"
    elif normalized.startswith("away_"):
        normalized = "away"
    return labels.get(normalized, str(selection))


def _line_label(line: Any, signed_positive: bool = False) -> str:
    if line is None:
        return ""
    try:
        value = float(line)
        sign = "+" if signed_positive and value > 0 else ""
        return f" {sign}{value:g}"
    except (TypeError, ValueError):
        return f" {line}"


def _signal_line(signal: dict[str, Any]) -> Any:
    for key in ("line", "total", "handicap"):
        if key in signal:
            return signal.get(key)
    return None


def _selection_key(selection: Any) -> str | None:
    if selection is None:
        return None
    normalized = str(selection).lower()
    if normalized.startswith("home_"):
        return "home"
    if normalized.startswith("away_"):
        return "away"
    return normalized


def _signal_model_prob(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    direct = signal.get("model_prob")
    if direct is not None:
        return direct
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    model = match.get("model") or {}
    if market_type == "1X2_90min":
        return (model.get("combined_1x2") or {}).get(selection)
    if market_type == "OverUnder_90min":
        return (model.get("ou_2_5") or {}).get(selection)
    return None


def _signal_market_prob(match: dict[str, Any], signal: dict[str, Any]) -> float | None:
    direct = signal.get("market_prob")
    if direct is not None:
        return direct
    market_type = signal.get("market_type")
    selection = _selection_key(signal.get("selection"))
    market = match.get("market") or {}
    if market_type == "1X2_90min":
        return ((market.get("1x2") or {}).get("probs") or {}).get(selection)
    if market_type == "OverUnder_90min":
        return ((market.get("ou_2_5") or {}).get("market_probs") or {}).get(selection)
    return None


def format_percent(value: float | None, signed: bool = True) -> str:
    if value is None:
        return EM_DASH
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.1f}%"


def format_probability(value: float | None) -> str:
    return format_percent(value, signed=False)


def format_market_label(market_type: str | None, selection: str | None, line: float | None) -> str:
    selection_label = _selection_label(selection)
    if market_type == "1X2_90min":
        return f"1X2 - {selection_label}"
    if market_type == "OverUnder_90min":
        return f"O/U{_line_label(line)} - {selection_label}"
    if market_type == "AsianHandicap_90min":
        return f"AH{_line_label(line, signed_positive=True)} - {selection_label}"
    return f"{market_type or 'Market'} - {selection_label}"


def derive_quality_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if _quality_values(snapshot, "source_errors"):
        reasons.append("source_errors")
        return {"label": "ATTENTION", "tone": "error", "reasons": reasons}

    for key in ("stale_sources", "missing_odds", "missing_elo", "time_mismatches"):
        if _quality_values(snapshot, key):
            reasons.append(key)
    if reasons:
        return {"label": "WARN", "tone": "warn", "reasons": reasons}
    return {"label": "GOOD", "tone": "ok", "reasons": []}


def build_signal_explanation(signal: dict[str, Any], stale: bool) -> str:
    if stale:
        return "Signal is capped because one or more inputs are stale or missing."
    market_type = signal.get("market_type")
    if market_type == "1X2_90min":
        return "Model probability is above the devigged market probability."
    if market_type == "OverUnder_90min":
        return "Model total-goals distribution differs from the market total."
    if market_type == "AsianHandicap_90min":
        return "Settlement EV is positive at the current handicap line."
    return "Model and market estimates differ enough to flag for review."


def _format_kickoff_date(parsed_kickoff: datetime | None) -> str:
    if parsed_kickoff is None:
        return "Date unavailable"
    return f"{parsed_kickoff.strftime('%A, %b')} {parsed_kickoff.day}, {parsed_kickoff.year}"


def project_signal_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    stale = bool(_quality_values(snapshot, "stale_sources"))
    rows: list[dict[str, Any]] = []
    for match in snapshot.get("matches", []):
        kickoff_at_utc = match.get("kickoff_at_utc", "")
        parsed_kickoff = _parse_datetime(kickoff_at_utc)
        home_team = match.get("home_team", "")
        away_team = match.get("away_team", "")
        for signal in match.get("signals") or []:
            market_type = signal.get("market_type")
            selection = signal.get("selection")
            line = _signal_line(signal)
            row = {
                "matchup": f"{home_team} vs {away_team}",
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_at_utc": kickoff_at_utc,
                "kickoff_date": _format_kickoff_date(parsed_kickoff),
                "kickoff_time": parsed_kickoff.strftime("%H:%M") if parsed_kickoff else EM_DASH,
                "stage": match.get("stage", ""),
                "group": match.get("group", ""),
                "market_type": market_type,
                "market_label": format_market_label(market_type, selection, line),
                "model_prob": format_probability(_signal_model_prob(match, signal)),
                "market_prob": format_probability(_signal_market_prob(match, signal)),
                "edge": format_percent(signal.get("edge")),
                "ev": format_percent(signal.get("ev")),
                "grade": signal.get("grade", ""),
                "status": signal.get("status", ""),
                "freshness": "stale" if stale else "fresh",
                "stale": stale,
                "explanation": build_signal_explanation(signal, stale),
            }
            rows.append(row)

    rows.sort(
        key=lambda row: (
            row["kickoff_at_utc"],
            -GRADE_ORDER.get(row.get("grade", ""), 0),
            row["matchup"],
            row["market_label"],
        )
    )
    return rows


def build_summary_metrics(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = project_signal_rows(snapshot)
    grade_counts = Counter(row.get("grade", "") for row in rows)
    quality = derive_quality_status(snapshot)
    return {
        "upcoming_matches": {"label": "Upcoming matches", "value": len(snapshot.get("matches") or [])},
        "strong_signals": {
            "label": "Strong signals",
            "value": sum(grade_counts[grade] for grade in STRONG_GRADES),
        },
        "watch_signals": {
            "label": "Watch signals",
            "value": sum(grade_counts[grade] for grade in WATCH_GRADES),
        },
        "weak_signals": {
            "label": "Weak signals",
            "value": sum(grade_counts[grade] for grade in WEAK_GRADES),
        },
        "grade_counts": {
            "label": "Grade counts",
            "value": {grade: grade_counts[grade] for grade in sorted(grade_counts)},
        },
        "stale_sources": {"label": "Stale sources", "value": len(_quality_values(snapshot, "stale_sources"))},
        "overall_quality": {
            "label": "Overall quality",
            "value": quality["label"],
            "tone": quality["tone"],
            "reasons": quality["reasons"],
        },
    }
