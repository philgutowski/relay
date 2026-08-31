---
title: In a headless task process ending the turn is exiting, so a backgrounded command is a killed command
date: 2026-08-27
category: workflow-issues
module: runner
last_updated: 2026-08-31
problem_type: workflow_issue
component: runner
severity: high
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [task-process, brief, launcher, timeout-handling]
applies_when:
  - "a task process runs headless under claude -p with no human present"
  - "the task's plan includes a long running command, such as a mutation table driver or a full test sweep"
  - "that command edits source files in place and restores them only when it finishes"
  - "the harness offers to run the command in the background and wait for it later"
  - "an attended session is asked to reproduce a headless task by hand"
symptoms:
  - "the task process commits its code, launches a long driver in the background, and ends the turn saying it is waiting for it"
  - "the CLI treats the ended turn as completion and kills every background task with the process"
  - "the driver dies mid mutation and leaves a two line null control comment in a source file it never restored"
  - "the runner finds the tree dirty after the process exits and halts the task as unclean_exit with the dirty paths as evidence"
  - "an attended session given an explicit foreground instruction still backgrounds the same driver, surviving only because an attended turn end is not an exit"
tags: [headless-claude, background-task, turn-ending, brief-template, unclean-exit, process-group-kill, unattended-run, mutation-testing]
---

# A headless task process backgrounds a long command by default, and ending its turn is exiting

## Context

Relay runs one fresh headless `claude -p` process per task, serially, with nobody watching. The
launcher gives that process its own session (`start_new_session=True` in the `Popen` call at
`skills/relay/scripts/relay/launch.py:223` to `:224`), captures its process group id at launch
(`launch.py:232` to `:235`), and holds a group kill in reserve for the timeout and lease paths
(`_kill_group` at `launch.py:169` to `:197`, invoked from the deadline check at `launch.py:292`
to `:298`). The design assumes the process works until it is done and then exits. What the first
live run showed is that the process can also decide, entirely reasonably by its own lights, to
stop working and wait, and in a headless process waiting does not exist.

On 2026-08-27, the first live Relay run against a real project, Cratekit, picked up GitHub issue
#62, unit U44. The task process planned and built the unit, made three commits with the hook
green, and reached the plan's mutation table step. Cratekit's `scripts/mutate.py` edits source
files in place, runs test slices against each mutation, and restores the files when it finishes.
It takes many minutes. The task process launched it as a background task, then ended its turn
with the message:

> The mutation run is underway. Waiting for the table rather than touching the tree while the
> driver holds it.

In a headless process there is no next turn. The CLI recorded the result as completed, killed
the background tasks it was still carrying, the transcript shows `task_updated` events with
status `killed` for both the mutation driver and a second background wait loop, and the process
exited. Anything that had survived that would have met the runner's own group kill, the
mechanism `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` exists to
keep honest, working exactly as designed here. Either way, nothing backgrounded outlives the
process, which is correct behavior on both sides and is precisely why the process must never
background.

The driver died mid mutation, before its restore step, leaving a two line comment
`# mutation driver null control, restored automatically` appended to
`src/cratekit/modules/tools/mastering.py`. The runner then tried to check out the default
branch, git refused because of the modified file, and the resulting `GitError` became an
`unclean_exit` halt through the handler in `skills/relay/scripts/relay/run.py:161` to `:166`,
which converts any failed git command while handling a task into `HALT_UNCLEAN_EXIT` with the
git stderr as evidence. That halt was correct. It was also the end of the road: 3195 seconds of
active work stranded on branch `relay/62`, three good commits, recoverable, and recovered by
hand.

The confirming observation came the same day. The attended session that finished U44 by hand was
given an explicit written instruction, "Run it in the FOREGROUND and wait for it," and still
launched the driver as a background task, then blocked on it with a wait. It survived only
because an attended session persists across turns. Two sessions, one told nothing and one told
explicitly, both backgrounded the long command. The harness's default for a long running command
leans toward background and yield. That default is right for an attended session, where the user
keeps working while the command runs, and it is exactly the one thing a headless process must
never do, because ending the turn is exiting and there is nobody to hand the next turn to.

## Guidance

**The brief states the rule directly, because nothing else can reach the process before it
decides.** Both templates now carry it under "Rules for the whole session," as the first rule,
at `skills/relay/templates/brief-local-merge.md:19` to `:22` and identically at
`skills/relay/templates/brief-pr-terminal.md:19` to `:22`:

```
Run every command in the foreground and wait for it to finish. Never start a command or an agent
in the background and end your turn to wait for it: in this session, ending your turn is exiting,
and everything still running is killed with you. A mutation driver, a test suite, or a build that
takes twenty minutes is twenty minutes of waiting, not a reason to background it.
```

The shape of the rule matters as much as its presence. A brief that does not say this is relying
on the model to infer a process lifetime rule from context it does not have; the model cannot
see that it is headless, and the opening of the brief ("You are running unattended," line 3 of
both templates) tells it nobody is watching without telling it what a turn boundary costs. So
the rule states the mechanism in the model's own terms, "ending your turn is exiting," names the
concrete shapes that tempt a background launch, a mutation driver, a test suite, a build, and
states the cost plainly: twenty minutes of waiting is the price, not a reason to background. A
rule phrased only as "do not background" loses to the harness's default; the attended session
proved that an explicit foreground instruction alone is not reliably enough, so the brief also
explains why the default is fatal here.

The rule is pinned by the suite so a template rewrite cannot drop it silently.
`tests/test_brief.py:67` asserts both load bearing phrases in both rendered templates:

```python
def test_the_brief_forbids_backgrounding_work_and_ending_the_turn(self):
    """The first Cratekit run: the task backgrounded the mutation driver, ended its turn
    to wait, and exited, and the driver was killed mid mutation."""
    for mode, text in self.each_template():
        self.assertIn("foreground", text, mode)
        self.assertIn("ending your turn is exiting", text, mode)
```

And it was exercised live the same day it was written. T-3 on the relay-proof throwaway target
and T-1 of Relay's own first self run, a 15 minute task that landed at `bbdeba3` with a full
suite run inside it, both completed under the new brief without ending a turn on backgrounded
work.

## Recurrence

The brief rule alone did not hold. Round six, 2026-08-30, task #35 committed its fix, launched
the unittest suite as a background Bash call, and still ended its turn on "Standing by for the
test suite's completion notification." Under `claude -p` no such notification arrives after the
final turn; the process exited with no envelope after 296s, and the runner classified
`unclean_exit` off the task's own untracked plan file, a downstream symptom that named the dirty
tree, not the waiting turn that caused it.

Task #49 (commit `c9043ba`, merged `9076000`, 2026-08-31) closed the gap the brief-only fix left
open: a task can still end on language that reads as waiting even after being told not to
background work, and when it does, the record should say so regardless of which halt class the
run lands in. The fix has two parts. The brief's foreground rule
(`skills/relay/templates/brief-local-merge.md`, `brief-pr-terminal.md`) was sharpened to name
background completion notifications specifically and forbid ending a turn on a promise to
resume. And `skills/relay/scripts/relay/classify.py` gained a `waiting_last_message` finding
(finding-only, KTD6, not a new halt class): whenever the run does not end in a complete envelope,
a regex checks the last message for phrasings like "standing by," "will resume," or "once it
finishes," and attaches the matched text as a finding so the Cause line names the real mechanism
even when a halt like `unclean_exit` fires from unrelated evidence gathered afterward.

The regex itself carries its own lesson. Two independent code-review lenses on task #49 caught
that an earlier draft of the third branch required a noun between the pronoun and the verb, so
it never matched the brief's own quoted cautionary phrase, "once it finishes," which is the
exact wording a real task is most likely to echo back. A regex written to mirror a brief's
language needs a fixture asserting it matches the brief's literal words, not the developer's
paraphrase of them; `tests/fixtures/transcripts/once_it_finishes.jsonl` now pins that case
alongside `outstanding_by.jsonl`, `will_resume.jsonl`, and `will_check_back.jsonl` for the other
phrasings.

The standing takeaway: a brief rule stated once is necessary but not sufficient for a class of
failure the model can still fall into under a different disguise. Treat the brief as the first
line of defense and the classifier finding as the second, and expect a third occurrence to look
different again.

## Why This Matters

Every runner side defense worked. The process got its own session, the CLI killed the
backgrounded driver on exit, the group kill stood behind that, the dirty tree was caught, and
the task halted as `unclean_exit` with real evidence instead of merging a half mutated source
file. The outcome was still an hour of unattended work stranded on a branch and a human
finishing the unit by hand. The runner cannot fix this class from its side, because by the time
it sees anything, the process has already exited; the decision that mattered was made one turn
earlier, inside the process, when it chose background over foreground. Only the brief reaches
the process before it decides.

