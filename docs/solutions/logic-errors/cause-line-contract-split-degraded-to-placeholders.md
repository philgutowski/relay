---
title: The cause line contract lived in two places and every mismatch degraded to a question mark instead of failing
date: 2026-08-26
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [summary, contracts, halt-evidence, run-loop]
symptoms:
  - "every blocked task, the most common non landing outcome in the system, printed \"blocked: ?\" instead of the blocker text"
  - "the unexpected error class printed its raw template, braces and all, because the hand written defaults list had gone stale"
  - "every crashed task read \"runner died during halted\", the record's post crash status shadowing the evidence field of the same name"
  - "evidence a raiser passed as a dict or a list supplied nothing at all, since line_fields drops both"
  - "nothing raised, nothing failed, and the full suite stayed green while six of the cause lines were wrong"
tags: [cause-line, halt-lines, str-format, template-drift, silent-placeholder, field-shadowing, table-driven-test, run-summary]
---

# The cause line contract lived in two places and every mismatch degraded to a question mark instead of failing

## Problem

Relay is an unattended outer loop: it launches one headless `claude -p` per tracker task, serially,
for hours, and when a task does not land the operator's entire readout is the run summary's cause
line, one sentence per halt class. `contracts.HALT_LINES` holds eighteen templates, one for each
of the sixteen halt classes plus two findings that print without being a class of their own. Six of
the sixteen classes rendered a placeholder or a raw template where the evidence should have been,
because the template table and the raisers that fill it are two separate places and nothing joined
them.

## Symptoms

Nothing failed. The run completed, the summary printed, the suite was green, and the sentence an
operator was supposed to act on said nothing. These are the six lines as they actually rendered
before commit `5eb615f`, reproduced from the pre fix `LINE_FIELD_DEFAULTS` and the pre fix raiser
evidence:

```
timed out after ? active minutes (? wall); tree dirty on relay/T-1
landed at ? but card reads ?
runner died during halted; tree ? on relay/T-1
closeout changed ? outside ?
blocked: ?
the runner hit an unexpected {error_type} on {task}: {error}
```

Each has its own small reason and they are all the same shape.

- **timeout.** `_timeout_route` wrote `active_seconds` and `wall_seconds` into the digest. The
  template at `contracts.py:229` names `{active_minutes}` and `{wall_minutes}`. Different keys, so
  both defaulted.
- **partial_landing.** The raiser passed `{"checks": final.checks}` and nothing else.
  `summary.line_fields` at `summary.py:42` to `:48` skips `dict` and `list` values on purpose, since
  neither can be formatted into a sentence, so that raiser supplied no fields at all against a
  template naming `{sha}` and `{card_status}`.
- **runner_crashed.** The template named `{status}`. So does the task record, and `_task_entry`
  passed the record *after* the evidence, so the record's post crash `status` won. Every crashed
  task read "runner died during halted", never what it was doing when the runner died. The same
  template also named `{tree}`, which the reclaim path in `state._mark_crashed` at `state.py:254`
  cannot know: it usually runs in a later process that never saw the repository.
- **closeout_out_of_scope.** The evidence carried `offending` and `reset_to`; the template at
  `contracts.py:220` names `{path}` and `{allowed}`.
- **blocked_envelope.** `_blocked_route` recorded `{"stranded_head": stranded["head"]}` and nothing
  else, against a template that is only `blocked: {blocker}`. This is the most common non landing
  outcome the system has, and it printed nothing.
- **unexpected_error.** `LINE_FIELD_DEFAULTS` was a hand written dict that had gone stale against
  the templates it was meant to backstop. It had no `error_type` and no `error`, so a record without
  those keys raised `KeyError` inside `format`, the bare `except (KeyError, IndexError, ValueError)`
  in `cause_line` returned the raw template, and the operator got the braces.

