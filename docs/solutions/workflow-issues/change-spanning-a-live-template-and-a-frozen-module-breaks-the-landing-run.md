---
title: A change spanning a live template and a frozen module breaks the run that lands it, and the halt names the correct half
date: 2026-08-27
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: high
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [closeout, brief, run-loop, contracts, classify]
applies_when:
  - "Relay is running against its own repository, so the Runner's package directory and the repository the Runner merges into are the same tree"
  - "one Task lands a change touching both a file under skills/relay/templates/ and a module under skills/relay/scripts/relay/, where the two halves fill each other"
  - "a halt names a template as the thing that is wrong, and that template at head reads correct"
  - "the pair the halt says disagree is green under a fresh test process"
  - "any long lived agent driver reads some of its own inputs from disk per use and holds the rest as imported code"
symptoms:
  - "T-7's gate passed at 452 tests, its merge landed at b6ec289 at 20:45:43 EDT, its push succeeded, and the same Task then halted"
  - "the halt reads \"the runner hit an unexpected BriefError on T-7: closeout template names an unknown placeholder 'learnings'\""
  - "the halt class is unexpected_error, the least specific class in the closed set, because a BriefError out of the Closeout has no class of its own"
  - "brief-closeout.md at head names $learnings and closeout.py at head supplies it, so both halves read correct"
  - "the run's briefs directory holds T-4.closeout.md and T-6.closeout.md and no T-7.closeout.md, because the render raised before the file was written"
tags:
  - self-hosting
  - long-lived-process
  - stale-import
  - split-contract
  - string-template
  - closeout-brief
  - unexpected-error
  - resume-not-patch
---

# A change spanning a live template and a frozen module breaks the run that lands it, and the halt names the correct half

## Context

`docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`
establishes the line this doc sits on. A Runner is one Python process for the length of a Manifest,
so every module in `skills/relay/scripts/relay/` is bound at import time and stays bound however
long the run lasts, while the brief templates in `skills/relay/templates/` are opened at the moment
of use and therefore change under a running Runner the instant a merge lands. That doc draws the
consequence one way only: the run cannot **observe** the code its own Tasks land, so its own record
is not evidence about that change.

The other consequence is this one. A change touching both sides of that line does not merely go
unobserved. It **breaks the run that lands it**, at the first render after the merge, with a message
pointing at the half that is correct.

That the passive consequence was documented first is not an accident of ordering, and it explains
why the active one went unanticipated. The parent doc was written roughly three hours before T-7
ran, from T-2, whose change touched only the frozen half, `state.py` and `run.py`. A frozen only
change produces silent staleness, so the failure mode read as harmless. The live template read was
touched in that same write, but only as a citation correction, replacing an invented
`brief.load_template` identifier with the real call path, and it was resolved as a naming fix rather
than developed into the asymmetry that matters (session history).

Round three of Relay on Relay, `~/.relay/manifests/relay-self-round3.toml`, four Tasks, the markdown
Tracker adapter, `local_merge` Shipping mode. **Three Runner processes drove this Manifest, and
which one was resident is the whole question**, so the sequence matters:

| Runner | Launched | Did |
|---|---|---|
| first | 17:11:14 EDT | landed T-4, halted on T-5 |
| second | 19:08:40 EDT | promoted T-5, landed T-6, **halted on T-7** |
| third | 20:53 EDT, inferred | promoted T-7, run completed 20:56:20 EDT |

The first two launch times are read from durable file birth times, shown under Examples below. The
third is inferred from the hand commit at 20:53:20 and the terminal record at 20:56:20, because that
Runner promoted T-7 at Verify-landed without launching a Task process and therefore wrote no new log
to date itself by. Marked as inferred rather than quietly presented as observed, since picking the
right anchor for a launch time is the subject of this doc.

T-7 was ordered last on purpose, because its Manifest comment says it moves the envelope grammar and
the operator owes it a live task against a throwaway target afterward.

