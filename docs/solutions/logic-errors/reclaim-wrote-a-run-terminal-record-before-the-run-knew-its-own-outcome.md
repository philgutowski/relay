---
title: Stale-lease reclaim wrote a run-level terminal record before the run knew its own outcome, so two independent readers were fooled by one shared write
date: 2026-08-31
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
symptoms:
  - "status printed \"terminal record: crashed\" and \"halted on 40 with class runner_crashed\" in the same output that showed 37 running and a live lease heartbeat"
  - "a --follow attached to the resuming run treated the reclaim's crashed terminal record as the run's real ending, printed the run summary, and exited"
  - "a second run launched on the belief the first had ended was correctly refused by the lease, so the visible symptom was operator confusion rather than data loss"
root_cause: async_timing
resolution_type: code_fix
related_components: [state-store, run-loop, tail-follow, lease]
tags: [reclaim, stale-lease, terminal-record, continue-past-task-halt, premature-write, follow, status]
---

# Stale-lease reclaim wrote a run-level terminal record before the run knew its own outcome, so two independent readers were fooled by one shared write

## Problem

`StateStore._mark_crashed`, called when a resume reclaims a stale lease, marked the reclaimed task's own record `halted`/`runner_crashed` (correct) and also wrote a run-level `state["terminal"]` block describing the whole run as crashed (wrong). The reclaiming run had not yet decided whether it would halt there or continue past the halt under `continue_past_task_halt`; the write happened before that decision existed.

## Symptoms

- `status` printed `terminal record: crashed` and `halted on 40 with class runner_crashed` in the same output that showed `37 running` and a live lease heartbeat.
- `tail --follow`, attached to the same resuming run, read the premature terminal record as the run's real ending, printed the run summary, and exited while the run was still going.
- The operator, told by `--follow` that the run had ended, launched a second `run`, which the lease correctly refused, since the first run was still live.

## What Didn't Work

Gating the write (only write `terminal` on reclaim if some condition holds) was considered and rejected. At the point `_mark_crashed` runs, the process cannot yet know whether the task it just reclaimed will be retried and succeed, retried and fail, or trigger a full run halt, because that decision is made later in `run()`'s own control flow. No condition available inside `_mark_crashed` can distinguish those outcomes.

## Solution

Delete the write. `_mark_crashed` keeps marking the reclaimed task's own record `halted`/`runner_crashed` and recording the previous holder in that record's `halt_evidence.previous_holder`, but no longer touches `state["terminal"]` at all. Only `run()`'s three existing terminal-write call sites (completion, a halt not continued past, and the crash backstop) write `state["terminal"]`, because only they run after the run has actually decided its own ending.

```python
# skills/relay/scripts/relay/state.py, StateStore._mark_crashed
# before: wrote state["terminal"] here, guarded by a written_at/lease_started comparison
# after: no state["terminal"] write in this method at all
```

## Why This Works

`cmd_status`'s raw terminal block and `tail.follow`'s floor comparison are two independent readers, but both trust `state["terminal"]` as proof the run is over. Both were fooled by the same premature write, not by two separate bugs. Once the write no longer exists, both readers see the run as still active until one of `run()`'s real terminal writes lands, which is the only point that write is actually true.

## Prevention

- A test that only asserts `state["terminal"]` after `run()` returns cannot distinguish this bug from its absence, because `run()`'s own final terminal write always overwrites whatever the reclaim wrote once the run reaches a real ending. The diagnostic assertion has to happen between the reclaim and the run's conclusion, for example by pausing the classifier or launcher mid-run and inspecting state at that point, not by inspecting state after `run()` exits.
- When a state field has multiple readers (here `cmd_status` and `tail.follow`), treat a fix to its writer as fixing every reader at once rather than patching each reader's interpretation separately; the shared root cause is the write, not the reads.

## Related Issues

- Relay issue #47 (the kill that left the stale lease this reclaim inherited).
- Relay issue #15 (`continue_past_task_halt`, the run-continues-anyway path this bug ran under).
- `docs/plans/2026-08-31-1155-fix-reclaim-writes-premature-terminal-record-plan.md`
