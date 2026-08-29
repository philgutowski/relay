---
title: Headless dontAsk mode blocks edits under .claude/ regardless of allowlist
date: 2026-08-25
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: high
root_cause: missing_workflow_step
resolution_type: workflow_improvement
last_updated: 2026-08-28
related_components: [manifest, task-process, permission-mode]
applies_when:
  - "launching claude -p with --permission-mode dontAsk (Claude backend only, see the Scope note)"
  - "a task's plan touches a path under .claude/ (skill, hook, or settings file)"
  - "the allowlist explicitly names Edit and Write"
  - "the run has no human present to approve a permission prompt"
symptoms:
  - "Edit denied with: Permission to use Edit has been denied because Claude Code is running in don't ask mode"
  - "every other Edit and Write in the same run succeeds; only the .claude/ path is refused"
  - "the run halts asking for an approval nobody can give, then exits clean and zero without merging"
  - "the tracker card carries no blocker comment, so the board looks untouched"
tags: [dontask-permission-mode, claude-directory-gate, headless-claude, manifest, pre-flight-check, unattended-run]
---

# Headless dontAsk mode blocks edits under .claude/ regardless of allowlist

## Context

Relay runs one fresh headless `claude -p` process per task, serially, with no human present. The
whole design rests on a task process being able to finish its work alone. `dontAsk` is the chosen
permission mode (R10, R11), because a tool outside the allowlist is denied outright rather than
raising a prompt nobody is there to answer.

**Scope, added 2026-08-28. Everything below is a Claude backend fact.** When this was written Claude
was Relay's only backend, so it reads as universal and is not. The pluggable backends work recorded
a per backend permission posture in `contracts.BACKEND_PINS`: Claude keeps `dontAsk`, Grok uses
`auto` and carries `dontAsk` in its forbidden tuple because there it cancels every tool call rather
than approving one, and Codex uses `--sandbox workspace-write` and has no permission mode concept at
all. See `grok-accepts-dontask-then-cancels-every-tool-call.md`, which is the descendant of this doc
and carries that evidence; the two read as a pair. Note the current state precisely: those pins are
recorded but nothing reads them yet, and `launch.build_args` still passes the single global
`contracts.PERMISSION_MODE` for every task, so today every Relay run really is a `dontAsk` run and
this doc is accurate as written. It becomes Claude-only the moment the launch seam starts resolving
the posture per backend.

On 2026-08-25 the design was proved by hand against a Jira-tracked repo. A gitignored shell runner
launched a single process for card IW-83:

```
claude -p --model opus --effort high --permission-mode dontAsk \
  --allowedTools "Bash,Read,Edit,Write,Grep,Glob,Skill,Agent,Task,TodoWrite,mcp__atlassian__*" \
  --disallowedTools "Bash(git push --force*),Bash(git push -f*),Bash(git reset --hard*),Bash(rm -rf *),Bash(git clean -fdx*)"
```

The run made 261 tool calls over exactly 60 minutes. It planned through `ce-plan`, reviewed the plan
through `ce-doc-review`, built eight commits on a feature branch, ran the project gate four times
(once as a baseline before planning, three more during and after the build), and did two rounds of
code review. It attempted 80 Edit calls and 1 Write. Eighty landed on ordinary source, test, and
documentation paths without incident. One did not.

The single Edit targeting `.claude/skills/itg-brief/SKILL.md`, a skill file inside that experiment's
target repo (support-workbench, not this one, so every `.claude/` path cited below is external to
Relay), came back with:

> Permission to use Edit has been denied because Claude Code is running in don't ask mode. IMPORTANT:
> You *may* attempt to accomplish this action using other tools that might naturally be used to
> accomplish this goal, e.g. using head instead of cat. But you *should not* attempt to work around
> this denial in malicious ways [...] If you believe this capability is essential to complete the
> user's request, STOP and explain to the user what you were trying to do and why you need this
> permission. Let the user decide how to proceed.

