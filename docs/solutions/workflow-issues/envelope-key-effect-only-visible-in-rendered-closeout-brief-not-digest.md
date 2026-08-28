---
title: An optional field's presence in a parsed digest proves parsing, not that the rendered artifact carries it
date: 2026-08-28
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: medium
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [classify, closeout, contracts, brief-closeout-template, brief-local-merge-template]
applies_when:
  - "verifying whether an envelope-grammar addition (a new key such as learnings) actually reached its consumer through classify.parse_envelope -> digest -> Closeout brief"
  - "the digest JSON (digests/<task>.json) has the identical shape and keys whether the new field is empty or populated, e.g. envelope.learnings as an empty list versus a populated one"
  - "the only channel whose shape actually changes is the rendered Closeout brief (skills/relay/templates/brief-closeout.md's output), which gains or omits a whole section depending on the value"
  - "discharging CLAUDE.md's one-live-task-against-a-throwaway-target requirement for a change to the envelope grammar"
  - "two Closeout briefs from the same state directory are available to diff, one from a task that landed before the change and one from a task that landed after"
tags: [envelope, digest, closeout-brief, verification, live-proof, contract-seam, learnings-key, rendered-output]
---

# An optional field's presence in a parsed digest proves parsing, not that the rendered artifact carries it

## Context

