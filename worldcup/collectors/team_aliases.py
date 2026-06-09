from __future__ import annotations

import re
import unicodedata

from worldcup.collectors.models import TeamAliasResult


_KNOWN_TEAM_NAMES = (
    "Algeria",
    "Argentina",
    "Australia",
    "Austria",
    "Belgium",
    "Bosnia & Herzegovina",
    "Bosnia and Herzegovina",
    "Brazil",
    "Canada",
    "Cape Verde",
    "Colombia",
    "Croatia",
    "Curaçao",
    "Czech Republic",
    "Czechia",
    "DR Congo",
    "Ecuador",
    "Egypt",
    "England",
    "France",
    "Germany",
    "Ghana",
    "Haiti",
    "Iran",
    "Iraq",
    "Ivory Coast",
    "Japan",
    "Jordan",
    "Mexico",
    "Morocco",
    "Netherlands",
    "New Zealand",
    "Norway",
    "Panama",
    "Paraguay",
    "Portugal",
    "Qatar",
    "Saudi Arabia",
    "Scotland",
    "Senegal",
    "South Africa",
    "South Korea",
    "Spain",
    "Sweden",
    "Switzerland",
    "Tunisia",
    "Turkey",
    "USA",
    "United States",
    "Uruguay",
    "Uzbekistan",
)

_EXPLICIT_ALIASES = {
    "bosnia & herzegovina": "bosnia_herzegovina",
    "bosnia and herzegovina": "bosnia_herzegovina",
    "czech republic": "czech_republic",
    "czechia": "czech_republic",
    "usa": "united_states",
    "united states": "united_states",
}


def _slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower().replace("&", " and ")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", ascii_value)).strip("_")


KNOWN_CANONICAL_KEYS = {
    _EXPLICIT_ALIASES.get(name.lower(), _slugify(name)) for name in _KNOWN_TEAM_NAMES
}


def canonicalize_team(name: str) -> str:
    stripped = name.strip()
    key = stripped.lower()
    return _EXPLICIT_ALIASES.get(key, _slugify(stripped))


def match_team_alias(name: str) -> TeamAliasResult:
    canonical = canonicalize_team(name)
    if canonical in KNOWN_CANONICAL_KEYS:
        return TeamAliasResult(name, canonical)
    return TeamAliasResult(name, None, name)

