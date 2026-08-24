from worldcup.theoddsapi_keys import (
    LEGACY_PROVIDER,
    PRIMARY_PROVIDER,
    SECONDARY_PROVIDER,
    TERTIARY_PROVIDER,
    choose_key_slot,
    quota_remaining_for_scheduler,
)


def test_choose_key_slot_prefers_primary_when_not_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 31}, SECONDARY_PROVIDER: {"remaining": 497}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "primary"
    assert selected.provider == PRIMARY_PROVIDER
    assert selected.slot == "primary"


def test_choose_key_slot_uses_legacy_key_as_primary():
    env = {"THE_ODDS_API_KEY": "legacy-primary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 31}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "legacy-primary"
    assert selected.provider == PRIMARY_PROVIDER


def test_choose_key_slot_rotates_to_secondary_when_primary_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {PRIMARY_PROVIDER: {"remaining": 0}, SECONDARY_PROVIDER: {"remaining": 497}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "secondary"
    assert selected.provider == SECONDARY_PROVIDER
    assert selected.slot == "secondary"


def test_choose_key_slot_rotates_when_current_slot_reaches_low_quota_threshold():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 30},
        SECONDARY_PROVIDER: {"remaining": 497},
    }

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.provider == SECONDARY_PROVIDER
    assert selected.slot == "secondary"


def test_choose_key_slot_rotates_to_unknown_tertiary_after_secondary_is_low():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
        "THE_ODDS_API_KEY_TERTIARY": "tertiary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 26},
    }

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "tertiary"
    assert selected.provider == TERTIARY_PROVIDER
    assert selected.slot == "tertiary"


def test_choose_key_slot_rotates_to_unknown_quaternary_after_tertiary_is_low():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
        "THE_ODDS_API_KEY_TERTIARY": "tertiary",
        "THE_ODDS_API_KEY_QUATERNARY": "quaternary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 26},
        TERTIARY_PROVIDER: {"remaining": 10},
    }

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "quaternary"
    assert selected.provider == "theoddsapi_quaternary"
    assert selected.slot == "quaternary"


def test_choose_key_slot_rotates_to_unknown_quinary_after_quaternary_is_low():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
        "THE_ODDS_API_KEY_TERTIARY": "tertiary",
        "THE_ODDS_API_KEY_QUATERNARY": "quaternary",
        "THE_ODDS_API_KEY_QUINARY": "quinary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 26},
        TERTIARY_PROVIDER: {"remaining": 10},
        "theoddsapi_quaternary": {"remaining": 30},
    }

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.api_key == "quinary"
    assert selected.provider == "theoddsapi_quinary"
    assert selected.slot == "quinary"


def test_choose_key_slot_uses_low_quota_reserve_when_no_fresh_slot_remains():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
        "THE_ODDS_API_KEY_TERTIARY": "tertiary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 26},
        TERTIARY_PROVIDER: {"remaining": 10},
    }

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.provider == SECONDARY_PROVIDER


def test_choose_key_slot_returns_none_when_all_configured_slots_exhausted():
    env = {
        "THE_ODDS_API_KEY_PRIMARY": "primary",
        "THE_ODDS_API_KEY_SECONDARY": "secondary",
        "THE_ODDS_API_KEY_TERTIARY": "tertiary",
    }
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 0},
        TERTIARY_PROVIDER: {"remaining": 0},
    }

    assert choose_key_slot(env, providers) is None


def test_unknown_remaining_is_usable():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary"}
    providers = {PRIMARY_PROVIDER: {"remaining": None}}

    selected = choose_key_slot(env, providers)

    assert selected is not None
    assert selected.provider == PRIMARY_PROVIDER


def test_quota_remaining_for_scheduler_uses_selected_slot():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 42},
        LEGACY_PROVIDER: {"remaining": 0},
    }

    assert quota_remaining_for_scheduler(providers, env) == 42


def test_quota_remaining_for_scheduler_returns_zero_when_all_configured_slots_exhausted():
    env = {"THE_ODDS_API_KEY_PRIMARY": "primary", "THE_ODDS_API_KEY_SECONDARY": "secondary"}
    providers = {
        PRIMARY_PROVIDER: {"remaining": 0},
        SECONDARY_PROVIDER: {"remaining": 0},
        LEGACY_PROVIDER: {"remaining": 497},
    }

    assert quota_remaining_for_scheduler(providers, env) == 0


def test_quota_remaining_for_scheduler_falls_back_to_legacy_without_slots():
    assert quota_remaining_for_scheduler({LEGACY_PROVIDER: {"remaining": 17}}, {}) == 17
