from __future__ import annotations

from typing import Any, Collection, Iterable, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS

FORMAL_SCOPE = "observed_schema_v2_match_pick_only"
TALLY_KEYS = ("hit", "miss", "push", "no_pick")
COVERAGE_KEYS = (
    "finished_result_count", "closing_available_count", "missing_closing_count",
    "decision_available_count", "missing_decision_count", "invalid_decision_count",
    "unresolved_count", "legacy_decision_count",
)


def crossed_evaluation_thresholds(
    previous_decided: int,
    current_decided: int,
    sent: Collection[int],
    thresholds: Sequence[int] = (20, 50, 100),
) -> list[int]:
    """Return crossed, unsent offline-evaluation milestones in stable order."""
    if (
        isinstance(previous_decided, bool)
        or isinstance(current_decided, bool)
        or not isinstance(previous_decided, int)
        or not isinstance(current_decided, int)
        or previous_decided < 0
        or current_decided < 0
    ):
        raise ValueError("evaluation_decided_count_invalid")
    if current_decided < previous_decided:
        return []
    checked: set[int] = set()
    for threshold in thresholds:
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold <= 0:
            raise ValueError("evaluation_threshold_invalid")
        checked.add(threshold)
    return [
        threshold for threshold in sorted(checked)
        if previous_decided < threshold <= current_decided and threshold not in sent
    ]


def _metrics(tally: dict[str, int], min_sample: int) -> dict[str, Any]:
    decided = tally["hit"] + tally["miss"]
    actionable = decided + tally["push"]
    decision_count = actionable + tally["no_pick"]
    return {
        "min_sample": min_sample, "decided": decided, "actionable": actionable,
        "decision_count": decision_count, "sample_too_small": decided < min_sample,
        "hit_rate": tally["hit"] / decided if decided else None,
        "pick_rate": actionable / decision_count if decision_count else None,
    }


def build_league_statistics(
    blocks: Iterable[dict[str, Any]], *, min_sample: int = 20
) -> dict[str, Any]:
    competitions: dict[str, dict[str, Any]] = {}
    for block in blocks:
        competition_id = str(block.get("competition_id") or "")
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or block.get("statistics_scope") != FORMAL_SCOPE:
            continue
        tally = {key: int((block.get("decision_tally") or {}).get(key, 0)) for key in TALLY_KEYS}
        coverage = {key: int((block.get("decision_coverage") or {}).get(key, 0)) for key in COVERAGE_KEYS}
        competitions[competition_id] = {
            "decision_tally": tally,
            "decision_sample": _metrics(tally, min_sample),
            "decision_coverage": coverage,
        }
    aggregate_tally = {key: sum(row["decision_tally"][key] for row in competitions.values()) for key in TALLY_KEYS}
    aggregate_coverage = {key: sum(row["decision_coverage"][key] for row in competitions.values()) for key in COVERAGE_KEYS}
    return {
        "statistics_scope": FORMAL_SCOPE,
        "competitions": competitions,
        "aggregate": {
            "decision_tally": aggregate_tally,
            "decision_sample": _metrics(aggregate_tally, min_sample),
            "decision_coverage": aggregate_coverage,
        },
    }
