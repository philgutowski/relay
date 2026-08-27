---
title: Stubbed producers and stubbed consumers agree by construction, so the first live run found five contract defects the suite never could
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [classify, closeout, summary, adapters, run-loop, contracts, fixtures, task-brief]
symptoms:
  - "a blocked task's record read \"no blocker text in the envelope\" while the transcript carried a paragraph under blockers:"
  - "every closeout classified unfinished because closeout.parse read the first 200 characters of the last message and the terminal line was past the cut"
  - "T-1 blocked with the code done and the gate green because the brief told a markdown tracked task to move a card to in review"
  - "the summary said the tree was dirty on relay/T-1 about a clean tree, the halt message having gone to stdout while the record kept only the class"
  - "the summary printed landed at <sha> twice for one landed task"
tags: [stub-cli, fixture-fidelity, contract-seam, envelope-parsing, last-message-truncation, tracker-adapter, halt-message, first-live-run]
---

# Stubbed producers and stubbed consumers agree by construction, so the first live run found five contract defects the suite never could

## Problem

Relay is an unattended outer loop. A Python runner launches one headless `claude -p` Task
process per tracker task, classifies its transcript (`skills/relay/scripts/relay/classify.py`),
launches a second Closeout process (`closeout.py`), verifies landed state (`verify.py`), and
prints a summary (`summary.py`). Every one of those handoffs is a contract between two processes
or two modules: the envelope the task prints and the parser that reads it, the terminal line the
closeout prints and the parser that matches it, the instruction the brief gives and the adapter
that has to make it possible, the halt the loop raises and the record the summary reads.

Until 2026-08-26 Relay had run only against a stub CLI (`tests/stub-claude`) and hand written
fixture transcripts (`tests/fixtures/transcripts/_make.py`). The first live run against a real
repository, a throwaway markdown tracked repo with a local bare origin and two one function
tasks, found five defects. All five sit at those contracts. None is in logic the stub exercises.
The fixtures and the parsers were written by the same hands in the same sessions, so on every
seam the two halves agreed by construction, and 353 tests stayed green while a real process
disagreed with all of them.

## Symptoms

The run's own readout, in the order the operator met it.

- **T-1 blocked with the code done and the gate green.** The brief's step 7 said "Move the
  tracker card to `in review` and comment the head commit." Under the markdown adapter the
  tracker is `tasks.md` read at `origin/<default>`, and a line is open or closed and nothing
  else (`skills/relay/scripts/relay/adapters/markdown.py:18` to `:25`, `:97` to `:98`). There
  was no card to move. T-2, given the same instruction, edited `tasks.md` on its own branch
  instead, and that edit rode into the merge.
- **The record could not say why T-1 blocked.** The task wrote `blockers:` and a paragraph
  under it. The parser accepted only an inline value or `- item` lines, so `blockers` was
  `[]`, the blocked route filled the evidence with `"no blocker text in the envelope"`
  (`run.py:484`), the closeout brief built its blockers bullets from the same empty list
  (`closeout.py:173`), and the tracker comment the closeout wrote said the cause of the block
  was not visible in the task data. The text sat one line below the key.
- **Every closeout read `unfinished`.** The closeout explained its skip in more than 300
  characters and then printed `Documentation skipped`. `closeout.parse` takes the last non
  empty line and matches it against `contracts.COMPOUND_COMPLETE_LINE` and
  `COMPOUND_SKIPPED_LINE` (`closeout.py:203` to `:216`). It was handed
  `digest["last_message"]`, which classify sets to the first `LAST_MESSAGE_CHARS` (200)
  characters (`classify.py:22`, `:250`). The terminal line was past the head. The summary
  told the operator to confirm the tracker write by hand, for a closeout that had finished.
- **A refused retry read as a dirty tree.** `run --retry-blocked` refused under R48 because
  `relay/T-1` carried commits past the baseline (`run.py:345` to `:347`). That refusal is
  raised as `_Halt` with class `unclean_exit` and a message that says exactly what happened.
  The loop stored only `halt_class` and `halt_evidence` and printed the message to stdout, so
  the summary rendered the class template, `left the tree dirty on {branch}`
  (`contracts.py:230`), about a tree that was clean.
- **`landed at <sha>` printed twice** for every landed task, once from the cause template
  `landed at {ref}` and once from the `landing_ref` line beneath it.

Nothing raised. The run completed, the summary printed, the suite was green.

## What Didn't Work

**353 green tests.** They could not fail, because every fixture on every one of these seams
was written by the person who wrote the parser, to the parser's expectation. Both closeout
fixtures were under 200 characters (`closeout_complete` at 198, `closeout_skipped` at 181), so
head and tail were the same string and `last_message` was enough. Every envelope fixture wrote
its blockers as `- item` lines. The stub never blocked on a brief instruction because the stub
does not read the brief. There is no fixture that a real process wrote.

