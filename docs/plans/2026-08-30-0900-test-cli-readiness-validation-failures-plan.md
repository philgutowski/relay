---
title: CLI Coverage for Readiness Validation Failures Plan
type: test
date: 2026-08-30
origin: docs/plans/2026-08-29-0745-feat-backend-readiness-preflight-plan.md
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: tests-test-cli-65-validate-readiness-failures
execution: code
---

# CLI Coverage for Readiness Validation Failures Plan

## Goal Capsule

- **Objective:** The `validate` CLI verb's own failure diagnostics for a missing backend executable or an inadequate plugin are directly asserted, not just the underlying `manifest.validate()` call.
- **Means:** Add `Validate` test cases in `tests/test_cli.py` that drive `cli.main(["validate", ...])` through a stubbed missing binary and a stubbed missing/below-floor plugin, asserting `EXIT_CONFIG` and the named backend in the printed diagnostic.
- **Product authority:** Finding `tests-test-cli-65-validate-readiness-failures` from the `feat/backend-readiness-preflight` review (severity P2, confidence 75).
- **Execution profile:** Test-only change; no production code path changes.
- **Stop conditions:** None expected; the seam under test (`manifest_module.validate(..., check_environment=True)`) is already exercised at the unit level in `tests/test_manifest.py::BackendReadiness`.
- **Tail ownership:** N/A, this plan is executed and verified within the Relay task session that owns it.

---

## Product Contract

### Summary

`tests/test_manifest.py::BackendReadiness` proves `manifest.validate(..., check_environment=True)` reports a missing binary and a missing/inadequate plugin as distinct errors. `tests/test_cli.py::Validate` proves the `validate` verb's other failure paths (a broken schema rule, a missing manifest file) but has no case that reaches the environment-readiness branch through `cmd_validate`, so a regression that breaks the CLI's own wiring of `check_environment=True`, its exit code, or its error line rendering would pass the existing suite.

### Problem Frame

`cmd_validate` (`skills/relay/scripts/relay/cli.py:110`) calls `manifest_module.validate(manifest, check_environment=True, env=env)` and prints `"error: %s\n" % error` for each entry in `result.errors`, returning `EXIT_CONFIG` when `not result.ok`. No CLI-level test drives a readiness failure through this path, so nothing pins the CLI's exit code or output format specifically for this failure class, only for schema failures.

### Requirements

- R1. A CLI test invokes `validate` against a manifest whose backend binary is stubbed absent and asserts exit code `cli.EXIT_CONFIG` and that the printed output names the backend and the missing binary.
- R2. A CLI test invokes `validate` against a manifest whose backend binary is present but whose plugin probe reports a missing or below-floor plugin, asserting exit code `cli.EXIT_CONFIG` and that the printed output names the backend and the plugin failure, distinct from a binary failure.
- R3. Both tests exercise the real `cli.main` / `cmd_validate` path (not a direct call to `manifest.validate`), so they fail if the CLI stops passing `check_environment=True` or stops printing `result.errors`.

### Scope Boundaries

- Out of scope: changing `manifest.validate`, `_backend_readiness_errors`, or any production behavior. This is additive test coverage only.
- Out of scope: `relay run`'s own readiness-gated refusal path (`cmd_run`); the finding names `validate` specifically, and `cmd_run` shares the same `manifest_module.validate` call already covered at the unit level.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Stub at the same seams `tests/test_manifest.py::BackendReadiness` already uses** (`mock.patch.object(mf.shutil, "which", ...)` and `mock.patch.object(mf, "_run_plugin_query", ...)`), imported as `manifest as manifest_module` inside `test_cli.py` (already imported at `tests/test_cli.py:14`). This keeps the two test files patching the identical functions the production code calls, so a rename of either seam breaks both suites together instead of only one silently trusting the other.
- KTD2. **Drive failures through `CliCase.call("validate", self.manifest_path)`**, the existing helper at `tests/test_cli.py:32`, rather than calling `cmd_validate` directly, so the assertions cover the full `cli.main` dispatch, not just the handler function.
- KTD3. **Add the two cases to the existing `Validate` class** in `tests/test_cli.py` (`tests/test_cli.py:64`) rather than a new class, matching how that class already groups every `validate`-verb behavior.

