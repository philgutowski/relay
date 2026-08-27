---
title: closing_reference could only be satisfied by the runner's own merge, so a task landed by hand between runs stayed unlanded forever
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [verify, run-loop, gitread, startup-reverify, terminal-card, adapters]
symptoms:
  - "a task record with every other check passing (card closed, new commits on default branch, head equals remote, tree clean) stayed \"not landed\" forever once the operator merged it by hand with a Closes #62 trailer"
  - "startup_reverify re-verified every check as passing and still left the halted record halted, because closing_reference was a blocking skip whenever landing_ref was unset"
  - "run.py's _one_task launched a fresh task process against issue #62 on the tracker on the very next run, wasting a full task run against an issue that was already CLOSED"
  - "landing_ref was only ever written by run.py's own merge step, so closing_reference had no way to recognize a merge it did not itself perform"
  - "skills/relay/SKILL.md documented \"repair by hand, then run again to resume\" as the recovery path for gate_refused, remote_advanced, unclean_exit, and tracker_write_denied halts, but verify.py could never complete that path"
tags: [landing-ref, closing-reference, hand-repair, startup-reverify, terminal-card, bidirectional-link, gitread, live-run]
---

# closing_reference could only be satisfied by the runner's own merge, so a task landed by hand between runs stayed unlanded forever

## Problem

`verify.verify` in `skills/relay/scripts/relay/verify.py` is Relay's own verdict on whether a
task landed (`verify.py:1` to `:31`). Under `SCOPE_FULL` it runs a `closing_reference` check as
one half of the tracker checks (`TRACKER_CHECKS = ("card_terminal", "closing_reference")` at
`verify.py:48`), and the module docstring is explicit that both halves are required, "code that
merged while the card stayed put is a partial landing, not a landing" (`verify.py:225` to
`:226`). Before this fix, that check read a single field:

```python
landing_ref = record.get("landing_ref")
if not landing_ref:
    checks["closing_reference"] = _skip("no landing_ref on the record yet", blocking=True)
else:
    ...  # adapter.closing_reference(task_id, landing_ref)
```

`landing_ref` on a task record was written from exactly one place in the whole codebase, the
runner's own merge step in `run.py`, after a task process finished and the runner itself merged
the branch. `verify.py` never wrote it, and no other module wrote it either. So a task that
landed by any means other than the runner's own merge always found `record.get("landing_ref")`
empty, always fell into the blocking skip branch, and stayed a blocking skip on every future
call to `verify()`, because nothing in the system was ever going to set the field for it.

`skills/relay/SKILL.md:154` to `:155` documents the supported recovery path for a halted task in
plain terms, "Run the same command again. The runner re-verifies every halted record first and
promotes any that now pass, then resumes at the first task that did not land." The per class
remedy table at `SKILL.md:134` to `:141` tells the operator to fix and resume for `gate_refused`,
to rebase or redo the branch by hand and resume for `remote_advanced`, to clean the tree and
resume for `unclean_exit`. Every one of those remedies ends the same way, "resume," which only
works if `startup_reverify` can actually promote a repaired record. `startup_reverify` at
`verify.py:331` to `:350` re-runs the full verdict on every halted record and, before this fix,
had no path by which a task the operator finished by hand could ever pass `closing_reference`,
so it stayed halted regardless of what the operator had actually done.

## Symptoms

2026-08-27, the first live Relay run against a real project, Cratekit, using the github tracker
adapter, on board issue #62, unit U44, "the master command line shell." The unattended task
process backgrounded a long running mutation table driver and ended its own turn to "wait" for
it. In a headless `claude -p` process, ending the turn is the process exiting, so the runner's
process group kill took the driver down mid mutation. The runner correctly halted that task as
`unclean_exit`, that part worked as designed and is a separate defect, a brief template gap, not
this one.

The operator then finished the unit by hand in an attended session on the same branch
(`relay/62`), ran the mutation table properly, and merged to main with a commit whose message
ended in a `Closes #62` trailer, standard git trailer syntax, in the commit body rather than the
subject line. Running `relay run <manifest>` again to resume, `startup_reverify` correctly
re-verified every other check as now passing, tree clean, on the default branch, head equal to
remote, a new commit since baseline, but `closing_reference` stayed a blocking skip because
`landing_ref` had never been set on the halted record and nothing in the code path could set it.
The record stayed "not landed" by verify's own accounting, and `_one_task` in `run.py` launched
task 62 again as a brand new task process, against a github issue that was already CLOSED on the
tracker. That relaunched process happened to behave sanely, it read the issue state, recognized
it was closed, verified the substance was already merged, and ran the gate rather than doing
anything destructive, purely because that particular model run happened to notice the
contradiction. Nothing guaranteed it would.