T-7's job was to add one optional `learnings` key to the return envelope, so the Closeout process
feeds `ce-compound` the Task's own account of what it learned instead of only `status`, `blockers`,
`changed_files` and `plan_path`. That change necessarily spans both sides of the frozen line, and
T-7's own `changed_files` says so: `contracts.py`, `classify.py` and `closeout.py` on the frozen
side, `brief-local-merge.md`, `brief-pr-terminal.md` and `brief-closeout.md` on the live side. Three
templates and three modules in one commit, and the pair that fill each other are `brief-closeout.md`
and `closeout.py`.

The sequence that followed is the learning. `_merge_route` at `run.py:385` runs the gate, merges,
verifies the code scope, and only then launches the Closeout at `run.py:435`. The gate passed on the
task branch, its log in the run's state directory ending `Ran 452 tests in 249.962s` and `OK`. The
merge landed at `b6ec289`, committed 20:45:43 EDT, and the push succeeded. Then `_run_closeout` at
`run.py:494` called `closeout.run`, which calls `closeout.render` at `closeout.py:234` before
writing anything to disk. `render` builds its values dict at `closeout.py:165` to `:189`, opens the
template at `closeout.py:190` to `:193`, and substitutes at `closeout.py:197`.

The template it opened was T-7's new one, on disk, naming `$learnings` at `brief-closeout.md:35`.
The `closeout` module doing the opening was the one imported at 19:08, an hour and thirty seven
minutes before the merge, whose values dict had no `learnings` key. `string.Template.substitute`
raised `KeyError('learnings')`, `closeout.py:198` to `:199` wrapped it as a `brief.BriefError`, and
the bare `except Exception` in the run loop at `run.py:172` to `:179` recorded it:

```
the runner hit an unexpected BriefError on T-7: closeout template names an unknown placeholder 'learnings'
```

Halt class `unexpected_error`, the least specific class in the closed set, because a `BriefError`
raised out of the Closeout has no class of its own. That it landed as a classified record at all,
rather than as a traceback leaving the record stuck at `running`, is prior work behaving correctly:
a P0 finding from the 2026-08-25 eight reviewer review named `BriefError` by name as one of the
exceptions escaping the run loop, and the fix for it is what produced this record (session history).

Every word of that message is true and the conclusion it invites is wrong. The template is correct.
`closeout.py:174` at head supplies `"learnings": _bullets(envelope.get("learnings") or [])`. The
pair at head agrees, and `python3 -m unittest test_closeout` in a fresh process is green at 49
tests, including `LearningsInBrief` at `tests/test_closeout.py:362` to `:390`.

## Guidance

**The frozen line is not only a limit on what a run can see. It is a fault line a change can be
split across, and a change split across it breaks at the first use after the merge.**

The rule for which side a file is on is mechanical, and there are exactly two read sites in this
package on the live side:

- `brief.py:91`, inside `_template_text` at `:84`, called from `brief.render` at `:137` on every
  Task brief.
- `closeout.py:192`, inline in `render`, called from `closeout.run` at `:234` on every Closeout
  brief.

Both resolve through `brief.TEMPLATE_DIR` at `brief.py:32`, which is
`os.path.dirname(os.path.abspath(__file__))` joined with `".."`, `".."`, `"templates"`. That is a
path into the same working tree the Runner is merging into, because the package is imported from the
repository itself. So the template read follows the checkout, and the checkout is what a merge moves.

Everything else is frozen at Runner launch: every module in `skills/relay/scripts/relay/`, and the
Manifest, loaded once by `cmd_run` and never re read. The Tracker is live in the same sense the
templates are, read through the adapter at `origin/<default_branch>` per Task.

**Recognise a split contract before writing it, not after.** The question to ask of a planned change,
before the first edit, is whether its file list contains at least one path under
`skills/relay/templates/` **and** at least one under `skills/relay/scripts/relay/`. If it does, and
the two halves fill each other, a self hosted run that lands it will break at the next render. T-7's
`changed_files` answers that in one glance, and so would the plan that preceded it.

**The direction of the break is asymmetric, and that asymmetry is the whole rule.** A template that
gains a placeholder before the module supplies it raises, because `string.Template.substitute`
refuses an unknown name. A module that supplies a key no template names is silent, because
`substitute` ignores extra values. So a split change breaks when the template half reaches
production first, which under a self hosted run is always, because the template half is live and the
module half is frozen.

