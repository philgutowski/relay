---
title: An allow-list flag grammar rejected unrecognized flags but not a dropped required one
date: 2026-08-30
category: logic-errors
module: tests/stub-claude
problem_type: logic_error
component: runner
severity: medium
root_cause: missing_validation
resolution_type: code_fix
related_components: [adapters, run-loop]
symptoms:
  - "codex and grok stub parse_args() raised on an unrecognized flag but accepted an argv missing --sandbox or --permission-mode"
  - "a build_args regression that drops a required flag would pass the exact test suite meant to catch that class of drift"
tags: [stub-cli, flag-grammar, allow-list, required-flags, code-review, adversarial-review]
---

# An allow-list flag grammar rejected unrecognized flags but not a dropped required one

## Problem

Relay's `codex` and `grok` test stubs (`tests/stub-claude/codex`, `tests/stub-claude/grok`,
Backends U12) each parse their own argv into a flags dict, so the suite can assert the runner
built the right command line for each backend. Both `parse_args()` functions raised
`ValueError` on an unrecognized flag, which reads as a strict grammar, but neither checked that
every flag `build_args` is supposed to always emit was actually present. An argv missing
`--sandbox` (codex) or `--permission-mode` (grok) parsed cleanly.

## Symptoms

- `codex`'s `parse_args` accepted argv with `--sandbox` silently dropped; the flags dict just
  had no `sandbox` key.
- `grok`'s `parse_args` accepted argv with `--permission-mode` silently dropped, same shape.
- No test in the U12 suite exercised this, because the happy-path tests only ever passed argv
  the stub's own `build_args` had built.

## What Didn't Work

Nothing was attempted and failed here; the gap simply existed unnoticed until an adversarial
code review pass (`ce-code-review`, applied in `bbf8f9e`, flagged P1 by two independent
reviewers) asked the allow-list a question its own test suite never asked: what happens when a
required flag is missing, not renamed or misspelled.

## Solution

Landed in `bbf8f9e`. Each stub now tracks which flags it actually saw and checks that set
against the flags `build_args` always emits, raising if any are missing.

`tests/stub-claude/codex`:

```python
# Every flag build_args always emits, --add-dir excluded since it is zero-or-more.
_REQUIRED_FLAGS = (_VALUE_FLAGS | _BOOL_FLAGS) - {"--add-dir"}

def parse_args(argv):
    ...
    seen = set()
    ...
        if arg in _BOOL_FLAGS:
            flags[arg[2:]] = True
            seen.add(arg)
        elif arg in _VALUE_FLAGS:
            ...
            seen.add(arg)
        ...
    missing = _REQUIRED_FLAGS - seen
    if missing:
        raise ValueError("missing required flag(s) %r" % sorted(missing))
```

`tests/stub-claude/grok` adds the same `seen` set and a `missing = _SINGLE_VALUE_FLAGS - seen`
check after its parse loop.

## Why This Works

An allow-list check (`arg in _VALUE_FLAGS` else raise) proves that everything present in argv
is recognized. It says nothing about whether argv is complete, because a shorter argv is just
as easy to satisfy the allow-list as a correct one. Catching an addition or a rename is a
membership test; catching an omission needs a second test, over the complement, that the
required set was actually seen. The two checks are independent and a grammar that only does the
first looks strict without being complete.

This matters specifically because the stub's job is to catch a `build_args` regression, and the
regression class most likely to slip past a manual diff read is exactly a dropped flag, a line
deleted rather than typoed. An allow-list-only grammar has a blind spot precisely where the stub
most needs to see.

## Prevention

**Any allow-list-shaped grammar check in this repo needs an explicit required-set check
alongside it, not just the allow-list.** When writing a parser whose job is to validate an
external call's shape (a CLI's argv, a request's headers, a config file's keys), the allow-list
proves no extra or misspelled entries; a separate `required - seen` check is what proves nothing
was dropped. The happy-path test alone will not expose the gap, because a correct argv always
satisfies both checks at once; the case that exposes it is a deliberately truncated argv, which
is worth adding as its own test once the required-set check exists.

## Related Issues

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the neighboring lesson about this same suite: a stub written alongside the thing it checks
  tends to agree with it by construction. This doc is a narrower instance of the same family,
  found by adversarial review rather than a live run, where the gap was not the stub agreeing
  with a fixture but the stub's own grammar checking only one direction of correctness.
