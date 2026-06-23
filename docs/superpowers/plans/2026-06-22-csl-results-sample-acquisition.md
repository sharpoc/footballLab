# CSL Results Sample Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a confirmed, local-only workflow for acquiring, normalizing, and validating 2023-2026 CSL result samples before any data can be considered for replay.

**Architecture:** Reuse the P9.3 `worldcup.csl_results_probe` path instead of adding new business code. The implementation creates ignored local source notes, manually saved sample CSV/JSON files, diagnostics, and a review packet; production runners, `club_rating_pending`, live refresh, secrets, and cloud state remain untouched.

**Tech Stack:** Markdown run notes, ignored local files under `data/probe/` and `data/local/diagnostics/`, Python 3.11 standard library, existing `worldcup.csl_results_probe`, existing `tests/run_tests.py`, no new dependency.

---

## Scope And Safety

This plan follows P9.3 and fills the current gap: the parser and probe exist, but no real CSL primary/check sample files are present locally.

Do not modify business code in this plan. Specifically, do not modify:

- `worldcup/collectors/csl_results.py`
- `worldcup/csl_results_probe.py`
- `worldcup/collectors/club_aliases.py`
- `worldcup/club_rating.py`
- `worldcup/league_runner.py`
- `worldcup/engine/`
- scheduler, publish, ingest, ECS, LaunchAgent, `.env`, or secret helpers

Do not call The Odds API, consume quota, read `.env`, print secrets, deploy, publish, write ECS, update LaunchAgent, install dependencies, or lift `club_rating_pending`.

Public web/source reads are a separate execution boundary. Before opening web pages, downloading samples, or saving public-source data, stop and obtain explicit user confirmation for that action. After confirmation, only save manually reviewed public samples to ignored local paths.

Research boundary: this work supports data-quality research only. It is not betting advice, does not output stake sizes, and does not recommend chasing, heavy positions, parlays, or execution actions.

Before each task, run:

```bash
git status --short
```

Expected: note any unrelated dirty files and do not revert them.

## File Structure

No business-code files are created or modified.

Future execution may create or modify these local-only files:

- Create: `data/local/diagnostics/csl_results_source_candidates.md` - source candidate notes, terms summary, field coverage, and selection decision.
- Create: `data/probe/csl_results_primary_sample.csv` - normalized primary-source CSL result sample in the P9.3 schema.
- Create: `data/probe/csl_results_check_sample.csv` - normalized check-source CSL result sample in the P9.3 schema.
- Create: `data/probe/csl_results_<source_id>_<season>_raw.*` - optional raw saved extracts for manual traceability.
- Create: `data/local/diagnostics/csl_results_source_probe.json` - output from `worldcup.csl_results_probe`.
- Create: `data/local/diagnostics/csl_results_replay_candidate.csv` - optional candidate written only when the local quality gate allows replay entry.
- Create: `data/local/diagnostics/csl_results_manual_review.md` - manual-review log for mismatches, missing rows, and source decisions.
- Modify: `RECENT_WORK.md` - short completion note after the local acquisition/validation stage is executed.

Tracked documentation changes for this plan are limited to:

- Create: `docs/superpowers/plans/2026-06-22-csl-results-sample-acquisition.md`

## Source Selection Rules

Use two independent source roles:

- `primary`: the structured source used to build normalized rows.
- `check`: the official or authority-like source used to cross-check date, home team, away team, status, and score.

Accept a candidate source only if all of these are true:

- Publicly accessible without login, API key, Cookie, paid account, scraping credentials, or private browser state.
- Terms or license do not obviously forbid local research use.
- Covers CSL results for at least one of `2023`, `2024`, `2025`, `2026`.
- Provides enough fields to map to `season,date,home_team,away_team,home_score,away_score,status`.
- Lets a human trace a row back to a source page, match id, or source URL.

Prefer source pairs that are operationally independent. A mirror that copies the primary source is useful for availability, but it is weak evidence for data correctness.

## Sample CSV Contract

Both normalized sample files must use this exact UTF-8 CSV header:

