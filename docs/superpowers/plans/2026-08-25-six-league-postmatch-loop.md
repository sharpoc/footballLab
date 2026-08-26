# Six-League Postmatch Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dry-run-first, FotMob-backed six-league postmatch pipeline that captures verified 90-minute results, joins immutable observed closings, settles picks, maintains isolated and aggregate statistics, and sends deduplicated daily and 20/50/100-stage notifications.

**Architecture:** Keep provider parsing, immutable result storage, closing selection, settlement/statistics, orchestration, notification, and LaunchAgent generation in separate modules. The live runner processes only evidence-active competitions under a non-blocking lock; every unsafe or ambiguous result fails closed without changing an accepted score, and notification delivery is recoverable without repeating provider calls or settlement.

**Tech Stack:** Python 3 standard library, existing FotMob request injection pattern, JSON atomic stores with `fcntl`, existing strict league identity registry, existing settlement/statistics modules, WxPusher adapter, macOS LaunchAgent plist generation.

**Spec:** `docs/superpowers/specs/2026-08-25-six-league-postmatch-loop-design.md`

## Global Constraints

- Formal scope is exactly `observed_schema_v2_match_pick_only` for `FORMAL_SINGLE_MATCH_IDS`.
- Default execution must not read `.env`, call a network, write files, send notifications, or consume The Odds API quota.
- FotMob live acceptance requires saved real samples proving competition, event, strict team identity, terminal status, and 90-minute integer score semantics.
- Do not call The Odds API scores automatically and do not add a paid dependency.
- Never infer completion from kickoff time; only an explicit verified terminal result is formal.
- Never synthesize `missing_closing` cases or rewrite/delete an accepted score.
- World Cup, CSL, legacy, reconstructed, and manual results cannot enter formal league statistics.
- One league failure must not block other leagues; identity/score conflicts fail closed within the affected partition.
- Runtime artifacts remain ignored under `data/probe/`, `data/cache/`, or `data/local/`; never log secrets or raw response headers.
- Real probe, live/write, LaunchAgent installation, first real notification, push, merge, and deployment remain separately confirmed gates.
- Public output remains research-only, with no stake, bankroll, EV/Edge, or execution advice.

---

## File Map

- `worldcup/collectors/league_fotmob_results.py`: pure parsing of saved calendar/detail result payloads.
- `worldcup/league_result_evidence.py`: extend the existing evidence contract to accept verified FotMob result schemas without weakening The Odds API compatibility.
- `worldcup/league_result_store.py`: monotonic, partition-safe accepted-result store and conflict detection.
- `worldcup/league_postmatch_planner.py`: pure due-event planning from acceptance, fixtures, and terminal receipts.
- `worldcup/league_postmatch_runner.py`: dry-run/live orchestration, locking, provider injection, atomic commit ordering, and safe projections.
- `worldcup/league_postmatch_notifications.py`: daily digest and threshold events plus durable outbox/sent receipts.
- `worldcup/league_postmatch_launch_agent.py`: deterministic plist generator only; never calls `launchctl`.
- `worldcup/league_closing.py`: preserve the existing selection contract; add merge/store behavior only if required for cumulative history.
- `worldcup/league_postmatch.py`: keep settlement pure; add strict cumulative merge validation only if the runner cannot compose it externally.
- `worldcup/league_statistics.py`: retain formal scope and expose deterministic threshold counts.
- `tests/collectors/test_league_fotmob_results.py`, `tests/test_league_result_store.py`, `tests/test_league_postmatch_planner.py`, `tests/test_league_postmatch_runner.py`, `tests/test_league_postmatch_notifications.py`, `tests/test_league_postmatch_launch_agent.py`: focused TDD coverage.
- `README.md`, `AGENTS.md`, `CLAUDE.md`, `RECENT_WORK.md`: implemented contract, safety gates, commands, and recent status.

---

### Task 1: FotMob Result Parser and Evidence Contract

**Files:**
- Create: `worldcup/collectors/league_fotmob_results.py`
- Modify: `worldcup/league_result_evidence.py`
- Test: `tests/collectors/test_league_fotmob_results.py`
- Test: `tests/test_league_result_evidence.py`