The defect is not scoped to `unclean_exit`. Relay's own documented recovery contract, repair by
hand then run again, could not be satisfied by the runner's own logic for any halt class, since
every one of them ends in "resume" and resuming runs through the same `closing_reference` check.

## What Didn't Work

**The suite could not catch this because nothing in the fixtures exercised a hand landing.**
Every existing verify test built a record with an explicit `landing_ref` already on it, or left
it absent and asserted the blocking skip as correct behavior, since before this incident the
blocking skip genuinely was correct, `landing_ref` really was always absent until the runner's
own merge wrote it, so a record with no `landing_ref` really did mean "not landed yet." The test
suite agreed with the code because the code's assumption was consistent within the world the
suite constructed. Only a real operator, merging by hand between two runs, produced a record
state the suite had never modeled, every code side check passing, the tracker card genuinely
closed, and still no `landing_ref`.

**`card_terminal` alone was not a fix, because it is only half the tracker check.** The
`card_terminal` check (`verify.py:227` to `:235`) already correctly read the tracker adapter's
`status()` and would have passed for the closed issue. But `_finish` requires both tracker
checks to pass before landing (`tracker_pass = all(...)` at `verify.py:319`), and a tracker card
being terminal is not, by itself, proof that the code that closed it is the code on the default
branch, that is exactly why `closing_reference` exists as a separate check in the first place.
Making `card_terminal` alone sufficient would have reintroduced the partial landing gap the
module docstring warns against.

**The single writer assumption traces to the original design, not to an oversight in this fix
(session history).** The build sessions that landed `verify.py` and `run.py` together as one
unit (U8, 2026-08-25) already carried `landing_ref` as a field on the verify record from the
start, and the plan level deepening pass that preceded implementation decided that the runner's
task process exits before the merge commit exists, so nothing inside a task process can ever
name the commit that closes the tracker. That is the reason a closeout process was introduced to
own every tracker write from a runner composed digest, and it is also, one seam over, the reason
`landing_ref` was designed as something only the runner's own merge step could produce. The
assumption was correct about how the runner's own automated path works. It was never checked
against a human doing the same thing by hand, because nothing before this incident had reason to.

## Solution

Landed in `1c1e408`, merged to main as `238ce91`. Four coordinated changes.

**A helper in `gitread.py` reads full commit messages, not just subjects.** A `Closes #62`
trailer lives in the commit body, and the existing `log_oneline` only returns the one line
subject via `git log --oneline`, which would never see it. `log_messages` at `gitread.py:137` to
`:146` reads full messages using NUL and record separator delimiters so a multi line body
survives intact:

```python
def log_messages(repo, base, head):
    """[(full sha, full message)] for base..head, newest first. The full message because a
    closing trailer such as `Closes #62` lives in the body, not the subject."""
    text = run(repo, ["log", "--format=%H%x00%B%x1e", "%s..%s" % (base, head)]).stdout
    entries = []
    for chunk in text.split("\x1e"):
        sha, _, message = chunk.strip("\n").partition("\x00")
        if sha.strip():
            entries.append((sha.strip(), message.strip()))
    return entries
```

**A task id aware pattern and a lookup function in `verify.py` find a hand landing.**
`_task_pattern` at `verify.py:264` to `:269` builds a strict pattern per task id shape, a numeric
id must appear as `#62` (a hash reference, not a bare number, so an unrelated commit that
happens to mention the number 62 does not match), and a non numeric id such as `T-1` or `PROJ-12`
must appear as a whole word:

```python
def _task_pattern(task_id):
    """`#62` for a numeric id, the id as a whole word otherwise (`T-1`, `PROJ-12`)."""
    escaped = re.escape(str(task_id))
    if str(task_id).isdigit():
        return re.compile(r"(?<![\w/])#%s(?!\d)" % escaped)
    return re.compile(r"(?<![\w-])%s(?![\w-])" % escaped)
```

`hand_landing` at `verify.py:272` to `:284` walks `gitread.log_messages` between the record's
`baseline_sha` and the current head, and returns the newest commit whose full message matches:

```python
def hand_landing(repo, baseline_sha, head_sha, task_id):
    """The newest commit between the baseline and the head whose message names the task, as
    {"sha", "subject"}, or None. This is how a task landed by hand between runs is recognised.
    The whole message is read, because a `Closes #62` trailer sits in the body."""
    pattern = _task_pattern(task_id)
    try:
        entries = gitread.log_messages(repo, baseline_sha, head_sha)
    except gitread.GitError:
        return None
    for sha, message in entries:
        if pattern.search(message):
            return {"sha": sha, "subject": message.splitlines()[0] if message else ""}
    return None
