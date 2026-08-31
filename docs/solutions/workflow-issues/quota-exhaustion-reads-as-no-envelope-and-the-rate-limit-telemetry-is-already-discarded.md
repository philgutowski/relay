---
title: An exhausted usage window is recorded as tasks that wrote no envelope, and the rate limit telemetry that would say otherwise is already on disk
date: 2026-08-28
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: high
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [classify, contracts, run-loop, tail, summary, launch]
applies_when:
  - "authoring a Relay manifest whose Task processes run on a subscription with a usage window rather than metered API billing"
  - "the manifest carries more than about two opus tasks, or is estimated at more than about three hours of wall clock"
  - "the run is launched detached and nobody is watching it turn by turn"
  - "tasks merge and push to main as they land, so an interrupted run leaves main already changed"
  - "diagnosing consecutive tasks that halted no_envelope with nothing on the branch and nothing on the card"
symptoms:
  - "several consecutive tasks record no_envelope, each stranding a branch, because the run continues and relaunches into the same exhausted window"
  - "the Cause line reads exited without a return envelope, so the operator debugs the Task rather than the account"
  - "a single unexpected_error naming unreadable evidence, when the CLI died before writing a transcript at all"
  - "grepping rate_limit, quota and 429 across the runner package returns one docstring line, naming rate_limit_event as a stream type the Follower skips"
  - "earlier tasks in the same manifest have already merged and pushed to main before the cascade starts"
tags:
  - usage-quota
  - weekly-window
  - halt-classification
  - pre-flight-check
  - unattended-run
  - rate-limit-event
  - manifest-sizing
  - discarded-telemetry
---

# An exhausted usage window is recorded as tasks that wrote no envelope, and the rate limit telemetry that would say otherwise is already on disk

## Context

On 2026-08-28 a two stage Relay manifest was authored for the pluggable backends plan, issue 16
on GitHub Project 4. Stage one was five tasks, four of them opus at high effort, estimated at
three and a half to four and a half hours of wall clock. Before launching, `/usage` read 89
percent of the weekly allowance consumed with the reset 31 hours away. The run was held. The
models on the two units with no named traps were dropped from opus to sonnet, which took the
estimate from roughly $80 to $135 down to roughly $45 to $95.

Holding the run was a judgement call made by a human reading a number the Runner never sees. If
that check had been skipped, the run would have launched and the weekly window would have closed
somewhere in the middle of it. The question this doc answers is what Relay would have reported
when that happened, and the answer is that it would have blamed the tasks.

**This is the second time in two days that the weekly window decided what work happened, and both
times the decision was made entirely outside the Runner (session history).** Earlier the same day,
a session probing the CLIs for the backends spike read `"status":"allowed_warning"` with
`surpassedThreshold` true at 87 percent of the seven day window, from an ad hoc `claude -p` call
made for another purpose. The response was to run the Codex and Grok legs of the spike first and
defer the Claude leg, then to reuse two historical transcripts so that four of six Claude fixture
shapes were captured with no spend at all. That reordering worked. It was also invisible to every
artifact Relay writes, so the next operator to author a manifest starts from nothing.

The halt classes are a closed set. `skills/relay/scripts/relay/contracts.py:287` defines
`HALT_CLASSES` as sixteen entries: `landed`, `blocked_envelope`, `no_envelope`, `denied_tool`,
`path_gate`, `tracker_write_denied`, `remote_advanced`, `closeout_out_of_scope`,
`runner_crashed`, `skill_substitution`, `gate_refused`, `partial_landing`, `timeout`,
`unclean_exit`, `ci_undecided`, `unexpected_error` (`contracts.py:262` to `contracts.py:304`).
Three of those always stop the whole run, `RUN_SCOPED_HALT_CLASSES` at `contracts.py:312`:
`remote_advanced`, `runner_crashed`, `unexpected_error`. Five plus the two closeout findings are
findings attached to a record rather than a record's own class, `FINDING_CLASSES` at
`contracts.py:319`. None of the sixteen means "the account's model quota ran out."

A grep for `rate_limit`, `quota`, `429` and `budget` across `classify.py`, `contracts.py` and
`run.py` returns nothing, and the same grep across the whole of `skills/relay/` returns exactly
two hits, neither of them a control: `launch.py:233`, a comment about wall clock during laptop
sleep, and `tail.py:88`, a docstring naming `rate_limit_event` as one of the stream line types
the follower deliberately skips. `skills/relay/SKILL.md` contains no occurrence of cost, budget,
usage, quota or limit at all.

