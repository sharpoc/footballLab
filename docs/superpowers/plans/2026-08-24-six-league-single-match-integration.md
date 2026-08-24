# Six-League Single-Match Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Serie A, Brasileirão, La Liga, Premier League, Bundesliga, and Ligue 1 to the formal single-match pipeline with odds inputs, MatchPick v3 prediction, immutable pre-kickoff closing, strict 90-minute settlement, and isolated statistics.

**Architecture:** Keep the CSL runner intact and add a pure `league_v1` pipeline around existing odds parsing, MatchPick, settlement, store, and public projection. Partition mutable artifacts by `competition_id`; expose fixed league entries and safe states through existing APIs and `/preview`. Live network/state work is deferred for separate approval.

**Tech Stack:** Python 3 standard library, dataclasses, JSON, SQLite-compatible snapshot contracts, injected transports, existing HTML renderer and custom test runner.

**Spec:** `docs/superpowers/specs/2026-08-24-six-league-single-match-integration-design.md`

## Global Constraints

- Targets are exactly `serie_a_2026_27`, `serie_a_brazil_2026`, `laliga_2026_27`, `epl_2026_27`, `bundesliga_2026_27`, and `ligue_1_2026_27`.
- Decisions are schema v2 and only `MATCH_PICK` or `NO_CLEAN_MARKET`.
- `club_rating_pending` uses de-vigged market consensus; placeholder 1500 cannot affect direction.
- Snapshot, history, closing, results, and statistics are isolated by `competition_id`.
- Statistics include only observed schema v2 rows; exclude World Cup, CSL, legacy, reconstructed, and daily-sidecar rows.
- Settle only verified football 90-minute non-negative integer scores.
- Default execution does not read `.env`, call a network, mutate quota/state/DB, publish, or deploy.
- Do not change CSL dual-source results, pending gate, coverage, shadow, or sentinel.
- Do not publish grades, EV, Edge, money, stake, or execution advice.
- Preserve unrelated dirty-worktree changes, especially existing five-key rotation edits.

---

### Task 1: Declare the six formal but disabled league profiles

**Files:**
- Modify: `worldcup/competitions.py`
- Modify: `tests/test_competitions.py`

**Interfaces:**
- Consumes: `CompetitionConfig`, `get_competition()`.
- Produces: five capability fields and `formal_single_match_competitions() -> tuple[CompetitionConfig, ...]`.

- [ ] **Step 1: Write the failing parameterized test**

```python
TARGETS = {
    "serie_a_2026_27": "soccer_italy_serie_a",
    "serie_a_brazil_2026": "soccer_brazil_campeonato",
    "laliga_2026_27": "soccer_spain_la_liga",
    "epl_2026_27": "soccer_epl",
    "bundesliga_2026_27": "soccer_germany_bundesliga",
    "ligue_1_2026_27": "soccer_france_ligue_one",
}

def test_six_leagues_declare_formal_disabled_capability():
    assert {x.id for x in formal_single_match_competitions()} == set(TARGETS)
    for competition_id, sport_key in TARGETS.items():
        cfg = get_competition(competition_id)
        assert cfg.theoddsapi_sport_key == sport_key
        assert cfg.pipeline_family == "league_v1"
        assert cfg.prediction_policy == "market_consensus_until_club_rating_verified"
        assert cfg.result_policy == "verified_football_90min"
        assert cfg.statistics_scope == "observed_schema_v2_match_pick_only"
        assert cfg.runtime_status == "disabled_until_live_acceptance"
```

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_competitions
```

Expected: FAIL because fields/helper are absent.

- [ ] **Step 3: Add immutable fields and exact whitelist**

```python
@dataclass(frozen=True)
class CompetitionConfig:
    # keep existing fields
    pipeline_family: str = "legacy"
    prediction_policy: str = "existing"
    result_policy: str = "football_90min"
    statistics_scope: str = "existing"
    runtime_status: str = "enabled"

FORMAL_SINGLE_MATCH_IDS = (
    "serie_a_2026_27", "serie_a_brazil_2026", "laliga_2026_27",
    "epl_2026_27", "bundesliga_2026_27", "ligue_1_2026_27",
)