```

**`verify()` itself tries `hand_landing` before falling back to the old blocking skip.** Where
`closing_reference` used to go straight from an absent `landing_ref` to a blocking skip, it now
first checks for a hand landing using the already resolved `local_sha` from the code side checks
earlier in the same function:

```python
    landing_ref = record.get("landing_ref")
    by_hand = None
    if not landing_ref and record.get("baseline_sha") and local_sha:
        by_hand = hand_landing(repo, record["baseline_sha"], local_sha, task_id)
    if by_hand:
        checks["closing_reference"] = _check(PASS, {
            "ref": by_hand["sha"], "subject": by_hand["subject"],
            "derived": "a commit on the default branch since the baseline names the task"})
        record = dict(record, landing_ref=by_hand["sha"])
    elif not landing_ref:
        checks["closing_reference"] = _skip("no landing_ref on the record yet", blocking=True)
    else:
        ...  # unchanged: the existing landing_ref path, calls adapter.closing_reference
```

A record that already carries its own `landing_ref` is untouched by this new path and still runs
the original `adapter.closing_reference` check, so an explicit landing reference is never second
guessed by the new inference.

**`startup_reverify` now persists the derived `landing_ref` on promotion.** Previously, at
promotion time it wrote `status`, `halt_class`, and `verify`, but never `landing_ref`, so even a
verdict that had derived one inside a single call to `verify()` would lose it the moment that
call returned. `verify.py:346` to `:348` now writes it through:

```python
        store.upsert(task_id, status=contracts.STATUS_LANDED, halt_class=contracts.HALT_LANDED,
                     landing_ref=record.get("landing_ref") or verdict.evidence.get("landing_ref"),
                     verify=verdict.as_dict())
```

**`run.py`'s `_one_task` also excludes a task whose tracker card is already terminal**, as a
second line of defense, immediately after reading the baseline and card status
(`run.py:246` to `:257`):

```python
    baseline_sha = gitread.rev_parse(repo, default)
    card_status = adapter.status(task.id)
    if card_status.get("terminal"):
        reason = ("the card already reads %s, which is terminal; nothing to run"
                  % card_status.get("status"))
        store.upsert(task.id, status=contracts.STATUS_EXCLUDED, excluded_reason=reason)
        if stream is not None:
            stream("%s skipped: %s" % (task.id, reason))
        return
```

`startup_reverify` runs once, before the per task loop, at the top of the whole run. So the
ordinary path is that a terminal card gets promoted to landed by `hand_landing` before
`_one_task` is ever reached for that task at all, and this exclusion in `_one_task` is genuinely
the second line of defense, it catches the narrower case where a card reads terminal on the
tracker by some means `hand_landing` cannot construct a `landing_ref` for, for example a card
closed without any commit message naming the task, so promotion never happened, yet the card
itself is undeniably terminal and relaunching against it would still be wrong.

**Tests.** `tests/test_verify.py`, class `LandedByHand`, four cases.
`test_a_commit_on_main_naming_the_task_is_the_closing_reference` lands a commit naming the task
and confirms the record lands with `closing_reference` PASS and `verdict.evidence["landing_ref"]`
set to that commit's sha. `test_a_numeric_id_is_matched_as_a_hash_reference_only` sets the task
id to the bare string `"62"`, lands a commit whose subject is `"feat: 62 tests, no reference"`,
and confirms `closing_reference` is still SKIPPED, a bare number is not a hash reference, then
lands a second commit with an actual `Closes #62` trailer in the body and confirms that one
matches and the record lands. `test_no_commit_naming_the_task_keeps_the_blocking_skip` lands an
unrelated commit and confirms the blocking skip is preserved rather than the check inventing a
landing. `test_a_record_with_its_own_landing_ref_is_not_second_guessed` builds a record that
already carries a `landing_ref`, and confirms the original `adapter.closing_reference` path still
runs and can still FAIL exactly as before, unaffected by the new inference. In the same file,
`StartupReverify.test_a_task_landed_by_hand_is_promoted_and_its_landing_ref_recorded` confirms
the promoted record's `landing_ref` field is actually written to the state store by
`startup_reverify`, not just held transiently inside one `verify()` call.