## Guidance

**Size the run against the usage window before launch, and record the sizing next to the go
decision.** The evidence already exists on disk. Every prior run's `logs/*.stdout.log` under
`~/.relay/<state-dir>/` carries per turn `usage` on each `assistant` message and a terminal
`result` event with `total_cost_usd` and `duration_ms`, because the Runner launches with
`--output-format stream-json` (`contracts.py:111`, read at `launch.py:117`). A per task cost
table can be built from those logs with about twenty lines of Python and no guessing.

Here is that table, measured from the logs on this machine on 2026-08-28. Cost, wall clock and
output tokens come from each log's terminal `result` event. Turns are assistant messages
carrying a `usage` block. Cache read and cache write are the sum of those per turn blocks, which
is why they are far larger than the single figure the `result` event reports.

| Task | Model | Turns | Output | Cache read | Cache write | Cost | Wall |
|---|---|---|---|---|---|---|---|
| largest seen, issue 62 | opus | 540 | 68K | 94.7M | 2.5M | $37.67 | 34 min |
| T-6 | opus | 267 | 95K | 60.9M | 826K | $19.72 | 32 min |
| T-7 envelope grammar | sonnet | 671 | 148K | 129.5M | 3.9M | $18.94 | 50 min |
| issue 12 run visibility | opus | 171 | 97K | 30.8M | 626K | $13.20 | 33 min |
| T-4 | sonnet | 274 | 64K | 41.8M | 1.6M | $6.91 | 18 min |
| issue 14 docs only | sonnet | 84 | 17K | 6.5M | 386K | $1.47 | 6 min |
| any Closeout | sonnet | 5 to 49 | 0.7 to 8K | 0.2 to 3.6M | 25 to 155K | $0.15 to $0.77 | 0 to 2 min |

Three things to read off it.

**Cache reads are the number that matters.** The input token column is near zero in every run,
128 to 318 tokens for a whole task, because almost everything a long agent session sends is a
cache read. Estimating from input tokens will under count by three orders of magnitude.

**A Closeout is free by comparison.** Every Closeout in every state directory ran sonnet, cost
under a dollar, and finished in under two minutes. Budget the Task processes and treat the
Closeouts as rounding.

**Add the gate.** The gate command for a self hosted run is `python3 -m unittest discover -s
tests`, and it runs twice per task, once as the Runner's gate and once in the local pre push
hook. The suite discovers 557 tests at the commit at the time of writing. Recent gate logs give
the rate directly: 508 tests in 200.5 seconds and 452 tests in 178.0 seconds, so 557 tests is
about 220 seconds, and two runs is about seven to eight minutes of gate per task. `CLAUDE.md`
still says the suite takes about two and a half minutes, which was true at roughly 380 tests and
is now low.

**Then use the second source, which is already streaming past.** Every `rate_limit_event` line
in the stdout log carries the live window state. A real one, from the issue 62 run's log at
`~/.relay/<state-dir>/logs/62.stdout.log` (this path is outside the repository; a state directory
is named by a hash of the manifest path, so the name differs on every machine and is not a
commit):

```json
{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1787850600,
 "rateLimitType": "five_hour", "unifiedWindows": {"five_hour": {"utilization": 0.12,
 "resetsAt": 1787850600}, "seven_day": {"utilization": 0.57, "resetsAt": 1788080400}}}}
```

That is a live utilization fraction and a reset timestamp for both the five hour and the seven
day window, arriving unprompted, already inside a file Relay writes. That one log carries 28 of
these events and shows the weekly window going 0.57 to 0.65 while the five hour window went 0.12
to 0.58 across a single $37.67 opus task. Thirty two files on this machine carry these events
already, 25 prior run logs under `~/.relay/*/logs/` and 7 under `tests/`.

`tail.decode` drops every one of them, because a line with no `message.content` list returns an
empty event list (`tail.py:100` to `tail.py:105`), which is correct for a display follower and is
the reason nothing downstream ever sees the field. **The discard is not incidental, it is pinned
by a passing test.** `tests/test_tail.py:100` to `:103`,
`test_the_line_types_that_carry_no_message_yield_nothing`, iterates
`("tool_progress", "rate_limit_event", "result", "system")` and asserts each decodes to no
events. So the one place in the codebase that names `rate_limit_event` outside a docstring is a
test asserting it is worthless. That is worth stating plainly, because it means the field was
seen, understood as contentless for display purposes, and locked in as such, rather than
overlooked. The Follower is the right place for that decision and the wrong place to conclude the
data has no other use.