**Interfaces:**
- Consumes: `LeagueTeamIdentityRegistry`, `FORMAL_SINGLE_MATCH_IDS`, saved FotMob calendar/detail mappings, evidence payloads from `league_result_evidence`.
- Produces: `parse_fotmob_league_results(calendar_payload: dict[str, Any], detail_payloads: Mapping[str, dict[str, Any]], competition_id: str, *, result_contract_evidence: Mapping[str, Any] | None, identity_registry: LeagueTeamIdentityRegistry, captured_at: datetime) -> dict[str, Any]`.
- Private helper: `_parse_verified_rows(calendar_payload: dict[str, Any], detail_payloads: Mapping[str, dict[str, Any]], competition_id: str, *, identity_registry: LeagueTeamIdentityRegistry, captured_at: datetime) -> dict[str, Any]` performs field-level validation after evidence acceptance.
- Produces safe output keys: `competition_id`, `results`, `pending`, `source_events`, `source_fingerprint`; each accepted result uses `source_event_id`, `kickoff_at_utc`, `home_team`, `away_team`, `home_canonical`, `away_canonical`, `home_score`, `away_score`, `captured_at`, `result_scope="football_90min"`, and `source_fingerprint`.

- [ ] **Step 1: Write failing parser tests using complete saved-shape fixtures**

```python
def test_finished_integer_score_with_strict_identity_is_accepted():
    parsed = parse_fotmob_league_results(
        _calendar(status="finished"),
        {"1001": _details(home_score=2, away_score=1)},
        "epl_2026_27",
        result_contract_evidence=_fotmob_evidence("epl_2026_27"),
        identity_registry=_registry(),
        captured_at=datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc),
    )
    assert parsed["results"][0]["result_scope"] == "football_90min"
    assert (parsed["results"][0]["home_score"], parsed["results"][0]["away_score"]) == (2, 1)

def test_unverified_semantics_and_wrong_competition_fail_closed():
    args = (_calendar(status="finished"), {"1001": _details(home_score=2, away_score=1)}, "epl_2026_27")
    kwargs = {"identity_registry": _registry(), "captured_at": datetime(2026, 8, 29, tzinfo=timezone.utc)}
    assert parse_fotmob_league_results(*args, result_contract_evidence=None, **kwargs)["results"] == []
    try:
        parse_fotmob_league_results(
            _wrong_competition_calendar(),
            {"1001": _details(home_score=2, away_score=1)},
            "epl_2026_27",
            result_contract_evidence=_fotmob_evidence("epl_2026_27"),
            identity_registry=_registry(),
            captured_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert str(exc) == "fotmob_result_competition_mismatch"
    else:
        raise AssertionError("wrong competition must fail closed")
```

Also cover scheduled/postponed/live states, duplicate event IDs, missing details, non-integer/negative scores, swapped teams, unknown aliases, kickoff mismatch, extra-time/penalty-only fields, naive `captured_at`, and duplicate competition containers.

- [ ] **Step 2: Run the parser/evidence tests and verify RED**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: FAIL because the FotMob result parser/evidence provider contract does not exist.

- [ ] **Step 3: Implement the minimal pure parser and evidence validation**

Use explicit field extraction; never fall back to slugs or alternate score fields. Extend evidence validation with provider schema `fotmob_league_results_v1` and score scope `football_90min`, while preserving existing accepted evidence fingerprints.