def formal_single_match_competitions() -> tuple[CompetitionConfig, ...]:
    return tuple(_REGISTRY[item] for item in FORMAL_SINGLE_MATCH_IDS)
```

Set fields only on the six entries. Keep `snapshot_block()` public whitelist unchanged.

- [ ] **Step 4: Run GREEN and compatibility tests**

Run the Step 2 command. Expected: PASS; existing World Cup/CSL snapshot block assertions remain unchanged.

- [ ] **Step 5: Commit after commit authorization**

```bash
git add worldcup/competitions.py tests/test_competitions.py
git commit -m "feat: declare formal six-league profiles"
```

---

### Task 2: Build a pure market-consensus league snapshot pipeline

**Files:**
- Create: `worldcup/league_competition_pipeline.py`
- Create: `tests/test_league_competition_pipeline.py`
- Modify only if required: `worldcup/match_decision.py`
- Read-only reference: `worldcup/league_runner.py`

**Interfaces:**
- Consumes: `parse_league_odds_events()`, `prepare_match_input_for_pick()`, `analyze_match_input()`, `decide_match()`.
- Produces: `build_league_competition_snapshot(raw_odds, competition_id, observed_at, cfg=None) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing six-profile snapshot test**

```python
def test_all_six_profiles_build_market_fallback_snapshots():
    for profile in formal_single_match_competitions():
        snapshot = build_league_competition_snapshot(
            [_odds_event(profile.theoddsapi_sport_key)],
            profile.id,
            "2026-08-24T12:00:00+00:00",
            cfg=_cfg(),
        )
        assert snapshot["competition"]["id"] == profile.id
        assert len(snapshot["matches"]) == 1
        decision = snapshot["matches"][0]["match_decision"]
        assert decision["schema_version"] == 2
        assert decision["label"] == "MATCH_PICK"
        assert "market_consensus_fallback" in snapshot["data_quality"]["warnings"]
```

The fixture contains one future event, three complete h2h outcomes, and a fresh bookmaker timestamp.

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_competition_pipeline
```

Expected: import/module failure.

- [ ] **Step 3: Implement strict profile validation and pending-rating boundary**

```python
def _formal_profile(competition_id: str) -> CompetitionConfig:
    profile = get_competition(competition_id)
    if profile.pipeline_family != "league_v1":
        raise ValueError(f"unsupported_league_pipeline: {competition_id}")
    return profile

def _pending_rating(code: str | None) -> EloRating:
    return EloRating(code=code or "club_rating_pending", rank=0, rating=1500)
```

Use existing parsing/analysis functions, but send `("club_rating_pending", "market_consensus_fallback")` as blockers so selection uses the existing market-only fallback. Add only the smallest tested MatchPick mapping if required.

- [ ] **Step 4: Prove placeholder Elo cannot change direction**

```python
def test_placeholder_elo_and_home_adv_cannot_change_pending_pick_direction():
    first = build_league_competition_snapshot(RAW, "epl_2026_27", OBSERVED, cfg=_cfg(home_adv=0))
    second = build_league_competition_snapshot(RAW, "epl_2026_27", OBSERVED, cfg=_cfg(home_adv=500))
    assert first["matches"][0]["match_decision"]["selection"] == second["matches"][0]["match_decision"]["selection"]
```

The implementation must select from market-only probabilities, not merely add a warning.

- [ ] **Step 5: Add fail-closed tests**

Cover expired/invalid odds, unknown competition, unmatched club identity, multiple markets, and no market. Assert every match has exactly one decision and labels are a subset of `{"MATCH_PICK", "NO_CLEAN_MARKET"}`.

- [ ] **Step 6: Run focused regression**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_competition_pipeline tests.test_match_decision tests.test_league_runner
```

Expected: PASS without CSL decision changes.

- [ ] **Step 7: Commit after authorization**

```bash
git add worldcup/league_competition_pipeline.py tests/test_league_competition_pipeline.py
git commit -m "feat: build market-fallback league snapshots"
```