There is a sharper point underneath: the failure is not a model error. Backgrounding a twenty
minute driver and yielding is the trained, sensible move in an attended session, and the harness
default encourages it. The same behavior in a headless process is fatal for a reason that is
invisible from inside the session. That makes this the second harness gate a live unattended run
found that the suite cannot, after the `dontAsk` gate on `.claude/` paths documented in
`docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`. That doc's closing
line says an unattended runner should assume there are more such gates it has not met, and this
is one of them. The suite could never have found it either way: the stub `claude` in
`tests/stub-claude` never reads the brief and never backgrounds anything, so the producer side
of this contract had never run before the live run.

The halt also carried a real observability cost. The operator watching nothing saw a run stop
with a git error about a file the plan never mentioned, and the transcript's last message said
the work was underway. The local backlog note filed during this run, "A run must never be
invisible" (`docs/backlog.md:1`, a gitignored local note, so it may exist only on this machine),
came out of exactly this experience.

## When to Apply

- Any headless `claude -p` brief, in this repo or anywhere else. The rule is about process
  lifetime, not about Relay.
- Any task whose plan includes a long running step: mutation testing, a full suite, a build, a
  large download. These are the shapes that trigger the background default.
- When writing or reviewing a brief template. The rule belongs under the session wide rules,
  first, before the steps, and the test in `tests/test_brief.py:67` is the pattern for pinning
  it.
- When diagnosing a halt. A task that halted `unclean_exit` with a partially applied in place
  edit, where the transcript's final message says it is waiting for something, is this failure,
  not a model that wandered.

## Examples

### Before, the first Cratekit run

The task process, given no rule about backgrounding, reached the mutation table step of its own
plan and did the normal attended thing. It started `scripts/mutate.py` as a background task,
started a second background loop to poll it, and ended its turn on:

> The mutation run is underway. Waiting for the table rather than touching the tree while the
> driver holds it.

The CLI treated the ended turn as the finished result, killed both background tasks, and exited.
The driver died between mutating `mastering.py` and restoring it, the tree was dirty, the
checkout of the default branch failed, and `run.py:161` to `:166` recorded the `unclean_exit`
halt. Three commits and 3195 active seconds sat on `relay/62` until a human recovered them.

### After, the template rule

The same step under the current brief is covered by `brief-local-merge.md:19` to `:22`: the
mutation driver is named as one of the tempting shapes, the twenty minute wait is named as the
price, and the mechanism is stated in the process's own frame, ending your turn is exiting. The
first two tasks run under the new brief, T-3 on relay-proof and Relay's own T-1 landing at
`bbdeba3`, each carried a long foreground command and completed without leaving a background
task unattended across a turn boundary.

Relay's own T-1 also shows the specific shape the rule produces in practice (session history).
Partway through the task it launched a review subagent in the background, then reversed course
mid turn, "Given the session rule against backgrounding, I'll do the three reviews inline myself
instead of waiting on subagents," and ran the reviews as inline work in the same turn rather than
letting the subagent finish unattended. Later, waiting on the full suite, it kept the turn alive
with a foreground poll loop, checking for an output file to appear, rather than ending the turn
to wait for a completion notification the way an attended session would. That poll loop is the
pattern that survives headless. The pattern of backgrounding work and waiting for a completion
notification works in an attended session, because the harness re-invokes the assistant when a
subagent returns, and it is exactly the one a headless process does not get, because its turn
ending is its exit.

### The diagnostic signature in a transcript

A `result` event with subtype `success` whose text says it is waiting for something, followed by
`task_updated` events with status `killed`. That pairing is this failure exactly: the process
believed it was mid task, the CLI believed it was done, and everything still running was killed
with it.

## Related

- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`: the other
  harness gate found by a live unattended run rather than the suite; its Why This Matters
  section argues an unattended runner should assume there are more such gates, and this is the
  second.
- `docs/solutions/logic-errors/verify-checked-only-one-direction-of-the-landing-tracker-link.md`:
  what the runner did after this halt; its Symptoms section names this backgrounding as the
  separate trigger event, a brief template gap, distinct from the verify defect it documents.
- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md`: the process group
  kill that stands behind the CLI's own cleanup of backgrounded work, working as designed here,
  which is exactly why the process must never background.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`:
  the rule that a live run is the only instrument for seams the stub cannot produce; the stub
  never reads the brief, so no test could have found this before a real process ran.