```python
def parse_fotmob_league_results(
    calendar_payload: dict[str, Any],
    detail_payloads: Mapping[str, dict[str, Any]],
    competition_id: str,
    *,
    result_contract_evidence: Mapping[str, Any] | None,
    identity_registry: LeagueTeamIdentityRegistry,
    captured_at: datetime,
) -> dict[str, Any]:
    if not verify_result_contract_evidence(result_contract_evidence, competition_id):
        return _pending_all(calendar_payload, "result_90min_semantics_unverified")
    return _parse_verified_rows(
        calendar_payload,
        detail_payloads,
        competition_id,
        identity_registry=identity_registry,
        captured_at=captured_at,
    )
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Expected: all new parser/evidence tests pass without network access.

- [ ] **Step 5: Commit Task 1**

```bash
git add worldcup/collectors/league_fotmob_results.py worldcup/league_result_evidence.py tests/collectors/test_league_fotmob_results.py tests/test_league_result_evidence.py
git commit -m "feat: parse verified FotMob league results"
```

---

### Task 2: Monotonic Result and Closing Stores

**Files:**
- Create: `worldcup/league_result_store.py`
- Modify: `worldcup/league_closing.py`
- Test: `tests/test_league_result_store.py`
- Test: `tests/test_league_closing.py`

**Interfaces:**
- Consumes: parser result rows and immutable league history snapshots.
- Produces: `LeagueResultStore(path).merge(payload: dict[str, Any]) -> dict[str, Any]` with `status`, `added`, `unchanged`, `conflicts`, and committed `fingerprint`.
- Produces: `merge_league_closings(existing: dict[str, Any] | None, snapshots: Iterable[dict[str, Any]], competition_id: str) -> dict[str, Any]`.

- [ ] **Step 1: Write failing monotonicity and partition tests**

```python
def test_same_score_is_idempotent_and_changed_score_is_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        store = LeagueResultStore(Path(tmp) / "epl_2026_27" / "results.json")
        assert store.merge(_results(2, 1))["added"] == 1
        assert store.merge(_results(2, 1))["unchanged"] == 1
        changed = store.merge(_results(3, 1))
        assert changed["status"] == "conflict"
        assert _read_score(store.path) == (2, 1)

def test_closing_merge_never_replaces_with_post_kickoff_snapshot():
    merged = merge_league_closings(_existing_closing(), [_post_kickoff_snapshot()], "epl_2026_27")
    assert merged == _existing_closing()
```

Cover deletion attempts, finished regression, identity change, cross-partition path, duplicate event IDs, atomic replace failure, and concurrent writers.

- [ ] **Step 2: Run focused tests and verify RED**

Run the two test modules with the project runtime; expected failure is missing store/merge interfaces.

- [ ] **Step 3: Implement minimal atomic monotonic stores**

Use `fcntl.flock`, same-directory `mkstemp`, file `fsync`, `os.replace`, and directory `fsync`. Conflict output must contain only event ID and safe reason, never raw provider content.

- [ ] **Step 4: Run focused tests and verify GREEN**

- [ ] **Step 5: Commit Task 2**

```bash
git add worldcup/league_result_store.py worldcup/league_closing.py tests/test_league_result_store.py tests/test_league_closing.py
git commit -m "feat: store league results monotonically"
```

---

### Task 3: Pure Due Planner and Cumulative Settlement

**Files:**
- Create: `worldcup/league_postmatch_planner.py`
- Modify: `worldcup/league_postmatch.py`
- Modify: `worldcup/league_statistics.py`
- Test: `tests/test_league_postmatch_planner.py`
- Test: `tests/test_league_postmatch.py`
- Test: `tests/test_league_statistics.py`

**Interfaces:**
- Consumes: acceptance payload, current fixture rows, accepted-result receipts, timezone-aware `now`.
- Produces: `plan_league_postmatch(acceptance: Mapping[str, Any], fixtures: Mapping[str, list[dict[str, Any]]], state: Mapping[str, Any], *, now: datetime) -> dict[str, Any]` with `due`, `blocked`, `competitions`, and `next_due_at`.
- Produces: `merge_league_postmatch(existing: dict[str, Any] | None, closing_payload: dict[str, Any], result_payload: dict[str, Any], competition_id: str) -> dict[str, Any]`.
- Produces: `crossed_evaluation_thresholds(previous_decided: int, current_decided: int, sent: Collection[int], thresholds: Sequence[int] = (20, 50, 100)) -> list[int]`.

- [ ] **Step 1: Write failing planner and cumulative settlement tests**

```python
def test_planner_selects_only_active_started_unsettled_events():
    plan = plan_league_postmatch(_acceptance(active=("epl_2026_27",)), _fixtures(), _state(), now=_utc("2026-08-29T00:30:00Z"))
    assert [row["source_event_id"] for row in plan["due"]] == ["epl-1"]

def test_thresholds_report_every_crossed_unsent_boundary_once():
    assert crossed_evaluation_thresholds(19, 101, {50}) == [20, 100]
