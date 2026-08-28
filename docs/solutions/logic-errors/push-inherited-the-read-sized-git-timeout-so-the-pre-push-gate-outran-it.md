---
title: The push inherited a read sized git timeout, so every task the run landed brought the pre-push gate closer to outrunning it
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [gitwrite, gitread, contracts, run-loop, external-gate, state-store]
symptoms:
  - "the runner halts with unexpected_error and a TimeoutExpired on `git push origin main` after 120 seconds"
  - "the same suite passed as the runner's own gate minutes earlier, under a 30 minute bound, and the gate log says OK"
  - "the task's merge is on local main but not on origin, and its tracker card never moved"
  - "state.json's git_ops holds a push entry with phase intent and no matching result"
  - "earlier tasks in the same run pushed cleanly, and only the later ones fail"
tags: [subprocess-timeout, pre-push-hook, external-gate, derived-bound, shared-wrapper-default, self-limiting-failure, git-ops-intent-result, first-live-run]
---

# The push inherited a read sized git timeout, so every task the run landed brought the pre-push gate closer to outrunning it

## Problem

Relay is an unattended outer loop. A long lived Runner process reads a Manifest, launches one
fresh headless Task process per Task serially, then runs the project's External gate, merges,
and pushes each landing itself. Every git command in the package goes through one wrapper,
`gitread.run` at `skills/relay/scripts/relay/gitread.py:23`, whose default `timeout` is
`GIT_TIMEOUT_SECONDS = 120` at `gitread.py:10`. That default applied to `push` as well as to
`rev-parse` and `fetch`. A push runs the repository's `pre-push` hook in process before any
transfer, and this repository's hook runs the full suite, so once the suite grew past two
minutes the Runner killed its own push, halted the run with Halt class `unexpected_error`, and
left the Task's merge on local `main` and nowhere else.

## Symptoms

Round three of Relay on Relay, from the Runner's own readout, in the order the operator met it. The
run's state directory and its logs live outside the repository and are gitignored, so the timings
and the `git_ops` shape below come from that readout and are not re-derivable from the tree. The
mechanism behind each is verifiable in the tree, and the sections after this one cite it.

- **T-4 landed and pushed. T-5 halted on its push.** The Cause line was rendered from
  `contracts.py:236`, the `unexpected_error` template
  `"the runner hit an unexpected {error_type} on {task}: {error}"`, and read: the runner hit an
  unexpected TimeoutExpired on T-5: Command `['git', '-C', '/Users/pgutowski/Documents/PhilAI/relay',
  'push', 'origin', 'main']` timed out after 120 seconds.

- **The gate log for the same Task said OK.** `local_merge_tail` runs the External gate at
  `gitwrite.py:339` under `gate_timeout_seconds`, which defaults to
  `contracts.DEFAULT_GATE_TIMEOUT_MINUTES * 60` at `gitwrite.py:322` to `:323`, thirty minutes
  (`contracts.py:271`). The gate passed. Minutes later the push at `gitwrite.py:378` ran the
  identical command inside the hook and was killed at 120 seconds.

- **The failure was progressive, not random.** The suite was about 109 seconds at the run's
  baseline commit `95c99ea`, and about 216 seconds once T-4 and T-5 had added their own tests.
  T-4's push fit inside 120 seconds. T-5's did not. Nothing about T-5's change was implicated.

- **The state left behind was the worst kind of half landing.** T-5's merge commit `c77c18c`
  was on local `main` and not on the remote, and its Tracker card never moved, because the
  Closeout process runs after the push. The code was landed locally and invisible remotely.

- **`state.json`'s `git_ops` named the exact moment.** The array held a `push` entry with phase
  `intent` and no matching `result`.

## What Didn't Work

This session went straight to the cause; the TimeoutExpired named the command and the bound in
one line. The honest content of this section is the reasoning that made the cause look
impossible for the first few minutes, because that reasoning is what a future session will
repeat.

**The same suite had just passed, so "the suite is too slow" read as already disproved.** The
External gate is not a different check. `local_merge_tail` runs the Manifest's gate argument
list at `gitwrite.py:339`, and this repository's `.git/hooks/pre-push` runs
`python3 -m unittest discover -s tests`, which is that same argument list. One suite, run twice,
minutes apart, in one Runner. The first run was bounded at thirty minutes and passed. The second
was bounded at two minutes and was killed. Only the first bound had ever been chosen
deliberately; the second was inherited from a read wrapper and nobody had ever decided it
applied to a push. A timeout on a command that had just succeeded looks like a lie until you
notice there were two different bounds on the two runs.

