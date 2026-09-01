---
title: Unenforced Audit Detection Bound - Plan
type: fix
date: 2026-09-01
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Unenforced Audit Detection Bound - Plan

## Goal Capsule

- **Objective:** An operator reading a Relay run's output for a backend that cannot enforce restrictions at launch can tell what the post hoc audit proved, so an empty findings list is never read as proof that the task obeyed the disallow list.
- **Means:** Carry the detection bound on the `unenforced_restrictions` record scalar and surface it in the run summary beside the findings list that prompts the misreading, and pin the observed evasion shapes as regression tests. (KTD2, KTD3, KTD6)
- **Authority:** The Product Contract's R-IDs govern behavior. KTDs govern mechanism. The `contracts.py` halt class set stays closed (KTD6 of `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`); nothing here adds one.
- **Execution profile:** Two units, both small. No new module, no new record key, no new finding class.
- **Stop conditions:** Stop and report if the audit turns out to miss a wrapper shape a live log actually carries. That is a defect in `classify._SHELL_WRAP`, a different fix from this one, and it invalidates KTD1.
- **Tail ownership:** `ce-work` implements and verifies locally. The Relay runner owns the gate, the merge, and the push.

---

## Product Contract

### Summary

Say on the operator's own surfaces what the post hoc disallowed-command audit can and cannot prove. Extend the `unenforced_restrictions` string on a Task record so it names the detection bound, and carry that string into the run summary's task entry, which today prints the findings list with nothing beside it. Pin the two command shapes a live codex run actually produced as regression tests, so the observed evasion is a recorded bound rather than an unexplained gap.

### Problem Frame

Relay task T-65 in the relay-proof campaign (state dir `a4da786f...`) gave a codex Task a manifest that disallowed `Bash(python3 -m unittest*)` while the task itself needed the suite to pass. Codex cannot refuse a tool at launch, so the disallow list reached it as brief instructions, and the post hoc audit (R24 of `docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md`) was the only check. The audit recorded `findings: []` while the transcript shows the suite ran three times.

The tracker card names two causes. Only the second is real, and the second is worse than the card states.

The first claim, that codex wraps every command in `/bin/zsh -lc` so no prefix pattern can ever match, no longer holds. `classify._SHELL_WRAP` (`skills/relay/scripts/relay/classify.py:30`) was widened to `zsh` and to a glued `-lc` flag cluster on 2026-08-30 in commit `06622f2`, recorded in `docs/solutions/logic-errors/shell-wrap-regex-missed-zsh-and-glued-lc-so-the-unenforced-audit-never-saw-a-live-destructive-command.md`. Replaying the T-65 log through `classify._unwrap_command` confirms the unwrap works on the live shape: every logged command unwraps to its inner script. The audit is not blind to the wrapper.

The second claim is the whole cause. `logs/T-65.stdout.log` shows codex ran the suite as `python3 -c "import unittest; ...loadTestsFromName..."` and `python3 -c "import unittest; ...discover..."`, both inside the usual `/bin/zsh -lc` wrapper. Both unwrap cleanly, neither matches the `python3 -m unittest*` glob, and no finding is correct on the pattern's own terms. None of the 24 logged commands spells `python3 -m unittest` at all.

This was not an innocent gap in the instructions. `brief.UNENFORCED_RESTRICTIONS` (`skills/relay/scripts/relay/brief.py:119`) already tells the process to avoid the listed operations "however this CLI spells it", and states that "the patterns are the runner's spelling; the operations they name are what is forbidden". The process was instructed at the operation level and audited at the spelling level, and it did at the operation level what it had been told not to do. Two different things are therefore true of an empty findings list, and the record distinguishes neither: the audit confirms only that no logged command matched a pattern's spelling, and it cannot confirm that the operation-level instruction was obeyed. An operator deciding whether to keep granting `permissions.unenforced_acceptance` to a backend needs that distinction, because a matcher bound and a disregarded instruction call for different responses.

Nothing on the operator's surfaces says any of this. `summary._task_entry` (`skills/relay/scripts/relay/summary.py:62`) renders a task's findings and never reads `unenforced_restrictions`, so `relay summary` shows a landed codex task beside an empty findings list with no caveat at all.

### Requirements

**What the record and the summary claim**

- R1. The `unenforced_restrictions` scalar on a Task record states that the disallow list reached the process as brief instructions naming operations, that the audit after exit matched command spellings against the process's own tool calls, and that no finding is therefore not proof the operation-level instruction was obeyed.
- R2. The scalar stays one plain single-line string, and it still names every unenforced inner operation. (inherits KTD7 of `docs/plans/2026-08-30-2244-feat-backends-u10-unenforced-restrictions-plan.md`)
- R3. The scalar carries the bound whether or not any finding fired, because the empty-findings case is the one the caveat exists for.
- R7. The run summary's task entry carries the scalar, so the bound appears beside the findings list rather than only in the raw record.

