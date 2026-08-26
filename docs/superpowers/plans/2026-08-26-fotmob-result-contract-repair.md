# FotMob Result Contract Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the real FotMob result integration after Gate A exposed endpoint, kickoff-field, Brasileirão competition-ID, and terminal-schema drift, while keeping every competition fail-closed until its saved current-season sample passes the strict parser.

**Architecture:** Keep HTTP transport in the existing FotMob source adapters, pure result interpretation in `league_fotmob_results`, and evidence/path enforcement in `league_result_evidence` plus the postmatch runner. Use one shared symlink-safe sample verifier for the runner and a new offline-only evaluator. Operational Gate A remains a separate confirmation after code, saved-sample offline validation, and final review.

**Tech Stack:** Python standard library, `urllib`, existing pure collectors/stores, JSON fixtures, `tests/run_tests.py`.

**Spec:** `docs/superpowers/specs/2026-08-25-six-league-postmatch-loop-design.md`

## Global Constraints

- Scope is the six `FORMAL_SINGLE_MATCH_IDS` competitions only.
- Current routes are `/api/data/matches` and `/api/data/matchDetails`; no silent old-route fallback.
- FotMob IDs are Serie A `55`, Brasileirão `268`, La Liga `87`, Premier League `47`, Bundesliga `54`, Ligue 1 `53`.
- Formal scope remains `football_90min`; missing proof fields fail closed.
- Calendar/detail must agree on finished+FT, score, event, competition, strict teams, and kickoff.
- Detail-only proof requires both extra-half start keys present and empty, penalty loser present and null, aggregate loser present and empty/null.
- Detail kickoff comes only from ISO `general.matchTimeUTCDate`; never parse display `matchTimeUTC` as fallback.
- Implementation and tests must not modify the real project `acceptance.json` or provider evidence. Tests may create isolated acceptance/evidence under `TemporaryDirectory`.
- No real provider requests, formal live/write, WxPusher, LaunchAgent install, push, merge, or deploy during Tasks 1–5.
- The Odds API requests and quota mutations remain zero.
- The project runtime has no pytest. Every RED/GREEN claim uses the full `/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` command.
- Task commits are allowed only when the user has authorized implementation plus local commits for this plan; otherwise stop before each commit.

---

### Task 1: Migrate Every Live FotMob URL Builder and Classify 404

**Files:**
- Modify: `worldcup/sources/league_fotmob_lineups.py`
- Modify: `worldcup/lineup_source_probe.py`
- Modify: `tests/sources/test_league_fotmob_lineups_source.py`
- Modify: `tests/test_lineup_source_probe.py`
- Modify: `tests/test_league_postmatch_runner.py`

**Interfaces:**
- Preserves: `fetch_fotmob_calendar(*, date, transport=None)` and `fetch_fotmob_details(*, match_id, transport=None)`.
- Produces: exact `/api/data/` URLs and `FotMobProviderContractDrift(RuntimeError)` with safe message `fotmob_provider_contract_drift_404`.
- Runner projection: calendar/detail 404 becomes `provider_contract_drift`; timeout/5xx remains `calendar_fetch_failed` or `details_fetch_failed`.

- [ ] **Step 1: Write failing URL tests**

```python
def test_current_fotmob_data_routes_are_used():
    assert build_fotmob_calendar_url("20260825") == "https://www.fotmob.com/api/data/matches?date=20260825"
    assert build_fotmob_details_url("5868020") == "https://www.fotmob.com/api/data/matchDetails?matchId=5868020"
    assert build_fotmob_matches_url("20260825") == "https://www.fotmob.com/api/data/matches?date=20260825"
    assert build_fotmob_match_details_url("5868020") == "https://www.fotmob.com/api/data/matchDetails?matchId=5868020"
```

These cover the shared six-league source and the separate pre-match `lineup_source_probe` builders.

- [ ] **Step 2: Write failing HTTP classification tests**

Use an injected transport that raises `urllib.error.HTTPError(url, 404, "Not Found", {}, None)`. Assert the source raises `FotMobProviderContractDrift("fotmob_provider_contract_drift_404")` without URL/body/header text. Inject 500 and timeout errors separately and assert `RuntimeError("fotmob_transport_failed")`. Add separate runner tests for calendar 404 and detail 404; each must block only the affected competition with `provider_contract_drift`, write no affected receipt, and allow another healthy partition to continue.

- [ ] **Step 3: Run full suite and verify RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected failures: old URL assertions and missing 404-specific exception/projection.

