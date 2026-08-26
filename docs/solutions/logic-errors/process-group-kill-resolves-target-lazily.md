---
title: A process group kill that resolved its target lazily worked only in the case that did not need it
date: 2026-08-26
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: concurrency
resolution_type: code_fix
related_components: [task-process, launcher, timeout-handling]
symptoms:
  - "the full test suite passes, 298 of 298, while the defect is present"
  - "fixing one conjunction in the same function turns a passing test into a hang past 60 seconds with no output"
  - "os.getpgid(proc.pid) raises ProcessLookupError, but only after proc.poll() has reaped the leader"
  - "a subagent or gate the task spawned keeps running after claude -p exits, and the runner never returns"
tags: [process-group-kill, os-getpgid, deadline-conjunction, reader-thread-deadlock, self-masking-guard, unattended-run, task-process]
---

# A process group kill that resolved its target lazily worked only in the case that did not need it

## Problem

Relay is an unattended outer loop. It launches one headless `claude -p` Task process per
tracker task, serially, for hours, with nobody watching.
`skills/relay/scripts/relay/launch.py` owns that launch, the deadline, and the process group
kill that is supposed to bound it. The module docstring names the hazard directly at
`launch.py:9` to `:11`: a detached `claude -p` that inherits an open pipe "reads it until EOF
and idles to the timeout; and without a new session, a timeout kill reaches the process but not
the subagents and gates it started, which then outlive the task." R49 in the plan exists for
exactly this reason.

Before commit `eae48d5`, the kill helper resolved the group id lazily, at the moment it was
asked to kill, with `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)`. The deadline check that
called it read `if time.monotonic() >= deadline and proc.poll() is None:`. Read together, the
kill only ever ran while `proc.poll()` still reported the direct child alive, which is also the
only case in which `os.getpgid(proc.pid)` was guaranteed to succeed. The one case the whole
mechanism exists for, `claude -p` having already exited while a subagent or gate it spawned
keeps running, was precisely the case the guard was written never to reach.

## Symptoms

Nothing was visibly wrong. The full suite, 298 tests, passed on the pre fix code. A code review
flagged the deadline's `and proc.poll() is None` conjunction on its own terms, independent of
the kill helper. Applying just that one fix, so the deadline could fire regardless of whether
the leader was still alive, turned the green suite into a single test that hung for more than
sixty seconds with no output. Nothing crashed. Nothing logged an error. The runner simply never
returned, which is the exact failure the timeout exists to prevent.

Diagnosing the hang surfaces two things at once, both consequences of the same lazy resolution:

- `os.getpgid(proc.pid)` raises `ProcessLookupError` once `proc.poll()` has reaped the leader.
  The kill helper's own except clause then swallows the failure and reports the kill as a no op,
  while the grandchild that inherited stdout keeps running.
- With that grandchild alive and holding the inherited stdout pipe open, the reader thread's
  `for line in self.stream:` loop never reaches EOF, so the sentinel never arrives, `done` stays
  `False`, and the only remaining exit condition in the read loop was the very deadline check
  that had just been defanged.

## What Didn't Work

**Removing the `proc.poll() is None` conjunction alone.** This was the fix proposed first, and
it is necessary, but applying it in isolation is what produced the hang above. The deadline now
fires after the leader has exited, which is correct, but the kill helper it calls still resolved
the group id with `os.getpgid(proc.pid)` at that later moment. By then `proc.poll()` has already
reaped the leader, so the lookup raises `ProcessLookupError`, the helper returns `False`, and
the group is never signalled. The fix meant to close the gap opened it wider, because the two
defects were coupled: the first had been hiding the second.

**Trusting that the group kill worked because the tests were green.** The pre fix helper had a
passing call site, the class now named `Timeout` at `tests/test_launch.py:204`, which sends a
process into a long sleep and checks it gets killed. That test only ever exercises the branch
where `proc.poll() is None` at kill time, because the stub is still running when the deadline
hits. It could not have caught this defect, because it never puts the code into the state where
`os.getpgid` is called on an already reaped pid. A guard whose only test shares the guard's own
precondition proves nothing about the branch that matters.

