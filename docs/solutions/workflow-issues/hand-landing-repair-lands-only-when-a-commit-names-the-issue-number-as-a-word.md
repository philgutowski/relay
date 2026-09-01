---
title: "The hand-landing repair for a tracker-write halt is only recognized when a default-branch commit names #N as a word, so a subject saying task 35 reads as not landed"
date: 2026-08-31
category: workflow-issues
module: runner
problem_type: workflow_issue
component: development_workflow
severity: medium
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [verify, run-loop, gitread, adapters, task-process]
applies_when:
  - "a Relay task completed its code and its gate but blocked or halted at the tracker write, leaving a relay/<id> branch unmerged"
  - "the operator repairs by hand: merge relay/<id> to main, run the gate, push, close the issue, then run relay_cli.py verify to promote the record"
  - "verify reports not landed while every git check passes: card closed, new commits on the default branch since the baseline, head equals remote, tree clean"
  - "the merge or repair commit names the task in prose (task 35, issue 35) rather than as #N"
symptoms:
  - "relay_cli.py verify leaves the record unpromoted after a hand landing that git shows as complete"
  - "the closing_reference check is a blocking skip (no landing_ref on the record yet) with every other landing check green"
  - "adding an empty commit whose message contains Closes #N and re-running verify promotes the record immediately"
tags: [hand-repair, verify, closing-reference, landing-ref, tracker-write-denied, empty-commit, relay-cli, live-run]
---

# The hand-landing repair for a tracker-write halt is only recognized when a default-branch commit names #N as a word, so a subject saying task 35 reads as not landed

## Context

Four times across rounds six and seven, a Relay task finished its actual work and passed the gate,
then never recorded the landing. Tasks 35 and 40 on 2026-08-30, tasks 24 and 26 on 2026-08-31. The
causes differed but the shape was identical: the code on `relay/<id>` was complete and green, and
only the tail of the pipeline failed. Observed causes: the task process was killed along with  the
Runner; a task backgrounded the test suite and exited before it returned; codex's workspace write
sandbox blocked network, so `gh` could not reach api.github.com and the card move failed (issue
#51, fixed 2026-09-01 by
`docs/plans/2026-09-01-1130-fix-codex-sandbox-network-access-plan.md`, which grants the sandbox
network at launch). In
every case the instinctive repair, delete the branch and rerun the task, was the expensive and
wrong one. The cheap repair is to land the branch by hand and let `verify` recognize it.

The recognition machinery itself dates to the first Cratekit run on 2026-08-27 (session history):
a hand merged `Closes #62` was never promoted because `closing_reference` was a blocking skip
whenever the record had no `landing_ref`, and only the runner's own merge ever wrote one;  the
resume then relaunched a task whose card was already closed. The fix taught `verify` to derive a
landing from the commit graph, and
`docs/solutions/logic-errors/verify-checked-only-one-direction-of-the-landing-tracker-link.md`
documents it. This doc is the operator side procedure built on that mechanism, and the naming trap
inside it.

## Guidance

The seven step repair, run from the target repo:

1. Merge the task branch to `main` by hand. If the task left an untracked plan file (a
   `docs/plans/` file it wrote but never committed), commit that on the branch first so nothing is
   stranded. Then:

   ```
   git checkout main
   git merge --no-ff relay/<id>
   ```

2. Run the gate:

   ```
   python3 -m unittest discover -s tests
   ```

3. Push. The pre push hook runs the suite again, which is fine, that is the point of the hook.

4. Add an empty marker commit whose message contains `Closes #N`, where N is the issue number.
   This is the commit `verify` will find. The form used all four times:

   ```
   git commit --allow-empty -m "chore: land relay task <id> by hand" -m "relay/<id> merged at <sha> after <one line cause>. Closes #<id>"
   ```

   Push it.

5. Close the issue with an evidence comment naming the merge commit and the gate result.

6. Confirm the record is recognized:

   ```
   python3 skills/relay/scripts/relay_cli.py verify <manifest> <id>
   ```

   It must print `landed` for the task.

7. Delete the branch.