That last one is worth stating precisely, because it is the sharpest version of the whole problem.
`HALT_UNEXPECTED_ERROR` and its template did not exist when the defaults list was written. They were
added by `eae48d5`, the commit that applied fourteen findings from the code review, to close a P0
where an unanticipated exception escaped the run loop as a traceback. That commit touched
`contracts.py` and never touched `summary.py`. The old defaults already had `task`, so two of the
three new fields were missing and the line broke the moment it was created. A fix pass that had just
been validated by an independent reviewer created a broken cause line, and no test noticed, because
no test rendered the set.

The reason none of this failed loudly is that `summary.cause_line` at `summary.py:51` to `:59`
swallows the mismatch **by design**. `LINE_FIELD_DEFAULTS` fills every missing key with `"?"`, and
the bare except returns the template unformatted. That net exists so a record that halted before its
evidence was filled still produces a readable line instead of crashing the summary. It is a good
net, and it is exactly why every one of these degraded silently.

## What Didn't Work

**An eight reviewer code review of the U4 to U11 milestone.** It read the raisers one at a time and
found three of the six, filed as one umbrella finding, "Operator facing cause lines render
placeholders instead of evidence", with three specifics under it: findings 22, 23 and 25 in
`docs/ideation/2026-08-25-relay-review-residuals.md`, which are the timeout, the partial landing,
and the runner crashed record shadowing. That is real work and the fixes came straight from it.

It could not find the other three, and the reason is structural rather than a reviewer failing. A
per site review looks at one raiser and one template at a time. It can only catch a mismatch where
both halves happen to be in the same field of view. The three it missed are precisely the ones where
the pair is invisible from either end alone: `blocked: ?` needs you to notice that the class is
chosen from the digest at `run.py:305`, so the one raiser has to cover every class that can route
through it; `closeout changed ? outside ?` needs you to hold two key names from two files in your
head at once; and the stale `LINE_FIELD_DEFAULTS` is not visible from any raiser at all, because it
is a property of the table against itself. Eighteen templates against roughly thirty sites that
build halt evidence is a set level fact, and no amount of per site reading enumerates a set.

**A set level test that already existed.** This is the part that should change how the rule is read.
`tests/test_contracts.py:34`, `test_every_class_that_can_print_has_a_cause_line_and_nothing_else_does`,
already asserted `set(contracts.HALT_LINES) == set(contracts.LINE_CLASSES)`. It walks the whole set
and it is a good test. It proves every printable class has a template and that nothing else does. It
says nothing about whether any template can be filled, because it never renders one. So the rule is
not "write a set level test". It is **write a set level test that exercises the thing that can be
wrong**, which here is rendering, not membership.

**The one assertion on a cause line anywhere in the suite.** `tests/test_cli.py:161` asserted
`self.assertTrue(entry["cause"])`, and the line after it checked that whatever the cause said also
appeared in the rendered text. Both pass on `blocked: ?`. The test pinned the wrong property: that a
cause line exists and that the JSON and the text agree, which is worth keeping, but never whether
the sentence carries any information. `?` is truthy and it renders. There was no
`tests/test_summary.py` at all.

**Fixing the found findings one at a time.** Applying 22, 23 and 25 as three separate edits would
have shipped three correct lines and left three broken ones, with no reason to suspect they existed.

## Solution

Landed in commits `5eb615f` and `1749fbf`, merged as `9f0a1df`. All three are reachable from `main`
in the current tree.

**Derive the defaults from the templates.** A hand written list goes stale the moment a template
gains a field, and it fails in the wrong direction when it does: the missing key raises inside
`format`, the except swallows it, and the operator gets braces instead of a sentence.

Before:

```python
LINE_FIELD_DEFAULTS = {
    "ref": "?", "blocker": "?", "last_message": "?", "tool": "?", "target": "?", "sha": "?",
    "path": "?", "allowed": "?", "status": "?", "tree": "?", "branch": "?", "name": "?",
    "required": "?", "log": "?", "card_status": "?", "active_minutes": "?", "wall_minutes": "?",
    "minutes": "?", "url": "?", "task": "?",
}
```