```

Cover inactive Bundesliga, future/postponed/cancelled fixtures, terminal receipts, missing strict IDs, naive times, cumulative postmatch idempotency, missing closing visibility, and exclusion of wrong statistics scope.

- [ ] **Step 2: Run focused tests and verify RED**

- [ ] **Step 3: Implement pure planner, merge, and thresholds**

The planner must never call providers or inspect `.env`. A kickoff in the past only makes an event eligible for a provider check; it never proves completion.

- [ ] **Step 4: Run focused tests and verify GREEN**

- [ ] **Step 5: Commit Task 3**

```bash
git add worldcup/league_postmatch_planner.py worldcup/league_postmatch.py worldcup/league_statistics.py tests/test_league_postmatch_planner.py tests/test_league_postmatch.py tests/test_league_statistics.py
git commit -m "feat: plan and aggregate league settlements"
```

---

### Task 4: Recoverable Notification Outbox

**Files:**
- Create: `worldcup/league_postmatch_notifications.py`
- Test: `tests/test_league_postmatch_notifications.py`

**Interfaces:**
- Consumes: committed per-league postmatch blocks, aggregate statistics, previous/new decided counts, durable threshold receipts.
- Produces: `build_daily_settlement_event(*, settlement_date: str, newly_settled: int, competitions: Mapping[str, Mapping[str, int]], aggregate_fingerprint: str) -> dict[str, Any] | None`, `build_threshold_events(*, previous_decided: int, current_decided: int, sent_thresholds: Collection[int], aggregate_fingerprint: str) -> list[dict[str, Any]]`, `render_postmatch_notification(event: Mapping[str, Any]) -> dict[str, str]`, and `LeaguePostmatchNotificationOutbox(path, notifier).deliver(event) -> dict[str, Any]`.

- [ ] **Step 1: Write failing digest, threshold, and recovery tests**

```python
def test_daily_digest_exists_only_for_new_settlements():
    kwargs = {
        "settlement_date": "2026-08-29",
        "competitions": {"epl_2026_27": {"hit": 2, "miss": 1, "push": 0, "no_pick": 0}},
        "aggregate_fingerprint": "a" * 64,
    }
    assert build_daily_settlement_event(newly_settled=0, **kwargs) is None
    event = build_daily_settlement_event(newly_settled=3, **kwargs)
    rendered = render_postmatch_notification(event)
    assert "hit/miss/push/no_pick" not in rendered["content"]  # use Chinese labels
    assert "不构成投注建议" in rendered["content"]

def test_failed_delivery_remains_pending_without_duplicate_sent_receipt():
    with tempfile.TemporaryDirectory() as tmp:
        outbox = LeaguePostmatchNotificationOutbox(Path(tmp) / "outbox.json", notifier=_fails_once())
        assert outbox.deliver(_event())["status"] == "failed"
        assert outbox.retry_pending()["sent"] == 1
        assert outbox.deliver(_event())["status"] == "already_sent"
```

Assert no stake/EV/Edge fields, safe league-level counts, one-time 20/50/100 receipts, atomic state, malformed-state fail closed, and notifier output redaction.

- [ ] **Step 2: Run notification tests and verify RED**

- [ ] **Step 3: Implement event fingerprinting, renderer, and outbox**

Persist canonical event payload before calling WxPusher. Only `sent`/`already_sent` moves the fingerprint to sent receipts. Daily events bind exact committed aggregate fingerprint; threshold events bind threshold plus aggregate fingerprint.

- [ ] **Step 4: Run notification tests and verify GREEN**

- [ ] **Step 5: Commit Task 4**

```bash
git add worldcup/league_postmatch_notifications.py tests/test_league_postmatch_notifications.py
git commit -m "feat: notify league settlement milestones"
```

---

### Task 5: Dry-Run-First Live Runner

**Files:**
- Create: `worldcup/league_postmatch_runner.py`
- Test: `tests/test_league_postmatch_runner.py`

**Interfaces:**
- Consumes Tasks 1-4 plus injected `calendar_fetcher`, `detail_fetcher`, `clock`, stores, and outbox.
- Type aliases: `CalendarFetcher = Callable[[str, str], Mapping[str, Any]]`, `DetailFetcher = Callable[[str, str], Mapping[str, Any]]`, and `Notifier = Callable[[str, str], Mapping[str, Any]]`.
- Produces: `run_league_postmatch(root: Path, *, live: bool = False, write: bool = False, notify: bool = False, now: datetime | None = None, calendar_fetcher: CalendarFetcher | None = None, detail_fetcher: DetailFetcher | None = None, notifier: Notifier | None = None) -> dict[str, Any]` and CLI `python3 -m worldcup.league_postmatch_runner`.
- Live CLI rejects `--now`; dry-run permits it for replayable tests.

- [ ] **Step 1: Write failing orchestration and safety tests**

```python
def test_default_dry_run_has_zero_external_or_write_side_effects():
    with tempfile.TemporaryDirectory() as tmp:
        result = run_league_postmatch(Path(tmp), calendar_fetcher=_forbidden, detail_fetcher=_forbidden)
        assert result["mode"] == "dry_run"
        assert result["safety"] == {"read_env": False, "called_fotmob": False, "wrote": False, "notified": False}

