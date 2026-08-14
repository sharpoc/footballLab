# Local Worktree Curation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the approved mixed local worktree into three reviewable local commits while preserving all user changes and excluding local-only Claude state.

**Architecture:** Work only on `codex/ssh-deploy-hardening`, which is based on remote squash commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`. Apply precise ignore rules first, verify and commit the existing SSH deployment hardening second, then normalize and commit project documentation last; every staging operation names explicit paths.

**Tech Stack:** Git, `.gitignore`, Python 3 standard library, project test runner `tests/run_tests.py`, Markdown.

## Global Constraints

- Do not push, create a PR, merge, deploy, modify secrets, write databases, call provider APIs, or alter production state.
- Never use `git reset --hard`, forced checkout, `git clean`, or broad `git add -A` / `git add .`.
- Preserve `.claude/settings.local.json` and `.claude/worktrees/` on disk; ignore them without deleting them.
- Do not ignore the complete `.claude/` directory.
- Do not change model logic, public API contracts, scheduler behavior, dependencies, or deployment targets.
- Stage only the exact files named in each task.
- Stop before committing if a test, whitespace check, staged-path check, or sensitive-content scan fails.

---

## File Structure and Responsibilities

- Modify: `.gitignore`
  - Ignore only local Claude settings and nested Claude worktrees.
- Modify: `worldcup/ssh_deploy.py`
  - Keep the already-written non-blocking deployment lock, release symlink rejection, and fail-closed previous-release validation.
- Modify: `tests/test_ssh_deploy.py`
  - Keep the four already-written regression tests for the deployment hardening.
- Modify: `AGENTS.md`
  - Commit the project stage-authorization rules for Codex.
- Modify: `CLAUDE.md`
  - Commit the equivalent stage-authorization rules for Claude Code.
- Modify: `RECENT_WORK.md`
  - Retain recent work and deployment history, but remove wording that becomes false after this local commit.
- Create: `docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md`
  - Track the completed implementation plan and mark its released status explicitly.

---

### Task 1: Ignore local Claude workspace state

**Files:**

- Modify: `.gitignore`

**Interfaces:**

- Consumes: Git ignore matching for `.claude/settings.local.json` and `.claude/worktrees/`.
- Produces: clean `git status` visibility without deleting local Claude files or suppressing future shared `.claude/*` configuration.

- [ ] **Step 1: Verify the two local-only paths are currently unignored**

Run:

```bash
git check-ignore -q .claude/settings.local.json
test $? -eq 1
git check-ignore -q .claude/worktrees/daily-sidecar-prod/README.md
test $? -eq 1
```

Expected: both `git check-ignore` calls return exit code `1`, proving the new rules are not already present.

- [ ] **Step 2: Add the exact ignore rules**

Append exactly these lines to `.gitignore`:

```gitignore

# Local Claude Code state and managed worktrees
.claude/settings.local.json
.claude/worktrees/
```

- [ ] **Step 3: Verify exact matching and guard against an overbroad rule**

Run:

```bash
git check-ignore -v .claude/settings.local.json
git check-ignore -v .claude/worktrees/daily-sidecar-prod/README.md
test -z "$(git check-ignore --no-index .claude/shared.example.json 2>/dev/null || true)"
test -z "$(git status --short | rg '^\?\? \.claude/' || true)"
```

Expected: the first two commands identify the new exact rules; `.claude/shared.example.json` is not ignored; `git status` has no untracked `.claude/` entry.

- [ ] **Step 4: Stage and inspect only `.gitignore`**

Run:

```bash
git diff --check
git add .gitignore
git diff --cached --check
test "$(git diff --cached --name-only)" = ".gitignore"
git diff --cached -- .gitignore
```

Expected: one staged path and only the three-line local-state block plus its blank separator.

- [ ] **Step 5: Commit the ignore policy**

Run:

```bash
git commit -m "chore: ignore local Claude workspace state"
```

Expected: a local commit containing only `.gitignore`.

---

### Task 2: Verify and commit SSH deployment hardening

**Files:**

- Modify: `worldcup/ssh_deploy.py`
- Modify: `tests/test_ssh_deploy.py`

**Interfaces:**

- Consumes: existing `_deploy_script()` output and project `run_ssh_deploy()` behavior.
- Produces: a remote shell script that acquires `flock -n`, rejects a symlink release target, and accepts an empty previous release only when `current` truly does not exist.

- [ ] **Step 1: Review the exact code and test scope**

Run:

```bash
git diff -- worldcup/ssh_deploy.py tests/test_ssh_deploy.py
```

Expected code changes:

- derive `releases_dir` and `$releases_dir/.deploy.lock`;
- acquire file descriptor 9 with non-blocking `flock -n`;
- reject `[ -L "$release" ]` before extraction or switching;
- distinguish missing `current` from an unresolvable existing entry;
- require a resolved previous path to be a physical directory inside `releases_dir`.

Expected tests: four named regression tests covering release symlink, previous path, flock, and unresolvable current.

- [ ] **Step 2: Run the complete project suite on the exact worktree**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all discovered tests pass; only the allowlisted optional FastAPI module may skip because `fastapi` is not installed.

- [ ] **Step 3: Stage only deployment code and tests**

Run:

```bash
git add worldcup/ssh_deploy.py tests/test_ssh_deploy.py
git diff --cached --check
test "$(git diff --cached --name-only)" = "tests/test_ssh_deploy.py
worldcup/ssh_deploy.py"
git diff --cached --stat
```

Expected: exactly two staged paths, with no project documentation or ignored files.

- [ ] **Step 4: Commit the deployment hardening**

Run:

```bash
git commit -m "feat: harden SSH release deployment"
```

Expected: a local commit containing only the deployment module and its tests.

---

### Task 3: Normalize and commit project documentation

**Files:**

- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `RECENT_WORK.md`
- Create: `docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md`

**Interfaces:**

- Consumes: the already-approved stage-authorization text, the merged PR #2 deployment record, and the completed postmatch-shadow implementation plan.
- Produces: synchronized project rules and accurate durable engineering records.

- [ ] **Step 1: Mark the historical implementation plan as completed**

Insert after the `Tech Stack` paragraph in `docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md`:

```markdown
**Status:** Implemented, verified, and released through PR #2 in remote `main` commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`.
```

- [ ] **Step 2: Remove the stale local-only qualifier from the deployment record**

In the first `RECENT_WORK.md` entry, replace:

```text
本条部署记录仍为本地未提交内容。
```

with:

```text
本条记录随 SSH 部署加固分支纳入本地版本历史。
```

- [ ] **Step 3: Verify the authorization sections are synchronized**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path

def section(path: str, heading: str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    start = text.index(heading)
    tail = text[start:]
    next_heading = tail.find("\n## ", len(heading))
    return (tail if next_heading < 0 else tail[:next_heading]).strip()

agents = section("AGENTS.md", "## 阶段确认规则")
claude = section("CLAUDE.md", "## 阶段确认规则")
assert agents == claude
print("stage_confirmation_sections=identical")
PY
```

Expected: `stage_confirmation_sections=identical`.

- [ ] **Step 4: Self-review documentation quality and safety**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 - <<'PY'
from pathlib import Path
text = Path("docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md").read_text(encoding="utf-8")
for marker in ("TB" + "D", "TO" + "DO", "PLACE" + "HOLDER"):
    assert marker not in text
print("placeholder_scan=ok")
PY
git diff --check
if git diff -- AGENTS.md CLAUDE.md RECENT_WORK.md docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md | rg -n 'BEGIN (RSA |OPENSSH )?PRIVATE KEY|gho_[A-Za-z0-9]+|sk-[A-Za-z0-9]{16,}|(api[_-]?key|token|secret|cookie)[[:space:]]*[:=][[:space:]]*[^[:space:]]+'; then exit 1; fi
```

Expected: no placeholders, whitespace errors, private keys, access tokens, secret assignments, or cookie values.

- [ ] **Step 5: Stage only the documentation set**

Run:

```bash
git add AGENTS.md CLAUDE.md RECENT_WORK.md docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md
git diff --cached --check
git diff --cached --name-only
```

Expected staged paths, in Git sort order:

```text
AGENTS.md
CLAUDE.md
RECENT_WORK.md
docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md
```

- [ ] **Step 6: Commit the project records**

Run:

```bash
git commit -m "docs: record workflow and postmatch plan"
```

Expected: a local commit containing exactly the four documentation files.

---

### Task 4: Final local-only verification

**Files:**

- Verify only; no file changes.

**Interfaces:**

- Consumes: the three task commits plus the previously committed design and this implementation plan.
- Produces: evidence that the branch is clean, fully tested, locally ahead of `origin/main`, and unpublished.

- [ ] **Step 1: Run the complete project suite again**

Run:

```bash
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
```

Expected: all discovered tests pass; only the allowlisted optional FastAPI module may skip.

- [ ] **Step 2: Verify branch, status, commit scope, and unpublished state**

Run:

```bash
git diff --check
test -z "$(git status --short)"
test "$(git branch --show-current)" = "codex/ssh-deploy-hardening"
git log --oneline origin/main..HEAD
test -z "$(git ls-remote --heads origin codex/ssh-deploy-hardening)"
```

Expected:

- clean worktree;
- current branch `codex/ssh-deploy-hardening`;
- local commits for design, implementation plan, ignore policy, SSH hardening, and documentation;
- no remote branch named `codex/ssh-deploy-hardening`.

- [ ] **Step 3: Report preserved local-only state**

Run:

```bash
test -f .claude/settings.local.json
test -d .claude/worktrees
git check-ignore -q .claude/settings.local.json
git check-ignore -q .claude/worktrees/daily-sidecar-prod/README.md
```

Expected: local Claude state still exists and is ignored rather than deleted.

---

## Plan Self-Review

- **Spec coverage:** branch safety, exact submission classification, precise ignore scope, explicit staging, validation, secret scanning, and local-only completion each map to a task.
- **Scope:** one Git-curation workflow; no independent subsystem or production mutation is introduced.
- **Type/interface consistency:** no new runtime API or schema is introduced.
- **Recovery:** every failure stops before the affected commit; no destructive cleanup command appears.
- **Adversarial conclusion:** the remaining material risk is a failing pre-existing test in the mixed worktree. Task 2 makes that visible before the SSH hardening commit and forbids papering over it.
