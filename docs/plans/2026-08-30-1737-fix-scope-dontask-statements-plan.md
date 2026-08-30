---
title: Scope the Relay Always Runs dontAsk Statements - Plan
type: fix
date: 2026-08-30
origin: tracker task relay task 18, part of #16
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Scope the Relay Always Runs dontAsk Statements - Plan

## Goal Capsule

- **Objective:** Every place that describes Relay's permission posture says
  what is true per backend today, not a single mode that only ever held for
  `claude`.
- **Means:** Reword the manifest validation error, the SKILL.md authoring
  instruction, the SKILL.md `path_gate` runbook row, and the two `HALT_LINES`
  cause-line templates that feed that row, so none of them assert `dontAsk`
  as universal.
- **Product authority:** Tracker task relay task 18 ("Scope the two 'Relay
  always runs dontAsk' statements when the launch seam lands"), part of #16.
- **Execution profile:** Small, surgical wording changes across two modules
  and one skill doc. No behavior change; `BACKEND_PINS`, `build_args`, and
  classification logic are already correct and untouched.
- **Stop conditions:** Stop if a reworded message would need to restate a
  `BACKEND_PINS` value that could drift (e.g. hardcoding `workspace-write`
  in prose that competes with the pin as source of truth); prefer pointing
  at the backend rather than repeating its posture value.
- **Tail ownership:** The calling process owns commit and the project gate.

---

## Product Contract

### Summary

Two places, named in the tracker task, assert Relay always runs `dontAsk`:
`skills/relay/scripts/relay/manifest.py`'s `permissions.permission_mode`
validation error, and `skills/relay/SKILL.md`'s authoring instruction plus
its `path_gate` runbook row. The task held this fix until Backends U5 (the
launch seam reading `contracts.BACKEND_PINS` per backend) landed, because
until then the statements were accurate: `launch.build_args` passed one
global `contracts.PERMISSION_MODE` for every task regardless of backend.

Verified against current `main` (branched into `relay/18`): U5 has landed.
`launch.build_args` (`skills/relay/scripts/relay/launch.py:127-145`)
delegates to `backends.build(task.backend).build_args`, and
`contracts.BACKEND_PINS` (`skills/relay/scripts/relay/contracts.py:126-241`)
already pins a distinct `permission_mode` per backend: `claude` keeps
`dontAsk`; `codex` uses `--sandbox workspace-write` and has no
permission-mode concept, carrying `danger-full-access` and
`--dangerously-bypass-approvals-and-sandbox` as its forbidden spellings
instead of `bypassPermissions`; `grok` uses `auto` and forbids `dontAsk` in
its own tuple, because under `dontAsk` grok cancels every tool call with no
one present to have cancelled anything
(`docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`).
So the two statements are now wrong for two of the three backends, and the
block on this task is lifted.

Also verified while reading `manifest.py` and `contracts.py` in full, beyond
the two lines the task named: `contracts.HALT_LINES` carries the same
overclaim in two cause-line templates that are the actual on-screen text
behind the SKILL.md `path_gate` row this task is scoping —
`HALT_DENIED_TOOL: "{tool} denied under dontAsk on {target}"` and
`HALT_PATH_GATE: "edit under .claude/ denied under dontAsk; ..."`
(`contracts.py:363-364`). Grok's `normalize_transcript`
(`backends/grok.py:96-105`) synthesizes the same claude-shaped denial text
Claude produces, so both findings are reachable on a `grok` task too (under
`auto`, having forbidden `dontAsk` itself, or its own `--deny` rule), and on
a `grok` task the printed cause line would say "denied under dontAsk" for a
task that never ran `dontAsk`. Correcting the SKILL.md row without
correcting the template it summarizes would leave the SKILL.md text
accurate while the tool's own output stays wrong, which is the same class
of drift the tracker task is asking to close. `codex` cannot reach either
finding (`backends/codex.py`'s `_UNDETECTABLE` lists both), so this template
fix only changes what a `grok` or `claude` operator actually reads.

`contracts.PERMISSION_MODE` (`contracts.py:115`, value `"dontAsk"`) is now
unread by any production code path (`launch.build_args` reads
`BACKEND_PINS` exclusively; grep confirms no other reference). Leaving a
dead, misleadingly-named module constant sitting next to `BACKEND_PINS`
invites the next reader to reach for it. Removing it is in scope as a
direct consequence of the same audit, not a separate cleanup pass.

### Problem Frame

Three surfaces assert or imply a single universal `dontAsk` posture that
`BACKEND_PINS` has since made backend-specific:

1. `manifest.py:401-402`'s `validate()` rejects a manifest that sets
   `permissions.permission_mode` or `permissions.mode`, with the message
   `"permissions.permission_mode is not a field; Relay always runs dontAsk
   (R11)"`. The *rejection* is still correct — `permissions` is a
   manifest-wide block applied regardless of any task's backend
   (`manifest.py:132-134`, `backend` is a per-task field; `permissions` has
   no per-task override), so a manifest still cannot choose or override any
   backend's posture. Only the *reason given* is now wrong: it is not that
   Relay always runs `dontAsk`, it is that permission posture is pinned per
   backend by `BACKEND_PINS` and is not manifest-configurable for any of
   them.
2. `SKILL.md:84`, the authoring instruction for the four qualifying
   sentences: `"Do not add a permission mode field: Relay always runs
   dontAsk and offers no switch."` Same problem as (1), same correct
   underlying rule (no such field exists), wrong reason given.
3. `SKILL.md:173`, the `path_gate` runbook row: `"the task needs an edit
   under .claude/, which dontAsk refuses whatever the allowlist says"`. This
   is backend-specific text describing a class that, per the audit above,
   is reachable under `claude` (`dontAsk`) and under `grok` (`auto`, with
   its own denial path), not only under `dontAsk`.
4. `contracts.py:363-364`, the `HALT_LINES` templates for `HALT_DENIED_TOOL`
   and `HALT_PATH_GATE`, both of which hardcode "denied under dontAsk" /
   "refuses" language into the literal cause line the operator reads via
   `summary.py`. This is the underlying source of (3)'s inaccuracy, not a
   new one.

### Requirements

- R1. `manifest.py`'s `permission_mode`/`mode` rejection message states that
  permission posture is fixed per backend by the runner and is never a
  manifest field, without naming `dontAsk` as if it were the universal
  value.
- R2. `SKILL.md`'s authoring instruction (currently line 84) carries the
  same correction as R1, in the operator-facing voice the surrounding
  authoring section already uses.
- R3. `SKILL.md`'s `path_gate` row (currently line 173) describes the class
  as reachable under any backend whose posture refuses a `.claude/` edit,
  without asserting `dontAsk` is the only such posture, and without
  claiming a posture `contracts.BACKEND_PINS` does not actually pin (no
  hardcoded restatement of `auto` or `workspace-write` behavior beyond what
  is already established as fact above).
- R4. `contracts.HALT_LINES[HALT_DENIED_TOOL]` and
  `HALT_LINES[HALT_PATH_GATE]` no longer hardcode "dontAsk" into the
  printed cause line; the printed line stays accurate for a `claude` task
  and for a `grok` task, the two backends that can reach these findings.
- R5. `contracts.PERMISSION_MODE` is removed, since it is unread by any
  production code path after U5 and its name and value now contradict
  `BACKEND_PINS`, the actual source of truth.
- R6. No behavior change: `BACKEND_PINS`, `launch.build_args`,
  `classify.py`'s finding detection, and every existing test's assertions
  about actual runtime behavior are untouched. This unit changes strings
  only (error text, doc prose, cause-line templates) plus the one dead
  constant's removal.

### Scope Boundaries

- Out of scope: widening `classify.py`'s denial detection to `codex`, or
  changing which findings are detectable per backend. `backends/codex.py`'s
  `_UNDETECTABLE` set is unchanged; codex still cannot reach
  `HALT_DENIED_TOOL` or `HALT_PATH_GATE`, and this plan does not comment on
  why beyond what the code already documents.
- Out of scope: changing the *rule* that `permissions.permission_mode` is
  rejected. That rule holds for every backend today (none is
  manifest-configurable), so only the message's stated reason moves, not
  the validation outcome. A manifest that sets `permissions.permission_mode`
  still fails `validate` after this plan.
- Out of scope: adding a manifest-level or per-task way to choose a
  permission mode. `BACKEND_PINS` remains the sole source of posture.
- Out of scope: any other `docs/solutions/` entry, `CONCEPTS.md`, or example
  manifest under `docs/examples/`. None of these were found (during the
  audit above) to assert a universal `dontAsk`, and re-scanning the whole
  repo for the phrase is not this unit's job; the tracker task named these
  specific surfaces plus the ones this plan's own read of `manifest.py` and
  `contracts.py` surfaced as directly downstream.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Keep the manifest-level rejection of `permission_mode`/`mode`,
  reword only the message.** `permissions` has no per-task override
  (`manifest.py:132-134`), so the field is correctly refused for every
  backend, not just `claude`. Rewriting the rule itself would be a schema
  change nothing in the tracker task or this plan's product contract calls
  for. Governs R1.
- KTD2. **Point the corrected wording at the backend's own posture rather
  than restating each backend's mode value in prose.** `BACKEND_PINS` is
  already the documented single producer of these values
  (`contracts.py:119-125`, "Do not restate these values elsewhere"). The
  three rewritten strings (manifest error, SKILL.md line 84, SKILL.md line
  173) say posture is "fixed per backend" or equivalent, not "dontAsk for
  claude, auto for grok, workspace-write for codex" — that phrasing would
  create a second copy of `BACKEND_PINS` that can drift the next time a pin
  changes. Governs R1, R2, R3.
- KTD3. **Fix `HALT_LINES` before touching the SKILL.md row it feeds.**
  SKILL.md:173 summarizes what the operator sees; what the operator
  actually sees is `HALT_LINES[HALT_PATH_GATE]`'s formatted string. Editing
  the doc without editing the template would leave the doc accurate and the
  tool's real output wrong, reproducing this task's own bug one layer down.
  Governs R3, R4.
- KTD4. **Remove `contracts.PERMISSION_MODE` rather than leave it as a
  legacy default.** It has no reader after U5 (confirmed by grep across
  `skills/relay/scripts/relay/`), and its value (`"dontAsk"`) is exactly
  the string this plan is scoping away from as universal; keeping it invites
  a future edit to reach for a name that looks authoritative but is dead.
  `contracts.FORBIDDEN_PERMISSION_MODE` is unaffected: it is still read by
  `manifest.py:405-406` to keep `bypassPermissions` out of
  `permissions.allowed`, a check this plan's Scope Boundaries leaves alone.
  Governs R5.

### Assumptions

- The tracker task's "Do not fix this before Backends U5 lands" condition
  is satisfied; this plan is the deliberate edit the task said would be
  needed "inside U5 or U8," done here as its own unit since U5 has already
  landed and U8 is not a precondition for wording-only corrections.
- No test asserts the literal string `"Relay always runs dontAsk"` or the
  literal `HALT_LINES` template text; `tests/` will be grepped for both
  during implementation and any hit updated as part of this unit, not
  treated as a surprise regression.

---

## Implementation Units

### U1. Reword the manifest validation error

- **Goal:** A manifest author who sets `permissions.permission_mode` sees an
  error that states the real reason (posture is pinned per backend, never a
  manifest field) instead of a now-inaccurate universal claim.
- **Requirements:** R1, R6; KTD1, KTD2.
- **Dependencies:** None.
- **Files:** `skills/relay/scripts/relay/manifest.py`.
- **Approach:**
  1. At `manifest.py:401-402`, replace the message
     `"permissions.permission_mode is not a field; Relay always runs
     dontAsk (R11)"` with wording that keeps the `(R11)` citation and the
     field name, states permission posture is fixed per backend by the
     runner via `BACKEND_PINS`, and is not settable from a manifest for any
     backend.
  2. Leave the surrounding `if` condition, the R11 comment above it
     (`manifest.py:399`), and the `permissions.allowed` bypass check
     (`manifest.py:405-406`) untouched; the comment at :399 already says
     "dontAsk only" in a context about the disallow list carrying every R10
     variant for the `claude` posture specifically, which is a separate,
     still-accurate statement about R10's own scope, not the R11 field
     rejection this unit rewords. Confirm during implementation that :399's
     comment does not also need scoping; if it turns out to assert a
     universal claim on inspection, reword it too under this same unit
     rather than opening a second one.
- **Patterns to follow:** The existing `err(...)` call style in `validate()`
  (`manifest.py:391-410`); keep the message a single sentence with the rule
  citation in parentheses, matching every other `err()` call in this
  function.
- **Test scenarios:** `tests/test_manifest.py` (or wherever the R11
  rejection is asserted) exercises a manifest with
  `permissions.permission_mode` set and asserts an error is raised; update
  the asserted message text to the new wording rather than the literal
  string `dontAsk`, matching on the stable parts (field name, `(R11)`) if
  the existing test does substring matching.
- **Verification:** `python3 -m unittest discover -s tests` passes; the
  reworded message no longer contains the literal claim "Relay always runs
  dontAsk" as a universal statement.

### U2. Reword the SKILL.md authoring instruction and path_gate row

- **Goal:** An operator authoring a manifest, or reading the halt-class
  table after a `path_gate` halt, reads accurate per-backend posture
  language in both places.
- **Requirements:** R2, R3, R6; KTD2.
- **Dependencies:** None; independent of U1 and U3 (different files, no
  shared state), but done after U3 so this unit's `path_gate` row wording
  can cite the corrected `HALT_LINES` behavior rather than the reverse.
- **Files:** `skills/relay/SKILL.md`.
- **Approach:**
  1. At `SKILL.md:84` ("Do not add a permission mode field: Relay always
     runs `dontAsk` and offers no switch."), reword to state that
     permission posture is fixed per backend by the runner (naming
     `contracts.BACKEND_PINS` as the mechanism, the way other parts of this
     doc name concrete internals) and is never a manifest field, for any
     backend.
  2. At `SKILL.md:173`, the `path_gate` row, reword "which `dontAsk`
     refuses whatever the allowlist says" to state the class fires when the
     task's backend posture refuses an edit under `.claude/`, true for
     `claude` under `dontAsk` and reachable for `grok` under its own
     posture, without inventing new detail about codex (which cannot reach
     this class at all, per Scope Boundaries) or restating exact mode
     values already owned by `BACKEND_PINS`.
  3. Leave every other row of the halt-classes table, the "Backend
     readiness" section, and the rest of the file untouched; grep the file
     once for the literal string `dontAsk` after editing to confirm no
     third, unscoped occurrence was missed.
- **Patterns to follow:** The doc's existing register: short declarative
  sentences, no dashes, internals named by their code identifier when that
  identifier is the actual mechanism (the file already does this
  throughout, e.g. naming `contracts.DISALLOWED_TOOLS` implicitly via "the
  disallow list").
- **Test scenarios:** Documentation only; no test file. Verification is a
  read-through plus `ce-doc-review` if the pipeline reaches it.
- **Verification:** `grep -n dontAsk skills/relay/SKILL.md` returns no line
  that asserts it as Relay's universal or only posture; the `claude`-only
  claim is present nowhere in the file after this unit.

### U3. Scope the HALT_LINES cause-line templates

- **Goal:** The literal cause line an operator reads for a `denied_tool` or
  `path_gate` halt is accurate for the backend that actually produced it,
  not hardcoded to `dontAsk`.
- **Requirements:** R4, R6; KTD3.
- **Dependencies:** None; done before U2's `path_gate` row wording so that
  row can describe the corrected behavior.
- **Files:** `skills/relay/scripts/relay/contracts.py`.
- **Approach:**
  1. At `contracts.py:363`, reword
     `HALT_DENIED_TOOL: "{tool} denied under dontAsk on {target}"` to drop
     the hardcoded posture name, e.g. stating the tool was denied by the
     task's permission posture on the target, without naming a specific
     mode string that would be wrong for a `grok` task running under
     `auto`.
  2. At `contracts.py:364`, reword `HALT_PATH_GATE: "edit under .claude/
     denied under dontAsk; apply attended, see solutions doc"` the same
     way: the edit was denied by the task's permission posture, keep "apply
     attended, see solutions doc" as-is since that instruction is
     posture-independent.
  3. Confirm neither template's `{tool}`/`{target}` substitution keys
     change; `summary.py:32,55` formats these from the finding dict
     unchanged by this unit, so only the literal template strings move.
- **Patterns to follow:** The existing template style in `HALT_LINES`
  (`contracts.py:360-374`): short, one clause per fact, `{field}`
  placeholders matching the finding dict's own keys.
- **Test scenarios:** Grep `tests/` for `"denied under dontAsk"` or the
  literal old template strings; update any test asserting the exact
  formatted cause line for `HALT_DENIED_TOOL` or `HALT_PATH_GATE` to the
  new wording. `test_summary.py` and `test_classify.py` are the likely
  homes; confirm by running the suite and reading any failure.
- **Verification:** `python3 -m unittest discover -s tests` passes; a
  formatted `HALT_PATH_GATE` or `HALT_DENIED_TOOL` line no longer contains
  the literal substring `dontAsk`.

### U4. Remove the dead PERMISSION_MODE constant

- **Goal:** `contracts.py` no longer carries a stale, unread constant whose
  name and value contradict `BACKEND_PINS`.
- **Requirements:** R5, R6; KTD4.
- **Dependencies:** None; independent of U1-U3.
- **Files:** `skills/relay/scripts/relay/contracts.py`.
- **Approach:**
  1. Grep the full repo (`skills/`, `tests/`, `docs/`) for
     `PERMISSION_MODE` to confirm the only remaining references after U1-U3
     are `contracts.py:115` itself (the definition) and
     `contracts.FORBIDDEN_PERMISSION_MODE` (a different name, unaffected).
     If any other reference to plain `contracts.PERMISSION_MODE` turns up,
     resolve it in this unit before deleting the constant, since a dangling
     reference would break at import or at call time, not at review time.
  2. Delete the `PERMISSION_MODE = "dontAsk"` line at `contracts.py:115`.
     Leave `FORBIDDEN_PERMISSION_MODE = "bypassPermissions"` on the next
     line untouched.
- **Patterns to follow:** None needed; this is a one-line deletion.
- **Test scenarios:** `python3 -m unittest discover -s tests` is the only
  check; a test importing `contracts.PERMISSION_MODE` directly would fail
  and is the signal to fix step 1 rather than skip the deletion.
- **Verification:** `grep -rn "contracts.PERMISSION_MODE\b" skills/ tests/`
  returns nothing; full suite passes.

---

## Verification Contract

| Gate | Evidence |
| --- | --- |
| Regression suite | `python3 -m unittest discover -s tests` passes after all four units. |
| No stray universal claim | `grep -rn "Relay always runs dontAsk\|denied under dontAsk" skills/` returns nothing. |
| Dead constant removed | `grep -rn "contracts.PERMISSION_MODE\b" skills/ tests/` returns nothing. |
| Rule preserved | A manifest setting `permissions.permission_mode` still fails `validate` (R11's outcome unchanged, only its message). |

---

## Definition of Done

- `manifest.py`'s R11 rejection message, `SKILL.md:84`, `SKILL.md:173`, and
  `contracts.HALT_LINES[HALT_DENIED_TOOL]` /
  `HALT_LINES[HALT_PATH_GATE]` all describe permission posture as fixed per
  backend by `BACKEND_PINS`, with no universal `dontAsk` claim remaining.
- `contracts.PERMISSION_MODE` is removed.
- No behavior change: `BACKEND_PINS`, `build_args`, and every finding-
  detection code path are untouched; only strings moved.
- Full test suite passes.
