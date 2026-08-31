---
title: The halt-comment gate's own checks lived outside its best-effort guard, and a closeout-scope reset looked like an ordinary unclean exit
date: 2026-08-31
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [run-loop, gitwrite, gitread, closeout, contracts, halt-evidence]
symptoms:
  - "run.py's _note_halt wrapped only the Closeout launch in try/except, so a check itself raising (gitread.is_clean, gitwrite.head_equals_remote, a lease heartbeat I/O failure) escaped _note_halt and reached run()'s own except-Exception handler, which fabricates a replacement halt from whatever broke, masking the real one"
  - "gitwrite.closeout_scope_check's own HALT_UNCLEAN_EXIT raise resets the tree to clean before raising, so _note_halt's tree-clean check alone would pass and let a second Closeout launch straight onto the mechanism that just misbehaved"
  - "a halt raised after the task's own landed closeout already ran (a mirror push refusal, a failing final verify) rendered as an undifferentiated halt comment on a card the runner had already moved to landed, with no mention that landing had in fact happened"
tags: [halt-comment-gate, best-effort-guard, try-except-scope, reset-to, masking, closeout-scope-check, adversarial-review, code-review]
---

# The halt-comment gate's own checks lived outside its best-effort guard, and a closeout-scope reset looked like an ordinary unclean exit

## Problem

Relay task 50 added `run.py:_note_halt`, a best-effort step that comments a halt's class and
cause onto the tracker card without changing its status (R4). The plan's own reasoning enumerated
four gating checks (halt class, tree clean, branch in sync, lease fresh) and wrapped the Closeout
launch in `try/except Exception` so a failure there would not crash the run. Three gaps in that
first pass were invisible from the plan's own reasoning and surfaced only under `ce-code-review`'s
reliability, correctness, and adversarial lenses, not from the plan text or the fixture suite.

## Symptoms

- A check itself raising (not the Closeout launch) escaped `_note_halt` entirely and reached
  `run()`'s outer `except Exception`, which fabricates a new halt from that failure, exactly the
  kind of masking `_note_halt` exists to prevent.
- `gitwrite.closeout_scope_check`'s `HALT_UNCLEAN_EXIT` raise site resets the tree before raising,
  so `gitread.is_clean` reports clean immediately afterward, and the gate would launch a second
  Closeout onto a mechanism that had just failed.
- A halt after a successful landing had no `landing_ref` passed through, so the rendered comment
  read as a fresh, undifferentiated halt rather than "landed, then this later step failed."

## What Didn't Work

- Wrapping only the Closeout launch in `try/except` looked sufficient because the plan's KTD4
  framing ("every stop is a named class with evidence, not an exception") was read as being about
  the launch, not about the four checks that run before it. The checks call into `gitread` and
  `gitwrite`, both of which can raise on real git or filesystem failures.
- Excluding `HALT_UNCLEAN_EXIT` outright was rejected: that class is too general, most of its raise
  sites are unrelated to the Closeout mechanism itself and still want the comment. The class alone
  cannot distinguish a closeout-scope self-failure from an ordinary unclean exit.

## Solution

`skills/relay/scripts/relay/run.py`'s `_note_halt` now wraps all four checks and the launch in one
`try/except Exception`, not just the launch, so any failure inside the function is logged and
swallowed rather than propagating to `run()`'s own handler. The halt-class exclusion also checks
`halt.evidence.get("reset_to") is not None`: that key is unique to
`gitwrite.closeout_scope_check`'s `HALT_UNCLEAN_EXIT` raise (recorded in relay task 50) and is the
only reliable signal that the tree was reset by the Closeout mechanism itself rather than left dirty
by ordinary work. When the task's stored record already carries a `landing_ref`, `_note_halt` reads
it (`ctx.store.get(halt.task_id)`) and passes it to `_run_closeout`, and `closeout.render` prefixes
the comment with `"Landed at %s, but the run then halted."` when present.

## Why This Works

A best-effort guard is only best-effort if the guard itself cannot fail loudly. Scoping the
`try/except` to the checks as well as the launch keeps every failure inside `_note_halt`'s own
swallow-and-log path, so the run's outer exception handler never sees it and never fabricates a
replacement halt. Keying the closeout-scope exclusion off `reset_to` rather than the halt class
lets the exclusion be precise (only the raise site that actually reset the tree is excluded)
without over-excluding the rest of `HALT_UNCLEAN_EXIT`'s raise sites, which still want a comment.

## Prevention

- When a function's contract is "never let an internal failure escape, only its own named
  evidence," wrap the whole function body, not just the step that seemed likeliest to fail. The
  plan's own trap enumeration for `_note_halt` did not name the checks as a risk, only the launch.
- When a downstream raise site resets shared state before raising, a check written against that
  state after the fact cannot detect it; key any exclusion off evidence the raise site itself
  attaches, not off state a caller reads afterward.
- Run `ce-code-review` in reliability, correctness, and adversarial modes on the same change
  before landing; each caught a different one of the three gaps here, and none surfaced from the
  fixture suite.

## Related Issues

- relay task 50 (GitHub issue #50, `philgutowski/relay`), commit range `440f2b2..28355b4`.
- [[stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects]]
