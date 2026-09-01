---
title: Three tests pinning known unenforced-audit misses passed while a mutation made the codex normalizer emit no tool call at all
date: 2026-09-01
category: logic-errors
module: classify
problem_type: logic_error
component: runner
severity: medium
root_cause: missing_validation
resolution_type: test_fix
related_components: [contracts, run, summary]
symptoms:
  - "a test asserting `findings == []` for a known-evasive command spelling stayed green during a mutation pass that made the codex normalizer synthesize zero tool_use calls"
  - "the same emptiness that proves a real evasion (T-65, #54) is indistinguishable from a broken input feeding the matcher nothing to check"
tags: [vacuous-test, unenforced-restrictions, classify, mutation-testing, negative-assertion]
---

# Three tests pinning known unenforced-audit misses passed while a mutation made the codex normalizer emit no tool call at all

## Problem

`#54` pinned three deliberate misses in `classify`'s unenforced-restrictions audit: real T-65 log
lines where a Codex Task ran `python3 -c "import unittest; ..."` instead of the literal
`python3 -m unittest` a manifest's `Bash(python3 -m unittest*)` pattern names, so
`matches_disallow_pattern` correctly finds nothing. Each test's job is to assert that miss stays
a miss, so a future widening of the matcher is a deliberate, reviewed choice rather than a silent
regression.

The first draft of each test asserted only `findings == []`. That assertion is also exactly what
an unrelated bug produces: if the codex event normalizer stopped synthesizing a `tool_use` at all
for a given transcript shape, `classify` would see zero calls, produce zero findings, and the test
would still pass, having audited nothing.

## What Didn't Work

**Asserting the negative alone.** `self.assertEqual(findings, [])` reads as "the matcher correctly
declined to flag this." It equally reads as "nothing was ever offered to the matcher." A mutation
pass on the first draft, deliberately making the normalizer emit no `tool_use` for these
transcript shapes, proved the two are the same test result. Nothing in the assertion distinguishes
a correct miss from a vacuous one.

## Solution

Landed in `e64b9c5`. `tests/test_classify.py`'s `UnenforcedAudit._assert_evaded` pairs the miss
assertion with a companion that the miss is real:

```python
def _assert_evaded(self, command):
    self.assertTrue(classify._unwrap_command(command).startswith("python3 -c"))
    r = self._classify(command, patterns=[self.UNITTEST_PATTERN])
    self.assertEqual(r["tool_calls"], 1)
    self.assertEqual([f for f in r["findings"]
                      if f["class"] == contracts.UNENFORCED_DISALLOWED], [], self.MISS_MSG)
```

`assertTrue(... .startswith("python3 -c"))` proves the shell wrapper was actually unwrapped
before matching. `assertEqual(r["tool_calls"], 1)` proves one call reached the audit at all. Only
after both hold does the empty-findings assertion mean what the test claims it means. A fourth
test, `test_a_wrapped_literal_unittest_spelling_matches_a_manifest_pattern`, is the control: it
runs the same pattern against the literal spelling and asserts a hit, so a typo in the shared
`UNITTEST_PATTERN` constant or a regression in `_SHELL_WRAP` turns every miss test green for the
wrong reason instead of silently passing.

## Why This Works

A test that asserts something did **not** happen only carries information if the test also proves
the code path that could have made it happen actually ran. `findings == []` is the fixed point of
both "the matcher correctly declined" and "the matcher was never invoked" and "the audit's own
input pipeline broke upstream." Distinguishing those requires a second, positive assertion on the
same call: here, that exactly one call reached the classifier and that the unwrap step produced
the expected inner command, so the empty-findings result cannot be explained by the input never
arriving.

## Prevention

**A test that asserts a negative needs a companion assertion that the code path under test
actually ran.** `tool_calls == 1` and `_unwrap_command(...).startswith(...)` are the minimum here;
the general shape is: prove the call was made, prove it reached the audited step, then assert the
audited step declined. Mutation testing (deliberately breaking the input pipeline and checking the
test still fails) is what surfaces a missing companion assertion; a first draft that only reads
right on inspection is not enough on its own.

**This is the same family as the neighboring doc's fixture problem, one layer down.** Where
`shell-wrap-regex-missed-zsh-and-glued-lc-so-the-unenforced-audit-never-saw-a-live-destructive-command.md`
is about a regex agreeing with self-authored fixtures by construction, this is about a negative
assertion agreeing with a broken pipeline by construction. Both hide behind a suite that stays
green.

## Related Issues

- `docs/solutions/logic-errors/shell-wrap-regex-missed-zsh-and-glued-lc-so-the-unenforced-audit-never-saw-a-live-destructive-command.md`
  fixed the zsh unwrap gap this task's tracker card partly, and wrongly, re-alleged as still open;
  replaying the T-65 log against the actual matcher (rather than trusting the card) is what showed
  the unwrap already worked and the real gap was the matcher only ever checking literal command
  spellings, a bound `run.UNENFORCED_BOUND` and `summary.py`'s per-task line now state on the
  record instead of leaving the empty findings list to be misread as a clean run.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the general rule this and the neighboring doc both follow.