`Edit` was on the allowlist. The mode was `dontAsk`, not `plan` or `manual`. The observable behavior
is that the harness gates paths under `.claude/` separately from the tool allowlist, because an Edit
on an ordinary path and an Edit on a `.claude/` path in the same process, with the same flags, got
different answers. This is not documented in the local CLI help, which lists only the mode choices
(`acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`), nor anywhere in the
installed plugin tree. Treat the boundary as observed behavior on CLI 2.1.245, not as a documented
contract, and re-test it when the CLI version moves.

The run did what the denial text told it to do. It wrote:

> This session is in don't-ask mode and the edit was refused, so I have stopped rather than working
> around it.

It finished the remaining unit, ran the gate, and asked for approval. It asked twice, and its final
message offered a way out:

> The Edit was denied under don't-ask mode. Approve it and I will apply, re-run the gate, and merge.
> Alternatively, say "merge without it" and I will merge the nine commits as they stand and open a
> follow-up IW card for the skill file, so the drift is tracked rather than silent.

That second option was the right call, and the run could not take it, because taking it required an
answer and nobody was there to give one. It exited without merging and without pushing. A human
later applied the two paragraph edit attended, merged, pushed, and closed the card.

What did work under the identical posture, verified in the same transcript:
`mcp__atlassian__transitionJiraIssue` went through with no prompt and no error under the
`mcp__atlassian__*` allowlist entry. The MCP allowlist is sound. The path gate is the exception.

## Guidance

**A task whose plan touches any path under `.claude/` cannot run unattended under `dontAsk`. Detect
that before launch, not at minute fifty of the run.**

This is a constraint on the task, not on the project. The README's three qualifying properties can
all hold while a single task in the list is still unrunnable. Relay already has the field for this:
R5 lets a manifest mark a task as excluded from unattended runs with a reason. This learning
sharpens R5 by supplying a mechanical detector for one specific class of stop-and-ask, and it gives
the `/relay` skill (R4) something concrete to check at manifest-authoring time rather than a
judgment call the operator has to remember to make. Because the runner generates each task brief
from the manifest rather than taking a hand-written one (R7), brief generation is the natural place
to plug the scan in.

### Pre-flight: scan the plan, route the task

Run this before the task process launches, against the committed plan for the task and against the
generated brief.