**A reviewer aimed at exactly this file raised it, and the validation gate overruled them
(session history).** The eight reviewer `ce-code-review` of units U4 to U11 on 2026-08-25 included a
reliability lens, dispatched with the stated scope "timeouts, process-group kills, CI polling, lease
heartbeat". One of its findings claimed `gitread.run` had no hang protection. The independent
validation gate rejected it, and the session recorded the rejection verbatim: the validator
confirmed it already passes a 120 second timeout and that the file predates this diff.

Both halves of that rejection are the mechanism, and neither is careless. The presence of a timeout
was accepted as the answer to the question of whether the timeout was right, which are different
questions. And "the file predates this diff" is how a diff scoped review is supposed to work, so the
constant introduced for reads in U1 was never re-examined once U8 stacked mutating commands onto it.
The reviewer was pointed at the correct file, asked a version of the correct question, and was
correctly overruled under the rules in force.

**The number was measured against the bound once, and the headroom was never named as headroom
(session history).** When the untracked `pre-push` hook was created on 2026-08-27 it was measured on
a real push: 362 tests, 89 seconds, green. That is 31 seconds inside a 120 second bound nobody in
that session was considering. The same session five hours earlier had estimated the suite at "about
two and a half minutes", the figure `CLAUDE.md` still carries, which is well over the bound. Two
irreconcilable numbers sat in one session and neither was ever checked against the 120 seconds that
would decide the matter.

**The growth was tracked the whole way and read as the wrong signal (session history).** The suite
count is recorded climbing through the window: 83 after the foundation, 128 after U8, 282 at U10,
298 after simplify, 323 after the review fixes, 353 before the Cratekit findings pass, 362 when the
hook was installed, 364 after round one, 376 after round two. Every increment was reported as a
health signal. None was read as a countdown against a fixed subprocess bound a push would have to
clear. The one review finding that came closest, a P0 on the lease TTL, contains the phrase "while
the first is still pushing" in its own sentence, so a push was being modelled as a slow operation
right there, and the reasoning stopped at the lease.

**"Push is network, and 120 seconds is generous for network" is true and irrelevant.** The
number is fine for what the wrapper was written for. `gitread` is the read only module
(`gitread.py:1`), and for `rev-parse` or `fetch` a command still running at two minutes is
genuinely hung. The flaw is not the value. It is that a push is not only network, and it was
sharing a default with commands that are.

**The hook is untracked, so it is invisible to the code.** `.git/hooks/pre-push` is deliberately
not tracked, so a commit inside the repository cannot disable its own gate. That is the right
call and it has a cost: nothing in the working tree, and nothing in a diff, tells a reader that
`git push` in this repository runs a multi minute suite. The work a push does is defined outside
everything the Runner can see.

## Solution

Commit `e7da811`, reachable from `origin/main`, verified with `git merge-base --is-ancestor`.

**A derived bound, not a second hardcoded number.** New helper at `gitwrite.py:69` to `:75`:

```python
def push_timeout_for(gate_timeout_seconds=None):
    """The bound for a push. A repository may run its gate inside a pre-push hook, so a push can
    legitimately take as long as the gate plus the transfer. gitread's read timeout is far too
    short for that, and applying it here kills the runner's own push mid-hook."""
    if gate_timeout_seconds is None:
        gate_timeout_seconds = contracts.DEFAULT_GATE_TIMEOUT_MINUTES * 60
    return gate_timeout_seconds + contracts.PUSH_NETWORK_MARGIN_SECONDS
```

The margin is the one new constant, at `contracts.py:277`, carrying its reason in the comment
above it at `contracts.py:273` to `:276`:

```python
# A push can run the project's gate inside a pre-push hook, so it is bounded by the gate's own
# timeout plus the network transfer, never by gitread's read timeout. See
# docs/solutions/ for why: a 120 second read bound killed a push whose hook was running a 216
# second suite, and the runner reported it as an unexpected error.
PUSH_NETWORK_MARGIN_SECONDS = 120
```

**The wrapper forwards a timeout only when one is set.** `_mutate` at `gitwrite.py:78` gained an
optional `timeout`, and the branch at `:81` to `:84` is what keeps the other callers untouched:

```python
def _mutate(repo, op, args, ops=None, task_id=None, env=None, check=False, timeout=None):
    """Run one mutating git command between an intent entry and a result entry."""
    _record(ops, task_id, op, "intent", {"args": list(args)})
    if timeout is None:
        proc = gitread.run(repo, args, check=check, env=env)
    else:
        proc = gitread.run(repo, args, check=check, env=env, timeout=timeout)
```

There are eight `_mutate` call sites in `gitwrite.py`, at `:163` (fetch), `:167` (checkout),
`:173` (merge), `:182` (merge_abort), `:194` (push), `:202` (mirror_push), `:227`
(delete_branch), and `:231` (reset_hard). Two of them pass a timeout. The other six keep the
read default, deliberately.

**Both push wrappers take a bound that means the gate bound.** At `gitwrite.py:193` and `:200`:

```python
def push(repo, args, ops=None, task_id=None, env=None, timeout=None):
    proc = _mutate(repo, "push", ["push"] + list(args), ops, task_id, env,
                   timeout=push_timeout_for(timeout))
```

The argument is handed to `push_timeout_for`, never to `subprocess` directly. `mirror_push` at
`:200` to `:203` does the same.

**The callers pass the bound they already had.** `local_merge_tail` passes its own
`gate_timeout_seconds` at `gitwrite.py:378` to `:379`. In `run.py` the mirror push passes
`ctx.overrides.get("gate_seconds")` at `:440` to `:442`, and the closeout push does the same at
`:540` to `:542`, the same override already feeding the gate at `run.py:411`.

`GIT_TIMEOUT_SECONDS` at `gitread.py:10` is unchanged, so a genuinely hung fetch still trips at
120 seconds.

## Why This Works

The design flaw was not the number 120. It was one shared subprocess wrapper carrying one
default bound for commands that do categorically different amounts of work. `gitread.run` is
correct as a read wrapper: for `status`, `rev-parse`, `merge-base`, or `fetch`, two minutes of
silence means hung. When the mutating wrappers were split into `gitwrite.py` they kept calling
that same function, and inherited its default without anyone deciding that a push belonged in the
same class. A default that is right for its original callers becomes a silent policy for every
caller added later, and the newer caller is the one that violates the assumption.

What separates a push from every other command in the module is that its duration is set by code
the Runner does not own and cannot see. `git push` runs the repository's `pre-push` hook in
process, before the transfer, and that hook is arbitrary user supplied work with no bound of its
own. A bound must be sized for what the command actually does, and a command that runs user
supplied hooks does unbounded work. The only bound in the system that was ever sized for that
work is the gate timeout, because the gate is that work.

Deriving the push bound from the gate bound rather than hardcoding a bigger number is what makes
the fix hold. Two independent constants would drift: an operator who raises `gate_seconds` for a
slow project raises the hook's own budget and leaves the push bound where it was, reintroducing
this exact halt one manifest later, and the second failure would look nothing like a timeout
policy problem. Because `push_timeout_for` reads the gate bound and adds only a transfer margin,
raising the gate raises the push with it, and the margin stays the small honest number it is:
the network part, on top of the gate part.

The failure mode is self limiting in a nasty direction. Relay's whole purpose is landing tasks,
and in a repository whose gate is its test suite, every Task a run lands makes the suite slower.
A run therefore walks toward this cliff with each success, and the first Task to cross it is
whichever one happens to sit just past the boundary. Nothing about that Task caused it. That is
also why it survived to a live run at all: the failure needs a real suite and real accumulation,
and it strikes late in a batch rather than at the start.

## Prevention

**Read `git_ops` first when a Runner dies mid git operation.** The `gitwrite` module docstring at
`gitwrite.py:4` to `:8` promises exactly the marker this defect leaves:

> Every mutating wrapper takes an `ops` recorder (the state store, or anything with the same
> `record_git_op(task_id, op, phase, detail)` method) and writes one `intent` entry before the
> call and one `result` entry after, so a crash between them is a named state rather than a
> mystery (R55, the plan's System-Wide Impact note).

`state.record_git_op` at `state.py:387` to `:392` is the implementation. An `intent` entry with
no matching `result` for the same op localises the death to inside one subprocess call, and names
which one, before any log reading. Here it said `push`, which is what turns "the Runner crashed"
into "the push never returned" in one glance.

**The test that pins the meaning of the argument** is `PushTimeout` at
`tests/test_gitwrite.py:321`, three tests, using the `pre_push` hook text at `:327` that
`TailBase.setUp` installs at `:43` through `_repo.make_repo`, which writes it executable to
`.git/hooks/pre-push` at `tests/_repo.py:51` to `:55`.

- `:329`, the arithmetic. `push_timeout_for(600)` equals `600 + PUSH_NETWORK_MARGIN_SECONDS`,
  the no argument form equals `DEFAULT_GATE_TIMEOUT_MINUTES * 60` plus the margin, and the result
  is greater than `gitread.GIT_TIMEOUT_SECONDS`.
- `:337`, the deliberate negative. It calls `gitread.run(repo, ["push", "origin", "main:probe"],
  timeout=0.5)` against the same slow hook and asserts `subprocess.TimeoutExpired`. This holds
  the defect itself in place: it proves the read bound really does kill a push whose hook is
  slower than it, so the reason for the whole mechanism cannot quietly stop being true.
- `:342`, the load bearing one. It installs a hook that sleeps one second, calls
  `push(..., timeout=1)`, and asserts the push **lands**, checking `origin/main` moved to the new
  head. If a later refactor forwards that argument straight to `subprocess` as the raw bound, one
  second is not enough for a one second hook plus a transfer, and this test fails. The argument's
  meaning, the gate bound and not the subprocess bound, is pinned by behaviour rather than by the
  comment above it.

**Verified live, not only by the suite.** After the fix the run was resumed and pushed T-6
(`85d3efe`) and T-7 (`b6ec289`) successfully, running the same untracked hook that had killed
T-5's push. The suite is green at 452 tests at head. The live confirmation matters here for the
same reason as in
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`:
this defect could not have been found by the suite as it stood, because the suite's fixtures
create repositories with no hooks, so a push in a test finishes in milliseconds. There was no
seam between a producer and a consumer to disagree; there was an environmental fact, a hook that
runs a real suite, that the test environment could not produce until a test deliberately created
one. That is why the fix ships with a hook installing test rather than only an arithmetic
assertion.

**The checks to carry forward.**

- When adding a caller to a shared subprocess wrapper, ask what bounds the new command's work,
  not whether the existing default looks reasonable. If the answer involves code outside the
  repository's own control, hooks, CI, a build tool's plugins, a linked script, the default is
  wrong by construction.
- Prefer a derived bound to a second constant whenever the new bound has to track an existing
  one. Two numbers that must move together and live in different places will diverge, and the
  divergence surfaces as an unrelated looking failure much later.
- Treat a failure whose likelihood grows with a run's own success as a priority. Any bound sized
  against something the run makes bigger, a suite, a repository, a log, a state file, is a cliff
  the pipeline walks toward. Relay's other time bounds deserve the same question:
  `DEFAULT_TASK_TIMEOUT_MINUTES` at `contracts.py:266` and `DEFAULT_CLOSEOUT_TIMEOUT_MINUTES` at
  `:268` both bound processes that read a repository that grows with every landing.
- After a live halt, check the tracker as well as git. This one left the merge on local `main`
  with no Tracker card moved, because the Closeout process runs after the push. Verify-landed is
  the verdict, and it reads both, so an operator repairing by hand has to restore both.

## Related

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the direct ancestor, and this defect is the same lesson arriving from a different direction.
  That one is about contracts between two processes where both halves were written by the same
  hands, so they agreed by construction. This one is not a contract at all: it is an
  environmental fact, an untracked hook running a real suite, that the stub environment simply
  does not contain. Both close the same way: the suite proved nothing about the case, and only a
  live run against a real target could.
- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` is the neighbouring
  timeout defect, in `launch.py` rather than `gitwrite.py`. Read them together as a pair about
  bounds. That one is a guard whose precondition excluded the case it existed for, so the kill
  never ran when it mattered. This one is a bound that ran exactly as written, on a command whose
  work it was never sized for. A green suite survived both.
- `CONCEPTS.md`'s **External gate** entry defines the project's own mechanism that refuses a broken
  change, naming a pre-push hook and continuous integration as forms of it. Relay verifies such a
  gate exists rather than providing it, which is precisely why the Runner has to assume a push can
  run one and budget for it. That entry was refined alongside this learning, because the two forms
  it names are not interchangeable to the Runner: a pre-push hook spends the gate's entire runtime
  inside the Runner's own push subprocess, while continuous integration runs after the push returns
  and costs the Runner nothing. That distinction is the one this defect turned on, and it was
  missing from the vocabulary the whole repository works from.
