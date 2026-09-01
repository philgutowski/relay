---
title: Launchd Privacy Grant Warning - Plan
type: docs
date: 2026-09-01
origin: https://github.com/philgutowski/relay/issues/43
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Launchd Privacy Grant Warning - Plan

## Goal Capsule

- **Objective:** An operator who arms a scheduled Relay launch on macOS learns, from the launch surfaces they already read, that the python binary needs a Files and Folders grant for the folders it will read, or Full Disk Access, before the first launchd or cron fire, so the run is not left sitting on a privacy prompt with no halt class and no log.
- **Means:** Add a short grant warning to `skills/relay/SKILL.md` Launch and `README.md` Use, next to the existing launch text, without naming `--detach` as the cause (KTD1, KTD2).
- **Product authority:** GitHub issue 43, observed on a launchd `StartCalendarInterval` fire.
- **Execution profile:** Documentation only. No runner, halt class, or plugin version change.
- **Stop conditions:** Stop if the warning would name a person, a live project, or a dated incident in shipped operator docs.
- **Tail ownership:** The calling process owns commit and the project gate.

## Product Contract

### Summary

macOS treats Files and Folders access as a per binary grant. A first launchd or cron launch of the runner sits outside Terminal's existing grant, so the python process blocks on a prompt until a person clicks. Record that fact on the two surfaces that already describe launching the runner, and name that the stall produces no halt class and no log line because the runner has not started.

### Problem Frame

Round six stage A was armed as a launchd agent. launchd fired on time. The runner ran outside the terminal's privacy grants, so macOS raised a Files and Folders prompt for the Documents folder, and the python process sat blocked for hours until the prompt was accepted. The run itself was fine once released. The grant is per binary and holds until that executable's identity changes, and it is now in place on that machine. The skill already describes `--detach`, `caffeinate`, and lid close. README Use already shows `run --detach`. Neither names this stall, which looks like a silent no start rather than a classified halt.

### Key Decisions

- KD1. **Record the grant on existing launch surfaces, not as a halt class.** The stall happens before the runner writes state, so a new class would never fire. Governs R1, R2, R3, R4.

### Requirements

- R1. `skills/relay/SKILL.md` Launch names that a first launchd or cron launch needs a Files and Folders grant for the folders that binary will read (the checkout and the state directory), or Full Disk Access, made ahead of time. Documents is the usual folder when the checkout lives there. It is not the only folder.
- R2. The same warning names that without the grant the process stalls with no halt class and no log line, because nothing has started yet.
- R3. `README.md` Use carries the same warning next to the launch commands, so an operator who never opens the skill still sees it.
- R4. The warning states that the grant is per binary and holds until that executable's identity changes (a Homebrew, pyenv, or Xcode python replacement is a new binary).
- R5. Shipped wording names System Settings, Privacy and Security, and the two panels Files and Folders or Full Disk Access. It names the ProgramArguments python path as the row to add. It does not walk a click path that will rot, and it does not name a person, a live project, or a dated incident.
- R6. The Launch "never started" ending also names this stall as a live python with no `runner.log` and no halt record, so a later session does not diagnose it as a held lease or an invalid manifest.

### Scope Boundaries

- Out of scope: detecting the TCC prompt in the runner, adding a halt class, shipping a launchd plist, or changing `cli.py` detach behavior.
- Out of scope: bumping `.claude-plugin/plugin.json`. Recent skill docs landed without a version bump.
- Out of scope: a `docs/solutions/` learning as part of this unit. The operator facing warning is the deliverable.
- Deferred to Follow-Up Work: none.

### Sources

- Issue 43, observed 2026-08-30 on a launchd `StartCalendarInterval` fire.
- Apple developer guidance that Full Disk Access and Files and Folders are granted in System Settings, Privacy and Security, and that a launchd child is not attributed to Terminal the way an interactive shell is.
- `skills/relay/SKILL.md` Launch already holds the macOS caveats (`caffeinate`, no `setsid`, lid close).
- `README.md` Use already shows `run --detach`.
- `tests/test_examples.py` pins SKILL launch fragments (`setsid`, `caffeinate`, `runner.log`, lid close) and forbids shipped files from naming a real project, tracker site, or person.

## Planning Contract

### Key Technical Decisions

