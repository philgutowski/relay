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
process, Halt class, Verify-landed. Use those words with the operator.

## The runner

Every operator action is a runner subcommand. There is nothing this skill can do that an operator
at a terminal cannot do the same way, and no verb prompts for anything.

Resolve `<runner>` once, from this skill's own directory as the harness gave it to you:

```text
<runner> = <this skill's directory>/scripts/relay_cli.py
```

The six verbs:

```bash
python3 <runner> validate <manifest>            # check the manifest and its target repo
python3 <runner> validate <manifest> --list     # the same, plus the tracker's candidate tasks
python3 <runner> run <manifest>                 # run to completion or to a halt
python3 <runner> run <manifest> --retry-blocked # the same, retrying records that read blocked
python3 <runner> status <manifest>              # what the run is doing; never takes the lease
python3 <runner> summary <manifest>             # the run summary as text
python3 <runner> summary <manifest> --json      # the same summary as data
python3 <runner> verify <manifest> <task-id>    # re-run the landing verdict for one task
python3 <runner> lease <manifest>               # who holds the lease
python3 <runner> lease <manifest> --break       # clear it; operator's explicit call only
```

Exit codes: 0 fine, 1 the manifest or environment is wrong, 2 the run halted, 3 another runner
holds the lease.

## Author a manifest

Start from what the tracker already holds rather than from a blank file. Write the manifest to a
path outside the target repo, since Relay adds nothing to a project it runs against.

1. Ask which repo and which tracker (jira, github, or markdown), then write a draft manifest and
   run `validate <manifest> --list` to read the candidate tasks back.
2. Confirm with the operator, one question at a time: which tasks to include and in what order;
   the model and effort for each; the shipping mode (`local_merge` when the runner merges and
   pushes, `pr_terminal` when the task process opens a pull request and CI decides); any task to
   exclude and why; and the two degraded path answers, `on_blocked.merge_partial` and
   `on_blocked.open_followup`.
3. Ask for the four qualifying sentences, in the operator's own words, as data:
   - `qualifying.gate`: what refuses a broken change, and how it is invoked.
   - `qualifying.durable_state`: where the state carried between tasks lives.
   - `qualifying.independence`: why the listed tasks do not depend on each other.
   - `qualifying.editors`: who can edit these cards and their comments. This one matters because
     card text is fed verbatim to an unattended process, so it names the accounts whose text is
     trusted to instruct one.
4. Write the TOML. The gate command and any mirror rule are argument lists, never shell strings.
   Do not add a permission mode field: Relay always runs `dontAsk` and offers no switch.

The examples under `docs/examples/` are the three shapes, one per adapter.

## Validate before anything else

```bash
python3 <runner> validate <manifest>
```

Refuse to launch when it exits nonzero, and name the property that failed rather than the exit
code. A missing qualifying satisfier is the most common one: say which of `qualifying.gate`,
`qualifying.durable_state`, `qualifying.independence`, or `qualifying.editors` has no sentence,
and ask the operator for it. Do not invent one on their behalf. If validate names a missing
credential environment variable, ask them to set it and run validate again.

## Launch

The runner outlives this session, so start it detached and stop. Do not wait on it.

```bash
setsid caffeinate -i python3 <runner> run <manifest> </dev/null \
  >~/.relay/<manifest-hash>/runner.log 2>&1 &
```

`setsid` gives the runner its own session, so a harness reaping this tool call's process group
cannot end the run. `caffeinate -i` keeps the host awake. Lid close is not supported: the machine
must stay open for the whole run.

A few seconds later, confirm it took the lease:

```bash
python3 <runner> status <manifest>
```

Then tell the operator three paths and stop: the state directory, the runner log
(`runner.log` inside it), and the per task output log for the task now running. The state file is
the contract between the runner and any later session, including yours.

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
| `path_gate` | the task needs an edit under `.claude/`, which `dontAsk` refuses whatever the allowlist says | apply that edit attended, then resume |
| `closeout_out_of_scope` | the closeout committed outside its allowed paths; the runner reset it | look at what it tried to write, then resume |
| `timeout` | the task ran past its bound and was killed with its whole process group | raise the timeout or split the task, then resume |
| `unclean_exit` | the process left a dirty tree, or claimed to finish and left nothing to merge | inspect the tree, clean it, resume |
| `runner_crashed` | a stale lease was reclaimed while a record was in flight | nothing usually; the next run re-verifies it |
| `ci_undecided` | in `pr_terminal` mode, the checks did not decide within the bound | wait for CI, then run `verify` for that task |

After a repair, confirm before resuming:

```bash
python3 <runner> verify <manifest> <task-id>
```

## Resume

Run the same command again. The runner re-verifies every halted record first and promotes any
that now pass, then resumes at the first task that did not land. A landed task is never re-run.

Blocked tasks are skipped by default, because blocked is a deliberate outcome rather than a
failure. Pass `--retry-blocked` only when the operator asks for it, and expect it to refuse when a
stranded `relay/<task-id>` branch still carries commits; that work is theirs to keep or discard.

## What this skill never does

Never merge, push, or move a card yourself. Never edit a manifest's qualifying sentences to make
validate pass. Never break a live lease without the operator saying so. Never suggest a permission
mode other than the one Relay uses.
