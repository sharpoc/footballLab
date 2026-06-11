# The Odds API Key Rotation Design

## Goal

Add conservative automatic rotation between two local The Odds API keys so scheduled refresh can recover when the active free-tier key is exhausted, without printing secrets, pushing keys, deploying, or changing model/signal behavior.

## Scope

- Support two local key slots: `THE_ODDS_API_KEY_PRIMARY` and `THE_ODDS_API_KEY_SECONDARY`.
- Keep backward compatibility with the existing single-key `THE_ODDS_API_KEY` as the primary slot.
- Rotate only when local quota ledger shows the current slot has `remaining <= 0`.
- If both slots are missing or exhausted, preserve the current `quota_exhausted` behavior.
- Do not retry another key after HTTP/API failure in the same refresh attempt.
- Do not log or expose raw key values.

## Configuration

`.env` may contain:

```bash
THE_ODDS_API_KEY_PRIMARY=
THE_ODDS_API_KEY_SECONDARY=
```

If `THE_ODDS_API_KEY_PRIMARY` is absent, `THE_ODDS_API_KEY` is treated as primary for compatibility. `.env.example` should list empty variable names only.

## Quota Model

Quota ledger remains a JSON file under `data/cache/quota.json`, but The Odds API entries become slot-aware:

```json
{
  "providers": {
    "theoddsapi_primary": {"remaining": 0, "last": 3},
    "theoddsapi_secondary": {"remaining": 497, "last": 3},
    "theoddsapi": {"remaining": 0, "last": 3}
  }
}
```

`theoddsapi` remains as a legacy aggregate/provider key for existing readers. Scheduler quota selection should use the best available slot value from `theoddsapi_primary` and `theoddsapi_secondary`; if only legacy `theoddsapi` exists, use it.

## Selection Rules

1. Load configured slots from `.env` without exposing values.
2. Read quota ledger for slot providers.
3. Choose primary if it has no known exhausted ledger entry.
4. If primary has `remaining <= 0`, choose secondary when configured and not exhausted.
5. If both configured slots are exhausted, scheduler reports `quota_exhausted`.
6. If no key is configured for the selected slot, live refresh raises a missing-key error.

Unknown `remaining` means usable, because The Odds API may omit quota headers.

## Data Flow

`scheduled_publish` calls `scheduled_refresh`; `scheduled_refresh` builds the scheduler report using slot-aware quota selection, then passes the selected key and provider alias into `refresh_runner`.

`refresh_runner` calls `fetch_worldcup_odds` with the selected API key and provider alias. `fetch_worldcup_odds` writes the quota response to that alias and also updates the legacy `theoddsapi` provider entry for existing status views.

## Error Handling

- Missing all keys: raise the existing missing key error, with variable names only.
- Selected key request fails and cache exists: preserve stale-cache fallback and mark `theoddsapi` stale exactly as today.
- Selected key archive/quota failures follow existing behavior; raw secrets are never printed.
- No same-run fallback to the other key on request failure.

## Testing

- Unit test key slot selection from env and ledger.
- Scheduler/report tests verify `quota_exhausted` only when all configured slots are exhausted.
- Scheduled refresh/publish tests verify selected provider alias and key are passed to refresh.
- Source quota tests verify slot provider and legacy provider entries are both updated.
- Existing single-key tests remain compatible.

## Non-Goals

- No request-level fallback from failed primary to secondary.
- No unlimited key pool.
- No key values in docs, tests, commits, logs, or user-visible output.
- No deployment or live refresh as part of implementation.
