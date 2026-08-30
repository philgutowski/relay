---
title: DENIAL_REGEX anchored immediately after the tool name, so a real Bash denial naming the command in between was never classified
date: 2026-08-30
category: logic-errors
module: classify
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [contracts, classify, fixtures]
symptoms:
  - "a denied Bash tool call in a real transcript produced no denied_tool finding at all"
  - "every synthetic and hand-written denial fixture matched DENIAL_REGEX, so the suite stayed green"
tags: [denial-regex, self-authored-fixtures, real-capture, halt-classification, contracts]
---

# DENIAL_REGEX anchored immediately after the tool name, so a real Bash denial naming the command in between was never classified

## Problem

`contracts.DENIAL_REGEX` in `skills/relay/scripts/relay/contracts.py` identifies a denied tool
call inside a transcript's `user` lines, so `classify.py` can raise `HALT_DENIED_TOOL`. Before
this fix it read:

```python
DENIAL_REGEX = re.compile(r"^Permission to use (\w+) has been denied")
```

Every fixture in `tests/fixtures/transcripts/` matched it, and the suite was green. A real Bash
denial, captured for Backends U1/U5 in
`tests/fixtures/backends/claude/denial-refusal.jsonl`, reads instead:

> Permission to use Bash with command `rm -rf ...` has been denied.

The command name sits between the tool and the verdict. The anchored regex required "has been
denied" immediately after the tool name, so it never matched a real Bash denial. A denied Bash
call went through Relay silently unclassified as `denied_tool`, and nothing in 353+ green tests
could have caught it, because every fixture that exercised the regex was written by the same
hand that wrote it.

## What Didn't Work

Trusting the regex because every existing fixture, synthetic and "hand written to look real,"
passed. `tests/test_contracts.py:95`-`96` asserted the regex against an `Edit` denial with no
clause in between and against `"Edit succeeded"` as the negative case. Neither exercises the
shape a real Bash denial actually has.

## Solution

Landed in `670ff26` (Backends U6.U1) and proven by `3d8a96d` (U6.U5), on `relay/22`, merged as
`b25de7f`.

```python
# Backends U6 found a real Bash denial reads "Permission to use Bash with command <cmd> has
# been denied.", naming the command between the tool and the verdict; a Jira or other named-tool
# denial has no such clause. `.*` (not anchored immediately after the tool name) covers both,
# proven against tests/fixtures/backends/claude/denial-refusal.jsonl, a real capture this
# anchored-immediately form never matched.
DENIAL_REGEX = re.compile(r"^Permission to use (\w+)\b.*has been denied")
```

`tests/test_classify.py`, `ClaudeBackendFixtures.test_the_real_bash_denial_names_bash_and_the_command`,
runs the real capture through `classify()` and asserts the `denied_tool` finding names `Bash`
and carries `rm -rf` in its target.

## Why This Works

`\w+\b.*has been denied` allows anything, including nothing, between the tool name and the
verdict, so it matches both the anchored form (a Jira or other named-tool denial with no
clause) and the real Bash form (a command named in between). The fix cost one line; finding it
cost nothing until a real capture was run through the classifier instead of a fixture written to
the regex's own expectation.

This is the same defect shape
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
names for cross-process contracts, one layer down: there the producer and consumer of a message
contract were both stubbed by the same author, so they agreed by construction. Here the input
and the regex that reads it were both authored by the same hand, so every fixture agreed with
the regex by construction. A test written by the regex's own author cannot fail on a shape the
author did not think to write.

## Prevention

**A regex or parser validated only against fixtures the same author wrote is unproven.** Before
trusting green tests on a string-matching contract, run it against at least one independently
captured real sample, not a fixture written to look real. The Backends U1/U5 work that surfaced
this had already captured real transcripts for other reasons; it was running the classifier
against those captures, rather than against `tests/fixtures/transcripts/`, that exposed the gap.

## Related Issues

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the same shape at the process-contract level: fixtures and parsers written by the same
  hand agree by construction, and only a real producer's output can test the contract. This doc
  is the same rule applied to a single regex rather than a multi-process handoff.