```text
season,round,date,kickoff_time_local,home_team,away_team,home_score,away_score,neutral,status,source_match_id,source_url
```

Rules:

- `season`: one of `2023`, `2024`, `2025`, `2026`.
- `round`: source-provided round if available; otherwise blank.
- `date`: strict `YYYY-MM-DD`.
- `kickoff_time_local`: local kickoff time if available; otherwise blank.
- `home_team` and `away_team`: source names that must already be accepted by `match_known_club_alias()` for `csl_2026`.
- `home_score` and `away_score`: non-negative integers for finished matches.
- `neutral`: `0` for normal home/away CSL matches unless the source explicitly marks a neutral venue.
- `status`: use `finished` for completed matches; do not include scheduled, postponed, canceled, abandoned, or unplayed matches as finished rows.
- `source_match_id`: source id if available; otherwise a stable local id such as `source_slug_2024_round_01_match_001`.
- `source_url`: public source URL for traceability.

Do not synthesize unknown teams, scores, dates, or statuses. If a row cannot be traced, omit it from the normalized sample and record it in `data/local/diagnostics/csl_results_manual_review.md`.

## Task 1: Prepare Local Intake Files

**Files:**
- Create: `data/local/diagnostics/csl_results_source_candidates.md`
- Create: `data/local/diagnostics/csl_results_manual_review.md`
- Create: `data/probe/csl_results_primary_sample.csv`
- Create: `data/probe/csl_results_check_sample.csv`

- [ ] **Step 1: Confirm the worktree state**

Run:

```bash
git status --short
```

Expected: either no output or unrelated dirty files that are not touched by this plan.

- [ ] **Step 2: Create local directories**

Run:

```bash
mkdir -p data/probe data/local/diagnostics
```

Expected: command exits 0.

- [ ] **Step 3: Create the candidate-notes file**

Create `data/local/diagnostics/csl_results_source_candidates.md` with this content:

```markdown
# CSL Results Source Candidates

Date: 2026-06-22
Competition: csl_2026
Scope: 2023-2026 finished CSL matches

## Confirmation Boundary

- Public web/source reads require explicit user confirmation before execution.
- No login, API key, Cookie, paid account, `.env`, The Odds API, or private browser state is allowed.
- Saved samples stay under ignored local paths.

## Candidate Register

| source_id | role_candidate | access | coverage | required_fields | traceability | independence_notes | decision |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Selected Pair

primary_source_id:
check_source_id:
selection_reason:
known_limitations:
```

Expected: file exists under `data/local/diagnostics/`, which is ignored by git.

- [ ] **Step 4: Create the manual-review file**

Create `data/local/diagnostics/csl_results_manual_review.md` with this content:

```markdown
# CSL Results Manual Review

Date: 2026-06-22
Competition: csl_2026

## Review Rules

- Unknown aliases do not enter replay.
- Score conflicts do not enter replay until resolved from source evidence.
- Home/away conflicts do not enter replay until resolved from source evidence.
- Missing-in-check rows remain degraded candidates and block `can_enter_replay`.
- `pending_gate.can_lift_club_rating_pending` remains false in P9.4.

## Decisions

| reason | season | date | home_team | away_team | primary_value | check_value | decision | evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
```

Expected: file exists under `data/local/diagnostics/`, which is ignored by git.

- [ ] **Step 5: Create empty normalized sample files with exact headers**

Create `data/probe/csl_results_primary_sample.csv` with:

```csv
season,round,date,kickoff_time_local,home_team,away_team,home_score,away_score,neutral,status,source_match_id,source_url
```

Create `data/probe/csl_results_check_sample.csv` with:

```csv
season,round,date,kickoff_time_local,home_team,away_team,home_score,away_score,neutral,status,source_match_id,source_url
```

Expected: both files exist under `data/probe/`, which is ignored by git.

- [ ] **Step 6: Verify ignored local files do not enter git status**

Run:

```bash
git status --short
```

