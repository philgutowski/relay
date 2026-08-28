---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-28
depth: lightweight
---

# Refresh: stubbed-seams doc scopes its rule to message contracts only

## Problem Frame

The Prevention section of
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
opens with an enumeration of what a live run must be run against: "the envelope grammar and
`parse_envelope`; `COMPOUND_TERMINAL_LINES` and `closeout.parse`; the brief templates and the
adapters; `_Halt` and the record the summary reads; and the classify digest keys." Every item on
that list is a message contract between two processes or modules that a stub could agree with by
construction. A reader applying the rule as written would conclude a timeout constant — not a
message contract at all — is out of scope for "run a live task first."

`docs/solutions/logic-errors/push-inherited-the-read-sized-git-timeout-so-the-pre-push-gate-outran-it.md`
(T-5) is the counterexample already landed in this repo: no message contract changed, and the
defect (a push inheriting `gitread`'s 120-second read timeout instead of a bound sized against
the gate) was only found by a live run, because `tests/_repo.py`'s fixture repositories carry no
`pre-push` hook, so every push in the suite returns in milliseconds. That doc's own Related
section already names the relationship in prose ("this defect is the same lesson arriving from a
different direction... this one is not a contract at all: it is an environmental fact"), but the
stubbed-seams doc's own Prevention section, the one a future session actually applies as a
checklist, was never amended to say so.

## Scope

**In scope:**
- Amend the Prevention section of the stubbed-seams doc to add the amendment: a stubbed seam
  agrees by construction, and a stubbed subprocess is free by construction; cost is the second
  thing a stub cannot produce, and any runner bound sized against real work is unproven until a
  live run pays it.
- Point the amendment at the T-5 push-timeout doc as the concrete counterexample (no contract
  changed, fixtures create hookless repos, every push in the suite is free).
- Cross-link: add the reverse pointer in the T-5 doc's Related section if it does not already
  name the stubbed-seams doc as covering this specific amendment (it currently cites the
  stubbed-seams doc but frames the relationship only in its own prose, not as an update to that
  doc). Check current text before editing — do not duplicate an existing sentence.
- Frontmatter: no changes needed. `tags` and `symptoms` describe defects found, not the
  Prevention rule's scope; the amendment does not add a new symptom or defect.

**Out of scope:**
- Rewriting the existing closed-list enumeration of message contracts. It stays accurate for what
  it enumerates; the fix is adding the missed dimension, not correcting the existing one.
- Any code change. This is a documentation-only refresh; `contracts.py`, `gitwrite.py`, and the
  rest of the runner package are untouched.
- Auditing other `docs/solutions/` entries for the same gap. The task names exactly one doc and
  one counterexample; a broader audit is a separate task if wanted.

## Assumptions

- "Refresh" means editing the existing doc in place, not creating a new solutions doc. The task
  explicitly names the file and section to amend and gives the exact amendment language to land.
- The amendment lands as a new paragraph inside the existing Prevention section (after the
  paragraph that states the closed list, since it's answering "is that list complete"), not as a
  new top-level section, matching how the doc's other Prevention caveats (e.g. "One exception:
  when the contract change is to the Runner package itself...") are already structured as
  same-section paragraphs.
- This is documentation-only work with no test surface of its own; the project gate (the full
  unittest suite) still must pass, since Relay's gate has no documentation-only bypass, and
  passing here just confirms the doc edit touched nothing importable.

## Current State (research)

Confirmed by reading both docs in full:

- `stubbed-seams-agree-by-construction-...md` Prevention section, first paragraph, states the
  closed list of five contracts quoted above and ends: "The stub cannot, by construction." That
  sentence is the one a reader would read as the rule's full scope.
- The same doc's Related Issues section already cites
  `push-inherited-the-read-sized-git-timeout-so-the-pre-push-gate-outran-it.md`... actually it
  does not — checked: the stubbed-seams doc's Related Issues section (5 entries) does not
  currently link to the T-5 push-timeout doc at all, since the push-timeout doc landed after it
  chronologically (2026-08-27, both same day, push-timeout doc's Related section links backward
  to stubbed-seams but the reverse link was never added).
- The T-5 push-timeout doc's Related section, entry 1, already says: "this defect is the same
  lesson arriving from a different direction... Both close the same way: the suite proved
  nothing about the case, and only a live run against a real target could." This is the raw
  material for the amendment but was never folded back into the stubbed-seams doc's own
  Prevention section, which is the one a future session skims as a checklist rather than reading
  the older doc's Related Issues.

## Design

### 1. Amend the Prevention section of the stubbed-seams doc

File: `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`

Insert a new paragraph immediately after the existing first Prevention paragraph (the one
ending "...The stub cannot, by construction.") and before the "One exception" paragraph. Content
(prose, matching the doc's no-dash convention):

> **A stubbed seam agrees by construction, and a stubbed subprocess is free by construction.**
> The list above is every message contract this run found broken, but cost is the second thing a
> stub cannot produce, and it breaks a different kind of bound. `push_timeout_for` in
> `docs/solutions/logic-errors/push-inherited-the-read-sized-git-timeout-so-the-pre-push-gate-outran-it.md`
> is the counterexample: no contract in the list above changed, and a live run was still the only
> instrument that found the defect, because `tests/_repo.py`'s fixture repositories carry no
> `pre-push` hook, so every push in the suite returns in milliseconds. Any runner bound sized
> against real work, a subprocess timeout, a retry budget, a rate limit, is unproven until a live
> run pays the cost the stub was never asked to pay.

Keep this scoped to the amendment; do not restate the T-5 doc's own Solution or Why This Works
sections, only point at the mechanism (`push_timeout_for`) and the fixture fact (hookless repos)
that make it the counterexample.

### 2. Cross-link from the Related Issues section

Same file, Related Issues section: add one bullet naming the T-5 doc, since it currently has no
forward link to it at all. Place it first (chronologically and thematically closest), phrased to
point at the amendment rather than duplicate the T-5 doc's own account:

> - `docs/solutions/logic-errors/push-inherited-the-read-sized-git-timeout-so-the-pre-push-gate-outran-it.md`
>   is the counterexample behind this doc's Prevention amendment: no message contract changed, and
>   a live run was still the only way to find it, because the fixtures never pay the cost a real
>   `pre-push` hook adds to a push.

### 3. Verify the reverse link already exists and is not duplicated

`push-inherited-...md`'s Related section entry 1 already links back to the stubbed-seams doc. Do
not add a second link there; confirm during implementation that its existing sentence still
reads correctly given the new amendment exists (no edit needed there unless the existing prose
now reads as stale, which a plain read during implementation should confirm one way or the
other).

## Test Scenarios

Documentation-only change; no new unit tests apply. Verification is by reading, not by test:

1. The amended Prevention section still reads as one coherent section (no orphaned reference, no
   broken markdown).
2. The new Related Issues bullet resolves to a real file path.
3. Full project gate (`python3 -m unittest discover -s tests`) passes unchanged, since nothing
   importable is touched.

## Files

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  — add the Prevention amendment paragraph and the Related Issues cross-link.

## Risks

- **Duplicating what the T-5 doc's Related section already says**, producing two slightly
  different accounts of the same fact that could drift. Mitigation: the amendment here states the
  rule and points at the mechanism by name (`push_timeout_for`, hookless fixtures) rather than
  re-narrating the T-5 doc's Problem/Solution sections in full.
- **Placement inside Prevention drifting the closed-list sentence's meaning.** Mitigation: the
  new paragraph is explicitly additive ("cost is the second thing... a different kind of bound")
  rather than editing the existing enumeration, so the original sentence stays true for what it
  enumerates.

## Verification

Run the full suite from repo root: `python3 -m unittest discover -s tests`. Confirm the count and
pass/fail status match the pre-change baseline (documentation-only change should not move the
count).