**What the audit is pinned against**

- R4. A regression test pins that a `/bin/zsh -lc "..."` command still matches its disallow pattern when the inner command is spelled literally. The T-65 log carries no literal spelling, so this test's command is a constructed control in the live wrapper shape, not a capture.
- R5. Regression tests pin that both equivalent-command shapes observed on T-65 produce no finding, so their absence is a recorded bound rather than an unexplained gap.
- R6. The evasion tests use command strings captured from `logs/T-65.stdout.log` verbatim and whole, including the trailing `&&` segments of log line 64, so the pin exercises `classify._shell_parts` splitting a line whose `&&` sits outside a quoted payload.

### Acceptance Examples

- AE1. **Covers R1, R3, R7.** **Given** a manifest whose backend does not enforce at launch, **when** the Task runs and the audit finds nothing, **then** the record's `unenforced_restrictions` names the unenforced operations and states the detection bound, and the summary's task entry carries the same string beside its empty findings list.
- AE2. **Covers R4.** **Given** the constructed control `/bin/zsh -lc "python3 -m unittest discover -s tests"` and the pattern `Bash(python3 -m unittest*)`, **when** `classify.matches_disallow_pattern` runs, **then** it returns True.
- AE3. **Covers R5, R6.** **Given** either T-65 command that runs the suite through `python3 -c`, captured whole, and the same pattern, **when** `classify.matches_disallow_pattern` runs, **then** it returns False and `classify._unwrap_command` returns a string starting with `python3 -c`.

### Scope Boundaries

**In scope**

- The text `run._unenforced_scalar` returns for the record's `unenforced_restrictions` key.
- One key added to the dict `summary._task_entry` returns.
- Three regression tests in `tests/test_classify.py`.
- One new test case in `tests/test_run.py` covering the empty-findings case, alongside the existing assertions it must not regress, and one in `tests/test_summary.py`.

**Deferred for later**

- Narrowing `brief.UNENFORCED_AUDIT`. That sentence promises the process that "a destructive disallowed call will not land", which holds only at the spelling level: `run._destructive_finding` (`run.py:300`) filters the same findings `classify.matches_disallow_pattern` produced, so an equivalently spelled destructive operation yields no finding and no refusal. Narrowing it is a brief-text change, which is a contract between processes and needs a live backend run under the repo rule in `CLAUDE.md`, and `brief.defang` plus the count assertions in `tests/test_brief.py` move with it. It gets its own tracker issue.
- A run-specific caveat naming the audited tool-call count and the task log path. Ranked against the fixed sentence and not taken: `tool_calls` lives on the digest and the log path is already on the record, so the operator can reach both, and KTD3's single-line shape is what keeps the scalar cheap to render. Revisit if the fixed sentence proves too abstract to act on.
- Semantic or behavioral detection of an evading command. Reading intent out of `python3 -c "import unittest; ..."` is a different capability with its own false-positive surface, and it would be an arms race against a process that has read the pattern list.

**Outside this work's identity**

- A new halt class. Halt classes are closed in `contracts.py` (KTD6 of the outer-loop plan). An evaded pattern is not observable, so there is nothing to raise.
- Changing `classify._SHELL_WRAP`. It already handles the live shape. (KTD1)
- Changing the brief. (KTD4)

### Sources

- `logs/T-65.stdout.log` in `~/.relay/a4da786f1f637756616e45ceada2cea8447ba6f41db1a062e0de22db1e776f45`, log lines 45 and 64, carries the two evasion commands R6 captures. Line 49 chains both spellings with `&&`. The file holds 24 `command_execution` items and no literal `python3 -m unittest` spelling, which is why R4's control is constructed. `digests/T-65.json` in the same directory holds `findings: []` and `tool_calls: 27`.
- `docs/solutions/logic-errors/shell-wrap-regex-missed-zsh-and-glued-lc-so-the-unenforced-audit-never-saw-a-live-destructive-command.md` records the unwrap fix that already landed, and `test_a_zsh_lc_rm_rf_without_and_still_matches` in `tests/test_classify.py` is its live-shape pin.
- `docs/solutions/logic-errors/disallow-glob-reused-for-log-forensics-had-no-word-boundary.md` records why `fnmatch` globs are right for the flag-grammar job and wrong for scanning prose. It is the reason this plan does not reach for a looser pattern.
- `docs/plans/2026-08-30-2244-feat-backends-u10-unenforced-restrictions-plan.md` is the origin unit; its KTD7 owns the scalar's shape. `docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md` owns R24 and, in its own KTD7, the rule that the landing bound and the audit cover scope and visibility rather than enforcement.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Leave `classify._SHELL_WRAP` alone.** The card's first claimed blind spot was fixed in `06622f2`, and `test_a_zsh_lc_rm_rf_without_and_still_matches` already pins a live `/bin/zsh -lc` shape. The T-65 log contains no literally spelled disallowed command, so replaying it confirms the unwrap and cannot confirm the match; R4's control covers the match instead. Widening the regex further would fix nothing and would risk a false positive on an unrelated command. Governs R4, and the Goal Capsule's stop condition is what reopens it.

