---
name: relay
description: Author a Relay manifest from a conversation, validate it, launch the runner detached, and explain a halt from state. Use when the operator wants to run a list of independent tracker tasks through the compound-engineering pipeline unattended, one fresh headless process per task, or asks what a running or halted Relay run is doing.
---

# Relay

Relay runs a list of independent tasks through the compound-engineering pipeline, one fresh
headless process per task, serially, with nobody watching. Your job in this skill is to author
the manifest, check it, start the runner, and later explain what happened. You never do the
runner's work by hand.

Read `CONCEPTS.md` at the repo root for the vocabulary: Runner, Manifest, Task process, Closeout
process, Backend, Halt class, Verify-landed. Use those words with the operator.

## The runner

Every operator action is a runner subcommand. There is nothing this skill can do that an operator
at a terminal cannot do the same way, and no verb prompts for anything.

Resolve `<runner>` once, from this skill's own directory as the harness gave it to you:

```text
<runner> = <this skill's directory>/scripts/relay_cli.py
<rubric> = <this skill's directory>/references/backend-rubric.md
```

Read the file at `<rubric>` before proposing a backend. That path is under this skill's own
directory, not the target repo. If the file cannot be opened, stop rather than inventing routing.

The seven verbs, with the follower options on the two that follow:

```bash
python3 <runner> validate <manifest>            # check the manifest and its target repo
python3 <runner> validate <manifest> --list     # the same, plus the tracker's candidate tasks
python3 <runner> run <manifest>                 # run to completion or to a halt
python3 <runner> run <manifest> --retry-blocked # the same, retrying records that read blocked
python3 <runner> run <manifest> --detach        # the same, in its own session, logged to the state dir
python3 <runner> run <manifest> --follow        # detach, then follow it here; implies --detach
python3 <runner> status <manifest>              # what the run is doing; never takes the lease
python3 <runner> tail <manifest>                # follow the tasks' activity decoded; never takes the lease
python3 <runner> summary <manifest>             # the run summary as text
python3 <runner> summary <manifest> --json      # the same summary as data
python3 <runner> verify <manifest> <task-id>    # re-run the landing verdict for one task
python3 <runner> lease <manifest>               # who holds the lease
python3 <runner> lease <manifest> --break       # clear it; operator's explicit call only
```

`run --follow` and `tail` share three options: `--phases` prints phase events without the decoded
task activity, `--for <seconds>` stops following at a bound and leaves the run going, and
`--notify` fires a macOS notification on each phase event. A phase event is a task's log starting,
a task's status moving, or the run reaching a terminal record.

Exit codes: 0 the run reached the end of the manifest, 1 the manifest or environment is wrong,
2 the run halted, 3 another runner holds the lease. Under `on_halt.continue_past_task_halt`, 0
can still mean one or more tasks halted and were stepped over: read `summary`'s check-by-hand
list rather than the exit code alone.

## Author a manifest

Start from what the tracker already holds rather than from a blank file. Write the manifest to a
path outside the target repo, since Relay adds nothing to a project it runs against.

1. Ask which repo and which tracker (jira, github, or markdown). Ask for the Task branch prefix,
   default `relay/`. An empty string is the Task id alone. When the tracker is jira, Task ids
   already look like `ABC-12`, so empty is the usual escape. Write `project.branch_prefix` only
   when the operator names a value other than the default. Point at the commented
   `branch_prefix` lines under `[project]` in `docs/examples/` rather than inventing a new table.
   Then write a draft manifest and run `validate <manifest> --list` to read the candidate tasks
   back.
2. Confirm with the operator, one question at a time: which tasks to include and in what order;
   the model, effort, and backend for each; any task to exclude and why; and the three degraded path
   answers, `on_blocked.merge_partial`, `on_blocked.open_followup`, and
   `on_halt.continue_past_task_halt`. For each included Task, propose a backend from the rubric
   with a one-line reason, then wait for accept or change. Do not write a backend the operator
   has not seen. Nothing re-applies the rubric after they choose. A Jira tracker can only pair
   with `claude`. The third degraded-path answer trades a mid run stop for throughput: on, a
   halt contained to one task pauses that task and the later independent tasks keep running,
   so several halts in a row surface only in the summary; off, the first halt stops the run.
   A value the operator gives goes into the manifest verbatim. Recommend when asked; never substitute your
   recommendation for an answer they already gave, including timeouts and status names. The shipping mode is
   `local_merge`, where the runner merges and pushes. `pr_terminal` is named in the schema and
   refused by `validate`: the run loop has no pull request sequence, so every task under it
   would halt without one being opened or checked.