The next `run` of the manifest promotes the record at its startup re verify, so nothing else needs
touching in state.

## The trap: what verify actually matches

`verify` recognizes a hand landing by scanning commit messages between the record's baseline and
the current head for one that names the task. The scan lives in `hand_landing`
(`skills/relay/scripts/relay/verify.py:272`), attempted only when the record has no `landing_ref`
yet (`verify.py:239`). The pattern comes from `_task_pattern` (`verify.py:264`). For a numeric id
it is `#N` as a word:

```
(?<![\w/])#%s(?!\d)
```

So the message must contain the literal `#35`, not the bare number. A merge subject like
`Merge relay task 35 from relay/35` does NOT match, there is no `#`. The observed symptom is
confusing to a reader who does not know this: `verify` prints every git check passing, then
`35: not landed`, with `closing_reference` skipped as `no landing_ref on the record yet` ( the
blocking skip at `verify.py:251`). The merge is real, the suite is green, and the record still
cannot promote, because no commit names the task in the shape the regex wants. The whole message
is read, not just the subject, so a `Closes #N` trailer in the body is enough. That is why step 4
exists and why its message form matters.

Non numeric ids (`T-1`, `PROJ-12`) match as a whole word without the `#`, per the second branch of
`_task_pattern`.

## Why This Matters

Rerunning a completed task costs 25 or more minutes and real dollars per task, and when  the
failure was external (a killed runner, or the sandboxed `gh` of issue #51 before 2026-09-01) the
rerun can hit  the exact same wall and burn the same money for the same halt. The hand landing takes minutes, and
because `verify` derives the landing from the commit graph rather than trusting the operator,  the
record stays honest: promotion happens only after the same checks a normal landing passes, plus a
commit that provably names the task.

The detection also depends on `verify` seeing the remote as it is: the run loop's final full scope
verify fetches before reading (Relay task T-5, 2026-08-27), because a hand landing happens on  the
remote between runs and stale tracking refs would hide it (session history).

## When to Apply

Only when the work is genuinely done and only the recording failed. Concretely: the `relay/<id>`
branch holds the complete change, the gate passes on the merged result, and the failure was  the
tracker write or the runner's own death. Do NOT hand land a task whose work is unfinished, that is
a rerun, not a repair.

Note the interaction with `blocked`: blocked is a deliberate outcome, so `--retry-blocked` refuses
to relaunch a task while a stranded `relay/<id>` branch still carries commits. That refusal is a
signal pointing at exactly this repair, land the branch by hand or discard it, do not fight  the
flag.

## Examples

Task 35, before and after, is the whole trap in two commits:

- Merge `dbff80d`, subject `Merge relay task 35 from relay/35`, landed the code. `verify` said
  `35: not landed`. No `#35` anywhere in the message.
- Empty commit `34c2207`, subject `chore: land relay task 35 by hand`, body `relay/35 merged at
  dbff80d after the task process exited waiting on a background suite. Closes #35`. `verify` then
  promoted it.

Task 40 repeated the same pair (`d50d3f6` then `911c834`) the same day. On 2026-08-31, tasks 24
and 26 skipped the failed first attempt entirely: the marker commits `49a2f03` and `b7d8295` were
written as step 4 of the repair from the start, and `verify` passed first try. All six commits are
on `main`.

## Related

- `docs/solutions/logic-errors/verify-checked-only-one-direction-of-the-landing-tracker-link.md`,
  the code side ancestor. Its Solution noted a live run through a genuinely hand landed task was
  still owed; the four repairs above are that exercise, so the debt is paid.
- `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`,
  the earlier one shape repair (move the card, run again) this procedure generalizes.
- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`, the other operator
  finishes what the runner could not workflow, triggered by a permission gate instead.
- Issue #51, the codex sandbox network fence, the standing producer of this repair until it was
  removed on 2026-09-01 by
  `docs/plans/2026-09-01-1130-fix-codex-sandbox-network-access-plan.md`. The repair itself stays
  live: the Context above names three other producers, and issue #60 is the control the fence's
  removal now needs.