Add `worldcup/match_decision.py` only if it changed.

---

### Task 3: Add fixed single-match league entries and safe states

**Files:**
- Modify: `worldcup/query.py`
- Modify: `worldcup/ledger_html.py`
- Modify: `tests/test_query.py`
- Modify: `tests/test_preview.py`

**Interfaces:**
- Consumes: formal profiles, merged snapshots, `project_match_rows()`, `project_finished_rows()`.
- Produces: `project_single_match_competitions(snapshot) -> list[dict[str, str]]`.

- [ ] **Step 1: Write failing projection tests**

```python
def test_fixed_six_are_projected_without_fake_match_rows():
    rows = project_single_match_competitions({"matches": [], "finished": {"matches": []}})
    by_id = {row["competition_id"]: row for row in rows}
    assert set(TARGET_IDS) <= set(by_id)
    assert all(by_id[item]["status"] == "disabled_until_live_acceptance" for item in TARGET_IDS)
    assert project_match_rows({"matches": []}) == []
```

Add inputs proving valid snapshot → `active`, stale source → `stale`, built with no legal odds → `no_valid_odds`.

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_query
```

- [ ] **Step 3: Implement whitelist-only statuses**

```python
PUBLIC_STATUS_LABELS = {
    "active": "有正式单场数据",
    "no_upcoming_fixtures": "暂无未来赛程",
    "no_valid_odds": "暂无合法赔率",
    "stale": "数据过期",
    "result_pending": "赛果待确认",
    "disabled_until_live_acceptance": "尚未启用正式刷新",
}
```

Derive only from public-safe snapshot fields; never expose raw errors, quota, blockers, or provider payload.

- [ ] **Step 4: Write failing preview tests**

```python
def test_preview_lists_fixed_six_when_no_rows_exist():
    html = build_preview_html(_empty_snapshot())
    for competition_id in TARGET_IDS:
        assert f'value="{competition_id}"' in html
    assert "尚未启用正式刷新" in html
    assert "Home FC" not in html
```

Also test HTML escaping, accessible label, selected-league history isolation, and absence of grades/EV/Edge/money.

- [ ] **Step 5: Render stable options without synthesizing rows**

Replace row-only discovery for decision mode with the public status projection. Stable order: all, World Cup, CSL, Serie A, Brasileirão, La Liga, EPL, Bundesliga, Ligue 1. World Cup/CSL appear when present; the fixed six always appear.

- [ ] **Step 6: Run page/API regression**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_query tests.test_preview tests.test_http_app tests.test_fastapi_app
```

Expected: PASS; optional FastAPI may skip if unavailable.

- [ ] **Step 7: Commit after authorization**

```bash
git add worldcup/query.py worldcup/ledger_html.py tests/test_query.py tests/test_preview.py
git commit -m "feat: expose six leagues in single-match analysis"
```

---

### Task 4: Freeze immutable per-competition closings

**Files:**
- Create: `worldcup/league_closing.py`
- Create: `tests/test_league_closing.py`

**Interfaces:**
- Produces: `select_league_closings(snapshots, competition_id) -> dict`; `LeagueClosingStore.commit(payload) -> str`.

- [ ] **Step 1: Write the strict pre-kickoff test**

```python
def test_last_snapshot_strictly_before_kickoff_becomes_closing():
    result = select_league_closings([
        _snapshot("2026-08-24T10:00:00Z", odds=1.90),
        _snapshot("2026-08-24T17:59:59Z", odds=1.80),
        _snapshot("2026-08-24T18:00:00Z", odds=1.70),
    ], "epl_2026_27")
    closing = result["closings"]["epl-event-1"]
    assert closing["closing_snapshot_at"] == "2026-08-24T17:59:59+00:00"
    assert closing["closing_match_decision"]["odds"] == 1.80
```

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_closing
```

- [ ] **Step 3: Implement identity and decision validators**

```python
@dataclass(frozen=True)
class ClosingIdentity:
    competition_id: str
    provider_event_id: str
    kickoff_at_utc: str
    home_canonical: str
    away_canonical: str