After, at `summary.py:23` to `:39`:

```python
def _template_fields():
    fields = {}
    for template in contracts.HALT_LINES.values():
        for _, field, _, _ in string.Formatter().parse(template):
            if field:
                fields[field] = "?"
    return fields


LINE_FIELD_DEFAULTS = _template_fields()
```

**Render weakest source first.** `_task_entry` passed the evidence, then the landing, then the
record, so later sources won and the record beat the evidence. After, at `summary.py:79` to `:81`:

```python
# Weakest source first. The record carries fields a template may also name, most
# of them written after the halt, so the evidence the raiser recorded has to win.
"cause": cause_line(record.get("halt_class"), record, landing, evidence),
```

**Give the crash template a name no record field can shadow.** At `contracts.py:225`, `{status}`
became `{status_before}`, and `{tree}` was dropped:

```python
HALT_RUNNER_CRASHED: "runner died during {status_before} on {branch}",
```

`state._mark_crashed` at `state.py:260` to `:264` already wrote `status_before`; every other raiser
was renamed to match, at `gitwrite.py:337` and `:361`, `run.py:288` and `run.py:499`.

**Make each raiser record the keys its class names.** The timeout digest at `run.py:345` to `:350`
now carries both units, seconds as the measurement and minutes as what the line names, deliberately
rather than converting at render time:

```python
ctx.digest["timeout"] = {
    "tree": disposition.tree, "branch": disposition.branch,
    "active_seconds": ctx.launched.active_seconds, "wall_seconds": ctx.launched.wall_seconds,
    "active_minutes": round((ctx.launched.active_seconds or 0) / 60.0),
    "wall_minutes": round((ctx.launched.wall_seconds or 0) / 60.0),
}
```

The blocked route at `run.py:456` to `:467` now covers every class that can route through it,
because the class arrives from the digest rather than being fixed at the call site:

```python
envelope = ctx.digest.get("envelope") or {}
blockers = envelope.get("blockers") or []
evidence = {
    "stranded_head": stranded["head"],
    "branch": stranded["branch"] or ctx.branch,
    "blocker": blockers[0] if blockers else "no blocker text in the envelope",
    "last_message": ctx.digest.get("last_message") or "(no final message)",
}
evidence.update(ctx.digest.get("timeout") or {})
```

The partial landing raiser at `run.py:433` stops reaching into the checks dict by hand and calls a
named accessor, `verify.card_status_of`, added at `verify.py:242` to `:249`, which returns the
tracker status the card read, or the reason it could not be read, rather than a placeholder:

```python
{"sha": tail.merge_sha, "card_status": verify.card_status_of(final),
 "branch": ctx.default, "checks": final.checks}
```

The closeout scope raiser at `run.py:508` to `:513` records `path` and `allowed` alongside the
`offending` and `reset_to` it already kept, and, after finding 21 in `1749fbf`, takes its class from
`scope.halt_class` so a dirty tree inside the allowed paths reports as `unclean_exit` rather than
out of scope. The gate refused raisers at `run.py:406`, `:422` and `:521` gained `branch`, `sha` and
`log`.

## Why This Works

The root cause is not six typos. It is one contract living in two places with nothing joining them.
The set of fields a cause line needs is declared in `contracts.HALT_LINES`. The set of fields
actually supplied is decided independently at each of roughly thirty sites that build halt evidence
across `run.py`, `gitwrite.py`, `state.py` and `classify.py`. Python's `str.format` will not tell you about the gap,
because `line_fields` fills the hole before `format` ever sees it, and the bare except covers
whatever slips past that. There is no point in the program where the two halves meet, so there is
nowhere for a mismatch to be caught, which means every mismatch is silent by construction.

The three fixes each remove one way the two halves could disagree without anyone noticing. Deriving
`LINE_FIELD_DEFAULTS` from the templates removes the possibility of the backstop lagging the thing
it backstops; the defaults can no longer be stale because they are no longer written down. Ordering
the sources weakest first removes an entire class of collision, where a name that happens to exist
in both the record and the evidence silently resolves to the wrong one; renaming to `status_before`
closes the specific collision that existed. Naming the keys at each raiser is the part that is just
work, and it stays correct only because of the test below.

