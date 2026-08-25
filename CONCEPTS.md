# Concepts

Shared domain vocabulary for this project, entities, named processes, and status concepts with
project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and
ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or
catch-all.

## Relationships

A Runner reads one Manifest and drives a series of Tasks. Each Task gets its own Task process and,
once it lands, its own Compound process. The Runner decides a Task's outcome by Verify-landed,
which consults git and the Tracker through a Tracker adapter, never the Task process itself. The
Shipping mode named in the Manifest decides what landing means for that project.

## The loop

### Runner
The Relay process that drives a Manifest to completion: it launches one Task process per Task in
order, decides each outcome, and halts rather than continuing past an outcome it cannot confirm.

The Runner holds no project knowledge of its own. Everything project-specific reaches it as
Manifest data. It reads the Tracker but does not write to it, so a defect in the Runner can never
move a card.

### Manifest
The single file, one per project, carrying every project-specific fact a Runner needs: the Task
list, the Tracker adapter to use, the Shipping mode, the permission allowlist and disallow list,
per-Task timeouts, and how each of the project's qualifying properties is satisfied.

### Task
One unit of work the operator defined before the run started, identified by a Tracker record. Tasks
in a Manifest are independent of each other by requirement; a Task that depends on another belongs
in a later run. A Task may be marked excluded from unattended runs, with a stated reason, when
something about it needs a human present.

### Task process
The single headless agent invocation that carries one Task from plan to landing. It starts with an
empty context, knows nothing of any other Task, and its report of its own success is not evidence.

### Compound process
A separate short agent invocation that runs after a Task lands, whose only job is to judge whether
that Task produced a learning worth keeping and to write it if so. It is separate because a Task
process at the end of its context is the worst available judge of its own learning.

## Outcomes

### Verify-landed
The Runner's own determination, from git and the Tracker alone, of whether a Task actually landed.
It never reads the Task process's exit code, printed result, or claims as evidence, because a
headless run has every incentive to report success.

### Landed
A Task whose work is durably where the Shipping mode says it belongs and whose Tracker record names
the landing. Both halves are required: code that merged while the card stayed put is a partial
landing, not a landing, and it halts the run.

### Blocked
A Task whose process stopped deliberately without landing, leaving the repository as it found it. A
Blocked Task is a normal outcome rather than a failure.

A Blocked Task is only legible to an operator who was not watching if the blocker reaches somewhere
outside the run's own transcript. Where that record is written, and whether the Runner may write it
given that it is otherwise read-only on the Tracker, is not yet settled.

### Shipping mode
The per-project choice of what landing means: a pull request whose checks have decided, or a merge
to the default branch performed outside the Task process. The Manifest names one, and Verify-landed
applies the matching checks.

## Surroundings

### Tracker adapter
The read-side interface between a Runner and whatever holds the Task list and their statuses. Each
adapter exposes the same three operations regardless of what sits behind it: list candidate Tasks,
read one Task's status, and confirm that a closing reference is present.

### External gate
The project's own mechanism that refuses a broken change independently of any agent's judgment,
such as a pre-push hook or continuous integration. Relay requires one to exist before it will run
against a project, and verifies its presence rather than providing it.

### Qualifying properties
The three conditions a project must meet before Relay can run against it: its Tasks are independent,
the state carried between Tasks is durable in git or on the Tracker, and an External gate refuses
broken changes. These describe the project. A Task can still be individually unrunnable while all
three hold.
