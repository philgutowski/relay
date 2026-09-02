---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
depth: lightweight
created: 2026-09-01
---

# Fix: suite leaves ResourceWarning: subprocess still running

## Problem Frame

The suite (`python3 -m unittest discover -s tests`) emits `ResourceWarning: subprocess N is
still running` lines during a full run. The suite still passes; this is cosmetic noise, not a
functional bug.

Source: `docs/backlog.md`, `- [2026-08-28] Round 4's suite run left two ResourceWarning:
subprocess N is still running lines: some test around the new run --follow/--detach path spawns
the stub claude and doesn't wait()/kill it before the test ends. Cosmetic, suite still passes;
find the test and clean it up.`

## Root Cause

Confirmed with `python3 -X tracemalloc=25 -m unittest discover -s tests`, which prints the
allocation traceback for every warning. Every single warning traces back to the same allocation
site: `skills/relay/scripts/relay/cli.py:211`, the `subprocess.Popen(...)` call inside
`_detach()`, reached through `tests/test_cli.py:38`, the `CliCase.call()` helper that every
`--detach`/`--follow` case in that file uses to invoke `cli.main(...)`.

`_detach()` deliberately does not join the process: a real `relay run --detach` is meant to
outlive the CLI invocation that launched it (that is the entire point of detaching), so
production code correctly never calls `.wait()`/`.poll()` there once the run isn't being
followed. `_follow()` does poll the process (`runner_alive=lambda: proc.poll() is None`) while
watching it, but that lambda is read from inside `tail.follow`'s own loop; the loop can observe
the run's completion (terminal record) and return before it happens to call `runner_alive()`
again after the child has actually exited. Either way, `cli.main()` returns with the local `proc`
Popen object never having had `.wait()`/`.poll()` called with the child already dead, so
`proc.returncode` stays `None`. `CliCase.call()` discards the `code, out` tuple `cli.main()`
returns and never sees `proc` at all, so nothing in the test ever reaps it. When Python collects
the abandoned `Popen` object, its `__del__` finds `returncode is None` and warns.

This is a test-harness gap, not a production bug: `_detach()`/`_follow()` must keep their current
fire-and-forget semantics for a real detached run. The fix has to reap the child from the test
side, after the point where the test already knows (or waits for) the run to be done, without
touching `cli.py`.

## Scope

**In scope:** `tests/test_cli.py`'s `CliCase` base class, which every `--detach`/`--follow` case
in that file inherits from.

**Out of scope:** `skills/relay/scripts/relay/cli.py` (`_detach`/`_follow` stay as they are;
their job is genuinely to leave a process running past the caller's return). No other test file
showed up in the tracemalloc allocation traces, so no other file needs a change.

## Approach

Give `CliCase` a `setUp`/`tearDown` pair that wraps `cli.subprocess.Popen` for the duration of
each test in that file, records every real child process `cli.main()` spawns, and reaps them
(`proc.wait(timeout=...)`) in `tearDown`, after the test method has already finished (every
affected test either blocks on `--follow` until completion or explicitly polls
`self.wait_for_terminal()`/`store().terminal()` before returning, so by `tearDown` time the child
is already done or finishing momentarily).

The wrap has to be dynamic (read `cli.subprocess.Popen` at call time, not cache a stale
reference) because `WhoNotifies.detach_argv()` already saves/restores `cli.subprocess.Popen`
itself mid-test to substitute a fake launch; the two patches need to compose rather than clobber
each other.

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

`WhoNotifies.detach_argv()` saves `original = cli.subprocess.Popen` (which will be
`tracking_popen` once this lands), installs its own `fake`, and restores `original` in a
`finally`, that still leaves `tracking_popen` active afterward, and `fake`'s real branch
(`return original(command, **kwargs)`) calls through `tracking_popen`, so those processes get
recorded too. No change needed there.

## Files

- `tests/test_cli.py`, add `setUp`/`tearDown` to `CliCase` (around line 35-39).

## Test Scenarios

No new test is warranted for a test-harness cleanup with no behavior change. There is no
observable outcome to assert beyond "the suite no longer warns." Verification is the gate itself:

1. Run the full suite (`python3 -m unittest discover -s tests`) and confirm it still passes with
   the same test count.
2. Run `python3 -X tracemalloc=25 -m unittest discover -s tests 2>&1 | grep -c "ResourceWarning: subprocess"`
   before and after the change and confirm the count drops to 0.

## Risks

- `proc.wait(timeout=30)` could in principle hang `tearDown` if a child never exits. Every
  affected test already waits for the run to reach a terminal state before returning (directly or
  via `wait_for_terminal()`), so the child should already be exited or exiting by `tearDown` time;
  the 30s timeout is a backstop, not the expected path. If it ever fires it should fail loudly
  (propagate `subprocess.TimeoutExpired`) rather than silently swallow a hang.
