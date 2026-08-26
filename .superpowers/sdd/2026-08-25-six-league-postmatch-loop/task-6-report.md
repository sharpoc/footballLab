# Task 6 Report: League Postmatch LaunchAgent Generator

## Implementation

- Added `worldcup.league_postmatch_launch_agent`.
- The deterministic plist uses the independent label
  `xin.celab.football.league-postmatch`, a `StartCalendarInterval` of 10:30
  and 16:30 Beijing time, and `RunAtLoad=false`.
- Generated runner arguments always use absolute paths and invoke
  `worldcup.league_postmatch_runner --root <workdir>`. The default observation
  artifact has no live, write, or notification flag. `--full-live` appends
  exactly `--live --write --notify`.
- Standard output and error paths are respectively
  `~/Library/Logs/worldcup/league-postmatch.out.log` and
  `~/Library/Logs/worldcup/league-postmatch.err.log` by default.
- The CLI prints a JSON dry-run artifact unless `--out` is supplied. Requested
  output is written through a same-directory temporary file, file and directory
  fsync, and atomic replacement. It does not install, load, or run the timer.
- The plist contains no endpoint, environment path/value, credential, quota, or
  notification configuration. Program arguments are an explicit plist array,
  with no shell interpolation.

## TDD evidence

Initial RED before production code:

```text
ModuleNotFoundError: No module named 'worldcup.league_postmatch_launch_agent'
```

Focused GREEN after implementation and a return-path correction for macOS
`/var` to `/private/var` canonicalization:

```text
4/4 focused tests passed
```

The focused tests parse the generated plist and cover the exact two scheduled
wakes, `RunAtLoad=false`, observation versus explicit full-live arguments,
default JSON dry-run behavior, sensitive-config exclusion, and same-directory
atomic replacement. They use only temporary directories and never touch the
user LaunchAgents or log directories.

## Full verification

```text
/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
1445/1445 tests passed, 1 module(s) skipped
Skipped modules:
  test_fastapi_app.py (optional: fastapi)

/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile worldcup/league_postmatch_launch_agent.py tests/test_league_postmatch_launch_agent.py
git diff --check
```

All listed verification commands exited 0.

## Adversarial review

- The approved design requires two calendar wakes, not a five-minute interval;
  the temporary dispatch ambiguity was resolved in favor of the approved design
  and task brief.
- No-match wakes are safe because the generator invokes the Task 5 runner's
  default dry-run observation mode unless an operator explicitly generates a
  separate `--full-live` artifact.
- `--full-live` is only artifact generation; it is not authorization to install
  the plist, execute the runner, probe FotMob, write results, or send a real
  notification. Those remain independent operational gates.
- The generator does not add endpoint, secret, environment, quota, provider
  fallback, model, deployment, or public API behavior.

## Review fix round 1

### RED evidence

Added a cross-call isolation regression that mutates the first returned
`StartCalendarInterval` and then builds a second plist. Before the fix it
failed with an assertion because the module-level mutable schedule list leaked
the changed hour into the later artifact.

### Correction

The schedule template is now an immutable tuple of time pairs. Each invocation
of `build_league_postmatch_launch_agent` constructs a new list of new plist
dictionaries, so caller mutation cannot affect any later generated artifact.

### Verification

```text
isolation regression passed
5/5 focused tests passed

/Users/eagod/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 tests/run_tests.py
1446/1446 tests passed, 1 module(s) skipped
Skipped modules:
  test_fastapi_app.py (optional: fastapi)
```

`py_compile` and `git diff --check` also exited 0. This review fix did not
install or load a timer, invoke the runner, run live processing, notify,
push, create a PR, merge, or deploy.