So the concrete practice, in order:

1. Before launch, build the cost table from past `logs/*.stdout.log` and multiply by the tasks in
   the manifest, at the model each task actually names. Manifests carry `model` and `effort` per
   task (`manifest.py:266`), so the estimate is per task, not an average.
2. Read `/usage` for the weekly utilization and the time to reset, and compare the two.
3. If the run does not fit, cut model before cutting tasks. Dropping opus to sonnet on units with
   no named trap is the cheapest lever, and it was the one taken on 2026-08-28.
4. Put the estimate and the window reading into the "Confirm before launch" block in
   `skills/relay/SKILL.md:100` to `:106`, which today shows the manifest path, the task list with
   model and effort, and the gate command, and says nothing about budget.

**Do not reach for a mixed backend manifest as the hedge.** It spreads billing across three
vendors, which is real, but it does not fail over. A review of the pluggable backends plan
already established that R2 has the run loop treat a mixed manifest exactly as a single backend
one, with runtime routing out of scope, so a halt on the running Task still stalls the whole
queue including tasks assigned to a healthy backend (session history). Spreading the spend is a
budget move, not an availability move, and conflating the two is how an operator discovers mid
run that the hedge was not one.

## Why This Matters

Classification is purely evidence driven. `classify.classify` (`classify.py:173`) reads one
thing, the session transcript, plus two attributes off the launch result, `timed_out` and
`exit_code` (`classify.py:176` to `classify.py:177`). Its precedence block at `classify.py:268`
to `classify.py:291` decides in this order: `timed_out` gives `timeout`; a transcript that will
not open gives `unexpected_error` with findings marked unavailable; a complete envelope leaves
the class to verify; an envelope that is not complete gives `blocked_envelope`; and no envelope
at all gives `no_envelope` at `classify.py:286`.

A process killed by an exhausted quota did not time out, so the first branch does not fire. It
leaves behind a partial transcript and no return envelope. It therefore lands in exactly one of
two places, and which one depends only on whether the CLI got far enough to create the transcript
file at all:

- **Transcript written, no envelope.** Class `no_envelope` at `classify.py:286`, cause line
  "exited without a return envelope; last message: {last_message}" (`contracts.py:336`). The
  Runner then checks whether git and the tracker show a finished pipeline anyway
  (`run.py:384`); for a task killed partway they will not, so it takes `_blocked_route`
  (`run.py:396`), strands the branch, records `STATUS_BLOCKED`, and **continues to the next
  task** (`run.py:535` to `run.py:568`). The next task launches into the same exhausted window
  and dies the same way. So does its Closeout, which `_blocked_route` launches at `run.py:540`.
  A five task manifest produces five identical blocked records that each read as though the Task
  process wrote nothing.
- **Transcript never created.** Class `unexpected_error` at `classify.py:278`, which is in
  `RUN_SCOPED_HALT_CLASSES` (`contracts.py:315`), so the whole run stops with a cause line
  reading "the runner hit an unexpected {error_type} on {task}" (`contracts.py:353`). That
  blames the Runner for the account's billing state.

Both outcomes are wrong about what happened, and they disagree with each other about severity and
about who is at fault, for one underlying cause. That is expensive in exactly the way the
existing corpus keeps finding self hosted runs to be expensive. The doc on a run not being able
to observe the code its own tasks land, and the one on a change spanning a live template and a
frozen module, both describe a halt whose stated cause was true and whose real cause was one
level down. This is the same shape: the record is accurate about the evidence and wrong about the
world.

The deeper reason a classifier cannot fix this is that quota exhaustion and a crash leave the
same absence. Both produce a truncated transcript with no envelope. There is no positive
artifact distinguishing them after the fact, so the two are indistinguishable to the classifier
by construction, not by oversight. `exit_code` is captured in the digest (`classify.py:181`) but
nothing branches on it, and even if it did, an exit code is a weak and version dependent signal.

