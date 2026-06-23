# CSL Club Alias Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the strict `csl_2026` club alias gate so verified 2023-2026 CSL source names can enter local parsing without weakening unknown-team blocking.

**Architecture:** Keep aliasing scoped to `worldcup.collectors.club_aliases`; do not add source fetching, replay installation, league-runner wiring, or rating-policy changes. Add explicit aliases for verified CFL official and 7M names, then prove `match_known_club_alias()` still rejects unknown clubs while accepting the names that blocked P9.4 full-sample expansion.

**Tech Stack:** Python standard library, existing `worldcup.collectors.club_aliases`, existing `worldcup.collectors.csl_results`, local ignored P9.4 proof samples, `tests/run_tests.py`.

---

## Scope And Safety

This plan follows P9.4. It fixes only the alias blocker discovered while preparing CSL result samples.

Do not modify:

- `worldcup/collectors/csl_results.py`
- `worldcup/csl_results_probe.py`
- `worldcup/club_rating.py`
- `worldcup/league_runner.py`
- `worldcup/engine/`
- scheduler, publish, ingest, ECS, LaunchAgent, `.env`, secret helpers, or quota files
- `data/cache/club_results_csl_2026.csv`

Do not call The Odds API, consume quota, read `.env`, print secrets, deploy, publish, write ECS, update LaunchAgent, install dependencies, create a replay candidate, or lift `club_rating_pending`.

This work is data-quality plumbing only. It is not betting advice, does not output stake sizes, and does not recommend chasing, heavy positions, parlays, or execution actions.

Before implementation, run:

```bash
git status --short
```

Expected: note existing tracked documentation changes and do not revert them. At the time this plan was written, expected tracked changes were `RECENT_WORK.md` and the P9.4 plan document.

## File Structure

Modify these files only:

- Modify: `tests/collectors/test_club_aliases.py` - add strict alias coverage for verified 2023-2026 CSL English and Chinese source names.
- Modify: `tests/collectors/test_csl_results.py` - add parser regression coverage proving the previously blocked source names can enter parsing.
- Modify: `worldcup/collectors/club_aliases.py` - expand `csl_2026` aliases while preserving strict `match_known_club_alias()` behavior.
- Modify: `RECENT_WORK.md` - record the alias expansion result after verification.

No new runtime dependency and no new business module are needed.

## Verified Alias Set

Add aliases only for names seen in P9.4 CFL official / 7M evidence or already accepted by the current table.

| canonical_key | aliases to accept |
| --- | --- |
| `shanghai_port` | `Shanghai Port`, `Shanghai SIPG`, `Shanghai Port FC`, `上海海港`, `上海上港` |
| `shanghai_shenhua` | `Shanghai Shenhua`, `上海申花` |
| `shandong_taishan` | `Shandong Taishan`, `山东泰山` |
| `beijing_guoan` | `Beijing Guoan`, `北京国安` |
| `chengdu_rongcheng` | `Chengdu Rongcheng`, `成都蓉城` |
| `zhejiang_professional` | `Zhejiang Professional`, `Zhejiang`, `Zhejiang FC`, `Zhejiang Greentown`, `浙江队`, `浙江`, `浙江俱乐部绿城` |
| `henan` | `Henan FC`, `Henan`, `Henan Songshan Longmen`, `Henan Jiuzu Dukang`, `Henan Club Jiuzu Dukang`, `Henan Club Caitao Fang`, `河南队`, `河南`, `河南俱乐部`, `河南酒祖杜康`, `河南俱乐部酒祖杜康`, `河南俱乐部彩陶坊` |
| `tianjin_jinmen_tiger` | `Tianjin Jinmen Tiger`, `天津津门虎` |
| `wuhan_three_towns` | `Wuhan Three Towns`, `武汉三镇` |
| `meizhou_hakka` | `Meizhou Hakka`, `梅州客家` |
| `qingdao_west_coast` | `Qingdao West Coast`, `青岛西海岸` |
| `qingdao_hainiu` | `Qingdao Hainiu`, `青岛海牛` |
| `changchun_yatai` | `Changchun Yatai`, `长春亚泰` |
| `shenzhen_peng_city` | `Shenzhen Peng City`, `深圳新鹏城` |
| `yunnan_yukun` | `Yunnan Yukun`, `云南玉昆` |
| `dalian_yingbo` | `Dalian Yingbo`, `大连英博`, `大连英博海发` |
| `cangzhou_mighty_lions` | `Cangzhou Mighty Lions`, `Cangzhou Mighty Lions FC`, `Cangzhou Mighty Lions F.C.`, `沧州雄狮` |
| `dalian_pro` | `Dalian Pro`, `Dalian Professional`, `Dalian Professional FC`, `大连人` |
| `nantong_zhiyun` | `Nantong Zhiyun`, `南通支云` |
| `shenzhen` | `Shenzhen`, `Shenzhen FC`, `深圳队` |
| `chongqing_tonglianglong` | `Chongqing Tonglianglong`, `Chongqing Tonglianglong FC`, `重庆铜梁龙` |
| `liaoning_tieren` | `Liaoning Tieren`, `Liaoning Tieren FC`, `辽宁铁人`, `辽宁铁人楠波湾` |

