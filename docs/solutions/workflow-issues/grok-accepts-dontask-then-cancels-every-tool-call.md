---
title: Grok accepts dontAsk at launch and then cancels every tool call, so a flag list is a claim about vocabulary rather than behavior
date: 2026-08-28
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: high
root_cause: missing_workflow_step
resolution_type: config_change
related_components:
  - contracts
  - backend-pins
  - permission-mode
  - task-process
  - launch
applies_when:
  - pinning a per backend permission posture for a CLI other than claude, taken from that CLI's --help output
  - launching a headless task under grok with --permission-mode dontAsk, the spelling grok --help lists alongside bypassPermissions
  - a headless run reports no launch error and then dies partway through with stopReason cancelled and no work landed
  - an evidence line says a user cancelled a tool call in a run where no user was present
  - a plan's Assumptions or a KTD pins a flag value taken from documentation rather than from an observed run
symptoms:
  - grok --permission-mode dontAsk is accepted at launch, prints no error and no warning, and the process starts normally
  - every tool call the task makes is cancelled, and the tool_call_update event attributes the cancellation to a user who is not there
  - the task does no work at all and the process dies partway through planning with stopReason cancelled
  - "reproduced five times: two full pipeline runs against the throwaway target and three isolated single command probes"
  - grok --help lists dontAsk and bypassPermissions, the same spellings claude uses, so the flag list reads as compatible vocabulary while the behavior is the opposite
tags:
  - permission-mode
  - backend-pins
  - launch-time-posture
  - grok
  - flag-vocabulary
  - first-live-run
  - unattended-run
  - demonstrated-refusal
---

# Grok accepts dontAsk at launch and then cancels every tool call, so a flag list is a claim about vocabulary rather than behavior

## Context

Relay runs one fresh headless CLI process per Task, serially, with nobody watching. The plan at
`docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md` widens that from `claude` alone to
three backends, so a Task can run on `codex` or `grok` instead. Every backend needs a permission
posture: the launch flag that lets a Task actually do work while still refusing the operations in
`contracts.DISALLOWED_TOOLS` (`skills/relay/scripts/relay/contracts.py:218` to `:230`).

The plan picked Grok's posture by reading `grok --help`. That flag list advertises
`--permission-mode` with the values `default`, `acceptEdits`, `auto`, `dontAsk`,
`bypassPermissions`, `plan`. Claude Code uses `dontAsk` as its own non bypass headless posture
(`contracts.py:130`). Two of the six spellings match Claude's exactly, so the plan reasoned from the
shared vocabulary that the semantics matched, and wrote `dontAsk` into KTD6 and into the Assumptions
bullet at line 257 of the plan.

Unit U1 was a spike whose whole purpose was to observe each CLI's real behavior before pinning any
constants, and it disproved that reasoning. Grok Build accepts `--permission-mode dontAsk` at launch,
reports no error, exits zero, and then cancels every tool call the headless Task makes. The
`tool_call_update` event in the streaming JSON says a user cancelled the execution of
`run_terminal_command`, even though the process is headless, no human is present, and nobody
cancelled anything. The run ends with `stopReason: cancelled`. A Task under that mode does no work
at all.

Reproduced five times: two full pipeline runs that died partway through planning, and three isolated
single command probes, which between them varied the command (`set -o noclobber && : > file`, then a
bare `: > file`, then a plain `echo "hello" > file`) and the tool's spelling in `--allow` (`Bash`
versus its real name `run_terminal_command`). The cancellation was invariant across all five.

**Where the evidence for this lives, and where it does not.** No fixture captures the `dontAsk`
failure, and that is deliberate rather than an omission: the posture produces no usable artifact,
which is the finding. What is committed is the replacement posture working, in
`tests/fixtures/backends/grok/`. The failure itself is recorded in prose, in the `BACKEND_PINS`
comment beside the pin it explains and in the plan's KTD6 amendment. A reader who goes looking for a
captured cancellation will not find one; reproduce it instead by launching any Grok Task under
`--permission-mode dontAsk`.

**How the pin was made, and why nothing caught it (session history).** The value was settled during
`ce-plan`'s research phase roughly four minutes after that session ran `grok --help`, and no Grok
process was ever launched with a real prompt before the spike. The recorded conclusion was that
Grok's `--permission-mode` accepts literally `dontAsk` and `bypassPermissions`, the same vocabulary
Claude uses. Three things then let that stand:

- **The doubt was raised and this is what closed it.** One research pass had flagged Grok's
  permission vocabulary as an open question that could kill the plan. The help text check was the
  answer given to that flag. The question asked whether the posture would work; the answer delivered
  was that the spelling matched. Nobody distinguished the two.
- **No alternative posture was ever considered for Grok.** `auto` and `acceptEdits` appear nowhere in
  the planning sessions. Codex got real posture reasoning in the same breath, with
  `--sandbox workspace-write` chosen and the bypass spellings named as forbidden. Grok got only the
  vocabulary match.
- **Claude's `dontAsk` had been hardened into a guardrail.** On Claude the value was empirically
  exercised, arriving with a working prototype, and its one known failure mode was already written up
  in `headless-dontask-blocks-claude-dir-edits.md`. A restart prompt then carried an explicit hazard
  line against loosening `dontAsk` to make a test pass. By the time the backends plan was written,
  `dontAsk` was not a tunable parameter in this project's mind, it was a rule you were forbidden to
  relax, which is a poor state in which to ask whether it means the same thing somewhere else.

Per this session's reconstruction of the planning history, the plan's own multi persona review rounds
never touched the Grok permission posture, and the adversarial reviewer raised this exact failure
shape, that a permission construct can exist by name on another CLI without the same enforcement
behavior, but scoped it to Codex's deny rules and never generalized it to Grok's `--permission-mode`.
No review artifact is committed, so that account rests on the session record rather than on the tree.
The load bearing part does not depend on it: the plan text asserting vocabulary equivalence was in
front of every reviewer, and the posture reached U1 unchallenged.

## Guidance

**Prove a launch time safety posture by observing both a refusal and a success. Never adopt one by
reading a help page.**

That is requirement R25 in the backends plan, stated at line 162: a backend is recorded as enforcing
restrictions at launch only when it has demonstrated a refusal of a denied tool. A backend that
cannot demonstrate one is recorded as not enforcing, which routes it through R19, R21, and R24. Read
R25 as covering two demonstrations, not one:

1. **The refusal.** Run a Task whose Brief instructs it to invoke a tool the deny list forbids, and
   capture the refusal verbatim into a fixture. Verify the side effect did not happen, not only that
   the CLI printed something that reads like a denial.
2. **The success.** Run the same posture through work that must actually execute tools, and confirm
   the tools ran. This is the half the plan's original wording did not force, and it is the half
   `dontAsk` would have failed. A mode that refuses everything passes a refusal test perfectly.

Concretely for Relay, `contracts.BACKEND_PINS["grok"]["permission_mode"]` is `"auto"`
(`contracts.py:194`) and `"dontAsk"` sits in that backend's `forbidden_permission_modes` tuple beside
`"bypassPermissions"` (`contracts.py:195`). The tuple is per backend and holds every forbidden
spelling, per KTD6, because a spelling the refusal does not know is a spelling that reaches the argv.
`dontAsk` is forbidden for Grok not because it is dangerous but because it silently produces nothing,
which for an unattended runner is its own kind of failure.

The demonstrated refusal is committed at `tests/fixtures/backends/grok/denial-refusal.jsonl`. Its
`tool_call_update` line records that `run_terminal_command` was not executed, denied by permission
policy against the deny rule matching `rm -rf*`. The check that the target directory survived was made
at the time, out of band, and is attested in the pin comment rather than in the fixture, since the
fixture holds the process's own output and not the operator's follow up. Verify the side effect
yourself when you repeat this on another backend; a refusal message is not proof the operation did not
happen. `enforces_at_launch` is `True` for Grok (`contracts.py:204`) on the strength of that
capture, not on the strength of `--deny` appearing in the help output. Compare Codex, where
`allow_flag` and `deny_flag` are both `None` (`contracts.py:160` to `:161`) and `enforces_at_launch`
is `False` (`contracts.py:166`), because no per tool deny flag exists to demonstrate a refusal with.

Two smaller Grok facts from the same spike, worth knowing but not the headline. A malformed `--deny`
rule spelling is rejected at launch with a message naming the missing closing parenthesis, so that
class of mistake fails fast rather than being silently accepted. And a bare `Skill` entry in
`--allow`, which `closeout.BASE_TOOLS` carries, is accepted without error.

## Why This Matters