**Four ways to land a split change safely, in the order to consider them.**

1. **Run against a throwaway target.** The Runner's package and the repository under it are the same
   tree only when Relay drives itself. Against any other repository, `TEMPLATE_DIR` points at the
   Relay install and no Task can move it. This is already what `CLAUDE.md` requires after a change to
   a contract between processes, and it names "a brief template" in that list explicitly.
2. **Order the Manifest so the module half is already resident.** If the module change lands in an
   earlier run and the template change in a later one, the Runner of the later run imported the
   module that supplies the key. Module first is the safe order and template first is not, which is
   the asymmetry above applied.
3. **Accept the halt and resume.** This is cheap, and it is what happened. The halt costs one
   Closeout. Everything before it, the gate, the merge, the push, the code scope verify, already
   succeeded and is durable in git.
4. **Do not split the change across two Tasks in one run and expect that to help.** Two Tasks in the
   same run share one Runner, so a module half landed by Task N is still not resident for Task N+1.
   This is the parent doc's "later Tasks in a run do not get the newer Runner" applied to a break
   rather than to an absence.

**The repair for this class of halt is a new process, not a patch.** The code at head was already
complete and correct at the moment of the halt. What the halt cost was the Closeout, whose two
duties are writing the outcome to the Tracker and then the Compound judgment. Neither ran, so T-7's
card was never closed, which is why the merge alone left the Task short of Landed. The operator wrote
the closing reference into `tasks.md` by hand at `419e159` and resumed. A fresh Runner imported the
merged `closeout.py`, ran Verify-landed, and promoted T-7 to landed. That run's terminal record reads
`"run_status": "completed"` with no halt class, written at 2026-08-28T00:56:20 UTC.

So the resume is not a workaround for a defect. It is the correct and complete repair, and the one
piece of hand work it needs is the Tracker write the missing Closeout owed.

## Why This Matters

**The halt message names the correct file.** That is the sharp edge, and it is worse than a message
saying nothing. A reader who follows it opens `brief-closeout.md`, finds `$learnings` on line 35,
and has to decide whether a placeholder that is spelled correctly, matches the key at
`closeout.py:174` exactly, and is covered by passing tests, is nevertheless the problem. The
available repairs from that reading are all destructive: delete the placeholder, rename it, or wrap
the substitute in `safe_substitute` so it stops raising. Each reverts or weakens a change that is
finished, tested, merged and pushed.

**Nothing in the run distinguishes it from a genuine defect.** The class is `unexpected_error`, which
carries no signal by construction. The Cause line is the raw sentence. The record shows a Task whose
gate passed, whose merge landed, whose push succeeded, and which then halted, a shape that
ordinarily means a real defect in the Closeout path.

**The gate cannot catch it, and this is structural rather than a gap to close.**
`gitwrite.local_merge_tail` checks out the task branch and runs `run_gate`, a `subprocess.Popen` of
the Manifest's gate argument list. A fresh operating system process running the suite on the task
branch imports the post merge `closeout.py` and reads the post merge `brief-closeout.md`. It sees
the consistent pair and passes, correctly. The gate's purpose is to judge the tree, and the tree is
fine. What is broken is a relationship between a resident process and that tree, which is not a
property of the tree and therefore invisible to any instrument examining the tree alone. The
repository's local pre-push hook has the same blind spot for the same reason: it was added before
the first self run specifically so a commit could not disable its own gate, and it guards against a
failing suite, not against a resident process holding stale code (session history).

**The rule that would have caught it existed, fired, and nothing consumed it.** `CLAUDE.md` requires
one live task against a throwaway target after a change to a contract between processes, naming a
brief template in that list. T-7 complied with the letter of it, closing with an explicit statement
that it could not run that live task from inside the run. The declaration went into the envelope and
no part of the Runner reads it (session history). A rule discharged by declaring a gap is a rule
nothing enforces, and that is the cheapest available place to make this class of halt impossible.