def test_live_commits_results_before_settlement_and_notification():
    with tempfile.TemporaryDirectory() as tmp:
        recorded_order = []
        result = run_league_postmatch(
            Path(tmp),
            live=True,
            write=True,
            notify=True,
            calendar_fetcher=_calendar_fetcher(recorded_order),
            detail_fetcher=_detail_fetcher(recorded_order),
            notifier=_notifier(recorded_order),
        )
        assert result["status"] == "settled"
        assert recorded_order == ["calendar", "details", "results", "closing", "postmatch", "statistics", "state", "notification"]
```

Cover exact live/write flag matrix, non-blocking lock before provider access, acceptance reread under lock, per-league isolation, provider failure, response crossing state changes, conflict blocking, crash after each atomic stage, pending-notification-first recovery, and safe JSON projections.

- [ ] **Step 2: Run runner tests and verify RED**

- [ ] **Step 3: Implement minimal orchestration and CLI**

Use `data/local/leagues/league_postmatch.lock`. Retry outbox pending first. Re-read acceptance/state after locking. Fetch only due active partitions. Commit monotonic result evidence before derivative artifacts. Do not load `.env`; FotMob public access needs no secret. Wire the global WxPusher command only when `notify=True`.

- [ ] **Step 4: Run runner tests and verify GREEN**

- [ ] **Step 5: Run integration-focused regression**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

- [ ] **Step 6: Commit Task 5**

```bash
git add worldcup/league_postmatch_runner.py tests/test_league_postmatch_runner.py
git commit -m "feat: orchestrate six-league postmatch loop"
```

---

### Task 6: LaunchAgent Generator

**Files:**
- Create: `worldcup/league_postmatch_launch_agent.py`
- Test: `tests/test_league_postmatch_launch_agent.py`

**Interfaces:**
- Consumes: Python path, workdir, output path, and `full_live` boolean.
- Produces: `build_league_postmatch_launch_agent(*, python: str, workdir: str, full_live: bool = False) -> dict[str, Any]` and a CLI that prints JSON by default or atomically writes only the requested plist.

- [ ] **Step 1: Write failing deterministic plist tests**

```python
def test_full_live_plist_has_exact_schedule_and_is_not_run_at_load():
    plist = build_league_postmatch_launch_agent(python="/runtime/python3", workdir="/repo", full_live=True)
    assert plist["Label"] == "xin.celab.football.league-postmatch"
    assert plist["StartCalendarInterval"] == [{"Hour": 10, "Minute": 30}, {"Hour": 16, "Minute": 30}]
    assert plist["RunAtLoad"] is False
    assert plist["ProgramArguments"][-3:] == ["--live", "--write", "--notify"]
```

Also assert exact log paths, observation mode has no live flags, output writing is atomic, the generator never calls `launchctl`, and no endpoint/secret is embedded.

- [ ] **Step 2: Run generator tests and verify RED**

- [ ] **Step 3: Implement the generator and CLI**

- [ ] **Step 4: Run generator tests and verify GREEN**

- [ ] **Step 5: Commit Task 6**

```bash
git add worldcup/league_postmatch_launch_agent.py tests/test_league_postmatch_launch_agent.py
git commit -m "feat: generate league postmatch timer"
```

---

### Task 7: Documentation, Full Verification, and Review

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`
- Test: all new and existing tests.

**Interfaces:**
- Consumes: final CLI flags, paths, schedule, safety output, and operational gates from Tasks 1-6.
- Produces: operator commands and durable project constraints matching implementation exactly.

- [ ] **Step 1: Update project documentation**