The asymmetry is the whole point. **The information exists while the process is alive and is gone
once it is dead.** Every `rate_limit_event` arrives mid run, on a stream Relay is already
capturing to a file it owns; the dead process leaves nothing that says why it died. So a control
placed in the classifier is working with the one view of the run that cannot answer the question,
while a control placed before or during the run is working with a view that can.

That is the argument for where the control belongs. **The remedy is a pre launch sizing check
plus a hint on the existing Cause line, not a new halt class.** The closed set is deliberate,
stated twice: KTD6 in `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md:275` says "Every
exit is classified into a closed halt class set from the transcript, by joining `tool_result`
lines to their `tool_use` by id", and R44 at line 150 says the runner "classifies the exit into
exactly one halt class from a closed set (KTD6)". `CLAUDE.md:49` restates it as a working rule:
"Halt classes are a closed set in `contracts.py` (KTD6). A new outcome is a finding attached to a
record, not a new class, unless the plan is amended." Adding a seventeenth class would buy an
after the fact label for a condition the operator can see before launch and cannot reliably
detect afterwards. That is the wrong trade.

**The repository's own precedent says the same thing twice, and both are written into the tree.**
When issue 15 found that `gate_refused` conflated a gate failure with a post merge push failure,
two outcomes with opposite continuation semantics, the fix was a repository state probe rather
than a new or split class. The reasoning survives as a comment at `contracts.py:306` to `:311`:
the same class "can leave the repo usable (a gate command that failed on the task branch) or not
(a push that failed after the merge, leaving the default ahead of origin), and only the repo can
tell them apart", so the disposition is decided by `gitwrite.resume_disposition` and not from the
class name. When the envelope learnings work faced the same pressure, its plan wrote the
constraint down as a requirement: `docs/plans/2026-08-27-1959-feat-relay-envelope-learnings-plan.md:59`
states under "What must not change" that "No new halt class is added, `HALT_NO_ENVELOPE` is
unchanged, and how `status` is read is unchanged." U7 then solved this doc's exact shape, evidence
that reframes what an unreadable exit means, with a `findings = None` state distinguishing "could
not look" from "looked and found none", and no new class. The set has been extended exactly once,
at plan time, before any code existed, which is what the "unless the plan is amended" clause is
for. A new class here would break a pattern the project has held twice under pressure.

The hint is cheap and stays inside the rule. `summary.cause_line` fills a class template from
whatever evidence a record carries (`summary.py:51` to `summary.py:59`), and `_blocked_route`
already assembles that evidence dict per class (`run.py:548` to `run.py:565`). Capturing the last
`rate_limit_event` seen on the stdout stream into the digest, then adding it to that evidence,
lets the `no_envelope` cause line say the seven day window read 0.99 at the moment the process
died. It does not create a class, it does not change precedence, and it turns an unfalsifiable
record into one an operator can act on. If a stronger control is wanted later, the same field
supports a mid run stop, checking utilization against a manifest threshold between tasks rather
than launching the next one into a wall. That mid run check is the one that would have converted
the five record cascade above into a single honest stop.

## When to Apply

- Authoring any Relay manifest whose Task processes will run on a subscription with a usage
  window rather than metered API billing. This is the normal case for a self hosted run.
- Any manifest with more than about two opus tasks, or any manifest estimated at more than about
  three hours of wall clock, since those are the shapes where the weekly window becomes the
  binding constraint rather than the clock.
- Whenever `/usage` reads above roughly 80 percent of the weekly window with the reset more than
  a few hours out. Above that line, size the run explicitly rather than launching on the strength
  of the manifest validating.
- Diagnosing a run where several consecutive tasks halted `no_envelope` with nothing in the
  branch and nothing on the card. Check the account's usage window before reading any transcript,
  because a cascade of identical `no_envelope` blocks is the signature this produces.
- Diagnosing a single `unexpected_error` whose cause line names unreadable evidence. Same check
  first, for the same reason.
- Diagnosing an `unclean_exit` with a dirty tree, but only after ruling out the backgrounded
  command case that owns that signature. If the transcript shows no killed background task, the
  usage window is the next thing to check.
- Not applicable to metered API key runs, where exhaustion presents as a billing failure with its
  own error rather than as a silent kill.

## Examples

