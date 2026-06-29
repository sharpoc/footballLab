# P9.23 CSL Ops Runner Design

## Goal

Build one local command for the CSL practical loop: inspect current CSL odds/cache state, generate a league snapshot, archive pre-match snapshots, write a safe observation report, and optionally run the post-match evaluation loop when local results exist.

This is a research operations runner. It does not provide betting advice, does not show stake sizing, and does not lift `club_rating_pending`.

## Recommendation

Use a staged runner with explicit modes:

1. `--dry-run` default: read local files, print the planned actions and safety blockers, write nothing, do not read `.env`, do not call The Odds API.
2. `--run-local`: use existing local CSL odds cache to run `league_runner`, write a local snapshot, archive it, and write an observation report under ignored local paths.
3. `--live-odds`: before local processing, run the existing CSL odds refresh with quota and source-fetch guards. This mode must be explicit and must be confirmed separately before use because it reads `.env` and consumes The Odds API quota.

This keeps the normal daily workflow safe while still allowing controlled live runs when needed.

## Scope

In scope:

- Add `worldcup.csl_ops_runner`.
- Reuse existing modules instead of duplicating logic:
  - `league_odds_refresh`
  - `league_runner`
  - `csl_snapshot_archive`
  - `csl_observation_report`
  - `csl_postmatch_runner`
- Produce one safe summary JSON for each run.
- Keep all generated artifacts in ignored `data/local/` or `data/cache/` paths.
- Add tests for dry-run safety, local-run writes, postmatch optional behavior, and live-mode guardrails.

Out of scope:

- No model tuning.
- No signal threshold changes.
- No `club_rating_pending` lift.
- No ECS deploy.
- No scheduled publish.
- No LaunchAgent install or update.
- No public CSL page publication.
- No automatic The Odds API live call in default mode.

## User Workflow

Daily safe check:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner
```

Use current cache and write local research artifacts:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local
```

After manually adding or confirming final scores, run the post-match loop:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --run-local --postmatch
```

Controlled live odds refresh remains a separate confirmed action:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_ops_runner --live-odds --run-local
```

## Data Flow

Dry-run:

1. Read only the narrow CSL local files needed for the runner summary.
2. Inspect whether `data/cache/theoddsapi_csl_2026_odds.json` exists and parses.
3. Inspect whether `data/cache/club_results_csl_2026.csv` exists and parses.
4. Inspect current CSL history directory coverage.
5. Inspect quota only from the configured quota ledger path, returning a safe `quota_remaining` count without provider names or raw payload.
6. Print planned actions and blockers.

Local run:

1. Build CSL snapshot from local cache via `league_runner`.
2. Write snapshot to `data/local/diagnostics/csl_live_league_snapshot.json`.
3. Archive snapshot into `data/local/diagnostics/csl_history/`.
4. Write observation report under `data/cache/` or `data/local/diagnostics/`.
5. Return a safe summary with counts, paths, warnings, `rating_policy`, and `strong_grades`.

Postmatch:

1. Read archived CSL snapshots and local results CSV.
2. Build eval CSV via `csl_eval_data`.
3. Run existing backtest report.
4. Feed market baseline into `csl_pending_gate`.
5. Keep `can_lift_club_rating_pending=false` unless a future, separately approved design changes that policy.

Live odds:

1. Require explicit `--live-odds`.
2. Load `.env` only inside the existing live refresh boundary.
3. Use source-fetch hardening and quota ledger.
4. If refresh fails, do not overwrite valid cache with bad data.
5. Continue local processing only when cache is valid, or report a blocker.

## Output Contract

The runner summary should include:

- `status`: `ok`, `warn`, `blocked`, or `error`
- `mode`: `dry_run`, `local`, or `live_odds_local`
- `competition_id`
- `generated_at`
- `steps`: per-step status and safe counts
- `paths`: only local relative paths, no secrets
- `data_quality`: selected safe warnings
- `postmatch`: optional sample counts and report paths
- `safety`: booleans for `read_env`, `called_theoddsapi`, `published`, `deployed`, `changed_launch_agent`

The summary must not include raw bookmaker rows, per-book prices, API keys, HMAC secrets, env values, request headers, stake amounts, or betting instructions.

## Accuracy Policy

The runner improves practical accuracy by preserving evidence, not by changing model math.

For CSL evaluation, the first-class metrics are:

- closing snapshot coverage
- `skipped_no_closing`
- Brier score
- Log Loss
- calibration bins
- model vs market baseline
- model vs home-prior baseline
- sample size and sample age

Hit rate can be shown only as secondary context. If the sample is small, closing coverage is weak, or the model trails market/home-prior baselines, conclusions must remain observational and no tuning should be applied.

## Error Handling

- Missing odds cache: dry-run reports `blocked / missing_odds_cache`; local run exits non-zero without writing snapshot.
- Invalid odds cache: report parse failure without printing raw payload.
- Missing results CSV: postmatch step is skipped or blocked, depending on whether `--postmatch` was requested.
- No archived pre-match snapshots: postmatch reports `skipped_no_closing` and cannot claim accuracy.
- Low or unknown quota in live mode: live refresh blocks unless explicitly forced by a future separately approved option.
- Existing archive duplicate: treat as safe `duplicate`, not a failure.

## Testing

Add focused tests for:

- default dry-run does not read `.env`, does not call live refresh, and writes nothing
- local run calls existing local modules and writes only ignored local/cache outputs
- postmatch mode uses archived snapshots and local results, and keeps pending gate conservative
- live mode requires explicit flag and uses existing safe fetch/quota boundaries
- summary output is safe and does not expose raw odds, bookmaker rows, secrets, env values, or stake language
- error paths for missing cache, invalid cache, missing results, and no closing snapshots

Run the full project test suite before completion:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

## Adversarial Review

- Root cause: the main blocker for CSL accuracy is not another model tweak; it is reliable capture of pre-match snapshots and honest post-match evaluation.
- Scope risk: a runner can accidentally become deployment or publication plumbing. This design explicitly excludes ECS, scheduled publish, public CSL pages, and LaunchAgent changes.
- Live risk: The Odds API quota is limited. Default mode must stay offline, and live mode must remain explicit.
- Data risk: one or two CSL rounds are not enough to tune model parameters. Small samples must remain observation-only.
- Semantic risk: `club_rating_pending` protects users from over-trusting a young CSL model. P9.23 must not lift it.
- Security risk: summaries must stay sanitized and must not print `.env`, API keys, raw request/response bodies, or raw bookmaker data.