Document dry-run and live commands, exact ignored paths, 10:30/16:30 schedule, independent label/logs, FotMob/no-quota boundary, result conflicts, missing closing semantics, notification behavior, and the four separately confirmed operational gates. Keep AGENTS.md and CLAUDE.md synchronized.

- [ ] **Step 2: Run focused tests**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all focused modules pass without network access.

- [ ] **Step 3: Run compile, whitespace, secret, and complete regression gates**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile \
  worldcup/collectors/league_fotmob_results.py \
  worldcup/league_result_store.py \
  worldcup/league_postmatch_planner.py \
  worldcup/league_postmatch_notifications.py \
  worldcup/league_postmatch_runner.py \
  worldcup/league_postmatch_launch_agent.py
git diff --check
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: compile and diff checks exit 0; complete suite has zero failures, with only explicitly optional modules skipped.

- [ ] **Step 4: Run a real-filesystem dry-run proof**

Run the CLI without live flags and record before/after hashes for quota, league snapshots, result stores, state, and notification files. Expected output: `mode=dry_run`, no FotMob call, no file changes, no WxPusher call, no The Odds API quota use.

- [ ] **Step 5: Request independent code review**

Use `superpowers:requesting-code-review` across the implementation base/head range. Fix every Critical and Important finding with TDD and rerun the full gates.

- [ ] **Step 6: Commit Task 7**

```bash
git add README.md AGENTS.md CLAUDE.md RECENT_WORK.md
git commit -m "docs: document league postmatch operations"
```

---

## Post-Implementation Operational Gates

These gates are not authorized by implementation approval and must remain separate:

### Gate A: Real FotMob Probe

- Report exact competitions/events, request count, saved ignored samples, schema fingerprints, and zero The Odds API quota use before asking for confirmation.
- Execute only after explicit confirmation.
- If any league cannot prove terminal 90-minute semantics, keep it blocked; do not weaken the parser.

### Gate B: First Live/Write and Notification Dry-Run

- Preflight exact active set, due events, existing closing coverage, state paths, and rollback behavior.
- Execute one confirmed live/write capture; keep actual notification delivery disabled.
- Repeat immediately to prove idempotency and zero duplicate settlement.

### Gate C: LaunchAgent Installation

- Present the exact plist, label, schedule, paths, flags, and rollback command.
- Install/bootstrap only after explicit confirmation; `RunAtLoad=false`, no kickstart during installation.

### Gate D: First Real Notification

- Use an actual newly settled event or a separately approved safe test event; never fabricate formal statistics.
- Send through the global WxPusher tool only after explicit confirmation and report only the redacted send status.

Push, PR, merge, and deployment each remain separate confirmations after implementation and verification.

---

## Adversarial Plan Review

- **Root-cause coverage:** The plan closes the currently missing production path from result capture through observed closing settlement and notification; it does not mistake existing pure settlement helpers for an operational loop.
- **Quota risk:** FotMob is the only automatic result provider; The Odds API scores are explicitly excluded, so postmatch work cannot silently consume odds-refresh quota.
- **Semantic risk:** Terminal status and 90-minute evidence are acceptance requirements, not best-effort checks. Unknown schema blocks formal settlement.
- **Closing risk:** Task 2 preserves immutable history and Task 3 surfaces missing closing. No task reconstructs or deletes missing cases.
- **Monotonicity risk:** Accepted scores are append-only by event identity. Provider revisions become conflicts requiring review.
- **Crash/retry risk:** Commit order is result evidence, closing, settlement/statistics, state, then notification intent. Tests inject crashes between stages and verify idempotent recovery.
- **Cross-league contamination:** Every store is partitioned; aggregate statistics require formal scope. Bundesliga remains excluded until acceptance is active.
- **Small-sample risk:** 20/50/100 are workflow gates, not proof of model improvement. No task modifies `match_pick_v3`.
- **Scope control:** The plan does not add public API/UI, model tuning, database migration, paid dependencies, provider fallback, deployment, or scheduler installation.
- **Remaining external risk:** FotMob may not expose a stable field that proves league-match 90-minute final scores. Gate A must block rather than infer if real samples do not prove the contract.

Review conclusion: no blocking plan contradiction remains. The external FotMob contract and existing closing-history coverage are deliberately deferred to explicit evidence gates, with fail-closed behavior and no automatic paid fallback.