- [ ] **Step 4: Implement minimal shared behavior**

Change both builder families to `/api/data/`. Catch `HTTPError` before the generic catch:

```python
class FotMobProviderContractDrift(RuntimeError):
    pass


try:
    response = (transport or _default_transport)(url)
except HTTPError as exc:
    if exc.code == 404:
        raise FotMobProviderContractDrift("fotmob_provider_contract_drift_404") from None
    raise RuntimeError("fotmob_transport_failed") from None
except Exception:
    raise RuntimeError("fotmob_transport_failed") from None
```

Update runner classification by exception type, never arbitrary message parsing. Do not return exception text.

- [ ] **Step 5: Run full suite and verify GREEN**

Expected: all non-optional tests pass; pre-match source tests remain injected and offline.

- [ ] **Step 6: Conditionally commit Task 1**

```bash
git add worldcup/sources/league_fotmob_lineups.py worldcup/lineup_source_probe.py tests/sources/test_league_fotmob_lineups_source.py tests/test_lineup_source_probe.py tests/test_league_postmatch_runner.py
git commit -m "fix: update FotMob provider routes"
```

---

### Task 2: Parse Real Kickoff and Composite 90-Minute Proof

**Files:**
- Modify: `worldcup/collectors/league_fotmob_results.py`
- Modify: `worldcup/collectors/league_fotmob_lineups.py`
- Modify: `tests/collectors/test_league_fotmob_results.py`
- Modify: `tests/collectors/test_league_fotmob_lineups.py`
- Modify: `tests/test_league_postmatch_runner.py`
- Modify: `tests/test_league_lineups_refresh.py`
- Modify: `tests/fixtures/fotmob_results/calendar_finished.json`
- Modify: `tests/fixtures/fotmob_results/details_1001_finished.json`
- Modify: `tests/fixtures/fotmob_lineups/details_confirmed.json`
- Modify: `tests/fixtures/fotmob_lineups/details_cross_league.json`
- Modify: `tests/fixtures/fotmob_lineups/details_incomplete.json`
- Modify: `tests/fixtures/fotmob_lineups/details_predicted.json`
- Modify: `tests/fixtures/fotmob_lineups/details_unknown.json`

**Interfaces:**
- Preserves: `parse_fotmob_league_results(calendar_payload, detail_payloads, competition_id, *, result_contract_evidence, identity_registry, captured_at) -> dict[str, Any]` and safe result row keys.
- Produces private predicates `_has_terminal_ft(status)` and `_has_detail_90min_proof(status)`; result kickoff reads `general.matchTimeUTCDate` only.

- [ ] **Step 1: Replace fixtures with sanitized real shape**

The detail fixture must retain match/league/team identity and include:

```json
{
  "general": {
    "matchTimeUTC": "Mon, Aug 24, 2026, 19:00 UTC",
    "matchTimeUTCDate": "2026-08-24T19:00:00.000Z"
  },
  "header": {
    "status": {
      "finished": true,
      "reason": {"short": "FT"},
      "scoreStr": "2 - 3",
      "halfs": {"firstExtraHalfStarted": "", "secondExtraHalfStarted": ""},
      "whoLostOnPenalties": null,
      "whoLostOnAggregated": ""
    }
  }
}
```

Calendar retains `finished`, `reason.short=FT`, `scoreStr`, and ISO `utcTime`; remove invented `reason.extraTime`.

- [ ] **Step 2: Write positive and kickoff RED tests**

Assert the saved shape produces one result. Test that display `matchTimeUTC` is ignored; missing, null, non-string, malformed, or naive `matchTimeUTCDate` yields `kickoff_invalid`; exactly five minutes is accepted and five minutes plus one second yields `kickoff_mismatch`; detail `general.matchId` mismatch yields `details_event_mismatch`.

- [ ] **Step 3: Write complete composite-proof RED tests**

For each extra-half key, delete it and replace it independently with a timestamp, list, and dict. Do equivalent cases for missing/non-null penalty loser, missing/non-empty/list/dict aggregate loser, and missing/non-mapping `halfs`. Each returns `result_90min_score_unverified` without throwing. Retain score mismatch, unfinished, AET, PEN, alternate score, boolean, and negative-score tests. Add positive cases for aggregate loser `""` and `None`.

Use an ordinary loop inside one test function because pytest parametrization is unavailable:

