"""Replay eloratings-style ratings from historical international results.

Purely offline: reads local CSV files only, never contacts external services,
and does not participate in the live pipeline.
"""
from __future__ import annotations

from worldcup.engine.elo import expected_score

DEFAULT_INITIAL_RATING = 1500.0
DEFAULT_HOME_ADV = 100.0

_FINALS_60 = {"FIFA World Cup"}
_MAJOR_50 = {
    "Copa América",
    "Copa America",
    "UEFA Euro",
    "African Cup of Nations",
    "AFC Asian Cup",
    "CONCACAF Championship",
    "Gold Cup",
    "Oceania Nations Cup",
    "Confederations Cup",
}
_LEAGUE_40 = {"UEFA Nations League", "CONCACAF Nations League"}


def k_factor(tournament: str) -> float:
    if tournament in _FINALS_60:
        return 60.0
    if tournament in _MAJOR_50:
        return 50.0
    if tournament in _LEAGUE_40 or "qualification" in tournament.lower():
        return 40.0
    if tournament == "Friendly":
        return 20.0
    return 30.0


def goal_index(margin: int) -> float:
    m = abs(margin)
    if m <= 1:
        return 1.0
    if m == 2:
        return 1.5
    return (11.0 + m) / 8.0


def update_pair(
    rating_home: float,
    rating_away: float,
    home_score: int,
    away_score: int,
    k: float,
    neutral: bool,
    home_adv: float = DEFAULT_HOME_ADV,
) -> tuple[float, float]:
    dr = rating_home - rating_away + (0.0 if neutral else home_adv)
    we = expected_score(dr)
    if home_score > away_score:
        w = 1.0
    elif home_score == away_score:
        w = 0.5
    else:
        w = 0.0
    delta = k * goal_index(home_score - away_score) * (w - we)
    return rating_home + delta, rating_away - delta