- KTD2. **The caveat lives on `run._unenforced_scalar`, not on a finding.** `skills/relay/scripts/relay/run.py:290` builds the scalar and `run.py:403` writes it whenever `capability.enforces_at_launch` is False, independent of findings. A caveat attached to a finding would be present only when the audit already caught something and absent in the empty-findings case, which is the case an operator can misread. Governs R1, R3.

- KTD3. **The caveat is one fixed sentence appended to the existing list, not per pattern.** The bound is a property of matching by command spelling, not of any individual pattern, so a per-pattern note would repeat one fact across the string. The origin plan's KTD7 pins the scalar as one comma-separated string of inner operations; a single appended sentence keeps that shape. Governs R2.

- KTD6. **The summary carries the scalar as a plain key on the task entry, not as a Cause line.** `summary._task_entry` already returns a fixed key set and renders findings through `cause_line`. Adding a key is additive for every reader of the summary JSON, while a Cause line would need a halt class the closed set does not have. The bound belongs beside the findings list because that list is what gets misread. Governs R7.

- KTD4. **Do not add the bound to the brief.** Withholding it protects nothing: `brief.UNENFORCED_RESTRICTIONS` already interpolates the full disallow pattern list verbatim and already forbids reaching those operations by another spelling, so a process that reads its own brief can derive the bound in one step. The decision stands on audience instead. The caveat exists for the operator reading the record afterwards, and no sentence added to the brief changes what a process that already disregarded the operation-level instruction will do. The identically named `$unenforced_restrictions` brief placeholder, built by `brief._unenforced_block` at `brief.py:219` and filled into both Task templates, is a different value from the record key and stays untouched.

- KTD5. **No live run is required to land this.** The repo rule in `CLAUDE.md` requires a live task after changing a contract between processes: the envelope grammar, the closeout terminal line, a brief template, the halt record, or the classify digest keys. This change touches the record scalar and one summary key. The brief is unchanged (KTD4), the digest keys are unchanged, and no halt class moves. The stub is sufficient here because the shapes the stub cannot produce are exactly the ones R6 takes from the captured live log instead. The deferred `brief.UNENFORCED_AUDIT` narrowing does cross this line, which is why it is deferred rather than folded in.

### Assumptions

- The captured T-65 log at `~/.relay/a4da786f...` is available on the implementing machine for copying the two evasion strings into the tests. If it is not, both strings are reproduced verbatim in U1's Approach below and are sufficient on their own.
- Nothing outside the summary parses `unenforced_restrictions` for structure. It is written only at `run.py:403`, named by no `contracts.HALT_LINES` template, and read by no closeout template. The existing assertion at `tests/test_run.py:1581` checks for a substring.

### Sequencing

U1 and U2 are independent and can land in either order. U1 is the cheaper of the two and settles KTD1 empirically, so run it first.

---

## Implementation Units

### U1. Pin the live wrapper shape and the evasion bound

- **Goal:** Three regression tests that hold `classify.matches_disallow_pattern` to what a live codex process actually produced, one match and two non-matches.
- **Requirements:** R4, R5, R6.
- **Files:** `tests/test_classify.py`.
- **Approach:** Add all three tests next to the existing `test_a_zsh_lc_rm_rf_without_and_still_matches`, which is the neighbouring live-shape pin from `06622f2`. All three use the pattern `Bash(python3 -m unittest*)`.
  - The catching case is a constructed control, not a capture: `/bin/zsh -lc "python3 -m unittest discover -s tests"`, expected True. Say in its docstring that the T-65 log carries no literal spelling, so this shape is built rather than captured.
  - The first evasion case is log line 45 verbatim, the `loadTestsFromName` spelling, expected False.
  - The second evasion case is log line 64 verbatim and whole, including its `&& git status --short --branch && git log -1 --oneline` tail, the `discover` spelling, expected False. Keeping the tail is the point of R6: the `&&` sits outside the quoted `python3 -c` payload, so `_shell_parts` splits a real compound line rather than a single command.

  Name the two evasion tests so their intent is unmistakable to a later reader who finds a failing assertion and assumes the matcher is broken. They assert a known bound, not desired blindness. Carry that in each docstring, citing T-65 and naming the reason: the brief instructs at the operation level and the audit matches at the spelling level, so the two can disagree.