Do not add fuzzy matching, substring matching, transliteration, source-specific fallback, or generic slug fallback to `match_known_club_alias()`.

## Task 1: Add Alias Gate Regression Tests

**Files:**
- Modify: `tests/collectors/test_club_aliases.py`

- [ ] **Step 1: Confirm worktree state**

Run:

```bash
git status --short
```

Expected: no source-code files are dirty before this implementation task starts. Existing documentation changes may be present and must not be reverted.

- [ ] **Step 2: Add strict alias matrix tests**

Append this test to `tests/collectors/test_club_aliases.py`:

```python
def test_csl_2023_2026_source_aliases_are_known():
    cases = [
        ("Shanghai Port", "shanghai_port"),
        ("Shanghai SIPG", "shanghai_port"),
        ("上海海港", "shanghai_port"),
        ("Shanghai Shenhua", "shanghai_shenhua"),
        ("上海申花", "shanghai_shenhua"),
        ("Shandong Taishan", "shandong_taishan"),
        ("山东泰山", "shandong_taishan"),
        ("Beijing Guoan", "beijing_guoan"),
        ("北京国安", "beijing_guoan"),
        ("Chengdu Rongcheng", "chengdu_rongcheng"),
        ("成都蓉城", "chengdu_rongcheng"),
        ("Zhejiang Professional", "zhejiang_professional"),
        ("Zhejiang", "zhejiang_professional"),
        ("浙江队", "zhejiang_professional"),
        ("浙江俱乐部绿城", "zhejiang_professional"),
        ("Henan FC", "henan"),
        ("Henan", "henan"),
        ("河南队", "henan"),
        ("河南俱乐部彩陶坊", "henan"),
        ("Tianjin Jinmen Tiger", "tianjin_jinmen_tiger"),
        ("天津津门虎", "tianjin_jinmen_tiger"),
        ("Wuhan Three Towns", "wuhan_three_towns"),
        ("武汉三镇", "wuhan_three_towns"),
        ("Meizhou Hakka", "meizhou_hakka"),
        ("梅州客家", "meizhou_hakka"),
        ("Qingdao West Coast", "qingdao_west_coast"),
        ("青岛西海岸", "qingdao_west_coast"),
        ("Qingdao Hainiu", "qingdao_hainiu"),
        ("青岛海牛", "qingdao_hainiu"),
        ("Changchun Yatai", "changchun_yatai"),
        ("长春亚泰", "changchun_yatai"),
        ("Shenzhen Peng City", "shenzhen_peng_city"),
        ("深圳新鹏城", "shenzhen_peng_city"),
        ("Yunnan Yukun", "yunnan_yukun"),
        ("云南玉昆", "yunnan_yukun"),
        ("Dalian Yingbo", "dalian_yingbo"),
        ("大连英博", "dalian_yingbo"),
        ("大连英博海发", "dalian_yingbo"),
        ("Cangzhou Mighty Lions", "cangzhou_mighty_lions"),
        ("沧州雄狮", "cangzhou_mighty_lions"),
        ("Dalian Pro", "dalian_pro"),
        ("大连人", "dalian_pro"),
        ("Nantong Zhiyun", "nantong_zhiyun"),
        ("南通支云", "nantong_zhiyun"),
        ("Shenzhen", "shenzhen"),
        ("Shenzhen FC", "shenzhen"),
        ("深圳队", "shenzhen"),
        ("Chongqing Tonglianglong", "chongqing_tonglianglong"),
        ("重庆铜梁龙", "chongqing_tonglianglong"),
        ("Liaoning Tieren", "liaoning_tieren"),
        ("辽宁铁人", "liaoning_tieren"),
        ("辽宁铁人楠波湾", "liaoning_tieren"),
    ]

    for raw_name, canonical_key in cases:
        result = match_known_club_alias("csl_2026", raw_name)

        assert result.raw_name == raw_name
        assert result.canonical_key == canonical_key
        assert result.unmatched_name is None
```

