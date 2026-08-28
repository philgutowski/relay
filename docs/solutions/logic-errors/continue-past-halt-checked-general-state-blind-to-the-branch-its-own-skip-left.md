---
title: continue-past-halt checked general repo state, blind to the task branch its own skip left behind, so the halted task could never run again
date: 2026-08-28
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [run-loop, gitwrite, lease, halt-evidence]
symptoms:
  - "run --continue-past-task-halt's _continue_past asked gitwrite.resume_disposition only about general repo state (tree clean, on default branch, head equals origin), never about the halted task's own relay/<id> branch"
  - "_one_task's own preflight refused that same task again on no_task_branch before launching anything, because the feature never deletes the stranded relay/<id> branch it skipped"
  - "resume_disposition saw the general state as clean and returned continue every time, so run.py marked the task continued_past again, forever, without the task ever actually running"
  - "every run wrote RUN_COMPLETED and exited 0, a silent success masking a task that could never run again"
  - "the feature's own retry regression test passed only because it deleted the stranded branch by hand before the second run, the exact repair the bug made necessary, so the test could never have caught it"
tags: [continue-past-halt, task-scoped-branch, resume-disposition, silent-success, lease-check, code-review-catch, self-masking-guard, retry-loop]
---

# continue-past-halt checked general repo state, blind to the task branch its own skip left behind, so the halted task could never run again

## Problem

Issue #15 let a manifest opt into `on_halt.continue_past_task_halt`, so a halt local to one task no longer stops every later, independent task. The disposition function that decides whether the run may step over a halt, `gitwrite.resume_disposition`, checked only general repo state (clean tree, on the default branch, default branch head equal to `origin/<default>`) and had no way to see that the very task it was stepping over had left its own stranded branch behind, which is exactly what the next run's own pre-flight refuses on.

## Symptoms

None, which is the symptom. The first run halts a task, the manifest is configured to continue past it, and `relay run` reports `RUN_COMPLETED` with exit code 0, with the task's record correctly showing `status=halted, continued_past=True`. Every subsequent `relay run` invocation does the exact same thing again for the same task: it reports success and exits 0. Nothing in the terminal output, the exit code, or the run summary distinguishes the first continue-past (a real decision, correctly deferring to the operator) from the second, third, and every later one (a stuck retry that will never make progress, because the task's own leftover branch keeps refusing pre-flight before the task's process ever launches). An operator watching only exit codes and the summary's "completed" status would never learn the task is permanently stuck.

## What Didn't Work

The feature shipped with a regression test for "a paused task is retried on the next run," and it passed. But the test called `git branch -D` on the task's stranded branch before invoking `run()` a second time, exactly the manual repair the plan already required for any halt (`docs/plans/2026-08-28-1028-feat-continue-past-task-scoped-halt-plan.md` Scope Boundaries: "No repair of the repo beyond checking out the default branch. The runner never resets, stashes, or deletes anything to make the disposition pass; a repo that needs that is the operator's to repair"). The test proved the repaired path worked. It never exercised the unrepaired path, and the unrepaired path is the one every real continue-past-halt run actually takes on its very next invocation, since nothing in the feature deletes the branch and no operator has been told to.

This is a specific case of a general trap: a regression test for a repeated decision that always starts from a state where the previous repair already happened cannot catch a bug in what happens when it hasn't. 532+ passing tests and a clean implementation gave no signal here, because the one test written for exactly this scenario quietly did the fix's job for it before asserting anything.

The bug was caught only by two independent reviewers in the same `ce-code-review` pass (a correctness-reviewer and an adversarial-reviewer), both tracing the retry path by hand across runs rather than trusting the feature's own green test.

## Solution

Landed in commit `cfebd40` on `relay/continue-past-task-halt`, merged to `main` at `27e8603`. `_continue_past` in `skills/relay/scripts/relay/run.py:230` now checks the halt's own evidence before ever calling `resume_disposition`, and refuses unconditionally when the halt IS the exact preflight refusal this same task left behind:

```python
def _continue_past(cfg, halt):
    if not cfg.manifest.on_halt.continue_past_task_halt:
        return False
    if halt.halt_class in contracts.RUN_SCOPED_HALT_CLASSES:
        return False
    if halt.evidence.get("check") == "no_task_branch":
        halt.evidence["resume"] = {"check": "no_task_branch"}
        return False
    if not cfg.store.heartbeat():
        halt.evidence["resume"] = {"check": "lease_lost"}
        return False
    try:
        result = gitwrite.resume_disposition(cfg.repo, cfg.default, ops=cfg.store,
                                             task_id=halt.task_id, env=cfg.env)
    except gitread.GitError as exc:
        halt.evidence["resume"] = {"check": "git_error", **_git_error_fields(exc)}
        return False
    except Exception as exc:
        halt.evidence["resume"] = {"check": "unexpected_error",
                                   "error_type": type(exc).__name__, "error": str(exc)[:500]}
        return False
    if result.ok:
        return True
    halt.evidence["resume"] = dict(result.evidence, check=result.failed)
    return False
```
(`run.py:230-273`)

The `no_task_branch` check at line 254 short-circuits before `resume_disposition` is ever called, restoring the exact full-stop behavior the `HALT_UNCLEAN_EXIT` raise site (`run.py:299-305`, in `_one_task`'s pre-flight block) had before the continue-past-halt feature existed. `_one_task`'s pre-flight (`gitwrite.preflight`, `gitwrite.py:256-272`) runs before any task process launches and checks four things in order — `tree_clean`, `on_default`, `head_equals_remote`, `no_task_branch` (the last one only, whether `relay/<task-id>` already exists) — and refuses with `HALT_UNCLEAN_EXIT`, a task-scoped class (not in `contracts.RUN_SCOPED_HALT_CLASSES`, `contracts.py:207-211`), evidence `{"check": "no_task_branch", ...}`, whenever the fourth check fails.

The same commit also closed a related gap the reviews found: `resume_disposition`'s one mutation (the `git checkout` back to the default branch, `gitwrite.py:436`) had no lease guard, unlike every other mutating call in `run.py`. The `heartbeat()` check at `run.py:257-259` now refuses before the checkout can fire against a repo whose lease is already gone. Both refusals record their reason under `halt.evidence["resume"]` so the record explains why the run stopped rather than continuing, and the `try/except` around the `resume_disposition` call catches `gitread.GitError` and any other exception (a hung `git checkout` raises `subprocess.TimeoutExpired`, not `GitError`) so nothing escapes the run loop as a traceback.

Two new tests pin this. `ContinuePastWithoutRepair` (`tests/test_run.py:924-966`) reproduces the bug end to end: run once with `continue_past_task_halt=true` and a task whose gate fails, confirm the task lands `continued_past=True` with its branch still present (`gitread.branch_exists(self.repo, "relay/T-2")` is true), run `relay run` a second time with nothing deleted, and assert the run now halts (`EXIT_HALTED`) with `halt_evidence["resume"] == {"check": "no_task_branch"}` rather than looping to a second silent `continued_past` and exit 0. Only a third run, after `git branch -D relay/T-2`, the same repair `ResumeAfterHalt` already exercises for an ordinary full-stop halt, lands the task. `ContinuePastGuards` (`tests/test_run.py:841-922`) calls `_continue_past` directly across four cases and proves the `no_task_branch` refusal never reaches `resume_disposition`, that a lost lease refuses before the checkout, and that a `GitError` or any other exception raised from inside `resume_disposition` is caught and recorded rather than escaping the run loop.

## Why This Works

`resume_disposition` was deliberately built to answer a general question, "is this repo, independent of any specific task, one the next task's pre-flight would accept" (`gitwrite.py:419-429`), so it could serve any halt class uniformly rather than needing to know which task it was clearing. That generality is correct for its stated job. But `_continue_past`'s decision does not stay general: marking a task `continued_past=True` leaves a specific piece of state behind, the task's own branch, that a later instance of the exact same decision will read through `_one_task`'s pre-flight before `_continue_past` is even reached. A check built to answer the general question has no way to see a blocker that is specific to the particular decision made last time, because that specificity was never part of what it was asked to check.

The failure mode this produces is the sharper problem. `resume_disposition` doesn't answer wrong: the ambient repo state genuinely is fine (tree clean, on default, head matches origin) after a continue-past. It answers a question that is no longer the one that matters, and returns success. The run loop, seeing a passing disposition, does exactly what it's designed to do: mark the record and advance. Nothing raises, nothing is unexpected, so nothing produces an error path an operator would notice. The bug lives entirely inside the "everything worked" branch of the code, repeating on every future run, forever, with a green exit code each time.

## Prevention

The generalizable rule: when a decision creates state that a later instance of the same decision will read, that later read must check specifically for the state the earlier decision just created, not only for general ambient safety. A guard scoped to "is the world okay" cannot substitute for a guard scoped to "is the thing I did last time still in the way." Where a system tracks which prior instance of a repeatable decision has already fired (here: which task was already stepped over, and what it left behind), the next instance of that decision has to consult that specific tracked state first, before falling back to a general check.

The test discipline this implies: a regression test for a repeated or rerun decision has to exercise the unrepaired path, not only the operator-repaired one. `ContinuePastWithoutRepair` is built exactly to that discipline: it runs a second time with nothing cleaned up, because that is the state every real run is actually in. A test that repairs the environment before asserting anything is testing that the repair works, not that the decision is safe without it, and a test with that shape will pass every time regardless of whether the underlying decision has a hole in it.

This is a smaller-scoped version of the lesson in `docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md`, but not quite the same shape, worth being precise about the difference. That doc's defect was a contract split across many independent fulfillment sites (thirty-odd places building halt evidence against one shared template dict), invisible to per-site review because no single site could see the whole set; the fix was a set-level test that iterates the declaration and exercises every entry. This defect is not a set-membership gap: `resume_disposition` and pre-flight's `no_task_branch` check both exist, both are correct on their own terms, and there is no missing row in a table. The gap here is sequential rather than structural: one call's success writes state a later call reads without ever going back through the check that would have caught it. The shared lesson is narrower than "write a set-level test": both defects are invisible to a test that only exercises the freshly-repaired or freshly-declared case, and both were found by tracing execution across more than one call rather than reading a single site in isolation. Where they diverge is what closes the gap. There, it was enumerating a set; here, it is making the second call in a sequence read the first call's leftover state directly, which is a review discipline of "trace this decision across a rerun," not "walk this declaration across its consumers."

## Related Issues

- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` is the closest relative in this repo, though the mechanism differs (concurrency vs. a missing state check) and so does the fix (capture a pgid early vs. add an unconditional refusal). Its generalization is "a guard whose precondition is the same condition that makes the guard unnecessary is untested by construction," and its Prevention section keeps a running, named audit list of Relay code shaped like that: the launcher's group-kill check, `run.py`'s lease-loss checks, `verify.py`'s blocking-skip pattern. `_continue_past`/`resume_disposition` is a fourth instance worth adding to that list in a future refresh — not quite the same precondition-excludes-the-hard-case shape (this guard's precondition set was simply incomplete, not self-defeating), but the same family: a guard that looks correct because the branch that would prove it wrong was never forced.
- `docs/solutions/logic-errors/verify-checked-only-one-direction-of-the-landing-tracker-link.md` is a secondary relative: the same `run.py`/`verify.py` neighborhood, the same pattern of a check built from an incomplete model of the states the repo can actually be in. There the gap was found live (a task landed by hand between runs stayed unlanded forever); here it was found in code review before it reached an operator.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md` is worth citing for a negative result: this bug was not a live-run-only defect. It is a pure control-flow gap, fully reachable and caught by the existing stub suite (`ContinuePastWithoutRepair` needs no real `claude` process), which confirms rather than undercuts that doc's already-narrowed scope (message contracts between the Runner and a real subprocess, and cost/timing bounds a stub can't reproduce — not general control flow).
- `docs/solutions/logic-errors/version-probe-between-lease-acquire-and-try-finally-must-never-raise.md` is the nearest existing doc about lease discipline around a mutating step in `run.py`, relevant only to the secondary finding above (the `git checkout` that previously had no lease guard). The hazard there is different in shape — an exception escaping outside the `try`/`finally` that releases the lease, not a mutation missing a lease check outright — so treat this as a neighbor, not a match.
- `https://github.com/philgutowski/relay/issues/15` is the feature request this fix's code implements. Its own design-pass questions (opt-in per manifest, how resume re-verifies a paused task, composition with halt notifications) did not anticipate the leftover-branch hazard; this doc is the record of the gap the proposal itself did not surface.