Expected: no tracked changes from `data/probe/` or `data/local/diagnostics/`. If these files appear, stop and inspect `.gitignore` before continuing.

## Task 2: Confirm And Review Public Source Candidates

**Files:**
- Modify: `data/local/diagnostics/csl_results_source_candidates.md`

- [ ] **Step 1: Ask for explicit confirmation before public web/source reads**

Send this confirmation request:

```text
P9.4 下一步需要只读公开网页/公开文件来寻找 CSL 2023-2026 赛果样例来源。确认后我只做公开源只读调研，并把候选来源摘要写到 data/local/diagnostics/csl_results_source_candidates.md；不登录、不用 Cookie、不用 API key、不读 .env、不调用 The Odds API、不写线上、不部署。确认继续吗？
```

Expected: continue only after the user explicitly confirms.

- [ ] **Step 2: Search candidate sources after confirmation**

Use a normal browser or web search with these queries:

```text
Chinese Super League 2023 results CSV
Chinese Super League 2024 results fixtures
Chinese Super League 2025 results fixtures
Chinese Super League 2026 results official
中超 2023 赛果 比分
中超 2024 赛程 比分
中超 2025 赛果 比分
中超 2026 赛程 比分 官方
```

Expected: collect candidate source pages or downloadable files that can be reviewed manually. Do not download bulk files until the source terms and fields are checked.

- [ ] **Step 3: Record each candidate**

For every reviewed candidate, add one row to the `Candidate Register` table in `data/local/diagnostics/csl_results_source_candidates.md`.

Use these values:

```text
source_id: lower-case source name, hyphenated, without secrets or query tokens
role_candidate: primary, check, or reject
access: public-no-login, public-download, blocked-login, blocked-paid, blocked-terms, or blocked-unstable
coverage: exact visible season coverage such as 2023-2026, 2024 only, or unknown
required_fields: yes if date/home/away/score/status can be obtained, otherwise no
traceability: match-url, match-id, page-url, or weak
independence_notes: independent, likely-mirror, same-provider, unknown
decision: primary-candidate, check-candidate, reject, or needs-review
```

Expected: at least one plausible `primary-candidate` and one plausible `check-candidate`, or a clear blocked status explaining why source acquisition cannot proceed.

- [ ] **Step 4: Select a source pair**

Fill the `Selected Pair` section:

```markdown
primary_source_id: use the selected primary source_id
check_source_id: use the selected check source_id
selection_reason: short evidence-based reason
known_limitations: list visible coverage, terms, parsing, language, or independence concerns
```

Expected: selected primary/check pair is visible in the local notes. If no pair is selected, stop and report that P9.4 is blocked by source availability.

## Task 3: Normalize A Small Proof Sample

**Files:**
- Modify: `data/probe/csl_results_primary_sample.csv`
- Modify: `data/probe/csl_results_check_sample.csv`
- Modify: `data/local/diagnostics/csl_results_manual_review.md`

- [ ] **Step 1: Save raw traceability extracts**

For each selected source and season touched by the proof sample, save a small raw extract under `data/probe/` using this naming pattern:

```text
data/probe/csl_results_<source_id>_<season>_raw.csv
data/probe/csl_results_<source_id>_<season>_raw.json
data/probe/csl_results_<source_id>_<season>_raw.html
```

Expected: only use the suffix matching the actual source format. Keep these files ignored and local.

- [ ] **Step 2: Normalize a minimum proof sample**

Populate both normalized sample CSVs with the same finished matches from the selected pair. Use at least:

```text
2023: 2 finished matches
2024: 2 finished matches
2025: 2 finished matches
2026: 2 finished matches if already finished matches exist
```

If no 2026 finished CSL matches exist at execution time, record that fact in `data/local/diagnostics/csl_results_manual_review.md` and continue with 2023-2025 proof rows only.

Expected: each normalized row has a source URL or source match id. Do not include scheduled, postponed, canceled, or unplayed fixtures.

- [ ] **Step 3: Verify header and row counts**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import csv
from pathlib import Path

