# Relay

Working name. Run a list of pre-defined tasks through a full engineering pipeline, one fresh
headless process per task, serially and unattended. `/relay` itself runs in Claude Code. Each
Task process and its Closeout process run on the backend that Task names, `claude`, `codex`, or
`grok`.

Each task gets its own context window. Nothing carries between tasks except what landed in git
and on the tracker. The runner verifies that landed state itself before it starts the next task,
and it never trusts a run's own report of success.

## Why this exists

The compound-engineering plugin ships an excellent single-task pipeline (`lfg`, `ce-work`,
`ce-code-review`, `ce-compound`). Nobody ships the outer loop: take the next ticket, run it in
its own process, confirm it merged, judge whether it produced a learning worth keeping, take the
next one. Relay is that outer loop and nothing more.

## What a project needs before Relay can run against it

1. **Independent tasks.** Each one can be planned, built, reviewed, and merged without the
   others in flight.
2. **Durable state between tasks.** A merge commit on the default branch and a card on a board
   are the only memory. If a task's outcome lives anywhere else, the next task cannot see it.
3. **An external gate that refuses broken changes.** A pre-push hook that runs the test suite,
   or CI that blocks the merge. This is what makes unattended acceptable. A project without one
   gets a gate before it gets Relay.

## Shape

- **Runner:** a small script. Reads a manifest, pops the next task, launches that Task's backend
  with the task's model, effort, and permission allowlist, waits, verifies the landed state, runs
  the compound judgment as a separate short process on the same backend, advances or halts.
- **Manifest:** one file per project. Names the tracker adapter, the task list, the shipping
  mode, any mirror rule, and the disallow patterns. A `[defaults]` table may set `backend`. A
  Task may name its own `backend`. When that value differs from the default, the Task carries a
  `reason`. `permissions.task_allowed_paths` is the commit-scope bound for an unenforced backend.
  `permissions.unenforced_acceptance` is the operator's own sentence accepting that condition.
  A Jira adapter cannot pair with `codex` or `grok`. Everything project-specific is data here,
  never code in the runner. One shipping mode runs today, `local_merge`, where the runner runs
  the gate and owns the merge. `pr_terminal`, where each task ends at a pull request whose checks
  have decided, is named in the schema and refused by `validate` until its run loop sequence
  exists.
- **Skill:** `/relay`. Writes the manifest from a conversation, proposes a backend per Task from
  the rubric, checks the three properties above, launches the runner. The skill itself runs in
  Claude Code. Only the launched processes vary.

## Install

Relay is a Claude Code plugin. Nothing in it needs editing to run against a new project: every
project specific fact lives in a manifest outside the target repo.

1. Install the plugin. A plugin installs from a marketplace, and this repository is its own
   single plugin marketplace (`.claude-plugin/marketplace.json`), so it is two commands:

   ```bash
   claude plugin marketplace add /path/to/relay   # or the GitHub URL
   claude plugin install relay@relay
   ```

   The install is a copy, not a link: after changing the skill or the runner, bump `version`
   in `.claude-plugin/plugin.json` and run `claude plugin update relay@relay`. The
   `compound-engineering` plugin version 3.23.4 or later must be installed too; it owns the per
   task pipeline Relay calls.
2. Confirm the runner works. It is Python 3 standard library only, so there is nothing to install:

   ```bash
   python3 skills/relay/scripts/relay_cli.py validate docs/examples/manifest-markdown.toml
   ```

   It will refuse until `project.repo` points at a real checkout, and it names what is missing.
3. For a Jira project, set the two environment variables the manifest names, by default
   `JIRA_API_TOKEN` and `JIRA_EMAIL`. For GitHub Projects, be logged in to `gh`.

## Use

Copy the example that matches your tracker from `docs/examples/`, point it at your repo, and
answer the four qualifying sentences in it. Then, in a Claude Code session in any repo, run
`/relay` and it will read the tracker, confirm the list with you, validate, and launch the runner
detached. Everything the skill does is a runner subcommand you can also run yourself:

```bash
python3 skills/relay/scripts/relay_cli.py validate <manifest> --list
python3 skills/relay/scripts/relay_cli.py run <manifest>
python3 skills/relay/scripts/relay_cli.py run <manifest> --detach
python3 skills/relay/scripts/relay_cli.py status <manifest>
python3 skills/relay/scripts/relay_cli.py tail <manifest>
python3 skills/relay/scripts/relay_cli.py summary <manifest>
```

A first launchd or cron launch of the runner sits outside Terminal's privacy grants. Before that
fire, grant the python binary that job launches access to the folders it will read (the checkout
and the state directory), typically Documents when the checkout lives there, in System Settings,
Privacy and Security, Files and Folders, or grant Full Disk Access. For launchd that path is the
ProgramArguments python. For cron it is the python in the crontab command. The grant is per binary
and holds until that executable's identity changes. Without it the process stalls on a Files and
Folders or Full Disk Access prompt on that Mac, with no halt class and no log line, because nothing
has started yet. Look at the Mac display. This session cannot click it.

`tail` is how you watch a run that is already going. It follows each task's output in order and
prints it decoded, one line per event, instead of the stream json that lands in `runner.log`. It
works before, during, and after a run, takes no lease, and exits when the run reaches a terminal
record.

The run halts rather than continuing past an outcome it cannot confirm, and the summary names the
halt class, its cause, and what a human still has to check. Repair by hand, then run again: the
runner re-verifies what halted and resumes at the first task that did not land. A manifest may opt
into continuing past a halt contained to one task instead (`on_halt.continue_past_task_halt`); the
summary still lists that task as a check-by-hand item, and the same repair-and-rerun path applies.

## Where things are

- `CONCEPTS.md`: the vocabulary. Runner, Manifest, Lease, Task process, Closeout process,
  Backend, Halt class, Cause line, Verify-landed. Read this first.
- `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`: the implementation plan, including
  the requirements, the key technical decisions, and the halt class table.
- `docs/examples/`: one manifest per adapter.
- `docs/solutions/`: the learnings store, three so far. The `.claude/` permission gate that shaped
  the pre-flight scan, the process group kill that resolved its target too late, and the cause line
  contract that lived in two places and degraded to question marks.
- `skills/relay/scripts/relay/`: the runner package.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Every test runs against a stub `claude` on the PATH with a temporary `HOME`. Nothing in the suite
launches a real model, touches a network, or invokes `gh`.