def _valid_decision(value: Any) -> bool:
    return isinstance(value, dict) and value.get("schema_version") == 2 and value.get("label") in {
        "MATCH_PICK", "NO_CLEAN_MARKET",
    }
```

Normalize aware UTC and reject `snapshot_at >= kickoff`, mixed competitions, missing IDs/teams, and identity conflicts.

- [ ] **Step 4: Add adversarial selection tests**

Cover event ID reused by different teams, conflicting kickoff, reschedule retaining old closing as audit-only, post-kickoff rows ignored, and missing closing reported without fabrication.

- [ ] **Step 5: Implement atomic idempotent store**

Use sibling lock, validated competition partition, sibling temp file, flush, `fsync`, and `os.replace`. Test first `stored`, identical `unchanged`, and failed replace preserves old bytes.

- [ ] **Step 6: Run GREEN**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_closing
git diff --check
```

- [ ] **Step 7: Commit after authorization**

```bash
git add worldcup/league_closing.py tests/test_league_closing.py
git commit -m "feat: freeze per-league closing decisions"
```

---

### Task 5: Validate 90-minute results and settle league records

**Files:**
- Create: `worldcup/league_results.py`
- Create: `worldcup/league_postmatch.py`
- Create: `tests/test_league_results.py`
- Create: `tests/test_league_postmatch.py`
- Modify only if required: `worldcup/sources/theoddsapi_scores.py`

**Interfaces:**
- Produces: `parse_verified_league_results(raw, competition_id, score_semantics_verified=False) -> dict`; `build_league_postmatch(closing_payload, result_payload, competition_id) -> dict`.

- [ ] **Step 1: Write the fail-closed score test**

```python
def test_unverified_score_semantics_cannot_create_formal_result():
    parsed = parse_verified_league_results(
        [_completed_score_event()], "epl_2026_27", score_semantics_verified=False,
    )
    assert parsed["results"] == []
    assert parsed["pending"][0]["reason"] == "result_90min_semantics_unverified"
```

Add `completed=false`, negative/decimal score, wrong sport key, duplicate event ID, incomplete teams, malformed kickoff.

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_results
```

- [ ] **Step 3: Implement the safe result type**

```python
@dataclass(frozen=True)
class VerifiedLeagueResult:
    competition_id: str
    provider_event_id: str
    kickoff_at_utc: str
    home_canonical: str
    away_canonical: str
    home_score: int
    away_score: int
    captured_at: str
    result_scope: str = "football_90min"
```

Do not infer verification from `completed=true`; require explicit evidence.

- [ ] **Step 4: Generalize scores URL only if needed**

```python
def build_scores_url(api_key: str, sport_key: str, days_from: int = DEFAULT_DAYS_FROM) -> str:
    if not sport_key.startswith("soccer_"):
        raise ValueError("invalid_sport_key")
    params = {"daysFrom": days_from, "apiKey": api_key}
    return f"{BASE_URL}/sports/{sport_key}/scores/?{urlencode(params)}"
```

Keep `build_worldcup_scores_url()` compatible. Test injected transport, redaction, and cache/quota update only after valid JSON. No real request.

- [ ] **Step 5: Write failing postmatch tests**

```python
def test_only_matching_observed_schema_v2_closing_is_settled():
    block = build_league_postmatch(_closing_payload(), _result_payload(2, 0), "epl_2026_27")
    assert block["decision_tally"] == {"hit": 1, "miss": 0, "push": 0, "no_pick": 0}
    assert block["matches"][0]["closing_match_decision_result"]["status"] == "hit"
```

Cover missing closing, competition/identity mismatch, unresolved result, legacy excluded, and one failed match not blocking another.

- [ ] **Step 6: Implement pure join and canonical summary**

Build records then call `settle_match_decision()` and `summarize_decision_records(records, skipped_no_closing=N)`. Never hand-calculate a divergent tally.

- [ ] **Step 7: Run result/settlement regression**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_results tests.test_league_postmatch tests.test_settlement_unit tests.test_finished_record tests.collectors.test_theoddsapi_scores tests.sources.test_theoddsapi_scores_source
```