**The review that had just closed on the markdown adapter.** Finding 20 in
`docs/ideation/2026-08-25-relay-review-residuals.md`, applied the same day in `7fc29f4`,
decided that under markdown the no envelope route is unavailable and `validate` warns. That
decision was about what the runner does when the task says nothing. It never asked what the
brief tells the task to do, so the brief kept instructing an impossible tracker write, and the
first real task did the only two things a process can do with an impossible instruction: block
on it, or improvise around it.

**The build order was chosen around what the stub could exercise (session history).** The
2026-08-25 build sessions deliberately built the verify tail against a hand written fake
adapter first, judging that better than building the run loop "against three invented seams",
and generated every closeout transcript from `tests/fixtures/transcripts/_make.py` rather than
capturing one. The 2026-08-26 handoff stated plainly that the runner had never run against a
real repository. So the gap was known as a fact about coverage and not yet as a class of
defect; the same sessions had already met it once without naming it, when the process group
kill in `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` turned out
to work only on the path that did not need it, because the stub only ever produced that path.

**Reading the summary and the record.** Both said something confident and wrong. The blocker
line said the envelope had no blocker text; it did. The cause line said the tree was dirty; it
was not. The closeout status said unfinished; it had finished. Each derived field had replaced
the raw evidence it was derived from, and once the derivation was wrong there was nothing left
in the record to contradict it.

## Solution

Landed in `86d05d5`, merged as `83ce954`, and `1db05ca`, merged as `f23ae44`. Both merges are
on `main` and pushed to origin.

**A paragraph under a list key is one item per line.** `classify._list_after` at
`classify.py:100` to `:124` now collects plain lines and stops at the next `key:` line, using
`KEY_LINE_RE` at `classify.py:97`. Before, the final branch was a bare `else: break`, so the
first prose line ended the list.

