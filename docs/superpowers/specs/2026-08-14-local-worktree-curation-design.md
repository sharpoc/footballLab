# Local Worktree Curation Design

## Goal

Safely turn the current mixed local worktree into reviewable local commits without losing user changes, recommitting the squash-merged CSL release history, or publishing local-only Claude state.

## Starting State

- Remote `main` and production use squash commit `5d006be240fd42ef320e0e5ec1aee69992f0e9c9`.
- The old local `main` commit tree is byte-equivalent to remote `main`, but its history contains the eight pre-squash commits.
- Five tracked files are modified and two paths are untracked.
- The tracked diff must remain intact while work moves to a non-`main` branch.

## Branch Strategy

Use `codex/ssh-deploy-hardening`, based directly on `origin/main`. Before and after switching, hash the complete tracked binary diff and require equality. The already-pushed `codex/csl-closing-coverage-release` branch preserves the pre-squash commit history, so no destructive reset or forced branch update is needed.

## Submission Classification

Commit these files:

- `worldcup/ssh_deploy.py` and `tests/test_ssh_deploy.py`: deployment lock, release symlink rejection, and fail-closed previous-release validation.
- `AGENTS.md` and `CLAUDE.md`: synchronized project authorization rules.
- `RECENT_WORK.md`: recent implementation and deployment record.
- `docs/superpowers/plans/2026-08-12-csl-postmatch-shadow.md`: the completed postmatch-shadow implementation plan retained as engineering provenance.
- `.gitignore`: precise local Claude-state exclusions.

Do not commit these local-only paths:

- `.claude/settings.local.json`
- `.claude/worktrees/`

Do not ignore the entire `.claude/` directory, so future shared Claude configuration can still be versioned intentionally.

## Commit Structure

Create three local commits with explicit path staging:

1. `chore: ignore local Claude workspace state`
2. `feat: harden SSH release deployment`
3. `docs: record workflow and postmatch plan`

The design document itself is committed separately before implementation, as required by the brainstorming workflow. No commit is pushed, no PR is opened, and no deployment is performed in this task.

## Validation

- Verify `.claude/settings.local.json` and `.claude/worktrees/` disappear from `git status`, while no broader `.claude/` rule exists.
- Run the complete project test command after the deployment code and tests are staged in the worktree.
- Run `git diff --check` and `git diff --cached --check` before every commit.
- Scan staged documentation for credential-like assignments and confirm no `.env`, token, API key, Cookie, or request header values are present.
- Confirm final branch status contains only the intended commits and no remaining tracked modifications.

## Failure and Recovery

- If switching branches changes the tracked diff hash, stop before staging or committing.
- If tests fail, do not commit the deployment hardening; diagnose the failure while preserving all changes.
- If any staged set contains an unintended path, unstage only that path and re-check the staged diff. Never use `git reset --hard`, forced checkout, or cleaning commands.
- The local-only `.claude/` data remains on disk throughout; ignore rules only suppress Git discovery.

## Adversarial Review

- **Squash-history risk:** addressed by branching from `origin/main`, not from the divergent local `main` history.
- **User-change loss:** addressed by before/after binary diff hashing and explicit-path staging.
- **Overbroad ignore risk:** addressed by ignoring only `settings.local.json` and `worktrees/`.
- **Mixed-commit risk:** addressed by separating ignore policy, deployment behavior, and project documentation.
- **Secret leakage risk:** addressed by staged-content scanning and by never staging ignored runtime data.
- **Scope risk:** no model, API, database, dependency, provider, scheduler, or production behavior is changed beyond the already-present SSH deployment hardening.
