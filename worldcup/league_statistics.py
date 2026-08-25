from __future__ import annotations

from typing import Any, Collection, Iterable, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS
from worldcup.league_postmatch import _existing_records, _payload

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
    excluded: dict[str, str] = {}
    seen: set[str] = set()
    duplicates: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        competition_id = str(block.get("competition_id") or "")
        if competition_id not in FORMAL_SINGLE_MATCH_IDS or block.get("statistics_scope") != FORMAL_SCOPE:
            continue
        expected_partition = block.get("_expected_partition_competition_id")
        if expected_partition is not None and expected_partition != competition_id:
            if isinstance(expected_partition, str) and expected_partition in FORMAL_SINGLE_MATCH_IDS:
                excluded[expected_partition] = "postmatch_partition_mismatch"
            continue
        if competition_id in duplicates:
            continue
        if competition_id in seen:
            competitions.pop(competition_id, None)
            excluded[competition_id] = "postmatch_duplicate"
            duplicates.add(competition_id)
            continue
        seen.add(competition_id)
        try:
            records, missing, receipts = _existing_records(block, competition_id)
            checked = _payload(competition_id, records, missing, receipts)
        except ValueError:
            excluded[competition_id] = "postmatch_invalid"
            continue
        tally = {key: checked["decision_tally"][key] for key in TALLY_KEYS}
        coverage = {key: checked["decision_coverage"][key] for key in COVERAGE_KEYS}
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
        "excluded_competitions": excluded,
        "aggregate": {
            "decision_tally": aggregate_tally,
            "decision_sample": _metrics(aggregate_tally, min_sample),
            "decision_coverage": aggregate_coverage,
        },
    }