```python
def test_extra_half_proof_is_present_empty_and_type_safe():
    for field in ("firstExtraHalfStarted", "secondExtraHalfStarted"):
        for mutation in ("delete", "2026-08-28T21:05:00Z", [], {}):
            details = deepcopy(_details_with_real_proof())
            halfs = details["header"]["status"]["halfs"]
            if mutation == "delete":
                del halfs[field]
            else:
                halfs[field] = mutation
            parsed = _parse(_calendar(status="finished"), {"1001": details})
            assert parsed["results"] == []
            assert parsed["pending"][0]["reason"] == "result_90min_score_unverified"
```

- [ ] **Step 4: Update runner fixtures in the same task**

Modify runner `_status`/detail builders so intended formal FT carries the new proof fields and `general.matchTimeUTCDate`. Tests for missing proof delete fields from an otherwise valid shape. This makes Task 2 independently GREEN.

Update the shared pre-match lineup collector to read `general.matchTimeUTCDate` only. Update all five `tests/fixtures/fotmob_lineups/details_*.json` files listed above so display `matchTimeUTC` and ISO `matchTimeUTCDate` coexist; preserve each fixture's original confirmed/cross-league/incomplete/predicted/unknown semantic purpose. Add collector and refresh-runner tests proving the ISO field is consumed, the display field is ignored, and missing/naive/malformed ISO kickoff remains blocked without affecting other matches.

- [ ] **Step 5: Run full suite and verify RED**

Expected failures: old `extraTime is False`, display kickoff parsing, and malformed aggregate types.

- [ ] **Step 6: Implement minimal strict predicates**

```python
def _has_terminal_ft(status: Mapping[str, Any]) -> bool:
    return status.get("finished") is True and _mapping(status.get("reason")).get("short") == "FT"


def _empty_or_null_scalar(value: Any) -> bool:
    return value is None or value == ""


def _has_detail_90min_proof(status: Mapping[str, Any]) -> bool:
    halfs = status.get("halfs")
    if not isinstance(halfs, Mapping):
        return False
    return (
        _has_terminal_ft(status)
        and "firstExtraHalfStarted" in halfs and halfs.get("firstExtraHalfStarted") == ""
        and "secondExtraHalfStarted" in halfs and halfs.get("secondExtraHalfStarted") == ""
        and "whoLostOnPenalties" in status and status.get("whoLostOnPenalties") is None
        and "whoLostOnAggregated" in status
        and _empty_or_null_scalar(status.get("whoLostOnAggregated"))
    )
```

Require FT+score on both payloads, detail-only proof on detail, exact score agreement, and kickoff from `matchTimeUTCDate`. Preserve safe row keys and treat `captured_at` as local response capture time, not provider update time.

- [ ] **Step 7: Run full suite and verify GREEN**

Expected: all tests pass, including evidence and runner modules.

- [ ] **Step 8: Conditionally commit Task 2**

```bash
git add worldcup/collectors/league_fotmob_results.py worldcup/collectors/league_fotmob_lineups.py tests/collectors/test_league_fotmob_results.py tests/collectors/test_league_fotmob_lineups.py tests/test_league_postmatch_runner.py tests/test_league_lineups_refresh.py tests/fixtures/fotmob_results/calendar_finished.json tests/fixtures/fotmob_results/details_1001_finished.json tests/fixtures/fotmob_lineups/details_confirmed.json tests/fixtures/fotmob_lineups/details_cross_league.json tests/fixtures/fotmob_lineups/details_incomplete.json tests/fixtures/fotmob_lineups/details_predicted.json tests/fixtures/fotmob_lineups/details_unknown.json
git commit -m "fix: verify real FotMob final results"
```

---

### Task 3: Correct Brasileirão ID and Six-League Isolation

**Files:**
- Modify: `worldcup/collectors/league_fotmob_results.py`
- Modify: `tests/collectors/test_league_fotmob_results.py`
- Modify: `tests/test_league_postmatch_runner.py`

**Interfaces:**
- Produces exact provider-ID mapping `55, 268, 87, 47, 54, 53` for the six formal competitions.

- [ ] **Step 1: Add a self-contained provider-ID matrix test**

Create a helper accepting competition ID, league ID, and two provider team names. It builds one calendar/detail pair with the Task 2 real status/kickoff shape, uses `accepted_league_team_identity_registry()`, and derives the sport key from `get_competition(competition_id)`. Loop through:

```python
def _parse_finished_for(competition_id, league_id, home, away):
    evidence = build_result_contract_evidence(
        competition_id=competition_id,
        sport_key=get_competition(competition_id).theoddsapi_sport_key,
        provider_schema="fotmob_league_results_v1",
        score_scope="football_90min",
        source_reference="a" * 64,
        provider="fotmob",
    )
    return parse_fotmob_league_results(
        _calendar(status="finished", league_id=league_id, home=home, away=away),
        {"1001": _details(league_id=league_id, home=home, away=away)},
        competition_id,
        result_contract_evidence=evidence,
        identity_registry=accepted_league_team_identity_registry(),
        captured_at=CAPTURED_AT,
    )
```

Task 2 must first make `_calendar` and `_details` emit the shared real FT/kickoff proof for arbitrary league/team arguments; this helper must not duplicate a second status schema.

```python
cases = (
    ("serie_a_2026_27", 55, "Bologna", "Lazio"),
    ("serie_a_brazil_2026", 268, "Bahia", "Botafogo"),
    ("laliga_2026_27", 87, "Valencia", "Real Betis"),
    ("epl_2026_27", 47, "Fulham", "Chelsea"),
    ("bundesliga_2026_27", 54, "Augsburg", "Bayern Munich"),
    ("ligue_1_2026_27", 53, "Angers", "Lille"),
)
```

Assert one accepted result for every case.

- [ ] **Step 2: Add negative and runner integration tests**

Assert Brasileirão ID `1122` fails with competition mismatch. In `TemporaryDirectory`, write isolated path-bound evidence/acceptance/history fixtures, inject a `268` result through the runner, and assert Task 2 receipt commit. Another test injects invalid Brazil plus valid EPL and proves EPL advances while Brazil is explicitly blocked. No real project runtime file is touched.

- [ ] **Step 3: Run full suite and verify RED**

Expected: `268` cases fail under old `1122` mapping.

- [ ] **Step 4: Change only Brasileirão FotMob ID**

Set `_FOTMOB_COMPETITION_IDS["serie_a_brazil_2026"] = "268"`. Do not change project IDs, The Odds sport keys, or other providers.

- [ ] **Step 5: Run full suite and verify GREEN**

Expected: all tests pass and dry-run remains side-effect free.

- [ ] **Step 6: Conditionally commit Task 3**

```bash
git add worldcup/collectors/league_fotmob_results.py tests/collectors/test_league_fotmob_results.py tests/test_league_postmatch_runner.py
git commit -m "fix: bind Brasileirao FotMob identity"
```

---

### Task 4: Add a Reproducible Offline Saved-Sample Evaluator

**Files:**
- Create: `worldcup/league_fotmob_result_probe.py`
- Modify: `worldcup/league_result_evidence.py`
- Modify: `worldcup/league_postmatch_runner.py`
- Create: `tests/test_league_fotmob_result_probe.py`
- Modify: `tests/test_league_result_evidence.py`
- Modify: `tests/test_league_postmatch_runner.py`

**Interfaces:**
- Produces: `read_fotmob_sample_bytes(root: str | Path, sample_path: str) -> tuple[bytes, str]`, returning bytes plus lowercase SHA-256 from one hardened open/read while preserving runner's exact-root, per-component `lstat`, `O_NOFOLLOW`, inode/dev recheck, and fd-read contract.
- Produces: `evaluate_saved_fotmob_result_bundle(*, root, sample_path, competition_id, captured_at, identity_registry=None) -> dict[str, Any]`.
- Produces: `evaluate_saved_fotmob_result_bundles(*, root: str | Path, entries: Sequence[tuple[str, str]], captured_at: datetime, identity_registry: LeagueTeamIdentityRegistry | None = None) -> dict[str, Any]`, rejecting an empty sequence or duplicate competition ID, sorting competitions, and returning a deterministic aggregate.
- CLI accepts repeated `--entry competition_id=data/probe/path.json` and prints one aggregate JSON document. Optional `--out data/probe/path.json` atomically writes the same bytes only below the chosen root's real non-symlink `data/probe` boundary; without `--out` it never writes.

- [ ] **Step 1: Write sample-binding RED tests**

Refactor target behavior into the public reader. Test valid bytes+digest, traversal/outside path, missing file, symlinked `data/probe`, intermediate/final symlink, and inode replacement. Errors are safe constants without absolute paths or bytes. Runner compares the returned digest with its expected evidence SHA; add runner regressions for wrong and malformed expected SHA. Evaluator reports the digest returned by the same single read and never performs an unsafe preliminary read.