**Closing `proc.stdout` from the main thread as the first step of cleanup.** The original order
was `proc.stdout.close()` immediately, then `reader.join(timeout=1)`. Once the deadline fix let
the loop exit while the reader thread was still blocked inside a read on that same stream,
closing the handle out from under it is a second latent deadlock: a buffered reader closed by
another thread while a read holds its internal lock does not raise, it hangs. This never
appeared as a separate bug report because it is downstream of the same trigger. Fixing the
deadline exposed both problems at once, from one hanging test.

## Solution

Three changes, all in `skills/relay/scripts/relay/launch.py`, landed together in commit
`eae48d5`, merged as `0118d50`, because none of them is safe to ship alone.

**Capture the process group id once, right after `Popen`, while the leader is still alive.**

Before:

```python
def _kill_group(proc, grace_seconds):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
```

After, at `launch.py:163` to `:191`:

```python
def _kill_group(proc, grace_seconds, pgid=None):
    """...
    `pgid` must be the value captured at launch. Resolving it here instead would fail once the
    leader has been reaped, which is the common case: the direct process exits, a subagent or a
    gate keeps running, and the group is exactly what still needs killing."""
    if pgid is None:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
```

`launch()` resolves `group_id` immediately after `Popen` returns, before the reader thread, the
heartbeat, or the signal handlers exist, at `launch.py:226` to `:229`:

```python
try:
    group_id = os.getpgid(proc.pid)
except OSError:
    group_id = None
```

Every call site, the signal handler, the lease loss branch, and the deadline branch, passes that
captured `group_id` rather than asking the helper to resolve it fresh.

**Fire the deadline unconditionally.** Before it read
`if time.monotonic() >= deadline and proc.poll() is None:`. After, at `launch.py:286` to `:292`:

```python
if time.monotonic() >= deadline:
    # Not conditioned on the child still running. A grandchild that inherited
    # stdout keeps the pipe open after its parent exits, so the reader never
    # reaches EOF and this is the only bound left.
    result.timed_out = True
    result.killed_group = _kill_group(proc, sigkill_grace_seconds, group_id)
    break
```

**Join the reader before closing the stream it reads.** At `launch.py:309` to `:319`:

```python
# Order matters. The reader thread may be blocked inside stdout, and closing a
# buffered reader from this thread while that read holds its lock deadlocks. Wait for
# the reader to finish first, which it does as soon as the last descendant holding the
# inherited pipe is gone, and only then close. If a descendant survived the group kill,
# leave the daemon thread be rather than hanging the runner on it.
reader.join(timeout=max(1.0, float(sigkill_grace_seconds)))
if not reader.is_alive():
    try:
        proc.stdout.close()
    except (OSError, ValueError):
        pass
```

That a saved pgid still reaches survivors after the leader is reaped was confirmed directly
before the fix was written, rather than assumed: a parent that spawns a sleeper and exits,
then `proc.poll()`, then `os.getpgid(proc.pid)`, raises `ProcessLookupError`, while
`os.killpg` against a pgid captured at launch delivers the signal to the survivor.

## Why This Works

The defect was never really three bugs. It was one guard, kill the group if the deadline has
passed, whose precondition, the direct child still being alive, was exactly the condition that
made the group kill unnecessary. If the leader is still running normally, letting it run a
little longer to hit a deadline check is low stakes and easy to test. The only scenario the
group kill exists for is the leader having exited while something it spawned has not, and that
scenario is precisely the one the old conjunction excluded from ever reaching the kill call. A
guard that cannot fail in the case it is exercised in, and is never exercised in the case it
matters, will pass every test written against it and do nothing the one time it is needed.

Capturing `group_id` at launch, while the leader is guaranteed alive, removes the lazy
resolution that failed later. Firing the deadline unconditionally removes the other half of the
coupling: it makes the reaped leader case reachable, so the kill runs in the state it was
written for instead of only in the state it was accidentally tested in. The reader join ordering
fixes a purely mechanical hazard that the first fix exposed by making that path executable at
all: a thread cannot safely close a stream another thread is blocked reading, so cleanup has to
wait for the reader before touching the handle it owns.

## Prevention

