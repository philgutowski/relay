# Backend routing rubric

`/relay` proposes a backend per Task from this file. The operator sees every proposal and can change it. The Runner never chooses or changes a backend during a run.

The closed set is `claude`, `codex`, and `grok`. Every backend runs the same pipeline. What differs is launch-time enforcement, residual exposure, and the compensating controls.

## What distinguishes them for routing

**Launch-time tool refusal.** `claude` and `grok` have demonstrated a refusal of a denied tool at launch. `codex` has no flag that does that. A Codex Task is told the allow list and the disallow list as Brief instructions, and the run records those restrictions as unenforced.

**Compensating controls on Codex.** One layer of defence in depth is gone. In its place: the operator's own acceptance sentence, a Task path bound that checks commit scope only before merge, and an evidence audit after the process exits. The landing guarantee itself is untouched. Verify-landed still reads git and the Tracker alone.

The audit detects rather than prevents. A matched call has already run. A match in the named destructive set (force push, hard reset, recursive delete) refuses the landing. Other matches land with a finding.

The audit matches command spellings, so what it can match bounds both of those. A disallowed operation reached by another spelling, a restriction naming a tool other than a command, and a call the process's log never recorded all produce no finding, and the destructive refusal rests on the same match. The Task path bound is the one control here that no command spelling evades, because it reads the paths the commit touched. Weigh `codex` on that basis rather than on the audit alone. Relay proof T-65 is the observed case: a Task ran a disallowed operation under a different spelling and the audit correctly reported nothing.

**Residual exposure, every backend.** All three CLIs share the operator's home and keep credentials in files there. Environment scrubbing does not reach those files. Codex's sandbox bounds writes, not reads. The acceptance sentence is where the operator accepts that residual on an unenforced backend.

**Adapter pairing.** Jira Closeout tools exist only on Claude. A Jira manifest with a Codex or Grok Task is refused at validate. GitHub and markdown pair with every backend.

## How to propose

Read the Task. Propose one backend and a one-line reason. Prefer `claude` when the work is high judgment, high blast radius, or needs launch-time refusal. Prefer `codex` or `grok` when the work is mechanical, bounded, and a good use of a different account's budget. Prefer `grok` over `codex` when launch-time refusal still matters. Prefer `codex` only when the operator is willing to write the acceptance sentence and set a Task path bound.

This is judgment, not a table. If the operator changes a proposal, keep their choice. Do not re-apply this rubric afterwards.

When the chosen backend does not enforce at launch, state that condition in plain words and ask the operator to write the acceptance sentence and to set `task_allowed_paths`. Write only what they supply. Do not invent either to make validate pass.

When a Task's backend differs from the manifest default, the Task carries a `reason` string. If they accepted a non-default proposal, the one-line reason you stated is that string. If they changed the backend, they supply the string. One string also covers an excluded Task.