3. If any chosen backend does not enforce tool restrictions at launch (today that is `codex`),
   state that condition in plain words: launch-time refusal is gone, the Task path bound covers
   commit scope only, and the evidence audit detects after the fact. Ask the operator to write
   `permissions.unenforced_acceptance` in their own words, and to set `permissions.task_allowed_paths`.
   Write only what they supply. Never invent either sentence or list, including to make validate
   pass.
4. Ask for the four qualifying sentences, in the operator's own words, as data:
   - `qualifying.gate`: what refuses a broken change, and how it is invoked.
   - `qualifying.durable_state`: where the state carried between tasks lives.
   - `qualifying.independence`: why the listed tasks do not depend on each other.
   - `qualifying.editors`: who can edit these cards and their comments. This one matters because
     card text is fed verbatim to an unattended process, so it names the accounts whose text is
     trusted to instruct one.
5. Write the TOML. The gate command and any mirror rule are argument lists, never shell strings.
   The gate is one command, the one the operator named; when a project's merge bar is several
   commands, ask which one the runner runs and say what covers the others (a pre-commit hook,
   usually). Do not author a wrapper script to bundle them unless the operator asks for one.
   Do not add a permission mode field: permission posture is fixed per backend by
   `contracts.BACKEND_PINS`, never a manifest choice. Put a `[defaults] backend` when every Task
   should inherit one. The resolved default is that value when the key is present, else `claude`.
   On a Task whose backend differs from that resolved default, write `reason` with the one-line
   reason they accepted, or the string they supplied after they changed the backend. One `reason`
   also covers an excluded Task. A Task that matches the resolved default needs no reason.

The examples under `docs/examples/` are the three shapes, one per adapter.

## Validate before anything else

```bash
python3 <runner> validate <manifest>
```

Refuse to launch when it exits nonzero, and name the property that failed rather than the exit
code. A missing qualifying satisfier is the most common one: say which of `qualifying.gate`,
`qualifying.durable_state`, `qualifying.independence`, or `qualifying.editors` has no sentence,
and ask the operator for it. Do not invent one on their behalf. A missing
`permissions.unenforced_acceptance`, a missing `task_allowed_paths` on an unenforced backend,
or a Task whose backend differs from the default with no `reason`, is the same shape: ask for
the operator's words, never invent them. If validate names a missing credential environment
variable, ask them to set it and run validate again. A backend readiness failure (binary
missing, plugin missing or below floor) is also a validate refusal, before any Task launches.
See Backend readiness below.

## Confirm before launch

Launching starts an unattended process that will merge and push to the operator's repository.
After validate passes, show the manifest path, the task list with model, effort, and backend,
the gate command, and `project.branch_prefix` (name the default when the key was omitted) with
one example branch, prefix plus the first Task id, then ask for an explicit go. Do not launch on the strength of the manifest being
valid, and do not launch when the operator has said to stop before launch.

## Launch

Launch and then stay with it. The operator should not have to open a second terminal to find out
what their own run is doing.

```bash
python3 <runner> run <manifest> --follow --phases --notify --for 540
```

Run that with your harness's command timeout set to its maximum, 600000 ms. The `--for 540` bound
is nine minutes, chosen to end the follow inside that cap rather than be killed at it.

`--follow` implies `--detach`, which starts the run in its own session, so a harness reaping this
tool call's process group cannot end it, and logs to `runner.log` in the state directory. On macOS
it wraps the run in `caffeinate -i` so the host stays awake; there is no `setsid` binary on macOS,
so do not reach for one. Lid close is not supported: the machine must stay open for the whole run.

`--phases` is what makes this usable in a session. Without it the follower prints every tool call
every task makes, which is right in a terminal and would consume this session's context in
minutes. `--notify` reaches the operator when they have walked away.

Three endings, and what to say for each:

- **A run summary.** The run finished inside the bound. Explain it the way "Explain a halt" below
  says, if it halted.
- **`still running after 540 second(s)`.** The bound was reached and the run continues. Report
  where it has got to, then hand the operator the bare `tail` command for their own terminal, and
  say that `Ctrl+C` there stops the follower and leaves the run alive. Do not launch again.
- **`the runner exited without writing a terminal record`.** The run never started, usually a held
  lease or an invalid manifest. Read the runner log the line names and say what it was.

