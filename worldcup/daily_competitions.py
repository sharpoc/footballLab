from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable


@dataclass(frozen=True)
class DailyCompetition:
    name: str
    status: str
    reason: str
    competition_id: str | None = None
    sport_key: str | None = None

    @property
    def enabled(self) -> bool:
        return self.status == "enabled"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "competition_id": self.competition_id,
            "sport_key": self.sport_key,
        }


_VERIFIED = (
    ("中超", "csl_2026", "soccer_china_superleague"),
    ("英超", "epl_2026_27", "soccer_epl"),
    ("英冠", "efl_championship_2026_27", "soccer_efl_champ"),
    ("德甲", "bundesliga_2026_27", "soccer_germany_bundesliga"),
    ("德乙", "bundesliga2_2026_27", "soccer_germany_bundesliga2"),
    ("法甲", "ligue_1_2026_27", "soccer_france_ligue_one"),
    ("意甲", "serie_a_2026_27", "soccer_italy_serie_a"),
    ("西甲", "laliga_2026_27", "soccer_spain_la_liga"),
    ("瑞典超", "allsvenskan_2026", "soccer_sweden_allsvenskan"),
    ("挪超", "eliteserien_2026", "soccer_norway_eliteserien"),
    ("丹超", "superliga_2026_27", "soccer_denmark_superliga"),
    ("芬超", "veikkausliiga_2026", "soccer_finland_veikkausliiga"),
    ("墨西哥超", "liga_mx_2026", "soccer_mexico_ligamx"),
    ("J联赛", "j1_league_2026", "soccer_japan_j_league"),
    ("K联赛", "k_league_1_2026", "soccer_korea_kleague1"),
    ("巴西甲", "serie_a_brazil_2026", "soccer_brazil_campeonato"),
    ("美职联", "mls_2026", "soccer_usa_mls"),
)

_CATALOG_BY_NAME = {
    item.name: item
    for item in tuple(
        DailyCompetition(
            name=name,
            status="enabled",
            reason="已通过真实 /sports 与 /events 验证，provider active 且返回完整未来赛事。",
            competition_id=competition_id,
            sport_key=sport_key,
        )
        for name, competition_id, sport_key in _VERIFIED
    )
}

_CATALOG = tuple(
    _CATALOG_BY_NAME[name]
    for name in (
        "中超",
        "英超",
        "英冠",
        "德甲",
        "德乙",
        "法甲",
        "意甲",
        "西甲",
        "瑞典超",
        "挪超",
        "丹超",
        "芬超",
        "墨西哥超",
        "墨西哥甲",
        "澳超",
        "J联赛",
        "K联赛",
        "巴西甲",
        "阿根廷超",
        "美职联",
    )
    if name in _CATALOG_BY_NAME
) + (
    DailyCompetition(
        name="墨西哥甲",
        status="code_reserved",
        reason="provider /sports 未提供可确认的墨西哥甲主赛事 key；保持 fail-closed。",
    ),
    DailyCompetition(
        name="澳超",
        status="code_reserved",
        reason="provider /sports 未提供可确认的澳超主赛事 key；保持 fail-closed。",
    ),
    DailyCompetition(
        name="阿根廷超",
        status="code_reserved",
        reason="provider /sports 未提供可确认的阿根廷超主赛事 key；保持 fail-closed。",
    ),
)

# Preserve the historical public catalog order while keeping the three disabled
# rows in their original positions.
_CATALOG = tuple(
    next(item for item in _CATALOG if item.name == name)
    for name in (
        "中超", "英超", "英冠", "德甲", "德乙", "法甲", "意甲", "西甲",
        "瑞典超", "挪超", "丹超", "芬超", "墨西哥超", "墨西哥甲", "澳超",
        "J联赛", "K联赛", "巴西甲", "阿根廷超", "美职联",
    )
)



def daily_competition_catalog() -> tuple[DailyCompetition, ...]:
    """Return the fixed daily-picks coverage catalog without probing the network."""
    return _CATALOG


def enabled_competition_ids() -> tuple[str, ...]:
    return tuple(
        item.competition_id
        for item in _CATALOG
        if item.enabled and item.competition_id is not None
    )


def coverage_projection() -> list[dict[str, str | None]]:
    return [item.to_dict() for item in _CATALOG]


def resolve_provider_catalog(
    catalog: Iterable[DailyCompetition],
    sports: Iterable[dict[str, Any]],
) -> tuple[DailyCompetition, ...]:
    """Resolve the fixed whitelist against an exact active provider key list."""
    by_key = {
        str(item.get("key") or "").strip(): item
        for item in sports
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    resolved: list[DailyCompetition] = []
    for item in catalog:
        key = item.sport_key
        provider = by_key.get(key) if key else None
        if key and provider is not None and provider.get("active") is True:
            resolved.append(
                replace(
                    item,
                    status="enabled",
                    reason="provider /sports 返回 exact active sport_key。",
                )
            )
        else:
            resolved.append(
                replace(
                    item,
                    status="provider_unavailable",
                    reason=(
                        "provider /sports 未返回 exact active sport_key；"
                        "不猜测或替换相似联赛。"
                    ),
                )
            )
    return tuple(resolved)