**It is the exact inverse of `cause-line-contract-split-degraded-to-placeholders.md`**, and reading
the two together makes the general rule legible. That doc is about the same kind of object, a
template declaring names and code elsewhere supplying them. There, `summary.cause_line` fills every
missing key with `"?"` and a bare except returns the raw template, so wrong cause lines survived for
weeks and nothing failed. Here, `substitute` refuses a name it cannot fill, so the mismatch stopped
the run within milliseconds. Same split contract, opposite failure posture. The silent one hides a
real defect. The loud one announces a defect that does not exist. Loud is the better default, and
its price is paid entirely by the reader of the message.

**Beyond Relay.** Any long lived driver that reads part of its configuration per use and holds the
rest as imported code has this fault line, and any change spanning it breaks at the first use after
deployment rather than at deployment. A worker holding compiled handlers while reading templates
from a shared volume. A scheduler holding its own code while re reading job definitions. The rule
generalises: **when a contract has two halves and the halves have different lifetimes, the half with
the shorter lifetime always arrives first, so the contract must be safe in that direction.**

## When to Apply

Apply this whenever a run halts and all three hold:

- The halt names a file under `skills/relay/templates/` as wrong, or names a placeholder, an unknown
  key, or a missing value at a rendering boundary.
- The same run landed a merge touching both that template and a module in
  `skills/relay/scripts/relay/`.
- The pair at head reads correct, and the tests for it are green.

It also applies **before** the halt, at planning time, whenever a change's file list spans
`skills/relay/templates/` and `skills/relay/scripts/relay/` and the halves fill each other. That is
the cheap place to catch it, and the only place to catch it before it costs a Closeout.

**This corrects the parent doc in two specific places.**
`self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md` presents live templates as a
convenience in its "Live, re read after a merge lands mid run" section, and its "When to Apply"
section states that the trap does not apply to a Task that changes a brief template. Both are correct
for a change touching **only** a template. Neither is correct for a change touching a template and
the module that fills its placeholders, which is the ordinary shape of a change to a brief carrying
new data. For that shape the live half is not a convenience, it is the half that arrives early and
breaks the run.

It does not apply to a template change with no module half. Adding prose to a brief, rewording an
instruction, or reordering sections lands live and takes effect for the next render exactly as the
parent doc says.

It does not apply outside self hosting. When Relay drives a different repository, `TEMPLATE_DIR`
resolves into the Relay install and no Task in the target can reach it. Keep this distinct from a
third staleness axis that also exists: Relay installed as a plugin is a copy under the plugin cache,
not a live link to the repository, so a run launched through the installed skill reads the cache
copy's templates while a run launched through `relay_cli.py` in the repository reads the
repository's (session history).

## Examples

**Where a future reader will and will not find the halt.** Look for `unexpected_error` in T-7's
record and it is not there. The third Runner's promotion overwrote the field, so the record now
reads `"status": "landed"` and `"halt_class": "landed"`. What a promotion does not clear is
`halt_message` and `halt_evidence`, which still carry the sentence verbatim and
`{"error_type": "BriefError", ...}` beside it. So the halt is recoverable, from the evidence rather
than from the class. This is the parent doc's lesson turning up one layer further out: the artifact
that recorded a stop is itself rewritten by the repair, and the class is the part that goes.

**The wrong read.** A session reconstructing the halt from those preserved fields reasons:

> T-7 halted with `unexpected_error`. The message says the closeout template names an unknown
> placeholder `learnings`. `string.Template.substitute` raises `KeyError` for exactly that, so
> `brief-closeout.md` names a placeholder nothing supplies. T-7's own merge added that placeholder.
> So T-7 shipped a broken template, and the fix is to remove `$learnings`, or to switch to
> `safe_substitute` so a missing key cannot stop a run again.

Every premise is true. The conclusion would revert a correct, tested, merged and pushed change, and
the `safe_substitute` variant would additionally convert the one loud failure in this seam into the
silent one that `cause-line-contract-split-degraded-to-placeholders.md` spent a milestone removing
from the neighbouring seam.

**The right read, and the trap inside the right read.** The comparison that decides it is the merge
time against the Runner's **launch** time, because launch is when the modules were bound. Wall clock
ordering against the halt is the wrong comparison: the halt is later than the merge, which is what
makes the wrong read convincing.

