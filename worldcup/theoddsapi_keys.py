from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

LEGACY_PROVIDER = "theoddsapi"
PRIMARY_PROVIDER = "theoddsapi_primary"
SECONDARY_PROVIDER = "theoddsapi_secondary"


@dataclass(frozen=True)
class KeySlotSelection:
    api_key: str
    provider: str
    slot: str


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def configured_key_slots(env: Mapping[str, str]) -> tuple[KeySlotSelection, ...]:
    primary = _clean(env.get("THE_ODDS_API_KEY_PRIMARY")) or _clean(env.get("THE_ODDS_API_KEY"))
    secondary = _clean(env.get("THE_ODDS_API_KEY_SECONDARY"))
    slots: list[KeySlotSelection] = []
    if primary:
        slots.append(KeySlotSelection(primary, PRIMARY_PROVIDER, "primary"))
    if secondary:
        slots.append(KeySlotSelection(secondary, SECONDARY_PROVIDER, "secondary"))
    return tuple(slots)


def _remaining(entry: Any) -> int | None:
    if not isinstance(entry, Mapping):
        return None
    value = entry.get("remaining")
    return value if isinstance(value, int) else None


def _is_exhausted(entry: Any) -> bool:
    remaining = _remaining(entry)
    return remaining is not None and remaining <= 0


def choose_key_slot(env: Mapping[str, str], providers: Mapping[str, Any]) -> KeySlotSelection | None:
    slots = configured_key_slots(env)
    if not slots:
        return None
    for slot in slots:
        if not _is_exhausted(providers.get(slot.provider)):
            return slot
    return None


def quota_remaining_for_scheduler(
    providers: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> int | None:
    env = env or {}
    slots = configured_key_slots(env)
    if slots:
        selected = choose_key_slot(env, providers)
        if selected is None:
            return 0
        return _remaining(providers.get(selected.provider))
    return _remaining(providers.get(LEGACY_PROVIDER))
