from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CompetitionConfig:
    id: str
    name: str
    kind: str
    country: str
    season: str
    timezone: str = "UTC"
    source: str = "theoddsapi"
    fixture_source: str = "explicit_fixture_source"
    fixture_policy: str = "explicit_fixture_source"
    rating_policy: str = "club_rating_pending"
    refresh_policy: str = "local_daily"
    window_days: int = 14
    theoddsapi_sport_key: str | None = None
    theoddsapi_candidate_keys: tuple[str, ...] = ()
    theoddsapi_search_terms: tuple[str, ...] = ()
    markets: tuple[str, ...] = ("h2h", "spreads", "totals")
    metadata: Mapping[str, str] = field(default_factory=dict)

    def snapshot_block(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "country": self.country,
            "season": self.season,
            "source": self.source,
            "fixture_source": self.fixture_source,
            "rating_policy": self.rating_policy,
        }


_REGISTRY: dict[str, CompetitionConfig] = {
    "fifa_world_cup_2026": CompetitionConfig(
        id="fifa_world_cup_2026",
        name="2026 世界杯",
        kind="tournament",
        country="international",
        season="2026",
        source="openfootball + theoddsapi",
        fixture_source="openfootball",
        fixture_policy="openfootball",
        rating_policy="national_team_elo",
        refresh_policy="worldcup_free_tier",
        window_days=60,
        theoddsapi_sport_key="soccer_fifa_world_cup",
        theoddsapi_candidate_keys=("soccer_fifa_world_cup",),
        theoddsapi_search_terms=("FIFA World Cup", "World Cup"),
    ),
    "csl_2026": CompetitionConfig(
        id="csl_2026",
        name="中超 2026",
        kind="domestic_league",
        country="CN",
        season="2026",
        timezone="Asia/Shanghai",
        source="theoddsapi",
        fixture_source="odds_event_only",
        fixture_policy="odds_event_window",
        rating_policy="club_rating_pending",
        refresh_policy="local_daily",
        window_days=14,
        theoddsapi_sport_key=None,
        theoddsapi_candidate_keys=("soccer_china_superleague", "soccer_china_super_league"),
        theoddsapi_search_terms=("Chinese Super League", "China Super League", "CSL"),
    ),
    "epl_2026_27": CompetitionConfig(
        id="epl_2026_27",
        name="英超 2026/27",
        kind="domestic_league",
        country="GB-ENG",
        season="2026/27",
        timezone="Europe/London",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_epl",),
        theoddsapi_search_terms=("English Premier League", "EPL", "Premier League"),
    ),
    "laliga_2026_27": CompetitionConfig(
        id="laliga_2026_27",
        name="西甲 2026/27",
        kind="domestic_league",
        country="ES",
        season="2026/27",
        timezone="Europe/Madrid",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_spain_la_liga",),
        theoddsapi_search_terms=("La Liga", "Spanish La Liga"),
    ),
    "bundesliga_2026_27": CompetitionConfig(
        id="bundesliga_2026_27",
        name="德甲 2026/27",
        kind="domestic_league",
        country="DE",
        season="2026/27",
        timezone="Europe/Berlin",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_germany_bundesliga",),
        theoddsapi_search_terms=("Bundesliga", "German Bundesliga"),
    ),
    "serie_a_2026_27": CompetitionConfig(
        id="serie_a_2026_27",
        name="意甲 2026/27",
        kind="domestic_league",
        country="IT",
        season="2026/27",
        timezone="Europe/Rome",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_italy_serie_a",),
        theoddsapi_search_terms=("Serie A", "Italian Serie A"),
    ),
    "ligue_1_2026_27": CompetitionConfig(
        id="ligue_1_2026_27",
        name="法甲 2026/27",
        kind="domestic_league",
        country="FR",
        season="2026/27",
        timezone="Europe/Paris",
        fixture_policy="dry_run_probe",
        refresh_policy="dry_run_probe",
        theoddsapi_candidate_keys=("soccer_france_ligue_one",),
        theoddsapi_search_terms=("Ligue 1", "French Ligue 1"),
    ),
}


def list_competitions() -> list[CompetitionConfig]:
    return list(_REGISTRY.values())


def get_competition(competition_id: str) -> CompetitionConfig:
    try:
        return _REGISTRY[competition_id]
    except KeyError as exc:
        raise KeyError(f"competition_not_configured: {competition_id}") from exc


def competition_block(competition_id: str) -> dict[str, str]:
    return get_competition(competition_id).snapshot_block()
