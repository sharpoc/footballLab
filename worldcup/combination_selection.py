from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import prod
from typing import Any, Iterable


APPROXIMATION_LABEL = "独立性近似组合分数"


@dataclass(frozen=True)
class CombinationResearch:
    parlay_2: list[dict[str, Any]]
    parlay_3: list[dict[str, Any]]
    rejection_reasons: tuple[str, ...]
    degradation_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        def public_item(item: dict[str, Any]) -> dict[str, Any]:
            result = dict(item)
            result["match_ids"] = sorted(str(value) for value in result.get("match_ids") or [])
            result["teams"] = sorted(str(value) for value in result.get("teams") or [])
            result["markets"] = [str(value) for value in result.get("markets") or []]
            result["selections"] = [value for value in result.get("selections") or []]
            return result

        return {
            "parlay_2": [public_item(item) for item in self.parlay_2],
            "parlay_3": [public_item(item) for item in self.parlay_3],
            "rejection_reasons": list(self.rejection_reasons),
            "degradation_reasons": list(self.degradation_reasons),
        }


def _match_id(pick: dict[str, Any]) -> str:
    return str(pick.get("match_id") or "").strip()


def _teams(pick: dict[str, Any]) -> set[str]:
    return {
        str(pick.get("home_team") or "").strip().casefold(),
        str(pick.get("away_team") or "").strip().casefold(),
    } - {""}


def _probability(pick: dict[str, Any]) -> float | None:
    value = pick.get("prediction_probability")
    if value is None:
        value = (pick.get("match_decision") or {}).get("p_hit_safe")
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0.0 < result <= 1.0 else None


def _market(pick: dict[str, Any]) -> str:
    return str(pick.get("market") or (pick.get("match_decision") or {}).get("market") or "")


def _valid_pick(pick: dict[str, Any]) -> bool:
    decision = pick.get("match_decision") or {}
    return (
        _match_id(pick) != ""
        and str(decision.get("label") or "MATCH_PICK") == "MATCH_PICK"
        and bool(_market(pick))
        and _probability(pick) is not None
    )


def _combination_key(items: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    return tuple(sorted(_match_id(item) for item in items))


def _build_item(items: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    probabilities = [float(_probability(item) or 0.0) for item in items]
    match_ids = {_match_id(item) for item in items}
    teams = set().union(*(_teams(item) for item in items))
    return {
        "match_ids": match_ids,
        "teams": sorted(teams),
        "markets": [_market(item) for item in items],
        "selections": [item.get("selection") for item in items],
        "approximate_score": prod(probabilities),
        "score_label": APPROXIMATION_LABEL,
        "is_calibrated_joint_probability": False,
    }


def _choose_best(items: Iterable[tuple[dict[str, Any], ...]]) -> dict[str, Any] | None:
    candidates = [_build_item(item) for item in items]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -float(item["approximate_score"]),
            tuple(sorted(str(value) for value in item["match_ids"])),
        )
    )
    return candidates[0]


def build_combination_research(top4: Iterable[dict[str, Any]]) -> CombinationResearch:
    picks = tuple(top4)
    rejection: set[str] = set()
    degradation: list[str] = []
    valid = []
    for pick in picks:
        if _valid_pick(pick):
            valid.append(pick)
        else:
            rejection.add("not_settleable")
    if len(valid) < 2:
        degradation.append("fewer_than_2_matches")
    if len(valid) < 3:
        degradation.append("fewer_than_3_matches")

    valid_pairs: list[tuple[dict[str, Any], ...]] = []
    valid_triples: list[tuple[dict[str, Any], ...]] = []
    for size, target in ((2, valid_pairs), (3, valid_triples)):
        for combo in combinations(valid, size):
            ids = [_match_id(item) for item in combo]
            if len(ids) != len(set(ids)):
                rejection.add("same_match_conflict")
                continue
            team_sets = [_teams(item) for item in combo]
            if sum(len(teams) for teams in team_sets) != len(set().union(*team_sets)):
                rejection.add("same_team_conflict")
                continue
            target.append(combo)

    best_2 = _choose_best(valid_pairs)
    best_3 = _choose_best(valid_triples)
    return CombinationResearch(
        parlay_2=[best_2] if best_2 is not None else [],
        parlay_3=[best_3] if best_3 is not None else [],
        rejection_reasons=tuple(sorted(rejection)),
        degradation_reasons=tuple(degradation),
    )
