---
title: a follower launched beside a fresh run needs a floor read and a bytes-past-start frontier, not os.path.exists
date: 2026-08-28
category: logic-errors
module: runner
problem_type: logic_error
component: runner
symptoms:
  - "a follower started with `run --follow` on a manifest whose state dir already held a previous run's logs replayed the entire previous run, then exited immediately on that previous run's terminal record"
  - "seeding reader offsets alone was not enough: the frontier's `os.path.exists` check was satisfied by a pre-existing log, so the cursor jumped to the last candidate on the first poll and swallowed output the new run appended to earlier tasks"
  - "a follower keyed only on the terminal record never ended when the runner refused the manifest (held lease, invalid manifest) and returned before writing one"
root_cause: logic_error
resolution_type: code_fix
severity: medium
related_components: [tail, launch, run]
tags: [follower, floor-read, frontier, terminal-record, append-mode]
---

# a follower launched beside a fresh run needs a floor read and a bytes-past-start frontier, not os.path.exists

## Problem

`run --follow` (skills/relay/scripts/relay/cli.py) launches the runner and then watches its
logs and `state.json` from the same process. Two facts, each correct on its own, combine badly
here: the state directory is keyed on the manifest's real path, so it outlives any one run and
task logs from a previous run are still on disk; and `launch.py` opens each task log in append
mode, so a second launch against the same manifest finds every previous log intact. `tail.py`'s
own rule is that a terminal record already present wins, which exists so a follower never hangs
on a run that already finished. Those two facts are correct separately and wrong together: a
follower on a fresh launch replayed the entire previous run's logs and then exited at once on
the previous run's terminal record.

## Symptoms

- A follower started beside a fresh launch, against a manifest whose state dir already held a
  finished run, printed the previous run's whole output and then ended immediately.
- Seeding reader offsets from the previous logs' sizes was not sufficient by itself: `_Reader.active()`
  (skills/relay/scripts/relay/tail.py:181) originally used `os.path.exists`, which a pre-existing
  log satisfies. The frontier jumped to the last candidate on the first poll, so output the new
  run appended to earlier tasks was never drained.
- A follower keyed only on the terminal record has no ending when the runner refuses the manifest
  (a held lease, an invalid manifest) and returns before `write_terminal`, since no record is ever
  written for it to key on.

## What Didn't Work

Seeding each `_Reader`'s starting offset from the previous run's log size looked sufficient but
was not, because the frontier predicate that decides whether a reader has anything new was
`os.path.exists(self.path)` rather than a comparison against that seeded offset. Existence is
true for a log the previous run already wrote, so the predicate could not tell "this file existed
before I started" from "this file has new bytes since I started."

## Solution

Read a floor before the child process starts, and drive the follow loop off it:

```python
# tail.py
def read_floor(manifest, store):
    state = store.read() or {}
    return {"offsets": {path: _size(path) for _id, _phase, path in candidates(manifest, store)},
            "terminal": state.get("terminal"),
            "statuses": {task_id: record.get("status")
                         for task_id, record in (state.get("tasks") or {}).items()}}

class _Reader:
    def active(self):
        """True once this file carries bytes past where this follower started."""
        return _size(self.path) > self.start_offset
```

`cli.py` calls `read_floor` before `launch()`, so the floor captures log sizes, the terminal
record, and every Task's status as they stood before the child could write anything. `follow()`
then also takes `runner_alive`, a callback that reports whether the launched process has exited,
and returns a distinct `GONE` sentinel when that happens with no terminal record on disk, rather
than looping forever.

## Why This Works

Comparing `_size(path) > self.start_offset` is "this file has bytes past where this follower
started," which is true only for output the new run produces, whereas `os.path.exists` is true
for anything on disk regardless of which run wrote it. The floor read happening before `launch()`
is what makes the comparison meaningful: it fixes "before" as a point in time relative to the
new run's own writes, not relative to some arbitrary poll. Watching the launched process closes
the gap the terminal-record rule leaves open: a run that never gets far enough to write a record
still has a defined ending for its follower, checked in process-exit-then-record order because
the record is written by the runner just before it exits.

## Prevention

When a follower or watcher starts beside a process that appends to files or a store that already
holds prior runs, do not trust `exists`, "value present," or "size greater than zero" as an
activity signal by itself. Take a floor snapshot before the watched process starts, and compare
everything the watcher reports against that floor, not against the state of the world at the
watcher's first poll. Separately, any ending condition keyed on a specific record has to be
paired with a check on the process itself, since the code path that would write that record can
return before reaching it.

## Related Issues

- docs/plans/2026-08-28-0836-feat-relay-foreground-follower-and-notifications-plan.md
- docs/solutions/logic-errors/tail-skips-validate-so-follow-loop-must-survive-empty-and-duplicate-task-lists.md
