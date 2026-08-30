---
title: A task card's stated blocking condition is a snapshot from when it was filed, not a live fact
date: 2026-08-30
category: workflow-issues
module: runner
problem_type: workflow_issue
component: development_workflow
severity: medium
applies_when:
  - a task card or issue says "do not do X before Y lands" or otherwise frames itself as blocked on another unit of work
  - the manifest or issue text predates the current head of main by an unknown margin
  - a task process is deciding whether to proceed or report blocked based on the card's own framing
tags: [stale-precondition, blocking-condition, task-card, verify-against-main, relay-manifest]
---

# A task card's stated blocking condition is a snapshot from when it was filed, not a live fact

## Context

Relay task 18 (issue #18) carried an explicit instruction in its own card: "Do not fix this before Backends U5 lands," because at filing time nothing read `contracts.BACKEND_PINS` and every run really was a single global `dontAsk` run. By the time the task process actually ran, Backends U5 had already landed on `main`: `launch.build_args` was reading `BACKEND_PINS` per backend. The stated block was stale, not active.

## Guidance

The task process re-checked the precondition directly against the current code (grepped for `BACKEND_PINS` usage in `launch.build_args`) rather than trusting the card's framing, confirmed the block no longer held, and proceeded to land the fix. Treat a card's blocking condition as a claim to verify against the current tree, not as a fact to inherit. This cuts both ways: a card that reads as safe to proceed can equally be stale in the other direction if some other precondition regressed after filing.

## Why This Matters

Manifest and issue text is written once, at filing time, and Relay tasks run later, sometimes much later, against whatever `main` has become by then. Trusting the text's framing outright risks two opposite failures: reporting `blocked` on a precondition that already resolved (wasted halt, needs a human to re-trigger), or proceeding on a precondition that reads as satisfied but no longer is (landing a change that contradicts the current tree). Both are avoided by re-verifying the specific condition the card names, directly against source, before acting on it.

## When to Apply

- Any task card containing a "do not do X before Y lands" or "blocked on #N" statement.
- Before reporting a halt class of `blocked`: confirm the named precondition still holds by reading the code or tracker state it depends on, not by re-reading the card text.
- Before skipping a stated block and proceeding: confirm the precondition it names is actually satisfied now, the same way.

## Examples

Issue #18 named `manifest.py:353` and `SKILL.md:79` as the two dontAsk statements to scope, gated on Backends U5. The task process, instead of halting on the card's "not yet" framing, grepped `launch.build_args` and `contracts.BACKEND_PINS` on `main`, confirmed U5's per-backend posture was already wired, and landed the scoping fix at commit `1e80684536130ec5a91ed19915c8f88ee6e77516`.

## Related
- Issue #16 (Backends U5, launch seam per backend), the precondition this task's card named.
- `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`, the sibling learning that motivated scoping these dontAsk statements in the first place.
