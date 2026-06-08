from __future__ import annotations


def combine_1x2(elo: dict, poisson: dict, w_elo: float, w_poisson: float) -> dict:
    keys = ("home", "draw", "away")
    mixed = {k: w_elo * elo[k] + w_poisson * poisson[k] for k in keys}
    total = sum(mixed.values())
    if total <= 0:
        raise ValueError("combined probabilities must have positive mass")
    return {k: v / total for k, v in mixed.items()}