The generalisable claim: **a contract split between a declaration and many independent fulfilment
sites cannot be verified by reading either side.** Per site review sees one fulfilment against one
declaration and is blind to the pairing as a set. The only thing that sees the set is a test that
iterates the declaration and **exercises** every entry. This is not specific to format strings. It
is true of any table of required keys, any registry of handlers keyed by a class, any schema whose
producers are scattered.

The word doing the work in that sentence is "exercises". `test_contracts.py:34` iterates the same
declaration and compares two key sets, and it was no help at all, because membership was never what
was broken. A set level test is only as good as the operation it performs on each member. Pick the
operation the production code performs.

## Prevention

**The artifact that pins this is `tests/test_summary.py`**, which did not exist before `5eb615f`. It
is a table, not a list of cases. `RECORD_ROWS` holds one row per halt class that becomes a record's
own class, and `FINDING_ROWS` holds one row per class that attaches as a finding. Each row is a
three tuple of the evidence its production raiser actually records, any extra record fields, and a
citation naming the function that raises it. A `RECORD_ROWS` row is that three tuple; a
`FINDING_ROWS` row is a pair, the finding dict and its citation, because a finding renders from its
own dict with no record behind it. The module docstring states the discipline the table depends
on: a row is a copy of what the cited raiser passes, not what would make the line read
nicely, and changing a raiser's keys means changing its row in the same commit.

Five guards hold it together, seven test methods across two classes, and each guard pins a
different failure mode:

```python
def test_the_table_covers_every_class_in_halt_lines(self):
    covered = set(RECORD_ROWS) | set(FINDING_ROWS)
    self.assertEqual(sorted(covered), sorted(contracts.HALT_LINES))

def test_no_placeholder_survives_a_record_cause_line(self):
    for halt_class, (evidence, fields, raiser) in sorted(RECORD_ROWS.items()):
        with self.subTest(halt_class=halt_class, raiser=raiser):
            self.store.upsert("T-1", halt_class=halt_class, halt_evidence=evidence,
                              wall_seconds=1.0, active_seconds=1.0, findings=[], **fields)
            entry = self.summarise(["T-1"])["tasks"][0]
            self.assertNotIn("?", entry["cause"], ...)
            self.assertNotIn("{", entry["cause"], ...)
```

- `test_the_table_covers_every_class_in_halt_lines` asserts the table covers `HALT_LINES` exactly,
  so a new class added without a row fails rather than reaching an operator untested. This is the
  guard that `eae48d5` would have tripped.
- `test_no_placeholder_survives_a_record_cause_line` and its finding line twin render every row
  through the real `summary.build` from a real state file and refuse a surviving `?` or `{`. The two
  assertions are separate on purpose: `?` catches a key nobody supplied, `{` catches a `KeyError`
  that fell through to the raw template.
- `test_a_record_field_never_shadows_the_evidence` pins the shadowing defect directly. It writes
  `status_before` and `branch` into the evidence while the record carries a different `branch`, and
  asserts the evidence wins.
- `test_an_empty_record_still_renders_rather_than_raising` keeps the safety net honest. It renders
  every class from an empty record and asserts the line is still non empty and still has no braces,
  so the net stays a net rather than becoming the thing that hides the next mismatch.
- `CauseLinesFromARealRun` takes three tasks from an actual run of the loop over the stub, because
  the table above is hand written and a row can drift from its raiser. Its timeout case is where
  finding 22 came from.

**A constraint the fix did not remove, and the table only partly covers.** `line_fields` at
`summary.py:42` to `:48` still drops `dict` and `list` values. That is correct, since neither
formats into a sentence, but it means an evidence key a template names must be a scalar. The fix
corrected the raisers that passed a structured value; it did not teach the filter to refuse one. The
next raiser that passes a dict under a name a template happens to use will fail the same silent way,
and the table catches it only for rows someone remembers to write.