**The test that pins this** is `tests/test_launch.py:266` to `:277`, class
`TimeoutWithASurvivingGrandchild`. It launches the stub with `RELAY_STUB_CHILD=1`, which spawns
a sleeping grandchild (`tests/stub-claude/claude:18` and `:83`), lets the stub itself exit
quickly, and asserts `result.timed_out` and `result.killed_group` are both still true. The
companion at `tests/test_launch.py:212` to `:230` checks the other half by reading the
grandchild's real pid out of the log and polling the `alive` helper at `tests/test_launch.py:23`
to `:28`, which is `os.kill(pid, 0)`, until the pid is provably gone.
Between them, both the deadline fires when it must claim and the kill actually reaches the
grandchild claim have a test that fails without the fix and passes with it. Neither existed
against the old code. A suite that is green cannot be trusted to have covered a branch it never
entered.

**The generalisation** is the part worth carrying past this one function: a guard whose
precondition is the same condition that makes the guard unnecessary is untested by construction.
It cannot fail in the case it is exercised in, because that case is defined to be the easy one,
and it is never exercised in the case where it matters, because that case is defined to be the
one the precondition excludes. The only way to find this shape is to force the code down the
excluded branch on purpose, the way `RELAY_STUB_CHILD=1` does, rather than infer safety from a
passing suite that never took that branch.

Relay has more code shaped this way, and it is worth naming rather than assuming clean:

- The lease loss checks at `skills/relay/scripts/relay/run.py:285` and `:495` only run their
  guarded behaviour once a lease is already gone. A test has to force the lease into a lost
  state deliberately, the way `test_a_heartbeat_that_reports_a_lost_lease_stops_the_run` does at
  `tests/test_launch.py:240` to `:244`, not merely assert the heartbeat exists.
- The blocking skip pattern in `skills/relay/scripts/relay/verify.py`, defined at `:87` and used
  at `:139` and `:172` among others, exists to turn an unreadable git state into not landed
  rather than a crash. That path by definition only runs when something upstream is already
  degraded, so it deserves the same scrutiny: does the suite actually put it into the degraded
  state, or does it exercise the healthy path and assume the fallback follows?

  **Checked on 2026-08-26, and the answer was worse than a missing test.** The skip machinery
  itself is sound. What was not sound was its supply: the GitHub adapter's `status` turned an
  unreadable project board into `terminal: False, skipped: None`, a definite answer, so the
  degraded path verify was written to take could not be reached from that adapter at all. A
  suite that exercises the skip by handing verify a `skipped` reason directly proves the skip
  works and proves nothing about whether anything ever produces one. The lesson generalises the
  one below: it is not enough to force the guarded branch, you also have to check that the code
  upstream can put the system into the state the branch guards. The fix, and the two tests that
  pin both halves of the distinction, are in `tests/test_adapters.py`.

The check to apply going forward, here and anywhere else in Relay with this shape: before
trusting a kill path, a lease check, or a skip branch, ask whether any existing test forces the
precondition the branch guards against, not merely the precondition that lets the surrounding
function return normally.

## Related

- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` is the same
  family of learning: behaviour of the CLI or the operating system that shows up only in an
  unattended run, is documented nowhere the CLI help or a code review would surface it, and has
  to be designed around rather than patched once and forgotten. That doc's permission gate on the
  `.claude/` directory of a target repo, which is external to Relay, and this one's process group
  kill both surfaced only because a run was left to complete with nobody present to notice a
  subtler failure. Both close on the same posture: an unattended
  runner should assume there are more such gates and more such guards it has not met yet.
- `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`, R49, is the requirement the buggy
  deadline was implementing: the runner starts each `claude -p` in its own process group and
  kills the whole group on timeout, so subagents and a gate mid run do not outlive the task. Its
  acceptance examples for U6 describe only the direct child still alive case, which is the case
  the old guard handled correctly and the only case it was ever tested against.
- The same plan's KTD10 is the decision that put a monotonic clock deadline and an independent
  timer thread into the launcher. This defect sits inside that decision rather than contradicting
  it; the monotonic clock was never the problem.
- `docs/ideation/2026-08-25-relay-review-residuals.md` records the findings from the same code
  review that this fix did not address. It is deliberately silent on the launcher, because the
  launcher findings were among the fourteen that were applied.

During the plan's own document review, before any launcher code existed, a feasibility reviewer
had already flagged that stdin must be devnull for a detached launch, and an adversarial
reviewer raised timeout halting the batch as a substantive finding (session history). Both were
folded into the plan. Neither anticipated this defect, which is worth noting: the design review
got the shape of the hazard right and still did not catch the guard that would fail to act on it.
