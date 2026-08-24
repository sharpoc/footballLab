from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from worldcup.competitions import FORMAL_SINGLE_MATCH_IDS


@dataclass(frozen=True)
class LeagueTeamIdentityResult:
    source_name: str
    canonical: str | None
    reason: str | None = None


class LeagueTeamIdentityRegistry:
    def __init__(self, groups: Mapping[str, Mapping[str, Sequence[str]]]) -> None:
        self._aliases: dict[str, dict[str, str]] = {}
        for competition_id, competition_groups in groups.items():
            if competition_id not in FORMAL_SINGLE_MATCH_IDS:
                raise ValueError("league_team_competition_not_allowed")
            aliases: dict[str, str] = {}
            for canonical, names in competition_groups.items():
                canonical_key = str(canonical).strip()
                if not canonical_key:
                    raise ValueError("league_team_canonical_invalid")
                for name in names:
                    key = str(name).strip().casefold()
                    if not key:
                        raise ValueError("league_team_alias_invalid")
                    existing = aliases.get(key)
                    if existing is not None and existing != canonical_key:
                        raise ValueError(f"league_team_alias_ambiguous:{competition_id}:{key}")
                    aliases[key] = canonical_key
            self._aliases[competition_id] = aliases

    def resolve(self, competition_id: str, provider_name: str) -> LeagueTeamIdentityResult:
        source = str(provider_name).strip()
        if competition_id not in FORMAL_SINGLE_MATCH_IDS:
            return LeagueTeamIdentityResult(source, None, "competition_not_allowed")
        canonical = self._aliases.get(competition_id, {}).get(source.casefold())
        return LeagueTeamIdentityResult(source, canonical, None if canonical else "unmatched_team")

    def resolve_fixture(self, competition_id: str, home: str, away: str) -> dict[str, str | None]:
        home_result = self.resolve(competition_id, home)
        away_result = self.resolve(competition_id, away)
        if home_result.canonical is None or away_result.canonical is None:
            return {
                "status": "blocked",
                "reason": "unmatched_team",
                "home_canonical": home_result.canonical,
                "away_canonical": away_result.canonical,
            }
        if home_result.canonical == away_result.canonical:
            return {
                "status": "blocked",
                "reason": "same_team_identity",
                "home_canonical": home_result.canonical,
                "away_canonical": away_result.canonical,
            }
        return {
            "status": "verified",
            "reason": None,
            "home_canonical": home_result.canonical,
            "away_canonical": away_result.canonical,
        }
