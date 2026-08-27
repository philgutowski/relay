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

### Lease
The claim a Runner holds while it drives a Manifest, which is what stops two Runners from
interleaving work against one repository. There are two: one over the Manifest, so a second run
of the same Manifest refuses to start, and one over the target repository, so two different
Manifests naming the same repository cannot merge into it at the same time.

A Lease is renewed on a heartbeat rather than held for the length of the work, so it expires on
its own if a Runner dies. A Lease past its expiry is stale and the next Runner reclaims it,
marking any Task the dead Runner left in flight as halted. The expiry is deliberately shorter
than any Task timeout, so a crashed Runner never blocks a repository for the length of a Task;
the cost of that choice is that every long operation a Runner performs has to keep renewing, and
one that does not is how a Runner ends up acting without the claim it thinks it holds.

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

A Task process owns whatever it spawns. Subagents and gate commands run underneath it and can
outlive it, so the Runner bounds the whole group rather than the one invocation, and a Task
process that has exited is not by itself evidence that its work has stopped.

Its own turn ending and the process exiting are the same event, so anything it starts in the
background is at the mercy of that exit rather than surviving it. This is why the Runner's own
bounding of the whole group is a backstop rather than the first line of defense: what usually
kills a background command is the process's own turn ending, not the Runner noticing afterward.

### Closeout process
A separate short agent invocation the Runner launches after every Task process exit except a
timeout that left the tree dirty. It has two ordered duties: write the Task's outcome to the Tracker (the closing reference
when Landed, a comment carrying the Runner's blocker digest when Blocked), then the Compound
judgment. It exists because the Runner never writes to the Tracker and the Task process exits
before the landing commit exists, so neither can name it.

Its ending is a contract: the final line of its last message says whether the Compound judgment
wrote a learning or skipped one, and the Runner reads that line from the end of the message, not
the start. A Closeout process that ends any other way is recorded as unfinished, which is a
finding for the operator rather than a halt.

### Compound process
The second duty of the Closeout process: judging whether a Task produced a learning worth keeping
and writing it if so. It is kept out of the Task process because a Task process at the end of its
context is the worst available judge of its own learning. Runs for Blocked Tasks too, since a
blocker is often the learning.

### Halt class
The Runner's classification of one Task process exit, drawn from a closed set and decided from the
session transcript plus git and Tracker evidence. Every class carries the evidence its Cause line
needs, so an operator learns why a Task did not land without reading a transcript.

### Cause line
The one sentence a run summary prints to say why a Task did not land, and the only diagnosis an
operator who was not watching gets. Each is a fixed template belonging to a Halt class, filled from
the evidence the Runner recorded when it stopped. Findings that attach to a Task without being its
own class carry one too, so a Cause line is not exclusively a property of a Halt class.

A template and the evidence that fills it are two halves of one contract, written in different
places by different code. Two rules keep them joined. Evidence a template names must be a plain
value, because a structured one cannot be rendered into a sentence and is dropped. And where the
evidence and the Task's own record both carry a field of that name, the evidence wins, since the
record acquired most of its fields after the stop and would otherwise describe the aftermath rather
than the cause.

A Cause line is a derived form, and the record keeps the raw sentence it was derived from beside
it: the words the code that stopped the run actually wrote. The summary prints that sentence under
the Cause line whenever the two differ, so a template that fits the class loosely, such as a
refused retry reported under the class for a dirty tree, cannot be the only account of the stop.

## Outcomes

### Verify-landed
The Runner's own determination, from git and the Tracker alone, of whether a Task actually landed.
It never reads the Task process's exit code, printed result, or claims as evidence, because a
headless run has every incentive to report success.

The landing it recognises does not have to be the Runner's own merge. An operator who finishes a
Task by hand between runs and lands it through the project's own tooling has produced the same
outcome by another means, and Verify-landed accepts it on the same terms, because it reads git and
the Tracker rather than trusting which actor performed the merge.

### Landed
A Task whose work is durably where the Shipping mode says it belongs and whose Tracker record names
the landing. Both halves are required: code that merged while the card stayed put is a partial
landing, not a landing, and it halts the run.

### Blocked
A Task whose process stopped deliberately without landing, leaving the repository as it found it. A
Blocked Task is a normal outcome rather than a failure.

A Blocked Task is only legible to an operator who was not watching if the blocker reaches somewhere
outside the run's own transcript. The Closeout process writes that record, as a comment on the
Tracker, and the Runner never does: it reads the Tracker afterwards to confirm a comment appeared
and reports a Blocked Task whose card carries none as a check for the operator to make by hand. So a
Blocked Task the Closeout failed to record is visible in the run summary rather than on the board,
which is the accepted cost of the Runner holding no write path at all.

### Shipping mode
The per-project choice of what landing means: a merge to the default branch performed outside the
Task process, or a pull request whose checks have decided. The Manifest names one, and Verify-landed
applies the matching checks. Only the merge mode is implemented; the pull request mode is named in
the schema and refused before a run starts, so no Task can be driven under it today.

## Surroundings

### Tracker adapter
The read-side interface between a Runner and whatever holds the Task list and their statuses. Each
adapter exposes the same operations regardless of what sits behind it, and not one of them writes:
listing candidate Tasks, reading one Task's status and its recent comments, confirming that a
closing reference is present, and supplying what the Task process and the Closeout process need in
order to write to that Tracker themselves. The absence of a write operation is the whole guarantee.
A Runner cannot move a card by mistake because it holds no way to move one at all.

### External gate
The project's own mechanism that refuses a broken change independently of any agent's judgment,
such as a pre-push hook or continuous integration. Relay requires one to exist before it will run
against a project, and verifies its presence rather than providing it.

### Qualifying properties
The three conditions a project must meet before Relay can run against it: its Tasks are independent,
the state carried between Tasks is durable in git or on the Tracker, and an External gate refuses
broken changes. These describe the project. A Task can still be individually unrunnable while all
three hold.
