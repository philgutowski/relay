---
title: The record-wins backend rule swapped a task back onto its old CLI even when the operator had deliberately moved it in the manifest
date: 2026-09-01
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: medium
root_cause: logic_error
resolution_type: code_fix
related_components: [manifest, state, contracts, summary]
symptoms:
  - "task 45 halted on grok; the operator edited the manifest to move it to sonnet; the retry relaunched it with args opening \"grok\" anyway"
  - "_one_task swapped task = replace(task, backend=record[\"backend\"]) whenever the record had a backend that differed from the manifest, with no way to tell a deliberate reassignment from a resumed launch"
  - "the retried task's model was never pinned the same way, so half of one routing choice (backend) was record-wins and the other half (model) was already manifest-wins, and nothing said the two disagreed"
tags: [relay-task, run-py, backend-routing, retry-semantics, record-wins]
---

# The record-wins backend rule swapped a task back onto its old CLI even when the operator had deliberately moved it in the manifest

## Problem

`_one_task` (`skills/relay/scripts/relay/run.py`) unconditionally preferred the stored record's
backend over the manifest's on every relaunch:

```python
if record.get("backend") and record["backend"] != task.backend:
    task = replace(task, backend=record["backend"])
```

That rule (from issue #53) exists so a task interrupted mid-run resumes on the CLI it was
actually running on. It cannot distinguish that case from an operator editing the manifest to
move a halted task to a different backend on purpose: both look identical, a record backend that
differs from the manifest's. Round eight hit the second case live. Task 45 halted on grok, the
operator changed its manifest entry to `model = "sonnet"` with no `backend` key, and the retry
still launched `args: ["grok", "-p", ...]`, because the record's stored `backend: "grok"` beat
the manifest every time. The only way out was hand-editing `state.json` with the lease free.

## Solution

`8e350dd`..`14f7a32` (issue #58) reverse the default: the manifest's resolution decides a
relaunch's backend and model, and record-wins is kept only at the three places in `run.py` that
raise before any launch, where the record still describes a real previous attempt (the stranded
branch refusal's name and baseline, and the halt handler's fill):

```python
previous = store.get(halt.task_id) or {}
kept = previous if previous.get("backend") else {"backend": task.backend, "model": task.model}
store.upsert(halt.task_id, ..., backend=kept.get("backend"), model=kept.get("model"))
```

`model` joins `state.RECORD_FIELDS` so it can be reasoned about the same way `backend` always
was. The move itself is never silent: `_reassignment()` compares the record's stored backend and
model against the manifest's resolved values, per field, and only where the record actually
carries a value (an absent field is not read as "changed"). When either differs, the runner
streams the move before launching and writes it onto the record as a `BACKEND_REASSIGNED`
finding, kept out of the digest object closeout renders to the tracker so a routing note never
becomes a card comment. `manifest.validate` also gained a check, since the backend and model are
independent manifest fields (`[defaults]` has a backend, not a model): a single `[defaults]`
backend edit hands every task's old model string to a new CLI, so validate refuses a backend
paired with a model another backend's capability record claims.

## Why This Works

The card described this as an edit being "ignored." It was actually half applied: there was no
`model` key on the old record, so the manifest already won for the model while the stored value
pinned the backend, and the two halves of one routing choice disagreed with nothing saying so.
Weighing the option of scoping record-wins narrower (only override when the manifest still names
the recorded backend) showed it reduces to a no-op, since the swap only ever fires when the two
already differ, and it would not have fixed this incident either, because task 45's record
carried no `backend` key at all and inherited the default. The honest form of that option is a
full reversal: default to the manifest, and keep record-wins scoped to the raise sites where the
record is evidence of something that actually happened on disk (a branch with real commits, a
baseline the retry must diff against) rather than a general resume preference.

## Prevention

- A "record wins on retry" rule is only safe where the record is grounded against something on
  disk (a branch, a baseline sha) that the manifest cannot re-derive. Anywhere else, prefer the
  manifest and make the divergence a visible finding rather than a silent swap.
- Two fields that describe one routing choice (backend, model) need one shared rule for when the
  stored value beats the current input, not one rule per field arrived at separately; check for a
  case where the two fields could tell different stories before shipping either independently.
- The regression test that pinned the old rule was vacuous: it retried a task on the blocked
  fixture, which always carries commits on its branch, so the R48 stranded-branch refusal halted
  the second run before any launch happened, and both its assertions passed on values the first
  run had already written. It never exercised an actual relaunch. This is the same family as
  `docs/solutions/logic-errors/miss-tests-for-the-unenforced-audit-passed-with-nothing-audited.md`:
  a retry test only proves something about the second run if that run reaches a real launch, which
  here meant a first run whose task process writes no transcript, ending the task blocked with no
  branch at all.

## Related Issues

- `docs/solutions/logic-errors/invalid-defaults-backend-silently-turned-off-the-reason-check.md`
  is the neighboring backend-validation defect in the same module, and this fix's new
  backend/model coherence check in `manifest.validate` follows the same "split the reference
  check from the per-record check" shape it established.
- `docs/solutions/logic-errors/miss-tests-for-the-unenforced-audit-passed-with-nothing-audited.md`
  is the general vacuous-test family this task's retry test belonged to.
- Issue #62 opened to read the round eight transcripts for a related but separate defect: the
  same retry's grok relaunch died in about a second with empty stdout and classified
  `no_envelope`, which is what turned task 45 from halted into blocked.