- [ ] **Step 2: Write evaluator schema tests**

Use a temporary bundle with exact top-level keys `schema_version`, `provider`, `competition_id`, `calendar_date`, `observed_league_id`, `calendar`, and `details`. The evaluator hashes safely opened bytes, builds in-memory path-bound evidence, invokes the parser, and returns the exact keys below. Tests assert both fingerprints with `re.fullmatch(r"[0-9a-f]{64}", value)`:

```python
{
    "schema_version": 1,
    "competition_id": "epl_2026_27",
    "sample_path": "data/probe/leagues/results/epl/sample.json",
    "sample_sha256": sample_sha256,
    "evidence_fingerprint": evidence_fingerprint,
    "status": "verified",
    "accepted_result_count": 1,
    "accepted_event_ids": ["5795372"],
    "pending": [],
    "reason": None,
}
```

Exactly one result is required for `verified`. Other calendar events without supplied details remain visible in `pending` but do not invalidate the selected event. Define and test this complete reason taxonomy:

- `target_competition_missing`: no target competition container exists.
- `no_current_season_finished_match`: target container exists but has no finished+FT event.
- `sample_detail_missing`: at least one target finished+FT event exists but no matching detail was supplied.
- `strict_parser_rejected`: matching detail exists but the strict parser accepts zero results.
- `multiple_results_not_allowed`: strict parser accepts more than one result.
- `bundle_competition_mismatch`, `bundle_provider_invalid`, `bundle_schema_invalid`, and `sample_path_invalid`: structural/path failures.

Wrong bundle competition/provider/schema, zero/multiple results, or unsafe path is `blocked`. Parser pending rows remain separately visible and are never promoted as arbitrary top-level reasons.

Aggregate evaluation returns exactly `schema_version`, `captured_at`, `status`, `verified_count`, `blocked_count`, and `competitions`. `competitions` is a key-sorted mapping of single-bundle results; status is `verified` only when every entry verifies, `partial` when at least one verifies, otherwise `blocked`.

- [ ] **Step 3: Write zero-side-effect CLI tests**

Patch socket/URL open functions to fail if called, snapshot temporary files before/after, then call `main(["--root", str(root), "--entry", "epl_2026_27=data/probe/leagues/results/epl/sample.json", "--entry", "bundesliga_2026_27=data/probe/leagues/results/bundesliga/calendar.json", "--captured-at", "2026-08-26T00:00:00+00:00"])` and assert an identical manifest plus key-sorted aggregate output. Add tests rejecting no entries and duplicate competition IDs. Test `--out data/probe/audit.json` writes atomically inside a temporary root, preserves an old file if replace fails, and rejects outside/traversal/symlink output. No acceptance/evidence/state/outbox/lock file may appear. Malformed input exits nonzero with safe JSON and no traceback/path leakage.

- [ ] **Step 4: Run full suite and verify RED**

Expected: missing reader/evaluator/CLI interfaces.

- [ ] **Step 5: Implement shared reader and evaluator**

Extract rather than duplicate runner safe-open logic into `league_result_evidence.py`; runner `_saved_sample_matches` calls it and preserves boolean fail-closed behavior. Evaluator imports parser/evidence/identity only—no fetcher, env loader, store, acceptance writer, or notifier.

- [ ] **Step 6: Run full suite and verify GREEN**

Expected: all tests and existing runner path-binding cases pass.

- [ ] **Step 7: Conditionally commit Task 4**

```bash
git add worldcup/league_fotmob_result_probe.py worldcup/league_result_evidence.py worldcup/league_postmatch_runner.py tests/test_league_fotmob_result_probe.py tests/test_league_result_evidence.py tests/test_league_postmatch_runner.py
git commit -m "feat: audit saved FotMob result samples"
```

---