A permission mode can be parsed, accepted, and silently invert its meaning at the point of use.
Grok's parser understood `dontAsk` well enough to not error on it; the executor then read it as
"never ask, therefore never approve" where Claude reads it as "never ask, therefore proceed". Same
token, opposite behavior, no diagnostic anywhere between the two.

For Relay the cost of getting this wrong is asymmetric in both directions. Pick a mode that refuses
everything and every Task on that backend burns a lease, a fresh process, and real tokens producing
nothing, while the halt looks like a task problem rather than a configuration problem. Pick a mode
that enforces nothing and the runner has no tool restriction at all while its capability record
claims it does, which is strictly worse than a backend that honestly records `enforces_at_launch` as
false and picks up the R19 acceptance sentence, the R21 landing bound, and the R24 audit as
compensating controls. The plan's own risk table names that second failure at line 901.

The generalization past this one flag: shared spelling across two vendors' CLIs is not shared
semantics. There is no standard behind `--permission-mode`, only convergent naming, and convergent
naming is exactly the condition under which an assumption feels too obvious to test.

**The contrast inside one session is the sharpest evidence (session history).** The same planning
session that pinned `dontAsk` from help text also ran `claude --version`, `codex --version`, and
`grok --version` for real, and that probe immediately produced a genuine defect: the version token
regex silently returns `None` for both alternates, because their output leads with a name token. It
was recorded at the time as a concrete finding rather than a hypothetical. Executed probes produced
findings, help text probes produced assumptions, and both were folded into the same plan carrying the
same confidence. The difference in reliability was invisible because the plan's format does not
distinguish an observed fact from a read one.

**Why this hid, and why the wrong suspect was structurally plausible.** The failure is silent. No
error, no nonzero exit, and a cancellation message naming a user who does not exist. The natural
first read is that something outside the CLI killed the process, and in this session it was initially
misdiagnosed exactly that way, as the orchestration harness killing a backgrounded process at a
timeout boundary. That suspect was not unreasonable, since this repo has already paid for a
neighbouring lesson about headless processes and backgrounding
(`headless-turn-end-is-exit-backgrounded-command-is-killed.md`). Real effort went into process
detachment before the real cause surfaced. macOS has no `setsid`, so the eventual fix was Python's
`subprocess.Popen(..., start_new_session=True)`, the same portable form the runner already uses at
`skills/relay/scripts/relay/launch.py:249` and `skills/relay/scripts/relay/cli.py:197`. A fully
detached run then reproduced the identical cancellation, which cleared the harness and left the
permission mode as the only remaining candidate. When an unattended process reports that a user
cancelled it, suspect the permission posture before suspecting the process plumbing.

## When to Apply

- Adding a backend, a model provider, or any third party CLI that Relay launches and depends on for a
  safety property. Every launch time posture is unproven until U1 style evidence exists for it.
- Any time a constant is about to be pinned from documentation, `--help` output, a changelog, or a
  vendor's README rather than from an observed run. The pin block in `contracts.py` exists so those
  values live in one place; it does not make them true.
- Any time a flag name on one tool matches a flag name on another. Matching names are the trigger to
  test harder, not the license to skip the test.
- Reading a halt whose evidence says a run was cancelled, interrupted, or stopped by a user, in a
  context where no user exists. Treat that message as untrusted narration about a cause, and check the
  launch configuration before the process supervision.
- Answering a flagged open question about whether an external tool will work. Check that the answer
  you are about to accept is about behavior and not about spelling, since those are the two that got
  swapped here.

The inverse also holds. Once a posture has both halves demonstrated and the fixtures are committed, it
does not need re demonstrating on every touch. The re verification triggers are a version bump of the
CLI, since `version_tested` is pinned per backend (`contracts.py:182` for Grok, `1.0.5`), a change to
the flags Relay passes, or a new backend, which is the axis this finding adds to the older doc's
version only rule.

## Examples

**Before, taken from the plan's Assumptions bullet at line 257.** The reasoning is entirely from the
flag list, and the phrase about sharing the `dontAsk` and `bypassPermissions` spellings carries the
whole unexamined inference:

> Grok Build's headless flags mirror Claude Code's closely: `-p/--single`, `-s/--session-id`, `-m/--model`, `--effort`, `--permission-mode` sharing the `dontAsk` and `bypassPermissions` spellings, `--allow`/`--deny` accepting Claude-style `Bash(...)` rules, and `--output-format streaming-json`.

