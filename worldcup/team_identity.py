from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from worldcup.collectors.club_aliases import match_known_club_alias


@dataclass(frozen=True)
class TeamIdentityRecord:
    team_id: str
    competition_id: str
    season_id: str
    canonical_key: str
    display_name: str
    aliases: tuple[str, ...]
    provider_team_ids: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    active_from: str | None = None
    active_to: str | None = None


@dataclass(frozen=True)
class TeamIdentityMatch:
    raw_name: str
    competition_id: str
    provider: str | None
    matched: bool
    record: TeamIdentityRecord | None = None
    unmatched_name: str | None = None


_CSL_2026_RECORDS: tuple[TeamIdentityRecord, ...] = (
    TeamIdentityRecord(
        team_id="csl_2026:shanghai_port",
        competition_id="csl_2026",
        season_id="2026",
        canonical_key="shanghai_port",
        display_name="Shanghai Port",
        aliases=("Shanghai Port", "Shanghai SIPG", "Shanghai SIPG FC", "Shanghai Port FC", "上海海港", "上海上港"),
        provider_team_ids={"theoddsapi": ("Shanghai Port", "Shanghai SIPG FC")},
        active_from="2026-01-01",
        active_to="2026-12-31",
    ),
    TeamIdentityRecord(
        team_id="csl_2026:beijing_guoan",
        competition_id="csl_2026",
        season_id="2026",
        canonical_key="beijing_guoan",
        display_name="Beijing Guoan",
        aliases=("Beijing Guoan", "Beijing Guoan FC", "Beijing FC", "北京国安"),
        provider_team_ids={"theoddsapi": ("Beijing Guoan", "Beijing FC")},
        active_from="2026-01-01",
        active_to="2026-12-31",
    ),
    TeamIdentityRecord(
        team_id="csl_2026:shandong_taishan",
        competition_id="csl_2026",
        season_id="2026",
        canonical_key="shandong_taishan",
        display_name="Shandong Taishan",
        aliases=("Shandong Taishan", "Shandong Luneng Taishan", "Shandong Luneng Taishan FC", "山东泰山"),
        provider_team_ids={"theoddsapi": ("Shandong Taishan", "Shandong Luneng Taishan FC")},
        active_from="2026-01-01",
        active_to="2026-12-31",
    ),
    TeamIdentityRecord(
        team_id="csl_2026:yunnan_yukun",
        competition_id="csl_2026",
        season_id="2026",
        canonical_key="yunnan_yukun",
        display_name="Yunnan Yukun",
        aliases=("Yunnan Yukun", "云南玉昆"),
        provider_team_ids={"theoddsapi": ("Yunnan Yukun",)},
        active_from="2026-01-01",
        active_to="2026-12-31",
    ),
    TeamIdentityRecord(
        team_id="csl_2026:henan",
        competition_id="csl_2026",
        season_id="2026",
        canonical_key="henan",
        display_name="Henan FC",
        aliases=(
            "Henan FC",
            "Henan",
            "Henan Songshan Longmen",
            "Henan Jiuzu Dukang",
            "Henan Club Jiuzu Dukang",
            "Henan Club Caitao Fang",
            "河南队",
            "河南",
            "河南俱乐部",
            "河南酒祖杜康",
            "河南俱乐部酒祖杜康",
            "河南俱乐部彩陶坊",
        ),
        provider_team_ids={"theoddsapi": ("Henan FC", "Henan")},
        active_from="2026-01-01",
        active_to="2026-12-31",
    ),
)

_RECORDS_BY_COMPETITION: dict[str, tuple[TeamIdentityRecord, ...]] = {
    "csl_2026": _CSL_2026_RECORDS,
}


def team_identity_records(competition_id: str) -> tuple[TeamIdentityRecord, ...]:
    return _RECORDS_BY_COMPETITION.get(competition_id, ())


def _record_by_canonical(competition_id: str, canonical_key: str) -> TeamIdentityRecord | None:
    for record in team_identity_records(competition_id):
        if record.canonical_key == canonical_key:
            return record
    return None


def resolve_team_identity(
    competition_id: str,
    raw_name: str,
    provider: str | None = None,
) -> TeamIdentityMatch:
    alias = match_known_club_alias(competition_id, raw_name)
    if alias.canonical_key is None:
        return TeamIdentityMatch(
            raw_name=raw_name,
            competition_id=competition_id,
            provider=provider,
            matched=False,
            unmatched_name=alias.unmatched_name or raw_name,
        )
    record = _record_by_canonical(competition_id, alias.canonical_key)
    if record is None:
        return TeamIdentityMatch(
            raw_name=raw_name,
            competition_id=competition_id,
            provider=provider,
            matched=False,
            unmatched_name=raw_name,
        )
    return TeamIdentityMatch(
        raw_name=raw_name,
        competition_id=competition_id,
        provider=provider,
        matched=True,
        record=record,
    )