### Task 5: Offline Gate A Audit, Documentation, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md` only if already present
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md` only after applying its retention rule
- Runtime ignored output: `/Users/eagod/ai-dev/足彩/data/probe/leagues/results/gate_a_offline_recheck_2026-08-26.json`

**Interfaces:**
- Consumes saved ignored bundles through Task 4 CLI.
- Produces ignored aggregate audit only; no provider evidence or acceptance activation.

- [ ] **Step 1: Run offline evaluator for saved samples**

Run one repeated-`--entry` CLI invocation for all six saved bundles with `--out data/probe/leagues/results/gate_a_offline_recheck_2026-08-26.json`. Use exact sample paths recorded by the first Gate A report and a timezone-aware capture time. Expected: four detail bundles verify one selected event; Brazil calendar-only reports `sample_detail_missing`; Bundesliga calendar reports `no_current_season_finished_match`. Never fabricate detail or borrow another season.

- [ ] **Step 2: Update documentation accurately**

Document routes, ID `268`, `matchTimeUTCDate`, composite proof, 404 classification, evaluator command, and remaining Gate A blockers. Synchronize business rules between `AGENTS.md` and `CLAUDE.md` while preserving their intentional Codex/Claude-specific entry paragraphs. Before editing `RECENT_WORK.md`, count actionable entries; if over the 20-entry boundary, stop and ask whether to archive/compress/delete rather than silently cleaning.

- [ ] **Step 3: Run full verification**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
python3 -m py_compile worldcup/sources/league_fotmob_lineups.py worldcup/lineup_source_probe.py worldcup/collectors/league_fotmob_results.py worldcup/collectors/league_fotmob_lineups.py worldcup/league_result_evidence.py worldcup/league_fotmob_result_probe.py worldcup/league_postmatch_runner.py
git diff --check
```

- [ ] **Step 4: Run precise safety scans**

Run `rg -n '\}/(matches|matchDetails)\?' worldcup/sources/league_fotmob_lineups.py worldcup/lineup_source_probe.py` and `rg -n '"serie_a_brazil_2026"\s*:\s*"1122"' worldcup/collectors/league_fotmob_results.py`; both scans must be empty. Exact builder tests remain the primary positive proof for new URLs. Negative tests may retain old literals under `tests/`, and docs/history/ignored audit may describe them. Scan tracked diff for secret values, Cookie/auth headers, raw response metadata, `launchctl`, direct WxPusher, and The Odds quota mutation. Run default runner with before/after manifest and confirm `read_env=false`, `called_fotmob=false`, `wrote=false`, `notified=false`.

- [ ] **Step 5: Request final code review**

Review complete range against spec; fix every Critical/Important finding and rerun Steps 3–4. Review must cover kickoff shape, malformed types, shared path reader, 404 projection, pre-match compatibility, and accidental Gate activation.

- [ ] **Step 6: Conditionally commit Task 5**

Stage only documentation files actually changed; do not hide missing required files with shell fallbacks.

```bash
git add README.md AGENTS.md CLAUDE.md
git add ARCHITECTURE.md  # only if it existed and changed
git add RECENT_WORK.md   # only after retention handling was confirmed
git commit -m "docs: record FotMob result contract repair"
```

---

## Operational Gate A Re-Probe — Separate Confirmation Required

Plan execution does not authorize this section.

1. Present exact dates/events, IDs, request count, ignored paths, and no-activation behavior.
2. Obtain explicit confirmation for real FotMob requests and evidence/acceptance writes.
3. Capture Brazil calendar+detail under `268` and first current-season Bundesliga FT; optionally freshness-check the four earlier samples.
4. Evaluate with Task 4 CLI and report SHA-256, identity, terminal proof, accepted count, and zero The Odds quota change.
5. Atomically save provider-named evidence for each individually verified league, but update the aggregate acceptance/live set only when all six verify and complete registry fingerprints match. Individual evidence persistence is not Gate B activation.
6. If any league remains blocked, Gate A stays partial, aggregate acceptance is not activated, and Gate B is prohibited. Five-of-six activation requires a new business-contract design.

Gate B, Gate C, Gate D, push, PR, merge, and deployment remain separate confirmations.

---

## Adversarial Plan Review

- **Root causes:** routes, 404 classification, ISO kickoff, composite proof, and Brazil ID are all addressed.
- **Shared pre-match impact:** both builder families and injected pre-match tests are included.
- **Semantic safety:** proof fields must exist; malformed list/dict values reject without crashing a league.
- **Reproducibility:** one hardened byte reader serves runner and offline evaluator.
- **Fixture fidelity:** tracked fixtures keep required real fields but remove URLs/metadata; ignored originals remain audit source.
- **Operational safety:** temporary roots are allowed; real acceptance/evidence and network stay behind Gate A.
- **Coverage:** four complete samples exist; Brazil needs detail and Bundesliga needs current-season FT.
- **Rollback:** reverting repair commits restores prior fail-closed behavior; no receipts or ignored samples are deleted.
- **Remaining risk:** FotMob is unofficial; any future missing proof, 404, or unexpected type blocks rather than downgrades validation.
