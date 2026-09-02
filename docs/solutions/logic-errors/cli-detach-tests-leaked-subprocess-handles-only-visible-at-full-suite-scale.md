---
title: CLI detach/follow tests leaked subprocess handles, warning only visible at full-suite scale
date: 2026-09-01
category: logic-errors
module: tests/test_cli.py
problem_type: test_failure
component: testing_framework
symptoms:
  - "Full suite run left ResourceWarning: subprocess N is still running lines, suite still passed"
  - "Narrowing to a single test module or file under -W always found nothing"
root_cause: test_isolation
resolution_type: test_fix
severity: low
tags: [resourcewarning, tracemalloc, subprocess-detach, test-isolation, shared-test-helper, full-suite-only]
---

# CLI detach/follow tests leaked subprocess handles, warning only visible at full-suite scale

## Problem
The suite intermittently left `ResourceWarning: subprocess N is still running` lines. The backlog line that filed it (docs/backlog.md, 2026-08-28) guessed it was "some test around the new run --follow/--detach path" that forgot to `wait()`/kill its child, framing it as a per-test bug.

## Symptoms
- Full suite run: two (later found to be up to 14) `ResourceWarning: subprocess N is still running` lines, suite still green.
- Re-running any single suspected test module or file with `-W error::ResourceWarning` found nothing.

## What Didn't Work
- Isolating individual test modules/files under `-W` to catch the warning at its source: found nothing, because Python only emits the warning when the garbage collector actually visits the abandoned `Popen` object, which can happen many tests later (or not at all) in a small run. A narrowed run changes the GC's timing enough to hide the warning entirely.

## Solution
`-X tracemalloc=25` on the full suite run produced allocation-site tracebacks for the abandoned objects. Every one traced back to a single shared site: `CliCase.call()` in `tests/test_cli.py`, used by every `--detach`/`--follow` test in the file. `cli._detach()` (skills/relay/scripts/relay/cli.py) correctly never waits on the real child process it spawns, since a detached run is meant to outlive the CLI call that launched it, but the test helper discarded the returned `Popen` object without ever reaping it.

Fix: track every `Popen` spawned during a test and reap it in `tearDown`, after the test itself has already driven the run to a terminal state.

```python
class CliCase(RunCase):
    def setUp(self):
        super().setUp()
        self._spawned = []
        real_popen = cli.subprocess.Popen

        def tracking_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            self._spawned.append(proc)
            return proc

        self._real_popen = real_popen
        cli.subprocess.Popen = tracking_popen

    def tearDown(self):
        cli.subprocess.Popen = self._real_popen
        for proc in self._spawned:
            if proc.poll() is None:
                proc.wait(timeout=30)
        super().tearDown()
```

Commit 14fdec2, landed via merge 241aa69.

## Why This Works
Production code was never the problem: `cli._detach()` deliberately never waits on the child so a `--detach`/`--follow` run can outlive the CLI invocation. The leak was entirely in the shared test fixture that called it. Reaping in `tearDown`, after the test has already waited for the run's terminal state (directly, via `--follow`, or via `wait_for_terminal()`), just closes the wrapper object instead of leaving it for the GC to warn about later.

## Prevention
- A ResourceWarning (or similar GC-timing-dependent warning) that reproduces at full-suite scale but disappears when the suspect test is run alone or in a small group is a signal to stop guessing at the named test and instead get an allocation-site traceback (`-X tracemalloc=<N>`) on the full run. The warning's timing depends on when the GC visits the object, not on which test created it.
- When a shared test base class wraps a resource-creating call (here, `subprocess.Popen`) for every subclass test, check that class for the corresponding cleanup before assuming the bug is in an individual test.

## Related Issues
- Backlog line docs/backlog.md 2026-08-28 (superseded by GitHub issue #45, closed).
