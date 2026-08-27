---
title: tail deliberately skips validate, so its own follow loop is the only guard against manifest shapes validate would reject
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
symptoms:
  - "an empty task list raised IndexError out of emit, because the cursor started at 0 with no readers to index"
  - "a task id listed twice built two readers on the same log file, each with its own offset, replaying that task's whole log a second time under a duplicate header"
root_cause: missing_validation
resolution_type: code_fix
severity: medium
related_components: [tail, manifest, validate]
tags: [tail, validate, follow-loop, manifest-shapes]
---

# tail deliberately skips validate, so its own follow loop is the only guard against manifest shapes validate would reject

## Problem

`cmd_tail` (skills/relay/scripts/relay/cli.py:198) loads the manifest but does not call `validate`
on purpose: a reader should still be able to watch a run whose manifest file was edited after the
run started. That choice means the two manifest shapes `validate` normally rejects, an empty task
list and a task id listed twice, reach `follow()` (skills/relay/scripts/relay/tail.py:159) intact
instead of being stopped at the door.

## Symptoms

- An empty task list raised `IndexError` out of `emit`, since `cursor` started at 0 with no
  readers in the list to index.
- A task id listed twice built one `_Reader` per manifest entry, so two readers pointed at the
  same log file, each with its own offset. The follow loop replayed that task's whole log a
  second time under a duplicate `== T-1 ... ==` header.

## What Didn't Work

Building `readers` as a straight one-per-manifest-entry list (`readers = [_Reader(*entry) for
entry in candidates(manifest, store)]`) assumes `candidates()` already returns a well-formed,
deduplicated sequence. It doesn't, because nothing upstream of `follow()` validates the manifest
for `tail`.

## Solution

Deduplicate readers by log path when building them, and bound the final unconditional `emit` by
the reader count instead of assuming at least one exists:

```python
readers = []
seen = set()
for task_id, phase, path in candidates(manifest, store):
    if path not in seen:
        seen.add(path)
        readers.append(_Reader(task_id, phase, path))
...
if cursor < len(readers):
    emit(cursor)
```

## Why This Works

One reader per distinct log path means a duplicated task id collapses onto the single reader that
already tracks that file's offset, so nothing gets drained twice. Guarding the trailing `emit`
call with `cursor < len(readers)` means an empty reader list leaves the loop polling `store.terminal()`
instead of indexing past the end of an empty list.

## Prevention

Any verb that deliberately reads a manifest without calling `validate` inherits the job of
surviving every shape `validate` would otherwise catch. When adding logic downstream of an
unvalidated manifest load, write a test for the shapes `validate` rejects (empty task list,
duplicate task id) against that code path specifically, not just against the validated case.

## Related Issues

- docs/plans/2026-08-27-1912-feat-relay-tail-verb-plan.md
