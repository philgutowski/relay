# Relay

Working name. Run a list of pre-defined tasks through a full engineering pipeline, one fresh
headless Claude Code process per task, serially and unattended.

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

- **Runner:** a small script. Reads a manifest, pops the next task, launches `claude -p` with
  the task's model, effort, and permission allowlist, waits, verifies the landed state, runs the
  compound judgment as a separate short process, advances or halts.
- **Manifest:** one file per project. Names the tracker adapter, the task list, the shipping
  mode, any mirror rule, and the disallow patterns. Everything project-specific is data here,
  never code in the runner. One shipping mode runs today, `local_merge`, where the runner runs
  the gate and owns the merge. `pr_terminal`, where each task ends at a pull request whose checks
  have decided, is named in the schema and refused by `validate` until its run loop sequence
  exists.
- **Skill:** `/relay`. Writes the manifest from a conversation, checks the three properties
  above, launches the runner.

## Install

Relay is a Claude Code plugin. Nothing in it needs editing to run against a new project: every
project specific fact lives in a manifest outside the target repo.

1. Install the plugin from this repo, alongside the `compound-engineering` plugin version 3.23.4
   or later, which owns the per task pipeline Relay calls.
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
python3 skills/relay/scripts/relay_cli.py status <manifest>
python3 skills/relay/scripts/relay_cli.py summary <manifest>
```

The run halts rather than continuing past an outcome it cannot confirm, and the summary names the
halt class, its cause, and what a human still has to check. Repair by hand, then run again: the
runner re-verifies what halted and resumes at the first task that did not land.

## Where things are

- `CONCEPTS.md`: the vocabulary. Runner, Manifest, Task process, Closeout process, Halt class,
  Verify-landed. Read this first.
- `docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`: the implementation plan, including
  the requirements, the key technical decisions, and the halt class table.
- `docs/examples/`: one manifest per adapter.
- `docs/solutions/`: learnings, starting with the `.claude/` permission gate that shaped the
  pre-flight scan.
- `skills/relay/scripts/relay/`: the runner package.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Every test runs against a stub `claude` on the PATH with a temporary `HOME`. Nothing in the suite
launches a real model, touches a network, or invokes `gh`.