expected = [
    "season",
    "round",
    "date",
    "kickoff_time_local",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "neutral",
    "status",
    "source_match_id",
    "source_url",
]
for name in ["primary", "check"]:
    path = Path(f"data/probe/csl_results_{name}_sample.csv")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    print(name, "rows", len(rows), "header_ok", reader.fieldnames == expected)
    if reader.fieldnames != expected:
        raise SystemExit(f"{path} header mismatch: {reader.fieldnames}")
    if not rows:
        raise SystemExit(f"{path} has no proof rows")
PY
```

Expected: prints `header_ok True` for both files and non-zero row counts.

## Task 4: Run The Proof Probe

**Files:**
- Create or modify: `data/local/diagnostics/csl_results_source_probe.json`
- Modify: `data/local/diagnostics/csl_results_manual_review.md`

- [ ] **Step 1: Run local-only probe without replay candidate**

Run:

```bash
set +e
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_results_probe \
  --competition csl_2026 \
  --primary-source-id "$(awk -F': ' '/^primary_source_id:/ {print $2}' data/local/diagnostics/csl_results_source_candidates.md)" \
  --primary-sample data/probe/csl_results_primary_sample.csv \
  --check-source-id "$(awk -F': ' '/^check_source_id:/ {print $2}' data/local/diagnostics/csl_results_source_candidates.md)" \
  --check-sample data/probe/csl_results_check_sample.csv \
  --output data/local/diagnostics/csl_results_source_probe.json \
  --min-valid-matches 300
probe_status=$?
set -e
echo "probe_status=${probe_status}"
test -f data/local/diagnostics/csl_results_source_probe.json
```

Expected: `data/local/diagnostics/csl_results_source_probe.json` exists. `probe_status=0` means no manual review is required; `probe_status=1` means diagnostics contain manual-review items. For proof samples, `pending_gate.can_enter_replay` is usually false because `valid_finished_matches` is below 300.

- [ ] **Step 2: Summarize diagnostics**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("data/local/diagnostics/csl_results_source_probe.json").read_text(encoding="utf-8"))
print("competition_id", payload["competition_id"])
print("coverage", payload["coverage"])
print("primary_issues", len(payload["sources"]["primary"]["issues"]))
print("check_issues", len(payload["sources"]["check"]["issues"]))
print("manual_review_required", len(payload["quality"]["manual_review_required"]))
print("team_alias_unmatched", payload["quality"]["team_alias_unmatched"])
print("score_mismatches", len(payload["quality"]["score_mismatches"]))
print("missing_in_primary", len(payload["quality"]["missing_in_primary"]))
print("degraded_candidates", len(payload["quality"]["degraded_candidates"]))
print("can_enter_replay", payload["pending_gate"]["can_enter_replay"])
print("can_lift_club_rating_pending", payload["pending_gate"]["can_lift_club_rating_pending"])
print("pending_reasons", payload["pending_gate"]["reasons"])
PY
```

Expected: `can_lift_club_rating_pending False` is printed. Any alias, score, date, home/away, missing, duplicate, or parse issue must be copied into `data/local/diagnostics/csl_results_manual_review.md`.

- [ ] **Step 3: Apply review decisions only to local samples**

Use these decisions:

```text
team_alias_unmatched: stop and draft a separate alias-update plan; do not edit aliases inside P9.4.
score_mismatch: inspect both source URLs; if one normalized row was copied incorrectly, correct the local CSV and rerun the probe; if the sources truly disagree, keep the row out of replay.
date_mismatch: inspect source match pages; do not auto-shift dates for timezone or postponement assumptions.
home_away_mismatch: inspect source match pages; do not swap teams without source evidence.
duplicate_candidate: remove only duplicate extraction artifacts from local CSV; if the source itself has duplicate conflicting records, keep the issue in manual review.
missing_in_primary: do not enter replay.
missing_in_check: degraded only, blocks can_enter_replay.
status_not_finished: remove from normalized samples unless the source can prove the match is finished.
```

Expected: after local corrections, rerun Task 4 Step 1 and Step 2. If issues remain, report them as blockers rather than weakening the gate.

