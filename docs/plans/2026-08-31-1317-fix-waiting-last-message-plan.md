---
title: Waiting Last Message Brief Warning and Finding - Plan
type: fix
date: 2026-08-31
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Waiting Last Message Brief Warning and Finding - Plan

## Goal Capsule

**Objective:** an operator reading a halted task's record learns, without opening the transcript,
that the task ended its turn on a promise to resume rather than a finished turn, on every future
occurrence of this pattern, whatever the record's own halt class ends up being.

**Means:** name background completion notifications explicitly in the brief's foreground rule
(KTD1), and detect waiting language in the task's last message as a classifier finding attached to
the record regardless of halt class (KTD2, KTD6's closed set).

**Authority hierarchy:** `CLAUDE.md` (KTD6, closed halt-class set) > `CONCEPTS.md` (Halt class,
Cause line, Finding) > this plan.

**Stop conditions:** the full suite passes; `tests/test_brief.py`'s pinned phrase test still
passes with its new assertion; `tests/test_contracts.py`'s generic `FINDING_CLASSES`/`LINE_CLASSES`
coverage tests pass with the new class included.

**Execution profile:** single session, no rollout steps, no monitoring.

**Tail ownership:** this plan's tail is a passing suite; landing and merge stay with the caller.

---

## Product Contract

### Summary

Round six task #35 committed its fix, launched the unittest suite as a background Bash call, and
ended its turn on "Standing by for the test suite's completion notification." Headless `claude -p`
gives that process no next turn, so the notification never arrived, the process exited with no
envelope, and the runner classified the halt as `unclean_exit` from the plan file it also left
untracked on `main` — a true but downstream symptom, not the mechanism that caused the halt. Two
follow-ups from the task text: sharpen the brief's existing foreground rule to name background
completion notifications specifically, and add a classifier finding for a last message that reads
as waiting so the record's Cause line and findings name the real mechanism.

### Problem Frame

This is the second live occurrence of the class `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md`
documents. That doc's fix, the shared "Rules for the whole session" foreground paragraph pinned by
`tests/test_brief.py:122`, was already live when task #35 ran on 2026-08-30 and still did not stop
this exact shape: a background gate command plus a "standing by" ending. The existing rule says
"never background and end your turn to wait for it" but does not say plainly that a completion
*notification* is exactly the thing that will not arrive, nor does it forbid the *shape* of ending
a turn on a promise to resume once phrased as "standing by" rather than as an explicit background
launch. The classifier side has no way to say this either: `classify.classify()` records the halt
class the transcript or the git tree hands it (KTD6, `HALT_UNCLEAN_EXIT` from a dirty tree,
`HALT_NO_ENVELOPE` from a missing envelope) but nothing reads the last message's own language.

### Requirements

**Brief wording**

- R1. The shared "Rules for the whole session" foreground paragraph in both
  `skills/relay/templates/brief-local-merge.md` and `skills/relay/templates/brief-pr-terminal.md`
  states plainly that a background command's completion notification does not survive the final
  turn under `claude -p`, and separately forbids ending a turn on a promise to resume ("standing
  by", "will check back", "once it finishes") even when nothing was technically backgrounded.
- R2. `tests/test_brief.py`'s existing pinned test (`test_the_brief_forbids_backgrounding_work_and_ending_the_turn`)
  gains an assertion for the added phrase, in the same style as its two existing `assertIn` checks,
  so a future template rewrite cannot silently drop the addition.

**Classifier finding**

- R3. `classify.classify()` detects a last message that reads as waiting on work that will not
  resume ("standing by", "will resume", "once the run/suite/build finishes", and close variants)
  and appends a finding to the record, independent of the record's own halt class, following the
  finding-only pattern `RUNNER_SELF_KILL` already establishes for KTD6.
- R4. The finding fires only when the run did not end in a complete envelope — a task that reports
  `standing by` mid-message but still delivers a complete envelope afterward is not exhibiting this
  failure, and the finding must not fire for it.
- R5. The new finding class renders a Cause-line-style sentence through the existing
  `contracts.HALT_LINES` / `summary.cause_line` machinery, so it appears in the closeout brief's
  "Other findings" bullets and the run summary's per-task findings list exactly like every other
  finding, without new rendering code.

### Scope Boundaries

**In scope:** the brief template wording, its pinned test, the classifier detection and its
`contracts.py` finding class, and the classifier's own test coverage plus a new fixture transcript.

**Out of scope (Deferred to Follow-Up Work):** changing which `halt_class` a record receives when
this finding fires (task text's follow-up says the Cause line should name the real mechanism
*alongside* the existing class, not replace it — KTD6 forbids a new halt class here). Also out of
scope: any pre-flight check that runs *during* the task session to catch the pattern live (the task
text says "pre-flight or classifier finding," and a classifier-side finding satisfies the request
without touching the task process's own tooling, which the runner does not control mid-session
anyway).

### Sources

- `logs/35.stdout.log` in state dir `926c93f0...`, cited directly in the task text.
- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md` —
  the existing fix this plan sharpens; documents the first occurrence and the brief rule it
  produced.
- `CLAUDE.md`, "Halt classes are a closed set in `contracts.py` (KTD6)."
- `skills/relay/scripts/relay/contracts.py:323`-`380` — `HALT_CLASSES` and the `RUNNER_SELF_KILL`
  finding this plan's KTD2 mirrors; `contracts.py:395`-`413` — `FINDING_CLASSES`, `LINE_CLASSES`,
  and `HALT_LINES`.
- `skills/relay/scripts/relay/classify.py:395`-`447` — where `HALT_NO_ENVELOPE`'s own finding is
  appended today, the closest existing precedent for a last-message-derived finding.

---

## Planning Contract

### Key Technical Decisions

**KTD1. Extend the existing shared paragraph rather than adding a new rule block.**
Both templates already carry one "Rules for the whole session" paragraph on this exact hazard
(`brief-local-merge.md:19`-`22`, identical in `brief-pr-terminal.md`), pinned by one test. Adding a
second, separate paragraph would leave two rules about the same mechanism to keep in sync by hand.
The paragraph gains two sentences: one naming the completion-notification case specifically (the
concrete shape task #35 hit — a foreground-looking wait that is actually still a background launch
underneath), and one forbidding the "standing by" / "will resume" ending shape on its own terms,
independent of whether something was literally backgrounded.

**KTD2. A finding-only class, not a new halt class (KTD6).**
`CLAUDE.md` requires a new outcome to be "a finding attached to a record, not a new class, unless
the plan is amended." This plan does not amend KTD6. `RUNNER_SELF_KILL` in `contracts.py:352`-`355`
is the direct precedent: a finding whose own comment says "the record's own halt_class stays
[whatever it already is], this just says why." The new class,
`WAITING_LAST_MESSAGE = "waiting_last_message"`, follows the same shape: added to
`FINDING_CLASSES` and `LINE_CLASSES` but never to `HALT_CLASSES`, so `test_contracts.py`'s existing
generic coverage tests (`tests/test_contracts.py:63`-`86`) pick it up without a new assertion.

**KTD3. Detect inside `classify.classify()`, not as a separate scan like `scan_self_kill`.**
`scan_self_kill` is a separate function because it reads the raw stdout log at a path that survives
even when `transcript_path` was never written back to state (round six #40's crash case) — a
different evidence source for a different reason. This detection has no such constraint: `last_text`
is already fully assembled inside `classify()` before the envelope is parsed
(`classify.py:410`-`417`), so the regex runs there, once, against the same string
`result["last_message"]` is truncated from. This keeps the finding available inside `run.py`'s
digest before any halt is raised (`run.py:384`-`391` writes `digest["findings"]` to the record via
`store.upsert` before the `unclean_exit` halt at `run.py:440` is even reached), which is exactly
what R5 needs: the finding survives to the record no matter which halt class the run loop assigns
afterward.

**KTD4. Match on the full `last_text`, gate on envelope completeness, not on `result["last_message"]`.**
`result["last_message"]` is head-truncated to `LAST_MESSAGE_CHARS` (200 characters); a waiting
phrase could sit past that cut the way `docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md`'s
sibling comment on `last_message_tail` already warns about for a different field. The regex runs
against the untruncated `last_text` local variable. The finding fires only when
`not envelope or envelope["status"] != contracts.ENVELOPE_STATUS_COMPLETE` (R4) — a task that
prints "standing by" language mid-message but still lands a complete fenced envelope afterward did
not exhibit the failure, and flagging it would be a false positive with no operator action to take.

### Assumptions

- The regex is illustrative-phrase-bounded (mirrors task text's three named phrases plus close
  variants), not a general sentiment classifier. A future occurrence phrased in an unanticipated
  way will not be caught; that is an acceptable false-negative rate for a best-effort diagnostic
  finding, the same posture `scan_self_kill`'s own docstring states for its command match.
- No existing `docs/solutions/` entry needs updating as part of this plan. The existing entry
  (`headless-turn-end-is-exit-backgrounded-command-is-killed.md`) documents the *first* occurrence
  and its fix; this plan's closing envelope may name the second occurrence as a learning worth a
  future `ce-compound` pass, but authoring that pass is not part of this implementation plan.

---

## Implementation Units

### U1. Sharpen the brief's foreground rule

**Goal:** name background completion notifications and the "standing by" ending shape explicitly
in both brief templates, and pin the addition with a test assertion.

**Requirements:** R1, R2

**Dependencies:** none

**Files:**
- `skills/relay/templates/brief-local-merge.md`
- `skills/relay/templates/brief-pr-terminal.md`
- `tests/test_brief.py`

**Approach:**
1. In both templates, extend the existing paragraph at `brief-local-merge.md:19`-`22` /
   `brief-pr-terminal.md:19`-`22` (identical text in both) with two sentences appended after the
   existing three: one stating that a background command's completion notification does not
   survive the final turn, one forbidding ending a turn on a promise to resume ("standing by",
   "will check back", "once it finishes") independent of whether the command was literally
   backgrounded. Keep the paragraph's existing three sentences unchanged; add, do not rewrite.
2. In `tests/test_brief.py`, extend
   `SkillPinning.test_the_brief_forbids_backgrounding_work_and_ending_the_turn` (currently two
   `assertIn` calls, `tests/test_brief.py:126`-`127`) with one more `assertIn` for a literal
   substring drawn from the new sentences (KTD1), following the same per-template loop shape.

**Patterns to follow:** the existing paragraph and its test at `tests/test_brief.py:122`-`127` —
same style: state the mechanism in the process's own terms, name the concrete tempting shape, pin
a literal substring.

**Test scenarios:**
- Both rendered templates (`local_merge`, `pr_terminal`) contain the new pinned phrase (extends
  the existing parametrized `each_template()` loop).
- The existing two assertions (`"foreground"`, `"ending your turn is exiting"`) still pass
  unchanged — regression coverage for not breaking the prior pin while adding to it.

**Verification:** `python3 -m unittest test_brief` from `tests/` passes, including the extended
`SkillPinning` test.

---

### U2. Classifier finding for a waiting last message

**Goal:** `classify.classify()` appends a `waiting_last_message` finding when the task's last
message reads as waiting on work that will not resume and the run did not end in a complete
envelope, rendered through the existing Cause-line machinery.

**Requirements:** R3, R4, R5

**Dependencies:** none (independent of U1)

**Files:**
- `skills/relay/scripts/relay/contracts.py`
- `skills/relay/scripts/relay/classify.py`
- `tests/test_classify.py`
- `tests/test_summary.py`
- `tests/test_contracts.py` (only if the generic coverage tests need no edit — verify first; likely
  no change needed per KTD2)
- `tests/fixtures/transcripts/_make.py`
- `tests/fixtures/transcripts/waiting_last_message.jsonl` (generated, committed)
- `tests/fixtures/transcripts/waiting_then_complete.jsonl` (generated, committed; R4's gating case)

**Approach:**
1. In `contracts.py`, add `WAITING_LAST_MESSAGE = "waiting_last_message"` near
   `RUNNER_SELF_KILL` (`contracts.py:352`-`355`), with a comment in the same voice explaining it is
   finding-only and names the mechanism the task text asked for. Add it to `FINDING_CLASSES`
   (`contracts.py:395`-`404`) and to the `LINE_CLASSES` tuple's finding half
   (`contracts.py:406`-`408`). Add one `HALT_LINES` entry rendering from `{last_message}` (the same
   field `HALT_NO_ENVELOPE`'s line already uses), e.g. "ended the turn waiting on background work
   that does not resume headless: {last_message}".
2. In `classify.py`, add a module-level compiled regex (near `_KILL_COMMAND_RE`,
   `classify.py:149`-`159`) matching the task text's three named phrasings and close variants,
   case-insensitive: "standing by", "will resume" / "will check back", "once (the|this|it) (run|
   suite|build|driver) (finishes|completes|is done)". Keep the pattern list bounded and commented,
   the same discipline `_KILL_COMMAND_RE`'s own comment applies to its three command names.
3. After `envelope = parse_envelope(last_text) if last_text else None` (`classify.py:416`-`417`)
   and before the halt-class precedence block, add: when `last_text` is set and
   (`envelope` is falsy or its `status` is not `contracts.ENVELOPE_STATUS_COMPLETE`) and the regex
   matches `last_text`, append a finding `{"class": contracts.WAITING_LAST_MESSAGE, "last_message":
   result["last_message"]}` to `result["findings"]`. This runs once, ahead of the existing
   precedence `if/elif/else` (`classify.py:423`-`447`), so it applies uniformly whether the record
   ends up `HALT_NO_ENVELOPE`, `HALT_BLOCKED_ENVELOPE`, or (via the git-tree check downstream in
   `run.py`) `HALT_UNCLEAN_EXIT`.
4. Regenerate fixtures: in `tests/fixtures/transcripts/_make.py`, add a `waiting_last_message()`
   builder mirroring `no_envelope()` (`_make.py:179`-`180`) but with a new `WAITING_TEXT` constant
   modeled on task #35's own last message ("Standing by for the test suite's completion
   notification."), and register it in the file's write list alongside the existing calls
   (`_make.py:302`). Run the generator and commit the resulting `.jsonl`.
5. In `tests/test_summary.py`, add a `contracts.WAITING_LAST_MESSAGE` entry to `FINDING_ROWS`
   (`test_summary.py:127`-`160`), a finding dict carrying `last_message` plus the raiser
   description, mirroring the existing `HALT_NO_ENVELOPE` entry immediately above it. Without this
   entry, `CauseLineTable.test_the_table_covers_every_class_in_halt_lines`
   (`test_summary.py:190`-`194`, `set(RECORD_ROWS) | set(FINDING_ROWS) == set(contracts.HALT_LINES)`)
   fails the moment `HALT_LINES` gains the new class in step 1.

**Technical design (directional):**

```
classify():
    ...
    envelope = parse_envelope(last_text) if last_text else None
    result["envelope"] = envelope

    if last_text and not _complete(envelope) and WAITING_LAST_MESSAGE_RE.search(last_text):
        result["findings"].append({
            "class": contracts.WAITING_LAST_MESSAGE,
            "last_message": result["last_message"],
        })

    # existing precedence block, unchanged
    ...
```

**Patterns to follow:** `RUNNER_SELF_KILL`'s finding shape and comment style
(`contracts.py:349`-`355`); the existing `HALT_NO_ENVELOPE` finding append at `classify.py:444`-`447`
for the append-into-`result["findings"]` shape; `_KILL_COMMAND_RE`'s bounded, commented regex
discipline (`classify.py:149`-`159`).

**Test scenarios:**
- Happy path: a transcript whose last message is "Standing by for the test suite's completion
  notification." and no fenced envelope produces a finding with class
  `contracts.WAITING_LAST_MESSAGE`, alongside the existing `HALT_NO_ENVELOPE` finding and
  `halt_class == contracts.HALT_NO_ENVELOPE` (new fixture, `tests/test_classify.py`).
- Negative: the existing `no_envelope.jsonl` fixture (whose last message names no waiting phrase)
  produces no `WAITING_LAST_MESSAGE` finding — regression coverage that the regex is not
  over-matching.
- Negative (R4): a last message containing "standing by" language followed by a complete fenced
  `relay-envelope` block with `status: complete` produces no `WAITING_LAST_MESSAGE` finding.
- Rendering (R5): `summary.cause_line(contracts.WAITING_LAST_MESSAGE, finding)` and
  `classify.finding_line(finding)` both render the new `HALT_LINES` template filled with the
  finding's `last_message`, mirroring the existing `FindingLines` test class's coverage
  (`tests/test_classify.py:382`-`415`) and `FINDING_ROWS` fixture in `test_summary.py`.

**Verification:** `python3 -m unittest test_classify test_contracts test_summary` from `tests/`
passes; the new fixture is committed alongside its generator entry.

---

## Verification Contract

- Full suite from the repo root: `python3 -m unittest discover -s tests` (approx. 2.5 minutes per
  `CLAUDE.md`).
- Targeted re-runs during development: `python3 -m unittest test_brief test_classify
  test_contracts test_summary` from `tests/`.
- No new external dependencies; standard-library-only per `CLAUDE.md`.

## Definition of Done

- Both brief templates carry the sharpened paragraph; `tests/test_brief.py`'s extended pin passes.
- `contracts.py` carries `WAITING_LAST_MESSAGE` in `FINDING_CLASSES`, `LINE_CLASSES`, and
  `HALT_LINES`, and not in `HALT_CLASSES`.
- `classify.classify()` appends the finding under the conditions R3/R4 state, verified by the new
  fixture and its tests.
- Full suite green; no unrelated code left uncommitted; no dead-end exploration code from
  approaches not taken remains in the diff.
