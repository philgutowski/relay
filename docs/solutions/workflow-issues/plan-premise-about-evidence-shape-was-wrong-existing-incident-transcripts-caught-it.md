---
title: A plan's premise about where evidence lives was wrong, and the untouched transcripts from the original incident caught it, not a fresh reproduction attempt
date: 2026-09-01
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: medium
applies_when:
  - "writing a fix for a Task or Closeout process failure whose plan was drafted before any live check against the real evidence"
  - "the original incident already left session state on disk (a transcript, an updates.jsonl, a stdout log) under a Relay state dir"
  - "a fresh attempt to reproduce the bug on the currently installed CLI version fails to reproduce it"
  - "the plan's Assumptions section describes the shape of a log line, a stream event, or a stopReason rather than quoting one actually captured"
resolution_type: workflow_improvement
tags:
  - premise-verification
  - live-run-evidence
  - untouched-transcripts
  - grok
  - classify
  - u4-live-verification
---

# A plan's premise about where evidence lives was wrong, and the untouched transcripts from the original incident caught it, not a fresh reproduction attempt

## Context

Issue 57 fixed a real failure: under `--permission-mode auto`, Grok's own permission layer
cancels a `run_terminal_command` call that contains command substitution (the
`git commit -m "$(cat <<'EOF' ... EOF)"` form the compound-engineering pipeline teaches for every
commit), the stream ends with `stopReason: "cancelled"`, and no return envelope is written.
Tasks 45 and 56 in round eight both died this way and were misclassified, one as
`closeout_out_of_scope` with an empty branch, the other as `unclean_exit` with a dirty tree,
neither cause line naming the real cause.

The plan drafted for the fix, written before any live check, assumed the cancellation could only
be read off the stdout log by matching `stopReason: "cancelled"`, which meant `classify.py`
reading a second file it does not otherwise open. Live verification (unit U4 in the plan)
disproved that. Grok's transcript file, `updates.jsonl`
(`skills/relay/scripts/relay/backends/grok.py:183`, normalized into the same `user` /
`tool_result` shape `classify.py:409` already reads for Claude), carries the cancellation as a
`tool_result` block with `is_error: true`, matched by `contracts.CANCELLED_TOOL_REGEX`
(`contracts.py:106`), the unanchored sibling of `DENIAL_REGEX` (`contracts.py:93`). No second
file, no `stopReason` match, was ever needed; the evidence was already in the file the classifier
opens for every other finding.

**What actually caught the wrong premise was not a fresh reproduction.** An attempt to reproduce
tasks 45/56's cancellation on the currently installed Grok CLI version failed outright, for
reasons unrelated to whether the plan's premise about the evidence's shape was correct. What
settled the question was reading the two untouched session state dirs left on disk from the
original round-eight failures, tasks 45 and 56's own stdout logs plus their `updates.jsonl`
captures, still sitting under that run's Relay state dir (a runtime artifact under `~/.relay/`,
not a path tracked in this repo). Those real captures showed the cancellation in `updates.jsonl`
as a
`tool_call_update` with `status: "failed"`, the same shape a permission denial uses, distinguished
only by the message text (`"User cancelled the execution for tool ..."` versus
`"... has been denied"`). One `cancelled_tool_call` finding came out of each, confirmed directly
against the real files, not a synthetic fixture.

## Guidance

**When a past incident already produced real transcripts, read those before trying to reproduce
the incident fresh.** A reproduction attempt can fail for reasons that have nothing to do with
whether the plan's premise about the evidence's shape is right, such as a CLI version bump
changing an unrelated code path. The untouched transcripts from the actual failure are stronger
evidence than a new run that may not even hit the same bug, and they cost nothing to read since
they are already on disk.

This is the same family as
`docs/solutions/workflow-issues/quota-exhaustion-reads-as-no-envelope-and-the-rate-limit-telemetry-is-already-discarded.md`:
in both cases the evidence that would settle a misclassification was already captured and simply
never read by the code doing the classifying. Here the same discarded-evidence shape extended
one step further, into the planning process itself: the plan's own Assumptions section guessed at
a shape (`stopReason` on a second file) instead of reading the shape that was already sitting on
disk from the incident the plan was written to fix.

## Why This Matters

A plan drafted before any live check is a hypothesis, not a fact, even when it reads as a precise
technical description (a specific stream key, a specific file). The cost of treating it as settled
is a fix built for evidence that does not exist in the shape assumed, here a stdout-log read and a
`stopReason` match that the actual fix (`classify.py:431`, `contracts.py:106`) never needed. The
corrective is cheap when the incident already left real transcripts: read them before writing the
fix, not after a failed attempt to reproduce the bug fresh.

## When to Apply

- Any Relay fix for a Task or Closeout failure where the state dir from the original incident is
  still on disk. Check for it before scoping the fix.
- Any plan whose Assumptions section describes an evidence shape (a log line, a stream event key,
  a status value) that was reasoned about rather than quoted from a real capture.
- A fresh reproduction attempt that fails is not evidence the original premise was right; it is
  evidence that this particular attempt did not hit the bug. Go to the original transcripts before
  concluding either way.

## Related

- `docs/solutions/workflow-issues/quota-exhaustion-reads-as-no-envelope-and-the-rate-limit-telemetry-is-already-discarded.md`
  is the same discarded-evidence shape at the classification layer; this doc is the same shape one
  step upstream, in the planning process that decided what the classifier should read.
- `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md` is the
  direct ancestor for the underlying Grok cancellation behavior itself, `--permission-mode auto`
  still refusing rather than bypassing. This doc is a separate cancellation trigger, a
  command-substitution commit form under the already-correct `auto` posture, not a permission mode
  error.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  shares the underlying lesson that a claim and the thing meant to verify it can share one
  unverified source; this doc's addition is that the correction did not require a new live run,
  only reading the evidence the previous live incident already left behind.