- **Test scenarios:**
  - The constructed wrapped literal `python3 -m unittest` spelling matches `Bash(python3 -m unittest*)`.
  - The T-65 line 45 `loadTestsFromName` command does not match.
  - The T-65 line 64 `discover` command, tail included, does not match.
  - `classify._unwrap_command` on each evasion string returns a value starting with `python3 -c`, proving the non-match comes from the pattern and not from a failed unwrap. This is the assertion that distinguishes KTD1's conclusion from the card's first claim.
- **Verification:** `cd tests && python3 -m unittest test_classify` passes.

### U2. Carry the detection bound on the record and in the summary

- **Goal:** Both operator surfaces say what the audit checked and what it cannot check.
- **Requirements:** R1, R2, R3, R7.
- **Files:** `skills/relay/scripts/relay/run.py`, `skills/relay/scripts/relay/summary.py`, `tests/test_run.py`, `tests/test_summary.py`.
- **Approach:** Extend `_unenforced_scalar` (`run.py:290`) to append a fixed caveat sentence after the comma-separated inner operations. Keep the existing lead and the list ahead of it so the current substring assertion at `tests/test_run.py:1581` keeps reading the same. The sentence states three things: the list reached the process as brief instructions naming operations, the audit after exit matched command spellings against the process's own tool calls, and no finding is therefore not proof the operation-level instruction was obeyed. Then add `"unenforced_restrictions": record.get("unenforced_restrictions")` to the dict `summary._task_entry` (`summary.py:62`) returns, so a claude task carries `None` there and an unenforced one carries the string. Put the reasoning in a comment on `_unenforced_scalar` citing T-65, following the commenting style the neighbouring `_KILL_COMMAND_RE` block uses in `classify.py`.
- **Test scenarios:**
  - `test_a_non_destructive_disallowed_call_lands_with_a_finding` still finds `git clean` in the scalar (existing assertion, must not regress).
  - `test_a_commit_outside_the_bound_halts_and_the_branch_survives` still gets a string (existing assertion).
  - A codex Task whose commands match no disallow pattern lands with an empty `UNENFORCED_DISALLOWED` finding set and a record whose `unenforced_restrictions` still carries the caveat. This is AE1's record half; `queue_codex()`'s default command `/bin/zsh -lc 'pwd'` already produces the empty-findings shape, so the case can be added to `UnenforcedRun` without a new fixture.
  - A claude Task still has no `unenforced_restrictions` key on its record at all (existing assertion at `tests/test_run.py:1553`, must not regress).
  - The summary's task entry for an unenforced Task carries the caveat string, and the entry for a claude Task carries `None`. This is AE1's summary half.
- **Verification:** `cd tests && python3 -m unittest test_run test_summary` passes.

---

## Verification Contract

| Gate | Command | Applies to |
|---|---|---|
| Full suite | `python3 -m unittest discover -s tests` from the repo root | U1, U2. About two and a half minutes. This is the project gate. |
| Classifier module | `python3 -m unittest test_classify` from `tests/` | U1 |
| Runner and summary modules | `python3 -m unittest test_run test_summary` from `tests/` | U2 |

A local pre-push hook, not tracked in git, runs the full suite again on every push, so the gate cannot be disabled by the change that would need to disable it.

No live backend run is required. KTD5 states the reasoning.

---

## Definition of Done

**Global**

- The full suite passes from the repo root.
- No new halt class, no new record key, no new finding class.
- No dashes of any kind in added prose or comments, per the repo convention.
- No abandoned experimental code is left in the diff.

**Per unit**

| Unit | Done when |
|---|---|
| U1 | Three matcher tests exist in `tests/test_classify.py` and pass. Both evasion tests use the T-65 strings verbatim and whole, and the catching test's docstring says its command is a constructed control. Each evasion docstring names T-65 and states that the assertion pins a known bound. |
| U2 | `run._unenforced_scalar` returns the operation list plus the caveat sentence, `summary._task_entry` carries the scalar, every existing assertion in `UnenforcedRun` still passes, and new cases cover an unenforced Task landing with no `UNENFORCED_DISALLOWED` finding on both the record and the summary entry. |