### High Level Technical Design

```mermaid
flowchart LR
  T[Validate test case] --> P[mock.patch manifest.shutil.which / _run_plugin_query]
  P --> C[CliCase.call('validate', manifest_path)]
  C --> M[cli.main -> cmd_validate]
  M --> V[manifest_module.validate check_environment=True]
  V --> O[printed 'error: ...' lines + EXIT_CONFIG]
  O --> A[assertEqual code, EXIT_CONFIG; assertIn backend name and failure kind in text]
```

### Assumptions

- `self.manifest_path` (from `RunCase.setUp`) names a `claude` backend task by default, matching `tests/test_manifest.py::BackendReadiness`'s fixture, so `"claude"` is the backend name asserted in output.
- No new stub binaries or queue fixtures are needed; the readiness probe is bypassed entirely by mocking `shutil.which` and `_run_plugin_query`, exactly as the manifest-level tests already do, so these tests do not depend on `tests/stub-claude` behavior at all.

---

## Implementation Units

### U1. CLI-level readiness failure tests

- **Goal:** Pin the `validate` verb's exit status and diagnostic text for a missing backend binary and for a missing/below-floor plugin.
- **Requirements:** R1, R2, R3; KTD1, KTD2, KTD3.
- **Dependencies:** None; `manifest_module.validate`'s `check_environment` parameter and `_backend_readiness_errors` already exist on `main`.
- **Files:** `tests/test_cli.py`.
- **Approach:**
  1. In the `Validate` class, add `test_a_missing_backend_binary_exits_config_and_names_the_backend`: patch `manifest_module.shutil.which` to return `None`, call `validate`, assert `code == cli.EXIT_CONFIG` and that the output names `"claude"` and mentions the binary.
  2. Add `test_a_missing_backend_plugin_exits_config_and_names_the_backend`: patch `manifest_module.shutil.which` to return a path and patch `manifest_module._run_plugin_query` to return a `SimpleNamespace(returncode=0, stdout="other-plugin 9.0.0", stderr="")` (mirroring `tests/test_manifest.py`'s `plugin_result` shape), call `validate`, assert `code == cli.EXIT_CONFIG` and that the output names `"claude"` and mentions the plugin, and does not mention a missing binary.
  3. Import `SimpleNamespace` from `types` in `tests/test_cli.py` if not already imported.
- **Patterns to follow:** `tests/test_manifest.py::BackendReadiness.test_missing_binary_names_the_backend_before_launch` and `test_missing_plugin_is_distinct_from_missing_binary` for the mock shapes; `tests/test_cli.py::Validate.test_a_manifest_with_a_broken_rule_exits_config_and_names_the_field` for the CLI-level assertion style.
- **Test scenarios:** The two scenarios in R1/R2 above are the only new scenarios; no other behavior changes.
- **Verification:** `python3 -m unittest test_cli` from `tests/` passes with the two new cases present and failing before the mocks are wired correctly (sanity-checked during implementation, not left in the diff).

---

## Verification Contract

| Gate | Evidence |
| --- | --- |
| Focused CLI tests | `python3 -m unittest test_cli` (run from `tests/`) passes, including the two new cases. |
| Regression suite | `python3 -m unittest discover -s tests` (run from repo root) passes. |

---

## Definition of Done

- `tests/test_cli.py::Validate` has one test asserting `EXIT_CONFIG` and a named backend/binary diagnostic for a missing executable, and one asserting `EXIT_CONFIG` and a named backend/plugin diagnostic for a missing or below-floor plugin, both driven through `cli.main`.
- No production code changes.
- Full suite passes.