Expected: test file contains one new matrix test and existing unknown-team rejection tests remain unchanged.

- [ ] **Step 3: Run the alias test and verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/collectors/test_club_aliases.py::test_csl_2023_2026_source_aliases_are_known -v
```

Expected: command exits non-zero before implementation. The first failure should show a verified alias such as `上海海港`, `Zhejiang`, `Henan`, or `Cangzhou Mighty Lions` returning `None`.

## Task 2: Add CSL Parser Regression Tests

**Files:**
- Modify: `tests/collectors/test_csl_results.py`

- [ ] **Step 1: Add parser coverage for blocker names**

Append this test after `test_parse_csl_result_rows_blocks_unknown_team_without_slug_fallback` in `tests/collectors/test_csl_results.py`:

```python
def test_parse_csl_result_rows_accepts_verified_csl_source_aliases():
    result = parse_csl_result_rows(
        [
            _row(
                season="2023",
                date="2023-04-16",
                home_team="Cangzhou Mighty Lions",
                away_team="Nantong Zhiyun",
                source_match_id="csl-2023-1",
            ),
            _row(
                season="2023",
                date="2023-04-17",
                home_team="大连人",
                away_team="深圳队",
                source_match_id="csl-2023-2",
            ),
            _row(
                season="2025",
                date="2025-02-23",
                home_team="Henan",
                away_team="Zhejiang",
                source_match_id="csl-2025-1",
            ),
            _row(
                season="2026",
                date="2026-03-07",
                home_team="重庆铜梁龙",
                away_team="辽宁铁人楠波湾",
                source_match_id="csl-2026-1",
            ),
        ],
        competition_id="csl_2026",
        source_id="primary",
        source_role="primary",
    )

    assert result.issues == []
    assert result.valid_rows == 4
    assert [
        (row.home_canonical, row.away_canonical)
        for row in result.rows
    ] == [
        ("cangzhou_mighty_lions", "nantong_zhiyun"),
        ("dalian_pro", "shenzhen"),
        ("henan", "zhejiang_professional"),
        ("chongqing_tonglianglong", "liaoning_tieren"),
    ]
```

Expected: parser test proves the full-sample blocker names can pass the same strict path used by `worldcup.csl_results_probe`.

- [ ] **Step 2: Run the parser test and verify it fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/collectors/test_csl_results.py::test_parse_csl_result_rows_accepts_verified_csl_source_aliases -v
```

Expected: command exits non-zero before implementation with `team_alias_unmatched` issues.

## Task 3: Expand CSL Alias Mapping

**Files:**
- Modify: `worldcup/collectors/club_aliases.py`

- [ ] **Step 1: Replace the flat `_CSL_ALIASES` literal with grouped verified aliases**

Replace the current flat `_CSL_ALIASES` dictionary with:

```python
_CSL_ALIAS_GROUPS = {
    "shanghai_port": (
        "Shanghai Port",
        "Shanghai SIPG",
        "Shanghai Port FC",
        "上海海港",
        "上海上港",
    ),
    "shanghai_shenhua": ("Shanghai Shenhua", "上海申花"),
    "shandong_taishan": ("Shandong Taishan", "山东泰山"),
    "beijing_guoan": ("Beijing Guoan", "北京国安"),
    "chengdu_rongcheng": ("Chengdu Rongcheng", "成都蓉城"),
    "zhejiang_professional": (
        "Zhejiang Professional",
        "Zhejiang",
        "Zhejiang FC",
        "Zhejiang Greentown",
        "浙江队",
        "浙江",
        "浙江俱乐部绿城",
    ),
    "henan": (
        "Henan FC",
        "Henan",
        "Henan Songshan Longmen",
        "Henan Jiuzu Dukang",
        "Henan Club Jiuzu Dukang",
        "Henan Club Caitao Fang",
        "河南队",
        "河南",
        "河南俱乐部",
        "河南酒祖杜康",
        "河南俱乐部酒祖杜康",
        "河南俱乐部彩陶坊",
    ),
    "tianjin_jinmen_tiger": ("Tianjin Jinmen Tiger", "天津津门虎"),
    "wuhan_three_towns": ("Wuhan Three Towns", "武汉三镇"),
    "meizhou_hakka": ("Meizhou Hakka", "梅州客家"),
    "qingdao_west_coast": ("Qingdao West Coast", "青岛西海岸"),
    "qingdao_hainiu": ("Qingdao Hainiu", "青岛海牛"),
    "changchun_yatai": ("Changchun Yatai", "长春亚泰"),
    "shenzhen_peng_city": ("Shenzhen Peng City", "深圳新鹏城"),
    "yunnan_yukun": ("Yunnan Yukun", "云南玉昆"),
    "dalian_yingbo": ("Dalian Yingbo", "大连英博", "大连英博海发"),
    "cangzhou_mighty_lions": (
        "Cangzhou Mighty Lions",
        "Cangzhou Mighty Lions FC",
        "Cangzhou Mighty Lions F.C.",
        "沧州雄狮",
    ),
    "dalian_pro": (
        "Dalian Pro",
        "Dalian Professional",
        "Dalian Professional FC",
        "大连人",
    ),
    "nantong_zhiyun": ("Nantong Zhiyun", "南通支云"),
    "shenzhen": ("Shenzhen", "Shenzhen FC", "深圳队"),
    "chongqing_tonglianglong": (
        "Chongqing Tonglianglong",
        "Chongqing Tonglianglong FC",
        "重庆铜梁龙",
    ),
    "liaoning_tieren": (
        "Liaoning Tieren",
        "Liaoning Tieren FC",
        "辽宁铁人",
        "辽宁铁人楠波湾",
    ),
}

_CSL_ALIASES = {
    alias.lower(): canonical_key
    for canonical_key, aliases in _CSL_ALIAS_GROUPS.items()
    for alias in aliases
}
```

Expected: `_KNOWN_BY_COMPETITION = {"csl_2026": _CSL_ALIASES}` remains unchanged.

- [ ] **Step 2: Keep strict unknown-team behavior unchanged**

Do not edit these functions:

```python
def match_known_club_alias(competition_id: str, name: str) -> TeamAliasResult:
    stripped = name.strip()
    aliases = _KNOWN_BY_COMPETITION.get(competition_id, {})
    canonical = aliases.get(stripped.lower())
    if canonical is None:
        return TeamAliasResult(stripped, None, stripped)
    return TeamAliasResult(stripped, canonical)
```

Expected: `match_known_club_alias("csl_2026", "Unknown FC")` still returns `canonical_key is None`.

- [ ] **Step 3: Run focused tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest tests/collectors/test_club_aliases.py tests/collectors/test_csl_results.py -v
```

Expected: both files pass. The output should include the two new tests.

## Task 4: Verify P9.4 Proof Sample Still Passes

**Files:**
- No code files modified in this task.

- [ ] **Step 1: Run the existing local proof probe**

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

Expected: `probe_status=0`. The command reads only local ignored samples and writes only the ignored diagnostics JSON.

- [ ] **Step 2: Summarize proof diagnostics**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
import json
from pathlib import Path

payload = json.loads(Path("data/local/diagnostics/csl_results_source_probe.json").read_text(encoding="utf-8"))
print("valid_finished_matches", payload["coverage"]["valid_finished_matches"])
print("manual_review_required", len(payload["quality"]["manual_review_required"]))
print("team_alias_unmatched", payload["quality"]["team_alias_unmatched"])
print("score_mismatches", len(payload["quality"]["score_mismatches"]))
print("degraded_candidates", len(payload["quality"]["degraded_candidates"]))
print("can_enter_replay", payload["pending_gate"]["can_enter_replay"])
print("can_lift_club_rating_pending", payload["pending_gate"]["can_lift_club_rating_pending"])
print("pending_reasons", payload["pending_gate"]["reasons"])
PY
```

Expected:

```text
valid_finished_matches 8
manual_review_required 0
team_alias_unmatched []
score_mismatches 0
degraded_candidates 0
can_enter_replay False
can_lift_club_rating_pending False
pending_reasons ['valid_finished_matches_below_300']
```

- [ ] **Step 3: Confirm no replay candidate or production cache was installed**

Run:

```bash
test ! -f data/local/diagnostics/csl_results_replay_candidate.csv
test ! -f data/cache/club_results_csl_2026.csv
```

Expected: both commands exit 0.

## Task 5: Full Verification And Recent Work

**Files:**
- Modify: `RECENT_WORK.md`

- [ ] **Step 1: Run full test suite**

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

- [ ] **Step 3: Confirm ignored data is still ignored**

Run:

```bash
git status --short
```

