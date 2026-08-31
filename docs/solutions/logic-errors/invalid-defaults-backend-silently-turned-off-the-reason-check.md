---
title: An invalid [defaults] backend silently turned off the per-Task reason check instead of being refused
date: 2026-08-31
category: logic-errors
module: manifest
problem_type: logic_error
component: runner
severity: medium
root_cause: missing_validation
resolution_type: code_fix
related_components: [manifest, backends]
symptoms:
  - "manifest.validate required both task.backend and default_backend to be members of BACKENDS before enforcing the reason-on-mismatch rule (R5-adjacent backend check)"
  - "an empty or wrong-case [defaults] backend (e.g. \"\" or \"CODEX\") is not in BACKENDS, so the guard's default_backend in BACKENDS half was false"
  - "with that half false, the whole condition was false for every Task, so a Task naming a non-default backend validated with no reason required, on a manifest that should have been refused outright"
tags: [relay-task, manifest-py, backend-validation, defaults-backend, guard-condition]
---

# An invalid [defaults] backend silently turned off the per-Task reason check instead of being refused

## Problem

`manifest.validate` enforces that a Task whose `backend` differs from the resolved
`[defaults] backend` must carry a `reason`. The guard read the raw `[defaults].backend`
value and required it to be a member of `BACKENDS` in the same boolean expression that
required the per-Task reason:

```python
if (task.backend in BACKENDS and default_backend in BACKENDS
        and task.backend != default_backend
        and not (task.reason or "").strip()):
    err(...)
```

An invalid default (missing, empty, or wrong case) makes `default_backend in BACKENDS`
false, which makes the whole conjunction false for every Task regardless of its own
backend or reason. The manifest then validates successfully even though it both names an
invalid default and lets a Task skip the reason it should owe.

## Symptoms

- A manifest with `[defaults] backend = ""` (or any string outside `BACKENDS`) passed
  validation with zero errors mentioning the default at all.
- A mixed manifest built on top of that invalid default, with a Task naming a different
  backend and no `reason`, also passed, because the reason check never fired.
- the existing empty-default test in the `Backends` class of `tests/test_manifest.py` previously
  only asserted one error per Task rather than an explicit `defaults.backend` error, so the
  missing top-level refusal had no test surface to catch it.

## Solution

Refuse an invalid `[defaults].backend` on its own, as its own error, independent of any
Task. Then resolve a `default` value that is always a member of `BACKENDS` (falling back
to `DEFAULT_BACKEND` when the raw value is absent or invalid) and compare every Task's
backend against that resolved value, never against the raw possibly-invalid one:

```python
dflt = manifest.raw.get("defaults") or {}
default_backend = str(dflt["backend"]) if "backend" in dflt else DEFAULT_BACKEND
if "backend" in dflt and default_backend not in BACKENDS:
    err("defaults.backend must be one of %s, not %r"
        % (", ".join(BACKENDS), default_backend))
resolved_default = default_backend if default_backend in BACKENDS else DEFAULT_BACKEND
...
if (task.backend in BACKENDS and task.backend != resolved_default
        and not (task.reason or "").strip()):
    err(...)
```

`skills/relay/scripts/relay/manifest.py:483-511` (relay task 28, commit aab8f4b) carries
the fix. Tests now pin a non-`claude` default with a matching Task (no reason owed), a
`grok` Task against the implicit `claude` default (reason owed, no unenforced-fields
error), and an invalid uppercase default that still requires a reason from a Task that
differs from the resolved fallback.

## Why This Works

Putting "is the default itself valid" and "does this Task owe a reason" in the same
boolean condition made the second question depend on the first being true, when the two
are independent facts: an invalid default is a manifest-level defect on its own, and a
Task's obligation to explain a backend choice should be judged against a default that is
always well-formed. Separating the invalid-default check into its own `err()` call, then
resolving a fallback that is guaranteed to be in `BACKENDS` before any Task is compared
against it, removes the shared boolean's ability to let one bad value cancel an unrelated
check.

## Prevention

- When a guard condition ANDs "is this reference value valid" together with "does this
  record violate a rule relative to that value," an invalid reference value makes the
  whole condition false and silently waives the rule, rather than surfacing the invalid
  reference. Split the validity check into its own unconditional error, then resolve a
  fallback that is guaranteed valid before using it in any per-record comparison.
- When adding a rule keyed off a resolved default, test the invalid-default case
  explicitly and assert on the specific error text, not just an error count, so a
  regression that changes which check fires (or stops firing) is caught by the assertion
  itself.

## Related Issues

- `docs/solutions/logic-errors/finding-text-undercounted-backend-readiness-failure-branches.md`
  is the neighboring backend-validation case in this same module.
