---
title: A code-review finding said backend readiness had three failure shapes; the source function has four, and trusting the finding's framing would have shipped a documentation gap
date: 2026-08-30
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: medium
root_cause: missing_validation
resolution_type: documentation_update
related_components: [manifest, skill-docs]
symptoms:
  - "the P2 finding driving relay task 39 (skills-relay-cli-114-document-readiness-remediation) described _backend_readiness_errors as emitting three preflight failure shapes"
  - "manifest.py's _backend_readiness_errors (skills/relay/scripts/relay/manifest.py:360-383) actually has four branches: missing binary, plugin-list subprocess failure (OSError/SubprocessError), no readable plugin, and plugin below the version floor"
  - "the fourth branch, the plugin-list subprocess failure, had no remediation row in the finding's framing"
tags: [relay-task, ce-code-review, finding-verification, backend-readiness, manifest-py, re-derive-from-source]
---

# A code-review finding said backend readiness had three failure shapes; the source function has four, and trusting the finding's framing would have shipped a documentation gap

## Problem

Relay task 39 asked to document remediation for backend executable and plugin preflight
failures in `skills/relay/SKILL.md`. The tracker finding driving the task described the
failure surface as three shapes. `ce-plan`'s feasibility-reviewer re-derived the enumeration
from the cited function instead of accepting the finding's count, and found a fourth branch
the finding had not named.

## Symptoms

- The finding text (`skills-relay-cli-114-document-readiness-remediation`) implied three
  preflight failure shapes for a selected backend.
- `_backend_readiness_errors` in `skills/relay/scripts/relay/manifest.py` (lines 360-383) has
  four: binary missing from `PATH`, the plugin-list subprocess itself failing
  (`OSError`/`SubprocessError`), no readable plugin at or above the version floor, and a plugin
  below the version floor.
- Had the fourth branch, the plugin-probe subprocess failure, gone undocumented, the shipped
  remediation table would have had a silent gap in exactly the class of operator guidance the
  task existed to add.

## What Didn't Work

- Taking the finding's enumeration at face value would have produced a plausible-looking,
  incomplete remediation table: three rows, three error strings matched, and no signal that a
  case was missing, because the doc would still read as complete to anyone who did not
  independently check the source function.

## Solution

Before writing remediation documentation for a validation function, read the function itself
and enumerate its actual branches rather than the branch count implied by the finding or brief
that requested the doc. The final `skills/relay/SKILL.md` remediation table (commit 24806c3)
carries all four rows, each naming a concrete per-backend command (e.g. `claude plugin list`,
`codex plugin list`, `grok plugin list --json` for the plugin-probe-failure row).

## Why This Works

A tracker finding is a compressed, human- or agent-authored summary written at some earlier
point against some earlier reading of the code; it is not the code. `_backend_readiness_errors`
is the actual contract, and its branch count is a fact about the tree, not about the finding.
Re-deriving the enumeration from the cited function is strictly cheaper than shipping a gap and
discovering it later from an operator hitting the undocumented case.

## Prevention

- When a finding or brief cites a specific function as the reason for a documentation or fix
  task, read that function and enumerate its actual branches/cases before writing, rather than
  reusing the count or list the finding gives.
- Treat a discrepancy between the finding's framing and the source as a signal to widen the
  fix, not a reason to distrust the task.

## Related Issues

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the neighboring case for the same underlying principle: a stand-in for the real thing
  (there a stub, here a finding's summary) agrees with itself by construction and only the real
  artifact (a live run, the source function) exposes the gap.