- KTD1. **Put the SKILL warning in Launch, in the existing macOS paragraph after lid close.** That is the skill section that already describes start on this host. The trigger to name is launchd or cron, not `--detach`. `--detach` from Terminal or Claude Code was not the observed parent, so shipped text must not name it as the cause. Governs R1, R2, R4, R5.
- KTD2. **Put the README warning in Use, immediately after the command list that includes `--detach`.** That is the only README section that describes launching the runner. Placement next to `--detach` is proximity, not causation. Governs R3, R2, R4, R5.
- KTD3. **Keep both copies the same facts, not the same paragraph.** SKILL may keep the skill's second person voice. README stays in the README's operator voice. Do not invent a third copy in `CONCEPTS.md`.
- KTD4. **Add one sentence to the Launch never-started ending.** A live python with no `runner.log` and no halt record can be this prompt on a launchd or cron parent. Governs R6.

### Assumptions

- Dual placement is the reading of the issue's "wherever scheduled or detached launches are described." The issue also said "skill or README," which would have allowed one file. Both files describe launching the runner, so both get the scheduled-launch warning.
- The python binary in the grant is whichever `python3` the launchd ProgramArguments list names, not Terminal.app and not Claude Code.
- `--detach` from Terminal or Claude Code is unproven for this stall. Do not claim it.
- No plugin version bump is required for this docs change, matching recent SKILL.md docs commits that left `0.2.0` in place.

## Implementation Units

### U1. Document the launchd privacy grant

- **Goal:** An operator reading Launch or Use knows to grant Files and Folders or Full Disk Access to the python binary before the first scheduled fire, and knows why a missed grant looks like a silent stall.
- **Requirements:** R1, R2, R3, R4, R5, R6; KTD1, KTD2, KTD3, KTD4; KD1.
- **Dependencies:** None.
- **Files:** `skills/relay/SKILL.md`, `README.md`.
- **Approach:**
  1. In `skills/relay/SKILL.md` Launch, after the lid close sentence, add two or three sentences covering R1, R2, R4, and R5. Name launchd or cron as the trigger. Do not name `--detach` as the cause.
  2. In the Launch never-started ending, add the R6 sentence.
  3. In `README.md` Use, after the command list that includes `--detach`, add a matching paragraph covering the same facts in README voice.
  4. Re-read both against R5 by hand. Also re-read against `tests/test_examples.py` leak patterns and the existing SKILL launch fragment pins. Do not drop `setsid`, `caffeinate`, `runner.log`, or lid close. The leak regex is not enough for R5.
- **Patterns to follow:** The existing SKILL Launch macOS caveats (caffeinate, lid close) and the README Use command list. Sibling docs unit in `docs/plans/2026-08-30-1400-docs-backend-readiness-remediation-plan.md`.
- **Test scenarios:** Documentation only. Test expectation: none, no behavioral change. `tests/test_examples.py` Skill and Readme and NoProjectLeakage cases must still pass.
- **Verification:** Both surfaces name the grant, the silent stall, the System Settings panels, and the per binary identity fact. Launch's never-started ending names this stall. Neither names a person, a live project, or the originating incident.

## Verification Contract

| Gate | Evidence |
| --- | --- |
| Regression suite | `python3 -m unittest discover -s tests` passes. |
| Skill launch pins | `tests/test_examples.py` still finds `setsid`, `caffeinate`, `runner.log`, and lid close in `skills/relay/SKILL.md`. |
| Shipped leak regex | Nothing in `skills/`, `docs/examples/`, or `README.md` matches the leak patterns in `tests/test_examples.py`. |
| Human re-read | Both surfaces name the grant, the silent stall, the System Settings panels, and the per binary identity fact. Launch's never-started ending names this stall. Neither names a person, a live project, or the originating incident. The leak regex is not this gate. |

## Definition of Done

- `skills/relay/SKILL.md` Launch and `README.md` Use both warn that a first launchd or cron launch needs a Files and Folders grant for the folders that python binary will read, or Full Disk Access, ahead of time, that the grant is per binary until that executable changes, and that a missed grant stalls with no halt class and no log.
- Launch's never-started ending names this stall as a live python with no runner log and no halt record.
- No runner code, halt class, plugin version, or solutions file changed.
- The full test suite still passes.
