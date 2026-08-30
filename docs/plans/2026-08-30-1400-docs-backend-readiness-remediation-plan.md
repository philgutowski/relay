---
title: Backend Readiness Remediation Guidance - Plan
type: docs
date: 2026-08-30
origin: docs/plans/2026-08-29-0745-feat-backend-readiness-preflight-plan.md
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Backend Readiness Remediation Guidance - Plan

## Goal Capsule

- **Objective:** An operator who sees a backend readiness preflight failure
  learns, from the skill's own guidance, exactly what to install or upgrade
  to fix it, without reading `manifest.py` or `contracts.py`.
- **Means:** Add a preflight-failure guidance table to `skills/relay/SKILL.md`,
  keyed on the three failure shapes `manifest.py` already emits.
- **Product authority:** Finding `skills-relay-cli-114-document-readiness-remediation`,
  raised against `docs/plans/2026-08-29-0745-feat-backend-readiness-preflight-plan.md`.
- **Execution profile:** Documentation only. No runner, manifest, or backend
  code changes.
- **Stop conditions:** Stop if a failure message documented here drifts from
  what `manifest.py` actually emits; the code is the source of truth.
- **Tail ownership:** The calling process owns commit and the project gate.

---

## Product Contract

### Summary

`skills/relay/SKILL.md` documents seven halt classes an operator sees after a
run starts, but backend readiness preflight failures happen at `validate`
time, before any run starts, and are not covered by that table. Add a second
table, keyed on the preflight error text, that tells the operator which
backend is unready and what to do about it.

### Problem Frame

`skills/relay/scripts/relay/manifest.py:_backend_readiness_errors` (U1 of the
backend readiness preflight plan) added four failure messages: a missing
binary, a plugin probe that could not run at all, a missing or unreadable
qualifying plugin, and a plugin below `contracts.PLUGIN_MIN_VERSION`.
`cmd_validate` and `cmd_run` surface these as manifest errors and exit 1
(`skills/relay/SKILL.md:50`, "1 the manifest or environment is wrong"). The
SKILL.md "Explain a halt" section documents halt classes recorded by the
runner after a run starts, not this exit-1 preflight path, so an operator who
hits it has no map from the error text to a fix. SKILL.md is also already the
operator's standing runbook for every other relay error surface (the halt
classes table, the lease commands), so this preflight guidance belongs beside
it rather than behind a new signpost from the CLI's own output.

### Requirements

- R1. `skills/relay/SKILL.md` documents that a preflight failure surfaces as
  a `validate`/`run` exit 1 before any Task launches, distinct from the halt
  classes table.
- R2. The guidance names, for a missing binary, that the named backend's CLI
  (`claude`, `codex`, or `grok`) must be installed and on `PATH`.
- R3. The guidance names, for a plugin probe that could not run at all
  (the query subprocess raised `OSError` or `SubprocessError`), that the
  backend's own plugin-list subcommand must run manually and succeed.
- R4. The guidance names, for a missing or unreadable qualifying plugin, that
  `compound-engineering` must be installed and enabled for that backend, at
  or above `contracts.PLUGIN_MIN_VERSION`.
- R5. The guidance names, for a below-floor plugin, that the installed
  plugin must be upgraded to at least `contracts.PLUGIN_MIN_VERSION`.
- R6. The guidance tells the operator to re-run `validate` after fixing the
  environment, matching the existing "after a repair" convention in
  the halt-class section: a sentence naming the next step, followed by the
  `validate` command in a code block.

### Scope Boundaries

- Out of scope: changing `manifest.py`'s error text, adding new preflight
  checks, or documenting the `jira`+`codex`/`grok` adapter incompatibility
  error (already self-explanatory and not part of the named finding).
- Out of scope: a standalone setup doc. This repo has no separate setup guide
  outside `SKILL.md` and `CONCEPTS.md`; the finding's "setup guidance" is
  satisfied inside `SKILL.md`.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Add a second table next to "Explain a halt" rather than merging
  rows into it.** Preflight failures are a distinct moment (before a run
  starts, exit 1, no halt record) from the seven halt classes (after a run
  starts, exit 2, a classified record). Governs R1.
- KTD2. **Quote all four error message shapes verbatim from
  `manifest.py:_backend_readiness_errors`** (`"backend %s binary %r is
  missing from PATH"`, `"backend %s plugin probe failed: %s"`, `"backend %s
  has no readable %s plugin at or above %s"`, `"backend %s has %s plugin %s,
  below required %s"`) so the operator can match the table row to the exact
  `validate` output. Governs R2, R3, R4, R5.

### Assumptions

- The operator already treats `skills/relay/SKILL.md` as the standing
  runbook for a relay error, the same way the halt-classes table works today
  with no separate pointer from the CLI's own stderr. This plan does not add
  a signpost from `cmd_validate`'s `error: <text>` output to the doc; that
  would be a `manifest.py`/`cli.py` change, out of scope per Scope Boundaries.

---

## Implementation Units

### U1. Document preflight failure remediation

- **Goal:** An operator reading `skills/relay/SKILL.md` can go from a
  `validate`/`run` exit-1 error line straight to the fix.
- **Requirements:** R1, R2, R3, R4, R5, R6; KTD1, KTD2.
- **Dependencies:** None. `manifest.py`'s preflight is already on `main`.
- **Files:** `skills/relay/SKILL.md`.
- **Approach:**
  1. After the existing "Explain a halt" section (`skills/relay/SKILL.md:153-185`),
     add a new `## Backend readiness` section, placed before "Resume" since
     it can occur before a run ever starts.
  2. Open with one sentence stating this failure surfaces as exit 1 from
     `validate` or `run`, before any Task launches, and is not a halt class.
  3. Add a table with columns Error text / What it means / What the operator
     does, one row per shape in KTD2: missing binary -> install that backend's
     CLI and put it on `PATH`; plugin probe failed -> run that backend's
     plugin-list subcommand by hand and fix why it errors or hangs;
     missing/unreadable plugin -> install and enable `compound-engineering`
     for that backend at or above the pinned floor; below-floor plugin ->
     upgrade the plugin to the pinned floor.
  4. Close the section the same way `skills/relay/SKILL.md:181-185` closes
     the halt-classes table: a sentence naming the next step ("After fixing
     the environment, confirm before resuming:"), followed by a code block
     with `python3 <runner> validate <manifest>`.
- **Patterns to follow:** The existing halt-classes table's column shape and
  the "after a repair, confirm before resuming" convention immediately below it.
- **Test scenarios:** Documentation only; no test file. Verification is a
  manual read-through plus `ce-doc-review`.
- **Verification:** The new section's four table rows quote
  `_backend_readiness_errors`' four message templates exactly (spot check
  against `skills/relay/scripts/relay/manifest.py:366-382`).

---

## Verification Contract

| Gate | Evidence |
| --- | --- |
| Regression suite | `python3 -m unittest discover -s tests` passes (no code touched, confirms nothing broke). |
| Message accuracy | The four quoted error templates in the new section match `manifest.py:_backend_readiness_errors` verbatim. |

---

## Definition of Done

- `skills/relay/SKILL.md` has a `## Backend readiness` section mapping each
  of the four preflight failure shapes to a concrete remediation step and a
  re-run instruction.
- No other file changed; the full test suite still passes.