Relay task T-7 (landed on `main` at `b6ec289`) added an optional `learnings:` key to the
return envelope a Task process prints as its final message. The key flows through four stages
before anyone downstream reads it: the Task process writes it inside a fenced `relay-envelope`
block; `classify.parse_envelope` (`skills/relay/scripts/relay/classify.py:127`) reads the block
into a Python dict, with `"learnings": _list_after(block, contracts.ENVELOPE_LEARNINGS_KEY)` at
`classify.py:146` and `ENVELOPE_LEARNINGS_KEY = "learnings"` defined at
`skills/relay/scripts/relay/contracts.py:58`; that dict is serialized to a per task digest JSON
file on disk; `closeout.py` reads the digest back and renders it into the Closeout brief, with
`"learnings": _bullets(envelope.get("learnings") or [])` at `closeout.py:174` feeding the
`$learnings` placeholder in `skills/relay/templates/brief-closeout.md:33` to `:35` ("Learnings
the task process reported:" followed by `$learnings`); and the Closeout process, a separate
headless `claude -p` run, reads that rendered brief text as its own prompt.

CLAUDE.md's standing rule is that a change to any contract between processes needs one live task
against a throwaway target before it counts as done, because a stubbed test harness's fake
producer (hand written fixture transcripts) and fake consumer (the parser written by the same
hands) agree by construction; `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
is the precedent, where an earlier stub-only suite of 353 green tests missed five real defects on
exactly this kind of seam. T-7's own landing task could not perform that proof itself, since the
task doing the landing is part of what needed proving, and its landing run separately hit a
different hazard on the way to `main`: `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`
documents the still-running self-hosted Runner halting on `BriefError: closeout template names
an unknown placeholder 'learnings'` because its own imported `closeout.py` was frozen at process
launch while the freshly merged `brief-closeout.md` template was read live from disk. That halt
was repaired by resuming with a fresh Runner process, not a code fix, since the code at head was
already correct; it is a different trap from the one this doc describes and is linked here only
because both sit inside the same T-7 story.

This session ran the live proof CLAUDE.md still owed: a fresh manifest at
`~/Documents/PhilAI/relay-proof/manifest.toml` against a throwaway repo at
`~/Documents/PhilAI/relay-proof/target` (local bare origin, nothing leaves the machine), with one
new ticket, also named T-4 in that target repo's own `tasks.md` and unrelated to any other T-4 in
this project, asking to fix `slugify()` in `toolkit/text.py` so it transliterates accented Latin
letters instead of destroying them. `relay run` landed it cleanly at commit `da54914`, with the
Closeout producing a real solutions doc in the target repo at commit `fab78e1`. While confirming
the `learnings:` key actually reached its consumer, the obvious first check, "open the digest
JSON and look for the key," turned out to prove nothing.

## Guidance

When you add an optional field to a pipeline that runs producer output through a parser into an
intermediate representation, then through a template renderer into a document a second process
reads, do not treat "the field appears in the parsed intermediate representation" as proof the
addition works. Verify against the final rendered artifact the downstream consumer actually
reads, and compare it against a case from before the change existed, or a case where the field is
genuinely empty.

Concretely, for this contract:
`~/.relay/e8e7297b9d90980f423133103dee2428767cd43d7ab1bed659d503c3585e7dd5/digests/T-4.json`
carries `"learnings": [...]` under `"envelope"` in the digest whether the list is empty or
populated, because `_list_after` (`classify.py:100`) always returns a list, `parse_envelope`
always sets the `"learnings"` key (`classify.py:146`), and JSON serializes an empty list and a
populated one under the same key name either way. Checking "does the digest have a `learnings`
key" would read as success in both the case where T-7 works and the case where the whole addition
silently did nothing.

The artifact that actually proves the wiring is the rendered Closeout brief. In the same run's
state directory, `briefs/T-4.closeout.md` (rendered after T-7's template change existed) has, at
line 34, the heading "Learnings the task process reported:" followed by the actual bulleted prose
the Task process wrote about a three-layer Unicode bug it found while implementing the fix. The
sibling `briefs/T-3.closeout.md`, from an earlier proof run before T-7's template change existed,
has no such section at all: its structure goes straight from "Blockers the task process
reported:" to "Denied tool calls the runner recorded in the task's transcript:", because
`closeout.py:174` and `brief-closeout.md:33` to `:35` did not exist yet at the time it was
rendered. That absence-versus-presence diff across two briefs in the same state directory, one
pre-change and one post-change, is the only artifact in the run that actually demonstrates the
field travels end to end.

## Why This Matters

A parser succeeding at extracting a field only proves the parser's regex or key lookup matched
something. It says nothing about whether the template that consumes the parsed value renders it
in a place and shape the downstream reader can use, and it says nothing about whether the value
is empty by design, a valid state for an optional field, per the guidance given to the Task
process at `brief-local-merge.md:70` ("Leave it empty on an ordinary run rather than filling it
by reflex"), versus empty because the wiring is broken. For an optional field specifically, both
states look identical at the parsed-JSON layer: `"learnings": []` either way. Only the rendered
template shows the difference, because the template either has a section with content in it, or
it renders the section with nothing under it, or, before the template existed, it has no section
at all.

Skipping this check and declaring the contract change proven from the digest alone would leave
exactly the kind of defect the stubbed-seams precedent describes: a stage where the suite passes
and a real value exists at the right key, while the actual consumer several stages downstream
never sees usable content, and nothing in the record would show it until a Closeout process
silently produced a document without the intended input.

A related but distinct guard already exists one layer earlier in this same pipeline:
`docs/solutions/logic-errors/digest-key-guard-regex-blind-to-suffixed-and-fallback-reads.md`
documents a static test that greps `closeout.py`'s and `run.py`'s source for digest-key reads to
prove a key `classify.py` sets is actually referenced by name somewhere downstream. That guard
answers "does the source code mention this key at all," at test time, with no data flowing. It is
necessary but not sufficient: this doc's check answers a different question, "does a real run's
rendered output actually carry the key's content," which only a live run with a before-and-after
diff can show. Passing the static guard does not mean the dynamic proof in this doc would also
pass, and the two should be treated as complementary layers of the same contract, not
substitutes for each other.

## When to Apply

Apply this whenever a change adds or modifies a field that is:

- **optional**, so its absence or emptiness is a valid, expected state and not itself a failure
  signal, and
- **multi-stage**, meaning it passes through more than one transformation (parse, serialize to an
  intermediate store, render into a template, get read by a separate process or person) before it
  reaches whoever actually consumes it.

In Relay's own architecture this covers any change to: the envelope grammar `parse_envelope`
reads (`classify.py`), the closeout digest keys `closeout.py` pulls from the envelope, the brief
templates in `skills/relay/templates/`, or any other spot where a value crosses a process
boundary via a rendered document rather than a direct function call. It is the same category
CLAUDE.md already names for its live-run rule (envelope grammar, closeout terminal line, brief
template, halt record, classify digest keys); this guidance is the narrower rule for verifying
the specific case of an optional field within that category, where "the key exists" is a weaker
check than usual because presence does not distinguish success from silent no-op.

It does not apply to required fields, where the parser or a schema check will already fail loudly
on absence, or to single-stage changes with no intermediate serialization or template step in
between, where inspecting the one output directly is already the end-to-end check.

## Examples

**Before (insufficient check):** Read `digests/T-4.json`, see `"learnings": ["...bug
writeup..."]` under `"envelope"`, conclude the `learnings:` key change works. This check cannot
distinguish a working pipeline from one where `closeout.py:174` never ran or `brief-closeout.md`
never got the `$learnings` placeholder added, because the digest is produced by `classify.py`
alone, one stage before either of those would matter.

**After (the check that actually proves it):** Diff the rendered Closeout brief against a brief
rendered before the change. `briefs/T-3.closeout.md` (pre-T-7, no `learnings:` in the envelope
grammar yet) has no "Learnings the task process reported:" section at all. `briefs/T-4.closeout.md`
(post-T-7) has that section at line 34 with real bulleted content, because the Task process
reported a genuine finding: the straightforward `NFKD` plus ASCII-encode approach to accent
stripping deletes rather than separates non-ASCII characters (merging adjacent words) and also
leaks unrelated symbols like the trademark sign and roman numerals, because `NFKD` is a
compatibility decomposition, not a plain accent-stripping one; the corrected per-character
transliteration approach then hit a third bug caught only in code review, that a per-codepoint
loop with no upfront `NFC` normalization splits words when input arrives as pre-decomposed `NFD`
Unicode, which is macOS/APFS's default form (`"cafés"` becomes `"cafe-s"` instead of `"cafes"`).
The Closeout process used that reported arc verbatim to write a real solutions doc in the target
repo (`relay-proof/target` commit `fab78e1`). The section existing, with that specific content,
in one brief and not the other, is the proof; the presence of a `learnings` key in either digest
JSON was never going to show it.

## Related

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the parent rule this doc discharges: any change to a contract between processes needs one
  live task against a throwaway target. This doc adds that satisfying the rule for an optional
  field also requires diffing rendered output, not just inspecting the parsed digest.
- `docs/solutions/logic-errors/digest-key-guard-regex-blind-to-suffixed-and-fallback-reads.md`
  is a static, source-level guard for the adjacent question, whether a digest key is referenced
  by name downstream at all. It operates one layer earlier than this doc's dynamic, rendered-output
  check and is complementary, not a substitute.
- `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`
  documents the halt T-7's own landing run hit on the way to `main`, a different trap (a live
  template read against a frozen module) in the same T-7 story. This doc covers what happened
  after landing, verifying the feature actually works.
- `docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`
  is a different verification trap in the same broad theme, a self-hosted Runner unable to see
  effects of code its own tasks land because of stale module imports rather than an opaque
  intermediate representation. Related, not the same mechanism.
