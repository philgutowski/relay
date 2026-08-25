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
  mode (PR-terminal or local merge), any mirror rule, and the disallow patterns. Everything
  project-specific is data here, never code in the runner.
- **Skill:** `/relay`. Writes the manifest from a conversation, checks the three properties
  above, launches the runner.

## Status

Pre-brainstorm. The design was proved by hand on 2026-08-25 with a shell script against a
Jira-tracked repo (headless `dontAsk` permission mode plus an MCP allowlist lets tracker writes
through with no prompt). `docs/` holds the pipeline artifacts as they are produced.