Expected: no `data/probe/` or `data/local/diagnostics/` files appear. Source and test changes should be limited to `worldcup/collectors/club_aliases.py`, `tests/collectors/test_club_aliases.py`, `tests/collectors/test_csl_results.py`, `RECENT_WORK.md`, and tracked plan docs already approved by the user.

- [ ] **Step 4: Update `RECENT_WORK.md`**

Add this top entry:

```markdown
## 2026-06-23 P9.5 CSL alias gate expansion

- Expanded strict `csl_2026` alias coverage for verified 2023-2026 CFL official / 7M source names, including historical clubs and source Chinese names that blocked P9.4 full-sample parsing.
- Preserved `match_known_club_alias()` strict behavior: unknown clubs still do not fall back to slugified names and remain blocked from replay.
- P9.4 proof probe remains clean: `valid_finished_matches=8`, `manual_review_required=0`, `team_alias_unmatched=[]`, `can_enter_replay=false`, `can_lift_club_rating_pending=false`.
- This did not create `data/local/diagnostics/csl_results_replay_candidate.csv`, did not install `data/cache/club_results_csl_2026.csv`, did not call The Odds API, did not read `.env`, did not deploy, did not update LaunchAgent, and did not lift `club_rating_pending`.
- Verification: `PYTHONDONTWRITEBYTECODE=1 /Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py` returned `530/530 tests passed`; `git diff --check` passed.
```

Expected: the entry records the verified proof-probe numbers and does not claim full-sample replay is complete. If another confirmed branch change adds tests before this implementation runs, record the observed final `tests/run_tests.py` pass count instead of `530/530`.

- [ ] **Step 5: Ask before committing**

Ask the user:

```text
P9.5 alias 扩展已完成并通过验证。是否创建本地 commit？不会 push。
```

Expected: commit only after explicit confirmation. Pushing remains a separate confirmation.

## Task 6: Final Report

**Files:**
- No files modified in this task.

- [ ] **Step 1: Report engineering status**

Report these exact fields:

```text
changed source files
changed test files
alias blocker names covered
focused test command and result
proof probe status
valid_finished_matches
manual_review_required count
team_alias_unmatched list
score_mismatches count
degraded_candidates count
pending_gate.can_enter_replay
pending_gate.can_lift_club_rating_pending
full test command and pass count
confirmation that no replay candidate, production cache, The Odds API, .env, ECS, LaunchAgent, deploy, push, or club_rating_pending lift was used
next recommended step: rerun P9.4 full local sample acquisition plan after user confirmation
```

Expected: the report distinguishes confirmed engineering state from the next unexecuted step.

## Adversarial Self-Review

- Root cause: this plan addresses the actual P9.4 blocker, which is strict alias coverage for verified CSL source names. It does not bypass the gate by translating rows outside the parser.
- Scope control: the plan touches only aliases and tests. It does not expand samples, write replay candidates, install cache, wire `league_runner`, or lift `club_rating_pending`.
- Historical-team risk: historical clubs such as `Dalian Pro`, `Shenzhen`, `Nantong Zhiyun`, and `Cangzhou Mighty Lions` are accepted only as aliases for parsing historical results. Accepting them does not make them active 2026 fixture participants.
- Source-language risk: Chinese aliases are added because 7M raw arrays and some CFL official fields expose Chinese names. The aliases are explicit; no fuzzy matching or transliteration is introduced.
- Canonical-key risk: `Zhejiang` maps to the existing `zhejiang_professional`; `Henan` maps to the existing `henan`; this preserves current downstream canonical keys.
- Data-quality risk: alias expansion alone does not prove full-season source agreement. Full sample acquisition and cross-source diagnostics remain a separate step.
- Production risk: even if future full-sample parsing succeeds, this plan does not install `data/cache/club_results_csl_2026.csv` and does not change `club_rating_pending`.
- Verification risk: targeted tests and the existing P9.4 proof probe are required before reporting success.
- Research boundary: no betting advice, stake sizing, chasing, heavy-position, parlay, or execution recommendation is produced.

## Plan Self-Review

- P9.4 continuity: the plan follows the recorded blocker in `data/local/diagnostics/csl_results_manual_review.md`.
- Type consistency: tests call the existing `match_known_club_alias()` and `parse_csl_result_rows()` signatures without adding new public APIs.
- Gate preservation: unknown-team tests remain in place and no slug fallback is added to `match_known_club_alias()`.
- State boundaries: no public web read, The Odds API call, `.env` read, replay candidate write, production cache install, deployment, LaunchAgent update, commit, or push is included without a separate confirmation.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-23-csl-club-alias-expansion.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
