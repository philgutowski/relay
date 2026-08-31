---
title: "fix: Launch Closeout on the Task backend"
date: 2026-08-30
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# fix: Launch Closeout on the Task backend

## Goal Capsule

**Objective:** A Relay Task receives a Closeout process that can read its rendered brief and classify its evidence on the same CLI backend that ran the Task.

**Means:** Pass the resolved Task backend through the run loop Closeout boundary and retain `closeout.run()` as the single fan out point for the renderer, launcher, and classifier. (KTD1)

**Authority:** The task brief, `docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md` U9, and the existing backend capability contract govern this plan.

**Stop conditions:** Stop if backend capability data or the Closeout terminal line grammar must change. This unit only repairs propagation of the Task backend already held by the caller.

---

## Product Contract

### Summary

Closeout execution currently has backend aware rendering, launch, and classification. The run loop does not supply the Task's backend, so Closeout falls back to Claude for non Claude Tasks. Make the run loop preserve backend identity at that boundary.

### Problem Frame

A Codex or Grok Task can produce a Closeout brief whose invocation and evidence parser are configured for Claude. The brief may therefore contain a skill spelling the launched CLI cannot run, and terminal evidence can be decoded by the wrong normalizer.

### Requirements

- **R1.** Every Closeout launched by a Task uses that Task's recorded backend.
- **R2.** The backend supplied to `closeout.run()` reaches the Closeout task record, the rendered Closeout brief, and transcript classification without a second default at the run loop seam.
- **R3.** Closeout retains its own configured model, effort, narrow tool allowlist, extra deny list, timeout handling, and terminal line behavior.
- **R4.** The regression coverage proves backend propagation at the run loop consumer, not only direct `closeout.run()` behavior.

### Scope Boundaries

In scope is `closeout.py`, `run.py`, and `tests/test_closeout.py`. The work does not change backend capability definitions, CLI argument grammars, terminal line parsing, manifest schema, or post execution audit behavior.

### Acceptance Examples

- **AE1.** A Task with backend `codex` launches a Closeout whose task record, rendered compound invocation, and evidence classifier all use `codex`.
- **AE2.** A Task with backend `grok` has the same propagation behavior.
- **AE3.** A timed out Closeout remains unfinished and does not consult a final message.

---

## Planning Contract

### Key Technical Decisions

- **KTD1. Propagate the backend from `_Context.task` at the only caller that already owns both Task and Closeout lifecycle.** `closeout.run()` is the established fan out seam for the renderer, launcher, and classifier. Passing `ctx.task.backend` there prevents a caller level Claude fallback while preserving the single resolved value through all consumers. Governs R1, R2.
- **KTD2. Make the Closeout execution boundary explicit rather than relying on a default.** The prior default preserved pre backend behavior, but it is unsafe once the calling Task can use another CLI. Test direct Closeout use with an explicit backend and test the run loop call separately. Governs R1, R4.

### Assumptions

- U5 and U8 are present on `main`: backend task records, backend specific launch arguments, evidence normalizers, and Closeout brief skill forms are already implemented.
- The existing stub can run all backend launch forms without a live external CLI, so the unittest suite is the project gate for this task.

### Sources and Research

- `docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md`, U9 defines the unchanged terminal line and timeout contracts.
- `skills/relay/scripts/relay/closeout.py` already accepts one backend value and routes it to `render()`, `_closeout_task()`, and `classify.classify()`.
- `skills/relay/scripts/relay/run.py`, `_run_closeout()` owns the only run loop call and currently omits that value.
- `tests/test_closeout.py`, `OneBackendValueReachesEveryConsumer`, proves direct fan out and needs caller level coverage.

---

## Implementation Units

### U1. Propagate the Task backend to Closeout

**Goal:** Launch and classify every Closeout using the backend recorded on its owning Task.

**Requirements:** R1, R2, R3, R4. Covers AE1, AE2, AE3.

**Dependencies:** U5 and U8 from the backends plan are already landed.

**Files:** `skills/relay/scripts/relay/closeout.py`, `skills/relay/scripts/relay/run.py`, `tests/test_closeout.py`.

**Approach:**

1. Require the backend at the Closeout execution boundary so callers cannot silently choose the legacy Claude default.
2. Pass `ctx.task.backend` from `_run_closeout()` to that boundary. Leave the existing Closeout fan out unchanged, because it already supplies the same value to the renderer, launcher, and classifier.
3. Extend the Closeout tests with a run loop level spy or fixture that demonstrates a non Claude Task reaches all three backend consumers. Keep direct tests for complete, skipped, unfinished, and timeout behavior to protect the terminal line contract.

**Patterns to follow:** `closeout.run()` is the single backend fan out seam; `run._start_task()` already calls `classify.classify(..., backend=task.backend)` for the Task process.

**Test scenarios:**

- A Codex Task propagates `codex` into the Closeout task record, brief skill form, and classifier backend.
- A Grok Task propagates `grok` through the same three consumers.
- A Closeout ending in `Documentation complete` is complete for every backend.
- A Closeout ending in `Documentation skipped` is skipped for every backend.
- A Closeout with no terminal line records the unfinished finding for every backend.
- A timed out Closeout records unfinished without relying on a parsed final message.

**Verification:** The focused Closeout unittest module passes, including explicit assertions on rendered text and captured call arguments for non Claude backends.

---

## Verification Contract

- Run `python3 -m unittest test_closeout` from `tests/` while iterating on the Closeout seam.
- Run `python3 -m unittest discover -s tests` from the repository root as the project gate.
- Compare rendered Codex and Claude Closeout briefs, asserting the backend specific compound invocation differs while the terminal lines and path bound remain identical.

---

## Definition of Done

- `run._run_closeout()` passes the owning Task's backend into Closeout.
- No default at the Closeout execution boundary can silently replace a Task backend with Claude.
- Focused tests cover Codex and Grok propagation plus terminal and timeout outcomes.
- The full unittest suite passes.
- No unrelated cleanup or backend capability changes are included.
