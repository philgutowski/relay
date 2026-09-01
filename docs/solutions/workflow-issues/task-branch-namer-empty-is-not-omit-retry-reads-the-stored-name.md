---
title: The Task branch namer is prefix plus id. Empty is not omit. Retry reads the stored name.
date: 2026-09-01
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: medium
applies_when:
  - "a Manifest, brief, preflight, merge tail, verify, or retry path needs the Task branch name"
  - "project.branch_prefix is being added, defaulted, or treated as absent"
  - "retrying a blocked Task after the Manifest prefix may have changed"
tags: [task-branch, branch-prefix, retry-blocked, empty-string, namer]
---

# The Task branch namer is prefix plus id. Empty is not omit. Retry reads the stored name.

## Context

Issue 55 asked to make the Task branch prefix Manifest configurable. The card named two hardcoded "relay/" literals, one in `gitwrite.py` and one in `brief.py`. Those two sites were not the namer. The Runner also names the branch in run, verify, the merge tail, and retry blocked, all through `gitwrite.task_branch_for`. A change that patched only the two literals would leave every other site on the default prefix.

Two further rules are easy to lose if the namer is treated as ordinary string defaulting.

## Guidance

Name a Task branch only through `gitwrite.task_branch_for(task_id, prefix)`. Pass the Manifest's `project.branch_prefix` at every production call. An omitted prefix argument is `None`, and `None` becomes `contracts.DEFAULT_TASK_BRANCH_PREFIX` again. The merge tail still has that fallback when its `branch` argument is missing (`gitwrite.local_merge_tail`), so a caller that forgets to pass the name silently rebuilds `relay/<id>`.

Treat empty and omit as different values. Manifest load uses `key in table`, not truthiness, so `branch_prefix = ""` stays empty (`manifest.py` `pick`). The namer uses `prefix is None`, not `prefix or default`, so an empty string concatenates to the Task id alone. A Python `or` default would turn the empty escape (a target whose branches are the Task id alone, or a prefix such as `IW-` with no default prefix in front) back into `contracts.DEFAULT_TASK_BRANCH_PREFIX`.

On `--retry-blocked`, prefer the branch stored on the blocked record over a name rebuilt from the Manifest as it stands now. `run._one_task` does `record.get("branch") or branch`. A later prefix edit must not hide stranded commits that still live on the old name.

## Why This Matters

The Task branch is an identity written at launch, not a derived view of the current Manifest. Recomputing it from today's prefix can look at a branch that was never created, skip the one that was, and let retry delete or ignore work the operator still has to keep or discard. Collapsing empty to the default has the same shape: the Manifest said "no prefix" and the Runner would still create `relay/<id>`.

A ticket that greps two literals is not a map of the namer surface. The next call site, especially in a new file the production namer test does not scan, will silently reintroduce the default prefix unless it passes the prefix through.

## When to Apply

- Adding or renaming any site that creates, checks, merges, verifies, or deletes a Task branch.
- Changing how `project.branch_prefix` is loaded or defaulted.
- Touching `--retry-blocked` or the refusal that a stranded branch still carries commits.

## Examples

Absent key: `task_branch_for("55")` and `task_branch_for("55", None)` are both `relay/55`.

Set prefix: `task_branch_for("55", "IW-")` is `IW-55`.

Empty prefix: `task_branch_for("55", "")` is `55`.

Retry after a prefix edit: a Task that blocked on `IW-T-2` still refuses retry against `IW-T-2` after the Manifest is changed to the prefix "other/". `tests/test_run.py` `test_retry_still_sees_a_stranded_branch_after_a_prefix_edit` pins that. `tests/test_gitwrite.py` `TaskBranchName.test_production_namer_calls_pass_a_prefix` fails if `run.py`, `verify.py`, or `gitwrite.py` call `task_branch_for` with only the Task id.

Landed at `8fbfa665dde50a12c8a598e0fc992ab08378b86a` (issue 55, commit range `c910265..8fbfa66`).

## Related

- `docs/solutions/logic-errors/continue-past-halt-checked-general-state-blind-to-the-branch-its-own-skip-left.md` still describes the stranded branch as `relay/<id>`. That name is now prefix plus id.
- `CONCEPTS.md` Task process: the branch is prefix plus Task id, default `contracts.DEFAULT_TASK_BRANCH_PREFIX` when the key is omitted.
