---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: task-26
---

# Backends U11: Record and summary shape

## Goal

Persist the backend that actually ran each task, including its resolved command evidence, and make terminal version evidence backend-keyed without preventing pre-U11 state directories from being resumed or summarized.

## Scope

- Update Relay state records and terminal records, including the schema version and legacy readers.
- Capture a task's resolved backend at launch time, before a later manifest edit can affect resume behavior.
- Capture the resolved executable path and argv actually passed to the launcher.
- Replace scalar pinned and observed terminal CLI-version values with mappings keyed by backend actually used.
- Render task backend information in the JSON and text summary without allowing structured values to disappear from Cause lines.

Out of scope: changing manifest routing, backend capability definitions, or live CLI proof runs.

## Implementation units

### U1 — State and launch evidence

Files: `skills/relay/scripts/relay/state.py`, `skills/relay/scripts/relay/contracts.py`, `tests/test_state.py`.

1. Bump the state schema and add `backend`, `binary_path`, and `args` to new task-record defaults.
2. Extend terminal writes and crash recovery to preserve per-backend tested and observed versions. Readers normalize old scalar version fields to the Claude entry and tolerate a missing task backend.
3. Give launch results the resolved executable and argv so `run._one_task` can persist the exact process evidence. Record the task backend when its running record is created, and keep resume tied to that stored value rather than a later manifest edit.

Tests: new records include the backend evidence; legacy state with scalar terminal version fields and no backend opens and resumes; terminal mappings retain all used backends and exclude unused ones.

### U2 — Summary contract

Files: `skills/relay/scripts/relay/summary.py`, `tests/test_summary.py`.

1. Normalize terminal version values through the state reader and expose backend-keyed values in summary JSON.
2. Add each task backend to summary entries and render it in the text task header.
3. Keep Cause-line formatting scalar-safe by supplying the backend string separately rather than passing mappings to the line renderer.

Tests: mixed-backend summaries name every task backend; legacy state summarizes; and Cause-line templates can interpolate the backend value.

## Verification

Run `python3 -m unittest test_state test_summary`, then the full unittest suite required by the Relay gate.
