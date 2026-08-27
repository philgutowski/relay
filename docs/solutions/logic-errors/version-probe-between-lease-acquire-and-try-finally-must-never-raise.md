---
title: A version probe placed between lease acquire and the run() try/finally must fail closed, never raise
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [launch, state-store, lease]
symptoms:
  - "a helper call placed after store.acquire() but before the run() try/finally would strand the lease on any exception it doesn't catch"
  - "text=True subprocess decoding raised UnicodeDecodeError, a ValueError subclass the original except tuple (OSError, subprocess.TimeoutExpired) missed"
tags: [lease, try-finally, cli-version, subprocess, fail-closed, code-review]
---

# A version probe placed between lease acquire and the run() try/finally must fail closed, never raise

## Context

T-2 added `launch.cli_version(env)`, a `claude --version` probe run once per `run()` call, so
`state.json` can show drift between the pinned `contracts.CLI_VERSION_TESTED` and the CLI
actually installed. The call was placed in `run.py` after `store.acquire()` succeeds (line
~129) but before the `try/finally` that guarantees `store.release()` (line ~156), and after
the two early-return paths (`EXIT_CONFIG`, `EXIT_LEASE`) that never need the version.

That placement makes `cli_version()`'s exception contract load-bearing: it runs in a gap the
`finally` doesn't cover, so any exception it doesn't catch skips `store.release()` and strands
the lease. Three independent code review passes converged on the same gap: the original
`except (OSError, subprocess.TimeoutExpired)` missed `UnicodeDecodeError`, raised by `text=True`
decoding a non-UTF-8 byte in the binary's stdout, which is a `ValueError` subclass.

## Guidance

When a helper call is deliberately placed outside a `try/finally` that owns cleanup (here,
deferred past the early-return paths so config/lease errors don't pay for a blocking subprocess
call they'd discard), that placement makes the helper's "never raises" contract a correctness
requirement, not a style preference. `except (OSError, subprocess.TimeoutExpired)` around a
`subprocess.run(..., text=True)` call is not enough on its own; add `ValueError` (or catch
`UnicodeDecodeError` explicitly) to also cover text-mode decode failures.

## Applies when

Reviewing or adding any call between a resource acquire (a lease, a lock, a connection) and the
`try/finally` that releases it. Check the callee's actual except clause against every exception
its own body can raise, including exceptions decoding/parsing raises rather than the operation
itself: `subprocess.run(text=True)` can raise `UnicodeDecodeError` even when the process starts
and exits cleanly.