**After, in `contracts.py:188` to `:195`**, where the pin carries the observation rather than the
inference:

```python
        # U1 finding, and a correction to the plan's Assumptions and KTD6. Grok accepts
        # `dontAsk` at launch and then cancels every tool call the task makes, reporting
        # "User cancelled the execution for tool `run_terminal_command`" with no human present
        # to have cancelled anything. Reproduced five times: two full pipeline runs that died
        # partway through planning, and three single-command probes. `auto` is the mode that
        # runs the task AND still refuses a denied call, so it is the non-bypass posture here.
        "permission_mode": "auto",
        "forbidden_permission_modes": ("bypassPermissions", "dontAsk"),
```

**The written amendment.** KTD6 in the plan was not quietly edited to the new value. It carries a
block quoted amendment directly beneath it, dated 2026-08-28, that states what the entry originally
claimed, what U1 observed, and why the correction follows. The plan's Sources and Research entry for
`grok --help` (line 993) was likewise annotated to read as what the CLI advertises, not what it does.
Recording the disproof beside the claim is what stops a later reader from re deriving the original
assumption from the same help page.

**What the demonstration looked like in practice.** Two probes, not one, against the same
`--deny "Bash(rm -rf*)"` rule:

- The refusal probe. Brief the Task to run `rm -rf` against a scratch marker directory, capture the
  `tool_call_update` carrying `status: "failed"` and the permission policy denial text, then check the
  directory is still on disk. That capture is the committed fixture.
- The success probe. Run a real seven stage pipeline task on the same posture and confirm every stage
  ran. Under `auto` it did: `tests/fixtures/backends/README.md` records T-14 adding `is_palindrome`
  and landing, and T-15 blocking on an unavailable webhook, both through all seven stages. Under
  `dontAsk` the same shape of run died partway through planning with `stopReason: cancelled`, which is
  what no amount of refusal testing alone would have caught.

That fixture belongs to the first body of fixtures in this repo cut from real process output rather
than generated, following the long Closeout transcript the stubbed seams doc singles out as the first
one a real process wrote. It is the same corrective applied to a new seam.

The fix described here landed on branch `worktree-issue-16-alt-cli-backends`, at commit a3443d8 as of
this writing, pushed but not yet merged to main. The branch is the durable locator; the SHA is the
commit at the time of writing and does not survive a rebase, so prefer the branch and the
`BACKEND_PINS` block itself when tracing this later.

## Related

- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` is the direct ancestor
  and the closest doc in the corpus. Same subject, a headless permission mode denying work the flag
  list gave no warning about, and same instrument, a live unattended run rather than the suite. Its
  closing generalization is that an allowlist is a claim about tools and not about paths; this doc is
  the next term in that sequence, that a permission mode is a claim about vocabulary and not about
  behavior. Its re test instruction is written against the CLI version moving, and this finding adds
  the second axis, re test per backend. Read as Claude backend facts: its `.claude/` path gate and its
  `dontAsk` posture are both Claude specific now that the posture is a per backend tuple.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  shares the mechanism, that a claim and the thing meant to verify it drew on a single source, so they
  could not disagree, and only an instrument outside that source could produce a contradiction. The
  reason they agreed differs and the difference is worth keeping: there it was shared authorship, a
  fixture and a parser written by the same hands, while a vendor's help page is external and the error
  is a category one, reading an artifact that enumerates accepted vocabulary as if it described
  behavior at the point of use. Its Prevention section already carries two members of this family, a
  stubbed seam agrees by construction and a stubbed subprocess is free by construction; a documented
  flag list is agreeable by construction is the third.
- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md` is the
  second member of the running series of harness gates a live unattended run found and the suite could
  not. This is the third, and the first on a non Claude harness, which widens the series from one
  harness having undocumented gates to every harness having them, with coinciding flag vocabularies
  being no evidence that the gates coincide.
- `docs/solutions/workflow-issues/envelope-key-effect-only-visible-in-rendered-closeout-brief-not-digest.md`
  is the same epistemic shape at a different seam. A field's presence in a parsed digest proves
  parsing, not that the rendered artifact carries it; a flag accepted at launch proves parsing, not
  enforcement.
- Issue 16 on `philgutowski/relay` is the tracker item this branch is named for. Its own open
  questions section called for a spike rather than a design, specifically because headless mode
  equivalence across these CLIs needed verification. U1 is that spike and this is part of its answer.
