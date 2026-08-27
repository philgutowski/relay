---
title: The digest-key contract guard's own regex was blind to closeout_digest and (x or {}) reads, so it silently checked nothing for closeout.py
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: medium
root_cause: missing_validation
resolution_type: test_fix
related_components: [classify, closeout, contracts, run-loop]
symptoms:
  - "_digest_keys_read(CLOSEOUT_PY_PATH) returned an empty set, so the new cross-module digest-key guard passed even though it was not actually inspecting closeout.py's reads"
  - "the guard's regex only matched a bare or single-dotted digest identifier (digest.get(...), ctx.digest.get(...)), so closeout.py's closeout_digest.get(...) calls never matched"
  - "the guard also missed the (digest or {}).get(...) fallback wrapper pattern closeout.py uses"
tags: [digest-keys, contract-guard, regex-blind-spot, classify, closeout, self-testing-guard, cross-module-contract]
---

# The digest-key contract guard's own regex was blind to closeout_digest and (x or {}) reads, so it silently checked nothing for closeout.py

## Problem

T-1 added `contracts.DIGEST_KEYS`, the guaranteed set of keys `classify.classify()` sets, plus a
`test_contracts.py` guard that fails if `run.py` or `closeout.py` reads a digest key outside that
set, or if `classify.py` stops setting a key either reader depends on. The first version of that
guard's own regex, meant to find every `digest.get("key")` style read in the two reader modules,
only matched a bare `digest` identifier or one with a single dot prefix (`digest.get(...)`,
`ctx.digest.get(...)`).

## Symptoms

`closeout.py` names its local variable `closeout_digest`, not `digest`, and also reads it through
a `(digest or {}).get(...)` fallback wrapper in at least one spot. Neither shape matched the
original regex, so `_digest_keys_read(CLOSEOUT_PY_PATH)` returned an empty set. The guard's two
tests still passed, an empty set is trivially a subset of `DIGEST_KEYS` and trivially satisfied
by whatever `classify()` returns, but the guard was not testing closeout.py's reads at all. A
contract guard whose own detection is incomplete agrees with the code by construction on the
exact module it exists to check, the same shape as the stubbed-seams finding this repo already
documented, just one seam over.

## What Didn't Work

Nothing was attempted and rejected here; the gap was caught before landing, in the same session
that wrote the guard, by rereading closeout.py's actual variable names against the regex rather
than trusting that the test passing meant the check was exercised.

## Solution

Broadened both regexes in `test_contracts.py` to match any identifier ending in `digest` (bare,
dotted, or suffixed like `closeout_digest`) and to also match the `(x or {})` fallback wrapper:

```python
DIGEST_GET_RE = re.compile(
    r"\(?\s*((?:\w+\.)*\w*digest)\s*(?:or\s+\{\}\s*\))?\s*\.get\(\s*[\"']([A-Za-z_]+)[\"']"
)
```

Confirmed the fix actually changes coverage, not just style, by checking `_digest_keys_read`
returns a non-empty set for `closeout.py` after the change, where before it returned empty.

## Why This Works

A guard that greps source for a naming pattern is only as complete as the patterns it enumerates,
and enumerating patterns by guessing rather than by reading the actual reader files first will
miss whatever those files happen to spell differently. The first version was written against the
shape `run.py` uses (`digest`) without independently confirming `closeout.py` uses the same
shape; it does not.

## Prevention

When a new regex-based or grep-based guard is meant to cover multiple source files, print or
otherwise inspect what it actually matched in each target file before trusting a passing test,
not just what the test's assertion concludes from the match. An empty match set that still
satisfies a subset/superset assertion is invisible unless checked directly.

## Related Issues

- [docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md](stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md) is the same general shape: a check that agrees with the code by construction because nothing forced it to actually exercise the seam it was meant to guard.
