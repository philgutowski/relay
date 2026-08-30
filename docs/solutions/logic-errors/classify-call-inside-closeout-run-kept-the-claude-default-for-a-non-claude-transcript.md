---
title: classify.classify inside closeout.run kept the claude backend default while normalizing a non-claude transcript
date: 2026-08-30
category: logic-errors
module: closeout
problem_type: logic_error
component: runner
symptoms:
  - "a Closeout launched on a non-claude backend (codex, grok) would have its transcript normalized by classify.classify with backend still defaulted to claude"
  - "closeout_digest's key set is identical whether the backend took effect or not, so no digest-level assertion can detect the miss"
  - "the plan's own trap enumeration (KTD15, four call sites) did not name this site"
root_cause: missing_validation
resolution_type: code_fix
severity: high
tags:
  - backends
  - closeout
  - classify
  - downstream-consumer
  - digest
  - KTD15
related_components: [classify, backends]
---

# classify.classify inside closeout.run kept the claude backend default while normalizing a non-claude transcript

## Problem

Backends U8 made the skill invocation form backend-resolved, and the plan named the trap
explicitly (KTD15): the skill invocation form has four call sites, not one, and the fix had to
touch `brief._qualified()`, `closeout.compound_command()`, the `compound_skill` value
`closeout.render()` supplies, and `classify.required_skill_for()`. All four were on the plan's
list, so all four were easy to find and fix.

The site that was not on that list, and that would have broken every non-claude Closeout, was
`classify.classify(...)` called from inside `closeout.run` at `closeout.py:269`, to normalize the
transcript of the Closeout process `closeout.run` had itself just launched. That call kept
`backend="claude"` as its default while the CLI it was reading back from could be codex or grok.

## Symptoms

- A Closeout launched on a non-claude backend would have its own transcript decoded with the
  wrong backend's evidence format.
- `closeout_digest`, the return value `classify.classify` produces, has the same key set
  (`findings`, `last_message_tail`, `last_message`, ...) regardless of whether the backend
  argument matched the launched CLI. A digest-shaped assertion cannot distinguish a correct read
  from a wrong-backend read that happened to produce plausible-looking output.

## What Didn't Work

Tracing only the four call sites the plan's trap enumeration named. That list is complete for
every site that *builds* a skill invocation, but `classify.classify` inside `closeout.run` does
not build one; it reads back what the launched process wrote. It is downstream of the launch
rather than an input to it, so it does not look like a site that needs the backend value at all
until you ask "what decodes this Closeout's own transcript."

## Solution

`closeout.run` threads its own resolved `backend` parameter into the `classify.classify` call:

```python
closeout_digest = classify.classify(launch_result.transcript_path, launch_result,
                                    adapter.write_tool_patterns(), backend=backend)
```

The fix is a one-line addition of `backend=backend` to a call already in scope of the resolved
value. The `classify.classify` signature already accepted a `backend` parameter (added in
Backends U6); the miss was in this one caller not passing it.

## Why This Works

`closeout.run` is described in its own docstring as "the only function on this seam that
defaults `backend`," and it feeds three consumers: the rendered brief, the launched CLI, and the
normalizer that reads what that CLI wrote. The first two are natural to find because they are
both about producing something for the launch. The third reads the launch's own output after the
fact, so tracing "who needs the backend to build a call" skips it by construction.

## Prevention

- When a value becomes backend-resolved (or any per-variant resolved value), enumerate consumers
  by asking two separate questions, not one: "who builds something for this backend" and "who
  reads back what was launched under this backend." The second question finds call sites the
  first one's tracing misses, because a downstream reader is not itself a builder and doesn't
  surface from grepping build sites.
- Do not trust a digest-shaped test to catch a defaulting miss on this kind of seam. Assert on
  the call itself. `tests/test_closeout.py`'s `OneBackendValueReachesEveryConsumer` (the
  `go_spied` helper) mocks both `launch.launch` and `classify.classify` and asserts
  `seen["classify_backend"] == backend`, specifically because the digest cannot tell the two
  cases apart.
- When a plan enumerates "N call sites" for a value going backend-resolved, treat that list as
  the floor, not the ceiling. Grep the module for every place the un-resolved default value
  (`"claude"` or `manifest_module.DEFAULT_BACKEND`) still appears after applying the plan's fix,
  and confirm each remaining occurrence is intentional rather than missed.

## Related Issues

- Backends U8 plan section (`docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md`,
  `### U8. Permission posture and skill form`) and its KTD15 trap, which named the four build
  sites this doc's site was missing from.
- `tests/test_closeout.py`, class `OneBackendValueReachesEveryConsumer`, the test that would
  catch a regression here.