```bash
#!/usr/bin/env bash
# preflight_claude_dir.sh <task-id> <plan-or-brief-path>
# Exit 0: safe for an unattended dontAsk run.
# Exit 3: the task names a path under .claude/. Route to attended, do not launch.
set -euo pipefail

task_id="$1"; doc="$2"

# Deliberately broad: a false positive costs one attended run, a false negative
# costs a full run that halts unmergeable at the end. Also catches markdown
# wrappers a tracker card commonly uses: a link's leading `[` and a bold or
# italic or unspaced-list-marker `*` (a spaced list marker, "- .claude/x",
# already matched via the whitespace class member).
hits="$(grep -nE '(^|[[:space:]"'"'"'`(/[*])\.claude/' "$doc" || true)"

if [ -n "$hits" ]; then
  cat >&2 <<EOF
BLOCKED $task_id: plan or brief names a path under .claude/
Edit and Write under .claude/ are denied in --permission-mode dontAsk
regardless of --allowedTools. Observed 2026-08-25, CLI 2.1.245.
Matches:
$hits
Route: mark this task excluded_from_unattended in the manifest (R5) with this
reason, or split the .claude/ edit out into its own attended follow-up task.
EOF
  exit 3
fi

echo "OK $task_id: no .claude/ paths in plan or brief"
```

**Widened 2026-08-27** to also catch a path wrapped in markdown syntax, which tracker cards
write often enough that the original six-character class (start of line, whitespace, `"`,
`'`, `` ` ``, `(`, `/`) missed it: a markdown link's display text (`[.claude/skills/x/SKILL.md](...)`,
leading `[`), bold (`**.claude/settings.json**`), italic (`*.claude/settings.json*`), and an
unspaced list marker (`*.claude/hooks/pre.sh`, indistinguishable from italic to the regex). A
spaced list marker (`- .claude/x`) already matched via the whitespace class member. The added
characters are `[` and `*`; the prefix appearing as the suffix of a longer word (`x.claude/y`,
a bare word character glued to the leading dot) still does not match, because no word
character was added to the class. `contracts.CLAUDE_DIR_SCAN_REGEX` in
`skills/relay/scripts/relay/contracts.py` is the production form; `tests/test_contracts.py`'s
`ClaudeDirScanRegex` class pins both the newly-caught forms and the suffix non-match.

The same check belongs on the way out as a backstop, because a plan can be right and an
implementation can still wander:

```bash
# post-run backstop, against the task branch's diff versus the baseline commit (R17)
if git -C "$repo" diff --name-only "$baseline"..HEAD | grep -qE '(^|/)\.claude/'; then
  echo "PARTIAL $task_id: branch touches .claude/, expect a denied edit in the transcript" >&2
fi
```

Feed that into R37, which already asks the runner to detect a denied tool call in the transcript
stream. A denial on a `.claude/` path becomes a named, expected shape rather than an unexplained log
line, so the summary can say what to do about it instead of only reporting it.

### Two secondary rules from the same run

**A blocked exit must write to the tracker, and the runner must enforce that, not merely ask for it
in the brief.** The IW-83 brief told the run to comment the blocker on the card if blocked. The
transcript shows zero `mcp__atlassian__addCommentToJiraIssue` calls. The run addressed its blocker to
the absent human in its final message instead, which reaches nobody, so the blocked exit was
invisible on the board. That is exactly AE2's shape, and the brief alone did not produce it.

This paragraph originally resolved the tension with R19 by asking the runner to write that comment
itself, as a narrow carved exception, on the argument that commenting is not moving. **That is not
what was built, and the reversal is deliberate. Decided 2026-08-26: R19 stays absolute and the
runner never writes to a tracker.** Three things changed the answer between writing this and
building it:

- The adapter interface settled at eight methods and none of them writes, with a shared test
  asserting exactly that surface (KTD16). Carving the exception means a ninth write method on every
  adapter, tracker write credentials reachable from runner code, and that assertion relaxed. The
  property R19 buys, that a defect in the runner can never move a card, is bought by the absence of
  the code path, not by the runner's intent to use it carefully.
- The Closeout process arrived, and it is a Claude process. Writing the blocker comment is its first
  duty, so the write already happens on the blocked path; it simply does not happen inside the
  runner.
- R36's summary and its pending checks arrived. A blocked task whose closeout wrote nothing raises
  the `blocked_unrecorded` finding, which becomes a named line in the summary telling the operator
  to check that card by hand. The silent case this paragraph was written against is now a checklist
  line rather than nothing at all.

So the mechanism is detection, not writing. `closeout.confirm_blocked_comment` reads
`comments_since` against the R17 baseline after the closeout has run, and a card with no newer
comment produces the finding. What this does not give you is the comment itself: if the closeout
process fails to write, the board still shows an untouched card and the operator learns it from the
summary rather than from the tracker. That is the accepted cost of R19.

**Pre-authorize the degraded path in the brief, so a run that finds a blocker can take it alone.**
The IW-83 run worked out the right fallback by itself: merge the finished commits, leave the blocked
edit out, and open a follow-up card so the drift is tracked rather than silent. It could not act on
that, because it phrased it as a question and questions need answers. A brief written for an
unattended run should decide these in advance rather than leaving the process to ask: whether
partial work may merge without the blocked piece, whether the run may open a follow-up task itself,
and what it must record before exiting. A run that has been told what to do when blocked can land
eight of nine commits instead of none.

**Pin fully qualified skill names in the brief.** The brief said `/ce-code-review`. The transcript
shows the run invoked the harness-native `code-review` skill twice and never
`compound-engineering:ce-code-review`. Those are different skills with different behavior, so a run
that reports a review round is not necessarily reporting the review the manifest intended, and a
headless brief has no human to catch the substitution mid-run. Write the qualified name every time,
`compound-engineering:ce-code-review`, `compound-engineering:ce-work`,
`compound-engineering:ce-compound`, and have the runner check the transcript for the qualified name
it asked for rather than a substring match on the short one.

## Why This Matters

The IW-83 run spent roughly an hour of a high-effort Opus process producing eight good commits, three
green gate runs, and two review rounds, and it landed none of it. The blocker was a two paragraph
edit to one file. Everything upstream of that edit was correct work, and all of it sat on a feature
branch waiting for an approval that could not arrive, because the design of the run guarantees nobody
is watching.

That failure is worse than a crash. A crash halts early and cheaply. This halted at the end, after
full cost, in a state that reads like success in every signal except the one that matters. The run
exited cleanly. It exited zero. It printed a coherent account of what it had done. Only git and the
tracker told the truth, which is precisely why Relay puts verify-landed on git and the tracker and
never on the run's report. This case is the concrete argument for that decision, and it adds a
wrinkle: verify-landed catches the failure, but only after the hour is spent. A pre-flight scan
catches the same failure in milliseconds, before launch.

There is also a quieter cost. The card stayed where it was and zero comments were written. An
operator returning to the board would see a task that looks untouched next to a repo holding an
unmerged branch of finished work. The tracker and the working tree disagreed, and nothing announced
the disagreement. Relay's premise is that a task is either landed or halted with a stated reason,
never silently half done. A blocked run that tells only its own transcript is the silent half-done
case.

The wider point generalizes past this one gate. An allowlist is a claim about tools, not about paths.
The harness applies at least one gate the allowlist does not describe, and the flags gave no advance
warning that a `.claude/` write would behave differently from any other write. Any unattended runner
should assume there are more such gates it has not met yet, and should prefer detecting the shape of
a task it cannot finish over discovering it at the end.

## When to Apply

Apply the pre-flight scan on every task, always, before the task process launches. It costs one
`grep` and it is the cheapest check in the pipeline. `run.py` does exactly this, unconditionally,
with no backend awareness, which is correct while every task is a Claude task.

Once tasks can run on another backend, that unconditional call needs a decision this doc cannot make
for you, because the gate's existence off Claude is unestablished. The failure modes are asymmetric
and worth naming: scanning a backend that has no such gate costs a false exclusion, a task routed to
attended that could have run alone, while skipping the scan on a backend that does have one costs
the full hour this doc was written about. Prefer the false exclusion until someone runs the probe.
Note also that the exclusion reason the runner writes names `dontAsk` explicitly, so a non Claude
task excluded by this scan would carry a reason that does not describe its own backend.

Apply the routing decision that follows from it when any of these hold:

- The task's plan, brief, or acceptance criteria names a path under `.claude/`. Skill files, agent
  definitions, hooks, `settings.json`, and `settings.local.json` all live there.
- The task's real scope is a contract change that will surface in a skill file even though the plan
  only names source paths. This is the case the plan scan misses and the post-run diff backstop
  catches. If a task's design touches an agent-facing contract, treat the skill file as in scope by
  default and route accordingly.
- The project is one where documentation of a change belongs in a skill file rather than a README.
  Where product skills carry contract text an agent reads at runtime, a contract change and a skill
  edit arrive together more often than not.

Do not apply this reasoning to MCP writes. Verified in the same run under the same posture,
`mcp__atlassian__transitionJiraIssue` succeeded with no prompt and no error. Tracker writes are not
affected by the path gate and need no workaround.

Re-test the boundary on two axes, not one. This instruction originally named only the first.

**When the Claude Code CLI version moves.** The behavior is observed on CLI 2.1.245 and is documented
nowhere, so it carries no stability guarantee in either direction. A future version could widen the
gate or remove it, and either change should show up as a change in this pre-flight's hit rate rather
than as a surprise at minute fifty.

**This trigger has fired and the re-test is outstanding, as of 2026-08-28.**
`contracts.CLI_VERSION_TESTED` now reads `2.1.250`, bumped against the installed binary during the
backends spike. Nothing in that spike exercised the `.claude/` path gate, so whether it still behaves
as described at 2.1.250 is unverified. Treat the gate as observed on 2.1.245 and unconfirmed since.

**When the backend changes.** A permission mode's spelling on another vendor's CLI is not a promise
about its behavior, which the descendant doc establishes for `dontAsk` on Grok. Whether a `.claude/`
path gate exists at all on Codex or Grok is unestablished: the backends spike tested permission
postures and denials, not this gate. Do not assume it carries, and do not assume it does not. Until
someone runs the probe, a `.claude/` hit on a non Claude backend is an open question rather than a
known refusal.

## Examples

### IW-83, what happened

Sequence, from the transcript:

1. Launch under `dontAsk` with `Edit` and `Write` on the allowlist.
2. Plan through `ce-plan`, review through `ce-doc-review`, transition the card via
   `mcp__atlassian__transitionJiraIssue`, which succeeds silently and correctly.
3. Build. Eighty successful Edit and Write calls on ordinary paths, eight commits on a feature
   branch, four gate runs, two review rounds invoked as `code-review` rather than the intended
   `compound-engineering:ce-code-review`.
4. Attempt the Edit on `.claude/skills/itg-brief/SKILL.md`. Denied, once, with the text quoted above.
5. The run obeys the denial's instruction to stop and explain, finishes the last unit, runs the gate,
   and asks for approval twice, the second time offering to merge without the skill edit and open a
   follow-up card instead. Both options need an answer that cannot arrive.
6. Zero comments written to the card. No merge. No push. Clean exit, status zero.
7. A human applies the edit attended, merges, pushes, closes the card.

Roughly an hour of high-effort work reached the end, stopped one file short of landing, and the
tracker showed nothing.

### IW-83, what the runner should have done

1. Read the task's plan before launch. The pre-flight scan hits on
   `.claude/skills/itg-brief/SKILL.md` and exits 3 in under a second.
2. Route by whichever fits the operator's intent:
   - **Exclude.** Mark the task `excluded_from_unattended` under R5 with the reason recorded as
     "plan edits `.claude/skills/itg-brief/SKILL.md`; Edit under `.claude/` is denied in `dontAsk`".
     The run skips it and the summary names it as skipped and why, so the operator sees a deliberate
     skip instead of a mystery.
   - **Split.** Divide the task into a source-and-tests half that runs unattended and a small
     attended follow-up carrying the skill edit and the merge. The unattended half lands its commits
     and closes its own card. The attended half is minutes of human time on the one edit the harness
     will not let a headless process make.
3. If the task ran anyway, the post-run backstop diffs the branch against the R17 baseline, sees a
   `.claude/` path, and pairs that with the denial line R37 already pulls from the transcript. The
   summary then reads "blocked on a `.claude/` write, which `dontAsk` denies; apply attended" rather
   than leaving the operator to work out why an hour of correct work never merged.
4. Either way, the blocked exit writes to the card, not only to the transcript, so the board and the
   working tree agree on the state of the task.

The difference between the two versions is not the outcome of the skill edit. A human was always
going to make that edit. The difference is whether the runner found that out in the first second or
the last, and whether the tracker said so.

## Related

- `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`: the direct
  descendant, and the reason this doc is now scoped to the Claude backend. It found that Grok accepts
  `dontAsk` and then cancels every tool call rather than approving one, so a Task under it does no
  work at all. This doc generalizes that an allowlist is a claim about tools and not about paths;
  that one generalizes that a permission mode is a claim about vocabulary and not about behavior.
  Same family, one term further out, and the pair is worth reading together.
- `docs/brainstorms/2026-08-25-1240-feat-relay-outer-loop-plan.md`: R5 (exclude a task from
  unattended runs with a reason) is the mechanism this learning reuses; R4, R7, R10, R11, R17, R19,
  R23, R37, and AE2 are the requirements it sharpens.
- `README.md`: the three qualifying properties are about the project. This learning adds a constraint
  on an individual task, which is a separate axis.