**Three renderers of this contract existed, and the first fix reached one.** Closed 2026-08-26:
`classify.cause_line` was deleted (its only output was an unread digest key, and four of its fields
were hard coded to `?` because the git and tracker evidence does not exist at classify time), and
`classify.finding_line` now delegates to `summary.cause_line`, pinned by the same `FINDING_ROWS`
table rendered through both entry points in `tests/test_classify.py`. The paragraph below records
the state before that, for the shape of the trap. `summary.cause_line` is now derived from the templates. `classify.cause_line` at
`classify.py:267` and `classify.finding_line` at `classify.py:289` are two more renderers of the
same `HALT_LINES` dict, each with its own hand written field dict and its own bare except returning
the raw template. `classify.cause_line` hard codes four of its keys to the literal `"?"`.
`classify.finding_line` setdefaults five by hand. Both are the stale hand written list the fix
deleted from `summary.py`, still present, one function away.

`classify.finding_line` matters more than the other, because it is not a console path.
`closeout.py:118` builds the Other findings bullets of the closeout brief from it, and the brief is
the data block the Closeout process reads when it writes the tracker card. A template that gains a
field degrades what reaches a card, not just a terminal line. `tests/test_summary.py` does not cover
it: its finding line test renders through `summary.build`, never through `classify.finding_line`. `classify.cause_line`'s output is
asserted in `tests/test_classify.py` and consumed by nothing in `run.py`, `state.py` or
`closeout.py`, so it may be dead and worth deleting rather than fixing. Establish that before
writing a table for it.

**One more contract in this codebase has the same shape.** The classify digest is a plain string
keyed dict with no shared contract test. It is built literally at `classify.py:166` to `:180`, and
its consumers read it by string key in two other modules, `run.py` and `closeout.py`. A renamed key
returns `None` silently, which is the same failure mode as a missing format field with the same
absence of any joining point. `docs/ideation/2026-08-25-relay-review-residuals.md` names it in its
residual risks section. It deserves the same table.

**The check to apply going forward.** Is there a declaration listing required names, and are the
things that satisfy it scattered? If so, write the test that walks the declaration and performs the
production operation on each entry. Do not review the sites, and do not settle for a test that walks
the declaration comparing names.

## Related Issues

- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` came out of the same
  milestone and the same review, and its generalisation is the other half of this posture. There, a
  single guard whose precondition was the condition that made the guard unnecessary, findable by
  forcing the excluded branch. Here there is no branch to force: the defect is distributed across a
  set, and the instrument enumerates the set rather than steering into a corner of it. That doc's
  audit list names the places in this codebase shaped like a guard. This one names the places shaped
  like a split contract. The check going forward is both questions, not one.
- `docs/ideation/2026-08-25-relay-review-residuals.md` is the review trail: findings 22, 23 and 25,
  each now marked applied, plus the record of what the table found beyond them.
- `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`, KTD6, is the decision this defect sat
  inside. It fixes the halt class **names** as a closed set living in `contracts.py`, so classify,
  verify and summary share one set. It said nothing about the **fields** inside each line, and the
  defect is the consequence of a contract centralised for names and decentralised for fields. The
  plan's own halt class table is a third, hand maintained prose copy of the same contract and has
  drifted from the shipped templates.
- The same shape bit the tooling in the same twenty four hours, outside this repo. A code review
  helper demoted every finding because the producer wrote `evidence[0]` and the consumer read
  `first_evidence`. Producer names one field, consumer reads another, nothing checks the two against
  each other, and the failure is a silent degradation rather than an error. It is filed outside this
  repository, in the sibling workspace corpus under the Integrel project, as
  `ce-code-review-findings-mechanics-demotes-without-first-evidence` in that corpus's operations
  category. Nothing in this repository resolves that path; it is named so the pair can be found.