- [ ] **Step 8: Commit after authorization**

```bash
git add worldcup/league_results.py worldcup/league_postmatch.py tests/test_league_results.py tests/test_league_postmatch.py
git commit -m "feat: validate and settle league results"
```

Stage existing scores files only if changed.

---

### Task 6: Produce isolated statistics and six-league aggregate

**Files:**
- Create: `worldcup/league_statistics.py`
- Create: `tests/test_league_statistics.py`
- Modify: `worldcup/query.py`
- Modify: `worldcup/ledger_html.py`
- Modify: `tests/test_query.py`
- Modify: `tests/test_preview.py`

**Interfaces:**
- Produces: `build_league_statistics(blocks, min_sample=20) -> dict`.

- [ ] **Step 1: Write isolation test**

```python
def test_statistics_exclude_csl_and_legacy_from_six_league_aggregate():
    report = build_league_statistics([
        _block("epl_2026_27", hit=2, miss=1),
        _block("serie_a_2026_27", hit=1, miss=1),
        _block("csl_2026", hit=100, miss=0),
        _legacy_block("laliga_2026_27"),
    ])
    assert report["competitions"]["epl_2026_27"]["decision_tally"]["hit"] == 2
    assert report["aggregate"]["decision_tally"] == {
        "hit": 3, "miss": 2, "push": 0, "no_pick": 0,
    }
    assert "csl_2026" not in report["competitions"]
```

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_statistics
```

- [ ] **Step 3: Implement strict scope and recomputation**

```python
FORMAL_SCOPE = "observed_schema_v2_match_pick_only"

def _empty_tally() -> dict[str, int]:
    return {"hit": 0, "miss": 0, "push": 0, "no_pick": 0}
```

Whitelist competition/scope. Recompute `decided`, `actionable`, `decision_count`, `hit_rate`, `pick_rate`, and `sample_too_small`; never average league rates.

- [ ] **Step 4: Add denominator/coverage tests**

Cover push/no-pick, missing/invalid/unresolved/legacy coverage, reconstructed rejection, mismatched totals, zero samples, and 19/20 `min_sample` boundary.

- [ ] **Step 5: Project and render safe statistics**

Expose only competition ID, tally, sample, and coverage. Selected league shows its own record; aggregate label is `六联赛同口径汇总`; small samples show `小样本观察`.

- [ ] **Step 6: Run GREEN**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_statistics tests.test_query tests.test_preview
```

- [ ] **Step 7: Commit after authorization**

```bash
git add worldcup/league_statistics.py tests/test_league_statistics.py worldcup/query.py worldcup/ledger_html.py tests/test_query.py tests/test_preview.py
git commit -m "feat: report isolated six-league statistics"
```

---

### Task 7: Add offline batch orchestration and final verification

**Files:**
- Create: `worldcup/league_batch_runner.py`
- Create: `tests/test_league_batch_runner.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`

**Interfaces:**
- Consumes: Tasks 1-6 through injected callables.
- Produces: `run_league_batch(root, observed_at, live=False, write=False, env_loader=None, odds_fetcher=None, score_fetcher=None, snapshot_builder=build_league_competition_snapshot) -> dict`; JSON CLI.

- [ ] **Step 1: Write dry-run and isolation tests**

```python
def test_batch_dry_run_never_reads_env_calls_transport_or_writes(tmp_path):
    result = run_league_batch(
        root=tmp_path, observed_at=OBSERVED, live=False, write=False,
        env_loader=_fail, odds_fetcher=_fail, score_fetcher=_fail,
    )
    assert result["status"] == "dry_run"
    assert set(result["competitions"]) == set(TARGET_IDS)
    assert list(tmp_path.rglob("*")) == []

def test_one_failure_does_not_block_other_five(tmp_path):
    result = run_league_batch(
        root=tmp_path,
        observed_at=OBSERVED,
        live=False,
        write=False,
        snapshot_builder=_fails_only("epl_2026_27"),
    )
    assert result["competitions"]["epl_2026_27"]["status"] == "error"
    assert sum(x["status"] == "built" for x in result["competitions"].values()) == 5
    assert result["status"] == "partial"
```