The state directory, `runner.log` inside it, and the per task output logs are the paths a later
session needs. The state file is the contract between the runner and any later session, including
yours.

The bare follower is for the operator's own terminal:

```bash
python3 <runner> tail <manifest>
```

It follows the tasks in order, decoded, and exits when the run reaches a terminal record. Like
`status` it takes no lease, so it is safe beside a live run, and `Ctrl+C` stops the follower
without touching the runner. Do not run it bare in this session: it has no bound, so it does not
return until the run ends.

## Explain a halt

```bash
python3 <runner> status <manifest>
python3 <runner> summary <manifest> --json
```

Everything you need is in those two outputs. Do not open a session transcript: the runner already
classified the exit into a halt class with its evidence, and the summary carries the cause line
and the checks a human still has to make. Explain the class in plain words, name the evidence,
and say what the operator has to do.

The classes and what they mean for the operator:

| Class | What happened | What the operator does |
|---|---|---|
| `gate_refused` | the project's gate refused the branch, or a push was rejected | read the gate log the summary names, fix, resume |
| `remote_advanced` | the default branch moved during the task, or the merge conflicted | rebase or redo the task branch by hand, resume |
| `partial_landing` | the code is on the remote but the card did not move | move the card by hand, then run `verify` for that task |
| `tracker_write_denied` | a tracker write was refused, so the card stayed put | check the tracker credentials, move the card, then `verify` |
| `path_gate` | the task needs an edit under `.claude/`, which the task's backend permission posture refuses whatever the allowlist says | apply that edit attended, then resume |
| `closeout_out_of_scope` | the closeout committed outside its allowed paths; the runner reset it | look at what it tried to write, then resume |
| `timeout` | the task ran past its bound and was killed with its whole process group | raise the timeout or split the task, then resume |
| `unclean_exit` | the process left a dirty tree, or claimed to finish and left nothing to merge | inspect the tree, clean it, resume |
| `runner_crashed` | a stale lease was reclaimed while a record was in flight | nothing usually; the next run re-verifies it |
| `unexpected_error` | the run loop hit something it did not anticipate: a defect, a library error, a task process that could not be launched, or a manifest naming an unimplemented shipping mode | read the error text in the cause line and the runner log; the fault is in the runner or the manifest, not the task, so fix that before resuming |
| `ci_undecided` | reserved for `pr_terminal` mode, which `validate` refuses; no run can reach it today | not applicable |

After a repair, confirm before resuming:

```bash
python3 <runner> verify <manifest> <task-id>
```

## Backend readiness

A backend readiness failure surfaces before any of this: `validate` and `run` exit 1 with the
manifest's environment unready, before any Task launches. It is not a halt class, since the runner
never started.

| Error text | What it means | What the operator does |
|---|---|---|
| `backend <name> binary <binary> is missing from PATH` | that backend's CLI is not installed, or not on `PATH` | install the backend's CLI and put it on `PATH` |
| `backend <name> plugin probe failed: <exc>` | the backend's plugin-list subcommand could not run at all | run it by hand and fix why it errors or hangs: `claude plugin list`, `codex plugin list`, or `grok plugin list --json` |
| `backend <name> has no readable compound-engineering plugin at or above <version>` | `compound-engineering` is missing, disabled, or unreadable for that backend | install it: `codex plugin add compound-engineering@compound-engineering-plugin`, `grok plugin install EveryInc/compound-engineering-plugin`, or for `claude` add it from its own marketplace and enable it |
| `backend <name> has compound-engineering plugin <version>, below required <version>` | the installed plugin is older than the pinned floor | upgrade it through that backend's own plugin update command to at least the pinned floor |

After fixing the environment, confirm before resuming:

```bash
python3 <runner> validate <manifest>
```

## Resume

Run the same command again. The runner re-verifies every halted record first and promotes any
that now pass, then resumes at the first task that did not land. A landed task is never re-run.

Blocked tasks are skipped by default, because blocked is a deliberate outcome rather than a
failure. Pass `--retry-blocked` only when the operator asks for it, and expect it to refuse when a
stranded Task branch still carries commits; that work is theirs to keep or discard.

## What this skill never does

Never merge, push, or move a card yourself. Never edit a manifest's qualifying sentences, the
unenforced acceptance sentence, or a Task `reason` to make validate pass. Never break a live
lease without the operator saying so. Never suggest a permission mode other than the one Relay
uses. This skill itself runs in Claude Code. Only the launched Task and Closeout processes vary.