`tests/test_run.py`, class `TerminalCard`, one case,
`test_a_card_that_is_already_terminal_is_excluded_instead_of_launched`, a tracker card is closed
by editing `tracker.md` directly on main outside the run, and the run is confirmed to exclude
that task rather than launch it, no `relay/T-1` branch is ever created for it, no session id is
recorded for it, and the other task in the same run lands normally and is unaffected.

Full suite, 362 tests passing at the time of this fix, grown from 353 before this session's
changes.

The fix has not yet been exercised against a second live hand landed task the way the original
defect was found, against Cratekit issue #62. A same day run against a throwaway proof target
(`~/Documents/PhilAI/relay-proof/target`) landed three tasks cleanly, but its two previously
halted records had already landed the day before through the ordinary path and were not the
scenario this fix addresses. The unit tests in `LandedByHand` reproduce the exact shape of the
Cratekit incident, a commit bearing a `Closes #N` trailer with no `landing_ref` on the record,
but a second live run through a genuinely hand landed task is still owed.

## Why This Works

A verdict function that can only be satisfied by evidence the system itself produces cannot
recognize the same real world outcome reached by another actor. `verify.closing_reference`
implicitly assumed `landing_ref` always originates from `run.py`'s own merge step, because until
this incident that assumption had never been wrong. But `SKILL.md` documents "repair by hand,
then run again to resume" as the supported recovery path for every halt class, not just the one
this incident happened to hit, and every one of those remedies converges on the same
`startup_reverify` call.

The link between a landing and its tracker card exists in two directions, a tracker comment that
names the commit, the direction `adapter.closing_reference` already checked, and a commit that
names the tracker card, the direction that was missing, now `hand_landing`. A verify function
that reads only one direction will silently fail exactly when a human intervenes between runs,
which is the moment an unattended system most needs to hand off gracefully rather than blindly
repeat work. Reading both directions, rather than only the one the runner's own code happens to
produce, is what closes the gap.

## Prevention

Any check whose only "pass" path is evidence the system's own automated step produces should be
re-examined for whether a human completing the same real world outcome by a different, equally
valid mechanism can also satisfy it, especially in a system that documents "repair by hand and
resume" as a supported path in its own skill file. When a new field like `landing_ref` is
introduced, the question to ask up front is not just "what writes this" but "what else, outside
this codebase, could make the fact this field represents become true," since an unattended
runner's operator is exactly such an outside actor by design.

This is also worth naming as a pattern rather than a one off. It is a second live run only
defect found on the seam between the runner's own merge and write side and its own verify and
read side of "landing," after the five contract defects documented in the stubbed seams doc
linked below. That suggests this specific seam, the runner's own write path for landing state
versus verify's own read path for landing state, deserves standing scrutiny whenever either side
changes, not just live run testing in general.

Note for context, the same commit also amended the brief templates to forbid backgrounding
commands and ending the turn early, since that is what caused the original `unclean_exit` halt
that led the operator to hand repair task 62 in the first place. That is a different root cause,
process lifetime in headless mode, not this verify blocking skip logic, and is not part of what
this fix solved.

## Related Issues

- [docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md](stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md) is the direct precedent for this one: the first live run against a real repository found five contract defects the suite's own fixtures could never have found, because the fixtures and the code were written by the same hand and agreed by construction. This doc is a second live run only finding in the same week, on the closely related seam between the runner's own merge step and verify's own landing check rather than that doc's transcript, closeout, and brief seams. That doc's own Prevention section names two seams a live run had not yet reached, the jira and github `review_step` and the classify digest keys; this fix found a defect on a third seam neither one named, so that section is now an incomplete inventory rather than a wrong one.
- [docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md](process-group-kill-resolves-target-lazily.md) is the same general shape one layer down: a guard whose only tested precondition, here `landing_ref` written by the runner's own merge, was also the only precondition under which the guard could ever pass, so the case it also needed to cover was excluded by construction and untested for the same structural reason.
- [docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md](cause-line-contract-split-degraded-to-placeholders.md) is the same general shape another layer down, a contract split between a declaration and its satisfiers, here `landing_ref`'s only writer being `run.py`'s merge step, with nothing joining that assumption to the field's actual meaning.
- [docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md](../workflow-issues/headless-dontask-blocks-claude-dir-edits.md) is the other doc produced by a live unattended run rather than by the suite, and its closing point, that an unattended runner should assume there are more such gates it has not met, applies here as directly as it did there.