## Task 5: Expand To Full Local Sample

**Files:**
- Modify: `data/probe/csl_results_primary_sample.csv`
- Modify: `data/probe/csl_results_check_sample.csv`
- Modify: `data/local/diagnostics/csl_results_manual_review.md`
- Modify: `data/local/diagnostics/csl_results_source_probe.json`
- Create: `data/local/diagnostics/csl_results_replay_candidate.csv` only if the local gate allows it

- [ ] **Step 1: Expand normalized samples season by season**

Add all manually traceable finished CSL rows for:

```text
2023 finished matches
2024 finished matches
2025 finished matches
2026 finished matches available at execution time
```

Expected: 2026 in-progress coverage is treated as partial finished-match coverage, not as a complete-season guarantee. Do not create rows for future fixtures.

- [ ] **Step 2: Run full probe with optional replay candidate**

Run:

```bash
rm -f data/local/diagnostics/csl_results_replay_candidate.csv
set +e
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m worldcup.csl_results_probe \
  --competition csl_2026 \
  --primary-source-id "$(awk -F': ' '/^primary_source_id:/ {print $2}' data/local/diagnostics/csl_results_source_candidates.md)" \
  --primary-sample data/probe/csl_results_primary_sample.csv \
  --check-source-id "$(awk -F': ' '/^check_source_id:/ {print $2}' data/local/diagnostics/csl_results_source_candidates.md)" \
  --check-sample data/probe/csl_results_check_sample.csv \
  --output data/local/diagnostics/csl_results_source_probe.json \
  --write-replay-candidate data/local/diagnostics/csl_results_replay_candidate.csv \
  --min-valid-matches 300
probe_status=$?
set -e
echo "probe_status=${probe_status}"
test -f data/local/diagnostics/csl_results_source_probe.json
```

Expected: if diagnostics has no manual-review issues and quality gates pass, `probe_status=0` and `data/local/diagnostics/csl_results_replay_candidate.csv` exists. If gates fail, `probe_status` may be 0 or 1 depending on issue type, and no replay candidate should be written unless `pending_gate.can_enter_replay` is true.

- [ ] **Step 3: Verify candidate status from diagnostics**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("data/local/diagnostics/csl_results_source_probe.json").read_text(encoding="utf-8"))
candidate = Path("data/local/diagnostics/csl_results_replay_candidate.csv")
print("valid_finished_matches", payload["coverage"]["valid_finished_matches"])
print("manual_review_required", len(payload["quality"]["manual_review_required"]))
print("can_enter_replay", payload["pending_gate"]["can_enter_replay"])
print("can_lift_club_rating_pending", payload["pending_gate"]["can_lift_club_rating_pending"])
print("candidate_exists", candidate.exists())
if payload["pending_gate"]["can_enter_replay"] != candidate.exists():
    raise SystemExit("candidate existence does not match can_enter_replay")
if payload["pending_gate"]["can_lift_club_rating_pending"] is not False:
    raise SystemExit("club_rating_pending lift gate must remain false")
PY
```

Expected: `can_lift_club_rating_pending False`. Candidate existence must match `can_enter_replay`.

- [ ] **Step 4: Keep candidate out of production cache**

Run:

```bash
test ! -f data/cache/club_results_csl_2026.csv
```

Expected: command exits 0 if no production cache file has been installed. If `data/cache/club_results_csl_2026.csv` already exists from older local work, do not overwrite it in P9.4; report that separate confirmation is required before installing any replay candidate.

## Task 6: Verification And Reporting

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run focused CSL tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all tests pass with a final line like `N/N tests passed`.

- [ ] **Step 2: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 3: Confirm ignored sample files are not staged or tracked**

Run:

```bash
git status --short
```

Expected: no `data/probe/` or `data/local/diagnostics/` files appear. If tracked docs changed, they should be limited to `RECENT_WORK.md` and any explicitly approved tracked documentation.

- [ ] **Step 4: Update recent work**

Add a top entry to `RECENT_WORK.md`:

```markdown
## 2026-06-22 P9.4 中超赛果样例获取与验收