- [ ] **Step 2: Run RED**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_league_batch_runner
```

- [ ] **Step 3: Implement injected, competition-by-competition runner**

```python
def run_league_batch(
    *, root: str | Path, observed_at: str, live: bool = False, write: bool = False,
    env_loader: Callable | None = None, odds_fetcher: Callable | None = None,
    score_fetcher: Callable | None = None,
    snapshot_builder: Callable = build_league_competition_snapshot,
) -> dict[str, Any]:
    if live or write:
        return {"status": "blocked", "reason": "live_acceptance_not_enabled"}
    competitions: dict[str, dict[str, Any]] = {}
    for profile in formal_single_match_competitions():
        try:
            snapshot = snapshot_builder([], profile.id, observed_at)
            competitions[profile.id] = {
                "status": "built" if snapshot.get("matches") else "empty",
                "match_count": len(snapshot.get("matches") or []),
            }
        except (OSError, TypeError, ValueError) as exc:
            competitions[profile.id] = {
                "status": "error",
                "reason": type(exc).__name__,
            }
    statuses = {row["status"] for row in competitions.values()}
    return {
        "status": "partial" if "error" in statuses and len(statuses) > 1 else "dry_run",
        "competitions": competitions,
    }
```

First implementation intentionally blocks live/write. Per-league statuses are `built`, `degraded`, `empty`, `blocked`, or `error`.

- [ ] **Step 4: Add secret-safe error and CLI tests**

Assert API keys, `apiKey` URLs, raw payloads, and headers never appear. CLI default returns six entries and writes nothing; `--live`/`--write` returns `blocked/live_acceptance_not_enabled`.

- [ ] **Step 5: Synchronize documentation**

Update README from “待实施” to exact offline status. Synchronize AGENTS/CLAUDE rules: live remains disabled, score semantics require evidence, and statistics scope exclusions. Append RECENT_WORK with files, tests, and explicit non-actions.

- [ ] **Step 6: Run focused and complete verification**

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest tests.test_competitions tests.test_league_competition_pipeline tests.test_league_closing tests.test_league_results tests.test_league_postmatch tests.test_league_statistics tests.test_league_batch_runner tests.test_query tests.test_preview
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/league_competition_pipeline.py worldcup/league_closing.py worldcup/league_results.py worldcup/league_postmatch.py worldcup/league_statistics.py worldcup/league_batch_runner.py
git diff --check
```

Expected: focused/full pass, only existing optional FastAPI skip if unavailable, compile and diff check exit 0.

- [ ] **Step 7: Run final adversarial checks**

Prove: no snapshot does not create a fake match; placeholder Elo cannot change direction; exact-kickoff snapshot cannot close; unknown score semantics cannot settle; missing closing does not block another match; CSL/legacy cannot inflate aggregate; dry-run leaves env/quota/cache/local/DB/network untouched.

- [ ] **Step 8: Request review**

Use `superpowers:requesting-code-review`. Fix only in-scope findings and rerun focused/full verification.

- [ ] **Step 9: Commit after separate authorization**

```bash
git add worldcup/league_batch_runner.py tests/test_league_batch_runner.py README.md AGENTS.md CLAUDE.md RECENT_WORK.md
git commit -m "feat: complete offline six-league single-match pipeline"
```

Do not include unrelated dirty files. Push/PR/merge/live capture/scheduler/deploy remain separately authorized.

---

## Deferred Live-Acceptance Boundary

A new approved spec/plan is required for:

1. Read-only `/scores` evidence capture for all six exact sport keys and saved `data/probe/` samples.
2. Proof that scores meet strict 90-minute semantics, including extra-time and revision counterexamples.
3. Real `/odds` quota-cost measurement across six keys.
4. Production anchors, low-quota degradation, key rotation, locks, pending publish, scheduler/LaunchAgent.
5. Formal state activation, DB ingest, ECS release, smoke, rollback, and monitoring.

Offline completion does not authorize or imply these actions.