```python
KEY_LINE_RE = re.compile(r"^[ \t]*(?:[-*]\s*)?[`*]*[A-Za-z_]+[`*]*\s*:")
...
        elif stripped == "":
            if items:
                break
        elif KEY_LINE_RE.match(line):
            break
        else:
            items.append(stripped.strip("`"))
```

Pinned by `tests/test_classify.py:211`, `ParagraphBlockers`, whose first case is the live
envelope verbatim, and whose third case keeps an empty `blockers:` followed by another key
empty.

**Carry the tail beside the head.** `classify.py:255` adds `last_message_tail`, the last 200
characters, next to the existing `last_message` head. `closeout.run` at `closeout.py:256` now
parses the tail:

```python
result = RESULT_UNFINISHED if launch_result.timed_out else parse(closeout_digest.get("last_message_tail"))
```

The fixture that proves it is `tests/fixtures/transcripts/closeout_skipped_long.jsonl`, cut
from the live closeout's wording and built by `CLOSEOUT_SKIPPED_LONG_TEXT` at
`tests/fixtures/transcripts/_make.py:236` to `:242`, which the comment above it says is longer
than `LAST_MESSAGE_CHARS` on purpose. `tests/test_closeout.py:240`,
`test_a_long_closeout_message_still_reads_its_terminal_line`, runs it through the real path.

**Resolve the tracker steps by adapter.** `adapters.task_tracker_steps(manifest, branch)` at
`skills/relay/scripts/relay/adapters/__init__.py:76` to `:106` returns a `review_step` and a `blocked_step` by adapter
name, without building the adapter, so a brief renders without a credential. Markdown gets:

```python
"review_step": ("There is no tracker write for you to make. This project's tracker is `%s` "
                "in the repository, which the runner's own closeout process edits after "
                "you exit. Do not edit `%s` yourself; confirm the head of `%s` is "
                "committed and go to the next step." % (path, path, branch)),
```

Jira and GitHub keep the card move. `brief.values` at `brief.py:105` to `:112` passes both as
`tracker_review_step` and `tracker_blocked_step`, and
`skills/relay/templates/brief-local-merge.md:45` and `:50` use them where the fixed sentences
were. `tests/test_brief.py:232`, `TrackerStepsPerAdapter`, renders the three example manifests
and asserts each brief against its adapter's real capabilities.

**Write the halt message down.** `run.py:182` to `:184`:

```python
store.upsert(halt.task_id, status=contracts.STATUS_HALTED,
             halt_class=halt.halt_class, halt_evidence=halt.evidence,
             halt_message=halt.message)
```

`summary._task_entry` carries it at `summary.py:82`, and `summary.lines` prints it under the
cause line when the two differ, `summary.py:194` to `:195`. The same function skips the
`landing_ref` line when the class is `landed`, `summary.py:200` to `:201`, since the cause
template already names the ref. `tests/test_summary.py:269`, `LinesFromTheFirstLiveRun`, pins
both with the live refusal message as its input.

After the fixes, `run --retry-blocked` landed T-1 with closeout `skipped` and no tracker edit
on the branch, T-2 landed, and the run completed with exit 0.

## Why This Works

Each fix is small and none of them is the learning. The learning is why five of them were
waiting for the first real process.

A test that stubs both the producer and the consumer of a contract does not test the contract.
It tests that the fixture matches the parser, and the fixture was written to match the parser,
so it proves nothing about what a real producer will emit. That is true of every seam here.
The envelope grammar was whatever `_list_after` accepted, and every fixture used that grammar.
The closeout terminal line was found at the end of `last_message`, and every fixture was short
enough that the end of the message was inside the head. The brief's step 7 was a sentence the
stub never read. The halt path stored what the summary rendered and rendered what it stored,
and no test asked whether the sentence the raiser wrote survived the round trip. In every case
the producer side of the test was a copy of the consumer's expectation, which is the same shape
as the cause line defect in
`docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md`: a
declaration and its satisfiers in different places, with nothing that joins them. There the
declaration was a template table and the satisfiers were raisers. Here the declaration is a
parser and the satisfier is a process that has never run.

The three fixes that generalise each remove one way the seam can stay untested.

- **Cut fixtures from real output.** `closeout_skipped_long` is the first fixture in the
  suite that a real process wrote. It exists because a real process disagreed with the parser,
  and it stays because the parser will be changed again.
- **Assert the instruction against the capability.** `TrackerStepsPerAdapter` renders the
  brief per adapter and checks it against what that adapter can do, so a step the adapter
  cannot satisfy is a test failure rather than a blocked task at hour three of a run.
- **Carry raw evidence beside the derived form.** `halt_message` beside `halt_class`,
  `last_message_tail` beside `last_message`. When the derivation is wrong, the record still
  holds the thing it was derived from, and the summary can never say less than the record
  knows. Before this the derived field was the only copy.

## Prevention

**Run one live task against a throwaway target after any change to a cross process contract.**
The contracts are: the envelope grammar and `parse_envelope`; `COMPOUND_TERMINAL_LINES` and
`closeout.parse`; the brief templates and the adapters; `_Halt` and the record the summary
reads; and the classify digest keys, which `run.py` and `closeout.py` read by string. A
markdown tracked repo with a local bare origin and two one function tasks takes minutes to set
up and is the only instrument that has found a defect on any of these seams. The stub cannot,
by construction.

**One exception: when the contract change is to the Runner package itself, the run that lands
it is not the run that can verify it.** The Runner is one long lived process that imports
`run.py`, `state.py`, and the rest of `skills/relay/scripts/relay/` once, at launch. A Task
inside that same run can merge a change to those modules, but the process that spawned the
Task keeps running the code it already loaded, so that run's own `state.json` and
`relay summary` output describe the pre merge behavior, not the change that just landed.
`docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`
documents the case: T-2 landed a change to `StateStore.write_terminal`, and the terminal
record that same run wrote afterward carried no trace of it, though the change was correct and
the suite proved it. The live run instrument still applies, it just needs to be the *next* run
against the target, not the one still executing when the change lands.

**Treat "the fixture and the parser were written together" as a smell.** When a fixture is
added, ask where its text came from. If the answer is the parser's docstring, the fixture
pins the parser to itself. The correction is a fixture cut from a transcript a real process
wrote, kept verbatim, with a comment saying which run it came from, as
`CLOSEOUT_SKIPPED_LONG_TEXT` does.

**Keep the raw form in the record.** A derived field (`halt_class`, a parsed list, a matched
terminal line) should never be the only copy of what it was derived from. The check on a new
record field is whether an operator reading the record after a bad derivation could see the
derivation was bad. `halt_message` and `last_message_tail` are the pattern.

**Two seams this run did not reach.** The jira and github `review_step` still instructs the
card move, and no live run has yet driven either adapter; the brief test asserts the sentence,
not that a real process can carry it out against a real tracker. And the classify digest is
still a string keyed dict read by two other modules with no joining test, which the
neighbouring doc names as the next contract of this shape. Both are where the next live run
should point.

## Related Issues

- `docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md` is the
  same shape one layer down: a contract split between a declaration and scattered satisfiers,
  verified by nothing that joins them. Its rule is to write the set level test that performs
  the production operation. This doc's rule is the case where no test can perform the
  production operation, because the producer is a process that has never run, and the only
  joining instrument is a live run against a throwaway target.
- `docs/ideation/2026-08-25-relay-review-residuals.md`, finding 20 and `7fc29f4`, is the
  decision that closed the markdown adapter's no envelope route and stopped one step short of
  the brief. It is the example of a review closing a finding at the runner without checking
  what the brief still told the task.
- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` states two
  questions to ask of a guard: does a test force the excluded branch, and can the code upstream
  put the system into the state the branch guards. This doc adds the third: does anything other
  than the author's own stub ever produce the input the consumer reads.
- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` is the only
  other learning that came from a live unattended run rather than the suite, and its closing
  line, that an unattended runner should assume there are more such gates it has not met,
  applies to Relay's own seams as much as to the harness's.
- `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`, unit U1, is where the fixture
  transcripts were described as trimmed copies of real shapes. That is the assumption this run
  disproved; the plan stays as the historical record.
- `docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`
  is the exception this doc's Prevention section now names: a live run against a throwaway
  target is still the instrument for a cross process contract change, except when the change is
  to the Runner package itself, since the run that lands it keeps executing the code it
  imported before the merge.