- 按 P9.4 计划完成 CSL 2023-2026 赛果样例来源筛选、本地归一化和 `worldcup.csl_results_probe` dry-run。
- 样例与诊断只写入 ignored 本地路径：`data/probe/`、`data/local/diagnostics/`；未改业务代码，未联网调用 The Odds API，未读取 `.env`，未部署，未更新 LaunchAgent。
- `pending_gate.can_lift_club_rating_pending` 仍为 `false`；如生成 `data/local/diagnostics/csl_results_replay_candidate.csv`，也未自动安装到 `data/cache/club_results_csl_2026.csv`。
- 验证：记录实际 `tests/run_tests.py` pass count、probe status、`valid_finished_matches`、manual review 数量和 candidate 是否生成。
```

Expected: the entry records actual numbers from the just-run diagnostics before final reporting.

- [ ] **Step 5: Ask before committing**

If tracked docs changed, ask the user before running a local commit:

```text
P9.4 本地样例验收已经完成，当前只有文档记录需要提交。是否创建本地 commit？不会 push。
```

Expected: commit only after explicit confirmation. Pushing remains a separate confirmation.

- [ ] **Step 6: Final status report**

Report:

```text
selected primary/check source ids
saved local sample paths
probe command used
probe status
valid_finished_matches
manual_review_required count
team_alias_unmatched list
score_mismatches count
missing_in_primary count
degraded_candidates count
pending_gate.can_enter_replay
pending_gate.can_lift_club_rating_pending
candidate path if generated
test command and pass count
confirmation that no business code, The Odds API, .env, ECS, LaunchAgent, publish, deploy, or push action was used
```

Expected: the report distinguishes data facts, observations, confirmed engineering status, and blockers.

## Adversarial Self-Review

- Root cause: this plan addresses the real blocker, which is missing trustworthy local CSL result samples. It does not pretend the parser alone proves the data chain is ready.
- Scope control: the plan avoids business-code changes, production runner wiring, model parameter changes, live odds refresh, online publish, deployment, LaunchAgent changes, and secret handling.
- Source independence risk: two sources may share the same upstream provider. The candidate notes must record independence assumptions and downgrade confidence when independence is weak.
- License and terms risk: public visibility is not the same as permission to bulk reuse. If terms are unclear or restrictive, reject the source or keep only minimal manually reviewed notes.
- 2026 partial-season risk: 2026 may be incomplete. The plan includes only finished matches and does not treat partial 2026 coverage as complete-season evidence.
- Alias risk: unknown teams or renamed clubs must stop the replay path and trigger a separate alias plan; they must not be silently slugified.
- Data-quality risk: score, date, home/away, duplicate, and missing-source conflicts block replay rather than being papered over.
- Production risk: even if `can_enter_replay=true`, P9.4 does not install the candidate into `data/cache/club_results_csl_2026.csv` and does not lift `club_rating_pending`.
- Verification risk: the probe diagnostics and full local test command are required before reporting success.
- Research boundary: no betting advice, stake sizing, chasing, heavy-position, parlay, or execution recommendation is produced.

## Plan Self-Review

- P9.3 continuity: the plan reuses the existing strict alias gate, parser, cross-source diagnostics, replay candidate writer, and `can_lift_club_rating_pending=false` contract.
- Sample acquisition coverage: tasks cover source confirmation, candidate review, local raw extracts, normalized primary/check CSVs, proof probe, full probe, manual review, optional replay candidate, and final reporting.
- State-change boundaries: public web reads, candidate installation, commits, push, deploy, live refresh, ECS writes, LaunchAgent changes, and secrets all require separate confirmation or are excluded.
- Type and field consistency: sample headers match `tests/test_csl_results_probe.py` and `worldcup.collectors.csl_results.parse_csl_result_rows()`.
- Placeholder scan target: the verification command should return no hits for the writing-plans red-flag phrases.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-22-csl-results-sample-acquisition.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
