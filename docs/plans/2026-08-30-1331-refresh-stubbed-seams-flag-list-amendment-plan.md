---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-30
depth: lightweight
---

# Refresh: stubbed-seams doc's Prevention needs the third family member

## Problem Frame

The Prevention section of
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
carries a two-member family so far: "a stubbed seam agrees by construction" (the original
message-contract list) and "a stubbed subprocess is free by construction" (the cost amendment
landed in `ba3c845`, issue #14). The U1 backend spike
(`docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`, written
2026-08-28) produced a third member that is out of scope of both existing clauses: a documented
flag list is agreeable by construction. Grok's own `--help` output advertises
`--permission-mode dontAsk`, the same spelling Claude Code uses as its non-bypass headless
posture, and reasoning from that shared vocabulary to shared semantics went unchallenged until a
live Task under that mode had every tool call cancelled. No stub is involved in that case: no
message contract changed, no bound sized against real work was tested, and the verifier was a
vendor help page rather than a fixture. The doc's Prevention section, read today, would not flag
that case as in scope, the same gap issue #14 found and fixed for the cost dimension.

This is the same shape as issue #14, filed by a Related Docs Finder against this same doc for
the same class of over-narrow Prevention scope, whose amendment landed in `ba3c845`. A third
amendment follows that precedent's pattern and placement.

## Scope

**In scope:**
- Amend the Prevention section of the stubbed-seams doc to add the third family member: a
  documented flag list is agreeable by construction, pointing at the grok `dontAsk` case as the
  concrete counterexample (shared spelling, opposite behavior, only a live run with both a
  refusal and a success caught it).
- Generalize the Prevention section's opening live-run instruction from "after any change to a
  cross process contract" to also cover "and before trusting any launch-time claim about an
  external tool", since the grok case shows the instrument applies before a launch posture is
  pinned, not only after a contract changes.
- Add the forward cross-link from the stubbed-seams doc's Related Issues section to the grok
  doc (the grok doc already links back to the stubbed-seams doc and names itself "the third";
  the stubbed-seams doc has no forward link yet).
- Frontmatter: no changes. `tags` and `symptoms` describe defects this doc's own incident found;
  the amendment documents a different incident's lesson, the same treatment issue #14 gave the
  cost amendment.

**Out of scope:**
- Rewriting the existing two-member enumeration. Both stay accurate for what they describe; the
  fix is adding the missed third dimension.
- Any code change. This is documentation-only; `contracts.py` and the rest of the runner package
  are untouched (the grok fix itself already landed on `worktree-issue-16-alt-cli-backends`,
  per that doc's own account).
- Auditing other `docs/solutions/` entries for the same gap. The task names exactly one doc and
  one counterexample.

## Assumptions

- "Refresh" means editing the existing doc in place, matching the issue #14 precedent, not
  creating a new solutions doc.
- The amendment lands as a new paragraph inside the existing Prevention section, immediately
  after the second family member's paragraph (the `ba3c845` cost amendment) and before the "One
  exception" paragraph, continuing the family list in place rather than starting a new
  subsection. This mirrors where the second member was inserted relative to the first.
- This is documentation-only work with no test surface of its own; the project gate (the full
  unittest suite) still must pass unchanged, since Relay's gate has no documentation-only
  bypass.

## Current State (research)

Confirmed by reading both docs in full:

- The stubbed-seams doc's Prevention section currently reads, in order: (1) the closed list of
  five message contracts and the "after any change to a cross process contract" instruction,
  (2) the `ba3c845` cost amendment ("a stubbed seam agrees by construction, and a stubbed
  subprocess is free by construction"), (3) the Runner-package self-hosting exception, (4) the
  "fixture and parser written together" smell, (5) "keep the raw form in the record", (6) "two
  seams this run did not reach."
- The stubbed-seams doc's Related Issues section (6 entries, confirmed by reading) does not
  currently link to the grok `dontAsk` doc.
- The grok doc's own Related section (entry 2, confirmed by reading) already links to the
  stubbed-seams doc and states explicitly: "Its Prevention section already carries two members
  of this family, a stubbed seam agrees by construction and a stubbed subprocess is free by
  construction; a documented flag list is agreeable by construction is the third." This is the
  raw material for the amendment, written at the time the grok doc was compounded but never
  folded back into the stubbed-seams doc's own Prevention section.
- Issue #14's landed amendment (`ba3c845`) is the direct structural precedent: it inserted its
  new paragraph right after the original closed-list paragraph and added one Related Issues
  bullet pointing at the counterexample doc, phrased to point at the amendment rather than
  restate the counterexample doc's own account.

## Design

### 1. Amend the Prevention section of the stubbed-seams doc

File: `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`

**1a. Broaden the opening instruction.** In the first Prevention paragraph, change:

> Run one live task against a throwaway target after any change to a cross process contract.

to:

> Run one live task against a throwaway target after any change to a cross process contract,
> and before trusting any launch-time claim about an external tool.

Leave the rest of that paragraph (the closed list of five contracts, "The stub cannot, by
construction.") unchanged; it stays accurate for what it enumerates.

**1b. Insert the third family member.** Immediately after the existing `ba3c845` paragraph (the
one starting "A stubbed seam agrees by construction, and a stubbed subprocess is free by
construction.") and before the "One exception" paragraph, insert:

> **A documented flag list is agreeable by construction, the third member of this family.** An
> assumption derived from a CLI's own `--help` output and then checked against that same help
> output cannot fail, because the claim and its verifier share one source. Nothing about this
> case involves a stub: no message contract changed, no bound sized against real work was
> tested, and the verifier was a vendor help page rather than a fixture.
> `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md` is the
> counterexample: Grok's `--help` advertises `--permission-mode dontAsk`, the same spelling
> Claude Code uses as its own non-bypass headless posture, and reading that shared vocabulary as
> shared semantics stood unchallenged in a plan's KTD6 and Assumptions until U1's spike launched
> a real Task under it and watched every tool call get cancelled with no human present to have
> cancelled anything. A launch-time posture is unproven until a live run has observed both a
> refusal and a success; a refusal alone would have passed a mode that refuses everything.

Keep this scoped to the amendment: point at the mechanism (the shared `dontAsk` spelling, the
observed cancellation, the refusal-and-success pairing) rather than restating the grok doc's own
Problem, Guidance, or Examples sections in full.

### 2. Cross-link from the Related Issues section

Same file, Related Issues section: add one bullet naming the grok doc, since it currently has
no forward link to it. Place it alongside the other workflow-issues entries (the doc already
links to `headless-dontask-blocks-claude-dir-edits.md` there), phrased to point at the
amendment rather than duplicate the grok doc's own account:

> - `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md` is the
>   counterexample behind this doc's third Prevention amendment: no message contract changed and
>   no stub was involved, and a live run observing both a refusal and a success was still the
>   only way to catch a launch-time posture that a vendor's own `--help` output made look safe.

### 3. Verify the grok doc's existing reverse link is not duplicated

The grok doc's Related section entry 2 already links back to the stubbed-seams doc and names
the amendment ("a documented flag list is agreeable by construction is the third"). No edit
needed there; confirm during implementation that its existing sentence still reads correctly
now that the amendment exists (a plain read should confirm it does, since it was written
anticipating exactly this landing).

## Test Scenarios

Documentation-only change; no new unit tests apply. Verification is by reading, not by test:

1. The amended Prevention section reads as one coherent section in order: closed list (now with
   the broadened instruction), cost amendment, flag-list amendment, Runner-package exception,
   the two remaining Prevention paragraphs. No orphaned reference, no broken markdown.
2. The new Related Issues bullet resolves to a real file path.
3. Full project gate (`python3 -m unittest discover -s tests`) passes unchanged, since nothing
   importable is touched.

## Files

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  — broaden the opening Prevention instruction, add the third family-member paragraph, add the
  Related Issues cross-link.

## Risks

- **Duplicating what the grok doc's Related section already says**, producing two slightly
  different accounts of the same fact that could drift. Mitigation: the amendment states the
  rule and points at the mechanism (shared spelling, observed cancellation, refusal-and-success
  pairing) by name rather than re-narrating the grok doc's Problem/Guidance sections in full.
- **Placement inside Prevention drifting the existing two paragraphs' meaning.** Mitigation: the
  new paragraph is explicitly additive ("the third member of this family") rather than editing
  either existing enumeration, so both stay true for what they describe.
- **Broadening the opening instruction reads as overreach beyond the task's scope.** Mitigation:
  the task text itself states this generalization explicitly ("It also generalizes the doc's
  live-run instrument beyond... to..."), so it is in scope, not an invented addition.

## Verification

Run the full suite from repo root: `python3 -m unittest discover -s tests`. Confirm the count
and pass/fail status match the pre-change baseline (documentation-only change should not move
the count).
