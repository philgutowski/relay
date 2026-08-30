---
title: The github adapter read only the first thirty board items, so every later card read as absent from the board
date: 2026-08-29
category: logic-errors
module: adapters
problem_type: logic_error
component: adapters
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [verify, run-loop, cli, fixtures]
symptoms:
  - "relay validate --list printed 30 candidates against a GitHub Project holding 39 items, with no warning"
  - "issues #33 to #40, all open and on the board, were missing from the candidate list"
  - "a card the board carried read as absent, which the adapter reports as (None, None): not terminal, and not skipped"
tags: [github-adapter, gh-cli, pagination, default-limit, partial-landing, silent-truncation, validate-list, tracker-adapter]
---

# The github adapter read only the first thirty board items, so every later card read as absent from the board

## Problem

`skills/relay/scripts/relay/adapters/github.py` reads the whole GitHub Project once, through
`gh project item-list <n> --owner <o> --format json`, and answers every board question from that
list: the candidate list `validate --list` prints, and the per card status Verify-landed reads
when it asks whether a card reached the `status_field`. The call passed no `--limit`, and `gh`
returns its first thirty items when none is given. Project 4 on `philgutowski/relay` crossed
thirty items on 2026-08-29, and from that moment every card past the thirtieth was invisible to
the Runner.

Nothing crashed. `_project_status()` (`github.py:74`) walks the items, finds no match, and
returns `(None, None)`, the shape reserved for a card the board genuinely does not carry, which
`status()` (`github.py:134`) reads as not terminal. Verify-landed treats a code scope that passed
beside a tracker check that failed as `partial_landing` (`verify.py:305`). So a Task whose code
had merged and pushed, and whose Closeout had moved the card to Done, would still have been
recorded as a partial landing, with a Cause line telling the operator to move a card that had
already moved. A closed issue is terminal on its own (`github.py:127`), so the misclassification
reaches only cards moved on the board without the issue being closed, which is exactly what a
`status_field` manifest asks the Closeout to do.

## Symptoms

- `relay validate --list` listed thirty candidates against a thirty nine item board, ending at
  issue #30, and issues #33 to #40 were absent although all were open and on the board.
- Nothing in the output said the list was cut. `validate` prints what the adapter returns and
  never compares it against anything.

## What Didn't Work

- Reading the candidate list as evidence the board was fine. The list looked complete because
  it had no gaps inside it; the truncation was at the end, where a reader does not look.

## Solution

Pass a limit well above any board Relay will drive, and pin it in the adapter test
(commit f4fc4c5, merge 16494e1, issue #42):

```python
# github.py
PROJECT_ITEM_LIMIT = 500

    def _items(self):
        payload, reason = self._gh([
            "gh", "project", "item-list", str(self._project_number),
            "--owner", str(self._owner), "--format", "json",
            "--limit", str(PROJECT_ITEM_LIMIT),
        ])
```

```python
# tests/test_adapters.py
def test_the_item_list_asks_for_more_than_the_thirty_gh_returns_by_default(self):
    run = self.run_for()
    self.github(run).candidates()
    call = run.calls[0]
    self.assertIn("--limit", call)
    self.assertGreater(int(call[call.index("--limit") + 1]), 30)
```

## Why This Works

The adapter's one board read now asks for the whole board, and the test fails if the flag is
dropped or lowered to the `gh` default. The test asserts the argument list rather than the
returned items because the fixture (`tests/fixtures/tracker/github_project_items.json`) holds two items
and can never show a page bound whatever the flag says.

## Prevention

- A tracker read that can be truncated by a default the tool owns, page size, limit, first N,
  must name its bound explicitly in the adapter, and the test must assert the bound is passed.
  The fixture cannot prove it: a stubbed transport returns whatever the fixture holds, so a
  two item fixture agrees with every limit by construction. This is the fourth shape of
  `stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`, beside
  a stubbed seam, a stubbed subprocess, and a documented flag list.
- Check the jira adapter the same way when its search is next touched: a JQL search has a
  server side page size too.

## Related Issues

- Issue #42 (fixed). Found while authoring the round six manifests, before any run read the
  truncated board in anger.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