Getting the launch time is where the obvious move fails. The state directory and its `state.lock`
carry the birth time of the **first** Runner for that Manifest, and they survive every resume, so a
run repaired and resumed twice still shows the original timestamp:

```
$ stat -f '%SB %N' ~/.relay/4a7fee45.../state.lock
Aug 27 17:11:14 2026 .../state.lock          # the FIRST Runner, not the one that halted
```

The Runner that halted on T-7 was the second, and the durable anchor for its launch is the stdout log
of the first Task it ran:

```
$ stat -f '%SB %N' ~/.relay/4a7fee45.../logs/T-6.stdout.log
Aug 27 19:08:40 2026 .../logs/T-6.stdout.log

$ git log -1 --format='%h %ci %s' b6ec289
b6ec289 2026-08-27 20:45:43 -0400 Merge relay task T-7 from relay/T-7
```

An hour and thirty seven minutes separate them. The merge is after the launch, so the running
`closeout` module cannot contain it, while the template on disk does. Reaching for `state.lock`
instead happens to give the right verdict here and would give the wrong one in any run whose first
Runner started before a merge the resident Runner postdates.

```
$ cd tests && python3 -m unittest test_closeout
Ran 49 tests in 12.372s
OK
```

Green in a fresh process, against the exact pair the halt says disagree. A fresh process is the only
instrument that loads the merged module.

```
$ ls ~/.relay/4a7fee45.../briefs/
T-4.closeout.md  T-4.md  T-5.md  T-6.closeout.md  T-6.md  T-7.md
```

No `T-7.closeout.md`. `closeout.run` renders before it writes the brief to disk, so a render that
raised leaves no file. The missing brief is direct evidence that the stop was at substitution rather
than anywhere downstream, and it distinguishes this halt from a Closeout that launched and
misbehaved.

**A second instance of the same asymmetry, in the same Task, pointing the other way.** T-7's own Task
brief was rendered before T-7 merged its edits to `brief-local-merge.md`. So the process that added
the `learnings` ask to the task briefs was never itself asked for learnings, and the Envelope the
Runner recorded for it, in the run's own state directory under `digests/T-7.json` rather than
anywhere in the repository, carries no `learnings` key at all, only `status`, `blockers`,
`changed_files`, `plan_path`, and the parser's own `fenced` marker. That one is harmless, because a live template read before an edit is simply the old template.
It is worth noticing because it is the same clock producing a second surprise in the same Task, and
because it means a Task adding a field to its own brief can never populate that field in its own run.

**The general form.** Before concluding that a template named in a halt is wrong, answer one
question. Did a merge land after **this** Runner started, touching both that template and the module
that renders it? If so the template is not wrong, the pair is out of phase, and the repair is a fresh
Runner rather than an edit. Then answer the planning question that would have avoided it: does this
change's file list span `skills/relay/templates/` and `skills/relay/scripts/relay/`, and if it does,
which half arrives first.

## Related

- `docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`:
  the parent, and the doc this one corrects. It establishes the frozen and live inventory the whole
  mechanism rests on, and draws only the passive consequence. This doc is the active one. Its
  templates bullet and its "When to Apply" exemption for template changes are incomplete in the
  direction that matters, since a template change worth making usually carries a module change with
  it.
- `docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md`: the nearest
  neighbour, and the inverse. Same object, same root cause of one contract living in two places with
  nothing joining them, opposite posture. Its closing check, whether a declaration lists required
  names while the things satisfying it are scattered, is the check that finds this class of change at
  planning time. This doc adds a second axis: not only where the halves live, but how long each half
  lives.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`:
  the standing rule that a live run against a throwaway target is required after any change to a
  contract between processes. T-7's Manifest entry named that obligation in advance and said no Task
  inside the run could discharge it. This halt is a second reason for the same rule from a different
  direction: the throwaway target is not only where the contract gets exercised honestly, it is the
  one place a split change can land without the Runner's own package moving underneath it.
- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md`: the
  Task process lifetime trap, where ending the turn is exiting. With the parent doc and this one,
  Relay now has the Runner's lifetime documented twice, once for what it cannot see and once for what
  it cannot survive.