**Before, the launch confirmation as `SKILL.md:100` to `:106` specifies it today.** After
validate passes, the skill shows the manifest path, the task list with model and effort, and the
gate command, then asks for a go. Nothing in that block, or anywhere else in the skill, mentions
what the run will consume. An operator confirming it is confirming that the work is right, not
that it can be paid for.

**After, the same confirmation with a sizing line, using the measured table above.** Stage one of
the issue 16 manifest, five tasks: two opus at roughly $20 each, two sonnet at roughly $7 each,
one docs only sonnet at roughly $1.50, plus five Closeouts at under $1 each and five gate pairs
at about eight minutes each. That comes to roughly $60 in model spend and three and a half to
four and a half hours of wall clock. Weekly window reads 89 percent used, resets in 31 hours.
The run does not fit, so it is held. Dropping the two units with no named trap from opus to
sonnet takes the range to roughly $45 to $95, and that is the version that goes to the operator.

**The failure mode this avoids, traced through the code.** Suppose the unchanged manifest had
launched. Task one runs 32 minutes on opus and lands. Task two starts, and 20 minutes in the
weekly window closes and the process is killed. `launch_result.timed_out` is False, so
`classify.py:268` does not fire. The transcript exists and holds 200 assistant lines, so
`classify.py:272` does not fire. There is no envelope in the last assistant text, so the else at
`classify.py:285` assigns `no_envelope` and appends a `no_envelope` finding. `run.py:384` asks
whether git and the tracker show a finished pipeline; the branch has three commits but the card
never reached `in_review_status`, so it is not routable. `run.py:396` calls `_blocked_route`,
which strands the branch, launches a Closeout that is killed the same way, and records
`STATUS_BLOCKED` with cause "exited without a return envelope; last message: ...". The run then
continues. Tasks three, four and five each launch, die in seconds, and record the same thing.
The operator opens the summary to five tasks blamed for not writing an envelope and one true
cause that appears nowhere.

**The evidence that was on the wire the whole time.** In `62.stdout.log`, 28 `rate_limit_event`
lines track the seven day window from 0.57 to 0.65 and the five hour window from 0.12 to 0.58
across one task. Across the other logs the per task weekly delta is 0.00 to 0.02 for sonnet work
and up to 0.08 for a large opus task, so the weekly window is readable as a per task cost in its
own units, without converting to dollars at all. Every one of those lines was written into a file
Relay owns, and every one was discarded by `tail.decode`.

## Related

- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md` is
  the same halt shape from an internal cause. A reader who finds `unclean_exit` with a dirty tree
  should rule out a backgrounded command per that doc and, if the transcript shows no killed
  task, check the usage window before concluding the Task misbehaved.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the standing rule this finding bounds. Its Prevention section names a rate limit as an
  unproven bound and prescribes a live run against a throwaway target to pay the cost the stub
  was never asked to pay. This doc adds that the pool the live run draws on is finite and shared
  with every other run, so the instrument itself has to be sized before it is prescribed. That is
  a fourth family member beside "a stubbed seam agrees by construction" and "a stubbed subprocess
  is free by construction": the instrument that pays the cost has a finite balance. Issue 17 is
  already open against that same section for the third member, so the two amendments belong in
  one rewrite.
- `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`
  is the nearest precedent for a Cause line that names the wrong thing. There the halt blamed a
  correct template because two halves of a contract had different lifetimes; here it blames a
  correct Task because quota death and a crash leave identical evidence.
- `docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md`
  is the parent case for an artifact that is true and misleading at once. There the Runner's own
  launch time is the fact the record does not carry; here it is the account's remaining quota,
  and unlike that case no later read of the run's own artifacts can supply it.
- `docs/solutions/logic-errors/cause-line-contract-split-degraded-to-placeholders.md` owns the
  Cause line seam this finding proposes to extend. There the line was wrong because the template
  contract lived in two places; here every template and substitution is correct and the line is
  still misleading, because the closed class set has no member for the real cause.
- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md` covers the other
  route to a process ending without an envelope. A timeout is a bound the Runner set and can
  therefore observe, which is why it gets its own class and precedence; a quota kill is a bound
  the Runner has no model of, which is why it falls through.
- Issue 20 on `philgutowski/relay`, Backends U3 backend readiness preflight, is the natural home
  for a pre launch budget check, since it is already the unit that refuses a run before launching
  anything. Issue 29, Backends U14 live proof runs, is the run most likely to hit this, being
  multi task and long duration across three backends.
