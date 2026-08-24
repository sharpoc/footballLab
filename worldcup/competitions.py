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
    settlement_rule: str = "football_90min"
    identity_policy: str = "club_identity_registry"
    model_family: str = "club_elo_poisson_pending_v1"
    refresh_priority: int = 50
    quota_budget: str = "shared_free_tier"
    market_quality_profile: str = "domestic_league_pending"
    theoddsapi_sport_key: str | None = None
    theoddsapi_candidate_keys: tuple[str, ...] = ()
    theoddsapi_search_terms: tuple[str, ...] = ()
    markets: tuple[str, ...] = ("h2h", "spreads", "totals")
    metadata: Mapping[str, str] = field(default_factory=dict)
    pipeline_family: str = "legacy"
    prediction_policy: str = "existing"
    result_policy: str = "football_90min"
    statistics_scope: str = "existing"
    runtime_status: str = "enabled"

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
            "settlement_rule": self.settlement_rule,
            "identity_policy": self.identity_policy,
            "model_family": self.model_family,
            "refresh_priority": self.refresh_priority,
            "quota_budget": self.quota_budget,
            "market_quality_profile": self.market_quality_profile,
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
        identity_policy="national_team_alias",
        model_family="worldcup_elo_poisson_v1",
        refresh_priority=100,
        quota_budget="worldcup_free_tier",
        market_quality_profile="worldcup_main",
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
        refresh_priority=70,
        quota_budget="csl_free_tier",
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
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_epl",
        theoddsapi_candidate_keys=("soccer_epl",),
        theoddsapi_search_terms=("English Premier League", "EPL", "Premier League"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "efl_championship_2026_27": CompetitionConfig(
        id="efl_championship_2026_27",
        name="英冠 2026/27",
        kind="domestic_league",
        country="GB-ENG",
        season="2026/27",
        timezone="Europe/London",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_efl_champ",
        theoddsapi_candidate_keys=("soccer_efl_champ",),
        theoddsapi_search_terms=("English Championship", "EFL Championship"),
    ),
    "laliga_2026_27": CompetitionConfig(
        id="laliga_2026_27",
        name="西甲 2026/27",
        kind="domestic_league",
        country="ES",
        season="2026/27",
        timezone="Europe/Madrid",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_spain_la_liga",
        theoddsapi_candidate_keys=("soccer_spain_la_liga",),
        theoddsapi_search_terms=("La Liga", "Spanish La Liga"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "bundesliga_2026_27": CompetitionConfig(
        id="bundesliga_2026_27",
        name="德甲 2026/27",
        kind="domestic_league",
        country="DE",
        season="2026/27",
        timezone="Europe/Berlin",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_germany_bundesliga",
        theoddsapi_candidate_keys=("soccer_germany_bundesliga",),
        theoddsapi_search_terms=("Bundesliga", "German Bundesliga"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "bundesliga2_2026_27": CompetitionConfig(
        id="bundesliga2_2026_27",
        name="德乙 2026/27",
        kind="domestic_league",
        country="DE",
        season="2026/27",
        timezone="Europe/Berlin",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_germany_bundesliga2",
        theoddsapi_candidate_keys=("soccer_germany_bundesliga2",),
        theoddsapi_search_terms=("2. Bundesliga", "Bundesliga 2", "German second Bundesliga"),
    ),
    "ligue_1_2026_27": CompetitionConfig(
        id="ligue_1_2026_27",
        name="法甲 2026/27",
        kind="domestic_league",
        country="FR",
        season="2026/27",
        timezone="Europe/Paris",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_france_ligue_one",
        theoddsapi_candidate_keys=("soccer_france_ligue_one",),
        theoddsapi_search_terms=("Ligue 1", "French Ligue 1"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "serie_a_2026_27": CompetitionConfig(
        id="serie_a_2026_27",
        name="意甲 2026/27",
        kind="domestic_league",
        country="IT",
        season="2026/27",
        timezone="Europe/Rome",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_italy_serie_a",
        theoddsapi_candidate_keys=("soccer_italy_serie_a",),
        theoddsapi_search_terms=("Serie A", "Italian Serie A"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "allsvenskan_2026": CompetitionConfig(
        id="allsvenskan_2026",
        name="瑞典超 2026",
        kind="domestic_league",
        country="SE",
        season="2026",
        timezone="Europe/Stockholm",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_sweden_allsvenskan",
        theoddsapi_candidate_keys=("soccer_sweden_allsvenskan",),
        theoddsapi_search_terms=("Allsvenskan", "Swedish Allsvenskan"),
    ),
    "eliteserien_2026": CompetitionConfig(
        id="eliteserien_2026",
        name="挪超 2026",
        kind="domestic_league",
        country="NO",
        season="2026",
        timezone="Europe/Oslo",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_norway_eliteserien",
        theoddsapi_candidate_keys=("soccer_norway_eliteserien",),
        theoddsapi_search_terms=("Eliteserien", "Norwegian Eliteserien"),
    ),
    "superliga_2026_27": CompetitionConfig(
        id="superliga_2026_27",
        name="丹超 2026/27",
        kind="domestic_league",
        country="DK",
        season="2026/27",
        timezone="Europe/Copenhagen",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_denmark_superliga",
        theoddsapi_candidate_keys=("soccer_denmark_superliga",),
        theoddsapi_search_terms=("Superliga", "Danish Superliga"),
    ),
    "veikkausliiga_2026": CompetitionConfig(
        id="veikkausliiga_2026",
        name="芬超 2026",
        kind="domestic_league",
        country="FI",
        season="2026",
        timezone="Europe/Helsinki",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_finland_veikkausliiga",
        theoddsapi_candidate_keys=("soccer_finland_veikkausliiga",),
        theoddsapi_search_terms=("Veikkausliiga", "Finnish Veikkausliiga"),
    ),
    "liga_mx_2026": CompetitionConfig(
        id="liga_mx_2026",
        name="墨西哥超 2026",
        kind="domestic_league",
        country="MX",
        season="2026",
        timezone="America/Mexico_City",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_mexico_ligamx",
        theoddsapi_candidate_keys=("soccer_mexico_ligamx",),
        theoddsapi_search_terms=("Liga MX", "Mexican Liga MX"),
    ),
    "j1_league_2026": CompetitionConfig(
        id="j1_league_2026",
        name="J联赛 2026",
        kind="domestic_league",
        country="JP",
        season="2026",
        timezone="Asia/Tokyo",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_japan_j_league",
        theoddsapi_candidate_keys=("soccer_japan_j_league",),
        theoddsapi_search_terms=("J League", "J1 League", "Japanese J League"),
    ),
    "k_league_1_2026": CompetitionConfig(
        id="k_league_1_2026",
        name="K联赛 2026",
        kind="domestic_league",
        country="KR",
        season="2026",
        timezone="Asia/Seoul",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_korea_kleague1",
        theoddsapi_candidate_keys=("soccer_korea_kleague1",),
        theoddsapi_search_terms=("K League 1", "Korean K League 1"),
    ),
    "serie_a_brazil_2026": CompetitionConfig(
        id="serie_a_brazil_2026",
        name="巴西甲 2026",
        kind="domestic_league",
        country="BR",
        season="2026",
        timezone="America/Sao_Paulo",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_brazil_campeonato",
        theoddsapi_candidate_keys=("soccer_brazil_campeonato",),
        theoddsapi_search_terms=("Brazilian Serie A", "Brazil Serie A", "Brasileirao"),
        pipeline_family="league_v1",
        prediction_policy="market_consensus_until_club_rating_verified",
        result_policy="verified_football_90min",
        statistics_scope="observed_schema_v2_match_pick_only",
        runtime_status="disabled_until_live_acceptance",
    ),
    "mls_2026": CompetitionConfig(
        id="mls_2026",
        name="美职联 2026",
        kind="domestic_league",
        country="US",
        season="2026",
        timezone="America/New_York",
        fixture_policy="dry_run_probe",
        refresh_policy="daily_odds_sidecar",
        model_family="club_elo_poisson_probe_v1",
        refresh_priority=40,
        quota_budget="shared_free_tier",
        theoddsapi_sport_key="soccer_usa_mls",
        theoddsapi_candidate_keys=("soccer_usa_mls",),
        theoddsapi_search_terms=("Major League Soccer", "MLS"),
    ),
}

FORMAL_SINGLE_MATCH_IDS = (
    "serie_a_2026_27",
    "serie_a_brazil_2026",
    "laliga_2026_27",
    "epl_2026_27",
    "bundesliga_2026_27",
    "ligue_1_2026_27",
)


def list_competitions() -> list[CompetitionConfig]:
    return list(_REGISTRY.values())


def formal_single_match_competitions() -> tuple[CompetitionConfig, ...]:
    return tuple(_REGISTRY[competition_id] for competition_id in FORMAL_SINGLE_MATCH_IDS)


def get_competition(competition_id: str) -> CompetitionConfig:
    try:
        return _REGISTRY[competition_id]
    except KeyError as exc:
        raise KeyError(f"competition_not_configured: {competition_id}") from exc


def competition_block(competition_id: str) -> dict[str, str]:
    return get_competition(competition_id).snapshot_block()
